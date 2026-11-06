#!/usr/bin/env bash
# Linux runner: pip install -r requirements.txt, then ./run.sh <SIM_URL>.
set -euo pipefail
SIM_URL="${1:?usage: ./run.sh <SIM_URL>}"
cd "$(dirname "$0")"
exec "${PYTHON_BIN:-python3}" main.py "$SIM_URL"
