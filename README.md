# Noah

Layered tiny-model orchestration. You pick a **decision engine** and an **orchestrator**; each job goes to a
small **expert** (a tiny model, a tool, or both). Every decision is traced, so you can later fine-tune your own.

```
message -> [decision engine] -> [orchestrator: Microsoft Agent Framework workflow] -> [expert(s)] -> answer
                                                                                         \-> trace (JSONL)
```

## Screenshots

Each message is routed to one tiny expert; open "routing decision" to see how every expert scored.

![Three experts answering in one chat, with the routing scores expanded](docs/chat.png)

A generate expert (C#, Qwen2.5-Coder 1.5B) answers with code. Orchestrator profiles (decision engine + mode + extractor + model) are managed in the UI.

| Code expert | Orchestrator profiles |
|---|---|
| ![C# expert answering with a code block](docs/code.png) | ![Orchestrators page with a profile editor](docs/orchestrators.png) |

Regenerate them against a running server with `python scripts/readme_shots.py` (needs `pip install -e ".[dev]"` plus `playwright install chromium`, or `CHROMIUM=/path/to/chromium`).

## Quick start (Docker or Podman)

```bash
make up            # builds the image, starts Ollama, pulls the tiny models, starts Noah
# open http://localhost:8765
```

`make` auto-detects Docker or Podman. First start downloads about 1.5 GB of models; later starts are instant.

| You use | Needs |
|---|---|
| Docker | Docker with the Compose plugin (`docker compose`) |
| Podman | Podman 4+ and a compose provider: `pip install podman-compose` or `docker-compose` |

Without `make`:

```bash
docker compose up --build                       # Docker
PODMAN_USERNS=keep-id podman compose up --build # Podman (rootless: keep-id makes bind mounts writable)
```

NVIDIA GPU for Ollama: `make up GPU=1` (Podman also needs the CDI spec: `nvidia-ctk cdi generate`).

Code, `experts/`, `data/` and `traces/` are bind-mounted, so edits reload live and traces survive restarts.
Ollama is not published to the host, so a host Ollama on port 11434 keeps working.

Other targets: `make test` (tests inside the image), `make logs`, `make shell`, `make down`,
`make pull-models M=qwen2.5-coder:3b`. Change the default models with `NOAH_MODELS="a b" make up`.

## Without containers

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
ollama serve &                                   # any OpenAI-compatible server works
ollama pull qwen2.5:0.5b && ollama pull qwen2.5-coder:1.5b
uvicorn noah.server:app --port 8765              # NOAH_LLM_URL=http://host:port to point elsewhere
pytest
python -m noah.cli ask "calculate 15% of 240" --engine embedding
```

## Slots

| Slot | Choices today | Add your own |
|---|---|---|
| Decision engine | `keyword`, `embedding`, `llm_tools` | implement `DecisionEngine` in `noah/engines/` |
| Orchestrator | `route` (best expert), `fanout` (several) | `noah/orchestrator.py` |
| Argument extractor | `rule`, `openai_compat` (any small model, schema-constrained JSON) | `noah/extractors/` |

Orchestrator profiles (a saved combination of the above) are created in the UI and stored in `data/profiles.json`.

## LLM orchestrators (Nemotron, FunctionGemma)

Besides score-based engines (`keyword`, `embedding`), the `llm_tools` engine hands the experts to a tool-calling
model as tools. The model picks the expert **and** its arguments in one call (with an explicit "none" tool so it can
decline), so no separate extractor model runs. Two example profiles ship next to `Default`:

```bash
ollama pull hf.co/MaziyarPanahi/Nemotron-Orchestrator-8B-GGUF:Q4_K_M   # 5 GB
ollama pull hf.co/ggml-org/functiongemma-270m-it-GGUF:Q8_0             # 300 MB
```

Measured on this repo's 28 labelled messages + 4 off-topic ones (`scripts/bench_orchestrators.py`), GTX 1070 8 GB:

| Orchestrator | Routes correctly | Declines off-topic | Time per decision |
|---|---|---|---|
| `embedding` (Default, threshold 0.63) | 28/28 | 4/4 | ~5 ms |
| Nemotron-Orchestrator-8B Q4_K_M | 19/28 | 4/4 | median 9 s (about 50 s on first load) |
| FunctionGemma 270M Q8_0 | 9/28 | 3/4 | median 0.6 s |

Read this as: the zero-shot LLM orchestrators are examples, not recommendations. Nemotron picks arguments well but
often answers directly instead of calling an expert, and is slow on a small GPU. FunctionGemma is meant to be
fine-tuned (the traces are the data for that). The embedding engine wins on this small, easy test set, but its
off-topic margin is thin (0.61 vs 0.65), so re-tune the threshold when you add experts.
Check each model's license before use: a third-party listing says Nemotron-Orchestrator-8B is research-only, and
FunctionGemma is under the Gemma terms.

## Adding an expert

Create `experts/<name>/expert.yaml` (the app reloads when you save). Two kinds:

```yaml
# 1) tool expert: a small model extracts arguments, tool.py does the work
name: weather
description: Current weather for a city.
examples: [what's the weather in Paris]
parameters: {type: object, properties: {city: {type: string}}, required: [city]}
license: MIT
# + experts/weather/tool.py with  run(args) -> str  and optional  extract_rule(message) -> dict
```

```yaml
# 2) generate expert: its own small model answers directly
name: csharp
kind: generate
description: Writes, explains and fixes C# and .NET code.
examples: [write a C# method that reverses a string]
model: {backend: openai_compat, id: "qwen2.5-coder:1.5b"}
system: You are a C# expert. Answer only with C#.
shots: [{user: "...", assistant: "..."}]   # optional few-shot pair that pins the output style
strip: ["<style\\b.*?</style>"]            # optional regexes removed from the output (hard domain guard)
license: Apache-2.0
```

Good `description` and `examples` matter most: the decision engine routes on them.
Note each expert's model license (the manifest has a `license` field); some small models are non-commercial.

## Known limits

- Small models make small mistakes (unused imports, mislabelled algorithms). Verify generated code.
- Ambiguous prompts that span experts (HTML vs CSS) score nearly equal; use fan-out mode for those.
- Chat history lives in the browser (localStorage), not on the server.
