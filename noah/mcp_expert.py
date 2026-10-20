"""An MCP server as one expert: Noah routes a message to the server, and the expert picks one of its tools and calls it.

Picking a tool, in three steps:
1. shortlist: the message, without the expert's name, is compared with each tool's description and examples (the same
   embeddings the embedding engine uses; keyword overlap where there are none) and the closest few are kept. Examples that
   are no request a person would type (code, the prompt echoed back; small models write those) are skipped;
2. pick: when the closest tool is clearly ahead, it; otherwise the model names one of the shortlist (structured output with
   their names as an enum, which any model can do). Then the model fills its arguments (the extractor, with that tool's
   schema). On a server with many similar tools, even a small model naming one of the shortlist beats the closest tool
   alone. With `model.choose_tool: true` the model instead calls a tool by tool calling, choosing and filling in one
   step: for models good at tool calling;
   Then the arguments are grounded: a value the message does not contain was made up by the model, so an optional one is
   dropped and a required one takes the expert's default (`defaults:` in expert.yaml, e.g. `path: D:/notes`) if it has one.
   A required one that is still a placeholder ("your_username") is asked for instead of sent; if the model's pick has
   such gaps, the closest tool is tried instead;
3. call: unless the tool is marked read-only (or the user said "always allow" for it), Noah asks first: the result carries
   `confirm` and the chat shows Run / Always allow / Cancel; Run goes to /api/mcp/run.

The expert's model (`model.id` in expert.yaml) picks the tool; without one, the orchestrator's tiny model does.
Tool examples are written once, when the server is added or its tools are refreshed (`write_examples`), and kept in
tools.json next to expert.yaml, where they can be edited like any routing text.
"""
from __future__ import annotations

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, cast

from . import embedder, mcp_client
from .engines.keyword import KeywordEngine
from .engines.llm_tools import NONE_TOOL, LLMToolsEngine
from .extractors import ArgExtractor, validate_args
from .extractors.openai_compat import OpenAICompatExtractor
from .llm import LLMError, chat
from .types import Expert

SHORTLIST = 6          # tools the model chooses from
CLEAR_LEAD = 0.08      # embedding: a lead this big over the runner-up and the closest tool is taken without asking the model
EXAMPLES = 5


def allowed_file() -> Path:
    return Path(os.environ.get("NOAH_MCP_ALLOWED", "data/mcp_allowed.json"))


def always_allowed() -> set[str]:
    try:
        return set(json.loads(allowed_file().read_text()))
    except (OSError, ValueError):
        return set()


def allow_always(expert: str, tool: str) -> None:
    f = allowed_file()
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(sorted(always_allowed() | {f"{expert}.{tool}"}), indent=1))


def read_only(tool: dict[str, Any]) -> bool:
    return bool(tool.get("annotations", {}).get("read_only_hint"))


def needs_confirm(expert: str, tool: dict[str, Any]) -> bool:
    return not read_only(tool) and f"{expert}.{tool['name']}" not in always_allowed()


def _props(tool: dict[str, Any]) -> dict[str, Any]:
    return cast(dict[str, Any], tool.get("input_schema", {}).get("properties") or {})


# an example a small model wrote that is no request a person would type: the prompt echoed back, an instruction to the
# user, or code (the tool's name, snake_case words, {braces}, <placeholders>, key=value, -flags). Such examples share words
# like "list" and "repo" with every message and pull routing to the wrong tool, so they are skipped.
_NOT_A_REQUEST = re.compile(r"^\s*(what the tool does|it needs|it can be told|i would type|enter|input|specify|choose|please specify|give me the name)\b"
                            r"|[{}<>=]|\b[a-z]+_[a-z_]+\b|(^|\s)--?[a-z]", re.I)


def usable_example(text: str, tool: dict[str, Any]) -> bool:
    return 0 < len(text.split()) <= 25 and tool["name"].lower() not in text.lower() and not _NOT_A_REQUEST.search(text)


