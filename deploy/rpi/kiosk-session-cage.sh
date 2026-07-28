#!/bin/bash
# WeldFlex kiosk — Wayland session (cage). Default stack as of the Lite rebuild.
#
# cage is a single-purpose Wayland compositor: it runs exactly one application
# fullscreen and exits when that application exits. That removes four X11-isms
# that the old session script needed:
#
#   matchbox-window-manager   cage owns fullscreen; no WM to start first, so the
#                             "must launch before Chromium" ordering trap is gone.
#   xinput float <touch-id>   Wayland routes touch and pointer as separate
#                             protocols, so touch never drags a cursor around.
#                             The hack existed only because X11 funnels touch
#                             through the master pointer.
#   unclutter                 No pointer device means no cursor to hide.
#   xset s off / -dpms        No X server to blank.
#
# The X11 stack is kept at kiosk-session-x11.sh. It is known-good on this
# hardware; switch back by re-running the installer with --x11 if the
# touchscreen misbehaves here.

set -u

URL="http://localhost:5000/operator"
READY_URL="http://localhost:5000/"

# Shrink the compositor's cursor to nothing. The app already sets `cursor: none`
# in kiosk mode, but that only governs the pointer once Chromium owns it — wlroots
# parks its own default cursor at the centre of the output at startup and keeps
# drawing it until a pointer motion event hands control to the client. With no
# mouse attached that motion never comes, so the arrow just sits there. cage has no
# hide-cursor flag; XCURSOR_SIZE is the lever wlroots actually reads.
export XCURSOR_SIZE=1

# Send stdout/stderr to journald so `journalctl -t weldflex-kiosk -f` works. The
# old session logged nowhere. Only stdout/stderr are redirected — cage still owns
# the VT and opens its own DRM/input devices, so this does not affect the session.
exec > >(logger -t weldflex-kiosk) 2>&1

echo "kiosk session starting (cage/wayland)"

# systemd starts weldflex-backend.service and this session independently, with no
# ordering between them, so wait for Flask to actually accept connections. An
# After= dependency would not help: it orders start, it does not wait for a
# listening socket.
until curl -sf "$READY_URL" >/dev/null 2>&1; do
    sleep 1
done
echo "backend is up, launching cage"

# Supervising loop. cage exits when its child exits, so one mechanism covers both
# a Chromium crash and a compositor crash. The old script needed this loop around
# Chromium only, and nothing restarted the X session itself.
while true; do
    # Flags to verify against `cage --help` on the target image before adding any
    # more — cage's option set is small and version-dependent. Bare `cage -- CMD`
    # is the stable, documented invocation.
    cage -- chromium \
        --kiosk \
        --ozone-platform=wayland \
        --noerrdialogs \
        --disable-infobars \
        --no-first-run \
        --touch-events=enabled \
        --no-sandbox \
        --disable-dev-shm-usage \
        --user-data-dir=/tmp/weldflex-kiosk \
        --disable-features=TranslateUI \
        --disable-background-networking \
        "$URL"
    echo "cage exited (status $?) — restarting in 2s"
    sleep 2
done
