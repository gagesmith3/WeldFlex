#!/usr/bin/env bash
set -euo pipefail

URL="${WELDFLEX_KIOSK_URL:-http://127.0.0.1:5000}"

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

# Hide the cursor via the XFixes protocol so touch taps never show a pointer.
unclutter --timeout 0 &

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
    --incognito \
    --noerrdialogs \
    --disable-infobars \
    --password-store=basic \
    --check-for-update-interval=31536000 \
    "$URL"

  # Restart browser if it exits unexpectedly.
  sleep 1
done
