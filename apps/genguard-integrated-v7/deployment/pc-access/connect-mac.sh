#!/bin/bash
# Screen connection only: never deploy, reset, retrain or start a second board server.
set -u
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
SERIAL=233936836
ADB=${GENGUARD_ADB:-"$ROOT/platform-tools/adb"}
if [ ! -x "$ADB" ]; then ADB=$(command -v adb || true); fi
if [ -z "$ADB" ] || [ ! -x "$ADB" ]; then
  echo 'ADB가 없습니다. 팀 공유 ZIP 전체를 압축 해제하거나 공식 Platform Tools를 설치하세요.'
  exit 1
fi
ONCE=0
if [ "${1:-}" = '--once' ]; then ONCE=1; fi
PORT=''
LAST=''
OPENED=''
say() { if [ "$LAST" != "$1" ]; then echo "$1"; LAST="$1"; fi; }
trap 'echo; echo "접속 도구를 종료합니다. UNO Q의 분석과 기록은 계속됩니다."; exit 0' INT TERM
select_port() {
  local existing candidate
  existing=$("$ADB" forward --list 2>/dev/null | awk -v s="$SERIAL" '$1==s && $3=="tcp:9883" && $2 ~ /^tcp:988[3-9]$/ {sub("tcp:","",$2); print $2; exit}')
  if [ -n "$existing" ]; then PORT=$existing; return 0; fi
  for candidate in 9883 9884 9885 9886 9887 9888 9889; do
    if "$ADB" -s "$SERIAL" forward --no-rebind "tcp:$candidate" tcp:9883 >/dev/null 2>&1; then
      PORT=$candidate; return 0
    fi
  done
  return 1
}
echo 'GenGuard · UNO Q USB 접속'
echo '다른 PC로 옮겨 전원이 꺼져도 괜찮습니다. 부팅과 분석 준비가 끝날 때까지 기다립니다.'
echo '이 창을 열어두면 USB 재연결도 자동 복구합니다. 종료: Control+C'
while true; do
  STATUS=$("$ADB" -s "$SERIAL" get-state 2>/dev/null || true)
  if [ "$STATUS" != device ]; then
    PORT=''; say 'UNO Q 연결 대기 · 데이터 통신이 가능한 USB 케이블로 보드를 연결하세요.'
  elif select_port; then
    HEALTH=$(curl --silent --max-time 3 "http://127.0.0.1:$PORT/api/health" 2>/dev/null || true)
    if printf '%s' "$HEALTH" | grep -Eq '"ready"[[:space:]]*:[[:space:]]*true' && printf '%s' "$HEALTH" | grep -Eq '"host"[[:space:]]*:[[:space:]]*"uno_q"'; then
      say "연결 완료: http://127.0.0.1:$PORT/ · 분석과 데이터는 UNO Q 내부에 있습니다."
      if [ "$OPENED" != "$PORT" ] && [ "${GENGUARD_NO_OPEN:-0}" != 1 ]; then open "http://127.0.0.1:$PORT/"; OPENED=$PORT; fi
      if [ "$ONCE" -eq 1 ]; then exit 0; fi
    else
      say '보드 부팅·분석 서버 준비 대기 · 보통 약 1분 정도 기다려 주세요. 모델을 다시 학습하지 않습니다.'
    fi
  else
    say '접속 포트가 사용 중입니다. 다른 서버를 종료하지 않고 빈 포트를 기다립니다.'
  fi
  if [ "$ONCE" -eq 1 ]; then exit 2; fi
  sleep 3
done
