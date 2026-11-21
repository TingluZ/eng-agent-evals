"""Tests for the CI evaluation gate. Results and baselines are made up: no API calls."""

from __future__ import annotations

import json

import pytest

from eng_agent_evals.gate import check, collect_outcomes, main

MODEL, DATASET = "openai:gpt-5.4-mini", "pipe_flow"


def _case(name: str, passed: bool, *, attempt: str = "1/1", tokens: int = 1000, latency: float = 5.0) -> dict:
    return {
        "name": f"{name} [{attempt}]",
        "source_case": name,
        "passed": passed,
        "output": {"usage": {"input_tokens": tokens, "output_tokens": tokens // 10}},
        "duration_s": latency,
        "evaluator_failures": [],
    }


def _results(outcomes: dict[str, bool], *, rechecks: dict[str, list[bool]] | None = None, **kw) -> dict:
    cases = [_case(name, passed, **kw) for name, passed in outcomes.items()]
    rechecked = [
        _case(name, passed, attempt=f"recheck {i}/{len(seq)}")
        for name, seq in (rechecks or {}).items()
        for i, passed in enumerate(seq, 1)
    ]
    return {"model": MODEL, "dataset": DATASET, "cases": cases, "rechecks": rechecked, "errors": [], "summary": {}}


def _baseline(cases: dict[str, int], attempts: int = 3, **kw) -> dict:
    return {
        "model": MODEL,
        "dataset": DATASET,
        "cases": {name: {"passed": passed, "attempts": attempts} for name, passed in cases.items()},
        "mean_usage": {"input_tokens": 1000, "output_tokens": 100, "latency_s": 5.0},
        **kw,
    }


STABLE = {f"case-{i:02d}": 3 for i in range(20)}


def test_identical_run_passes():
    result = check(_results({name: True for name in STABLE}), _baseline(STABLE))
    assert result.passed
    assert result.failures == result.warnings == []
    assert "Evaluation gate: passed" in result.report


def test_rule_1_task_error_fails():
    results = _results({name: True for name in list(STABLE)[1:]})
    results["errors"] = [{"name": "case-00 [1/1]", "error": "TimeoutError"}]
    result = check(results, _baseline(STABLE))
    assert not result.passed
    assert any("Task error in `case-00 [1/1]`" in f for f in result.failures)


def test_rule_1_evaluator_error_fails():
    results = _results({name: True for name in STABLE})
    results["cases"][0]["evaluator_failures"] = ["ValueError: boom"]
    assert not check(results, _baseline(STABLE)).passed


def test_rule_1_missing_baseline_case_fails():
    results = _results({name: True for name in list(STABLE)[:-1]})
    result = check(results, _baseline(STABLE))
    assert result.failures == ["Baseline case `case-19` was not run"]


def test_rule_2_stable_case_failing_every_attempt_is_a_regression():
    outcomes = {**{name: True for name in STABLE}, "case-03": False}
    result = check(_results(outcomes, rechecks={"case-03": [False, False]}), _baseline(STABLE))
    assert not result.passed
    assert result.failures == ["Regression: `case-03` passed 3/3 in the baseline; now 0/3 attempts passed"]


def test_rule_2_single_flaky_failure_that_passes_recheck_does_not_fail():
    outcomes = {**{name: True for name in STABLE}, "case-03": False}
    result = check(_results(outcomes, rechecks={"case-03": [True, True]}), _baseline(STABLE))
    assert result.passed
    assert any("passed the recheck" in n for n in result.notes)


def test_rule_2_without_rechecks_a_single_failure_counts():
    outcomes = {**{name: True for name in STABLE}, "case-03": False}
    assert not check(_results(outcomes), _baseline(STABLE)).passed


def test_known_failure_never_blocks_and_is_reported_when_it_starts_passing():
    cases = {**STABLE, "trap-missing-01": 0}
    still_failing = check(_results({**{n: True for n in STABLE}, "trap-missing-01": False}), _baseline(cases))
    assert still_failing.passed and still_failing.notes == []

    fixed = check(
        _results({**{n: True for n in STABLE}, "trap-missing-01": True}),
        _baseline(cases, known_failures={"trap-missing-01": "F11"}),
    )
    assert fixed.passed
    assert fixed.notes == ["Known failure `trap-missing-01` (F11) now passes: 1/1 attempts passed. Update the baseline if this holds"]


def test_flaky_baseline_case_is_reported_not_gated():
    cases = {**STABLE, "trap-units-03": 1}
    result = check(_results({**{n: True for n in STABLE}, "trap-units-03": False}), _baseline(cases))
    assert result.passed
    assert any("Flaky case `trap-units-03` (1/3" in n for n in result.notes)


def test_new_case_is_reported_not_gated():
    result = check(_results({**{n: True for n in STABLE}, "case-new": False}), _baseline(STABLE))
    assert result.passed
    assert any("New case `case-new`" in n for n in result.notes)


def _sixty(passing: int) -> tuple[dict, dict]:
    # 60 cases; the baseline passes 57 of them in every attempt (95 %).
    names = [f"c{i:02d}" for i in range(60)]
    baseline = _baseline({name: 3 if i < 57 else 0 for i, name in enumerate(names)})
    results = _results({name: i < passing for i, name in enumerate(names)})
    return results, baseline


def test_rule_3_significant_drop_is_report_only_by_default():
    # 45/60 = 75 %, Wilson upper bound 84 %, below the 95 % baseline. Rule 2 also fires.
    results, baseline = _sixty(45)
    result = check(results, baseline)
    assert any(w.startswith("Headline:") and w.endswith("[report-only]") for w in result.warnings)
    assert not any(f.startswith("Headline:") for f in result.failures)


def test_rule_3_enforced():
    results, baseline = _sixty(45)
    result = check(results, baseline, enforce_headline=True)
    assert any(f.startswith("Headline:") for f in result.failures)


def test_rule_3_small_drop_is_within_noise():
    # 55/60 = 92 %: upper bound 96 %, above the baseline, so not significant.
    results, baseline = _sixty(55)
    assert not any(w.startswith("Headline:") for w in check(results, baseline).warnings)


def test_rechecks_do_not_count_towards_the_headline_rate():
    outcomes = {**{name: True for name in STABLE}, "case-03": False}
    result = check(_results(outcomes, rechecks={"case-03": [True, True]}), _baseline(STABLE))
    assert "19/20" in result.report


def test_cost_increase_warns_but_does_not_fail():
    result = check(_results({name: True for name in STABLE}, tokens=2000), _baseline(STABLE))
    assert result.passed
    assert result.warnings == ["Input tokens per attempt rose from 1000 to 2000", "Output tokens per attempt rose from 100 to 200"]


def test_mismatched_model_or_dataset_fails_before_comparing():
    results = _results({name: True for name in STABLE})
    results["model"] = "openai:gpt-6.1-sol"
    result = check(results, _baseline(STABLE))
    assert result.failures == ["Configuration: results are for model 'openai:gpt-6.1-sol', baseline for 'openai:gpt-5.4-mini'"]


def test_collect_outcomes_reads_repeats_errors_and_rechecks():
    results = _results({"a": False}, rechecks={"a": [True, False]})
    results["errors"] = [{"name": "b [2/3]", "error": "x"}]
    outcomes = collect_outcomes(results)
    assert outcomes["a"].first == [False] and outcomes["a"].rechecks == [True, False]
    assert outcomes["a"].majority_failed
    assert outcomes["b"].first == [False]


@pytest.mark.parametrize(("passed", "code"), [(True, 0), (False, 1)])
def test_cli_exit_code_and_summary_file(tmp_path, capsys, passed, code):
    outcomes = {**{name: True for name in STABLE}, "case-03": passed}
    (tmp_path / "r.json").write_text(json.dumps(_results(outcomes)))
    (tmp_path / "b.json").write_text(json.dumps(_baseline(STABLE)))
    summary = tmp_path / "summary.md"
    assert main(["check", str(tmp_path / "r.json"), str(tmp_path / "b.json"), "--summary-file", str(summary)]) == code
    assert "## Evaluation gate" in capsys.readouterr().out
    assert "## Evaluation gate" in summary.read_text()


# --- baseline builder ----------------------------------------------------------

def _repeated_results(outcomes: dict[str, list[bool]]) -> dict:
    cases = [
        _case(name, passed, attempt=f"{i}/{len(seq)}")
        for name, seq in outcomes.items()
        for i, passed in enumerate(seq, 1)
    ]
    summary = {"process_checks": {"InputsTraceable": {"successes": 5, "n": 6, "rate": 5 / 6}}}
    return {"model": MODEL, "dataset": DATASET, "cases": cases, "errors": [], "summary": summary, "repeat": 3}


def test_build_baseline_counts_first_attempts_and_marks_known_failures():
    from eng_agent_evals.gate import build_baseline

    results = _repeated_results({"stable": [True] * 3, "flaky": [True, False, True], "missing-01": [False] * 3})
    results["rechecks"] = [_case("flaky", True, attempt="recheck 1/2")]  # ignored: biased sample
    baseline = build_baseline(results, "run.json", {"missing-01": "F11"})
    assert baseline["cases"] == {
        "flaky": {"passed": 2, "attempts": 3},
        "missing-01": {"passed": 0, "attempts": 3},
        "stable": {"passed": 3, "attempts": 3},
    }
    assert baseline["known_failures"] == {"missing-01": "F11"}
    assert baseline["source_results"] == "run.json"
    assert baseline["mean_usage"]["input_tokens"] == 1000
    assert baseline["process_checks"]["InputsTraceable"]["rate"] == pytest.approx(5 / 6)


def test_build_baseline_rejects_single_runs_and_wrong_findings():
    from eng_agent_evals.gate import build_baseline

    with pytest.raises(ValueError, match="at least 3 attempts"):
        build_baseline(_results({"a": True}), "run.json")
    with pytest.raises(ValueError, match="not known failures"):
        build_baseline(_repeated_results({"a": [True] * 3}), "run.json", {"a": "F1"})


def test_baseline_round_trip_through_the_cli(tmp_path):
    results = _repeated_results({**{name: [True] * 3 for name in STABLE}, "missing-01": [False] * 3})
    (tmp_path / "run.json").write_text(json.dumps(results))
    out = tmp_path / "baselines" / "b.json"
    assert main(["baseline", str(tmp_path / "run.json"), "-o", str(out), "--finding", "missing-01=F11"]) == 0
    same_again = _results({**{name: True for name in STABLE}, "missing-01": False})
    assert check(same_again, json.loads(out.read_text())).passed


def test_recheck_errors_count_as_failed_rechecks():
    results = _results({"a": False}, rechecks={"a": [True]})
    results["errors"] = [{"name": "a [recheck 2/2]", "error": "TimeoutError"}]
    outcome = collect_outcomes(results)["a"]
    assert outcome.first == [False] and sorted(outcome.rechecks) == [False, True]