def tool_expert(tool: dict[str, Any]) -> Expert:
    """A tool in the shape the extractors and engines take."""
    examples = [x for x in dict.fromkeys(tool.get("examples", [])) if usable_example(x, tool)]
    return Expert(name=tool["name"], description=tool.get("description") or tool.get("title") or tool["name"],
                  examples=examples, parameters=tool.get("input_schema") or {"type": "object", "properties": {}},
                  model={}, license="")


def _without(message: str, name: str) -> str:
    """The message without the expert's name ("notes: show my notes" → "show my notes"): every tool of the server is about
    the same thing, so the name says nothing about which tool and only adds noise."""
    words = [w for w in re.split(r"[_\s-]+", name) if w]
    rest = re.sub(r"\b" + r"[\s_-]*".join(map(re.escape, words)) + r"\b", " ", message, flags=re.I) if words else message
    rest = re.sub(r"\s+", " ", rest).strip(" ,:;")
    return rest or message


def shortlist(message: str, tools: list[dict[str, Any]], k: int = SHORTLIST, server: str = "") -> list[tuple[float, dict[str, Any]]]:
    """The k tools closest to the message, closest first, with their scores. `server`: the expert's name, left out of the
    message."""
    if server:
        message = _without(message, server)
    if embedder.AVAILABLE:
        q = embedder.embed([message])[0]
        scored = [(float((embedder.embed(tool_expert(t).routing_texts) @ q).max()), t) for t in tools]
    else:
        by_name = {t["name"]: t for t in tools}
        scored = [(d.score, by_name[d.expert]) for d in KeywordEngine().decide(message, {t["name"]: tool_expert(t) for t in tools})]
    return sorted(scored, key=lambda st: st[0], reverse=True)[:k]


def _fill(message: str, tool: dict[str, Any], model: str, base_url: str) -> dict[str, Any]:
    """Arguments for a tool already chosen (none without a model: defaults may still supply them, see `ground`)."""
    if not _props(tool) or not model:
        return {}
    # nothing required here: what the message leaves out may come from the expert's defaults, and `run` validates after that
    loose = tool_expert(tool)
    loose.parameters = {k: v for k, v in loose.parameters.items() if k != "required"}
    return OpenAICompatExtractor(base_url, model, timeout=90, fallback=False).extract(message, loose)


CHOOSE_PROMPT = 'Pick the one tool that does what the user asks. Reply with JSON {"tool": "<name>"}.\nTools:\n'


def _choose(message: str, ranked: list[tuple[float, dict[str, Any]]], model: str, base_url: str) -> dict[str, Any] | None:
    """The tool the model names among the shortlist (structured output: its answer can only be one of their names), or None."""
    names = [t["name"] for _, t in ranked]
    tools = "\n".join(f"- {t['name']}: {(t.get('description') or t.get('title') or '')[:200]}" for _, t in ranked)
    body = {"model": model, "temperature": 0, "max_tokens": 60, "messages": [
        {"role": "system", "content": CHOOSE_PROMPT + tools}, {"role": "user", "content": message}],
        "response_format": {"type": "json_schema", "json_schema": {"name": "pick", "strict": True, "schema": {
            "type": "object", "properties": {"tool": {"type": "string", "enum": names}}, "required": ["tool"]}}}}
    try:
        text = re.sub(r"<think>.*?</think>", "", chat(base_url, body, 60)["choices"][0]["message"]["content"] or "", flags=re.S)
        name = json.loads(text[text.find("{"):text.rfind("}") + 1])["tool"]
    except LLMError as e:
        if e.fatal:
            raise
        return None
    except (ValueError, KeyError, TypeError):
        return None
    return next((t for _, t in ranked if t["name"] == name), None)


