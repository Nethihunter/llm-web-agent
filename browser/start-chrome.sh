#!/bin/bash
set -euo pipefail

export DISPLAY=:99
PROFILE_DIR=/tmp/chrome-profile

# Stale locks survive a container restart and would block Xvfb / Chrome.
rm -f /tmp/.X99-lock /tmp/.X11-unix/X99
rm -f "$PROFILE_DIR"/Singleton* 2>/dev/null || true

# --- Xvfb: virtual display ---
Xvfb :99 -screen 0 1920x1080x24 &
for _ in $(seq 1 50); do
    [ -e /tmp/.X11-unix/X99 ] && break
    sleep 0.1
done

# --- Fluxbox: minimal window manager ---
fluxbox -display :99 &

# --- x11vnc + noVNC (reachable only through the ports published on 127.0.0.1) ---
x11vnc -display :99 -forever -shared -nopw -rfbport 5900 -quiet &
websockify --web=/usr/share/novnc/ 6080 localhost:5900 &

# --- CDP exposure ---
# Chrome ignores --remote-debugging-address: the DevTools port always listens
# on 127.0.0.1 only. From another container (or through Docker's port
# publishing) the connection is refused. So Chrome listens on 9223 (loopback)
# and socat forwards 0.0.0.0:9222 -> 127.0.0.1:9223.
socat TCP-LISTEN:9222,fork,reuseaddr,bind=0.0.0.0 TCP:127.0.0.1:9223 &

# --- Chrome (no sandbox: required inside Docker) ---
exec google-chrome \
    --remote-debugging-port=9223 \
    --no-first-run \
    --no-default-browser-check \
    --no-sandbox \
    --disable-setuid-sandbox \
    --disable-dev-shm-usage \
    --disable-gpu \
    --disable-software-rasterizer \
    --user-data-dir="$PROFILE_DIR" \
    --window-size=1920,1080 \
    --start-maximized
