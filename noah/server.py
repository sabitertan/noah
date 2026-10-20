from __future__ import annotations

from typing import Any, Awaitable, Callable, TypeVar
import hmac
import importlib.util
import json
import logging
import os
import time
from dataclasses import asdict
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response, FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import filesvc
from .engines import make_engine
from .engines.base import DecisionEngine
from .engines.laya import CHECKPOINTS as LAYA_CHECKPOINTS
from .extractors import make_extractor
from .generate import answer
from . import azure, expert_files, expert_store, hf, llm, mcp_client, mcp_expert, openrouter, webllm
from .extractors import validate_args
from .orchestrator import Orchestrator
from .registry import load_experts
from .types import Expert
from .trace import TraceStore

WEB = Path(os.environ.get("NOAH_WEB_DIST", Path(__file__).parent / "web" / "dist"))  # the built UI (frontend/: npm run build)
OLLAMA = os.environ.get("NOAH_LLM_URL", "http://localhost:11434")

app = FastAPI(title="noah")

# Desktop mode: the Electron shell sets a per-launch secret. Without it any web page the user visits could call
# http://127.0.0.1:<port>/api (CSRF / DNS rebinding), so we also pin the Host header to loopback names.
LAUNCH_TOKEN = os.environ.get("NOAH_LAUNCH_TOKEN", "")
OAUTH_CALLBACK = "/api/mcp/oauth/callback"   # where an MCP server's sign-in page sends the user back (mcp_client.sign_in)


def _pid_alive(pid: int) -> bool:
    """Existence check without side effects. On Windows os.kill(pid, 0) would TERMINATE the process, so use the API."""
    if os.name == "nt":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel32.CloseHandle(handle)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by someone else
    return True


def _exit_with_parent(pid: int) -> None:
    """Desktop mode: if the Electron shell dies (even by crash), don't leave an orphaned server behind."""
    import threading
    import time

    def watch():
        while _pid_alive(pid):
            time.sleep(2)
        os._exit(0)

    threading.Thread(target=watch, daemon=True).start()


if os.environ.get("NOAH_PARENT_PID", "").isdigit():
    _exit_with_parent(int(os.environ["NOAH_PARENT_PID"]))