def pick(message: str, ranked: list[tuple[float, dict[str, Any]]], model: str, base_url: str,
         choose: bool = False, server: str = "") -> tuple[dict[str, Any], dict[str, Any], str]:
    """(tool, arguments, how it was chosen). Unless the closest tool is clearly ahead, the model names one of the shortlist
    (`_choose`); with `choose` it instead calls one by tool calling, which fills the arguments in the same step."""
    top = ranked[0][1]
    clear = len(ranked) == 1 or ranked[0][0] - ranked[1][0] >= CLEAR_LEAD
    if model and not clear and not choose:
        chosen = _choose(_without(message, server) if server else message, ranked, model, base_url)
        if chosen:
            return chosen, _fill(message, chosen, model, base_url), f"chosen by {model}"
    if choose and model and not clear:
        options = {t["name"]: tool_expert(t) for _, t in ranked}
        try:
            for name, args in LLMToolsEngine(base_url, model, timeout=90).calls(message, options):
                if name in options and name != NONE_TOOL:
                    tool = next(t for _, t in ranked if t["name"] == name)
                    args = {k: v for k, v in (args or {}).items() if k in _props(tool)}   # a no-argument tool gets a stand-in "request"
                    validate_args(args, tool.get("input_schema", {}))
                    return tool, args, f"picked by {model}"
        except LLMError as e:
            if e.fatal:
                raise
        except Exception:
            pass   # no usable tool call: fall back to the closest tool
    return top, _fill(message, top, model, base_url), "closest tool" + (f", arguments by {model}" if _props(top) and model else "")


def _said(value: Any, text: str) -> bool:
    """Whether a value comes from the message (so the model did not make it up)."""
    if isinstance(value, bool):
        return False   # true/false is rarely said in so many words: an optional flag is left to the tool's default
    if isinstance(value, (int, float)):
        return re.search(rf"(?<![\d.]){re.escape(str(value))}(?![\d.])", text) is not None
    if isinstance(value, str):
        v = value.strip().lower()
        return bool(v) and (v in text or all(w in text for w in re.findall(r"[a-z0-9]{3,}", v)) and bool(re.findall(r"[a-z0-9]{3,}", v)))
    return True   # lists and objects (e.g. a list of edits) are built from the message, not copied from it


def ground(args: dict[str, Any], tool: dict[str, Any], message: str, defaults: dict[str, Any]) -> dict[str, Any]:
    """Keep what the message says; for the rest use the expert's defaults, drop invented optional values, and keep an
    invented required one only when there is no default (the tool then says what is wrong)."""
    schema = tool.get("input_schema", {})
    required, props, text = set(schema.get("required", [])), _props(tool), message.lower()
    out: dict[str, Any] = {}
    for k, v in args.items():
        if _said(v, text):
            out[k] = v
        elif k in defaults:
            out[k] = defaults[k]
        elif k in required:
            out[k] = v
    for k in required:
        if k not in out and k in defaults:
            out[k] = defaults[k]
    return {k: v for k, v in out.items() if k in props or not props}


# a value a model writes when it has nothing to go on: "your_username", "user:your-name", "<owner>", "{repo}", "example-repo", "owner"
_PLACEHOLDER = re.compile(r"\byour[_-]|^\s*(your|my)\s|[<{]|\bexample\b|\bplaceholder\b|^\s*(owner|repo|repository|user|username|name|"
                          r"string|value|id|path|xxx+)\s*$", re.I)


def missing(args: dict[str, Any], tool: dict[str, Any], message: str, defaults: dict[str, Any]) -> list[str]:
    """Required arguments the message did not give, for which the model wrote a placeholder (or nothing) and there is no default."""
    text = message.lower()
    return [k for k in tool.get("input_schema", {}).get("required", [])
            if k not in defaults and (k not in args or isinstance(args[k], str) and not _said(args[k], text) and _PLACEHOLDER.search(args[k]))]


def model_for(expert: Expert, extractor: ArgExtractor) -> str:
    return str(expert.model.get("id") or (getattr(extractor, "model", "") if extractor.name == "openai_compat" else ""))


