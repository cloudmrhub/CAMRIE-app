#!/usr/bin/env bash
#
# Deploy & register CAMRIE Mode 2 (user-owned compute) - Linux / macOS / WSL.
#
# Thin wrapper around worker/manage.py: deploys the worker stack into YOUR AWS
# account and registers it with CloudMR Brain so it shows up in the CAMRIE
# frontend's computing-unit list.
#
# Usage:
#   ./scripts/deploy-and-register-mode2.sh --profile my-aws-profile --alias "My Lab Worker"
#   ./scripts/deploy-and-register-mode2.sh --email you@example.com --profile my-aws-profile
#
#   # non-interactive (CI):
#   export CLOUDMR_EMAIL="you@example.com" CLOUDMR_PASSWORD='...' AWS_PROFILE=my-aws-profile
#   ./scripts/deploy-and-register-mode2.sh
#
# Any other manage.py flag is passed through (e.g. --region us-east-1).
# Password: omit --password to get a hidden prompt (recommended).
#
# Manage afterwards:  python worker/manage.py {status|logs|costs|teardown} --profile <p>
# GUI:                python worker/manage.py

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MANAGE="$REPO_ROOT/worker/manage.py"

PY="${PYTHON:-}"
if [[ -z "$PY" ]]; then
    for c in python3 python; do
        if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
    done
fi
if [[ -z "$PY" ]]; then
    echo "[ERROR] Python 3.9+ not found. Install it or set PYTHON=/path/to/python." >&2
    exit 1
fi

if ! "$PY" -c "import boto3, requests" >/dev/null 2>&1; then
    echo "[INFO] Installing worker manager dependencies (boto3, requests)..."
    "$PY" -m pip install --quiet -r "$REPO_ROOT/worker/requirements.txt"
fi

ARGS=("$@")
# Fall back to AWS_PROFILE if --profile was not given.
if [[ -n "${AWS_PROFILE:-}" ]] && [[ " $* " != *" --profile "* ]] && [[ " $* " != *" -p "* ]]; then
    ARGS+=(--profile "$AWS_PROFILE")
fi

exec "$PY" -u "$MANAGE" deploy "${ARGS[@]}"
