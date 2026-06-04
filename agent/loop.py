import asyncio
import logging
from pathlib import Path
from typing import Any

import litellm

from agent.tools import TOOL_SCHEMAS, execute_tool

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "system_prompt.md"

MAX_TOOL_CALLS = 25


def _load_system_prompt() -> str:
    return _SYSTEM_PROMPT_PATH.read_text(encoding="utf-8")


async def run_agent(
    payload: Any,
    repo_path: str,
    mpr_path: str,
    model: str,
    timeout: int,
    max_tool_calls: int = MAX_TOOL_CALLS,
) -> str:
    ctx = {
        "repo_path": repo_path,
        "mpr_path": mpr_path,
        "before": payload.before,
        "after": payload.after,
    }

    messages: list[dict] = [
        {"role": "system", "content": _load_system_prompt()},
        {
            "role": "user",
            "content": (
                f"Review commit {payload.after[:12]} door {payload.authorName} "
                f"op branch {payload.branchName}.\n"
                f"Commit message: {payload.commitMessage}"
            ),
        },
    ]

    tool_call_count = 0

    try:
        async with asyncio.timeout(timeout):
            while tool_call_count < max_tool_calls:
                response = await litellm.acompletion(
                    model=model,
                    messages=messages,
                    tools=TOOL_SCHEMAS,
                    max_tokens=2000,
                )
                choice = response.choices[0]
                msg = choice.message

                if choice.finish_reason == "stop" or not msg.tool_calls:
                    return msg.content or "Geen bevindingen."

                messages.append({
                    "role": "assistant",
                    "content": msg.content,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in msg.tool_calls
                    ],
                })

                for tc in msg.tool_calls:
                    tool_call_count += 1
                    result = await asyncio.to_thread(execute_tool, tc, ctx)
                    logger.debug("tool %s → %d chars", tc.function.name, len(result))
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result,
                    })

            return "Review onvolledig — limiet van tool-calls bereikt."

    except TimeoutError:
        return "Review onvolledig — timeout bereikt."
