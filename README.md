# Energiewende — Stromsystem verstehen

Eine kleine Lern-Website zum deutschen Strommarkt: Erklärseiten zu Merit-Order,
Residuallast und erneuerbaren Energien, dazu ein Simulator, der den stündlichen
Kraftwerkseinsatz und den Börsenpreis für frei wählbare Parameter durchrechnet.

Gerechnet wird wahlweise auf erzeugten Profilen oder auf echten Messwerten von
SMARD. Weil aus der gemessenen Einspeisung und der installierten Leistung ein
Kapazitätsfaktor gebildet wird, lässt sich dasselbe echte Wetter mit einem
beliebigen Ausbaustand durchspielen: Was wäre gewesen, wenn schon doppelt so
viel Wind gestanden hätte?

## Starten

```bash
cd website
./run.sh
```

Das Skript legt beim ersten Aufruf eine virtuelle Umgebung an, installiert die
Abhängigkeiten und startet den Server auf <http://127.0.0.1:8000>.
Abhängigkeiten sind nur FastAPI, Uvicorn und Jinja2 — das Modell rechnet mit der
Python-Standardbibliothek, das Frontend kommt ohne externe Bibliothek aus.

## Tests

```bash
cd website
./test.sh
```

Geprüft werden die Zusammenhänge, die die Erklärseiten behaupten: Energiebilanz,
Preisbildung nach dem Einheitspreisverfahren, der Merit-Order-Effekt und die
Umschlagpunkte, an denen der CO₂-Preis Kohle und Gas vertauscht. Sind diese
Werte im Kraftwerkspark verändert worden, fallen die Tests — und erinnern daran,
die Hinweistexte mitzuziehen.

Die Tests laufen in drei Stufen, jede für sich lauffähig:

| Stufe | Braucht | Prüft |
|---|---|---|
| Modelltests | nur Python, `unittest` aus der Standardbibliothek | Merit-Order, Dispatch, Profile, Datenschicht |
| HTTP-Tests | zusätzlich FastAPI und httpx | Seiten, Endpunkte, Fehlerverhalten |
| Frontend | Node mit jsdom | Formularlogik, Sichtbarkeit, Herkunftszeile |

Fehlt eine Voraussetzung, überspringt `test.sh` die Stufe mit einem Hinweis,
statt zu scheitern. Kein Test geht ins Netz oder rührt den abgerufenen Bestand
an: Die Datenschicht wird gegen eine Wegwerf-Datenbank im Temp-Verzeichnis
geprüft.

Beide Testabhängigkeiten sind reine Entwicklungswerkzeuge — die Anwendung
selbst kommt weiter mit FastAPI, Uvicorn und Jinja2 aus und lädt im Browser
keine einzige externe Bibliothek:

```bash
backend/.venv/bin/pip install -r backend/requirements-dev.txt   # httpx
npm install                                                      # jsdom
```

Die Frontend-Tests laufen gegen die Seite, die das Template wirklich
ausliefert: `test.sh` rendert sie vor jedem Lauf neu, damit die Vorlage nicht
hinter dem Template zurückbleibt. Damit das prüfbar ist, liegt die Logik der
Analyseseite in `scenario.js` getrennt von ihrer Verdrahtung in `analysis.js` —
nichts darin greift beim Laden auf das Dokument zu.

## Messwerte holen

Ohne Daten läuft alles — dann stehen nur die erzeugten Profile zur Verfügung.
Für die echten Messwerte holt ein eigenes Programm die Zeitreihen von SMARD in
eine lokale SQLite-Datei:

```bash
cd website
backend/.venv/bin/python -m backend.data.ingest --weeks 52   # ein Jahr aufbauen
backend/.venv/bin/python -m backend.data.ingest --status     # zeigen, was vorliegt
backend/.venv/bin/python -m backend.data.ingest              # laufend aktualisieren
```

Bereits geholte Wochen werden übersprungen, die drei jüngsten immer erneuert —
SMARD liefert zunächst vorläufige Werte und bessert sie nach. Ein voller
Jahresaufbau dauert einige Minuten, die tägliche Aktualisierung wenige Sekunden.

