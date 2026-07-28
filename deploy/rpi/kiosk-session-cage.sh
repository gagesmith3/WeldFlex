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

# Hide the compositor's cursor by giving it a fully transparent cursor theme.
#
# The app already sets `cursor: none` in kiosk mode, but that only governs the
# pointer once Chromium owns it. wlroots parks its own default cursor at the
# centre of the output and keeps drawing it until a pointer motion event hands
# control to the client — and the panel's touchscreen enumerates as a pointer
# without ever producing motion, so the arrow just sits there. XCURSOR_SIZE=1
# was tried first and changed nothing (cage passes its own size when it builds
# the cursor manager), which leaves the cursor *image* as the only lever.
#
# Built here rather than in the installer so a `git pull` + reboot is enough.
CURSOR_THEME_DIR="$HOME/.local/share/icons/default"
if [ ! -f "$CURSOR_THEME_DIR/cursors/left_ptr" ]; then
    echo "generating blank cursor theme at $CURSOR_THEME_DIR"
    mkdir -p "$CURSOR_THEME_DIR/cursors"
    printf '[Icon Theme]\nName=default\n' > "$CURSOR_THEME_DIR/index.theme"
    # One 24x24 fully-transparent frame in Xcursor's binary format: a 16-byte
    # file header, a 12-byte table-of-contents entry, then a 36-byte image
    # chunk header and the pixels. Written directly because xcursorgen lives in
    # x11-apps, which is a silly thing to drag onto a Wayland-only image.
    python3 - "$CURSOR_THEME_DIR/cursors/left_ptr" << 'PY'
import struct, sys
W = H = 24
with open(sys.argv[1], "wb") as f:
    f.write(b"Xcur")
    f.write(struct.pack("<III", 16, 0x00010000, 1))       # header len, version, 1 TOC entry
    f.write(struct.pack("<III", 0xfffd0002, W, 28))       # image chunk, nominal size, offset
    f.write(struct.pack("<IIII", 36, 0xfffd0002, W, 1))   # chunk header len, type, subtype, version
    f.write(struct.pack("<IIIII", W, H, 0, 0, 0))         # w, h, xhot, yhot, delay
    f.write(b"\x00" * (W * H * 4))                        # transparent ARGB pixels
PY
    # Every name a client might ask for has to resolve inside this theme. Any
    # miss and wlroots falls back to the system theme, and the arrow is back.
    for name in default arrow top_left_arrow pointer hand1 hand2 text xterm \
                watch wait progress left_ptr_watch; do
        ln -sf left_ptr "$CURSOR_THEME_DIR/cursors/$name"
    done
fi
# Ours has to come before /usr/share/icons, where Debian ships a "default"
# theme of its own that inherits a perfectly visible arrow.
export XCURSOR_PATH="$HOME/.local/share/icons:$HOME/.icons:/usr/share/icons"
export XCURSOR_THEME=default

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
