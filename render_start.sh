#!/usr/bin/env bash
set -euo pipefail
if [[ ! "${SAFELEDGER_SIGNING_KEY:-}" =~ ^[0-9a-f]{64}$ ]]; then
  echo "A stable 64-character lowercase hex signing key is required." >&2
  exit 1
fi
exec python -m uvicorn api.app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
