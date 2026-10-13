# Picks Docker or Podman automatically. Override with:  make up RUNTIME=podman
RUNTIME ?= $(shell command -v docker >/dev/null 2>&1 && echo docker || echo podman)
COMPOSE ?= $(RUNTIME) compose
GPU     ?=            # make up GPU=1  adds the NVIDIA overlay
FILES   = -f compose.yaml $(if $(GPU),-f compose.gpu.yaml)

# Rootless Podman: map the host user into the container so bind mounts (traces, data) are writable.
ifeq ($(RUNTIME),podman)
export PODMAN_USERNS = keep-id
endif

.PHONY: up dev dev-host ui ui-build down logs build test shell pull-models
up:            ## build and start everything, UI at http://localhost:8765
	mkdir -p traces data
	$(COMPOSE) $(FILES) up --build
dev:           ## like up, plus hot reload: Python/YAML via uvicorn, the web UI via Vite at http://localhost:5173
	mkdir -p traces data
	$(COMPOSE) $(FILES) -f compose.dev.yaml up --build
dev-host:      ## no containers: API with reload on :8765 + Vite on :5173 (Linux/macOS; Windows: run the two commands from the README)
	(cd frontend && npm install --no-audit --no-fund) && trap 'kill 0' INT TERM EXIT; \
	  ( . .venv/bin/activate && uvicorn noah.server:app --port 8765 --reload --reload-include "*.yaml" ) & \
	  ( cd frontend && npm run dev ) & wait
ui:            ## only the web UI with hot reload at http://localhost:5173 (proxies /api to the server on :8765)
	cd frontend && npm install && npm run dev
ui-build:      ## build the web UI into noah/web/dist (what the server and desktop app serve)
	cd frontend && npm ci && npm run build
down:          ## stop and remove containers (models volume is kept)
	$(COMPOSE) $(FILES) down
logs:
	$(COMPOSE) $(FILES) logs -f noah
build:
	$(RUNTIME) build -t noah:dev .
test: build    ## run the test suite inside the image
	$(RUNTIME) run --rm noah:dev pytest -q
shell:
	$(COMPOSE) $(FILES) exec noah bash
pull-models:   ## pull an extra model into the compose Ollama, e.g. make pull-models M=qwen2.5-coder:3b
	$(COMPOSE) $(FILES) exec ollama ollama pull $(M)
