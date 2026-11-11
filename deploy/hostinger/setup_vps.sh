#!/usr/bin/env bash
# One-time setup of the Smart Library System on a Hostinger VPS (Ubuntu 22.04 or 24.04).
#
#   1. git clone <your repo> /opt/smart-library
#   2. sudo bash /opt/smart-library/deploy/hostinger/setup_vps.sh library.example.com you@example.com
#
# Safe to run again: it keeps your existing secrets and data.
set -euo pipefail

DOMAIN="${1:-}"
EMAIL="${2:-}"
APP_DIR=/opt/smart-library
DATA_DIR=/var/lib/smart-library
ENV_DIR=/etc/smart-library
ENV_FILE="$ENV_DIR/smart-library.env"

[ "$(id -u)" -eq 0 ] || { echo "Run this with sudo / as root."; exit 1; }
[ -n "$DOMAIN" ] && [ -n "$EMAIL" ] || { echo "Usage: sudo bash $0 <domain> <email-for-certificate-notices>"; exit 1; }
[ -f "$APP_DIR/backend/app.py" ] || { echo "Project not found in $APP_DIR. Clone it there first."; exit 1; }

echo "==> 1/8  System packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y python3 python3-venv python3-pip nginx certbot python3-certbot-nginx ufw sqlite3 curl git

echo "==> 2/8  Service user and folders"
id smartlib >/dev/null 2>&1 || useradd --system --home "$DATA_DIR" --shell /usr/sbin/nologin smartlib
install -d -o smartlib -g smartlib -m 750 "$DATA_DIR" "$DATA_DIR/models"
install -d -o root -g smartlib -m 750 "$ENV_DIR"

echo "==> 3/8  Python environment (this takes a few minutes)"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install --upgrade pip
"$APP_DIR/.venv/bin/pip" install -r "$APP_DIR/requirements.txt"

echo "==> 4/8  Secrets"
if [ ! -f "$ENV_FILE" ]; then
    SECRET="$("$APP_DIR/.venv/bin/python" -c 'import secrets; print(secrets.token_urlsafe(48))')"
    IOT="$("$APP_DIR/.venv/bin/python" -c 'import secrets; print(secrets.token_urlsafe(32))')"
    sed -e "s|__SECRET__|$SECRET|" -e "s|__IOT__|$IOT|" "$APP_DIR/deploy/hostinger/smart-library.env.example" > "$ENV_FILE"
    chown root:smartlib "$ENV_FILE"
    chmod 640 "$ENV_FILE"
    echo "    Created $ENV_FILE"
else
    echo "    Keeping existing $ENV_FILE"
fi

echo "==> 5/8  Face-recognition models used by the server"
sudo -u smartlib env SMART_LIBRARY_MODEL_DIR="$DATA_DIR/models" \
    "$APP_DIR/.venv/bin/python" "$APP_DIR/backend/face_enrollment.py" \
    || echo "    WARNING: model download failed. The site still works; the Raspberry Pi will register new faces instead."

echo "==> 6/8  Start the app"
install -m 644 "$APP_DIR/deploy/hostinger/smart-library.service" /etc/systemd/system/smart-library.service
systemctl daemon-reload
systemctl enable smart-library
systemctl restart smart-library
for _ in $(seq 1 30); do
    curl -fsS http://127.0.0.1:8000/healthz >/dev/null 2>&1 && break
    sleep 1
done
curl -fsS http://127.0.0.1:8000/healthz >/dev/null 2>&1 \
    || { echo "App did not start. See: journalctl -u smart-library -n 50"; exit 1; }
echo "    App is running."

echo "==> 7/8  Nginx, firewall, HTTPS"
sed "s|__DOMAIN__|$DOMAIN|g" "$APP_DIR/deploy/hostinger/nginx-smart-library.conf" > /etc/nginx/sites-available/smart-library
ln -sf /etc/nginx/sites-available/smart-library /etc/nginx/sites-enabled/smart-library
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx
ufw allow OpenSSH
ufw allow 'Nginx Full'
ufw --force enable
certbot --nginx -d "$DOMAIN" -m "$EMAIL" --agree-tos --no-eff-email --redirect --non-interactive \
    || echo "    HTTPS step failed. The domain's A record must point to this server first. Then run:  certbot --nginx -d $DOMAIN"

echo "==> 8/8  Daily backup"
install -m 755 "$APP_DIR/deploy/hostinger/backup.sh" /usr/local/bin/smart-library-backup
echo "30 2 * * * root /usr/local/bin/smart-library-backup" > /etc/cron.d/smart-library-backup

echo
echo "Done.  Open https://$DOMAIN  and sign in as MIS Admin, then CHANGE THE DEFAULT PASSWORDS."
echo "Raspberry Pi key (use as SMART_LIBRARY_IOT_KEY on the Pi):"
echo "    sudo grep SMART_LIBRARY_IOT_KEY $ENV_FILE"
