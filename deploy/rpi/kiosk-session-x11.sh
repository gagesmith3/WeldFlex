#!/bin/bash
# WeldFlex kiosk X11 session — launched by startx.
#
# FALLBACK STACK. The default is now Wayland/cage (kiosk-session-cage.sh); this
# is kept because it is the configuration proven working on this hardware,
# including the ft5x06 touchscreen. Install it with:
#
#   sudo bash deploy/rpi/install_rpi_kiosk.sh --x11
#
# Nothing here restarts the X session itself — only Chromium is supervised. That
# asymmetry is one of the reasons the cage stack is preferred.

# Send stdout/stderr to journald (`journalctl -t weldflex-kiosk -f`), matching
# the cage session. Only stdout/stderr are touched; X still owns the VT.
exec > >(logger -t weldflex-kiosk) 2>&1

# Prevent screen blanking and DPMS power-off
xset s off
xset s noblank
xset -dpms

# Hide the mouse cursor immediately (unclutter with 0s idle threshold)
unclutter -idle 0 -root &

# Float the ft5x06 touchscreen off the master pointer so touch never shows a cursor
TOUCH_ID=$(xinput list --id-only "10-0038 generic ft5x06 (00)" 2>/dev/null)
[ -n "$TOUCH_ID" ] && xinput float "$TOUCH_ID"

# matchbox-window-manager must run before Chromium for correct fullscreen positioning
matchbox-window-manager -use_titlebar no &

# Wait until Flask backend is accepting connections
until curl -sf http://localhost:5000/ >/dev/null 2>&1; do
    sleep 1
done

# Loop so the kiosk restarts if Chromium ever exits
while true; do
    chromium \
        --kiosk \
        --noerrdialogs \
        --disable-infobars \
        --no-first-run \
        --touch-events=enabled \
        --no-sandbox \
        --disable-dev-shm-usage \
        --user-data-dir=/tmp/weldflex-kiosk \
        --disable-features=TranslateUI \
        "http://localhost:5000/operator"
    sleep 2
done
