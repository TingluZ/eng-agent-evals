"""Run the evaluation suite against a real model and summarise the results.

    uv run python -m eng_agent_evals.run_evals [--subset N] [--model openai:gpt-5.4-mini]

Calls the model API for every case. Prints the Pydantic Evals report and a
summary with Wilson 95 % intervals, and saves everything as JSON in results/.
Traces go to Logfire if a token or `logfire auth` credential is present.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import logfire
from pydantic_ai import Agent
from pydantic_evals import Dataset
from pydantic_evals.reporting import EvaluationReport, ReportCase
from rich.console import Console
from rich.table import Table

from .agent import DEFAULT_MODEL, Answer, build_agent
from .dataset import DATASET_PATH, load_dataset
from .hard_dataset import HARD_DATASET_PATH
from .evaluators import CORRECTNESS_CHECKS, AgentRun, extract_tool_calls, with_trajectory_evaluators

RESULTS_DIR = Path(__file__).resolve().parents[2] / "results"
DATASETS = {"regression": DATASET_PATH, "hard": HARD_DATASET_PATH}


# --- statistics ----------------------------------------------------------------

def wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion (95 % by default).

    Unlike the normal approximation, it stays inside [0, 1] and behaves sensibly
    for small n and for proportions near 0 or 1.
    """
    if n == 0:
        return 0.0, 1.0
    p = successes / n
    denom = 1 + z**2 / n
    centre = (p + z**2 / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z**2 / (4 * n**2)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


@dataclass
class Rate:
    successes: int
    n: int

    @property
    def value(self) -> float:
        return self.successes / self.n if self.n else float("nan")

    def as_dict(self) -> dict[str, float | int]:
        low, high = wilson_interval(self.successes, self.n)
        return {"successes": self.successes, "n": self.n, "rate": self.value, "ci_low": low, "ci_high": high}


# --- running -------------------------------------------------------------------

def make_task(agent: Agent[None, Answer]):
    async def task(question: str) -> AgentRun:
        result = await agent.run(question)
        usage = result.usage
        return AgentRun(
            answer=result.output,
            tool_calls=extract_tool_calls(result.all_messages()),
            usage={
                "requests": usage.requests,
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
            },
        )

    return task


def stratified_subset(dataset: Dataset, n: int) -> Dataset:
    """The first `n` cases taken round-robin across case types, so a small fixed
    subset still covers every type. Deterministic, because the dataset is."""
    by_type: dict[str, list] = defaultdict(list)
    for case in dataset.cases:
        by_type[case.metadata.case_type].append(case)
    queues = list(by_type.values())
    picked = []
    while len(picked) < n and any(queues):
        for queue in queues:
            if queue and len(picked) < n:
                picked.append(queue.pop(0))
    return Dataset(name=dataset.name, cases=picked, evaluators=dataset.evaluators)


# --- summarising ---------------------------------------------------------------

def case_passed(case: ReportCase) -> bool:
    """Every correctness assertion that applies passed, and no evaluator raised."""
    return not case.evaluator_failures and all(
        bool(r.value) for name, r in case.assertions.items() if name in CORRECTNESS_CHECKS
    )


def case_process_ok(case: ReportCase) -> bool:
    """Every process assertion that applies passed."""
    return all(bool(r.value) for name, r in case.assertions.items() if name not in CORRECTNESS_CHECKS)


_REPEAT_SUFFIX = re.compile(r" \[(\d+)/(\d+)\]$")


def _repeat_index(name: str) -> int | None:
    match = _REPEAT_SUFFIX.search(name)
    return int(match.group(1)) if match else None


def _source_name(name: str) -> str:
    return _REPEAT_SUFFIX.sub("", name)


def summarise(report: EvaluationReport) -> dict[str, Any]:
    """Pass rates with Wilson intervals, overall, per evaluator and per case type.

    A case passes when every correctness assertion that applies to it passes;
    process assertions are summarised separately. Cases whose task raised, or
    where an evaluator raised, count as failed on both.

    With repeated runs, rates are pooled over all attempts, and each repeat's
    rate is also reported, with the cases whose outcome differed between repeats.
    Pooled intervals treat repeats of a case as independent, which they are not,
    so they are narrower than the evidence supports.
    """
    overall, process = Rate(0, 0), Rate(0, 0)
    per_type: dict[str, Rate] = defaultdict(lambda: Rate(0, 0))
    per_evaluator: dict[str, Rate] = defaultdict(lambda: Rate(0, 0))
    per_repeat: dict[int, Rate] = defaultdict(lambda: Rate(0, 0))
    outcomes: dict[str, set[bool]] = defaultdict(set)
    trajectory_scores, tool_calls, retries = [], 0, 0

    for case in report.cases:
        passed = case_passed(case)
        outcomes[_source_name(case.name)].add(passed)
        if (index := _repeat_index(case.name)) is not None:
            per_repeat[index].n += 1
            per_repeat[index].successes += passed
        for name, result in case.assertions.items():
            per_evaluator[name].n += 1
            per_evaluator[name].successes += bool(result.value)
        if "trajectory" in case.scores:
            trajectory_scores.append(case.scores["trajectory"].value)
        tool_calls += case.scores["tool_calls"].value if "tool_calls" in case.scores else 0
        retries += case.scores["retries"].value if "retries" in case.scores else 0
        for rate in (overall, per_type[case.metadata.case_type]):
            rate.n += 1
            rate.successes += passed
        process.n += 1
        process.successes += case_process_ok(case) and not case.evaluator_failures

    for failure in report.failures:
        overall.n += 1
        process.n += 1
        per_type[failure.metadata.case_type].n += 1
        outcomes[_source_name(failure.name)].add(False)
        if (index := _repeat_index(failure.name)) is not None:
            per_repeat[index].n += 1

    completed = len(report.cases)
    return {
        "overall": overall.as_dict(),
        "process": process.as_dict(),
        "per_case_type": {k: v.as_dict() for k, v in sorted(per_type.items())},
        "correctness_checks": {k: v.as_dict() for k, v in per_evaluator.items() if k in CORRECTNESS_CHECKS},
        "process_checks": {k: v.as_dict() for k, v in per_evaluator.items() if k not in CORRECTNESS_CHECKS},
        "mean_trajectory_score": sum(trajectory_scores) / len(trajectory_scores) if trajectory_scores else None,
        "mean_tool_calls": tool_calls / completed if completed else None,
        "retry_rate": retries / tool_calls if tool_calls else 0.0,
        "task_errors": len(report.failures),
        "per_repeat": {k: v.as_dict() for k, v in sorted(per_repeat.items())},
        "inconsistent_cases": sorted(name for name, seen in outcomes.items() if len(seen) > 1),
    }


def _rate_row(name: str, d: dict[str, float | int]) -> tuple[str, ...]:
    return (name, f"{d['successes']}/{d['n']}", f"{d['rate']:.0%}", f"[{d['ci_low']:.0%}, {d['ci_high']:.0%}]")


def print_summary(summary: dict[str, Any], console: Console) -> None:
    table = Table(title="Pass rates (Wilson 95 % CI)")
    for col in ("", "passed", "rate", "95 % CI"):
        table.add_column(col)
    table.add_row(*_rate_row("overall (correct answers)", summary["overall"]), style="bold")
    table.add_row(*_rate_row("process (all process checks)", summary["process"]))
    table.add_section()
    for name, d in summary["per_case_type"].items():
        table.add_row(*_rate_row(f"case type: {name}", d))
    table.add_section()
    for name, d in summary["correctness_checks"].items():
        table.add_row(*_rate_row(f"correctness: {name}", d))
    table.add_section()
    for name, d in summary["process_checks"].items():
        table.add_row(*_rate_row(f"process: {name}", d))
    console.print(table)

    mean_traj = summary["mean_trajectory_score"]
    console.print(f"mean trajectory score (in-order F1): {mean_traj:.3f}" if mean_traj is not None else "")
    if summary["mean_tool_calls"] is not None:
        console.print(f"mean tool calls per case: {summary['mean_tool_calls']:.1f}")
    console.print(f"retry rate (retried / all tool calls): {summary['retry_rate']:.1%}")
    console.print(f"task errors: {summary['task_errors']}")

    if len(summary["per_repeat"]) > 1:
        repeats = Table(title="Correct answers per repeat")
        for col in ("", "passed", "rate", "95 % CI"):
            repeats.add_column(col)
        for index, d in summary["per_repeat"].items():
            repeats.add_row(*_rate_row(f"repeat {index}", d))
        console.print(repeats)
        inconsistent = summary["inconsistent_cases"]
        console.print(f"cases with different outcomes across repeats: {len(inconsistent)}")
        for name in inconsistent:
            console.print(f"  {name}")
        console.print("pooled intervals treat repeats as independent, so they are too narrow")


def to_json(report: EvaluationReport, summary: dict[str, Any], model: str) -> dict[str, Any]:
    def results(mapping) -> dict[str, Any]:
        return {name: {"value": r.value, "reason": r.reason} for name, r in mapping.items()}

    return {
        "model": model,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git_commit": _git_describe(),
        "dataset": report.dataset_name,
        "summary": summary,
        "cases": [
            {
                "name": case.name,
                "source_case": _source_name(case.name),
                "case_type": case.metadata.case_type,
                "tags": case.metadata.tags,
                "passed": case_passed(case),
                "process_ok": case_process_ok(case),
                "assertions": results(case.assertions),
                "scores": results(case.scores),
                "output": case.output.model_dump(mode="json"),
                "duration_s": case.task_duration,
                "evaluator_failures": [str(f) for f in case.evaluator_failures],
            }
            for case in report.cases
        ],
        "errors": [
            {"name": f.name, "case_type": f.metadata.case_type, "error": f.error_message} for f in report.failures
        ],
    }


def _git_describe() -> str | None:
    try:
        out = subprocess.run(
            ["git", "describe", "--always", "--dirty"], capture_output=True, text=True, check=True
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return out.stdout.strip()


# --- CLI -----------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--dataset",
        choices=sorted(DATASETS),
        default="regression",
        help="regression: the 60-case set (default); hard: the 30-case set beyond it",
    )
    parser.add_argument("--subset", type=int, help="run a fixed, stratified subset of N cases")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"model name (default {DEFAULT_MODEL})")
    parser.add_argument("--max-concurrency", type=int, default=5)
    parser.add_argument("--repeat", type=int, default=1, help="run every case N times")
    parser.add_argument("--output-dir", type=Path, default=RESULTS_DIR)
    args = parser.parse_args(argv)

    # Tracing is required, not optional: the trajectory evaluators read tool calls from spans.
    logfire.configure(send_to_logfire="if-token-present", service_name="eng-agent-evals", console=False)
    logfire.instrument_pydantic_ai()

    dataset = load_dataset(DATASETS[args.dataset])
    if args.subset:
        dataset = stratified_subset(dataset, args.subset)
    dataset = with_trajectory_evaluators(dataset)

    report = dataset.evaluate_sync(
        make_task(build_agent(args.model)),
        name=args.model,
        max_concurrency=args.max_concurrency,
        repeat=args.repeat,
    )

    console = Console()
    report.print(console=console, include_durations=False)
    summary = summarise(report)
    print_summary(summary, console)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    model = re.sub(r"[^\w.-]+", "_", args.model)
    path = args.output_dir / f"{stamp}-{model}-{args.dataset}.json"
    data = to_json(report, summary, args.model)
    data["repeat"] = args.repeat
    data["dataset_file"] = DATASETS[args.dataset].name
    path.write_text(json.dumps(data, indent=2, default=str) + "\n")
    console.print(f"saved {path}")


if __name__ == "__main__":
    main()
