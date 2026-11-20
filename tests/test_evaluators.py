"""Evaluator tests on hand-built agent outputs, plus one offline end-to-end run
through Pydantic Evals with a scripted fake model. No API calls."""

from __future__ import annotations

import logfire
import pytest
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_evals import Case, Dataset
from pydantic_evals.evaluators import EvaluatorContext
from pydantic_evals.otel._errors import SpanTreeRecordingError

from eng_agent_evals import physics
from eng_agent_evals.agent import Answer, build_agent
from eng_agent_evals.dataset import CaseMetadata, ExpectedAnswer
from eng_agent_evals.evaluators import (
    AgentRun,
    FlagsMissingInputs,
    InputsTraceable,
    RegimeCorrect,
    ReportsRequestedUnit,
    ToolCall,
    ToolCallStats,
    ValueTraceableToTool,
    ValueWithinTolerance,
    extract_tool_calls,
    normalise_unit,
    with_trajectory_evaluators,
)

DP = 21_559.0  # reference pressure drop [Pa]


def _answer(**kw) -> Answer:
    defaults = dict(value=DP, unit="Pa", regime="turbulent", assumptions=[], missing_inputs=[], explanation="x")
    return Answer(**{**defaults, **kw})


def _expected(**kw) -> ExpectedAnswer:
    defaults = dict(quantity="pressure_drop", value=DP, unit="Pa", regime="turbulent")
    return ExpectedAnswer(**{**defaults, **kw})


def _ctx(answer: Answer, expected: ExpectedAnswer, calls: list[ToolCall] = (), tags: list[str] = ()):
    return EvaluatorContext(
        name="case",
        inputs="question",
        metadata=CaseMetadata(case_type="trap", expected_trajectory=None, tags=list(tags)),
        expected_output=expected,
        output=AgentRun(answer=answer, tool_calls=list(calls)),
        duration=0.0,
        _span_tree=SpanTreeRecordingError("not recorded"),
        attributes={},
        metrics={},
    )


# --- units -------------------------------------------------------------------

@pytest.mark.parametrize(
    "unit, normalised",
    [("W/m²K", "W/(m^2*K)"), ("W/m^2 K", "W/(m^2*K)"), ("W/(m²·K)", "W/(m²*K)"), ("-", "dimensionless"), ("", "dimensionless")],
)
def test_normalise_unit(unit, normalised):
    assert normalise_unit(unit) == normalised


# --- value -------------------------------------------------------------------

@pytest.mark.parametrize(
    "value, unit, ok",
    [(DP, "Pa", True), (21.56, "kPa", True), (DP * 1.019, "Pa", True), (DP * 1.03, "Pa", False), (21.56, "m", False)],
)
def test_value_within_tolerance(value, unit, ok):
    result = ValueWithinTolerance().evaluate(_ctx(_answer(value=value, unit=unit), _expected()))
    assert result.value is ok


def test_value_within_tolerance_converts_written_units():
    expected = _expected(quantity="heat_transfer", value=3000.0, unit="W/(m^2*K)")
    assert ValueWithinTolerance().evaluate(_ctx(_answer(value=3.0, unit="kW/m²K"), expected)).value is True


def test_value_within_tolerance_dimensionless():
    expected = _expected(quantity="reynolds_number", value=99_621.0, unit="dimensionless")
    assert ValueWithinTolerance().evaluate(_ctx(_answer(value=99_600.0, unit="-"), expected)).value is True


@pytest.mark.parametrize("expected_value, value, ok", [(None, None, True), (None, 5.0, False), (DP, None, False)])
def test_value_within_tolerance_with_nulls(expected_value, value, ok):
    result = ValueWithinTolerance().evaluate(_ctx(_answer(value=value), _expected(value=expected_value)))
    assert result.value is ok


# --- requested unit ----------------------------------------------------------