@app.middleware("http")
async def launch_guard(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    if LAUNCH_TOKEN and request.url.path != OAUTH_CALLBACK:   # the sign-in page may open in another browser; its one-time state protects it
        host = request.headers.get("host", "").rsplit(":", 1)[0].strip("[]").lower()
        supplied = request.cookies.get("noah_token", "") or request.headers.get("authorization", "").removeprefix("Bearer ")
        if host not in ("127.0.0.1", "localhost", "::1") or not hmac.compare_digest(supplied, LAUNCH_TOKEN):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
    return await call_next(request)

app.include_router(hf.router)
app.include_router(openrouter.router)
app.include_router(azure.router)
app.include_router(webllm.router)
app.mount("/assets", StaticFiles(directory=WEB / "assets", check_dir=False), name="assets")  # content-hashed JS/CSS/fonts
PROFILES = Path(os.environ.get("NOAH_PROFILES", "data/profiles.json"))
DEFAULT_PROFILES = [
    # bge-small cosine scores: in-domain >= 0.65, off-topic <= 0.61 on our test set, so 0.63 declines chit-chat.
    {"name": "Default", "engine": "embedding", "mode": "route", "extractor": "openai_compat",
     "model": "qwen2.5:0.5b", "threshold": 0.63},
    # Example LLM orchestrators: the model picks the expert AND its arguments by tool calling.
    # Pull first:  ollama pull hf.co/MaziyarPanahi/Nemotron-Orchestrator-8B-GGUF:Q4_K_M   (5 GB, slow on small GPUs)
    {"name": "Nemotron orchestrator", "engine": "llm_tools", "mode": "fanout", "extractor": "rule", "model": "",
     "engine_model": "hf.co/MaziyarPanahi/Nemotron-Orchestrator-8B-GGUF:Q4_K_M", "threshold": 0.25},
    # Pull first:  ollama pull hf.co/ggml-org/functiongemma-270m-it-GGUF:Q8_0   (300 MB; needs fine-tuning to be reliable)
    {"name": "FunctionGemma router", "engine": "llm_tools", "mode": "route", "extractor": "rule", "model": "",
     "engine_model": "hf.co/ggml-org/functiongemma-270m-it-GGUF:Q8_0", "threshold": 0.25},
    # Decision models: pick one expert (or 'none') from lettered options; the extractor then fills the arguments.
    # Pull first:  ollama pull hf.co/DreamBlooms/Tev1-0.8B-experimental-GGUF:Q8_0   (800 MB)
    {"name": "Tev1 decision", "engine": "llm_choice", "mode": "route", "extractor": "openai_compat",
     "model": "qwen2.5:0.5b", "engine_model": "hf.co/DreamBlooms/Tev1-0.8B-experimental-GGUF:Q8_0", "threshold": 0.5},
]
HAS_LAYA = importlib.util.find_spec("laya") is not None  # optional extra: pip install -e ".[laya]"
if HAS_LAYA:
    # Zero-shot Laya puts most mass on "none", so a low threshold; bench: english 20/28 routed, 4/4 declined at 0.1.
    DEFAULT_PROFILES.append({"name": "Laya decision", "engine": "laya", "mode": "route", "extractor": "openai_compat",
                             "model": "qwen2.5:0.5b", "engine_model": "english", "threshold": 0.1})
HAS_EMBEDDING = importlib.util.find_spec("fastembed") is not None  # not in the browser build (native ONNX runtime)
if not HAS_EMBEDDING:
    DEFAULT_PROFILES[0] = {**DEFAULT_PROFILES[0], "engine": "keyword", "threshold": 0.25}
EXPERTS_DIR = Path(os.environ.get("NOAH_EXPERTS", "experts"))
EXPERT_ERRORS: dict[str, str] = {}   # folder name -> why it did not load; shown on the Experts page so it can be fixed in the editor
log = logging.getLogger("noah")


def _load_all() -> dict[str, Expert]:
    """Every expert that loads. A broken one (say, a tool.py edited by hand) is left out and reported, never fatal."""
    errors: dict[str, BaseException] = {}
    loaded = load_experts(EXPERTS_DIR, errors)
    EXPERT_ERRORS.clear()
    EXPERT_ERRORS.update({n: f"{type(e).__name__}: {e}" for n, e in errors.items()})
    for n, why in EXPERT_ERRORS.items():
        log.warning("expert %s did not load: %s", n, why)
    return loaded


experts = _load_all()


def _reload_experts() -> None:
    """Pick up what the editor just wrote. Engines and the orchestrator read this dict per request, so nothing else needs resetting
    (the embedding engine caches by each expert's card text, so an edit is re-embedded on its own)."""
    fresh = _load_all()
    experts.clear()
    experts.update(fresh)
trace = TraceStore()
DISABLED = Path(os.environ.get("NOAH_DISABLED_EXPERTS", "data/disabled_experts.json"))


def _disabled() -> set[str]:
    try:
        return set(json.loads(DISABLED.read_text()))
    except (OSError, ValueError):
        return set()


def _active_experts() -> dict[str, Any]:
    off = _disabled()
    return {n: e for n, e in experts.items() if n not in off}
_engines: dict[str, DecisionEngine] = {}  # engines can be expensive to load (embedding), so cache them


class AskRequest(BaseModel):
    message: str
    profile: str = ""  # orchestrator profile name (optional); a disabled profile is refused
    engine: str = "keyword"
    engine_model: str = ""
    mode: str = "route"
    extractor: str = "rule"
    model: str = ""
    threshold: float = 0.25


NOT_BUILT = """<!doctype html><meta charset="utf-8"><title>Noah</title>
<body style="font:15px/1.6 monospace;max-width:640px;margin:15vh auto;padding:0 20px">
<h2>Noah: the web UI is not built yet</h2>
<p>The API is running. Build the UI once with <code>cd frontend &amp;&amp; npm install &amp;&amp; npm run build</code>,
or develop it with hot reload: <code>cd frontend &amp;&amp; npm run dev</code> and open http://localhost:5173.</p>"""


@app.get("/")
def index():
    page = WEB / "index.html"
    if not page.is_file():
        return HTMLResponse(NOT_BUILT, status_code=503)
    return FileResponse(page, headers={"Cache-Control": "no-cache"})  # the page is tiny; its assets are content-hashed


@app.get("/favicon.ico", include_in_schema=False)
@app.get("/favicon.svg", include_in_schema=False)
def favicon():
    return FileResponse(WEB / "favicon.svg", media_type="image/svg+xml")


def _is_local(request: Request) -> bool:
    """True only for a direct loopback client (no proxy headers): the machine running Noah."""
    fwd = any(h in request.headers for h in ("x-forwarded-for", "forwarded", "x-real-ip"))
    return bool(request.client) and request.client.host in ("127.0.0.1", "::1") and not fwd


@app.get("/api/config")
def config(request: Request):
    try:
        models = [m["name"] for m in httpx.get(f"{OLLAMA}/api/tags", timeout=2).json()["models"]]
    except Exception:
        models = []
    return {
        "local": _is_local(request),
        "engines": ["keyword", *(["embedding"] if HAS_EMBEDDING else []), "llm_tools", "llm_choice", *(["laya"] if HAS_LAYA else [])],
        "engine_models": {"laya": LAYA_CHECKPOINTS},  # engines whose models are not Ollama models
        "modes": ["route", "fanout"],
        "extractors": ["rule", "openai_compat"],
        "models": models,
        "hf": hf.status(),  # whether a token is set (never the token)
        "hf_models": [llm.HF_PREFIX + m for m in hf.my_models()],  # hosted models the user added
        "or": openrouter.status(),
        "or_models": [llm.OR_PREFIX + m for m in openrouter.my_models()],
        "az": azure.status(),  # endpoint is not secret; the key never leaves the server
        "az_models": [llm.AZ_PREFIX + m for m in azure.my_models()],
        "web_models": [webllm.PREFIX + m for m in webllm.my_models()],  # downloaded into the browser (browser build only)
        "experts": [{"name": e.name, "description": e.description, "license": e.license,
                     "parameters": e.parameters, "examples": e.examples,
                     "kind": e.kind, "model": e.model.get("id", ""),
                     **({"tools": [t["name"] for t in e.tools], "server": mcp_client.describe_server(e.server),
                         "url": e.server.get("url", ""), "oauth": bool(e.server.get("oauth")),
                         "signed_in": mcp_client.signed_in(e.server)} if e.kind == "mcp" else {}),
                     "enabled": e.name not in _disabled()} for e in experts.values()],
        "mcp": {"available": not mcp_client.unavailable(), "why": mcp_client.unavailable()},
        "expert_errors": EXPERT_ERRORS,
    }


@app.post("/api/ask")
async def ask(req: AskRequest):
    if req.profile:
        match = next((p for p in _load_profiles() if p.get("name") == req.profile), None)
        if match is not None and not _profile_enabled(match):
            raise HTTPException(409, f"Orchestrator '{req.profile}' is disabled. Enable it or pick another one.")
    key = f"{req.engine}:{req.engine_model}"
    if key not in _engines:
        if req.engine in ("llm_tools", "llm_choice"):
            kw = {"base_url": f"{OLLAMA}/v1", "model": req.engine_model}
        else:
            kw = {"model": req.engine_model} if req.engine == "laya" else {}
        _engines[key] = make_engine(req.engine, **kw)
    kw = {"base_url": f"{OLLAMA}/v1", "model": req.model} if req.extractor == "openai_compat" else {}
    orch = Orchestrator(_active_experts(), _engines[key], make_extractor(req.extractor, **kw), trace,
                        mode=req.mode, threshold=req.threshold, llm_url=f"{OLLAMA}/v1")
    results, decisions = await orch.ask_traced(req.message)
    return {"results": [asdict(r) for r in results], "decisions": [asdict(d) for d in decisions]}


class ExpertToggle(BaseModel):
    enabled: bool


_T = TypeVar("_T")


def _editor(call: Callable[[], _T]) -> _T:
    try:
        return call()
    except expert_store.ExpertError as e:
        raise HTTPException(e.status, str(e))


@app.get("/api/experts/{name}/manifest")
def get_expert_manifest(name: str):
    return _editor(lambda: expert_store.read(EXPERTS_DIR, name))


@app.post("/api/experts", status_code=201)
def create_expert(body: expert_store.ExpertIn):
    name = _editor(lambda: expert_store.create(EXPERTS_DIR, body))
    expert_files.record(VERSIONS, name, expert_files.read_files(EXPERTS_DIR / name), "Created in Noah")
    _reload_experts()
    return expert_store.read(EXPERTS_DIR, name)


@app.put("/api/experts/{name}/manifest")
def update_expert(name: str, body: expert_store.ExpertIn):
    _editor(lambda: expert_files.baseline(VERSIONS, EXPERTS_DIR, name))
    _editor(lambda: expert_store.update(EXPERTS_DIR, name, body))
    expert_files.record(VERSIONS, name, expert_files.read_files(EXPERTS_DIR / name), "Edited settings")
    _reload_experts()
    return expert_store.read(EXPERTS_DIR, name)


# ---- every file of an expert, in the code editor ----
VERSIONS = Path(os.environ.get("NOAH_EXPERT_VERSIONS", "data/expert_versions"))
CODE_EDIT = os.environ.get("NOAH_CODE_EDIT", "")   # "1": Python may be changed from any client; "0": from none; default: this machine only


def _code_edit_allowed(request: Request) -> bool:
    """Saving Python means the server runs it, so by default only someone at this machine may. The custom header keeps other
    web sites out (a cross-site page cannot send it without a CORS preflight, which we never answer) and the Host check stops
    a DNS-rebinding page, which arrives under its own host name."""
    if CODE_EDIT == "0" or request.headers.get("x-noah-code") != "1":
        return False
    host = request.headers.get("host", "").rsplit(":", 1)[0].strip("[]").lower()
    return CODE_EDIT == "1" or (_is_local(request) and host in ("127.0.0.1", "localhost", "::1"))


class FilesIn(BaseModel):
    files: dict[str, str]
    base: str = ""     # the hash the editor loaded, so a save never overwrites changes made elsewhere
    note: str = Field(default="", max_length=200)


class RestoreIn(BaseModel):
    base: str = ""


def _saved(name: str, result: dict[str, Any]) -> Any:
    if not result["saved"]:
        return JSONResponse({"detail": "Not saved: fix the errors first.", **result}, status_code=422)
    _reload_experts()
    return result


@app.get("/api/experts/{name}/files")
def get_expert_files(name: str, request: Request):
    folder = _editor(lambda: expert_files._folder(EXPERTS_DIR, name))  # pyright: ignore[reportPrivateUsage]
    files = expert_files.read_files(folder)
    allowed = _code_edit_allowed(request)
    return {"name": name, "files": files, "hash": expert_files.digest(files), "code_editable": allowed,
            "code_note": "" if allowed else expert_files.CODE_REFUSED, "editable": list(expert_files.EDITABLE)}


@app.post("/api/experts/{name}/files/check")
def check_expert_files(name: str, body: FilesIn, request: Request, load: bool = True):
    """Lint (load=false: what the editor runs as you type) or lint and load a draft, without saving it.
    Loading runs the draft's Python, so changed code needs the same permission as a save."""
    folder = _editor(lambda: expert_files._folder(EXPERTS_DIR, name))  # pyright: ignore[reportPrivateUsage]
    if load and expert_files.code_changed(expert_files.read_files(folder), body.files) and not _code_edit_allowed(request):
        raise HTTPException(403, expert_files.CODE_REFUSED)
    return _editor(lambda: expert_files.check(EXPERTS_DIR, name, body.files, load=load))


@app.put("/api/experts/{name}/files")
def save_expert_files(name: str, body: FilesIn, request: Request):
    allowed = _code_edit_allowed(request)
    return _saved(name, _editor(lambda: expert_files.save(EXPERTS_DIR, VERSIONS, name, body.files, base=body.base,
                                                          note=body.note, allow_code=allowed)))


@app.get("/api/experts/{name}/versions")
def expert_versions(name: str):
    return _editor(lambda: expert_files.list_versions(VERSIONS, EXPERTS_DIR, name))


@app.get("/api/experts/{name}/versions/{vid}")
def expert_version(name: str, vid: int):
    return _editor(lambda: expert_files.get_version(VERSIONS, name, vid))


@app.post("/api/experts/{name}/versions/{vid}/restore")
def restore_expert_version(name: str, vid: int, body: RestoreIn, request: Request):
    """Switch to a version: its files are saved as the expert's files, through the same checks as any save."""
    v = _editor(lambda: expert_files.get_version(VERSIONS, name, vid))
    allowed = _code_edit_allowed(request)
    return _saved(name, _editor(lambda: expert_files.save(EXPERTS_DIR, VERSIONS, name, v["files"], base=body.base,
                                                          note=f"Switched to version {vid}", allow_code=allowed)))


@app.delete("/api/experts/{name}")
def delete_expert(name: str):
    _editor(lambda: expert_store.delete(EXPERTS_DIR, name))
    off = _disabled()
    if name in off:
        off.discard(name)
        DISABLED.write_text(json.dumps(sorted(off)))
    _reload_experts()
    return {"deleted": name}


class ExpertTry(expert_store.ExpertIn):
    message: str = Field(min_length=1, max_length=2000)


@app.post("/api/experts/preview")
def try_expert(body: ExpertTry):
    """Run a draft generate expert on a sample message without saving it, so a prompt can be tuned before it is kept."""
    if not body.model or not body.system:
        raise HTTPException(400, "Choose a model and write a system prompt first.")
    draft = Expert(name=body.name or "draft", description=body.description, examples=body.examples, parameters={}, model={"id": body.model},
                   license=body.license, kind="generate", system=body.system, shots=[s.model_dump() for s in body.shots], strip=body.strip)
    t0 = time.perf_counter()
    try:
        return {"ok": True, "output": answer(draft, body.message, f"{OLLAMA}/v1", timeout=120), "ms": round((time.perf_counter() - t0) * 1000)}
    except llm.LLMError as e:
        return {"ok": False, "error": str(e)}
    except (httpx.HTTPError, KeyError, ValueError):
        return {"ok": False, "error": "The model did not answer. Is it running, and is its name right?"}


# ---- MCP servers as experts (noah/mcp_expert.py, noah/mcp_client.py) ----
class McpAdd(BaseModel):
    config: str = Field(min_length=2, max_length=20000)   # pasted JSON: {"mcpServers": {...}}, {"servers": {...}} or one server
    name: str = Field(default="", max_length=64)          # for a single pasted server
    model: str = Field(default="", max_length=200)        # writes the tools' examples, then picks a tool for each message
    description: str = Field(default="", max_length=500)


class McpRefresh(BaseModel):
    model: str = Field(default="", max_length=200)
    rewrite: bool = False   # write every tool's examples again, not only those of new or changed tools


class McpRun(BaseModel):
    expert: str
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict[str, Any])
    always: bool = False   # also run this tool without asking from now on


