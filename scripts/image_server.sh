#!/usr/bin/env bash
# Start a local image server for the `image` expert: stable-diffusion.cpp's sd-server with an open image model.
# Downloads what is missing (the sd.cpp Vulkan build and the model files), then serves the OpenAI-style
# POST /v1/images/generations that experts/image/tool.py calls (NOAH_IMAGE_URL, default http://localhost:1234).
#
#   scripts/image_server.sh            # Qwen-Image-2.1 (unsloth GGUF, Q4_K_M): ~10 GB, best quality, Qwen Research License (non-commercial)
#   scripts/image_server.sh qwen-turbo # Qwen-Image-2.1-Turbo (unsloth GGUF, Q4_K_M): same model distilled to 8 steps, ~5x faster
#   scripts/image_server.sh zimage     # Z-Image-Turbo (unsloth GGUF, Q4_K_M): ~8 GB, 8 steps so much faster, Apache-2.0
#
# Env: NOAH_IMAGE_DIR (where files go, default ~/.cache/noah/image), NOAH_IMAGE_HOST (127.0.0.1), NOAH_IMAGE_PORT (1234),
#      SD_SERVER (use your own sd-server, e.g. a CUDA build), SD_ARGS (extra sd-server flags), HF_TOKEN (optional).
set -euo pipefail

PRESET="${1:-qwen}"
DIR="${NOAH_IMAGE_DIR:-$HOME/.cache/noah/image}"
PORT="${NOAH_IMAGE_PORT:-1234}"
HOST="${NOAH_IMAGE_HOST:-127.0.0.1}"   # 0.0.0.0 when Noah runs in a container (make up): it cannot reach the host's loopback
SD_RELEASE="${SD_RELEASE:-master-964-1d0a58a}"   # stable-diffusion.cpp release with Qwen-Image-2.1 and Z-Image support
HF="https://huggingface.co"

case "$PRESET" in
  qwen)
    MODEL="unsloth/Qwen-Image-2.1-GGUF qwen-image-2.1-Q4_K_M.gguf"
    VAE="unsloth/Qwen-Image-2.1-FP8 vae/qwen_image_2.1_vae_bf16.safetensors"
    LLM="unsloth/Qwen3-VL-8B-Instruct-GGUF Qwen3-VL-8B-Instruct-UD-Q4_K_XL.gguf"
    SAMPLING=(--steps 20 --cfg-scale 6.0 --sampling-method euler) ;;
  qwen-turbo)
    MODEL="unsloth/Qwen-Image-2.1-Turbo-GGUF qwen-image-2.1-turbo-Q4_K_M.gguf"
    VAE="unsloth/Qwen-Image-2.1-FP8 vae/qwen_image_2.1_vae_bf16.safetensors"
    LLM="unsloth/Qwen3-VL-8B-Instruct-GGUF Qwen3-VL-8B-Instruct-UD-Q4_K_XL.gguf"
    SAMPLING=(--sigmas qwen-image-2.1-turbo --steps 8 --cfg-scale 1.0 --sampling-method euler) ;;
  zimage)
    MODEL="unsloth/Z-Image-Turbo-GGUF z-image-turbo-Q4_K_M.gguf"
    VAE="Comfy-Org/z_image_turbo split_files/vae/ae.safetensors"
    LLM="unsloth/Qwen3-4B-Instruct-2507-GGUF Qwen3-4B-Instruct-2507-UD-Q4_K_XL.gguf"
    SAMPLING=(--steps 8 --cfg-scale 1.0) ;;
  *) echo "usage: $0 [qwen|qwen-turbo|zimage]" >&2; exit 2 ;;
esac

fetch() {  # fetch <repo> <path-in-repo>: prints the local file, downloading (resumably) when missing
  local repo="$1" file="$2" out="$DIR/models/${2##*/}"
  if [[ ! -s "$out" ]]; then
    mkdir -p "$DIR/models"
    echo "Downloading $repo/$file" >&2
    curl -fL --retry 3 -C - ${HF_TOKEN:+-H "Authorization: Bearer $HF_TOKEN"} -o "$out.part" "$HF/$repo/resolve/main/$file" >&2
    mv "$out.part" "$out"
  fi
  echo "$out"
}

SERVER="${SD_SERVER:-}"
if [[ -z "$SERVER" ]]; then
  SERVER="$DIR/sd-$SD_RELEASE/sd-server"
  if [[ ! -x "$SERVER" ]]; then
    [[ "$(uname -s)" == Linux ]] || { echo "Prebuilt download is Linux only here; set SD_SERVER to your sd-server (github.com/leejet/stable-diffusion.cpp/releases)." >&2; exit 1; }
    zip="sd-master-${SD_RELEASE##*-}-bin-Linux-Ubuntu-24.04-x86_64-vulkan.zip"   # assets are named by the commit hash
    mkdir -p "$DIR/sd-$SD_RELEASE"
    echo "Downloading stable-diffusion.cpp $SD_RELEASE (Vulkan build: NVIDIA, AMD and Intel GPUs)" >&2
    curl -fL --retry 3 -o "$DIR/sd.zip" "https://github.com/leejet/stable-diffusion.cpp/releases/download/$SD_RELEASE/$zip"
    python3 -m zipfile -e "$DIR/sd.zip" "$DIR/sd-$SD_RELEASE" && rm "$DIR/sd.zip"
    found="$(find "$DIR/sd-$SD_RELEASE" -name sd-server -type f | head -1)"
    [[ -n "$found" ]] || { echo "sd-server not found in the release archive" >&2; exit 1; }
    [[ "$found" == "$SERVER" ]] || ln -sf "$found" "$SERVER"
    chmod +x "$found"
  fi
fi

read -r m_repo m_file <<<"$MODEL"; read -r v_repo v_file <<<"$VAE"; read -r l_repo l_file <<<"$LLM"
model="$(fetch "$m_repo" "$m_file")"; vae="$(fetch "$v_repo" "$v_file")"; llm="$(fetch "$l_repo" "$l_file")"

echo "Starting sd-server ($PRESET) on http://$HOST:$PORT" >&2
export LD_LIBRARY_PATH="$(dirname "$(readlink -f "$SERVER")")${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
# --offload-to-cpu keeps weights in RAM and streams them to the GPU, so an 8 GB card runs models that do not fit in VRAM
# shellcheck disable=SC2086
exec "$SERVER" --diffusion-model "$model" --vae "$vae" --llm "$llm" "${SAMPLING[@]}" \
  --offload-to-cpu --flash-attn off,diffusion=on --listen-ip "$HOST" --listen-port "$PORT" ${SD_ARGS:-}
