"""Checks that the evaluation dataset is deterministic and well formed."""

from collections import Counter

from eng_agent_evals.dataset import DATASET_PATH, ExpectedAnswer, build_dataset, load_dataset


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


# --- hard set -------------------------------------------------------------------

def test_hard_set_regenerates_to_the_committed_file(tmp_path):
    from eng_agent_evals.hard_dataset import HARD_DATASET_PATH, build_hard_dataset

    out = tmp_path / "hard_cases.yaml"
    build_hard_dataset().to_file(out)
    assert out.read_text() == HARD_DATASET_PATH.read_text(), (
        "data/hard_cases.yaml is stale: run `uv run python -m eng_agent_evals.hard_dataset`"
    )


def test_hard_set_shape():
    from eng_agent_evals.hard_dataset import HARD_DATASET_PATH, build_hard_dataset

    cases = build_hard_dataset().cases
    assert len(cases) == 30
    assert len({c.name for c in cases}) == 30
    assert all(c.metadata.case_type == "hard" and c.metadata.expected_trajectory is None for c in cases)
    assert len(load_dataset(HARD_DATASET_PATH).cases) == 30


def test_hard_knowledge_cases_separate_right_from_naive_answers():
    """The traps are only traps if the naive answer falls outside the 2 % tolerance."""
    from eng_agent_evals.hard_dataset import INCH, _dp

    steel, as_smooth = _dp(2.0, 0.050, 30, 20, 0.045e-3), _dp(2.0, 0.050, 30, 20)
    assert abs(as_smooth - steel) / steel > 0.02
    sch40, nominal = _dp(2.0, 2.067 * INCH, 50, 15, 0.045e-3), _dp(2.0, 2 * INCH, 50, 15, 0.045e-3)
    assert abs(nominal - sch40) / sch40 > 0.02
    # PVC is effectively smooth: treating it as smooth must still pass.
    pvc, pvc_smooth = _dp(1.5, 0.040, 50, 20, 0.0015e-3), _dp(1.5, 0.040, 50, 20)
    assert abs(pvc_smooth - pvc) / pvc < 0.02


def test_expected_values_are_rounded_so_the_files_match_across_platforms():
    # Linux and macOS differ in the last digit of some results (…821 vs …823).
    a = ExpectedAnswer(quantity="pressure_drop", value=17189.746858820738, unit="Pa", regime="turbulent")
    b = ExpectedAnswer(quantity="pressure_drop", value=17189.746858820742, unit="Pa", regime="turbulent")
    assert a.value == b.value == 17189.74686
    assert ExpectedAnswer(quantity="pressure_drop", value=None, unit="Pa", regime=None).value is None