MCP_REFUSED = ("Adding an MCP server can start a program on the computer running Noah, so by default only someone at that computer "
               "may do it (set NOAH_CODE_EDIT=1 to allow it from anywhere).")


def _mcp_ready() -> None:
    if why := mcp_client.unavailable():
        raise HTTPException(400, why)


def _mcp_expert(name: str) -> Expert:
    e = experts.get(name)
    if e is None or e.kind != "mcp":
        raise HTTPException(404, f"No MCP expert named '{name}'.")
    return e


@app.post("/api/mcp/servers")
def add_mcp_servers(body: McpAdd, request: Request):
    """Connect to each pasted server, list its tools, write examples for them, and add one expert per server."""
    _mcp_ready()
    if not _code_edit_allowed(request):
        raise HTTPException(403, MCP_REFUSED)
    try:
        pairs = mcp_expert.parse_config(body.config, mcp_expert.expert_name(body.name) if body.name.strip() else "")
    except mcp_client.McpError as e:
        raise HTTPException(400, str(e))
    added: list[dict[str, Any]] = []
    failed: list[dict[str, str]] = []
    for name, server in pairs:
        try:
            if (EXPERTS_DIR / name).exists():
                raise expert_store.ExpertError(f"An expert named '{name}' already exists. Refresh its tools instead.")
            try:
                tools = mcp_client.list_tools(server)
                problems = mcp_expert.write_examples(tools, body.model, f"{OLLAMA}/v1")
                sign_in = False
            except mcp_client.SignInNeeded:   # added now; its tools are listed once the user has signed in
                server, tools, problems, sign_in = {**server, "oauth": True}, [], [], True
            data = mcp_expert.manifest(name, server, tools, body.model, body.description if len(pairs) == 1 else "")
            expert_store.create_mcp(EXPERTS_DIR, data, tools)
            expert_files.record(VERSIONS, name, expert_files.read_files(EXPERTS_DIR / name), "Added MCP server")
            added.append({"name": name, "server": mcp_client.describe_server(server), "tools": len(tools), "sign_in": sign_in,
                          "url": server.get("url", ""), "with_examples": sum(1 for t in tools if t["examples"]), "problems": problems[:5]})
        except (mcp_client.McpError, expert_store.ExpertError) as e:
            failed.append({"name": name, "error": str(e)})
    _reload_experts()
    return {"added": added, "failed": failed}


