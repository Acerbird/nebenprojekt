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

Die Antwort von `/api/simulate` enthält zusätzlich `storage` (Lade- und
Entladeplan je Anlage samt Zyklen und Preisschwellen) sowie bei echten
Messwerten `validation` mit dem Vergleich zum tatsächlichen Börsenpreis und
`forecasts` mit den vorberechneten Vorhersagen samt eigener Fehlerkennzahlen.

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
variablen Betriebskosten. Die Last wird Stunde für Stunde gedeckt; der Preis ist
der des letzten benötigten Blocks.

### Gebotsspannen statt Treppenstufen

Ein Kraftwerkspark besteht nicht aus einem Block je Technologie, sondern aus
vielen Anlagen unterschiedlichen Alters. Deshalb bietet jede Technologie in
einer **Spanne** an: Der modernste Gasblock bietet zum unteren Preis, der
älteste zum oberen. Aus der Treppenstufe wird eine Rampe, und aus der
Merit-Order eine Kurve statt einer Treppe.

Bei Anlagen ohne Brennstoffkosten beschreibt die Spanne kein Wirkungsgrad-,
sondern ein Gebotsverhalten: Wer Einspeisevergütung bekommt, bietet auch bei
deutlich negativen Preisen noch an, statt abzuschalten — Photovoltaik und
Laufwasser bis −500 €/MWh, Biomasse bis −200, Wind bis −70.

Gesucht wird dann der Preis, bei dem das gesamte Angebot die Nachfrage deckt
(Intervallhalbierung über die Angebotskurve). Mehrere Dinge, die vorher
Sonderfälle mit festen Konstanten waren, ergeben sich daraus von selbst:

* Biomasse und Laufwasser laufen bei negativen Preisen weiter, weil ihr Gebot
  so weit hinunterreicht.
* Bei Überschuss fällt der Preis in den Gebotsbereich von Wind und
  Photovoltaik, und genau der überzählige Teil wird abgeregelt.
* Je größer der Überschuss, desto tiefer der Preis — ohne dass irgendwo ein
  fester Wert dafür hinterlegt wäre.

Der Unterschied ist messbar: Über eine Woche entstehen so rund 160 verschiedene
Preise statt vier, und die Korrelation mit dem tatsächlichen Börsenpreis steigt
von 0,64 auf 0,68.

Die Wirkungsgrad-Bandbreiten und Gebotsuntergrenzen stammen aus `tech_params.csv`
des Forschungsprojekts *Forecasting Electricity Prices* (S. Hellbusch).

### Speicher

Batterien und Pumpspeicher laden in den billigsten Stunden und entladen in den
teuersten — eine nachvollziehbare Faustregel statt einer Optimierung, aber genau
so verdienen Speicherbetreiber am Markt ihr Geld. Zwei Dinge begrenzen den
Einsatz: der Wirkungsgrad (wer 100 MWh einspeichert und 88 zurückbekommt,
braucht einen Preisabstand, der den Verlust deckt) und der Vorrat (ein
Batteriespeicher mit 1,6 Stunden Volllast überbrückt einen Abend, keine
Dunkelflaute). Ist die Spreizung zu klein, bleibt der Speicher stehen — auch das
ist eine Aussage über das Stromsystem.

Weil Speicher Stunden miteinander verknüpft, rechnet das Modell zweimal: erst
die Preise ohne ihn als Entscheidungsgrundlage, dann den Einsatz mit ihm. Die
Vereinfachung dabei: Der Speicher plant anhand der Preise, die ohne ihn
entstanden wären, und sieht seine eigene Wirkung nicht voraus. Er startet leer
und kann deshalb nur abgeben, was er im betrachteten Zeitraum aufgenommen hat.

### Prüfstein: Modell gegen Wirklichkeit

Bei echten Messwerten stellt die Analyseseite den gerechneten Preis neben den
tatsächlich gezahlten Day-Ahead-Preis und weist drei Kennzahlen aus: die
mittlere Abweichung, die Verzerrung (rechnet das Modell systematisch zu hoch
oder zu tief?) und die Korrelation.

Das Ergebnis ist unbequem und genau deshalb lehrreich. Für eine Septemberwoche
2026 liegt die Korrelation bei etwa 0,68 — der Verlauf stimmt also grob —,
während das Preisniveau um rund 99 €/MWh zu niedrig herauskommt. Ein höherer
Gaspreis schließt die Lücke nicht, sondern verschlechtert die Korrelation. Der
Grund liegt tiefer: Der hinterlegte Kraftwerkspark ist zu groß und zu billig,
und Knappheitsaufschläge kennt das Modell nicht. Wer das Modell verbessern will,
hat hier eine messbare Zielgröße.

Nicht abgebildet: Import und Export, Mindestlasten thermischer Blöcke, An- und
Abfahrkosten, Kraft-Wärme-Kopplung, Netzengpässe. Die Ergebnisse sind
Größenordnungen zum Verstehen der Mechanik — keine Prognose.

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

## Preisvorhersage

Neben dem Merit-Order-Modell stehen drei eigenständige Vorhersagemodelle zur
Verfügung, die auf der Analyseseite als **Forecast-Modell 1** bis **3**
erscheinen, dazu eine einfache Regel als Vergleichsmaßstab. Sie stammen aus einem getrennten Forschungsprojekt und werden hier
nur benutzt; ihre Funktionsweise gehört nicht in die Oberfläche.