def run(expert: Expert, message: str, extractor: ArgExtractor, llm_url: str) -> dict[str, Any]:
    """{text, files, args, label, confirm?, error?} for one message."""
    if not expert.tools:
        raise mcp_client.McpError(f"{expert.name} has no tools. Refresh its tools on the Experts page.")
    ranked = shortlist(message, expert.tools, server=expert.name)
    model = model_for(expert, extractor)
    tool, args, how = pick(message, ranked, model, llm_url, choose=bool(expert.model.get("choose_tool")), server=expert.name)
    gaps = missing(args, tool, message, expert.defaults) if model else []
    if gaps and tool is not ranked[0][1]:   # the model's pick needs what the message does not say: the closest tool may not
        closest = ranked[0][1]
        alt = _fill(message, closest, model, llm_url)
        alt_gaps = missing(alt, closest, message, expert.defaults)
        if len(alt_gaps) < len(gaps):   # none: it runs; fewer: it is the one to ask about
            tool, args, how, gaps = closest, alt, f"closest tool, arguments by {model}", alt_gaps
    if gaps:   # calling with "your_username" only gets a 404 back: say what is missing instead
        names = " and ".join(f"**{k}**" + (f" (the model wrote “{args[k]}”)" if isinstance(args.get(k), str) and args[k] else "") for k in gaps)
        raise mcp_client.McpError(f"{tool['name']} needs {names}, and the message does not say. Say it in the message, "
                                  f"or set a default in this expert's settings (Default arguments, e.g. `{gaps[0]}: …`).")
    args = ground(args, tool, message, expert.defaults)
    try:
        validate_args(args, tool.get("input_schema", {}))
    except ValueError as e:
        if model:
            raise
        raise mcp_client.McpError(f"{tool['name']}: {e}, and no model is set to fill it. Choose one for this MCP expert (its settings), "
                                  "give it a default, or use an orchestrator with a tiny model.") from None
    out: dict[str, Any] = {"args": {"tool": tool["name"], "arguments": args}, "label": f"mcp:{tool['name']} · {how}", "files": []}
    if needs_confirm(expert.name, tool):
        hint = "it may change something" + (" and may delete data" if tool.get("annotations", {}).get("destructive_hint", True) else "")
        return {**out, "text": f"**{expert.name}** wants to run **{tool['name']}** ({hint}). Run it?",
                "confirm": {"expert": expert.name, "tool": tool["name"], "arguments": args}}
    return {**out, **call(expert, tool["name"], args)}


def call(expert: Expert, tool: str, args: dict[str, Any]) -> dict[str, Any]:
    res = mcp_client.call_tool(expert.server, expert.name, tool, args)
    return {"text": res["text"] or "(no output)", "files": res["files"],
            **({"error": f"{tool} reported an error"} if res["is_error"] else {})}


# ---- adding a server: its tools, with examples to route by ----
# Only what the tool does and the names of its arguments: shown the argument schema, small models write command lines.
EXAMPLE_PROMPT = ("You write example chat messages for a tool, so a router can tell when a user wants it. Reply with JSON: "
                  '{"examples": ["...", "..."]}. Write ' + str(EXAMPLES) + " short messages, each a different request a person "
                  "would type to an assistant in everyday words, with concrete values (for a weather tool: \"will it rain in "
                  "Paris tomorrow?\"). Never write the tool's name, code, flags or argument names.")


def _examples_for(tool: dict[str, Any], model: str, base_url: str) -> list[str]:
    try:
        return _ask_examples(tool, model, base_url)
    except (ValueError, KeyError):   # a small model's answer was not the JSON asked for: one more try
        return _ask_examples(tool, model, base_url)


def _ask_examples(tool: dict[str, Any], model: str, base_url: str) -> list[str]:
    needs = ", ".join(k.replace("_", " ") for k in _props(tool)) or "nothing"
    body = {"model": model, "temperature": 0.7, "max_tokens": 400, "messages": [
        {"role": "system", "content": EXAMPLE_PROMPT},
        {"role": "user", "content": f"What the tool does: {tool.get('description') or tool.get('title') or tool['name'].replace('_', ' ')}\n"
                                    f"It can be told: {needs}"}],
        "response_format": {"type": "json_schema", "json_schema": {"name": "examples", "strict": True, "schema": {
            "type": "object", "properties": {"examples": {"type": "array", "items": {"type": "string"}}}, "required": ["examples"]}}}}
    text = chat(base_url, body, 120)["choices"][0]["message"]["content"] or ""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    items = json.loads(text[text.find("{"):text.rfind("}") + 1])["examples"]
    out = [x.strip() for x in dict.fromkeys(items) if isinstance(x, str) and len(x.strip()) <= 200 and usable_example(x, tool)]
    if not out:
        raise ValueError("the model wrote no usable examples")
    return out[:EXAMPLES + 1]


