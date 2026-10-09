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
