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

# Paint root window dark immediately so the LXDE desktop never flashes through.
xsetroot -solid "#1c1c1e"

# Prevent display power-down/blanking.
xset s off
xset -dpms
xset s noblank

# Set an invisible 1×1 cursor at the X level so it never appears on touch.
_blank=$(mktemp --suffix=.xbm)
cat > "$_blank" <<'XBM'
#define blank_width 1
#define blank_height 1
static unsigned char blank_bits[] = { 0x00 };
XBM
xsetroot -cursor "$_blank" "$_blank" || true
rm -f "$_blank"

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