@app.post("/api/mcp/{name}/refresh")
def refresh_mcp_tools(name: str, body: McpRefresh):
    """List the server's tools again. Unchanged tools keep their examples (unless `rewrite`); new or changed ones get new ones."""
    _mcp_ready()
    e = _mcp_expert(name)
    mcp_client.disconnect(e.server)   # reconnect, so a server that was updated or restarted is seen as it is now
    try:
        tools = mcp_client.list_tools(e.server)
    except mcp_client.SignInNeeded as err:
        if not e.server.get("oauth"):   # remember it, so the UI offers Sign in
            expert_store.set_server(EXPERTS_DIR, name, {**e.server, "oauth": True})
            _reload_experts()
        raise HTTPException(401, str(err))
    except mcp_client.McpError as err:
        raise HTTPException(502, str(err))
    model = body.model or str(e.model.get("id", ""))
    problems = mcp_expert.write_examples(tools, model, f"{OLLAMA}/v1", keep=None if body.rewrite else e.tools)
    _editor(lambda: expert_files.baseline(VERSIONS, EXPERTS_DIR, name))
    expert_store.write_tools(EXPERTS_DIR, name, tools)
    expert_store.set_routing(EXPERTS_DIR, name, *mcp_expert.routing(name, tools))
    expert_files.record(VERSIONS, name, expert_files.read_files(EXPERTS_DIR / name), "Refreshed tools")
    _reload_experts()
    return {"tools": len(tools), "with_examples": sum(1 for t in tools if t["examples"]), "problems": problems[:5]}


