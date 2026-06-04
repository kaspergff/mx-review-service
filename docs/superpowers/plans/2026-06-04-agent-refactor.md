# Agent-based Mendix Code Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the static diff + single LLM call with an agentic loop that uses mxcli to query the Mendix project directly.

**Architecture:** FastAPI webhook returns 202 immediately and enqueues a background task. The task clones the repo, runs an agentic loop where the LLM calls read-only mxcli tools, then posts the review to Teams.

**Tech Stack:** Python 3.12, FastAPI BackgroundTasks, LiteLLM tool use, mxcli v0.12.0, asyncio

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `agent/__init__.py` | Create | Package marker |
| `agent/repo.py` | Create | git clone, find .mpr, cleanup |
| `agent/tools.py` | Create | mxcli tool functions + LiteLLM schema definitions |
| `agent/loop.py` | Create | Agentic loop with max-calls and timeout |
| `prompts/system_prompt.md` | Modify | Rewrite for navigating agent |
| `server.py` | Modify | 202, BackgroundTasks, wire agent |
| `mendix/parser.py` | Delete | Replaced by mxcli |
| `tests/test_agent_repo.py` | Create | Unit tests for repo.py |
| `tests/test_agent_tools.py` | Create | Unit tests for tools.py |
| `tests/test_agent_loop.py` | Create | Integration tests for loop.py |
| `tests/test_server.py` | Modify | Update for 202 and new mocks |
| `Dockerfile` | Create | Image with git + mxcli binary |
| `.env.example` | Modify | Add REVIEW_TIMEOUT_SECONDS |

---

## Task 0: Verify mxcli works without JDK

Manual verification before writing any code. mxcli is a Go binary — JDK should only be needed for MxBuild (compiling), not for read-only queries.

**Files:** none

- [ ] **Step 1: Download mxcli binary locally**

```bash
curl -L https://github.com/mendixlabs/mxcli/releases/download/v0.12.0/mxcli-linux-amd64 -o /tmp/mxcli
chmod +x /tmp/mxcli
/tmp/mxcli --version
```

Expected: prints version without requiring Java.

- [ ] **Step 2: Run a read-only query against your local .mpr**

```bash
/tmp/mxcli describe -p /path/to/your/app.mpr <Module.SomeElement>
```

Expected: structured output, no JDK error. If you see `java not found` or similar → add `eclipse-temurin:21-jre-alpine` to the Dockerfile base image.

- [ ] **Step 3: Record result**

Document whether JDK is needed in the spec open points section. This determines the Dockerfile base image in Task 7.

---

## Task 1: agent/repo.py

Clone the Mendix repo, find the `.mpr` file, and clean up after use.

**Files:**
- Create: `agent/__init__.py`
- Create: `agent/repo.py`
- Create: `tests/test_agent_repo.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_agent_repo.py`:

```python
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest

from agent.repo import clone_repo, find_mpr


def test_find_mpr_finds_file(tmp_path):
    mpr = tmp_path / "MyApp.mpr"
    mpr.write_bytes(b"")
    result = find_mpr(str(tmp_path))
    assert result == str(mpr)


def test_find_mpr_raises_when_missing(tmp_path):
    with pytest.raises(FileNotFoundError, match="No .mpr"):
        find_mpr(str(tmp_path))


def test_clone_repo_calls_git_with_pat():
    captured = []

    def fake_run(args, **kwargs):
        captured.append(args)
        r = MagicMock()
        r.stdout = b""
        return r

    with patch("agent.repo.subprocess.run", side_effect=fake_run):
        clone_repo(
            app_id="abc-123",
            target="/tmp/fake",
            git_base_url="https://git.api.mendix.com",
            mx_pat="mytoken",
        )

    assert len(captured) == 1
    args = captured[0]
    assert "git" in args
    assert "clone" in args
    assert "--depth" in args
    assert any("pat:mytoken" in str(a) for a in args)
    assert "https://git.api.mendix.com/abc-123.git" in args


def test_clone_repo_raises_on_git_failure():
    with patch(
        "agent.repo.subprocess.run",
        side_effect=subprocess.CalledProcessError(128, "git", stderr=b"fatal: not found"),
    ):
        with pytest.raises(subprocess.CalledProcessError):
            clone_repo("abc", "/tmp/fake", "https://git.api.mendix.com", "pat")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/test_agent_repo.py -v
```

