#!/bin/zsh
set -eu
cd "$(dirname "$0")/.."
export OPENBLAS_NUM_THREADS=1
export OMP_NUM_THREADS=1
export PYTHONDONTWRITEBYTECODE=1
if [[ -x .venv/bin/python ]]; then
  GENGUARD_PYTHON=.venv/bin/python
elif [[ -x "$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3" ]]; then
  GENGUARD_PYTHON="$HOME/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/bin/python3"
else
  GENGUARD_PYTHON=python3
fi
"$GENGUARD_PYTHON" -m adapter.usb_connect --installation phone-fixed-20261011 --api-url http://127.0.0.1:9883
