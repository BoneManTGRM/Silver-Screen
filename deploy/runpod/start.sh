#!/usr/bin/env bash
set -Eeuo pipefail

repo_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)
workspace=/workspace
comfy_dir="$workspace/ComfyUI"
comfy_commit=0228a9d73cd7c2daf8f61fe13d56b12efe307171

if [[ -n ${SILVER_SCREEN_PUBLIC_HOST:-} ]]; then
    browser_host=$SILVER_SCREEN_PUBLIC_HOST
    caddy_config="$repo_dir/deploy/ec2/Caddyfile"
else
    : "${RUNPOD_POD_ID:?Set SILVER_SCREEN_PUBLIC_HOST outside RunPod}"
    browser_host="$RUNPOD_POD_ID-8080.proxy.runpod.net"
    caddy_config="$repo_dir/deploy/runpod/Caddyfile"
fi
studio_venv=${SILVER_SCREEN_STUDIO_VENV:-$repo_dir/.venv}

: "${SILVER_SCREEN_PASSWORD:?Set a private Silver Screen password}"
if (( ${#SILVER_SCREEN_PASSWORD} < 11 )); then
    echo "Use a Silver Screen password of at least 11 characters." >&2
    exit 1
fi
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y --no-install-recommends caddy curl ffmpeg git python3-venv

# Compute the proxy credential before starting the application processes.
export SILVER_SCREEN_PASSWORD_HASH
SILVER_SCREEN_PASSWORD_HASH=$(caddy hash-password --plaintext "$SILVER_SCREEN_PASSWORD")
unset SILVER_SCREEN_PASSWORD

if [[ ! -d "$comfy_dir/.git" ]]; then
    git init "$comfy_dir"
    git -C "$comfy_dir" remote add origin https://github.com/Comfy-Org/ComfyUI.git
fi
if [[ "$(git -C "$comfy_dir" rev-parse --verify HEAD 2>/dev/null || true)" != "$comfy_commit" ]]; then
    git -C "$comfy_dir" fetch --depth 1 origin "$comfy_commit"
    git -C "$comfy_dir" checkout --detach "$comfy_commit"
fi

python -c 'import torch; assert torch.cuda.is_available(), "No usable CUDA GPU found"'
if [[ ! -x "$comfy_dir/.venv/bin/python" ]]; then
    python -m venv --system-site-packages "$comfy_dir/.venv"
fi
"$comfy_dir/.venv/bin/python" -m pip install -r "$comfy_dir/requirements.txt"

if [[ ! -x "$studio_venv/bin/python" ]]; then
    python -m venv "$studio_venv"
fi
"$studio_venv/bin/python" -m pip install -r "$repo_dir/requirements.txt"

download_model() {
    local url=$1 relative_path=$2
    local destination="$comfy_dir/models/$relative_path"
    if [[ -s "$destination" ]]; then
        return
    fi
    mkdir -p "$(dirname "$destination")"
    curl --fail --location --retry 3 --connect-timeout 20 --output "$destination.part" "$url"
    mv "$destination.part" "$destination"
}

wan=https://huggingface.co/Comfy-Org/Wan-2.2/resolve/main/split_files
download_model https://huggingface.co/Comfy-Org/Flux-1-Schnell/resolve/main/flux1-schnell-fp8.safetensors checkpoints/flux1-schnell-fp8.safetensors
download_model "$wan/diffusion_models/wan2.2_ti2v_5B_fp16.safetensors" diffusion_models/wan2.2_ti2v_5B_fp16.safetensors
download_model "$wan/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors" text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors
download_model "$wan/vae/wan2.2_vae.safetensors" vae/wan2.2_vae.safetensors

export SILVER_SCREEN_COMFYUI_URL=http://127.0.0.1:8188
export SILVER_SCREEN_COMFYUI_IMAGE_WORKFLOW="$repo_dir/deploy/runpod/workflows/flux-schnell.json"
export SILVER_SCREEN_COMFYUI_VIDEO_WORKFLOW="$repo_dir/deploy/runpod/workflows/wan-video.json"
export SILVER_SCREEN_COMFYUI_REFERENCE_WORKFLOW="$repo_dir/deploy/runpod/workflows/wan-reference.json"
export SILVER_SCREEN_COMFYUI_FPS=24
export SILVER_SCREEN_VIDEO_BACKEND=comfyui

pids=()
cleanup() {
    if (( ${#pids[@]} )); then
        kill "${pids[@]}" 2>/dev/null || true
        wait "${pids[@]}" 2>/dev/null || true
    fi
}
trap cleanup EXIT
trap 'exit 143' TERM
trap 'exit 130' INT

cd "$comfy_dir"
env -u SILVER_SCREEN_PASSWORD_HASH .venv/bin/python main.py --listen 127.0.0.1 --port 8188 --disable-auto-launch --disable-all-custom-nodes --disable-metadata &
pids+=("$!")

cd "$repo_dir"
env -u SILVER_SCREEN_PASSWORD_HASH "$studio_venv/bin/python" -m streamlit run streamlit_app.py \
    --server.headless=true --server.address=127.0.0.1 --server.port=8501 \
    --browser.serverAddress="$browser_host" \
    --server.enableCORS=true --server.enableXsrfProtection=true &
pids+=("$!")

caddy run --adapter caddyfile --config "$caddy_config" &
pids+=("$!")
wait -n "${pids[@]}"
