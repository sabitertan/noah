# Works with both Docker and Podman:  docker build -t noah .   |   podman build -t noah .
# The web UI is built once in a throwaway Node stage; the final image has no Node.
FROM node:22-slim AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend ./
RUN npm run build -- --outDir /ui-dist --emptyOutDir

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 FASTEMBED_CACHE_PATH=/opt/fastembed
WORKDIR /app

# Dependencies come from pyproject.toml only, so this layer is cached until deps change.
COPY pyproject.toml ./
RUN python - <<'PY' > /tmp/requirements.txt
import tomllib
p = tomllib.load(open("pyproject.toml", "rb"))["project"]
print("\n".join(p["dependencies"] + p["optional-dependencies"]["container"]))
PY
RUN pip install -r /tmp/requirements.txt

# Bake the embedding model into the image so the container works offline.
RUN python -c "from fastembed import TextEmbedding; TextEmbedding(model_name='BAAI/bge-small-en-v1.5')"

COPY noah ./noah
COPY --from=ui /ui-dist /app/web-dist
COPY experts ./experts
COPY tests ./tests
COPY data ./data

RUN useradd --create-home noah && mkdir -p /app/traces && chown -R noah /app /opt/fastembed
USER noah

ENV NOAH_LLM_URL=http://ollama:11434 PYTHONPATH=/app NOAH_WEB_DIST=/app/web-dist
EXPOSE 8765
CMD ["uvicorn", "noah.server:app", "--host", "0.0.0.0", "--port", "8765"]
