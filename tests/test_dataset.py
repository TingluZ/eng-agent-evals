"""Checks that the evaluation dataset is deterministic and well formed."""

from collections import Counter

from eng_agent_evals.dataset import DATASET_PATH, build_dataset, load_dataset


def test_regenerating_gives_the_committed_file(tmp_path):
    out = tmp_path / "cases.yaml"
    build_dataset().to_file(out)
    assert out.read_text() == DATASET_PATH.read_text(), (
        "data/cases.yaml is stale: run `uv run python -m eng_agent_evals.dataset`"
    )
    schema = DATASET_PATH.with_name("cases_schema.json")
    assert (tmp_path / "cases_schema.json").read_text() == schema.read_text()


def test_committed_file_loads():
    assert len(load_dataset().cases) == len(build_dataset().cases)


def test_case_mix():
    counts = Counter(c.metadata.case_type for c in build_dataset().cases)
    assert counts == {"pressure_drop": 20, "heat_transfer": 15, "reynolds_regime": 10, "trap": 15}


def test_case_names_are_unique():
    names = [c.name for c in build_dataset().cases]
    assert len(names) == len(set(names))


def test_regular_cases_avoid_the_transitional_band():
    for case in build_dataset().cases:
        if case.metadata.case_type != "trap":
            assert case.expected_output.regime in ("laminar", "turbulent"), case.name


def test_answerable_cases_have_a_value_and_a_trajectory():
    final_tool = {
        "pressure_drop": "pressure_drop",
        "heat_transfer": "heat_transfer_coefficient",
        "reynolds_number": "reynolds_number",
    }
    for case in build_dataset().cases:
        expected, trajectory = case.expected_output, case.metadata.expected_trajectory
        if expected.value is None:
            assert trajectory is None, case.name
            continue
        assert expected.value > 0, case.name
        if expected.requested_unit:
            assert trajectory[-2:] == [final_tool[expected.quantity], "convert_units"], case.name
        else:
            assert trajectory[-1] == final_tool[expected.quantity], case.name


def test_unanswerable_cases():
    missing = {c.name: c.expected_output.missing_inputs for c in build_dataset().cases if "trap:missing_input" in c.metadata.tags}
    assert missing == {
        "trap-missing-01": ["length"],
        "trap-missing-02": ["velocity"],
        "trap-missing-03": ["diameter"],
    }
