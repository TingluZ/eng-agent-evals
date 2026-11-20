"""Evaluators for the pipe-flow agent.

The task under evaluation returns an `AgentRun`: the agent's `Answer` plus the
tool calls read from its message history. The custom evaluators below check the
answer against the reference (`ExpectedAnswer`) and the tool outputs; the
built-in `TrajectoryMatch` and `ToolCorrectness`, attached per case by
`with_trajectory_evaluators`, check the tool sequence from the trace.

Assertions (bool) count towards a case passing; scores and metrics (numbers) are
reported but do not.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import pint
from pydantic import BaseModel
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import (
    EvaluationReason,
    Evaluator,
    EvaluatorContext,
    EvaluatorOutput,
    ToolCorrectness,
    TrajectoryMatch,
)

from .agent import Answer
from .dataset import CaseMetadata, ExpectedAnswer
from .units import ureg

OUTPUT_TOOL_PREFIX = "final_result"


# --- the evaluated output ----------------------------------------------------

class ToolCall(BaseModel):
    name: str
    args: dict[str, Any]
    result: Any = None
    retried: bool = False
    """The call raised ModelRetry or failed validation; the model was asked to retry."""


class AgentRun(BaseModel):
    answer: Answer
    tool_calls: list[ToolCall]

    @property
    def successful_calls(self) -> list[ToolCall]:
        return [c for c in self.tool_calls if not c.retried]


def extract_tool_calls(messages: list[ModelMessage]) -> list[ToolCall]:
    """Tool calls in order, with their results, excluding the final-output tool."""
    calls: dict[str, ToolCall] = {}
    for msg in messages:
        for part in msg.parts:
            if isinstance(msg, ModelResponse) and isinstance(part, ToolCallPart):
                if not part.tool_name.startswith(OUTPUT_TOOL_PREFIX):
                    calls[part.tool_call_id] = ToolCall(name=part.tool_name, args=part.args_as_dict())
            elif isinstance(part, (ToolReturnPart, RetryPromptPart)) and part.tool_call_id in calls:
                call = calls[part.tool_call_id]
                if isinstance(part, ToolReturnPart):
                    call.result = part.content
                    call.retried = part.outcome != "success"
                else:
                    call.retried = True
    return list(calls.values())


# --- unit handling -----------------------------------------------------------

_DIMENSIONLESS = {"", "-", "1", "none", "dimensionless"}
# "W/m²K" and similar are read by engineers as W/(m²·K); pint would read W/m² · K.
_HEAT_TRANSFER_UNIT = re.compile(r"^(\w?W)\s*/\s*m\s*(\^2|²|\*\*2|2)\s*[·*.]?\s*K$")


def normalise_unit(unit: str) -> str:
    u = unit.strip()
    if u.lower() in _DIMENSIONLESS:
        return "dimensionless"
    if m := _HEAT_TRANSFER_UNIT.match(u):
        return f"{m.group(1)}/(m^2*K)"
    return u.replace("·", "*")


def to_unit(value: float, from_unit: str, to: str) -> float:
    """Convert with pint after normalising the unit strings. Raises ValueError."""
    try:
        return ureg.Quantity(value, normalise_unit(from_unit)).to(normalise_unit(to)).magnitude
    except (pint.errors.PintError, AssertionError, TypeError, ValueError) as exc:
        raise ValueError(f"cannot convert {from_unit!r} to {to!r}: {exc}") from exc


def _numbers(obj: Any) -> list[float]:
    if isinstance(obj, bool):
        return []
    if isinstance(obj, (int, float)):
        return [float(obj)]
    if isinstance(obj, dict):
        return [x for v in obj.values() for x in _numbers(v)]
    if isinstance(obj, (list, tuple)):
        return [x for v in obj for x in _numbers(v)]
    return []


def _is_close(a: float, b: float, rel_tol: float) -> bool:
    return abs(a - b) <= rel_tol * abs(b)


# --- custom evaluators -------------------------------------------------------

Ctx = EvaluatorContext[str, AgentRun, CaseMetadata]


@dataclass
class ValueWithinTolerance(Evaluator):
    """The value, converted to the expected unit, is within `rel_tol` of the
    reference. Passes when both are null (the agent correctly declined)."""

    rel_tol: float = 0.02

    def evaluate(self, ctx: Ctx) -> EvaluationReason:
        answer, expected = ctx.output.answer, _expected(ctx)
        if expected.value is None or answer.value is None:
            ok = expected.value is None and answer.value is None
            return EvaluationReason(ok, f"expected {expected.value}, got {answer.value}")
        try:
            got = to_unit(answer.value, answer.unit, expected.unit)
        except ValueError as exc:
            return EvaluationReason(False, str(exc))
        error = abs(got - expected.value) / abs(expected.value)
        return EvaluationReason(
            error <= self.rel_tol, f"{got:.6g} vs {expected.value:.6g} {expected.unit} ({error:.2%})"
        )


@dataclass
class ReportsRequestedUnit(Evaluator):
    """When the question names an output unit, the answer is in that unit."""

    def evaluate(self, ctx: Ctx) -> EvaluatorOutput:
        requested = _expected(ctx).requested_unit
        if requested is None:
            return {}
        unit = ctx.output.answer.unit
        try:
            ok = ureg.Unit(normalise_unit(unit)) == ureg.Unit(requested)
        except (pint.errors.PintError, AssertionError, TypeError, ValueError):
            ok = False
        return {"reports_requested_unit": EvaluationReason(ok, f"requested {requested}, got {unit!r}")}


@dataclass
class RegimeCorrect(Evaluator):
    """The reported regime matches the reference, including null when it cannot be computed."""

    def evaluate(self, ctx: Ctx) -> EvaluationReason:
        got, expected = ctx.output.answer.regime, _expected(ctx).regime
        return EvaluationReason(got == expected, f"expected {expected}, got {got}")


_SYNONYMS = {
    "length": ("length",),
    "velocity": ("velocity", "flow rate", "speed"),
    "diameter": ("diameter", "bore"),
}


@dataclass
class FlagsMissingInputs(Evaluator):
    """Missing inputs are named and no value is invented; when nothing is
    missing, nothing is flagged. Not applied to out-of-range cases, where
    declining is checked by the value evaluator."""

    def evaluate(self, ctx: Ctx) -> EvaluatorOutput:
        if "trap:out_of_range" in ctx.metadata.tags:
            return {}
        answer, expected = ctx.output.answer, _expected(ctx)
        flagged = " | ".join(answer.missing_inputs).lower()
        if not expected.missing_inputs:
            ok = not answer.missing_inputs
            return EvaluationReason(ok, f"nothing missing, flagged {answer.missing_inputs}")
        named = all(
            any(word in flagged for word in _SYNONYMS.get(name, (name,))) for name in expected.missing_inputs
        )
        ok = named and answer.value is None
        return EvaluationReason(
            ok, f"expected {expected.missing_inputs}, flagged {answer.missing_inputs}, value {answer.value}"
        )


@dataclass
class ValueTraceableToTool(Evaluator):
    """The reported value, in SI, equals some successful tool output within
    `rel_tol`. Catches numbers the model computed itself. Unit scaling of the
    final value (Pa to kPa) is allowed because the comparison is made in SI, and
    0.5 % allows rounding to three significant figures."""

    rel_tol: float = 5e-3

    def evaluate(self, ctx: Ctx) -> EvaluatorOutput:
        answer = ctx.output.answer
        if answer.value is None:
            return {}
        try:
            si = ureg.Quantity(answer.value, normalise_unit(answer.unit)).to_base_units().magnitude
        except (pint.errors.PintError, AssertionError, TypeError, ValueError) as exc:
            return EvaluationReason(False, f"unit {answer.unit!r} not understood: {exc}")
        for call in ctx.output.successful_calls:
            if any(_is_close(si, x, self.rel_tol) for x in _numbers(call.result)):
                return EvaluationReason(True, f"matches {call.name} output")
        return EvaluationReason(False, f"{answer.value} {answer.unit} matches no tool output")


@dataclass
class ToolCallStats(Evaluator):
    """Metrics, not assertions: number of tool calls and of retried calls."""

    def evaluate(self, ctx: Ctx) -> dict[str, int]:
        calls = ctx.output.tool_calls
        return {"tool_calls": len(calls), "retries": sum(c.retried for c in calls)}


def _expected(ctx: Ctx) -> ExpectedAnswer:
    return ctx.expected_output  # type: ignore[return-value]  # reference, not an AgentRun


CUSTOM_EVALUATORS: list[Evaluator] = [
    ValueWithinTolerance(),
    ReportsRequestedUnit(),
    RegimeCorrect(),
    FlagsMissingInputs(),
    ValueTraceableToTool(),
    ToolCallStats(),
]


# --- built-in trajectory evaluators, per case ---------------------------------

def with_trajectory_evaluators(dataset: Dataset) -> Dataset:
    """Copy of `dataset` with the built-in trajectory evaluators attached to each
    case that has an expected trajectory, and the custom evaluators dataset-wide.

    `TrajectoryMatch` (in order, F1 over the longest common subsequence) is a
    score; `ToolCorrectness` (every expected tool called, extras allowed) is an
    assertion. Both read tool calls from the OpenTelemetry span tree, so the
    agent must be instrumented (`logfire.instrument_pydantic_ai()`).
    """
    cases = []
    for case in dataset.cases:
        evaluators = list(case.evaluators)
        trajectory = case.metadata.expected_trajectory
        if trajectory is not None:
            evaluators += [
                TrajectoryMatch(trajectory, order="in_order", evaluation_name="trajectory"),
                ToolCorrectness(sorted(set(trajectory)), allow_extra=True, evaluation_name="tool_correctness"),
            ]
        cases.append(
            Case(
                name=case.name,
                inputs=case.inputs,
                metadata=case.metadata,
                expected_output=case.expected_output,
                evaluators=evaluators,
            )
        )
    return Dataset(name=dataset.name, cases=cases, evaluators=[*dataset.evaluators, *CUSTOM_EVALUATORS])