def test_reports_requested_unit():
    expected = _expected(requested_unit="kPa")
    assert ReportsRequestedUnit().evaluate(_ctx(_answer(value=21.56, unit="kPa"), expected))[
        "reports_requested_unit"
    ].value is True
    assert ReportsRequestedUnit().evaluate(_ctx(_answer(), expected))["reports_requested_unit"].value is False


def test_reports_requested_unit_not_applicable():
    assert ReportsRequestedUnit().evaluate(_ctx(_answer(), _expected())) == {}


# --- regime ------------------------------------------------------------------

@pytest.mark.parametrize(
    "got, expected, ok", [("turbulent", "turbulent", True), ("laminar", "turbulent", False), (None, None, True)]
)
def test_regime_correct(got, expected, ok):
    assert RegimeCorrect().evaluate(_ctx(_answer(regime=got), _expected(regime=expected))).value is ok


# --- missing inputs ----------------------------------------------------------

@pytest.mark.parametrize(
    "value, flagged, ok",
    [
        (None, ["pipe length"], True),
        (None, ["Length of the pipe (m)"], True),
        (DP, ["pipe length"], False),  # flagged but guessed anyway
        (None, ["roughness"], False),  # wrong input named
        (None, [], False),
    ],
)
def test_flags_missing_inputs(value, flagged, ok):
    expected = _expected(value=None, missing_inputs=["length"])
    result = FlagsMissingInputs().evaluate(_ctx(_answer(value=value, missing_inputs=flagged), expected))
    assert result.value is ok


def test_flags_missing_inputs_synonyms():
    expected = _expected(value=None, missing_inputs=["velocity"])
    answer = _answer(value=None, missing_inputs=["flow rate or velocity"])
    assert FlagsMissingInputs().evaluate(_ctx(answer, expected)).value is True


def test_nothing_missing_means_nothing_flagged():
    assert FlagsMissingInputs().evaluate(_ctx(_answer(), _expected())).value is True
    assert FlagsMissingInputs().evaluate(_ctx(_answer(missing_inputs=["roughness"]), _expected())).value is False


def test_flags_missing_inputs_skips_out_of_range_cases():
    answer = _answer(value=None, missing_inputs=["temperature within 0-100 °C"])
    ctx = _ctx(answer, _expected(value=None), tags=["trap:out_of_range"])
    assert FlagsMissingInputs().evaluate(ctx) == {}


# --- traceability ------------------------------------------------------------

_CALLS = [
    ToolCall(name="water_properties", args={}, result={"density": 998.2, "dynamic_viscosity": 1.002e-3}),
    ToolCall(name="pressure_drop", args={}, result=DP),
]


@pytest.mark.parametrize(
    "value, unit, ok",
    [(DP, "Pa", True), (21.559, "kPa", True), (21.6, "kPa", True), (21.8, "kPa", False), (998.2, "kg/m^3", True), (12_345.0, "Pa", False)],
)
def test_value_traceable_to_tool(value, unit, ok):
    result = ValueTraceableToTool().evaluate(_ctx(_answer(value=value, unit=unit), _expected(), _CALLS))
    assert result.value is ok


def test_value_traceable_ignores_retried_calls():
    calls = [ToolCall(name="pressure_drop", args={}, result=DP, retried=True)]
    assert ValueTraceableToTool().evaluate(_ctx(_answer(), _expected(), calls)).value is False


def test_value_traceable_not_applicable_without_value():
    assert ValueTraceableToTool().evaluate(_ctx(_answer(value=None), _expected(value=None))) == {}


# --- input traceability ------------------------------------------------------

_QUESTION = "What is the Reynolds number for water at 37 °C flowing at 0.26 m/s in a 0.025 m pipe?"
_PROPS = {"density": 993.25, "dynamic_viscosity": 0.0006965}


def _inputs_ctx(calls: list[ToolCall]):
    ctx = _ctx(_answer(), _expected(), calls)
    ctx.inputs = _QUESTION
    return ctx


