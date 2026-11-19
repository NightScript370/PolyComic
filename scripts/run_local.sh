#!/usr/bin/env bash
# Run PolyComic directly (no Docker) on Linux/macOS.
# Usage: scripts/run_local.sh [--dev] [--reload] [--fake] [--skip-migrate] [--port N]
# Port: --port N, else PORT from the environment / .env, else 7659.
# DATABASE_URL in .env: postgresql+asyncpg:// (postgres:// / postgresql:// are rewritten).
set -euo pipefail

DEV=0 RELOAD=0 FAKE=0 MIGRATE=1 PORT_ARG=""
while [ $# -gt 0 ]; do
  case "$1" in
    --dev) DEV=1 ;;
    --reload) RELOAD=1 ;;
    --fake) FAKE=1 ;;
    --skip-migrate) MIGRATE=0 ;;
    --port) shift; PORT_ARG="${1:?--port needs a value}" ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  echo "No .env file found. Copy .env.example to .env (cp .env.example .env) and fill it in." >&2
  exit 1
fi

if [ ! -x .venv/bin/python ]; then
  echo "Creating .venv ..."
  PY=""
  for c in python3.13 python3.12 python3; do
    if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
  done
  [ -n "$PY" ] || { echo "No python3 found." >&2; exit 1; }
  "$PY" -m venv .venv
fi

echo "Installing requirements ..."
.venv/bin/python -m pip install --disable-pip-version-check -q -r requirements.txt
if [ "$DEV" = 1 ]; then
  .venv/bin/python -m pip install --disable-pip-version-check -q -r requirements-dev.txt
fi

set -a
# shellcheck disable=SC1091
. ./.env
set +a
echo "Loaded .env"

# Priority: --port flag, then PORT (environment / .env), then the project default.
PORT="${PORT_ARG:-${PORT:-7659}}"

if [ "$FAKE" = 1 ]; then
  export IMAGE_PROVIDER=fake
  echo "IMAGE_PROVIDER=fake (offline mode)"
fi

echo "Note: DATABASE_URL must use the postgresql+asyncpg:// scheme."

if [ "$MIGRATE" = 1 ]; then
  echo "Running alembic upgrade head ..."
  .venv/bin/python -m alembic upgrade head
fi

ARGS=(app.main:app --host 127.0.0.1 --port "$PORT")
[ "$RELOAD" = 1 ] && ARGS+=(--reload)
echo "Starting http://127.0.0.1:$PORT (Ctrl+C to stop)"
exec .venv/bin/python -m uvicorn "${ARGS[@]}"