Expected: `ModuleNotFoundError: No module named 'agent'`

- [ ] **Step 3: Create package marker**

Create `agent/__init__.py` (empty file).

- [ ] **Step 4: Implement agent/repo.py**

Create `agent/repo.py`:

```python
import base64
import subprocess
from pathlib import Path


def find_mpr(repo_path: str) -> str:
    mprs = list(Path(repo_path).glob("*.mpr"))
    if not mprs:
        raise FileNotFoundError(f"No .mpr found in {repo_path}")
    return str(mprs[0])


def clone_repo(app_id: str, target: str, git_base_url: str, mx_pat: str) -> None:
    repo_url = f"{git_base_url}/{app_id}.git"
    auth_header = (
        "Authorization: Basic "
        + base64.b64encode(f"pat:{mx_pat}".encode()).decode()
    )
    args = ["git", "clone", "--depth", "50", "--no-single-branch"]
    if git_base_url.startswith("https://"):
        args += ["-c", f"http.{git_base_url}/.extraHeader={auth_header}"]
    args += [repo_url, target]
    subprocess.run(args, check=True, capture_output=True, timeout=120)
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/test_agent_repo.py -v
```

Expected: all 4 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add agent/__init__.py agent/repo.py tests/test_agent_repo.py
git commit -m "feat: add agent/repo.py — git clone and .mpr discovery"
```

---

## Task 2: agent/tools.py

Read-only mxcli tool functions and the LiteLLM tool schema list. All functions are synchronous — the loop will call them via `asyncio.to_thread`.

**Files:**
- Create: `agent/tools.py`
- Create: `tests/test_agent_tools.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_agent_tools.py`:

```python
import json
import subprocess
from unittest.mock import patch, MagicMock
import pytest

from agent.tools import (
    list_changed_files,
    describe_element,
    find_refs,
    find_callers,
    find_callees,
    lint_project,
    search,
    execute_tool,
    TOOL_SCHEMAS,
)


def _mock_run(stdout: str):
    r = MagicMock()
    r.stdout = stdout
    return r


def test_list_changed_files_returns_git_output():
    with patch("agent.tools.subprocess.run", return_value=_mock_run("M\tSales/ACT_Test.mxunit")) as mock:
        result = list_changed_files("/repo", "aaa", "bbb")
    assert "ACT_Test" in result
    args = mock.call_args[0][0]
    assert "diff" in args
    assert "--name-status" in args
    assert "aaa..bbb" in args


def test_list_changed_files_empty():
    with patch("agent.tools.subprocess.run", return_value=_mock_run("")):
        result = list_changed_files("/repo", "aaa", "bbb")
    assert result == "Geen wijzigingen gevonden."


def test_describe_element_calls_mxcli():
    with patch("agent.tools.subprocess.run", return_value=_mock_run("Microflow: ACT_Test")) as mock:
        result = describe_element("/app.mpr", "Sales.ACT_Test")
    assert "ACT_Test" in result
    args = mock.call_args[0][0]
    assert "describe" in args
    assert "Sales.ACT_Test" in args


def test_find_refs_calls_mxcli_refs():
    with patch("agent.tools.subprocess.run", return_value=_mock_run("refs output")) as mock:
        result = find_refs("/app.mpr", "Sales.Customer")
    assert "refs output" in result
    args = mock.call_args[0][0]
    assert "refs" in args
    assert "Sales.Customer" in args


def test_find_callers_plain():
    with patch("agent.tools.subprocess.run", return_value=_mock_run("caller: MF_A")) as mock:
        result = find_callers("/app.mpr", "Sales.ACT_Test", transitive=False)
    args = mock.call_args[0][0]
    assert "callers" in args
    assert "--transitive" not in args


def test_find_callers_transitive():
    with patch("agent.tools.subprocess.run", return_value=_mock_run("chain")) as mock:
        find_callers("/app.mpr", "Sales.ACT_Test", transitive=True)
    args = mock.call_args[0][0]
    assert "--transitive" in args


def test_find_callees_calls_mxcli():
    with patch("agent.tools.subprocess.run", return_value=_mock_run("callee output")) as mock:
        find_callees("/app.mpr", "Sales.ACT_Test")
    args = mock.call_args[0][0]
    assert "callees" in args


