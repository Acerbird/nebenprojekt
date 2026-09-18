#!/usr/bin/env bash
# Startet die Anwendung unter http://127.0.0.1:8000
set -euo pipefail

# Aus website/ heraus starten: backend ist ein Paket, damit die Importe
# unabhängig vom Arbeitsverzeichnis funktionieren (systemd, cron, Tests).
cd "$(dirname "$0")"

if [ ! -d backend/.venv ]; then
  echo "Lege virtuelle Umgebung an ..."
  python3 -m venv backend/.venv
fi

source backend/.venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet -r backend/requirements.txt

exec uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
