#!/usr/bin/env bash
# Mac -> USB-connected UNO Q: stage numpy + the clika_runtime wheel for the board
# on the Mac, push them to ~/clikart/site and run verify_clikart.py with PYTHONPATH.
# The stock UNO Q Debian has no pip/venv and is externally managed, so nothing is
# installed system-wide: no sudo, apt, firmware or OS changes. The board needs no
# internet. The license credential is never handled here; see README.md.
set -euo pipefail

usage() {
  echo "usage: $0 <clika_runtime wheel .whl or the platform's .zip> [--sha256 HEX] [--serial SERIAL]" >&2
  exit 2
}

WHEEL_INPUT="${1:-}"; [ -n "$WHEEL_INPUT" ] || usage; shift
SHA256=""; SERIAL=""
while [ $# -gt 0 ]; do
  case "$1" in
    --sha256) SHA256="$2"; shift 2 ;;
    --serial) SERIAL="$2"; shift 2 ;;
    *) usage ;;
  esac
done
[ -f "$WHEEL_INPUT" ] || { echo "파일이 없습니다: $WHEEL_INPUT" >&2; exit 1; }
HERE="$(cd "$(dirname "$0")" && pwd)"

# 1. adb: the one Arduino's tools already installed, never a new download.
ADB="${ADB_BIN:-$(command -v adb || true)}"
if [ -z "$ADB" ]; then
  for c in ~/Library/Android/sdk/platform-tools/adb ~/Library/Arduino15/packages/arduino/tools/adb/*/adb; do
    [ -x "$c" ] && ADB="$c" && break
  done
fi
[ -n "$ADB" ] || { echo "adb를 찾지 못했습니다. ADB_BIN=/path/to/adb 로 지정하세요." >&2; exit 1; }

DEVICES=$("$ADB" devices | awk 'NR>1 && $2=="device"{print $1}')
[ -z "$SERIAL" ] || DEVICES=$(printf '%s\n' "$DEVICES" | grep -x "$SERIAL" || true)
COUNT=$(printf '%s\n' "$DEVICES" | grep -c . || true)
[ "$COUNT" = 1 ] || { echo "연결된 adb 장치가 ${COUNT}개입니다. USB 연결/승인을 확인하고, 여러 개면 --serial로 지정하세요." >&2; exit 1; }
SERIAL="$DEVICES"
board() { "$ADB" -s "$SERIAL" shell "$@"; }

# 2. Board facts that decide which wheel fits, and its clock (an Offline license
#    is checked against it; the UNO Q has no battery-backed clock).
ARCH=$(board uname -m | tr -d '\r')
PYVER=$(board 'python3 -c "import sys;print(\"%d.%d\"%sys.version_info[:2])"' | tr -d '\r')
PYTAG="cp${PYVER/./}"
RHOME=$(board 'echo $HOME' | tr -d '\r')
echo "보드: $SERIAL  arch=$ARCH  python=$PYVER  home=$RHOME"
[ "$ARCH" = aarch64 ] || { echo "aarch64 보드가 아닙니다. 다른 장치를 고르지 않았습니다." >&2; exit 1; }
SKEW=$(( $(date +%s) - $(board 'date +%s' | tr -d '\r') ))
if [ "${SKEW#-}" -gt 600 ]; then
  echo "경고: 보드 시계가 Mac과 ${SKEW}초 다릅니다. Offline 라이선스 검증이 실패할 수 있습니다 (README.md 3단계)." >&2
fi

# 3. The wheel: verify the download, then match it to the board.
if [ -n "$SHA256" ]; then
  echo "$SHA256  $WHEEL_INPUT" | shasum -a 256 -c -
fi
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
case "$WHEEL_INPUT" in
  *.zip) unzip -q "$WHEEL_INPUT" -d "$TMP/zip"; WHEEL=$(find "$TMP/zip" -name 'clika_runtime-*.whl' | head -1) ;;
  *.whl) WHEEL="$WHEEL_INPUT" ;;
  *) usage ;;
esac
[ -n "${WHEEL:-}" ] || { echo "zip 안에서 clika_runtime 휠을 찾지 못했습니다." >&2; exit 1; }
NAME=$(basename "$WHEEL")
case "$NAME" in *"+cu"*) echo "CUDA 포함 휠($NAME)입니다. UNO Q에는 CUDA가 없으니 비CUDA 0.6.x 휠을 받으세요." >&2; exit 1 ;; esac
case "$NAME" in
  *"-$PYTAG-$PYTAG-"*aarch64*) ;;
  *) echo "휠($NAME)이 보드($PYTAG, aarch64)와 맞지 않습니다. 플랫폼에서 Linux arm64, CPython $PYVER 용을 받으세요." >&2; exit 1 ;;
esac

# 4. Stage on the Mac with a real pip (the wheel is LZMA-compressed; uv cannot read it).
HOSTPY="${HOST_PYTHON:-}"
if [ -z "$HOSTPY" ]; then
  for c in "$(command -v python3.13 || true)" ~/.pyenv/versions/3.1[0-9]*/bin/python3 /opt/homebrew/bin/python3 "$(command -v python3 || true)"; do
    [ -n "$c" ] && [ -x "$c" ] && "$c" -c 'import sys,lzma,venv; assert sys.version_info >= (3,10)' 2>/dev/null && HOSTPY="$c" && break
  done
fi
[ -n "$HOSTPY" ] || { echo "Mac에 lzma가 되는 Python 3.10+ 이 필요합니다. HOST_PYTHON=/path/to/python3 로 지정하세요." >&2; exit 1; }
"$HOSTPY" -m venv "$TMP/pip"
export PIP_DISABLE_PIP_VERSION_CHECK=1
TARGET=(--only-binary=:all: --platform manylinux_2_28_aarch64 --platform manylinux2014_aarch64
        --python-version "$PYVER" --implementation cp --abi "$PYTAG")
"$TMP/pip/bin/pip" -q download numpy "${TARGET[@]}" -d "$TMP/wheels"
"$TMP/pip/bin/pip" -q install --no-deps "${TARGET[@]}" --target "$TMP/site" "$TMP"/wheels/numpy-*.whl "$WHEEL"

# 5. Push to the board's home directory and make the bundled tools executable.
REMOTE="$RHOME/clikart"
board "mkdir -p '$REMOTE' && rm -rf '$REMOTE/site'"
"$ADB" -s "$SERIAL" push "$TMP/site" "$REMOTE/" | tail -1
"$ADB" -s "$SERIAL" push "$HERE/verify_clikart.py" "$REMOTE/verify_clikart.py" >/dev/null
board "chmod 755 '$REMOTE'/site/clika_runtime/bin/*"

# 6. Check. A missing credential shows up as LICENSE_FAILED.
OUT="$HERE/results/$(date +%Y%m%d-%H%M%S)"; mkdir -p "$OUT"
set +e
board "cd '$REMOTE' && PYTHONPATH='$REMOTE/site' python3 verify_clikart.py --output verify.json"
STATUS=$?
set -e
"$ADB" -s "$SERIAL" pull "$REMOTE/verify.json" "$OUT/" >/dev/null || true
echo "결과 사본: $OUT"
if [ "$STATUS" != 0 ]; then
  echo
  echo "검증 실패. LICENSE_FAILED라면 README.md 3단계(시계 맞추기, 라이선스 넣기)를 따르세요."
  exit "$STATUS"
fi
echo "검증 통과."
