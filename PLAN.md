# Noah: layered tiny-model orchestration (plan)

Working name: **noah**. Goal: a tool where the user picks an *orchestrator* and a
*decision engine*, and each job goes to a small *expert* (tiny model + tool).

## Findings that shape the design
- Odysseus: AGPL, ~256k lines, agent loop (~38k lines) hard-wired to Qwen and regex rules.
  Not forkable into a slot-based design. Borrow ideas only: teacher escalation,
  retrieval-based tool selection, hardware-aware model recommendations.
- MAF (microsoft/agent-framework, MIT): `GroupChatBuilder(selection_func=...)`,
  switch/multi-selection edge groups, `HandoffBuilder`, checkpointing, events.
  OpenAI chat client takes `base_url`, so any OpenAI-compatible server works.

## Architecture
```
user message
   |
[Decision engine]  (message, expert cards) -> ranked expert ids + scores   <- slot 1
   |
[Orchestrator]     MAF workflow that runs the chosen expert(s)             <- slot 2
   |
[Expert]           manifest + small model (args extraction) + tool         <- N plug-ins
   |
[Trace store]      every decision, args, result, latency (JSONL)           -> later training
```

### Contracts (stable, small)
- `Expert` manifest (`experts/<name>/expert.yaml`): name, description, examples,
  parameters (JSON schema), model (backend/id), tool (python entrypoint or MCP), license.
- `DecisionEngine` protocol: `decide(message, cards) -> list[Decision(expert, score)]`.
  Implementations: `keyword` (no deps, baseline), `embedding` (fastembed/bge-small),
  `gliclass` (zero-shot classifier), `llm` (tiny LLM via OpenAI-compatible endpoint).
- `ArgExtractor` protocol: `extract(message, schema) -> dict`. Implementations:
  `rule` (regex, baseline), `openai_compat` (FunctionGemma/xLAM/any model behind
  llama.cpp, vLLM, Ollama).
- `Orchestrator`: builds a MAF workflow. Modes: `route` (single best expert, group chat with
  selection_func), `fanout` (multi-selection, concurrent experts), `handoff` (LLM triage).

## Phases
1. **PoC core (this session)**
   - Package `noah/` with the contracts, registry, trace store.
   - Expert 1: `weather` (Open-Meteo, no key). Expert 2: `calc` (local, deterministic) to make
     routing non-trivial. Expert 3: `files` stub (Drive-like, reads a local folder) to show a non-web expert.
   - Decision engines: `keyword`, `embedding`.
   - Orchestrator `route` mode on MAF with `selection_func`.
   - CLI: `noah ask "..."`, `noah experts`, `noah trace`.
   - Tests: routing accuracy on a labelled set; no network or model needed.
2. **Real tiny model test**: serve FunctionGemma-270M or xLAM-1b via llama.cpp/Ollama,
   measure arg-extraction accuracy vs the rule baseline on the same labelled set.
   This is the riskiest assumption; decide go/no-go on its result.
3. **UI**: FastAPI + plain JS. Slot pickers, expert list, chat with decision trace.
4. **More slots**: `gliclass` engine, `llm` engine, `handoff`/`fanout` orchestrators, teacher
   fallback, trace -> fine-tune dataset export.
5. **Packaging experts**: install from a folder or Hugging Face repo; license checks.

## Success criteria for the PoC
- Swapping the decision engine is one config line and changes no other code.
- Adding an expert is a folder with a manifest and a tool, no core edits.
- Routing accuracy on the labelled set reported per engine.
- Every request leaves a trace record.

## Risks
- Tiny models may produce invalid tool calls: mitigate with schema validation, retry, rule fallback.
- MAF's local-model path is less documented: keep model access behind our own protocol.
- Python 3.14 compatibility of dependencies (MAF, fastembed): verify in step 1, fall back to 3.12 venv if needed.
- Some candidate models are non-commercial or gated (xLAM CC-BY-NC, FunctionGemma gated): manifest stores license.

## Status (2026-10-09)
- Phase 1 done: contracts, registry, trace store, `keyword` + `embedding` engines, `route`/`fanout`
  orchestrator on MAF, 3 experts, CLI, 6 passing tests. Routing accuracy on 12 labelled messages:
  keyword 92%, embedding (bge-small) 100%.