@app.post("/api/mcp/run")
def run_mcp_tool(body: McpRun):
    """Run a tool the user confirmed in the chat (Run / Always allow)."""
    _mcp_ready()
    e = _mcp_expert(body.expert)
    if e.name in _disabled():
        raise HTTPException(409, f"Expert '{e.name}' is disabled.")
    tool = next((t for t in e.tools if t["name"] == body.tool), None)
    if tool is None:
        raise HTTPException(404, f"{e.name} has no tool named '{body.tool}'.")
    try:
        validate_args(body.arguments, tool.get("input_schema", {}))
    except ValueError as err:
        raise HTTPException(400, str(err))
    if body.always:
        mcp_expert.allow_always(e.name, body.tool)
    t0 = time.perf_counter()
    try:
        res = mcp_expert.call(e, body.tool, body.arguments)
    except mcp_client.McpError as err:
        res = {"text": f"Expert '{e.name}' failed: {err}", "files": [], "error": str(err)}
    return {"output": res["text"], "attachments": res["files"], "error": res.get("error"),
            "latency_ms": round((time.perf_counter() - t0) * 1000)}


@app.post("/api/mcp/{name}/signin")
def mcp_sign_in(name: str, request: Request):
    """Start signing in to the server: {url} to open in a popup, or {signed_in: true} when it needed no sign-in."""
    _mcp_ready()
    e = _mcp_expert(name)
    redirect = str(request.base_url).rstrip("/") + OAUTH_CALLBACK
    try:
        return mcp_client.sign_in(e.server, redirect)
    except mcp_client.McpError as err:
        raise HTTPException(502, str(err))