def test_inputs_traceable_when_copied_from_question_and_tools():
    calls = [
        ToolCall(name="water_properties", args={"temperature_c": 37}, result=_PROPS),
        ToolCall(
            name="reynolds_number",
            args={"velocity": 0.26, "diameter": 0.025, "density": 993.25, "viscosity": 0.0006965},
            result={"reynolds_number": 9269.4, "regime": "turbulent"},
        ),
        ToolCall(name="friction_factor", args={"reynolds_number": 9269.4, "relative_roughness": 0.0}, result=0.03),
    ]
    assert InputsTraceable().evaluate(_inputs_ctx(calls)).value is True


def test_inputs_traceable_catches_substituted_properties():
    # Run 1, reynolds_regime-06: properties looked up, then generic values used.
    calls = [
        ToolCall(name="water_properties", args={"temperature_c": 37}, result=_PROPS),
        ToolCall(
            name="reynolds_number",
            args={"velocity": 0.26, "diameter": 0.025, "density": 1000, "viscosity": 0.001},
            result={"reynolds_number": 6500.0, "regime": "turbulent"},
        ),
    ]
    result = InputsTraceable().evaluate(_inputs_ctx(calls))
    assert result.value is False
    assert "reynolds_number.density=1000" in result.reason
    assert "reynolds_number.viscosity=0.001" in result.reason


def test_inputs_traceable_catches_retyped_numbers():
    # Run 1, pressure_drop-12: Re 9132.3 from the tool passed on as 9145.7.
    calls = [
        ToolCall(name="water_properties", args={"temperature_c": 37}, result=_PROPS),
        ToolCall(name="friction_factor", args={"reynolds_number": 9145.7}, result=0.03),
    ]
    ctx = _inputs_ctx([*calls[:1], ToolCall(name="reynolds_number", args={}, result={"reynolds_number": 9132.3}), calls[1]])
    assert InputsTraceable().evaluate(ctx).value is False


def test_inputs_traceable_rejects_values_from_later_calls():
    calls = [
        ToolCall(name="reynolds_number", args={"velocity": 0.26, "diameter": 0.025, "density": 993.25, "viscosity": 0.0006965}),
        ToolCall(name="water_properties", args={"temperature_c": 37}, result=_PROPS),
    ]
    assert InputsTraceable().evaluate(_inputs_ctx(calls)).value is False


def test_tool_call_stats():
    calls = [*_CALLS, ToolCall(name="water_properties", args={}, result="error", retried=True)]
    assert ToolCallStats().evaluate(_ctx(_answer(), _expected(), calls)) == {"tool_calls": 3, "retries": 1}


# --- trajectory extraction ---------------------------------------------------

def test_extract_tool_calls_pairs_results_and_skips_output_tool():
    messages: list[ModelMessage] = [
        ModelResponse(parts=[ToolCallPart("water_properties", {"temperature_c": 150}, tool_call_id="a")]),
        ModelRequest(parts=[ToolReturnPart("water_properties", "out of range", tool_call_id="a", outcome="retried")]),
        ModelResponse(parts=[ToolCallPart("water_properties", {"temperature_c": 20}, tool_call_id="b")]),
        ModelRequest(parts=[ToolReturnPart("water_properties", {"density": 998.2}, tool_call_id="b")]),
        ModelResponse(parts=[ToolCallPart("final_result", {"value": None}, tool_call_id="c")]),
        ModelRequest(parts=[ToolReturnPart("final_result", "Final result processed.", tool_call_id="c")]),
    ]
    calls = extract_tool_calls(messages)
    assert [(c.name, c.retried) for c in calls] == [("water_properties", True), ("water_properties", False)]
    assert calls[0].args == {"temperature_c": 150}
    assert calls[1].result == {"density": 998.2}


# --- end to end, offline -----------------------------------------------------

