import json
import subprocess
from typing import Any

MXCLI = "mxcli"


def get_diff(repo_path: str, mpr_path: str, before: str, after: str) -> str:
    try:
        result = subprocess.run(
            [MXCLI, "diff-local", "-p", mpr_path, "--ref", f"{before}..{after}"],
            check=True, capture_output=True, text=True, timeout=60,
            cwd=repo_path,
        )
        return result.stdout.strip() or "Geen wijzigingen gevonden."
    except subprocess.CalledProcessError as e:
        return f"mxcli fout: {e.stderr.strip()}"


def get_context(mpr_path: str, name: str, depth: int = 2) -> str:
    return _mxcli(mpr_path, ["context", name, "--depth", str(depth)])


def lint_project(repo_path: str, mpr_path: str) -> str:
    return _mxcli(mpr_path, ["lint"])


def search(mpr_path: str, query: str) -> str:
    return _mxcli(mpr_path, ["search", query])


def _mxcli(mpr_path: str, args: list[str]) -> str:
    try:
        result = subprocess.run(
            [MXCLI, "-p", mpr_path] + args,
            check=True, capture_output=True, text=True, timeout=120,
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
        case "get_diff":
            return get_diff(repo_path, mpr_path, ctx["before"], ctx["after"])
        case "get_context":
            return get_context(mpr_path, args["name"], depth=args.get("depth", 2))
        case "lint_project":
            return lint_project(repo_path, mpr_path)
        case "search":
            return search(mpr_path, args["query"])
        case _:
            return f"Onbekende tool: {name}"


TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_diff",
            "description": (
                "Toont de MDL-diff van alle elementen die gewijzigd zijn in deze commit. "
                "Geeft leesbare before/after vergelijking in Mendix Definition Language. "
                "Roep dit altijd als eerste aan."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_context",
            "description": (
                "Haalt rijke context op voor een Mendix element (microflow, entiteit, pagina, etc.). "
                "Detecteert het type automatisch. Geeft definitie, callers, callees, gebruikte entiteiten "
                "en pagina's — alles wat je nodig hebt om risico in te schatten."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Gekwalificeerde naam, bijv. Sales.ACT_CreateOrder of Sales.Customer",
                    },
                    "depth": {
                        "type": "integer",
                        "description": "Diepte van call-chain traversal (default 2, max 4)",
                        "default": 2,
                    },
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
