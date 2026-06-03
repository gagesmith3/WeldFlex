#!/usr/bin/env bash
set -euo pipefail

if [[ "$(uname -s)" != "Linux" ]]; then
  echo "This installer is intended for Linux (Raspberry Pi OS)."
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

TARGET_USER="${WELDFLEX_TARGET_USER:-${SUDO_USER:-$USER}}"
if [[ "$TARGET_USER" == "root" ]]; then
  TARGET_USER="pi"
fi

TARGET_HOME="$(getent passwd "$TARGET_USER" | cut -d: -f6 || true)"
if [[ -z "$TARGET_HOME" ]]; then
  echo "Unable to resolve home directory for user '$TARGET_USER'."
  exit 1
fi

echo "== WeldFlex Raspberry Pi Installer =="
echo "Repo root: $REPO_ROOT"
echo "Target user: $TARGET_USER"
echo "Target home: $TARGET_HOME"

echo
echo "[1/8] Installing system packages..."
sudo apt update

CHROMIUM_PACKAGE=""
chromium_candidate="$(apt-cache policy chromium | awk '/Candidate:/ {print $2}')"
chromium_browser_candidate="$(apt-cache policy chromium-browser | awk '/Candidate:/ {print $2}')"

# Prefer modern package naming first (Debian/Raspberry Pi OS Bookworm/Trixie).
if [[ -n "$chromium_candidate" && "$chromium_candidate" != "(none)" ]]; then
  CHROMIUM_PACKAGE="chromium"
elif [[ -n "$chromium_browser_candidate" && "$chromium_browser_candidate" != "(none)" ]]; then
  CHROMIUM_PACKAGE="chromium-browser"
else
  echo "Unable to find a Chromium package ('chromium-browser' or 'chromium')."
  echo "Install Chromium manually, then re-run this installer."
  exit 1
fi

sudo apt install -y python3-venv "$CHROMIUM_PACKAGE" unclutter git onboard at-spi2-core plymouth imagemagick

echo
echo "[1b/8] Configuring onboard virtual keyboard (auto-show on input focus)..."
ONBOARD_OVERRIDE=/usr/share/glib-2.0/schemas/99_onboard-weldflex.gschema.override
sudo tee "$ONBOARD_OVERRIDE" > /dev/null <<'OVERRIDE'
[org.onboard]
auto-show-enabled=true
start-minimized=true

[org.onboard.auto-show]
enabled=true
OVERRIDE
sudo glib-compile-schemas /usr/share/glib-2.0/schemas/

echo
echo "[2/8] Creating Python virtual environment..."
cd "$REPO_ROOT"
if [[ ! -d "$REPO_ROOT/venv" ]]; then
  python3 -m venv "$REPO_ROOT/venv"
fi

# shellcheck source=/dev/null
source "$REPO_ROOT/venv/bin/activate"

echo
echo "[3/8] Installing Python dependencies..."
python -m pip install --upgrade pip
python -m pip install -r "$REPO_ROOT/requirements.txt"

echo
echo "[4/8] Preparing .env..."
if [[ ! -f "$REPO_ROOT/.env" ]]; then
  cp "$REPO_ROOT/.env.example" "$REPO_ROOT/.env"
  echo "Created .env from .env.example"
fi

LINUX_SDK_PATH="$REPO_ROOT/fairino-python-sdk-main/linux"
if grep -q '^WELDFLEX_FAIRINO_PATH=' "$REPO_ROOT/.env"; then
  sed -i "s|^WELDFLEX_FAIRINO_PATH=.*$|WELDFLEX_FAIRINO_PATH=$LINUX_SDK_PATH|" "$REPO_ROOT/.env"
else
  echo "WELDFLEX_FAIRINO_PATH=$LINUX_SDK_PATH" >> "$REPO_ROOT/.env"
fi

echo
echo "[5/8] Running preflight checks..."
if ! python "$REPO_ROOT/scripts/rpi_preflight.py"; then
  echo "Warning: Preflight checks reported issues."
  echo "Continuing install so services are configured; review preflight output above."
fi

echo
echo "[6/8] Installing and enabling boot update service..."
chmod +x "$REPO_ROOT/deploy/rpi/update_on_boot.sh"
TMP_UPDATE="$(mktemp)"
sed \
  -e "s|^User=.*$|User=$TARGET_USER|" \
  -e "s|^WorkingDirectory=.*$|WorkingDirectory=$REPO_ROOT|" \
  -e "s|^ExecStart=.*$|ExecStart=$REPO_ROOT/deploy/rpi/update_on_boot.sh|" \
  "$REPO_ROOT/deploy/rpi/weldflex-update.service" > "$TMP_UPDATE"
