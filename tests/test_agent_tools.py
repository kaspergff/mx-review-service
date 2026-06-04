import json
import subprocess
from unittest.mock import patch, MagicMock
import pytest

from agent.tools import (
    get_diff,
    get_context,
    lint_project,
    search,
    execute_tool,
    TOOL_SCHEMAS,
)


def _mock_run(stdout: str):
    r = MagicMock()
    r.stdout = stdout
    return r


def test_get_diff_calls_mxcli_diff_local():
    with patch("agent.tools.subprocess.run", return_value=_mock_run("--- Microflow ...")) as mock:
        result = get_diff("/repo", "/repo/App.mpr", "aaa", "bbb")
    args = mock.call_args[0][0]
    assert "diff-local" in args
    assert "--ref" in args
    assert "aaa..bbb" in args
    assert mock.call_args[1].get("cwd") == "/repo"
    assert "Microflow" in result


def test_get_diff_empty_returns_message():
    with patch("agent.tools.subprocess.run", return_value=_mock_run("")):
        result = get_diff("/repo", "/repo/App.mpr", "aaa", "bbb")
    assert "geen" in result.lower() or "no" in result.lower()


def test_get_context_calls_mxcli_context():
    with patch("agent.tools.subprocess.run", return_value=_mock_run("Context for Sales.ACT_Test")) as mock:
        result = get_context("/app.mpr", "Sales.ACT_Test")
    args = mock.call_args[0][0]
    assert "context" in args
    assert "Sales.ACT_Test" in args
    assert "ACT_Test" in result


def test_get_context_passes_depth():
    with patch("agent.tools.subprocess.run", return_value=_mock_run("context")) as mock:
        get_context("/app.mpr", "Sales.ACT_Test", depth=3)
    args = mock.call_args[0][0]
    assert "--depth" in args
    assert "3" in args


def test_lint_project_calls_mxcli_lint():
    with patch("agent.tools.subprocess.run", return_value=_mock_run("3 issues found")) as mock:
        result = lint_project("/repo", "/app.mpr")
    assert "lint" in mock.call_args[0][0]
    assert "issues" in result


def test_search_calls_mxcli_search():
    with patch("agent.tools.subprocess.run", return_value=_mock_run("match: line 42")) as mock:
        search("/app.mpr", "commit transaction")
    args = mock.call_args[0][0]
    assert "search" in args
    assert "commit transaction" in args


def test_execute_tool_dispatches_get_diff():
    tc = MagicMock()
    tc.function.name = "get_diff"
    tc.function.arguments = "{}"
    ctx = {"repo_path": "/repo", "mpr_path": "/app.mpr", "before": "a" * 40, "after": "b" * 40}
    with patch("agent.tools.get_diff", return_value="diff output") as mock:
        result = execute_tool(tc, ctx)
    mock.assert_called_once_with("/repo", "/app.mpr", "a" * 40, "b" * 40)
    assert result == "diff output"


def test_execute_tool_dispatches_get_context():
    tc = MagicMock()
    tc.function.name = "get_context"
    tc.function.arguments = json.dumps({"name": "Sales.ACT_Test"})
    ctx = {"repo_path": "/repo", "mpr_path": "/app.mpr", "before": "a" * 40, "after": "b" * 40}
    with patch("agent.tools.get_context", return_value="ctx") as mock:
        execute_tool(tc, ctx)
    mock.assert_called_once_with("/app.mpr", "Sales.ACT_Test", depth=2)


def test_execute_tool_dispatches_get_context_with_depth():
    tc = MagicMock()
    tc.function.name = "get_context"
    tc.function.arguments = json.dumps({"name": "Sales.ACT_Test", "depth": 4})
    ctx = {"repo_path": "/repo", "mpr_path": "/app.mpr", "before": "a" * 40, "after": "b" * 40}
    with patch("agent.tools.get_context", return_value="ctx") as mock:
        execute_tool(tc, ctx)
    mock.assert_called_once_with("/app.mpr", "Sales.ACT_Test", depth=4)


def test_execute_tool_unknown_returns_error():
    tc = MagicMock()
    tc.function.name = "delete_everything"
    tc.function.arguments = "{}"
    ctx = {"repo_path": "/repo", "mpr_path": "/app.mpr", "before": "a" * 40, "after": "b" * 40}
    result = execute_tool(tc, ctx)
    assert "Onbekende tool" in result


def test_tool_schemas_are_read_only():
    names = {t["function"]["name"] for t in TOOL_SCHEMAS}
    forbidden = {"create", "execute", "delete", "move", "alter"}
    assert not names & forbidden, f"Schrijf-tools gevonden: {names & forbidden}"


def test_tool_schemas_cover_all_tools():
    names = {t["function"]["name"] for t in TOOL_SCHEMAS}
    assert names == {"get_diff", "get_context", "lint_project", "search"}


def test_mxcli_error_returns_error_string():
    with patch(
        "agent.tools.subprocess.run",
        side_effect=subprocess.CalledProcessError(1, "mxcli", stderr=b"element not found"),
    ):
        result = get_context("/app.mpr", "Sales.DoesNotExist")
    assert "fout" in result.lower() or "error" in result.lower()
