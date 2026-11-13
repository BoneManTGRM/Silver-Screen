#!/usr/bin/env bash
set -euo pipefail
: "${SILVER_SCREEN_PASSWORD_HASH:?Set the private studio password hash}"
streamlit run streamlit_app.py --server.headless=true --server.address=127.0.0.1 --server.port=8501 &
studio_pid=$!
caddy run --config /etc/caddy/Caddyfile --adapter caddyfile &
proxy_pid=$!
trap 'kill "$studio_pid" "$proxy_pid" 2>/dev/null || true' EXIT
trap 'exit 143' TERM
trap 'exit 130' INT
wait -n "$studio_pid" "$proxy_pid"
