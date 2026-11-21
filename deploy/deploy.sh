#!/usr/bin/env bash
# استقرار پنل روی سرور. فقط سرویس/پورت/کانفیگ خودش را می‌سازد و به بقیه دست نمی‌زند.
# اجرا روی سرور از پوشه کد:  bash deploy/deploy.sh
set -euo pipefail

APP_DIR=${APP_DIR:-/opt/divar}
PORT=${PORT:-8004}
DOMAIN=${DOMAIN:-divar.chabooksaz.ir}
SERVICE=${SERVICE_NAME:-divar}
SRC_DIR=${SRC_DIR:-$(cd "$(dirname "$0")/.." && pwd)}

echo "== 1) بررسی پورت $PORT =="
if ss -ltnp | grep -q ":${PORT}\b"; then
  echo "پورت $PORT در حال استفاده است. با PORT=80XX دوباره اجرا کنید." >&2
  ss -ltnp | grep ":${PORT}\b" >&2
  exit 1
fi

echo "== 2) کپی کد به $APP_DIR =="
mkdir -p "$APP_DIR"
tar -C "$SRC_DIR" -cf - \
  --exclude=.git --exclude=.venv --exclude=data --exclude=__pycache__ \
  --exclude='*.pyc' . | tar -C "$APP_DIR" -xf -

echo "== 3) محیط پایتون ۳.۱۱ =="
cd "$APP_DIR"
if [ ! -x .venv/bin/python ]; then
  uv venv --seed --python 3.11 .venv
fi
.venv/bin/pip install -q -r requirements.txt

echo "== 4) فایل تنظیمات =="
mkdir -p "$APP_DIR/data"
if [ ! -f .env ]; then
  cp .env.example .env
  echo "فایل .env ساخته شد؛ توکن ربات و رمز پنل باید داخلش پر شود."
fi
chmod 600 .env

echo "== 5) سرویس systemd =="
sed -e "s#/opt/divar#${APP_DIR}#g" -e "s#--port 8004#--port ${PORT}#" \
  deploy/divar.service > "/etc/systemd/system/${SERVICE}.service"
systemctl daemon-reload
systemctl enable --now "$SERVICE"
sleep 2
systemctl --no-pager status "$SERVICE" | head -12

echo "== 6) nginx =="
sed -e "s#divar\.chabooksaz\.ir#${DOMAIN}#g" -e "s#127\.0\.0\.1:8004#127.0.0.1:${PORT}#g" \
  deploy/nginx-divar.conf > "/etc/nginx/sites-available/${SERVICE}"
ln -sfn "/etc/nginx/sites-available/${SERVICE}" "/etc/nginx/sites-enabled/${SERVICE}"
nginx -t
systemctl reload nginx

echo "== 7) گواهی TLS =="
certbot --nginx -d "$DOMAIN" --redirect --non-interactive --agree-tos \
  --register-unsafely-without-email || echo "certbot ناموفق بود؛ بعداً دستی اجرا کنید."
nginx -t && systemctl reload nginx

echo "== 8) تست =="
curl -s -o /dev/null -w "local  : %{http_code}\n" -H "Host: ${DOMAIN}" "http://127.0.0.1/login"
curl -s -o /dev/null -w "domain : %{http_code}\n" "https://${DOMAIN}/login" || true

echo "تمام. پنل: https://${DOMAIN}"
