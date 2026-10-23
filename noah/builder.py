"""Build mode: turn "make me a todo app" into a working single-page prototype, built in front of the user.

A planner model writes a short spec (title, summary, features) and one task per part. Then the experts that build a part
(`produces: html | css | js` in expert.yaml) write it in a fixed order: markup first, then styles and behaviour, each seeing
the markup so ids and classes line up. The parts are put together into one self-contained HTML file: no server, no database,
no external files. Data the prototype keeps goes in localStorage.

A later request changes the app instead of starting over: given the current version (its plan and parts), the planner keeps
the spec and gives a task only to the experts whose part must change, and each of those rewrites its current code with the
change. Parts nobody touches carry over unchanged.

The steps are separate calls (plan, step, finish) so the page can show progress and the browser build needs no streaming.
`build()` runs them all in one go for the CLI and tests.
"""
from __future__ import annotations

from typing import Any
import json
import os
import re
import time
from dataclasses import replace

from .generate import answer
from .llm import LLMError, WEB_PREFIX, chat
from .types import Expert

PARTS = ("html", "css", "js")  # also the build order: styles and behaviour are written against the markup
LANGS = {"html": ("html",), "css": ("css",), "js": ("js", "javascript")}
STEP_TOKENS = 3000
# A step writes a whole file: a model larger than the GPU (part of it on the CPU) can need many minutes for one
STEP_TIMEOUT = float(os.environ.get("NOAH_BUILD_TIMEOUT", "900"))
BIG_ENOUGH = 7.0  # billions of parameters: below this a local model builds simple demos and often misses features

PLANNER = ("You plan small web app prototypes that run in one browser page with no server and no database. "
           "Given what the user wants, write a short spec: a title, a one-sentence summary and at most 6 concrete features. "
           "Then give each listed expert one task describing exactly what it must write for this app. "
           "Data the app keeps is stored in the browser's localStorage. Reply with JSON only. /no_think")

REVISER = ("You plan changes to a small web app prototype that already works in one browser page (no server, no database). "
           "Given the app, its current markup and what the user asks to change, decide which experts must change their part, "
           "and give each of them one precise task. Leave out experts whose part does not need to change: a new colour or font is "
           "only CSS; new behaviour usually needs JavaScript and often HTML. Keep the existing features and add any new ones. "
           "Reply with JSON only. /no_think")
KEEP_RATIO = 0.4  # a rewrite shorter than this share of the current part lost most of it: keep the current one instead

PART_RULES = {
    "html": ("Write only the markup that goes inside <body> for this app. Give every element the styles or scripts need a "
             "clear id or class. Include every control the features need (inputs, buttons, lists). "
             "No <style>, no <script>, no external files, no images from the web."),
    "css": ("Write the CSS for this markup: a clean, modern, responsive look. Style the ids and classes it uses. "
            "Only CSS, no external fonts or files."),
    "js": ("Write plain JavaScript (no libraries, no imports, no fetch) that makes this markup work and implements every feature. "
           "Use the ids and classes in the markup exactly. Keep the app's data in localStorage so it survives a reload. "
           "The script runs after the markup has loaded. Only JavaScript."),
}


class BuildError(ValueError):
    """The request cannot be built; the message is shown to the user."""


def build_experts(experts: dict[str, Expert]) -> dict[str, Expert]:
    """The experts that write a part of a prototype, at most one per part, in build order."""
    out: dict[str, Expert] = {}
    for part in PARTS:
        e = next((e for e in experts.values() if e.produces == part and e.kind == "generate"), None)
        if e:
            out[e.name] = e
    return out


def is_small(model: str) -> bool:
    """A model too small to plan and build more than a simple demo, judged by the size in its name (qwen2.5-coder:1.5b,
    Qwen2.5-7B-Instruct). A name without a size (claude-opus-5-5, gpt-5-mini, devstral) is given the benefit of the doubt."""
    if model.startswith(WEB_PREFIX):
        return True  # WebLLM runs what fits in a browser tab
    sizes = [float(s) for s in re.findall(r"(?<![\w.])(\d+(?:\.\d+)?)[bB](?![a-zA-Z])", model.split("/")[-1].replace("-", " ").replace(":", " ").replace("_", " "))]
    if sizes:
        return max(sizes) < BIG_ENOUGH
    return False