- Phase 2 done: Ollama 0.40.2 installed in ~/tools/ollama (user dir, no sudo). Extraction benchmark
  (`scripts/bench_extract.py`, 15 cases, no fallback, schema-constrained JSON mode):
  rule 11/15 | qwen2.5:0.5b 14/15 (76-200 ms) | llama3.2:1b 14/15 (~400 ms) | qwen3:0.6b 14/15 (1.6 s)
  | smollm2:360m 12/15 | gemma3:270m 12/15. Most misses are tool limits, not model errors.
  FunctionGemma in Ollama has no tool template ("does not support tools"), so native tool calling
  was not testable; schema mode works with any model and is now the default.
- Phase 3 done: FastAPI server `noah/server.py` + Odysseus-style UI (`noah/web/`): dark/light themes,
  Fira Code, collapsible sidebar, chat history, composer with orchestrator picker and Route/Fan-out toggle,
  views for Orchestrators (saved profiles, server-side in data/profiles.json), Experts, Models, Traces.
  Driven with headless Chromium via `scripts/shots.py`: no console errors.
- Next: browser check, gliclass/llm engines, teacher fallback, trace -> fine-tune export, expert install.
- Generate-type experts added: `csharp` and `python` (Qwen2.5-Coder 1.5B via Ollama; manifest `kind: generate`
  with its own model + system prompt, no tool.py). Routing on 20 labelled messages: keyword 95%, embedding 100%;
  C# and Python never confused. Quality caveat: a 1.5B model adds unused usings / mislabels algorithms.
- `html` and `css` generate experts added (7 experts total). Routing on 28 labelled messages: keyword 96%,
  embedding 100%. HTML vs CSS genuinely overlap on mixed prompts ("style my HTML table": 0.75 vs 0.74), so
  fan-out mode is the right tool there. 1.5B model ignored prompt-only rules ("CSS only"); fixed with a
  one-shot example (`shots:`) and a hard output filter (`strip:` regexes) in the manifest.
- Containers done: `Dockerfile` (python:3.12-slim, deps read from pyproject, embedding model baked in, non-root),
  `compose.yaml` (ollama + one-shot model puller + noah with live reload), `compose.gpu.yaml`, `Makefile`
  (auto Docker/Podman), README quick start. Verified with rootless Podman 5.7 + podman-compose: full stack up,
  models pulled, requests answered through the containerized Ollama, host bind mounts writable, YAML live reload.
  Not verified: real Docker (not installed here), NVIDIA GPU overlay, `podman compose` with the docker-compose provider.
  Bugs found while verifying: podman-compose pod ignored keep-id (fixed with `x-podman: in_pod: false`);
  uvicorn needs `watchfiles` for `--reload-include`; trace-write failure no longer fails requests.
- LLM orchestrators added (`llm_tools` engine; Nemotron-Orchestrator-8B and FunctionGemma example profiles).
  28 labelled + 4 off-topic: embedding 28/28 & 4/4 (only after threshold 0.25 -> 0.63; before, it declined nothing),
  Nemotron 19/28 & 4/4 (median 9 s), FunctionGemma 9/28 & 3/4 (0.6 s). Router now calls the engine in a thread.
  Caveat: the Nemotron run overlapped with other GPU work (one 300 s timeout), so its latency is pessimistic.
- Desktop (Electron 38) done on Linux: `desktop/` shell spawns the server (random port, per-launch token, Host check,
  exits with its parent), PyInstaller sidecar (169 MB) verified end to end, AppImage 182 MB smoke-tested with a real
  window and no leaked processes. Bugs found: ELECTRON_RUN_AS_NODE set by VS Code; Wayland+NVIDIA segfault (X11 wrapper
  in after-pack.js); `os.kill(pid, 0)` would terminate processes on Windows (fixed with a ctypes check).
  NOT verified: Windows and macOS builds (CI workflow written, never run), `.deb` (needs maintainer metadata),
  first-run embedding model download inside the packaged app, installer signing.
- Files expert upgraded (2026-10-13): opens txt/md/csv/pdf in the chat (new `noah/filesvc.py`, `/api/files/{preview,download,open}`,
  `attachments` on expert results, file cards in the UI), falls back to Download / Open with app for other types or unreadable
  files. Path confinement + local-only launch + allow-listed types; previews never stored in traces. pypdf added. Verified in
  Chromium (stubbed the real app launch) and through the frozen PyInstaller server. 41+ tests. Lesson: generic example phrases in
  an expert's manifest raised off-topic scores above the 0.63 threshold; file-specific examples widened the gap from -0.05 to +0.08.
  NOT verified: "Open with app" actually launching apps (Linux xdg-open / Windows startfile / macOS open), PDFs with complex
  layouts, encrypted PDFs, very large files, the Windows/macOS packaged builds.