def test_lint_project_calls_mxcli_lint():
    with patch("agent.tools.subprocess.run", return_value=_mock_run("lint: 3 issues")) as mock:
        result = lint_project("/app.mpr")
    assert "lint" in mock.call_args[0][0]
    assert "issues" in result


def test_search_calls_mxcli_search():
    with patch("agent.tools.subprocess.run", return_value=_mock_run("match: line 42")) as mock:
        search("/app.mpr", "commit transaction")
    args = mock.call_args[0][0]
    assert "search" in args
    assert "commit transaction" in args


def test_execute_tool_dispatches_list_changed_files():
    tc = MagicMock()
    tc.function.name = "list_changed_files"
    tc.function.arguments = "{}"
    ctx = {"repo_path": "/repo", "mpr_path": "/app.mpr", "before": "a" * 40, "after": "b" * 40}
    with patch("agent.tools.list_changed_files", return_value="changed") as mock:
        result = execute_tool(tc, ctx)
    mock.assert_called_once_with("/repo", "a" * 40, "b" * 40)
    assert result == "changed"


def test_execute_tool_dispatches_describe_element():
    tc = MagicMock()
    tc.function.name = "describe_element"
    tc.function.arguments = json.dumps({"name": "Sales.ACT_Test"})
    ctx = {"repo_path": "/repo", "mpr_path": "/app.mpr", "before": "a" * 40, "after": "b" * 40}
    with patch("agent.tools.describe_element", return_value="desc") as mock:
        execute_tool(tc, ctx)
    mock.assert_called_once_with("/app.mpr", "Sales.ACT_Test")


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
    expected = {"list_changed_files", "describe_element", "find_refs", "find_callers", "find_callees", "lint_project", "search"}
    assert names == expected


def test_mxcli_error_returns_error_string():
    with patch("agent.tools.subprocess.run", side_effect=subprocess.CalledProcessError(1, "mxcli", stderr=b"element not found")):
        result = describe_element("/app.mpr", "Sales.DoesNotExist")
    assert "fout" in result.lower() or "error" in result.lower()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/test_agent_tools.py -v
```

Expected: `ModuleNotFoundError: cannot import name 'list_changed_files' from 'agent.tools'`

- [ ] **Step 3: Implement agent/tools.py**

Create `agent/tools.py`:

```python
import json
import subprocess
from typing import Any

MXCLI = "mxcli"


def list_changed_files(repo_path: str, before: str, after: str) -> str:
    result = subprocess.run(
        ["git", "-C", repo_path, "diff", "--name-status", f"{before}..{after}"],
        check=True, capture_output=True, text=True, timeout=30,
    )
    return result.stdout.strip() or "Geen wijzigingen gevonden."


def describe_element(mpr_path: str, name: str) -> str:
    return _mxcli(mpr_path, ["describe", name])


def find_refs(mpr_path: str, name: str) -> str:
    return _mxcli(mpr_path, ["refs", name])


def find_callers(mpr_path: str, name: str, transitive: bool = False) -> str:
    args = ["callers", name]
    if transitive:
        args.append("--transitive")
    return _mxcli(mpr_path, args)


def find_callees(mpr_path: str, name: str) -> str:
    return _mxcli(mpr_path, ["callees", name])


def lint_project(mpr_path: str) -> str:
    return _mxcli(mpr_path, ["lint"])


def search(mpr_path: str, query: str) -> str:
    return _mxcli(mpr_path, ["search", query])


def _mxcli(mpr_path: str, args: list[str]) -> str:
    try:
        result = subprocess.run(
            [MXCLI, "-p", mpr_path] + args,
            check=True, capture_output=True, text=True, timeout=60,
        )
        return result.stdout.strip() or "(geen output)"
    except subprocess.CalledProcessError as e:
        return f"mxcli fout: {e.stderr.strip()}"


def execute_tool(tool_call: Any, ctx: dict) -> str:
    name = tool_call.function.name
    args = json.loads(tool_call.function.arguments)
    repo_path = ctx["repo_path"]
    mpr_path = ctx["mpr_path"]

    match name:
        case "list_changed_files":
            return list_changed_files(repo_path, ctx["before"], ctx["after"])
        case "describe_element":
            return describe_element(mpr_path, args["name"])
        case "find_refs":
            return find_refs(mpr_path, args["name"])
        case "find_callers":
            return find_callers(mpr_path, args["name"], args.get("transitive", False))
        case "find_callees":
            return find_callees(mpr_path, args["name"])
        case "lint_project":
            return lint_project(mpr_path)
        case "search":
            return search(mpr_path, args["query"])
        case _:
            return f"Onbekende tool: {name}"


TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "list_changed_files",
            "description": "Geeft de bestanden die gewijzigd zijn in deze commit (A=toegevoegd, M=gewijzigd, D=verwijderd).",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "describe_element",
            "description": "Volledige definitie van een Mendix element (microflow, entiteit, pagina, etc.).",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Gekwalificeerde naam, bijv. Sales.ACT_CreateOrder"},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_refs",
            "description": "Alle inkomende + uitgaande referenties van een element. Gebruik voor blast-radius analyse.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Gekwalificeerde naam van het element"},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_callers",
            "description": "Welke microflows roepen dit element aan. Gebruik transitive=true voor de volledige call chain.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Gekwalificeerde naam van het element"},
                    "transitive": {"type": "boolean", "description": "Volledige transitive call chain", "default": False},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_callees",
            "description": "Wat roept dit element aan — directe afhankelijkheden.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Gekwalificeerde naam van het element"},
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "lint_project",
            "description": "Voert kwaliteitsregels uit op het hele project.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search",
            "description": "Zoekt door log messages, captions en expressies in het project.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Zoekterm"},
                },
                "required": ["query"],
            },
        },
    },
]
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/test_agent_tools.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add agent/tools.py tests/test_agent_tools.py
git commit -m "feat: add agent/tools.py — read-only mxcli tool functions"
```

---

## Task 3: agent/loop.py

The agentic loop: sends messages to the LLM, executes tool calls, and repeats until the model returns a final answer or limits are hit.

**Files:**
- Create: `agent/loop.py`
- Create: `tests/test_agent_loop.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_agent_loop.py`:

```python
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
        _tool_call_response("list_changed_files", {}),
        _stop_response("Geen bevindingen."),
    ]
    payload = MagicMock()
    payload.before = "a" * 40
    payload.after = "b" * 40
    payload.authorName = "Alice"
    payload.branchName = "main"
    payload.commitMessage = "Fix bug"

    with patch("litellm.acompletion", new_callable=AsyncMock, side_effect=responses), \
         patch("agent.loop.execute_tool", return_value="file1.mxunit"):
        result = await run_agent(payload, "/repo", "/app.mpr", "claude-sonnet", timeout=60)

    assert result == "Geen bevindingen."


@pytest.mark.asyncio
async def test_loop_stops_at_max_tool_calls():
    responses = [_tool_call_response("list_changed_files", {})] * 30

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
            return _tool_call_response("list_changed_files", {})
        return _stop_response("Review gedaan.")

    payload = MagicMock()
    payload.before = "a" * 40
    payload.after = "b" * 40
    payload.authorName = "Alice"
    payload.branchName = "main"
    payload.commitMessage = "Fix"

    with patch("litellm.acompletion", new_callable=AsyncMock, side_effect=fake_completion), \
         patch("agent.loop.execute_tool", return_value="changed: ACT_Test.mxunit"):
        await run_agent(payload, "/repo", "/app.mpr", "claude-sonnet", timeout=60)

    roles = [m["role"] for m in captured_messages if isinstance(m, dict)]
    assert "tool" in roles
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/bin/pytest tests/test_agent_loop.py -v
```

Expected: `ModuleNotFoundError: No module named 'agent.loop'`

- [ ] **Step 3: Implement agent/loop.py**

Create `agent/loop.py`:

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
.venv/bin/pytest tests/test_agent_loop.py -v
```

Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add agent/loop.py tests/test_agent_loop.py
git commit -m "feat: add agent/loop.py — agentic review loop with tool use"
```

---

## Task 4: Rewrite system_prompt.md

The existing prompt is written for a static diff. It needs to guide a navigating agent.

**Files:**
- Modify: `prompts/system_prompt.md`

- [ ] **Step 1: Replace system_prompt.md**

Replace the entire contents of `prompts/system_prompt.md`:

```markdown
Je bent een senior Mendix developer die een commit reviewt. Je hebt tools om het project zelf te bevragen — gebruik ze om te begrijpen wat er veranderd is en wat het risico is.

## Werkwijze

