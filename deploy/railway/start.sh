#!/usr/bin/env bash
set -euo pipefail
if [[ -z ${SILVER_SCREEN_PASSWORD_HASH:-} ]]; then
    : "${SILVER_SCREEN_PASSWORD:?Set the private studio password}"
    if (( ${#SILVER_SCREEN_PASSWORD} < 12 )); then
        echo "Use a studio password with at least 12 characters." >&2
        exit 1
    fi
    SILVER_SCREEN_PASSWORD_HASH=$(printf '%s\n' "$SILVER_SCREEN_PASSWORD" | caddy hash-password)
fi
export SILVER_SCREEN_PASSWORD_HASH
unset SILVER_SCREEN_PASSWORD
# Safari can omit Basic Auth on WebSocket and media requests.
SILVER_SCREEN_SESSION_KEY=$(python -c 'import secrets; print(secrets.token_hex(32))')
export SILVER_SCREEN_SESSION_KEY
python -m uvicorn railway_studio:app --host 127.0.0.1 --port 8501 --no-access-log &
studio_pid=$!
caddy run --config /etc/caddy/Caddyfile --adapter caddyfile &
proxy_pid=$!
trap 'kill "$studio_pid" "$proxy_pid" 2>/dev/null || true' EXIT
trap 'exit 143' TERM
trap 'exit 130' INT
wait -n "$studio_pid" "$proxy_pid"
