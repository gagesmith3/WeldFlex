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

# ── 1. System packages ────────────────────────────────────────────────────────
echo "==> Installing system packages..."
apt-get update -qq
apt-get install -y \
    lightdm \
    chromium-browser \
    matchbox-window-manager \
    xinput \
    curl \
    python3 \
    python3-pip \
    python3-venv

# ── 2. Python venv + deps ─────────────────────────────────────────────────────
echo "==> Setting up Python venv..."
VENV="$PROJECT_DIR/venv"
python3 -m venv "$VENV"
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -r "$PROJECT_DIR/requirements.txt"
chown -R "$KIOSK_USER:$KIOSK_USER" "$VENV"

# ── 3. .env check ─────────────────────────────────────────────────────────────
if [ ! -f "$PROJECT_DIR/.env" ]; then
    echo ""
    echo "WARNING: No .env found at $PROJECT_DIR/.env"
    echo "  Copy deploy/rpi/.env.rpi.example to .env and fill in your robot IP."
    echo ""
fi

# ── 4. Make scripts executable ────────────────────────────────────────────────
echo "==> Setting permissions..."
chmod +x "$DEPLOY_DIR/kiosk-session.sh"
chown "$KIOSK_USER:$KIOSK_USER" "$DEPLOY_DIR/kiosk-session.sh"

# ── 5. Systemd backend service ────────────────────────────────────────────────
echo "==> Installing weldflex-backend.service..."
# Patch the service file with the actual project path and user
sed \
    -e "s|/home/pi/WeldFlex|$PROJECT_DIR|g" \
    -e "s|User=pi|User=$KIOSK_USER|g" \
    "$DEPLOY_DIR/weldflex-backend.service" \
    > /etc/systemd/system/weldflex-backend.service

systemctl daemon-reload
systemctl enable weldflex-backend.service

# ── 6. Kiosk X session ────────────────────────────────────────────────────────
echo "==> Installing kiosk X session..."
# Patch desktop file with actual path
sed \
    -e "s|/home/pi/WeldFlex|$PROJECT_DIR|g" \
    "$DEPLOY_DIR/weldflex-kiosk.desktop" \
    > /usr/share/xsessions/weldflex-kiosk.desktop

# ── 7. LightDM autologin ──────────────────────────────────────────────────────
echo "==> Configuring LightDM..."
# Must be in the main lightdm.conf — drop-in conf.d/ files are overridden by it
LIGHTDM_CONF="/etc/lightdm/lightdm.conf"

if [ ! -f "$LIGHTDM_CONF" ]; then
    cat > "$LIGHTDM_CONF" << EOF
[Seat:*]
autologin-user=$KIOSK_USER
autologin-user-timeout=0
user-session=weldflex-kiosk
autologin-session=weldflex-kiosk
EOF
else
    # Update existing file — set all four keys under [Seat:*]
    python3 - "$LIGHTDM_CONF" "$KIOSK_USER" << 'PYEOF'
import sys, re

conf_path = sys.argv[1]
user = sys.argv[2]

with open(conf_path) as f:
    text = f.read()

def set_key(text, key, value):
    pattern = rf'^[#\s]*{re.escape(key)}\s*=.*$'
    replacement = f'{key}={value}'
    new_text, n = re.subn(pattern, replacement, text, flags=re.MULTILINE)
    if n == 0:
        # Key not present — append under [Seat:*] or at end
        if '[Seat:*]' in new_text:
            new_text = new_text.replace('[Seat:*]', f'[Seat:*]\n{replacement}', 1)
        else:
            new_text += f'\n[Seat:*]\n{replacement}\n'
    return new_text

for key, val in [
    ('autologin-user', user),
    ('autologin-user-timeout', '0'),
    ('user-session', 'weldflex-kiosk'),
    ('autologin-session', 'weldflex-kiosk'),
]:
    text = set_key(text, key, val)

with open(conf_path, 'w') as f:
    f.write(text)

print(f"  lightdm.conf updated for user={user}, session=weldflex-kiosk")
PYEOF
fi

# ── Done ──────────────────────────────────────────────────────────────────────
echo ""
echo "Install complete. Next steps:"
echo "  1. Make sure .env exists at $PROJECT_DIR/.env"
echo "  2. Make sure the FAIRINO linux SDK is at $PROJECT_DIR/fairino-python-sdk-main/linux/"
echo "  3. Make sure the RPi has an IP on the 192.168.58.x subnet (robot's network)"
echo "  4. sudo reboot"
echo ""
