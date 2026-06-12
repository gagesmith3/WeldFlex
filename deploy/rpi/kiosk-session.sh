#!/bin/bash
# WeldFlex kiosk X11 session — launched by LightDM

# Float the ft5x06 touchscreen off the master pointer so touch never shows a cursor
# (unclutter/xsetroot are not sufficient — xinput float is the correct fix)
TOUCH_ID=$(xinput list --id-only "10-0038 generic ft5x06 (00)" 2>/dev/null)
[ -n "$TOUCH_ID" ] && xinput float "$TOUCH_ID"

# matchbox-window-manager must run before Chromium for correct fullscreen positioning
matchbox-window-manager -use_titlebar no &

# Wait until Flask backend is accepting connections
until curl -sf http://localhost:5000/ >/dev/null 2>&1; do
    sleep 1
done

# Launch Chromium in kiosk mode
exec chromium-browser \
    --kiosk \
    --noerrdialogs \
    --disable-infobars \
    --no-first-run \
    --touch-events=enabled \
    --password-store=basic \
    --disable-features=TranslateUI \
    "http://localhost:5000/operator"