1. Begin altijd met `list_changed_files` om te zien welke elementen gewijzigd zijn.
2. Gebruik `describe_element` om te begrijpen wat een gewijzigd element doet.
3. Gebruik `find_refs` als je wilt weten wat er afhangt van een gewijzigd element (blast radius).
4. Gebruik `find_callers` of `find_callees` als je de call-richting specifiek wilt traceren.
5. Wees selectief: niet elk element heeft diepere analyse nodig. Focus op wat riskant lijkt.
6. Gebruik `search` als je een specifiek patroon wilt zoeken (bijv. een hardcoded waarde of aanroep).
7. Gebruik `lint_project` als je twijfelt of er kwaliteitsproblemen zijn.
8. Geef je final review zodra je genoeg weet. Je hoeft niet alle tools te gebruiken.

## Denkwijze

Trace de logica mentaal: welke data stroomt er doorheen, wie kan dit aanroepen, wat gebeurt er als een aanname niet klopt? Denk in scenario's, niet in categorieën. Wat doet een gebruiker met meer rechten dan verwacht? Wat als een externe call faalt? Wat als de lijst leeg is?

Rapporteer alleen wat je echt zorgelijk vindt: een scenario waarbij iets kapot gaat, data lekt, of een gebruiker iets kan doen wat niet de bedoeling is.

---

## Outputformaat

Eerste regel: één zin die samenvat wat er gewijzigd is en wat de potentiële impact is.

Daarna per bevinding één bullet, gesorteerd op ernst:
- 🔴 kritiek: direct exploiteerbaar of dataverlies in productie
- 🟡 middel: risico onder specifieke omstandigheden
- 🟢 laag: verborgen tijdbom of tech debt met toekomstig risico

Formaat per bullet: `🔴 ElementNaam — bevinding`

Regels:
- Maximaal 8 bevindingen
- Alleen problemen — geen positieve observaties
- Als er niets is: alleen "Geen bevindingen."
- Taal: Nederlands
- Max ~300 woorden totaal
```

- [ ] **Step 2: Commit**

```bash
git add prompts/system_prompt.md
git commit -m "docs: herschrijf system prompt voor navigerende agent"
```

---

## Task 5: Update server.py

Return 202, use BackgroundTasks, wire the agent loop. Remove old `review_diff` and `get_diff`.

**Files:**
- Modify: `server.py`
- Modify: `tests/test_server.py`

- [ ] **Step 1: Update the endpoint test**

In `tests/test_server.py`, first update the import at the top of the file:

```python
# was:
from server import app, verify_signature, WEBHOOK_SECRET, ReviewRequest, get_diff
# becomes:
from server import app, verify_signature, WEBHOOK_SECRET, ReviewRequest
```

Then find `test_review_endpoint_full_flow` and replace it:

```python
def test_review_endpoint_returns_202():
    import json
    body = json.dumps({
        "appId": VALID_APP_ID_STR,
        "before": "a" * 40,
        "after": "b" * 40,
        "branchName": "main",
        "authorName": "Alice",
        "commitMessage": "Fix bug",
    }).encode()

    with patch("server._run_review", new_callable=AsyncMock):
        response = client.post("/review", content=body, headers=_make_review_headers(body))

    assert response.status_code == 202
    assert response.json() == {"status": "accepted"}
```

Also remove `test_get_diff_returns_parsed_markdown`, `test_get_diff_truncates_at_limit`, `test_get_diff_subprocess_error_raises`, `test_review_diff_returns_text`, `test_review_diff_raises_on_api_error`, `test_get_diff_clone_depth_handles_multi_commit_push`, `test_get_diff_tmpdir_cleaned_up_on_clone_failure`, `test_get_diff_truncation_stops_at_section_boundary`, and `test_review_diff_uses_sufficient_max_tokens` — these test code that will be deleted.

- [ ] **Step 2: Run updated tests to verify they fail**

```bash
.venv/bin/pytest tests/test_server.py::test_review_endpoint_returns_202 -v
```

Expected: FAIL — endpoint still returns 200.

- [ ] **Step 3: Rewrite server.py**

Replace `server.py` with the following. Keep `verify_signature`, `ReviewRequest`, `post_to_teams`, `health`, and the app definition. Remove `get_diff`, `review_diff`, `_parse_mxunit_at`, `_format_mxunit_change`, `_load_system_prompt`, `_SYSTEM_PROMPT_PATH`, and the old `review` endpoint.

```python
import asyncio
import logging
import os
import base64
import hmac
import hashlib
import time
import re
import shutil
import tempfile

