"""Gate evaluation results against a committed baseline, for CI.

    uv run python -m eng_agent_evals.gate check RESULTS.json BASELINE.json [--enforce-headline]
    uv run python -m eng_agent_evals.gate baseline RESULTS.json RESULTS.json ... -o BASELINE.json [--finding CASE=ID ...]

Exits with status 1 if the gate fails. Three rules (docs/methodology.md, "CI gate"):

1. No crashes: no task errors, no evaluator errors, every baseline case run.
2. Stable cases stay passing: a case that passed every baseline attempt fails
   the gate only if it fails a majority of its attempts in this run, counting
   rechecks. A single failed attempt is not enough: stable cases fail a single
   attempt about 0.6 % of the time with no code change.
3. Headline: the baseline correct rate lies above the Wilson 95 % upper bound of
   this run's first attempts. Reported only, unless `--enforce-headline`.

Process checks, cost and latency are compared with the baseline and reported,
never gated, following the correctness/process split used throughout.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .run_evals import wilson_interval

# Warn when mean tokens or latency per attempt grow by more than this factor.
COST_WARNING_RATIO = 1.5
# A baseline needs evidence from separate runs: within one run, repeats share
# conditions, and stable cases flipped 4.5 times more often in a later run (2.6 %)
# than between repeats of the same run (0.58 %). See docs/methodology.md (E9).
MIN_BASELINE_RUNS = 2
MIN_BASELINE_ATTEMPTS = 3

_REPEAT_SUFFIX = re.compile(r" \[(?:recheck )?\d+/\d+\]$")


def _source_name(name: str) -> str:
    return _REPEAT_SUFFIX.sub("", name)


@dataclass
class Outcomes:
    """Pass/fail of every attempt at one case: first attempts, then rechecks."""

    first: list[bool] = field(default_factory=list)
    rechecks: list[bool] = field(default_factory=list)

    @property
    def all(self) -> list[bool]:
        return self.first + self.rechecks

    @property
    def majority_failed(self) -> bool:
        return sum(not p for p in self.all) * 2 > len(self.all)


@dataclass
class GateResult:
    passed: bool
    failures: list[str]
    warnings: list[str]
    notes: list[str]
    report: str


def collect_outcomes(results: dict[str, Any]) -> dict[str, Outcomes]:
    """Per-case outcomes from a run_evals results file. Task errors count as failed
    first attempts; `rechecks` holds the re-run attempts of failing cases, if any."""
    outcomes: dict[str, Outcomes] = defaultdict(Outcomes)
    for case in results["cases"]:
        outcomes[case["source_case"]].first.append(case["passed"])
    for error in results.get("errors", []):
        outcome = outcomes[_source_name(error["name"])]
        (outcome.rechecks if "[recheck " in error["name"] else outcome.first).append(False)
    for case in results.get("rechecks", []):
        outcomes[case["source_case"]].rechecks.append(case["passed"])
    return dict(outcomes)


def _mean_usage(results: dict[str, Any]) -> dict[str, float]:
    cases = results["cases"]
    if not cases:
        return {}
    total: dict[str, float] = defaultdict(float)
    for case in cases:
        for key in ("input_tokens", "output_tokens"):
            total[key] += ((case.get("output") or {}).get("usage") or {}).get(key, 0)
        total["latency_s"] += case.get("duration_s") or 0.0
    return {key: value / len(cases) for key, value in total.items()}


def check(results: dict[str, Any], baseline: dict[str, Any], enforce_headline: bool = False) -> GateResult:
    failures: list[str] = []
    warnings: list[str] = []
    notes: list[str] = []

    for key in ("model", "dataset"):
        if results.get(key) != baseline.get(key):
            failures.append(f"Configuration: results are for {key} {results.get(key)!r}, baseline for {baseline.get(key)!r}")
    if failures:
        return _result(failures, warnings, notes, rows=[])

    # Rule 1: no crashes.
    for error in results.get("errors", []):
        failures.append(f"Task error in `{error['name']}`: {error['error']}")
    for case in [*results["cases"], *results.get("rechecks", [])]:
        for failure in case.get("evaluator_failures", []):
            failures.append(f"Evaluator error in `{case['name']}`: {failure}")
    outcomes = collect_outcomes(results)
    for name in sorted(set(baseline["cases"]) - set(outcomes)):
        failures.append(f"Baseline case `{name}` was not run")

    # Rule 2: stable cases stay passing; other cases are reported.
    known = baseline.get("known_failures", {})
    for name, outcome in sorted(outcomes.items()):
        base = baseline["cases"].get(name)
        attempts = f"{sum(outcome.all)}/{len(outcome.all)} attempts passed"
        if base is None:
            notes.append(f"New case `{name}`, not in the baseline: {attempts}")
        elif base["passed"] == base["attempts"]:
            if outcome.majority_failed:
                failures.append(f"Regression: `{name}` passed {base['passed']}/{base['attempts']} in the baseline; now {attempts}")
            elif not all(outcome.all):
                notes.append(f"Stable case `{name}` failed an attempt but passed the recheck: {attempts}")
        elif base["passed"] == 0:
            if not outcome.majority_failed:
                finding = f" ({known[name]})" if known.get(name) else ""
                notes.append(f"Known failure `{name}`{finding} now passes: {attempts}. Update the baseline if this holds")
        elif not all(outcome.all):
            notes.append(f"Flaky case `{name}` ({base['passed']}/{base['attempts']} in the baseline): {attempts}")

    # Rule 3: headline rate, first attempts only (rechecks are a biased sample).
    first = [p for outcome in outcomes.values() for p in outcome.first]
    successes, n = sum(first), len(first)
    base_rate = sum(c["passed"] for c in baseline["cases"].values()) / sum(c["attempts"] for c in baseline["cases"].values())
    low, high = wilson_interval(successes, n)
    rows = [("Correct answers (first attempts)", f"{base_rate:.1%}", f"{successes}/{n}, {successes / n:.1%} [{low:.1%}, {high:.1%}]" if n else "none")]
    if n and base_rate > high:
        message = f"Headline: correct rate {successes}/{n} is significantly below the baseline {base_rate:.1%} (upper bound {high:.1%})"
        (failures if enforce_headline else warnings).append(message + ("" if enforce_headline else " [report-only]"))

    # Reported, never gated: process checks, cost, latency.
    current_process = results.get("summary", {}).get("process_checks", {})
    for name, base_check in sorted(baseline.get("process_checks", {}).items()):
        if name in current_process:
            rows.append((f"Process: {name}", f"{base_check['rate']:.1%}", f"{current_process[name]['rate']:.1%}"))
    usage, base_usage = _mean_usage(results), baseline.get("mean_usage", {})
    for key, label in (("input_tokens", "Input tokens per attempt"), ("output_tokens", "Output tokens per attempt"), ("latency_s", "Latency per attempt (s)")):
        if key in usage and base_usage.get(key):
            rows.append((label, f"{base_usage[key]:.0f}", f"{usage[key]:.0f}"))
            if usage[key] > COST_WARNING_RATIO * base_usage[key]:
                warnings.append(f"{label} rose from {base_usage[key]:.0f} to {usage[key]:.0f}")

    return _result(failures, warnings, notes, rows)


def build_baseline(runs: list[tuple[dict[str, Any], str]], findings: dict[str, str] | None = None) -> dict[str, Any]:
    """A baseline pooled from separate runs, given as (results, source file name):
    per-case pass counts over first attempts, known failures (cases that failed
    every attempt) annotated with finding IDs, process-check rates and mean usage.

    A case counts as stable only if it passed every attempt across all runs.
    Repeats within one run share conditions and understate the variation between
    runs, so at least MIN_BASELINE_RUNS separate runs are required."""
    findings = findings or {}
    if len(runs) < MIN_BASELINE_RUNS:
        raise ValueError(f"baseline needs at least {MIN_BASELINE_RUNS} separate runs; got {len(runs)}")
    first, *_ = (results for results, _ in runs)
    for results, source in runs:
        for key in ("model", "dataset"):
            if results.get(key) != first.get(key):
                raise ValueError(f"{source} is for {key} {results.get(key)!r}, not {first.get(key)!r}")

    cases: dict[str, dict[str, int]] = defaultdict(lambda: {"passed": 0, "attempts": 0, "runs": 0})
    process: dict[str, dict[str, float]] = defaultdict(lambda: {"successes": 0, "n": 0})
    usage: dict[str, float] = defaultdict(float)
    for results, _ in runs:
        for name, outcome in collect_outcomes(results).items():
            cases[name]["passed"] += sum(outcome.first)
            cases[name]["attempts"] += len(outcome.first)
            cases[name]["runs"] += 1
        for name, rate in results.get("summary", {}).get("process_checks", {}).items():
            process[name]["successes"] += rate["successes"]
            process[name]["n"] += rate["n"]
        for key, value in _mean_usage(results).items():
            usage[key] += value * len(results["cases"])
    attempts = sum(len(results["cases"]) for results, _ in runs)

    too_few = sorted(name for name, c in cases.items() if c["attempts"] < MIN_BASELINE_ATTEMPTS or c["runs"] < MIN_BASELINE_RUNS)
    if too_few:
        raise ValueError(
            f"every case needs at least {MIN_BASELINE_ATTEMPTS} attempts from {MIN_BASELINE_RUNS} runs; "
            f"too few for {too_few[:3]}"
        )
    not_failing = sorted(set(findings) - {name for name, c in cases.items() if c["passed"] == 0})
    if not_failing:
        raise ValueError(f"findings given for cases that are not known failures: {not_failing}")
    return {
        "model": first["model"],
        "dataset": first["dataset"],
        "dataset_file": first.get("dataset_file"),
        "runs": [
            {"source_results": source, "git_commit": results.get("git_commit"), "repeat": results.get("repeat")}
            for results, source in runs
        ],
        "cases": dict(sorted(cases.items())),
        "known_failures": {name: findings.get(name, "") for name, c in sorted(cases.items()) if c["passed"] == 0},
        "process_checks": {
            name: {**r, "rate": r["successes"] / r["n"]} for name, r in sorted(process.items()) if r["n"]
        },
        "mean_usage": {key: value / attempts for key, value in usage.items()} if attempts else {},
    }


def _result(failures: list[str], warnings: list[str], notes: list[str], rows: list[tuple[str, str, str]]) -> GateResult:
    passed = not failures
    lines = [f"## Evaluation gate: {'passed' if passed else 'FAILED'}", ""]
    if rows:
        lines += ["| | Baseline | This run |", "|---|---|---|", *(f"| {a} | {b} | {c} |" for a, b, c in rows), ""]
    for title, items in (("Failures", failures), ("Warnings", warnings), ("Notes", notes)):
        if items:
            lines += [f"### {title}", "", *(f"- {item}" for item in items), ""]
    return GateResult(passed, failures, warnings, notes, "\n".join(lines))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    check_cmd = commands.add_parser("check", help="gate a results file against a baseline")
    check_cmd.add_argument("results", type=Path)
    check_cmd.add_argument("baseline", type=Path)
    check_cmd.add_argument("--enforce-headline", action="store_true", help="fail on rule 3 instead of reporting it")
    check_cmd.add_argument("--summary-file", type=Path, help="also append the report here (e.g. $GITHUB_STEP_SUMMARY)")
    baseline_cmd = commands.add_parser("baseline", help="build a baseline pooled from separate runs")
    baseline_cmd.add_argument("results", type=Path, nargs="+", help="results files of separate runs (at least 2)")
    baseline_cmd.add_argument("-o", "--output", type=Path, required=True)
    baseline_cmd.add_argument(
        "--finding", action="append", default=[], metavar="CASE=ID", help="annotate a known failure, e.g. trap-missing-01=F11"
    )
    args = parser.parse_args(argv)

    if args.command == "baseline":
        findings = dict(item.split("=", 1) for item in args.finding)
        runs = [(json.loads(path.read_text()), path.name) for path in args.results]
        baseline = build_baseline(runs, findings)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(baseline, indent=2) + "\n")
        print(f"wrote {args.output}: {len(baseline['cases'])} cases, {len(baseline['known_failures'])} known failures")
        return 0

    result = check(json.loads(args.results.read_text()), json.loads(args.baseline.read_text()), args.enforce_headline)
    print(result.report)
    if args.summary_file:
        with args.summary_file.open("a") as f:
            f.write(result.report + "\n")
    return 0 if result.passed else 1


if __name__ == "__main__":
    sys.exit(main())