def _schema(names: list[str]) -> dict[str, Any]:
    return {"type": "object", "required": ["title", "summary", "features", "steps"], "additionalProperties": False, "properties": {
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "features": {"type": "array", "items": {"type": "string"}},
        "steps": {"type": "array", "items": {"type": "object", "required": ["expert", "task"], "additionalProperties": False,
                                             "properties": {"expert": {"type": "string", "enum": names}, "task": {"type": "string"}}}}}}


def default_plan(message: str, builders: dict[str, Expert]) -> dict[str, Any]:
    """What a build falls back on when the planner gives nothing usable: every builder, with the request as its task."""
    return {"title": message.strip()[:60] or "Prototype", "summary": message.strip(), "features": [],
            "steps": [{"expert": e.name, "part": e.produces, "task": message.strip()} for e in builders.values()]}


def normalize(raw: Any, message: str, builders: dict[str, Expert]) -> dict[str, Any]:
    """Keep what is usable from a planner's reply: known experts only, one step per part, in build order, markup always first."""
    base = default_plan(message, builders)
    if not isinstance(raw, dict):
        return base
    tasks: dict[str, str] = {}
    for s in raw.get("steps") or []:
        if isinstance(s, dict) and s.get("expert") in builders and str(s.get("task") or "").strip():
            tasks.setdefault(s["expert"], str(s["task"]).strip())
    html = next((n for n, e in builders.items() if e.produces == "html"), None)
    if html is None:
        raise BuildError("No expert writes HTML, so nothing can be built. Enable the html expert (or one with `produces: html`).")
    tasks.setdefault(html, message.strip())
    feats = [str(f).strip() for f in raw.get("features") or [] if str(f).strip()][:8]
    return {"title": str(raw.get("title") or "").strip()[:80] or base["title"],
            "summary": str(raw.get("summary") or "").strip() or base["summary"], "features": feats,
            "steps": [{"expert": n, "part": e.produces, "task": tasks[n]} for n, e in builders.items() if n in tasks]}


def normalize_change(raw: Any, message: str, builders: dict[str, Expert], base: dict[str, Any]) -> dict[str, Any]:
    """A change to an existing app: the spec carries over (the title too: a small planner tends to rename the app after the
    change), new features are added, and only the parts the planner names are rewritten (all of them if it names none)."""
    tasks: dict[str, str] = {}
    for s in (raw.get("steps") or []) if isinstance(raw, dict) else []:
        if isinstance(s, dict) and s.get("expert") in builders and str(s.get("task") or "").strip():
            tasks.setdefault(s["expert"], str(s["task"]).strip())
    if not tasks:
        tasks = {n: message.strip() for n in builders}
    feats = [str(f) for f in base.get("features") or []]
    for f in (raw.get("features") or []) if isinstance(raw, dict) else []:
        if str(f).strip() and str(f).strip().lower() not in (x.lower() for x in feats):
            feats.append(str(f).strip())
    return {"title": base.get("title") or "Prototype", "summary": base.get("summary") or message.strip(), "features": feats[:12],
            "change": message.strip(), "steps": [{"expert": n, "part": e.produces, "task": tasks[n]} for n, e in builders.items() if n in tasks]}


def _unescape(code: str) -> str:
    return code.replace("<\\/script", "</script").replace("<\\/style", "</style")


def split(html: str) -> dict[str, str]:
    """The parts of a page assemble() wrote (for versions saved before their parts were kept)."""
    css = re.search(r"<style>\n?(.*?)\n?</style>", html, flags=re.S)
    body = re.search(r"<body>\n?(.*?)\n?</body>", html, flags=re.S)
    markup, js = (body.group(1) if body else ""), ""
    script = re.search(r"<script>\n?(.*?)\n?</script>\s*$", markup, flags=re.S)
    if script:
        markup, js = markup[:script.start()].rstrip(), script.group(1)
    return {k: _unescape(v) for k, v in (("html", markup), ("css", css.group(1) if css else ""), ("js", js)) if v.strip()}


