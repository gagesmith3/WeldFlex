#!/bin/bash
set -e

# ── WeldFlex RPi kiosk installer ──────────────────────────────────────────────
# Run as root from the project root:
#
#   sudo bash deploy/rpi/install_rpi_kiosk.sh          # Wayland (cage) — default
#   sudo bash deploy/rpi/install_rpi_kiosk.sh --x11    # X11 fallback stack
#
# Targets Raspberry Pi OS **Lite** (no desktop, no compositor, no display
# manager preinstalled). It also runs on the Desktop image, where the extra
# display-manager teardown below actually does something.
#
# Re-running is safe: every step is idempotent, so iterate with this rather than
# hand-editing the installed copies.

if [ "$(id -u)" -ne 0 ]; then
    echo "Run with sudo." >&2
    exit 1
fi

STACK="cage"
for arg in "$@"; do
    case "$arg" in
        --x11)  STACK="x11" ;;
        --cage) STACK="cage" ;;
        *) echo "Unknown option: $arg (expected --cage or --x11)" >&2; exit 1 ;;
    esac
done

PROJECT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
KIOSK_USER="${SUDO_USER:-pi}"
DEPLOY_DIR="$PROJECT_DIR/deploy/rpi"

echo "==> Project root : $PROJECT_DIR"
echo "==> Kiosk user   : $KIOSK_USER"
echo "==> Session stack: $STACK"
echo "==> OS           : $(. /etc/os-release && echo "$PRETTY_NAME")"
echo ""

# ── 1. Display server ─────────────────────────────────────────────────────────
# Only the X11 stack needs a forced switch, and only on images that ship a
# desktop session to switch away from. On Lite there is no compositor installed
# at all, so raspi-config may not offer the toggle — not an error, skip it.
if [ "$STACK" = "x11" ]; then
    echo "==> Forcing X11 display server..."
    if command -v raspi-config >/dev/null 2>&1; then
        raspi-config nonint do_wayland W1 || echo "  (toggle unavailable — expected on Lite)"
    else
        echo "  raspi-config not found — skipping (expected on Lite)."
    fi
else
    echo "==> Wayland stack selected — no display-server switch needed."
fi

# ── 2. System packages ────────────────────────────────────────────────────────
echo "==> Installing system packages..."
apt-get update -qq

COMMON_PKGS="chromium curl python3 python3-pip python3-venv"
if [ "$STACK" = "cage" ]; then
    # seatd is a fallback seat provider; logind normally handles this, but having
    # it installed costs nothing and unblocks cage if libseat cannot reach logind.
    STACK_PKGS="cage seatd"
else
    STACK_PKGS="xserver-xorg xinit matchbox-window-manager xinput x11-xserver-utils unclutter"
fi

# shellcheck disable=SC2086
apt-get install -y $COMMON_PKGS $STACK_PKGS

# ── 3. Python venv + deps ─────────────────────────────────────────────────────
echo "==> Setting up Python venv..."
VENV="$PROJECT_DIR/venv"
python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -r "$PROJECT_DIR/requirements.txt"
chown -R "$KIOSK_USER:$KIOSK_USER" "$VENV"

# ── 4. .env check ─────────────────────────────────────────────────────────────
if [ ! -f "$PROJECT_DIR/.env" ]; then
    echo ""
    echo "WARNING: No .env found at $PROJECT_DIR/.env"
    echo "  Copy deploy/rpi/.env.rpi.example to .env and fill in your robot IP."
    echo ""
fi

# ── 5. Session scripts + device access ────────────────────────────────────────
echo "==> Setting permissions..."
chmod +x "$DEPLOY_DIR/kiosk-session-cage.sh" "$DEPLOY_DIR/kiosk-session-x11.sh"
chown "$KIOSK_USER:$KIOSK_USER" \
    "$DEPLOY_DIR/kiosk-session-cage.sh" "$DEPLOY_DIR/kiosk-session-x11.sh"

# wlroots (which cage is built on) wants direct DRM/input access. logind normally
# grants this via the seat, but the group membership is the standard belt-and-
# braces and is harmless on the X11 stack too.
for grp in video input render; do
    if getent group "$grp" >/dev/null 2>&1; then
        usermod -aG "$grp" "$KIOSK_USER"
    fi
done

# ── 6. Systemd backend service ────────────────────────────────────────────────
echo "==> Installing weldflex-backend.service..."
sed \
    -e "s|/home/pi/WeldFlex|$PROJECT_DIR|g" \
    -e "s|User=pi|User=$KIOSK_USER|g" \
    "$DEPLOY_DIR/weldflex-backend.service" \
    > /etc/systemd/system/weldflex-backend.service

systemctl daemon-reload
systemctl enable weldflex-backend.service

# ── 7. Autologin: getty → session script ──────────────────────────────────────
# getty autologin gives the kiosk user a real logind session on seat0, which is
# what lets the compositor open DRM and input devices. A systemd *user* unit
# would supervise better, but user units live outside the login session scope and
# seat access there is not guaranteed — so the supervising loop lives inside the
# session script instead. See the LightDM rationale in the deploy docs.
echo "==> Configuring autologin (getty + session script)..."

# Not installed on Lite; only meaningful if this is run on the Desktop image.
systemctl disable lightdm 2>/dev/null || true
systemctl disable gdm3   2>/dev/null || true

mkdir -p /etc/systemd/system/getty@tty1.service.d/
cat > /etc/systemd/system/getty@tty1.service.d/autologin.conf << EOF
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin $KIOSK_USER --noclear %I \$TERM
EOF
systemctl daemon-reload

if [ "$STACK" = "cage" ]; then
    cat > "/home/$KIOSK_USER/.bash_profile" << EOF
# WeldFlex kiosk — start the Wayland (cage) session automatically on TTY1
if [[ -z "\$WAYLAND_DISPLAY" && -z "\$DISPLAY" && "\$XDG_VTNR" == "1" ]]; then
    exec $PROJECT_DIR/deploy/rpi/kiosk-session-cage.sh
fi
EOF
else
    cat > "/home/$KIOSK_USER/.bash_profile" << EOF
# WeldFlex kiosk — start X automatically on TTY1
if [[ -z "\$DISPLAY" && "\$XDG_VTNR" == "1" ]]; then
    exec startx $PROJECT_DIR/deploy/rpi/kiosk-session-x11.sh
fi
EOF
fi
chown "$KIOSK_USER:$KIOSK_USER" "/home/$KIOSK_USER/.bash_profile"

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "Install complete ($STACK stack). Next steps:"
echo "  1. Make sure .env exists at $PROJECT_DIR/.env"
echo "  2. Make sure the FAIRINO linux SDK is at $PROJECT_DIR/fairino-python-sdk-main/linux/"
echo "  3. Make sure the RPi has an IP on the 192.168.58.x subnet (robot's network)"
echo "  4. sudo reboot"
echo ""
echo "After boot, to watch the two halves independently:"
echo "  journalctl -u weldflex-backend -f     # Flask + SDK"
echo "  journalctl -t weldflex-kiosk -f       # compositor + Chromium"
echo ""
if [ "$STACK" = "cage" ]; then
    echo "If the touchscreen misbehaves under Wayland, fall back with:"
    echo "  sudo bash deploy/rpi/install_rpi_kiosk.sh --x11 && sudo reboot"
    echo ""
fi
