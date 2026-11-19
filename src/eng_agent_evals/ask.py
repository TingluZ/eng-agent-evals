"""Ask the agent one question from the command line, with Logfire tracing.

    uv run ask "Water at 20 °C flows at 2 m/s through 30 m of 50 mm smooth pipe. Pressure drop?"

Tracing is sent to Logfire only if LOGFIRE_TOKEN is set (or you ran `logfire auth`
and `logfire projects use`); otherwise it stays local and nothing is uploaded.
"""

from __future__ import annotations

import sys

import logfire

from .agent import build_agent


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit('usage: ask "your question"')
    logfire.configure(send_to_logfire="if-token-present", service_name="eng-agent-evals")
    logfire.instrument_pydantic_ai()

    result = build_agent().run_sync(" ".join(sys.argv[1:]))
    print(result.output.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