def plan(message: str, experts: dict[str, Expert], llm_url: str, model: str = "", timeout: float = STEP_TIMEOUT,
         build_model: str = "", base: dict[str, Any] | None = None) -> dict[str, Any]:
    """The build plan for a request. `model` is the planner; without one, the markup expert's own model plans.
    `build_model` replaces every builder's own model (one setting to build with, say, a 32B model or Claude).
    `base` is the version to change ({"plan": ..., "parts": ...}); without it, a new app is planned."""
    builders = build_experts(experts)
    if not builders:
        raise BuildError("No expert can build a prototype. Enable the html, css and javascript experts.")
    model = model or build_model or next(iter(builders.values())).model.get("id", "")
    cards = "\n".join(f"- {e.name}: writes the {e.produces.upper()}. {e.description}" for e in builders.values())
    base_parts: dict[str, str] = dict((base or {}).get("parts") or split((base or {}).get("html") or ""))
    if base and base_parts.get("html"):
        bp: dict[str, Any] = base.get("plan") or {}
        feats = "".join(f"\n- {f}" for f in bp.get("features") or [])
        ask = (f"Experts:\n{cards}\n\nThe app: {bp.get('title', '')}. {bp.get('summary', '')}" + (f"\nFeatures:{feats}" if feats else "")
               + f"\n\nIts markup:\n```html\n{base_parts['html'][:3000]}\n```\n\nThe user asks to change: {message}")
        system = REVISER
    else:
        base, base_parts = None, {}
        ask, system = f"Experts:\n{cards}\n\nThe user wants: {message}", PLANNER
    body = {"model": model, "temperature": 0.2, "max_tokens": 900, "messages": [
        {"role": "system", "content": system}, {"role": "user", "content": ask}],
        "response_format": {"type": "json_schema", "json_schema": {"name": "plan", "schema": _schema(list(builders)), "strict": True}}}
    try:
        text = chat(llm_url, body, timeout)["choices"][0]["message"]["content"] or ""
        raw = json.loads(re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip())
    except LLMError as e:
        if e.fatal:
            raise  # a bad key or no credits must be seen, not hidden behind the fallback plan
        raw = None
    except Exception:  # an unreachable planner or invalid JSON: build from the request alone
        raw = None
    p = normalize_change(raw, message, builders, base.get("plan") or {}) if base else normalize(raw, message, builders)
    used = [model, *(build_model or builders[s["expert"]].model.get("id", "") for s in p["steps"])]
    return {**p, "planner": model, "build_model": build_model, "small": [m for m in dict.fromkeys(used) if m and is_small(m)],
            "base_parts": base_parts}  # where the steps start: the current version's parts (empty for a new app)


def extract_code(text: str, part: str) -> str:
    """The code in an expert's answer: the fenced block in the part's language (else the longest block, else the whole text)."""
    blocks = re.findall(r"```([\w+-]*)[^\n]*\n(.*?)```", text, flags=re.S)
    if not blocks and "```" in text:  # an answer cut off at the token limit: keep the unclosed block
        blocks = re.findall(r"```([\w+-]*)[^\n]*\n(.*)", text, flags=re.S)
    mine = [b for lang, b in blocks if lang.lower() in LANGS[part]]
    code = max(mine or [b for _, b in blocks] or [text], key=len).strip()
    if part == "html":
        body = re.search(r"<body\b[^>]*>(.*?)(</body>|$)", code, flags=re.S | re.I)
        code = body.group(1) if body else re.sub(r"<!doctype[^>]*>|</?html\b[^>]*>|<head\b.*?</head>", "", code, flags=re.S | re.I)
        code = re.sub(r"<script\b.*?</script>|<style\b.*?</style>|<link\b[^>]*>", "", code, flags=re.S | re.I).strip()
    else:  # a <style> / <script> wrapper around the whole answer (inside, the tag is just text and assemble() escapes it)
        tag = "style" if part == "css" else "script"
        code = re.sub(rf"^<{tag}\b[^>]*>\s*|\s*</{tag}>$", "", code, flags=re.I).strip()
    return code


