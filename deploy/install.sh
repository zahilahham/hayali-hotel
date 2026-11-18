#!/bin/sh
# Install the Hayali server on Linux. Run from the project folder:
#   sudo sh deploy/install.sh
# One process serves the pages and the rooms. Do not add workers.

set -eu

if [ "$(id -u)" -ne 0 ]; then
  echo "Run this as root: sudo sh deploy/install.sh" >&2
  exit 1
fi

ROOT=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
if [ ! -f "$ROOT/backend/main.py" ]; then
  echo "Cannot find the server next to deploy/." >&2
  exit 1
fi
if [ ! -f "$ROOT/frontend/dist/index.html" ]; then
  echo "The pages are not built. Build the frontend first, then run this again." >&2
  exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is not installed." >&2
  exit 1
fi

if ! id hayali >/dev/null 2>&1; then
  useradd --system --home /opt/hayali --shell /usr/sbin/nologin hayali
fi

saved=""
if [ -f /opt/hayali/backend/.env ]; then
  saved=$(mktemp)
  cp /opt/hayali/backend/.env "$saved"
fi

rm -rf /opt/hayali/backend /opt/hayali/frontend
mkdir -p /opt/hayali/backend /opt/hayali/frontend
cp -a "$ROOT/backend/." /opt/hayali/backend/
rm -rf /opt/hayali/backend/__pycache__ /opt/hayali/backend/.venv
rm -f /opt/hayali/backend/.env
cp -a "$ROOT/frontend/dist" /opt/hayali/frontend/dist

if [ -n "$saved" ]; then
  cp "$saved" /opt/hayali/backend/.env
  rm -f "$saved"
elif [ ! -f /opt/hayali/backend/.env ]; then
  cp "$ROOT/deploy/hayali.env.example" /opt/hayali/backend/.env
fi

python3 -m venv /opt/hayali/backend/.venv
/opt/hayali/backend/.venv/bin/pip install --upgrade pip
/opt/hayali/backend/.venv/bin/pip install -r /opt/hayali/backend/requirements.txt

chown -R hayali:hayali /opt/hayali
cp "$ROOT/deploy/hayali.service" /etc/systemd/system/hayali.service
systemctl daemon-reload
systemctl enable hayali.service

if grep -q "change-me" /opt/hayali/backend/.env; then
  echo "Edit /opt/hayali/backend/.env (the database password), then: systemctl start hayali"
else
  systemctl restart hayali.service
  echo "Hayali is running on port 8001."
fi
