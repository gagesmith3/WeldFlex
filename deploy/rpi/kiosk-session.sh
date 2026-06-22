#!/bin/bash
# WeldFlex kiosk X11 session — launched by startx

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
