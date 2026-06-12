#!/bin/bash
set -e

# ── WeldFlex RPi kiosk installer ──────────────────────────────────────────────
# Run as root from the project root:  sudo bash deploy/rpi/install_rpi_kiosk.sh

if [ "$(id -u)" -ne 0 ]; then
    echo "Run with sudo." >&2
    exit 1
fi

PROJECT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
KIOSK_USER="${SUDO_USER:-pi}"
DEPLOY_DIR="$PROJECT_DIR/deploy/rpi"

echo "==> Project root : $PROJECT_DIR"
echo "==> Kiosk user   : $KIOSK_USER"
echo ""

# ── 1. Force X11 ──────────────────────────────────────────────────────────────
# RPi OS Bookworm defaults to Wayland. Our kiosk uses X11 (matchbox, xinput,
# chromium --kiosk all require it). Switch before anything else.
echo "==> Forcing X11 display server..."
if command -v raspi-config >/dev/null 2>&1; then
    raspi-config nonint do_wayland W1
    echo "  X11 selected via raspi-config."
else
    echo "  WARNING: raspi-config not found — set display to X11 manually via raspi-config."
fi

# ── 2. System packages ────────────────────────────────────────────────────────
echo "==> Installing system packages..."
apt-get update -qq
apt-get install -y \
    xserver-xorg \
    xinit \
    chromium \
    matchbox-window-manager \
    xinput \
    curl \
    python3 \
    python3-pip \
    python3-venv

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

# ── 5. Make scripts executable ────────────────────────────────────────────────
echo "==> Setting permissions..."
chmod +x "$DEPLOY_DIR/kiosk-session.sh"
chown "$KIOSK_USER:$KIOSK_USER" "$DEPLOY_DIR/kiosk-session.sh"

# ── 6. Systemd backend service ────────────────────────────────────────────────
echo "==> Installing weldflex-backend.service..."
sed \
    -e "s|/home/pi/WeldFlex|$PROJECT_DIR|g" \
    -e "s|User=pi|User=$KIOSK_USER|g" \
    "$DEPLOY_DIR/weldflex-backend.service" \
    > /etc/systemd/system/weldflex-backend.service

systemctl daemon-reload
systemctl enable weldflex-backend.service

# ── 7. Autologin: getty → startx (replaces LightDM kiosk session) ─────────────
# LightDM + custom X sessions is fragile on RPi OS (Wayland greeter, PAM group
# checks, session type detection). getty autologin + startx is the standard
# RPi kiosk pattern and has no such failure modes.
echo "==> Configuring autologin (getty + startx)..."

# Disable any display manager — we start X ourselves via startx
systemctl disable lightdm 2>/dev/null || true
systemctl disable gdm3   2>/dev/null || true

# Autologin the kiosk user on TTY1
mkdir -p /etc/systemd/system/getty@tty1.service.d/
cat > /etc/systemd/system/getty@tty1.service.d/autologin.conf << EOF
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin $KIOSK_USER --noclear %I \$TERM
EOF
systemctl daemon-reload

# On TTY1 login, immediately launch X into the kiosk session
cat > "/home/$KIOSK_USER/.bash_profile" << EOF
# WeldFlex kiosk — start X automatically on TTY1
if [[ -z "\$DISPLAY" && "\$XDG_VTNR" == "1" ]]; then
    exec startx $PROJECT_DIR/deploy/rpi/kiosk-session.sh
fi
EOF
chown "$KIOSK_USER:$KIOSK_USER" "/home/$KIOSK_USER/.bash_profile"

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "Install complete. Next steps:"
echo "  1. Make sure .env exists at $PROJECT_DIR/.env"
echo "  2. Make sure the FAIRINO linux SDK is at $PROJECT_DIR/fairino-python-sdk-main/linux/"
echo "  3. Make sure the RPi has an IP on the 192.168.58.x subnet (robot's network)"
echo "  4. sudo reboot"
echo ""
