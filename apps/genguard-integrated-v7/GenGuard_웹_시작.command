#!/bin/zsh
set -eu
cd "$(dirname "$0")"
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
print 'GenGuard 웹: http://127.0.0.1:9883'
print '다음으로 adapter/USB_IMU_연결.command를 실행하세요.'
"$GENGUARD_PYTHON" server.py --port 9883
