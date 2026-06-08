#!/usr/bin/env bash
set -euo pipefail

URL="${WELDFLEX_KIOSK_URL:-http://127.0.0.1:5000/operator}"

# Resolve browser binary — explicit override wins, otherwise auto-detect.
if [[ -n "${WELDFLEX_BROWSER_CMD:-}" ]]; then
  BROWSER_CMD="$WELDFLEX_BROWSER_CMD"
elif command -v chromium >/dev/null 2>&1; then
  BROWSER_CMD="chromium"
elif command -v chromium-browser >/dev/null 2>&1; then
  BROWSER_CMD="chromium-browser"
else
  echo "No Chromium browser found. Install chromium or chromium-browser." >&2
  exit 1
fi

# Paint root window dark immediately so the display stays #1c1c1e while
# the backend and browser start up.
xsetroot -solid "#1c1c1e"

# Prevent display power-down/blanking.
xset s off
xset -dpms
xset s noblank

# Minimal window manager — no decorations, handles Chromium's fullscreen
# request so the window fills the screen exactly.
matchbox-window-manager -use_titlebar no &

# Detach the touchscreen from the master pointer so taps never move the X11
# cursor. Chromium still receives touch events directly via XInput2.
_tid=$(xinput list | grep -i 'ft5x06' | grep -oP 'id=\d+' | grep -oP '\d+' | head -1)
if [[ -n "$_tid" ]]; then
  xinput float "$_tid" 2>/dev/null || true
fi

# Wait for the Flask backend before launching Chromium so the browser never
# shows an "unable to connect" error page on startup.
echo "Waiting for backend at $URL ..."
until curl -sf --max-time 2 "$URL" > /dev/null 2>&1; do
  sleep 0.5
done
echo "Backend ready."

while true; do
  "$BROWSER_CMD" \
    --kiosk \
    --touch-events=enabled \
    --incognito \
    --noerrdialogs \
    --disable-infobars \
    --password-store=basic \
    --check-for-update-interval=31536000 \
    "$URL"

  # Restart browser if it exits unexpectedly.
  sleep 1
done
