#!/usr/bin/env bash
# X session entry-point for WeldFlex kiosk.
#
# LightDM executes this script as the *entire* X session — no LXDE, no
# desktop environment, nothing between power-on and Chromium.  The root
# window is painted dark immediately so the display stays #1c1c1e while
# the backend and browser start up.
#
# When this script exits (e.g. on error), LightDM tears down the X server
# and, with autologin configured, immediately starts a new session.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Paint the root window dark the instant the X server hands us a display.
# Use || true so a missing xsetroot doesn't abort the session.
xsetroot -solid "#1c1c1e" || true

# Hand off to the kiosk launcher.  It handles screen settings, cursor
# hiding, on-screen keyboard, backend wait, and Chromium restart loop.
exec "$SCRIPT_DIR/kiosk-launch.sh"
