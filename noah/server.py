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
from . import azure, expert_files, expert_store, hf, llm, openrouter
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
    if LAUNCH_TOKEN:
        host = request.headers.get("host", "").rsplit(":", 1)[0].strip("[]").lower()
        supplied = request.cookies.get("noah_token", "") or request.headers.get("authorization", "").removeprefix("Bearer ")
        if host not in ("127.0.0.1", "localhost", "::1") or not hmac.compare_digest(supplied, LAUNCH_TOKEN):
            return JSONResponse({"error": "unauthorized"}, status_code=401)
    return await call_next(request)

app.include_router(hf.router)
app.include_router(openrouter.router)
app.include_router(azure.router)
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
        "engines": ["keyword", "embedding", "llm_tools", "llm_choice", *(["laya"] if HAS_LAYA else [])],
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
        "experts": [{"name": e.name, "description": e.description, "license": e.license,
                     "parameters": e.parameters, "examples": e.examples,
                     "kind": e.kind, "model": e.model.get("id", ""),
                     "enabled": e.name not in _disabled()} for e in experts.values()],
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
