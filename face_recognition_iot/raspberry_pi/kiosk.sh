#!/usr/bin/env bash
# Opens the face-recognition screen full-screen in Chromium once the station is ready.
# Started automatically at login by ~/.config/labwc/autostart (see setup_pi.sh).
URL="http://127.0.0.1:${SMART_LIBRARY_STATION_PORT:-5001}/"

# Only ever run one copy.
exec 9>/tmp/smart-library-kiosk.lock
flock -n 9 || exit 0

# Wait (up to ~2 minutes) for the station to answer.
for _ in $(seq 1 60); do
    curl -fsS -o /dev/null "$URL" && break
    sleep 2
done

BROWSER="$(command -v chromium-browser || command -v chromium || true)"
[ -n "$BROWSER" ] || { echo "Chromium is not installed."; exit 1; }

# Avoid the "Chromium didn't shut down correctly" bar after a power cut.
sed -i 's/"exited_cleanly":false/"exited_cleanly":true/; s/"exit_type":"Crashed"/"exit_type":"Normal"/' \
    "$HOME/.config/chromium/Default/Preferences" 2>/dev/null || true

exec "$BROWSER" --kiosk --noerrdialogs --disable-infobars --no-first-run \
    --disable-session-crashed-bubble --password-store=basic --incognito "$URL"