Der Abruf läuft bewusst außerhalb des Webservers. Der liest nur, und die Seite
bleibt bedienbar, wenn SMARD gerade nicht antwortet. Auf einem Dauerläufer wie
einem Raspberry Pi übernimmt cron das Nachladen:

```cron
15 6 * * * cd /pfad/zu/website && backend/.venv/bin/python -m backend.data.ingest --quiet
```

Der Ort der Datenbank lässt sich über die Umgebungsvariable `ENERGIEWENDE_DB`
umlegen, etwa von der SD-Karte auf eine SSD. Die Datei wächst langsam: Ein Jahr
mit fünf Zeitreihen belegt wenige Megabyte. Sie gehört nicht ins Repository und
ist deshalb ignoriert.

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
| `GET /api/data/status` | welche Messwerte lokal vorliegen |
| `GET /api/glossary` | Glossareinträge als JSON |
| `GET /health` | Statusabfrage |

Parameter von `/api/simulate`: `wind_gw`, `solar_gw`, `co2_price`, `gas_price`,
`peak_load_gw`, `hours`, `source` (`synthetic` oder `historical`), dazu
`season` (`winter`, `uebergang`, `sommer`) bei erzeugten Profilen und `start`
(`JJJJ-MM-TT`) bei Messwerten. Werte außerhalb der zulässigen Bereiche werden
begrenzt, nicht abgelehnt. Das eingestellte Szenario steht auf der Analyseseite
in der Adresszeile und ist damit teilbar.

Bei `source=historical` gilt die gemessene Last. Nur wenn `peak_load_gw`
ausdrücklich mitgegeben wird, streckt das Modell die gemessene Kurve auf diesen
Wert — damit lässt sich zusätzlicher Verbrauch durch Wärmepumpen und E-Autos
durchspielen. Wird der gewünschte Zeitraum verschoben oder gekürzt, weil so
weit keine Messwerte vorliegen, steht das unter `params.adjustments` in der
Antwort. Fehlen die Messwerte ganz, antwortet der Endpunkt mit Status 409 und
einem Hinweis — und nicht etwa still mit erzeugten Profilen.

## Das Modell

Grenzkosten je Kraftwerksblock ergeben sich aus Brennstoffpreis geteilt durch
Wirkungsgrad, CO₂-Kosten (Emissionsfaktor × Zertifikatspreis / Wirkungsgrad) und
variablen Betriebskosten. Die Last wird Stunde für Stunde von den günstigsten
verfügbaren Blöcken gedeckt; der Preis ist der des letzten benötigten Blocks.
Muss erneuerbare Leistung abgeregelt werden, fällt der Preis unter null.

Nicht abgebildet: Speicher, Import und Export, Mindestlasten, An- und Abfahrkosten,
Kraft-Wärme-Kopplung, Netzengpässe. Die Ergebnisse sind Größenordnungen zum
Verstehen der Mechanik — keine Prognose.

### Woher die Zeitreihen kommen

Das Modell rechnet nicht mit fertiger Einspeisung, sondern mit einer Lastkurve
und Kapazitätsfaktoren. Dahinter stehen zwei austauschbare Quellen:

| Quelle | Herkunft | Wozu |
|---|---|---|
| `synthetic` | nachgebildete Tagesgänge, AR(1)-Windprozess | immer verfügbar, drei Jahreszeiten, reproduzierbar |
| `historical` | SMARD-Messwerte aus der lokalen Datenbank | echtes Wetter, echte Last, echte Preise zum Vergleich |

Der Kapazitätsfaktor ist gemessene Einspeisung geteilt durch die installierte
Leistung zum jeweiligen Zeitpunkt. Weil laufend zugebaut wird, ist dieser Nenner
keine Konstante: 2015 standen 38 GW Wind an Land, 2026 sind es 71 GW. Die
Jahreswerte liegen in `static_data/installed_capacity.json`, zwischen den
Jahresenden wird linear interpoliert.

Aus dieser Trennung folgt die eigentlich interessante Möglichkeit: Derselbe
gemessene Zeitraum lässt sich mit beliebiger installierter Leistung
durchrechnen. Das Wetter bleibt echt, der Ausbaustand wird hypothetisch.

