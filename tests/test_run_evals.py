"""Tests for the eval runner: statistics, subsetting and the summary. No API calls."""

from __future__ import annotations

import json
from collections import Counter

import pytest
from pydantic_evals import Dataset

from eng_agent_evals.dataset import build_dataset
from eng_agent_evals.run_evals import stratified_subset, summarise, to_json, wilson_interval
from test_evaluators import run_scripted_evaluation, scripted_case


# --- Wilson interval -----------------------------------------------------------

def test_wilson_interval_known_value():
    # 8/10: the textbook Wilson 95 % interval is [0.490, 0.943].
    low, high = wilson_interval(8, 10)
    assert low == pytest.approx(0.4902, abs=1e-4)
    assert high == pytest.approx(0.9433, abs=1e-4)


def test_wilson_interval_at_the_extremes_stays_in_range():
    low, high = wilson_interval(0, 20)
    assert low == 0.0 and 0.0 < high < 0.2
    low, high = wilson_interval(20, 20)
    assert 0.8 < low < 1.0 and high == 1.0


def test_wilson_interval_narrows_with_n():
    narrow = wilson_interval(80, 100)
    wide = wilson_interval(8, 10)
    assert narrow[1] - narrow[0] < wide[1] - wide[0]


def test_wilson_interval_empty():
    assert wilson_interval(0, 0) == (0.0, 1.0)


# --- subset --------------------------------------------------------------------

def test_stratified_subset_covers_every_case_type():
    subset = stratified_subset(build_dataset(), 20)
    counts = Counter(c.metadata.case_type for c in subset.cases)
    assert sum(counts.values()) == 20
    assert counts == {"pressure_drop": 5, "heat_transfer": 5, "reynolds_regime": 5, "trap": 5}


def test_stratified_subset_is_deterministic():
    names = lambda: [c.name for c in stratified_subset(build_dataset(), 20).cases]  # noqa: E731
    assert names() == names()


# --- summary, from an offline run ------------------------------------------------

@pytest.fixture(scope="module")
def report():
    # The scripted agent always answers the same question correctly, so a case
    # whose reference is deliberately wrong fails the value check.
    cases = [
        scripted_case("right-1"),
        scripted_case("right-2", case_type="trap"),
        scripted_case("wrong", value=1.0),
    ]
    return run_scripted_evaluation(Dataset(name="offline", cases=cases))


def test_summary_counts_a_case_as_passed_only_if_every_correctness_check_passes(report):
    summary = summarise(report)
    assert summary["overall"]["successes"] == 2
    assert summary["overall"]["n"] == 3
    assert summary["per_case_type"]["pressure_drop"]["successes"] == 1
    assert summary["per_case_type"]["trap"]["successes"] == 1
    assert summary["correctness_checks"]["ValueWithinTolerance"]["successes"] == 2
    assert summary["correctness_checks"]["RegimeCorrect"]["successes"] == 3
    # The wrong reference makes the answer wrong, not the process.
    assert summary["process"]["successes"] == 3
    assert set(summary["process_checks"]) == {"ValueTraceableToTool", "InputsTraceable", "tool_correctness"}
    assert summary["mean_trajectory_score"] == 1.0
    assert summary["mean_tool_calls"] == 5.0
    assert summary["retry_rate"] == 0.0
    assert summary["task_errors"] == 0


def test_json_output_is_serialisable(report):
    data = to_json(report, summarise(report), "test-model")
    data = json.loads(json.dumps(data, default=str))
    assert data["model"] == "test-model"
    assert [c["passed"] for c in data["cases"]] == [True, True, False]
    assert [c["process_ok"] for c in data["cases"]] == [True, True, True]
    assert data["cases"][0]["output"]["answer"]["unit"] == "kPa"
