#!/bin/sh
set -eu
# This release lives beside the team's original app, gateway and Clika runtime.
BASE=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
APP="$BASE/app"
# Once installed, one service owns gateway/API/receiver together. Do not spawn
# a second manual API alongside it, even when that service is currently stopped.
if [ -f "$HOME/.config/systemd/user/genguard.service" ]; then
    if ! grep -Fqx "WorkingDirectory=$APP" "$HOME/.config/systemd/user/genguard.service"; then
        echo 'Existing genguard.service belongs to another installation; no process was started.' >&2
        exit 1
    fi
    export XDG_RUNTIME_DIR="/run/user/$(id -u)"
    export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
    exec systemctl --user start genguard.service
fi
export PYTHONPATH=/home/arduino/clikart/site
export PATH="$BASE/runtime/node-v22.23.3-linux-arm64/bin:$PATH"
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 PYTHONDONTWRITEBYTECODE=1
mkdir -p "$BASE/logs"
for name in server sensor; do
    if [ -f "$BASE/logs/$name.pid" ] && kill -0 "$(cat "$BASE/logs/$name.pid")" 2>/dev/null; then
        echo "GenGuard $name is already running; keep the existing process." >&2
        exit 1
    fi
done
cd "$APP"
nohup python3 server.py --host 127.0.0.1 --port 9883 \
    --state-dir "$BASE/state" --imu-baseline-file "$BASE/config/imu-baseline-transfer.json" \
    > "$BASE/logs/server.log" 2>&1 < /dev/null &
echo "$!" > "$BASE/logs/server.pid"
python3 deployment/configure_linked.py
nohup python3 -m adapter.imu_to_api --installation phone-fixed-20261011 \
    --api-url http://127.0.0.1:9883 --gateway-port 9877 \
    > "$BASE/logs/sensor.log" 2>&1 < /dev/null &
echo "$!" > "$BASE/logs/sensor.pid"
echo 'GenGuard is running on UNO Q at 127.0.0.1:9883.'
