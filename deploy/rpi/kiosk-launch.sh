#!/usr/bin/env bash
set -euo pipefail

URL="${WELDFLEX_KIOSK_URL:-http://127.0.0.1:5000}"
BROWSER_CMD="${WELDFLEX_BROWSER_CMD:-chromium-browser}"

# Prevent display power-down/blanking.
xset s off
xset -dpms
xset s noblank

# Hide mouse cursor if available.
if command -v unclutter >/dev/null 2>&1; then
  unclutter -idle 0.1 -root &
fi

# Start on-screen keyboard (auto-shows when a text input gains focus).
if command -v onboard >/dev/null 2>&1; then
  onboard --layout=Phone --size=800x160 &
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
    --start-fullscreen \
    --incognito \
    --noerrdialogs \
    --disable-infobars \
    --check-for-update-interval=31536000 \
    "$URL"

  # Restart browser if it exits unexpectedly.
  sleep 1
done