sudo cp "$TMP_UPDATE" /etc/systemd/system/weldflex-update.service
rm -f "$TMP_UPDATE"

echo
echo "[7/8] Installing and enabling backend service..."
TMP_BACKEND="$(mktemp)"
sed \
  -e "s|^User=.*$|User=$TARGET_USER|" \
  -e "s|^WorkingDirectory=.*$|WorkingDirectory=$REPO_ROOT|" \
  -e "s|^ExecStart=.*$|ExecStart=$REPO_ROOT/venv/bin/python -m backend.app|" \
  "$REPO_ROOT/deploy/rpi/weldflex-backend.service" > "$TMP_BACKEND"
sudo cp "$TMP_BACKEND" /etc/systemd/system/weldflex-backend.service
rm -f "$TMP_BACKEND"

echo
echo "[8/8] Installing and enabling kiosk service..."
chmod +x "$REPO_ROOT/deploy/rpi/kiosk-launch.sh"

KIOSK_BROWSER_CMD=""
if command -v chromium >/dev/null 2>&1; then
  KIOSK_BROWSER_CMD="chromium"
elif command -v chromium-browser >/dev/null 2>&1; then
  KIOSK_BROWSER_CMD="chromium-browser"
else
  echo "Chromium command not found after install."
  echo "Install Chromium manually and set WELDFLEX_BROWSER_CMD in the kiosk service."
  exit 1
fi

TMP_KIOSK="$(mktemp)"
sed \
  -e "s|^User=.*$|User=$TARGET_USER|" \
  -e "s|^WorkingDirectory=.*$|WorkingDirectory=$REPO_ROOT|" \
  -e "s|^Environment=XAUTHORITY=.*$|Environment=XAUTHORITY=$TARGET_HOME/.Xauthority|" \
  -e "s|^Environment=WELDFLEX_BROWSER_CMD=.*$|Environment=WELDFLEX_BROWSER_CMD=$KIOSK_BROWSER_CMD|" \
  -e "s|^ExecStart=.*$|ExecStart=$REPO_ROOT/deploy/rpi/kiosk-launch.sh|" \
  "$REPO_ROOT/deploy/rpi/weldflex-kiosk.service" > "$TMP_KIOSK"
sudo cp "$TMP_KIOSK" /etc/systemd/system/weldflex-kiosk.service
rm -f "$TMP_KIOSK"

sudo systemctl daemon-reload
sudo systemctl enable --now weldflex-update.service
sudo systemctl enable --now weldflex-backend.service
sudo systemctl enable --now weldflex-kiosk.service

echo
echo "[9/8] Configuring Plymouth boot splash..."
SPLASH_SRC="$REPO_ROOT/deploy/rpi/splash.jpg"
PLYMOUTH_DEST=/usr/share/plymouth/themes/pix/splash.png
if [[ -f "$SPLASH_SRC" ]]; then
  # Composite logo centred on a dark background, output 800x480 PNG.
  convert \
    \( -size 800x480 xc:'#1c1c1e' \) \
    \( "$SPLASH_SRC" -fuzz 15% -transparent white -resize 640x320 \) \
    -gravity center -composite \
    "$PLYMOUTH_DEST"
  sudo plymouth-set-default-theme --rebuild-initrd pix
  # Enable quiet splash in boot cmdline (handles both legacy and firmware paths).
  for CMDLINE in /boot/firmware/cmdline.txt /boot/cmdline.txt; do
    if [[ -f "$CMDLINE" ]] && ! grep -q 'splash' "$CMDLINE"; then
      sudo sed -i 's/$/ quiet splash plymouth.ignore-serial-consoles/' "$CMDLINE"
      echo "Added splash to $CMDLINE"
    fi
  done
  echo "Plymouth splash configured."
else
  echo "Warning: deploy/rpi/splash.jpg not found — skipping Plymouth setup."
fi

echo
echo "Install complete."
echo "Check service status:"
echo "  sudo systemctl status weldflex-update.service"
echo "  sudo systemctl status weldflex-backend.service"
echo "  sudo systemctl status weldflex-kiosk.service"
echo "Reboot when ready:"
echo "  sudo reboot"