def _scripted_pressure_drop(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
    """A fake LLM that follows the ideal trajectory for 2 m/s, 50 mm, 30 m, 20 °C,
    then reports the answer in kPa converted 'in its head' (as observed live)."""
    calls = extract_tool_calls(messages)
    done = {c.name: c.result for c in calls if not c.retried}
    if "convert_units" not in done:
        call = ToolCallPart("convert_units", {"value": 50, "from_unit": "mm", "to_unit": "m"})
    elif "water_properties" not in done:
        call = ToolCallPart("water_properties", {"temperature_c": 20})
    elif "reynolds_number" not in done:
        w = done["water_properties"]
        args = {"velocity": 2, "diameter": done["convert_units"], "density": w["density"], "viscosity": w["dynamic_viscosity"]}
        call = ToolCallPart("reynolds_number", args)
    elif "friction_factor" not in done:
        call = ToolCallPart("friction_factor", {"reynolds_number": done["reynolds_number"]["reynolds_number"]})
    elif "pressure_drop" not in done:
        args = {
            "friction_factor": done["friction_factor"],
            "length": 30,
            "diameter": done["convert_units"],
            "density": done["water_properties"]["density"],
            "velocity": 2,
        }
        call = ToolCallPart("pressure_drop", args)
    else:
        args = {
            "value": round(done["pressure_drop"] / 1000, 2),
            "unit": "kPa",
            "regime": "turbulent",
            "assumptions": ["smooth pipe"],
            "explanation": "Darcy-Weisbach with Colebrook.",
        }
        call = ToolCallPart(info.output_tools[0].name, args)
    return ModelResponse(parts=[call])


def run_scripted_evaluation(dataset: Dataset):
    """Evaluate `dataset` offline with the scripted agent, as run_evals does with a real model."""
    from eng_agent_evals.run_evals import make_task

    logfire.configure(send_to_logfire=False, console=False)
    logfire.instrument_pydantic_ai()
    task = make_task(build_agent(FunctionModel(_scripted_pressure_drop)))
    return with_trajectory_evaluators(dataset).evaluate_sync(task, progress=False)


def scripted_case(name: str = "pd", case_type: str = "pressure_drop", **expected) -> Case:
    w = physics.water_properties(20)
    re = physics.reynolds_number(2, 0.05, w.density, w.dynamic_viscosity)
    expected_dp = physics.pressure_drop(physics.friction_factor(re), 30, 0.05, w.density, 2)
    trajectory = ["convert_units", "water_properties", "reynolds_number", "friction_factor", "pressure_drop"]
    return Case(
        name=name,
        inputs="Water at 20 °C flows at 2 m/s through 30 m of 50 mm smooth pipe. What is the pressure drop?",
        expected_output=_expected(**{"value": expected_dp, **expected}),
        metadata=CaseMetadata(case_type=case_type, expected_trajectory=trajectory, tags=[]),
    )


def test_end_to_end_with_scripted_agent():
    report = run_scripted_evaluation(Dataset(name="one_case", cases=[scripted_case()]))
    assert not report.failures, report.failures[0].error_message
    case = report.cases[0]
    assertions = {name: r.value for name, r in case.assertions.items()}

    assert assertions == {
        "ValueWithinTolerance": True,
        "RegimeCorrect": True,
        "FlagsMissingInputs": True,
        "ValueTraceableToTool": True,
        "InputsTraceable": True,
        "tool_correctness": True,
    }
    # The output tool is not part of the span-based trajectory, so the match is exact.
    assert case.scores["trajectory"].value == 1.0
    assert case.scores["tool_calls"].value == 5
    assert case.scores["retries"].value == 0
    assert case.output.usage["requests"] == 6


def test_trajectory_evaluators_attached_only_where_a_trajectory_is_defined():
    from eng_agent_evals.dataset import build_dataset

    dataset = with_trajectory_evaluators(build_dataset())
    for case in dataset.cases:
        names = {e.evaluation_name for e in case.evaluators}
        if case.metadata.expected_trajectory is None:
            assert names == set(), case.name
        else:
            assert names == {"trajectory", "tool_correctness"}, case.name
    assert len(dataset.evaluators) == 7
