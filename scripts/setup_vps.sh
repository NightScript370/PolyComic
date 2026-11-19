#!/usr/bin/env bash
# Idempotent setup of PolyComic on Debian/Ubuntu WITHOUT Docker.
# Run as root after copying the code to /opt/polycomic.
# Optional env: POLYCOMIC_DB_PASSWORD (otherwise prompted), APP_DIR, DB_NAME, DB_USER.
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/polycomic}"
DB_NAME="${DB_NAME:-polycomic}"
DB_USER="${DB_USER:-polycomic}"
APP_USER=polycomic

[ "$(id -u)" -eq 0 ] || { echo "Run as root (sudo)." >&2; exit 1; }
[ -f "$APP_DIR/requirements.txt" ] || { echo "Copy the code to $APP_DIR first." >&2; exit 1; }

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y python3 python3-venv python3-pip postgresql

if ! id "$APP_USER" >/dev/null 2>&1; then
  useradd --system --home-dir "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
fi

# Database role + database (password never hard-coded)
case "$DB_USER$DB_NAME" in
  *[!a-z0-9_]*) echo "DB_USER / DB_NAME may only contain a-z, 0-9 and _." >&2; exit 1 ;;
esac
DB_PASSWORD="${POLYCOMIC_DB_PASSWORD:-}"
if [ -z "$DB_PASSWORD" ]; then
  read -r -s -p "Password for PostgreSQL role '$DB_USER': " DB_PASSWORD; echo
fi
[ -n "$DB_PASSWORD" ] || { echo "Empty password." >&2; exit 1; }
nl=$'\n'; cr=$'\r'; bs='\'; sq="'"
case "$DB_PASSWORD" in
  *"$nl"*|*"$cr"*) echo "The password must not contain line breaks." >&2; exit 1 ;;
esac

# The password reaches psql ONLY through stdin: printf is a bash builtin, so it never
# shows up in the process list or in psql's argv (no -v pw=..., no -c "..."). Inside a
# psql \set '...' argument backslashes and single quotes are doubled; :'pw' then quotes
# the value as an SQL literal. Re-running just resets the password (idempotent).
pw_escaped=${DB_PASSWORD//"$bs"/"$bs$bs"}
pw_escaped=${pw_escaped//"$sq"/"$sq$sq"}
if [ "$(sudo -u postgres psql -tAc "SELECT 1 FROM pg_roles WHERE rolname='$DB_USER'")" = 1 ]; then
  verb=ALTER
else
  verb=CREATE
fi
{
  printf '\\set pw %s%s%s\n' "$sq" "$pw_escaped" "$sq"
  printf '%s ROLE %s LOGIN PASSWORD :%spw%s;\n' "$verb" "$DB_USER" "$sq" "$sq"
} | sudo -u postgres psql -q -v ON_ERROR_STOP=1 >/dev/null
unset DB_PASSWORD pw_escaped
echo "PostgreSQL role '$DB_USER': $verb ROLE done."

if [ "$(sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='$DB_NAME'")" != 1 ]; then
  sudo -u postgres createdb -O "$DB_USER" "$DB_NAME"
fi

# venv + dependencies
cd "$APP_DIR"
[ -x .venv/bin/python ] || python3 -m venv .venv
.venv/bin/python -m pip install --disable-pip-version-check -q -r requirements.txt

# .env template (never overwrite an existing one)
if [ ! -f .env ]; then
  cp .env.example .env
  echo "Created $APP_DIR/.env from .env.example - edit it."
fi
chown -R "$APP_USER:$APP_USER" "$APP_DIR"
chmod 600 .env

# systemd unit
install -m 644 deploy/polycomic.service /etc/systemd/system/polycomic.service
systemctl daemon-reload
systemctl enable polycomic.service

cat <<MSG

Setup done. Next steps:
 1. Edit $APP_DIR/.env: set SESSION_SECRET, API keys, PUBLIC_BASE_URL, and
      DATABASE_URL=postgresql+asyncpg://$DB_USER:<password>@localhost:5432/$DB_NAME
 2. Start the service:   systemctl start polycomic   (logs: journalctl -u polycomic -f)
 3. nginx: copy deploy/nginx.conf.example to /etc/nginx/sites-available/, set your
    domain, enable it, add TLS (certbot), then: nginx -t && systemctl reload nginx
    (the app listens on 127.0.0.1:7659, which the example already proxies to).
MSG
