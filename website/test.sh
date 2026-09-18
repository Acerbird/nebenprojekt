#!/usr/bin/env bash
# Führt alle Tests aus.
#
# Drei Stufen, jede für sich lauffähig:
#   1. Modelltests   nur Python, keine Abhängigkeiten
#   2. HTTP-Tests    zusätzlich FastAPI und httpx (aus der virtuellen Umgebung)
#   3. Frontend      Node mit jsdom, falls installiert
#
# Fehlt eine Voraussetzung, wird die Stufe übersprungen statt zu scheitern.
set -uo pipefail

cd "$(dirname "$0")"

PYTHON=python3
if [ -x backend/.venv/bin/python ]; then
  PYTHON=backend/.venv/bin/python
fi

status=0

echo "── Python: Modell, Datenschicht und HTTP ──────────────────────────"
"$PYTHON" -m unittest discover -s tests -t . "$@" || status=1

# Frontend-Tests brauchen die gerenderte Seite. Sie wird jedes Mal neu erzeugt,
# damit sie nicht hinter dem Template zurückbleibt.
echo ""
echo "── Frontend: Formularlogik im DOM ─────────────────────────────────"

NODE=$(command -v node || echo /opt/homebrew/bin/node)

if [ ! -x "$NODE" ]; then
  echo "übersprungen: Node ist nicht installiert."
elif [ ! -d node_modules/jsdom ]; then
  echo "übersprungen: jsdom fehlt — einmalig installieren mit 'npm install'."
elif [ ! -x backend/.venv/bin/python ]; then
  echo "übersprungen: für die gerenderte Seite wird die virtuelle Umgebung gebraucht."
else
  backend/.venv/bin/python -c "
from fastapi.testclient import TestClient
from backend.main import app
client = TestClient(app)
for pfad, datei in (('/analysen', 'analysis-page.html'), ('/handel', 'trade-page.html'),
                    ('/maerkte', 'markets-page.html')):
    open('tests/frontend/' + datei, 'w', encoding='utf-8').write(client.get(pfad).text)
" && "$NODE" --test tests/frontend/*.test.mjs || status=1
fi

echo ""
if [ "$status" -eq 0 ]; then
  echo "Alle Stufen bestanden."
else
  echo "Es sind Tests fehlgeschlagen."
fi
exit "$status"