logger = logging.getLogger(__name__)

import httpx
from agent.loop import run_agent
from agent.repo import clone_repo, find_mpr
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator
from dotenv import load_dotenv

load_dotenv()

MX_PAT = os.environ.get("MX_PAT", "")
LLM_MODEL = os.environ["LLM_MODEL"]
TEAMS_WEBHOOK_URL = os.environ.get("TEAMS_WEBHOOK_URL", "")
WEBHOOK_SECRET = os.environ["WEBHOOK_SECRET"]
_raw_app_ids = os.environ.get("ALLOWED_APP_IDS", "")
ALLOWED_APP_IDS: set[str] = set(_raw_app_ids.split(",")) - {""} if _raw_app_ids else set()
MX_GIT_BASE_URL = os.environ.get("MX_GIT_BASE_URL", "https://git.api.mendix.com")
MX_LOCAL_REPO = os.environ.get("MX_LOCAL_REPO", "")
REVIEW_TIMEOUT_SECONDS = int(os.environ.get("REVIEW_TIMEOUT_SECONDS", "300"))


def verify_signature(webhook_id: str, timestamp: str, signature_header: str, body: bytes) -> None:
    """Verify Mendix HMAC-SHA256 webhook signature and reject replays > 5 min."""
    try:
        ts = int(timestamp)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid timestamp")

    if abs(time.time() - ts) > 300:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Request too old")

    msg = f"{webhook_id}.{timestamp}.".encode() + body
    expected_mac = hmac.new(WEBHOOK_SECRET.encode(), msg, hashlib.sha256).digest()
    expected_sig = "v1," + base64.b64encode(expected_mac).decode()

    sigs = [s.strip() for s in signature_header.split() if s.strip()]
    if not any(hmac.compare_digest(sig, expected_sig) for sig in sigs):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid signature")


class ReviewRequest(BaseModel):
    appId: str
    before: str
    after: str
    branchName: str
    authorName: str
    commitMessage: str

    @field_validator("appId")
    @classmethod
    def app_id_allowed(cls, v: str) -> str:
        if ALLOWED_APP_IDS and v not in ALLOWED_APP_IDS:
            raise ValueError(f"appId '{v}' is not in ALLOWED_APP_IDS")
        return v

    @field_validator("before", "after")
    @classmethod
    def valid_commit_hash(cls, v: str) -> str:
        if not re.fullmatch(r"[0-9a-f]{40}", v):
            raise ValueError("Commit hash must be exactly 40 lowercase hex characters")
        return v


async def post_to_teams(
    author: str,
    commit_hash: str,
    commit_message: str,
    branch: str,
    review: str,
) -> None:
    """Post an Adaptive Card to the configured Teams webhook."""
    card = {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": [
                        {
                            "type": "FactSet",
                            "facts": [
                                {"title": "Author", "value": author},
                                {"title": "Branch", "value": branch},
                                {"title": "Commit", "value": commit_hash[:12]},
                                {"title": "Message", "value": commit_message},
                            ],
                        },
                        {"type": "TextBlock", "text": "**Code Review**", "weight": "Bolder"},
                        {"type": "TextBlock", "text": review, "wrap": True},
                    ],
                },
            }
        ],
    }
    async with httpx.AsyncClient(timeout=10) as client:
        response = await client.post(TEAMS_WEBHOOK_URL, json=card)

    if response.status_code not in (200, 201):
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Teams webhook error {response.status_code}",
        )


async def _run_review(payload: ReviewRequest) -> None:
    """Background task: clone repo, run agent, post to Teams."""
    tmp_dir = None
    try:
        if MX_LOCAL_REPO:
            repo_path = MX_LOCAL_REPO
        else:
            tmp_dir = tempfile.mkdtemp()
            await asyncio.to_thread(
                clone_repo, payload.appId, tmp_dir, MX_GIT_BASE_URL, MX_PAT
            )
            repo_path = tmp_dir

        mpr_path = find_mpr(repo_path)
        review_text = await run_agent(
            payload=payload,
            repo_path=repo_path,
            mpr_path=mpr_path,
            model=LLM_MODEL,
            timeout=REVIEW_TIMEOUT_SECONDS,
        )

        if TEAMS_WEBHOOK_URL:
            await post_to_teams(
                author=payload.authorName,
                commit_hash=payload.after,
                commit_message=payload.commitMessage,
                branch=payload.branchName,
                review=review_text,
            )
        else:
            logger.info(
                "[review] %s %s %s\n%s",
                payload.authorName, payload.branchName, payload.after[:12], review_text,
            )
    except Exception:
        logger.exception("Review failed for commit %s", payload.after[:12])
    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)


