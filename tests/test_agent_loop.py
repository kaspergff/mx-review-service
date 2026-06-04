import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from agent.loop import run_agent


def _tool_call_response(tool_name: str, args: dict, call_id: str = "call_1"):
    tc = MagicMock()
    tc.id = call_id
    tc.function.name = tool_name
    tc.function.arguments = json.dumps(args)

    msg = MagicMock()
    msg.content = None
    msg.tool_calls = [tc]

    choice = MagicMock()
    choice.finish_reason = "tool_calls"
    choice.message = msg

    resp = MagicMock()
    resp.choices = [choice]
    return resp


def _stop_response(content: str):
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = None

    choice = MagicMock()
    choice.finish_reason = "stop"
    choice.message = msg

    resp = MagicMock()
    resp.choices = [choice]
    return resp


@pytest.mark.asyncio
async def test_loop_returns_final_review():
    responses = [
        _tool_call_response("get_diff", {}),
        _stop_response("Geen bevindingen."),
    ]
    payload = MagicMock()
    payload.before = "a" * 40
    payload.after = "b" * 40
    payload.authorName = "Alice"
    payload.branchName = "main"
    payload.commitMessage = "Fix bug"

    with patch("litellm.acompletion", new_callable=AsyncMock, side_effect=responses), \
         patch("agent.loop.execute_tool", return_value="diff output"):
        result = await run_agent(payload, "/repo", "/app.mpr", "claude-sonnet", timeout=60)

    assert result == "Geen bevindingen."


@pytest.mark.asyncio
async def test_loop_stops_at_max_tool_calls():
    responses = [_tool_call_response("get_diff", {})] * 30

    payload = MagicMock()
    payload.before = "a" * 40
    payload.after = "b" * 40
    payload.authorName = "Alice"
    payload.branchName = "main"
    payload.commitMessage = "Fix"

    with patch("litellm.acompletion", new_callable=AsyncMock, side_effect=responses), \
         patch("agent.loop.execute_tool", return_value="output"):
        result = await run_agent(payload, "/repo", "/app.mpr", "claude-sonnet", timeout=60, max_tool_calls=3)

    assert "onvolledig" in result.lower() or "limiet" in result.lower()


@pytest.mark.asyncio
async def test_loop_respects_timeout():
    async def slow_completion(**kwargs):
        await asyncio.sleep(10)

    payload = MagicMock()
    payload.before = "a" * 40
    payload.after = "b" * 40
    payload.authorName = "Alice"
    payload.branchName = "main"
    payload.commitMessage = "Fix"

    with patch("litellm.acompletion", new_callable=AsyncMock, side_effect=slow_completion):
        result = await run_agent(payload, "/repo", "/app.mpr", "claude-sonnet", timeout=1)

    assert "timeout" in result.lower() or "onvolledig" in result.lower()


@pytest.mark.asyncio
async def test_loop_passes_tool_result_back():
    captured_messages = []

    async def fake_completion(**kwargs):
        captured_messages.extend(kwargs["messages"])
        if len(captured_messages) < 4:
            return _tool_call_response("get_diff", {})
        return _stop_response("Review gedaan.")

    payload = MagicMock()
    payload.before = "a" * 40
    payload.after = "b" * 40
    payload.authorName = "Alice"
    payload.branchName = "main"
    payload.commitMessage = "Fix"

    with patch("litellm.acompletion", new_callable=AsyncMock, side_effect=fake_completion), \
         patch("agent.loop.execute_tool", return_value="diff: ACT_Test changed"):
        await run_agent(payload, "/repo", "/app.mpr", "claude-sonnet", timeout=60)

    roles = [m["role"] for m in captured_messages if isinstance(m, dict)]
    assert "tool" in roles
