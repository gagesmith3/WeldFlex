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

# The compositor's cursor is deliberately NOT handled here — see the diversion
# step in install_rpi_kiosk.sh.
#
# cage parks a default cursor in the centre of the output and never removes it:
# the panel's touchscreen produces no pointer-motion event, so nothing ever hands
# the cursor to the client, and the app's kiosk `cursor: none` CSS only governs
# the pointer once Chromium owns it. cage 0.2.0 has no option for this at all —
# `cage --help` lists only -d/-D/-h/-m/-s/-v.
#
# This block used to generate a fully-transparent Xcursor theme and point
# XCURSOR_PATH/XCURSOR_THEME at it. That never worked, and proving it took a
# while: on 2026-08-10 the theme was pointed at a fully *opaque red* cursor and
# the panel still showed a normal arrow, so wlroots was never loading the theme
# in the first place. Do not reintroduce it — it reads like a working fix.
#
# What does work is removing the image wlroots resolves to. Mind the name:
# wlroots asks for the XDG cursor name `default`, and `left_ptr` is merely a
# symlink pointing at it, which is why the "rename left_ptr" recipes on the Pi
# forums have no effect here.

# Send stdout/stderr to journald so `journalctl -t weldflex-kiosk -f` works, and
# tee it to the console so a startup that never completes says so on the panel.
# Routing to logger alone meant every pre-cage failure looked identical from the
# front of the machine: a bare blinking cursor. Console output stops mattering the
# moment cage takes over the display, which is exactly when we no longer need it.
# Only stdout/stderr are redirected — cage still owns the VT and opens its own
# DRM/input devices, so this does not affect the session.
exec > >(tee >(logger -t weldflex-kiosk)) 2>&1

echo "kiosk session starting (cage/wayland)"

# systemd starts weldflex-backend.service and this session independently, with no
# ordering between them, so wait for Flask to actually accept connections. An
# After= dependency would not help: it orders start, it does not wait for a
# listening socket.
#
# The wait is unbounded on purpose — a slow boot should still end up at the app —
# but it reports itself every 5s. A silent wait here is indistinguishable from a
# hung session, and the backend failing to start is the most likely reason this
# script ever stalls.
waited=0
until curl -sf "$READY_URL" >/dev/null 2>&1; do
    sleep 1
    waited=$((waited + 1))
    if [ $((waited % 5)) -eq 0 ]; then
        echo "still waiting for backend at $READY_URL (${waited}s) — check: systemctl status weldflex-backend"
    fi
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
