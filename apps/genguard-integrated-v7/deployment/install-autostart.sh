#!/bin/sh
# Installs only this user's GenGuard unit. Never replaces data, models or firmware.
set -eu
BASE=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
GATEWAY=/home/arduino/t4-6-vibration-lab
START=0
if [ "${1:-}" = --start ]; then START=1; shift; fi
if [ "$#" -ne 0 ]; then echo 'Usage: install-autostart.sh [--start]' >&2; exit 2; fi
if [ "$(id -un)" != arduino ]; then echo 'Run as the existing arduino user, without sudo.' >&2; exit 1; fi
case "$BASE" in *[!a-zA-Z0-9_./-]*) echo 'Installation path contains unsupported systemd characters.' >&2; exit 1;; esac
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
if [ ! -S "$XDG_RUNTIME_DIR/bus" ]; then
    echo 'The user service manager is unavailable. Enable loginctl enable-linger arduino first.' >&2
    exit 1
fi
if [ "$(loginctl show-user arduino -p Linger --value)" != yes ]; then
    echo 'Automatic boot needs loginctl enable-linger arduino first; no unit was installed.' >&2
    exit 1
fi
python3 "$BASE/app/deployment/supervise_on_unoq.py" --base "$BASE" --gateway-root "$GATEWAY" --check
UNIT_DIR="$HOME/.config/systemd/user"
UNIT="$UNIT_DIR/genguard.service"
mkdir -p "$UNIT_DIR"
if [ -L "$UNIT" ]; then echo 'Refusing to replace a symlinked unit.' >&2; exit 1; fi
if [ -f "$UNIT" ] && ! grep -q '^# GenGuard managed user unit v1$' "$UNIT"; then
    echo 'An unrelated genguard.service already exists; keep it and inspect manually.' >&2
    exit 1
fi
if [ -f "$UNIT" ] && ! grep -Fqx "WorkingDirectory=$BASE/app" "$UNIT"; then
    echo 'The managed unit belongs to another installation; it was not replaced.' >&2
    exit 1
fi
TEMP=$(mktemp "$UNIT_DIR/.genguard.XXXXXX")
CHECK_DIR=$(mktemp -d "$UNIT_DIR/.genguard-check.XXXXXX")
trap 'rm -f "$TEMP" "$CHECK_DIR/genguard.service"; rmdir "$CHECK_DIR"' EXIT HUP INT TERM
sed -e "s|@BASE@|$BASE|g" -e "s|@GATEWAY@|$GATEWAY|g" "$BASE/app/deployment/genguard.service.in" > "$TEMP"
chmod 600 "$TEMP"
# systemd validates the actual service syntax before replacing a working unit.
cp "$TEMP" "$CHECK_DIR/genguard.service"
systemd-analyze --user verify "$CHECK_DIR/genguard.service"
if ! cmp -s "$TEMP" "$UNIT"; then
    if [ -f "$UNIT" ]; then cp "$UNIT" "$UNIT.previous"; fi
    mv "$TEMP" "$UNIT"
fi
systemctl --user daemon-reload
systemctl --user enable genguard.service
if [ "$START" -eq 1 ]; then systemctl --user start genguard.service; fi
echo 'GenGuard automatic boot is enabled. Current status:'
systemctl --user is-enabled genguard.service
systemctl --user is-active genguard.service || true