def write_examples(tools: list[dict[str, Any]], model: str, base_url: str, keep: list[dict[str, Any]] | None = None) -> list[str]:
    """Give every tool its examples, in place: kept from `keep` (the previous tools.json) when the tool did not change,
    else written by `model`. Returns the problems, one line per tool that got none."""
    old = {(t["name"], t.get("description", "")): t.get("examples", []) for t in keep or []}
    todo = []
    for t in tools:
        t["examples"] = old.get((t["name"], t.get("description", "")), [])
        if not t["examples"] and model:
            todo.append(t)
    problems: list[str] = []

    def one(t: dict[str, Any]) -> None:
        try:
            t["examples"] = _examples_for(t, model, base_url)
        except Exception as e:   # one tool without examples still routes by its description
            problems.append(f"{t['name']}: {e}")

    if mcp_client.IN_BROWSER:   # no threads in the browser
        for t in todo:
            one(t)
    else:
        with ThreadPoolExecutor(4) as pool:
            list(pool.map(one, todo))
    return problems


def manifest(name: str, server: dict[str, Any], tools: list[dict[str, Any]], model: str, description: str = "") -> dict[str, Any]:
    """expert.yaml for a new MCP expert. The description and examples are a summary for the expert cards and the LLM
    orchestrators; the embedding and keyword engines also see every tool's own description and examples."""
    names = [t["name"] for t in tools]
    desc = description.strip() or f"Tools from the {name} MCP server: " + ", ".join(names[:12]) + (", …" if len(names) > 12 else ".")
    examples = [t["examples"][0] for t in tools if t.get("examples")][:6] or [f"use {n.replace('_', ' ')}" for n in names[:6]] or [f"ask {name}"]
    return {"name": name, "kind": "mcp", "description": desc[:500], "examples": examples, "server": server,
            "model": {"backend": "openai_compat", "id": model} if model else {"backend": "orchestrator"},
            "license": "see the server's terms",
            **({} if description.strip() else {"auto_routing": True})}   # written by Noah: refreshing the tools rewrites it


def routing(name: str, tools: list[dict[str, Any]]) -> tuple[str, list[str]]:
    """The description and examples `manifest` writes, for an expert whose routing text Noah wrote (see auto_routing)."""
    m = manifest(name, {}, tools, "")
    return m["description"], m["examples"]


def parse_config(text: str, name: str = "") -> list[tuple[str, dict[str, Any]]]:
    """(name, server) pairs from pasted JSON: an `mcpServers` map (Claude Desktop, Cursor), a `servers` map (VS Code), or one
    server ({url} or {command}) with `name` given separately."""
    try:
        data: Any = json.loads(text)
    except ValueError as e:
        raise mcp_client.McpError(f"That is not valid JSON ({e}).") from None
    if not isinstance(data, dict):
        raise mcp_client.McpError("Paste a JSON object: {\"mcpServers\": {...}} or one server {\"url\": ...} / {\"command\": ...}.")
    data = cast(dict[str, Any], data)
    servers = data.get("mcpServers") or data.get("servers")
    if isinstance(servers, dict):
        pairs = list(cast(dict[str, Any], servers).items())
    elif data.get("url") or data.get("command"):
        if not name:
            raise mcp_client.McpError("Give this server a name.")
        pairs = [(name, data)]
    else:
        raise mcp_client.McpError("No servers found: expected mcpServers, servers, or a url / command.")
    out: list[tuple[str, dict[str, Any]]] = []
    for raw_name, raw in pairs:
        if isinstance(raw, dict) and cast(dict[str, Any], raw).get("type") == "sse":
            raise mcp_client.McpError(f"{raw_name}: SSE servers (the older transport) are not supported; use its Streamable HTTP address.")
        out.append((expert_name(str(raw_name)), mcp_client.check_server(raw)))
    return out


def expert_name(raw: str) -> str:
    """A server's name as an expert name: lowercase letters, digits and underscores, starting with a letter."""
    n = re.sub(r"[^a-z0-9]+", "_", raw.lower()).strip("_")
    n = n if n[:1].isalpha() else "mcp_" + n
    return n[:32].rstrip("_") or "mcp_server"