def step_prompt(p: dict[str, Any], step: dict[str, Any], parts: dict[str, str]) -> str:
    feats = "".join(f"\n- {f}" for f in p.get("features") or [])
    lines = [f"App: {p['title']}. {p['summary']}", *([f"Features:{feats}"] if feats else [])]
    current = parts.get(step["part"]) if p.get("change") else None
    if current:
        lines.append(f"The app already works. The user asked: {p['change']}\n\nYour task: {step['task']}")
    else:
        lines.append(f"Your task: {step['task']}")
    if step["part"] != "html" and parts.get("html"):
        lines.append(f"The markup:\n```html\n{parts['html']}\n```")
    if current:
        lines.append(f"The current {step['part'].upper()}:\n```{step['part']}\n{current}\n```")
        lines.append(f"Change it as asked and keep everything else exactly as it is: the same ids, classes, features and saved data. "
                     f"Return the complete updated {step['part'].upper()}, not only the changed lines.")
    lines.append(PART_RULES[step["part"]])
    return "\n\n".join(lines)


def run_step(p: dict[str, Any], index: int, parts: dict[str, str], experts: dict[str, Expert], llm_url: str) -> dict[str, Any]:
    """Write one part. `parts` holds what earlier steps wrote ({"html": ...})."""
    try:
        step = p["steps"][index]
    except (KeyError, IndexError, TypeError):
        raise BuildError(f"The plan has no step {index}.")
    e = experts.get(step.get("expert", ""))
    if e is None or e.produces != step.get("part"):
        raise BuildError(f"Expert '{step.get('expert')}' cannot write the {step.get('part')} part (is it enabled?).")
    if p.get("build_model"):
        e = replace(e, model={**e.model, "id": p["build_model"]})
    start, error, code = time.perf_counter(), None, ""
    try:
        code = extract_code(answer(e, step_prompt(p, step, parts), llm_url, timeout=STEP_TIMEOUT, max_tokens=STEP_TOKENS), step["part"])
    except LLMError as err:
        error = str(err)
    except Exception as err:
        error = f"{type(err).__name__}: {err}"
    current = parts.get(step["part"], "") if p.get("change") else ""
    if current and len(code) < KEEP_RATIO * len(current):  # a small model that answered with a fragment: don't lose the app
        code, error = current, error or f"kept the current {step['part'].upper()}: the rewrite lost most of it"
    return {"expert": e.name, "part": step["part"], "code": code, "error": error,
            "latency_ms": (time.perf_counter() - start) * 1000}


def assemble(p: dict[str, Any], parts: dict[str, str]) -> str:
    """One self-contained HTML file. A part can't close its own tag early (</style> in CSS, </script> in JS)."""
    title = re.sub(r"[<>&]", "", str(p.get("title") or "Prototype"))
    css = re.sub(r"</style", r"<\\/style", parts.get("css", ""), flags=re.I)
    js = re.sub(r"</script", r"<\\/script", parts.get("js", ""), flags=re.I)
    return ("<!doctype html>\n<html lang=\"en\">\n<head>\n<meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
            f"<title>{title}</title>\n<style>\n{css}\n</style>\n</head>\n<body>\n{parts.get('html', '')}\n"
            + (f"<script>\n{js}\n</script>\n" if js else "") + "</body>\n</html>\n")


def trace_record(message: str, p: dict[str, Any], steps: list[dict[str, Any]], total_ms: float) -> dict[str, Any]:
    """A build in the trace log, shaped like a routing trace (engine, results, total_ms) plus the plan (without the code)."""
    return {"message": message, "engine": f"planner:{p.get('planner', '')}", "mode": "change" if p.get("change") else "build",
            "decisions": [], "plan": {k: v for k, v in p.items() if k != "base_parts"},
            "results": [{k: s.get(k) for k in ("expert", "part", "error", "latency_ms")} | {"chars": len(s.get("code") or "")}
                        for s in steps], "total_ms": total_ms}


def build(message: str, experts: dict[str, Expert], llm_url: str, model: str = "", build_model: str = "",
          base: dict[str, Any] | None = None) -> tuple[str, dict[str, Any], list[dict[str, Any]]]:
    """Plan, write every part and assemble: (html, plan, steps). With `base`, change that version instead."""
    p = plan(message, experts, llm_url, model, build_model=build_model, base=base)
    parts: dict[str, str] = dict(p["base_parts"])
    steps: list[dict[str, Any]] = []
    for i in range(len(p["steps"])):
        s = run_step(p, i, parts, experts, llm_url)
        steps.append(s)
        if s["code"]:
            parts[s["part"]] = s["code"]
    return assemble(p, parts), p, steps
