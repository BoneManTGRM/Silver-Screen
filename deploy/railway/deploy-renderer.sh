#!/usr/bin/env bash
set -euo pipefail
: "${MODAL_TOKEN_ID:?Set the Modal token ID in Railway Variables}"
: "${MODAL_TOKEN_SECRET:?Set the Modal token secret in Railway Variables}"
studio_password=${SILVER_SCREEN_PASSWORD:-}
if [[ -z ${SILVER_SCREEN_PASSWORD_HASH:-} ]] && (( ${#studio_password} < 12 )); then
    echo "Set a private studio password with at least 12 characters." >&2
    exit 1
fi
exec modal deploy deploy/modal/worker.py
