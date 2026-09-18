# Energiewende — Stromsystem verstehen

Eine kleine Lern-Website zum deutschen Strommarkt: Erklärseiten zu Merit-Order,
Residuallast und erneuerbaren Energien, dazu ein Simulator, der den stündlichen
Kraftwerkseinsatz und den Börsenpreis für frei wählbare Parameter durchrechnet.

## Starten

```bash
cd website
./run.sh
```

Das Skript legt beim ersten Aufruf eine virtuelle Umgebung an, installiert die
Abhängigkeiten und startet den Server auf <http://127.0.0.1:8000>.
Abhängigkeiten sind nur FastAPI, Uvicorn und Jinja2 — das Modell rechnet mit der
Python-Standardbibliothek, das Frontend kommt ohne externe Bibliothek aus.

## Seiten

| Pfad | Inhalt |
|---|---|
| `/` | Einstieg und Kurzfassung |
| `/stromsystem` | Last, Residuallast, Merit-Order, Einheitspreisverfahren |
| `/erneuerbare` | Kapazitätsfaktoren, Dunkelflaute, Abregelung, Flexibilität |
| `/analysen` | Simulator mit Merit-Order, Erzeugungsmix und Preisverlauf |
| `/glossar` | 25 Begriffe mit Sofortsuche und Querverweisen |
| `/docs` | automatisch erzeugte API-Dokumentation |

## API

| Endpunkt | Zweck |
|---|---|
| `GET /api/simulate` | stündlicher Einsatz, Preise und Kennzahlen |
| `GET /api/merit-order` | Merit-Order-Kurve für die übergebenen Parameter |
| `GET /api/profiles/day` | Tagesgänge von Last und Photovoltaik |
| `GET /api/glossary` | Glossareinträge als JSON |
| `GET /health` | Statusabfrage |

Parameter von `/api/simulate`: `wind_gw`, `solar_gw`, `co2_price`, `gas_price`,
`peak_load_gw`, `hours`, `season` (`winter`, `uebergang`, `sommer`). Werte außerhalb
der zulässigen Bereiche werden begrenzt, nicht abgelehnt. Das eingestellte Szenario
steht auf der Analyseseite in der Adresszeile und ist damit teilbar.

## Das Modell

Grenzkosten je Kraftwerksblock ergeben sich aus Brennstoffpreis geteilt durch
Wirkungsgrad, CO₂-Kosten (Emissionsfaktor × Zertifikatspreis / Wirkungsgrad) und
variablen Betriebskosten. Die Last wird Stunde für Stunde von den günstigsten
verfügbaren Blöcken gedeckt; der Preis ist der des letzten benötigten Blocks.
Muss erneuerbare Leistung abgeregelt werden, fällt der Preis unter null.

Nicht abgebildet: Speicher, Import und Export, Mindestlasten, An- und Abfahrkosten,
Kraft-Wärme-Kopplung, Netzengpässe. Die Wetterprofile sind synthetisch, aber in
Niveau und Tagesgang an deutsche Verhältnisse angelehnt. Die Ergebnisse sind
Größenordnungen zum Verstehen der Mechanik — keine Prognose.

## Aufbau

```
website/
├── run.sh                      Startskript
├── backend/
│   ├── main.py                 Routen und API
│   ├── analysis.py             Merit-Order und Dispatch
│   ├── utils/profiles.py       Last-, Wind- und PV-Profile
│   └── static_data/            Kraftwerkspark und Glossar als JSON
└── frontend/
    ├── templates/              Jinja2-Templates
    └── static/
        ├── css/style.css       Designtokens, hell und dunkel
        └── js/
            ├── charts.js       SVG-Diagramme (Linie, Fläche, Blöcke)
            ├── analysis.js     Simulator-Seite
            ├── explain.js      Diagramme der Erklärseiten
            └── glossary.js     Glossarsuche
```

### Diagrammfarben

Die Serienfarben liegen als CSS-Variablen in `style.css` und folgen automatisch
dem Hell-/Dunkel-Modus. Die Zuordnung ist gegen Farbfehlsichtigkeit geprüft —
benachbarte Kategorien halten in beiden Modi genügend Abstand. Wer die Farben
ändert, sollte das erneut prüfen und nicht nur nach Augenmaß entscheiden.
