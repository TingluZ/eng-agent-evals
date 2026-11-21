"""Agent wiring tests. No API calls: the LLM is replaced by scripted fake models.

These check that the tools are registered with the right schemas, that tool
calls reach physics.py, that bad inputs come back to the model as retry
prompts, and that the final answer validates against the Answer schema. They
do not say anything about how well a real LLM uses the tools; that is what the
evals are for.
"""

from __future__ import annotations

import pytest
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel

from eng_agent_evals import physics
from eng_agent_evals.agent import Answer, build_agent

EXPECTED_TOOLS = {
    "convert_units",
    "water_properties",
    "mean_velocity",
    "reynolds_number",
    "relative_roughness",
    "pressure_drop",
    "heat_transfer_coefficient",
}


def _returns(messages: list[ModelMessage]) -> list[ToolReturnPart]:
    return [
        part
        for msg in messages
        if isinstance(msg, ModelRequest)
        for part in msg.parts
        if isinstance(part, ToolReturnPart)
    ]


def _tool_returns(messages: list[ModelMessage]) -> list[ToolReturnPart]:
    """Successful calls to the agent's own tools (not the final-output tool)."""
    return [r for r in _returns(messages) if r.tool_name in EXPECTED_TOOLS and r.outcome != "retried"]


def _retries(messages: list[ModelMessage]) -> list[ToolReturnPart]:
    """Tool calls that raised ModelRetry; the error text is sent back to the model."""
    return [r for r in _returns(messages) if r.outcome == "retried"]


def test_all_tools_are_registered_with_descriptions():
    seen: dict[str, str] = {}

    def capture(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        seen.update({t.name: t.description or "" for t in info.function_tools})
        args = {"value": 1.0, "unit": "Pa", "regime": None, "explanation": "x"}
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, args)])

    build_agent(FunctionModel(capture)).run_sync("hello")
    assert set(seen) == EXPECTED_TOOLS
    assert all(seen.values()), "every tool needs a docstring: it is what the LLM reads"


def test_output_validates_against_answer_schema():
    output = {
        "value": 25.4,
        "unit": "kPa",
        "regime": "turbulent",
        "assumptions": ["smooth pipe"],
        "missing_inputs": [],
        "explanation": "Darcy-Weisbach with Colebrook friction factor.",
    }
    result = build_agent(TestModel(call_tools=[], custom_output_args=output)).run_sync("q")
    assert isinstance(result.output, Answer)
    assert result.output.value == 25.4
    assert result.output.unit == "kPa"


def test_scripted_pressure_drop_trajectory_matches_reference():
    """A fake LLM that follows the intended trajectory, reading each tool result
    from the message history, should reproduce the reference answer exactly."""
    v, d_mm, length, t = 2.0, 50.0, 30.0, 20.0

    def script(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        returns = {r.tool_name: r.content for r in _tool_returns(messages)}
        if "convert_units" not in returns:
            call = ToolCallPart("convert_units", {"value": d_mm, "from_unit": "mm", "to_unit": "m"})
        elif "pressure_drop" not in returns:
            args = {"velocity": v, "diameter": returns["convert_units"], "length": length, "temperature_c": t}
            call = ToolCallPart("pressure_drop", args)
        else:
            result = returns["pressure_drop"]
            args = {
                "value": result["pressure_drop"],
                "unit": "Pa",
                "regime": result["regime"],
                "assumptions": ["smooth pipe"],
                "explanation": "Darcy-Weisbach with Colebrook.",
            }
            call = ToolCallPart(info.output_tools[0].name, args)
        return ModelResponse(parts=[call])

    result = build_agent(FunctionModel(script)).run_sync("pressure drop?")

    w = physics.water_properties(t)
    re = physics.reynolds_number(v, d_mm / 1000, w.density, w.dynamic_viscosity)
    expected = physics.pressure_drop(physics.friction_factor(re), length, d_mm / 1000, w.density, v)

    assert result.output.value == pytest.approx(expected)
    assert result.output.regime == "turbulent"
    called = [r.tool_name for r in _tool_returns(result.all_messages())]
    assert called == ["convert_units", "pressure_drop"]


def test_parallel_tool_calls_are_disabled():
    assert build_agent(TestModel()).model_settings == {"parallel_tool_calls": False}


def test_invalid_tool_input_is_returned_to_the_model_as_a_retry():
    """Out-of-range temperature: the model should see the error and can correct itself."""

    def script(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        retries = _retries(messages)
        if not retries:
            return ModelResponse(parts=[ToolCallPart("water_properties", {"temperature_c": 150.0})])
        args = {
            "value": None,
            "unit": "Pa",
            "regime": None,
            "missing_inputs": ["temperature within 0-100 °C"],
            "explanation": str(retries[0].content),
        }
        return ModelResponse(parts=[ToolCallPart(info.output_tools[0].name, args)])

    result = build_agent(FunctionModel(script)).run_sync("water at 150 °C?")
    assert result.output.value is None
    assert "outside the table range" in result.output.explanation
