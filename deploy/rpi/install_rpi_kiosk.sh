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
sudo apt install -y python3-venv chromium-browser unclutter git

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
python "$REPO_ROOT/scripts/rpi_preflight.py"

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
TMP_KIOSK="$(mktemp)"
sed \
  -e "s|^User=.*$|User=$TARGET_USER|" \
  -e "s|^WorkingDirectory=.*$|WorkingDirectory=$REPO_ROOT|" \
  -e "s|^Environment=XAUTHORITY=.*$|Environment=XAUTHORITY=$TARGET_HOME/.Xauthority|" \
  -e "s|^ExecStart=.*$|ExecStart=$REPO_ROOT/deploy/rpi/kiosk-launch.sh|" \
  "$REPO_ROOT/deploy/rpi/weldflex-kiosk.service" > "$TMP_KIOSK"
sudo cp "$TMP_KIOSK" /etc/systemd/system/weldflex-kiosk.service
rm -f "$TMP_KIOSK"

sudo systemctl daemon-reload
sudo systemctl enable --now weldflex-update.service
sudo systemctl enable --now weldflex-backend.service
sudo systemctl enable --now weldflex-kiosk.service

echo
echo "Install complete."
echo "Check service status:"
echo "  sudo systemctl status weldflex-update.service"
echo "  sudo systemctl status weldflex-backend.service"
echo "  sudo systemctl status weldflex-kiosk.service"
echo "Reboot when ready:"
echo "  sudo reboot"