- Orchestrator enable/disable switch (2026-10-13): `enabled` flag per profile (missing = enabled), `PUT /api/profiles/{name}`,
  server refuses disabled profiles (409) and refuses disabling the last enabled one (400); UI switch on cards, picker lists
  enabled only, active falls back, sidebar shows enabled/total. 7 new server tests + browser walkthrough (throwaway server).
  Process slip: one scripted edit truncated server.py (file endpoints); caught by grep, restored from git, tests confirmed.
- UI moved to Vue 3 (2026-10-13): vendored `vue.global.prod.js` (3.5.13, MIT, ~158 KB, no build step, works offline in the desktop app),
  markup in `index.html`, state and logic in `app.js` (~300 lines, was 275 of string-building render functions). Cards and the
  orchestrator editor now read one `profiles` + `draft` state, so they cannot disagree (the editor edits a draft, the selected card
  shows it live with an "unsaved" tag; switching cards discards it). Duplicate orchestrator names are refused. Verified in Chromium
  (46 checks: chat, file previews, editor/card sync, save/new/delete, both switches and counts, models, traces, theme, reload
  persistence; no console warnings) plus the existing screenshot script; 55 Python tests pass. NOT verified: the packaged
  desktop build with the new files (sidecar bundles `noah/web` wholesale, so it should just work), Windows/macOS.
- UI routing (2026-10-13): the URL hash is the route (`#/orch`, `#/experts`, `#/models`, `#/traces`, `#/chat`, `#/chat/<id>`), so refresh, back/forward and deep links work; unknown views or chat ids fall back to a new chat. Hash routing needs no server support and works in the desktop app. 20 browser checks + the 46 earlier ones pass.
- Vite + .vue components (2026-10-13): the UI moved from one app.js with a vendored Vue to `frontend/` (Vite 8, Vue 3.5.44, single-file components, state in
  `src/store.js`). `npm run dev` gives HMR on :5173 with /api proxied to the server; `npm run build` writes `noah/web/dist` (92 KB JS / 35 KB gzip, smaller than the
  158 KB vendored runtime + compiler). Server serves dist (`NOAH_WEB_DIST` overrides; a "not built" page if missing), the Dockerfile builds it in a Node stage,
  `build_sidecar.py` builds it before PyInstaller, CI caches the frontend lockfile. Dropped the vendored Vue (3.5.13 has an npm-audit advisory; 3.5.44 clean).
  Verified: the 46+20 browser checks pass against the built UI, the Vite dev server and the PyInstaller-frozen server; HMR kept the page and half-typed text across a
  component edit and applied a CSS edit live; Docker UI stage built with Podman; 55 Python tests. NOT verified: the full Docker image, Windows/macOS CI, Electron
  window with the new bundle (the frozen server serves it, so it should work). Trade-off: a source checkout now needs one `npm run build` (Node), where before it needed none.
- Vite is the dev entry (2026-10-13): `make dev` adds a `ui` service (node:22, bind-mounted frontend, polling) at :5173 proxying to the API container; `make dev-host` runs API+Vite on the host. Verified HMR (component + CSS edit, typed text kept) through the container service with a bind mount. `make dev-host` itself not run (port 8765 was in use); full `make dev` compose not run.
- Hugging Face models (2026-10-13): any model slot can be local (Ollama) or hosted on Hugging Face (`hf:org/name` -> Inference Providers router with the
  user's token, no download); the Models page labels each model's origin, searches HF (hosted list + GGUF repos), adds hosted models, and downloads a GGUF into
  Ollama with quant sizes and progress. New `noah/llm.py` (one `chat()` used by generate, both LLM engines and the extractor; plain-language HF errors, token
  never in messages), `noah/hf.py` (token, search, quants, pull, my models, test). Orchestrator model dropdowns grouped Local / Hugging Face. 26 new tests
  (network faked), plus a browser run against a stub standing in for Ollama + HF router + hub (token, hosted add/test, download with progress, dropdown groups,
  a chat answered through a hosted extractor, disconnect). Real HF endpoints verified unauthenticated only (model list, GGUF search, quants, token rejection).
  NOT verified: any real inference call (no token here), real Ollama pull of an HF repo, provider differences (tools/structured output/logprobs), Windows/macOS.
