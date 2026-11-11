#!/usr/bin/env bash
# Sets up the Smart Library face-recognition station on a Raspberry Pi.
#
# Needs: Raspberry Pi OS 64-bit WITH desktop (Bookworm or newer), Camera Module connected, internet.
# Run as your normal user (not root), from the cloned project:
#   bash face_recognition_iot/raspberry_pi/setup_pi.sh https://library.example.com
# It asks for the IoT key (or pass it as the 2nd argument).
set -euo pipefail

API_URL="${1:-}"
IOT_KEY="${2:-}"
[ -n "$API_URL" ] || { echo "Usage: bash $0 https://your-library-address [iot-key]"; exit 1; }
[ "$(id -u)" -ne 0 ] || { echo "Run as your normal user, not root. The script uses sudo when needed."; exit 1; }
case "$API_URL" in https://*) ;; *) echo "The address must start with https://"; exit 1;; esac
API_URL="${API_URL%/}"

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STATION_DIR="$(cd "$HERE/.." && pwd)"
VENV="$STATION_DIR/.venv"
PI_USER="$(id -un)"

if [ -z "$IOT_KEY" ]; then
    read -r -s -p "Paste the IoT key from the server: " IOT_KEY
    echo
fi
[ -n "$IOT_KEY" ] || { echo "An IoT key is required."; exit 1; }

echo "==> 1/7  System packages"
sudo apt-get update
sudo apt-get install -y python3-venv python3-pip python3-picamera2 curl util-linux
if ! command -v chromium-browser >/dev/null 2>&1 && ! command -v chromium >/dev/null 2>&1; then
    sudo apt-get install -y chromium-browser || sudo apt-get install -y chromium
fi

echo "==> 2/7  Checking the camera"
python3 - <<'PY' || echo "    WARNING: no camera detected. Check the ribbon cable, then reboot and re-run."
from picamera2 import Picamera2
cams = Picamera2.global_camera_info()
print("    cameras found:", len(cams))
raise SystemExit(0 if cams else 1)
PY

echo "==> 3/7  Python environment (a few minutes)"
# --system-site-packages lets the venv use the apt-installed picamera2.
python3 -m venv --system-site-packages "$VENV"
"$VENV/bin/pip" install --upgrade pip
# numpy<2 keeps compatibility with the apt-installed camera libraries.
"$VENV/bin/pip" install -r "$STATION_DIR/requirements.txt" "numpy<2"

echo "==> 4/7  Face-recognition models"
( cd "$STATION_DIR" && "$VENV/bin/python" -c "from models import ensure_models; print('   ', *ensure_models().values(), sep='\n    ')" )

echo "==> 5/7  Connection settings"
sudo install -m 600 -o root -g root /dev/null /etc/smart-library-station.env
sudo tee /etc/smart-library-station.env >/dev/null <<ENV
SMART_LIBRARY_API_URL=$API_URL
SMART_LIBRARY_IOT_KEY=$IOT_KEY
SMART_LIBRARY_AUTO_ATTENDANCE=0
SMART_LIBRARY_AUTO_COOLDOWN=60
SMART_LIBRARY_CAMERA=0
SMART_LIBRARY_STATION_HOST=127.0.0.1
SMART_LIBRARY_STATION_PORT=5001
ENV
if curl -fsS -m 20 -H "X-IOT-API-Key: $IOT_KEY" "$API_URL/api/iot/face-templates" -o /dev/null; then
    echo "    Server reachable and key accepted."
else
    echo "    WARNING: could not reach $API_URL with that key. Check the address, key and internet."
fi

echo "==> 6/7  Start automatically at boot"
sed -e "s|__USER__|$PI_USER|" -e "s|__STATION_DIR__|$STATION_DIR|" -e "s|__VENV__|$VENV|" \
    "$HERE/smart-library-station.service" | sudo tee /etc/systemd/system/smart-library-station.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now smart-library-station

echo "==> 7/7  Full-screen browser at login"
chmod +x "$HERE/kiosk.sh"
mkdir -p "$HOME/.config/labwc"
AUTOSTART="$HOME/.config/labwc/autostart"
touch "$AUTOSTART"
grep -qF "$HERE/kiosk.sh" "$AUTOSTART" || echo "\"$HERE/kiosk.sh\" &" >> "$AUTOSTART"
sudo raspi-config nonint do_boot_behaviour B4 || echo "    Could not set desktop auto-login; set it in: sudo raspi-config > System Options > Boot / Auto Login."
sudo raspi-config nonint do_blanking 1 || true

echo
echo "Done. Reboot to test the full start-up:   sudo reboot"
echo "Check the station any time:               systemctl status smart-library-station"
echo "Live log:                                 journalctl -u smart-library-station -f"