@app.delete("/api/mcp/{name}/signin")
def mcp_sign_out(name: str):
    e = _mcp_expert(name)
    mcp_client.sign_out(e.server)
    return {"signed_in": False}


@app.get(OAUTH_CALLBACK, include_in_schema=False)
def mcp_oauth_callback(state: str = "", code: str = "", iss: str | None = None, error: str = "", error_description: str = ""):
    """The server's sign-in page sends the user back here. The page tells Noah's tab (which then lists the tools) and closes."""
    ok, message = False, error_description or error
    if not error:
        try:
            mcp_client.finish_sign_in(state, code, iss)
            ok, message = True, "Signed in. You can close this window."
        except mcp_client.McpError as err:
            message = str(err)
    return HTMLResponse(oauth_page(ok, message))


def oauth_page(ok: bool, message: str) -> str:
    import html

    note = json.dumps({"noahMcpSignIn": True, "ok": ok, "message": message})
    return f"""<!doctype html><meta charset="utf-8"><title>Noah sign-in</title>
<body style="font:15px/1.6 system-ui,sans-serif;max-width:520px;margin:15vh auto;padding:0 20px">
<h2>{'Signed in' if ok else 'Sign-in failed'}</h2><p>{html.escape(message)}</p>
<script>const note = {note};
try {{ new BroadcastChannel('noah-mcp-oauth').postMessage(note); }} catch (e) {{}}
try {{ window.opener && window.opener.postMessage(note, location.origin); }} catch (e) {{}}
if (note.ok) setTimeout(() => window.close(), 800);</script></body>"""


