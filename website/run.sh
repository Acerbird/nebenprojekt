#!/usr/bin/env bash
# Startet die Anwendung unter http://127.0.0.1:8000
set -euo pipefail

cd "$(dirname "$0")/backend"

if [ ! -d .venv ]; then
  echo "Lege virtuelle Umgebung an ..."
  python3 -m venv .venv
fi

source .venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet -r requirements.txt

exec uvicorn main:app --reload --host 127.0.0.1 --port 8000