app = FastAPI(docs_url=None, redoc_url=None)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/review", status_code=202)
async def review(request: Request, background_tasks: BackgroundTasks) -> JSONResponse:
    body = await request.body()

    webhook_id = request.headers.get("webhook-id")
    webhook_timestamp = request.headers.get("webhook-timestamp")
    webhook_signature = request.headers.get("webhook-signature")

    if not all([webhook_id, webhook_timestamp, webhook_signature]):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing signature headers")

    verify_signature(webhook_id, webhook_timestamp, webhook_signature, body)

    try:
        payload = ReviewRequest.model_validate_json(body)
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))

    background_tasks.add_task(_run_review, payload)
    return JSONResponse({"status": "accepted"}, status_code=202)
```

- [ ] **Step 4: Run server tests**

```bash
.venv/bin/pytest tests/test_server.py -v
```

Expected: all remaining tests PASS (signature tests, validation tests, Teams tests, health).

- [ ] **Step 5: Commit**

```bash
git add server.py tests/test_server.py
git commit -m "feat: refactor server.py — 202 response, background task, agent loop"
```

---

## Task 6: Delete old code

**Files:**
- Delete: `mendix/parser.py`
- Delete: `mendix/__init__.py`

- [ ] **Step 1: Remove mendix package**

```bash
rm mendix/parser.py
rm mendix/__init__.py
rmdir mendix
```

- [ ] **Step 2: Clean up test_server.py imports**

In `tests/test_server.py`, find and remove:
```python
from server import app, verify_signature, WEBHOOK_SECRET, ReviewRequest, get_diff
```
Replace with:
```python
from server import app, verify_signature, WEBHOOK_SECRET, ReviewRequest
```

- [ ] **Step 3: Run all tests**

```bash
.venv/bin/pytest tests/ -v
```

Expected: all tests PASS, no import errors.

- [ ] **Step 4: Commit**

```bash
git add -u
git commit -m "chore: remove mendix/parser.py — replaced by mxcli"
```

---

## Task 7: Dockerfile and .env.example

**Files:**
- Create: `Dockerfile`
- Modify: `.env.example`

- [ ] **Step 1: Create Dockerfile**

Create `Dockerfile`. Use the JDK base image if Task 0 showed it's needed, otherwise use `python:3.12-slim`:

```dockerfile
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends git curl \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://github.com/mendixlabs/mxcli/releases/download/v0.12.0/mxcli-linux-amd64 \
    -o /usr/local/bin/mxcli \
    && chmod +x /usr/local/bin/mxcli

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 2: Add REVIEW_TIMEOUT_SECONDS to .env.example**

Open `.env.example` and add:
```
REVIEW_TIMEOUT_SECONDS=300
```

- [ ] **Step 3: Build the image to verify it works**

```bash
docker build -t mx-review-service .
```

Expected: build succeeds, mxcli binary present in image.

- [ ] **Step 4: Verify mxcli in image (no JDK test)**

```bash
docker run --rm mx-review-service mxcli --version
```

Expected: version printed, no Java error.

- [ ] **Step 5: Commit**

```bash
git add Dockerfile .env.example
git commit -m "feat: add Dockerfile with mxcli v0.12.0"
```

---

## Task 8: Full test suite

- [ ] **Step 1: Run the full suite**

```bash
.venv/bin/pytest tests/ -v
```

Expected: all tests PASS, no import errors, no deprecation warnings from pytest-asyncio.

- [ ] **Step 2: Verify no old references remain**

```bash
grep -r "parser\|get_diff\|review_diff\|DIFF_CHAR_LIMIT\|mxunit" server.py tests/ agent/
```

Expected: no matches (or only in comments/test fixtures that are valid).

- [ ] **Step 3: Final commit**

```bash
git add -u
git commit -m "chore: full agent refactor complete — mxcli replaces BSON parser"
```