@app.put("/api/experts/{name}")
def toggle_expert(name: str, req: ExpertToggle):
    if name not in experts:
        raise HTTPException(404, f"Unknown expert '{name}'")
    off = _disabled()
    off.discard(name) if req.enabled else off.add(name)
    DISABLED.parent.mkdir(parents=True, exist_ok=True)
    DISABLED.write_text(json.dumps(sorted(off)))
    return {"name": name, "enabled": req.enabled}


@app.get("/api/traces")
def traces(limit: int = 50):
    return trace.read()[-limit:][::-1]


def _profile_enabled(p: dict[str, Any]) -> bool:
    return p.get("enabled", True) is not False  # profiles saved before the switch existed count as enabled


def _load_profiles() -> list[dict[str, Any]]:
    if PROFILES.exists():
        try:
            return json.loads(PROFILES.read_text())
        except (OSError, ValueError):
            pass
    return json.loads(json.dumps(DEFAULT_PROFILES))  # deep copy so callers can mutate


def _save_profiles(profiles: list[dict[str, Any]]) -> None:
    PROFILES.parent.mkdir(parents=True, exist_ok=True)
    PROFILES.write_text(json.dumps(profiles, indent=2))


@app.get("/api/profiles")
def get_profiles():
    return _load_profiles()


@app.put("/api/profiles")
def put_profiles(profiles: list[dict[str, Any]]):
    if profiles and not any(_profile_enabled(p) for p in profiles):
        raise HTTPException(400, "At least one orchestrator must stay enabled.")
    _save_profiles(profiles)
    return profiles


class ProfileToggle(BaseModel):
    enabled: bool


@app.put("/api/profiles/{name:path}")
def toggle_profile(name: str, req: ProfileToggle):
    profiles = _load_profiles()
    target = next((p for p in profiles if p.get("name") == name), None)
    if target is None:
        raise HTTPException(404, f"Unknown orchestrator '{name}'")
    if not req.enabled and not any(_profile_enabled(p) for p in profiles if p is not target):
        raise HTTPException(400, "At least one orchestrator must stay enabled.")
    target["enabled"] = req.enabled
    _save_profiles(profiles)
    return {"name": name, "enabled": req.enabled}


class OpenRequest(BaseModel):
    path: str


def _file_or_404(path: str):
    try:
        return filesvc.safe_path(path)
    except filesvc.FileAccessError as e:
        raise HTTPException(404, str(e))


@app.get("/api/files/preview")
def file_preview(path: str):
    p = _file_or_404(path)
    return {**filesvc.describe(p), "preview": filesvc.preview(p)}


@app.get("/api/files/download")
def file_download(path: str):
    p = _file_or_404(path)
    # attachment + nosniff: the browser saves the file, it never renders or executes it from our origin
    return FileResponse(p, filename=p.name, content_disposition_type="attachment",
                        headers={"X-Content-Type-Options": "nosniff"})


@app.get("/api/files/image")
def file_image(path: str):
    """Raster images inline, for previews in the chat. Anything else (SVG included) only ever downloads."""
    p = _file_or_404(path)
    if filesvc.kind_of(p) != "image":
        raise HTTPException(400, "not an image; download it instead")
    return FileResponse(p, media_type=filesvc.IMAGE_TYPES[p.suffix.lower()],
                        headers={"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "default-src 'none'; sandbox"})


@app.post("/api/files/open")
def file_open(req: OpenRequest, request: Request):
    """Open with the OS default app. Only meaningful (and only allowed) on the machine running Noah."""
    if not _is_local(request):
        raise HTTPException(403, "Opening in another app only works on the computer running Noah. Download the file instead.")
    p = _file_or_404(req.path)
    try:
        filesvc.open_with_default_app(p)
    except filesvc.FileAccessError as e:
        raise HTTPException(400, str(e))
    except OSError as e:
        raise HTTPException(500, f"Could not start an application for this file ({e.__class__.__name__}).")
    return {"opened": True, "name": p.name}