Wind an Land und auf See werden zu einem Kapazitätsfaktor zusammengefasst,
gewichtet nach installierter Leistung — das Modell kennt nur einen Wind-Regler.
Fehlende Stundenwerte werden bis zu drei Stunden linear überbrückt und im
Ergebnis vermerkt; längere Ausfälle führen zu einer Fehlermeldung statt zu
erfundenen Messwerten.

### Zeitrechnung

Alle Zeitpunkte im Modell sind UTC: in der Datenbank als Unix-Sekunden, in der
API als ISO-Zeitstempel mit `+00:00`. Das ist die einzige Darstellung, die
eindeutig bleibt, wenn eines Tages weitere Länder danebenstehen.

Eine Ortszeit wird trotzdem gebraucht, aber nur an zwei Stellen: für die
Anzeige — die Mittagsspitze der Photovoltaik gehört auf 13 Uhr, nicht auf
11 Uhr UTC — und für die erzeugten Tagesgänge, die einem lokalen Rhythmus
folgen. Deshalb liefert die API die Zeitzone als eigenes Feld
`display_timezone` mit, statt sie im Code festzuschreiben. Die Diagramme
formatieren damit; ein Betrachter in Kalifornien sieht denselben Tagesgang wie
einer in Bielefeld.

Die Regionseinstellungen stehen in `backend/region.py` — Zeitzone, SMARD-Kürzel
und Währung an einer Stelle, vorbereitet für weitere Länder.

## Datenquellen

| Was | Quelle | Hinweis |
|---|---|---|
| Last, Einspeisung, Großhandelspreis | [SMARD](https://www.smard.de), Bundesnetzagentur | Quelle bei Weitergabe nennen |
| Installierte Leistung | [Energy-Charts](https://energy-charts.info), Fraunhofer ISE | einmal jährlich nachpflegen |

Beide Quellen sind öffentlich zugänglich und für die Weiterverwendung gedacht.
Die genauen Nutzungsbedingungen sind vor einer kommerziellen Verwendung zu
prüfen; die Quellenangabe gehört ohnehin sichtbar in die Oberfläche und steht
dort bei jedem Szenario mit echten Messwerten.

## Aufbau

```
website/
├── run.sh                      Startskript
├── test.sh                     Testlauf
├── tests/                      Modell- und HTTP-Tests (unittest)
├── backend/
│   ├── __init__.py             backend ist ein Paket — Importe ohne cwd-Trick
│   ├── main.py                 Routen und API
│   ├── analysis.py             Merit-Order und Dispatch
│   ├── region.py               Zeitzone und Regionsschlüssel
│   ├── data/
│   │   ├── sources.py          Zeitreihenquellen: erzeugt oder gemessen
│   │   ├── smard.py            Abruf von SMARD, nur Standardbibliothek
│   │   ├── store.py            lokaler Speicher (SQLite)
│   │   ├── capacity.py         installierte Leistung je Zeitpunkt
│   │   └── ingest.py           Abrufprogramm für cron
│   ├── utils/profiles.py       erzeugte Last-, Wind- und PV-Profile
│   ├── static_data/            Kraftwerkspark, Glossar, installierte Leistung
│   ├── data_store/             abgerufene Messwerte (nicht versioniert)
│   ├── requirements.txt        Laufzeit: FastAPI, Uvicorn, Jinja2
│   └── requirements-dev.txt    zusätzlich httpx für die HTTP-Tests
├── package.json                nur für die Frontend-Tests (jsdom)
└── frontend/
    ├── templates/              Jinja2-Templates
    └── static/
        ├── css/style.css       Designtokens, hell und dunkel
        └── js/
            ├── charts.js       SVG-Diagramme (Linie, Fläche, Blöcke)
            ├── scenario.js     Logik der Analyseseite, ohne DOM-Zugriff
            ├── analysis.js     Verdrahtung der Simulator-Seite
            ├── explain.js      Diagramme der Erklärseiten
            └── glossary.js     Glossarsuche
```

### Diagrammfarben

Die Serienfarben liegen als CSS-Variablen in `style.css` und folgen automatisch
dem Hell-/Dunkel-Modus. Die Zuordnung ist gegen Farbfehlsichtigkeit geprüft —
benachbarte Kategorien halten in beiden Modi genügend Abstand. Wer die Farben
ändert, sollte das erneut prüfen und nicht nur nach Augenmaß entscheiden.