Sie laufen **außerhalb der Website**, im Ordner `forecasting/` mit eigener
virtueller Umgebung. Das ist keine Sparsamkeit um ihrer selbst willen: Die
Modelle brauchen pandas, numpy, scipy, statsmodels und scikit-learn, und
Modell 2 rechnet rund zwei Minuten für einen einzigen Tag. Zur Laufzeit einer
Webseite ist das unmöglich — einmal vorberechnet ist es sofort da. Die Website
liest nur das Ergebnis aus der Datenbank und bleibt bei FastAPI, Uvicorn und
Jinja2.

```bash
cd website
/usr/bin/python3 -m venv forecasting/.venv
forecasting/.venv/bin/pip install -r forecasting/requirements.txt

forecasting/.venv/bin/python -m forecasting.run_forecast --model 1 --from 2025-06-01 --days 30
forecasting/.venv/bin/python -m forecasting.run_forecast --status
```

Die Paketversionen sind auf den Stand des Forschungsprojekts festgelegt, damit
hier dieselben Zahlen herauskommen wie dort. Vor langen Läufen fragt das
Programm nach — ein Jahr mit Modell 2 wären rund zwölf Stunden.

Vorliegende Ergebnisse lassen sich auch direkt übernehmen, statt sie erneut zu
rechnen:

```bash
backend/.venv/bin/python -m forecasting.import_results forecasting/data/*_forecast.csv
```

Diese Dateien enthalten für jede Stunde den tatsächlichen Preis und die
Vorhersage jedes Modells. Drei Jahre so zu übernehmen dauert Sekunden — sie
selbst zu rechnen wären mit Modell 2 rund anderthalb Tage.

Liegen für einen Zeitraum Vorhersagen vor, zeigt die Analyseseite sie als
weitere Linien neben dem Modellpreis und stellt alle Modelle in einer Tabelle
gegenüber. Neben den drei Vorhersagemodellen läuft eine **einfache Regel** mit —
derselbe Wochentag der Vorwoche. Sie ist der Maßstab, den ein Modell schlagen
muss, und sie schlägt ihrerseits das Merit-Order-Modell deutlich. Genau das ist
der Gewinn des Vergleichs: Er zeigt, wo ein Modell steht, das den
Kraftwerkseinsatz nachrechnet, verglichen mit Modellen, die aus der
Vergangenheit lernen.

### Zwei Preisreihen, ein Maßstab

Die Vorhersagemodelle wurden auf einer anderen Preisreihe entwickelt, als SMARD
liefert — und das ist kein Fehler auf einer der beiden Seiten:

* **SMARD** weist den **Stundenkontrakt** der Day-Ahead-Auktion aus. Fragt man
  dort Viertelstundenauflösung ab, wird derselbe Stundenwert viermal wiederholt.
* Die **Referenzreihe** der Modelle ist das **Mittel der vier viertelstündlichen
  Day-Ahead-Preise** einer Stunde, wie ENTSO-E sie für die Gebotszone DE/LU
  ausweist. Innerhalb einer Stunde laufen diese vier Werte im Mittel um rund
  45 €/MWh auseinander, in praktisch jeder Stunde seit 2018.

Ihr Mittel ist deshalb nicht der Stundenpreis. Beide Reihen korrelieren mit etwa
0,96, weichen je Stunde aber um durchschnittlich 9 €/MWh ab.

Für die Bewertung ist das erheblich: Ein Modell an einer anderen Reihe zu
messen, als es vorhersagt, lastet ihm einen Fehler an, den es nicht gemacht
hat. Deshalb gilt: Liegen Vorhersagen vor, werden **alle** Modelle an deren
Referenzreihe gemessen — auch das Merit-Order-Modell. Sonst bleibt es beim
SMARD-Preis. Welcher Maßstab gerade gilt, steht unter der Vergleichstabelle;
die gezeichnete Preiskurve bleibt in jedem Fall die von SMARD.

Das Import-Programm prüft diesen Abgleich bei jedem Lauf und warnt, wenn die
mitgelieferten Preise nicht zu den eigenen Messwerten passen.

> **Vor einer Veröffentlichung zu klären.** Die Trainingsdaten unter
> `forecasting/data/` enthalten lizenzierte Commodity-Preise (Refinitiv/LSEG)
> und liegen deshalb nicht im Repository. Ob der Modellcode selbst öffentlich
> werden darf, hängt außerdem am laufenden Publikationsverfahren des
> Forschungsprojekts. Beides ist offen und muss entschieden sein, bevor diese
> Seite online geht.

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
├── forecasting/                Preisvorhersage, getrennt von der Website
│   ├── run_forecast.py         rechnet Vorhersagen in die Datenbank
│   ├── import_results.py       übernimmt vorliegende Ergebnisse (nur Stdlib)
│   ├── config.py               Einstellungen beider Modelle
│   ├── models/, utils/         die Modelle selbst
│   ├── requirements.txt        pandas, numpy, scipy, statsmodels, scikit-learn
│   └── data/                   Trainingsdaten (nicht versioniert)
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

Das Prüfverfahren für die zuletzt ergänzte Kategorie „Speicher": sRGB in den
LMS-Raum umrechnen, die drei Dichromasien simulieren, in CIELAB zurückrechnen
und die paarweisen Abstände messen. Gewählt wurde die Kandidatenfarbe mit dem
größten *kleinsten* Abstand zu allen bestehenden Farben — in beiden Modi und
unter allen drei Sehschwächen. Der erreichte Wert liegt über dem kleinsten
Abstand, den die bisherigen Farben untereinander haben, verschlechtert die Lage
also nicht.
