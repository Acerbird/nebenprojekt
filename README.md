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
| `/speicher` | Pumpspeicher, Batterien, flexibler Verbrauch und ihre Grenzen |
| `/maerkte` | Day-Ahead, Intraday, Regelleistung und die Viertelstunde |
| `/handel` | Import, Export, Marktkopplung und Kuppelkapazität |
| `/analysen` | Simulator mit Merit-Order, Erzeugungsmix und Preisverlauf |
| `/glossar` | 30 Begriffe mit Sofortsuche und Querverweisen |
| `/docs` | automatisch erzeugte API-Dokumentation |

## API

| Endpunkt | Zweck |
|---|---|
| `GET /api/simulate` | stündlicher Einsatz, Preise und Kennzahlen |
| `GET /api/merit-order` | Merit-Order-Kurve für die übergebenen Parameter |
| `GET /api/profiles/day` | Tagesgänge von Last und Photovoltaik |
| `GET /api/data/status` | welche Messwerte lokal vorliegen |
| `GET /api/exchange-curve` | gemessene Außenhandelskurve für die Erklärseite |
| `GET /api/quarter-prices` | Stunden- und Viertelstundenpreis eines Tages |
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

### Mindestlast: real, und trotzdem abgeschaltet

Ein Braunkohleblock lässt sich nicht beliebig weit herunterfahren, und ihn ganz
abzustellen kostet Stunden und Geld. Solange der Verlust je Stunde kleiner ist
als ein Neustart, bleibt er im Markt und nimmt dafür auch einen negativen Preis
hin. Genau daraus entstehen die Stunden, in denen der Börsenpreis unter null
fällt und trotzdem Kohle im Netz ist.

Im Modell bietet deshalb ein Teil jedes thermischen Blocks getrennt an, zu
−80 bis −20 €/MWh statt zu seinen Grenzkosten. Wie groß dieser Teil ist, ist
nicht geschätzt, sondern an SMARD gemessen — in den 1.802 Stunden mit negativem
Day-Ahead-Preis zwischen Dezember 2022 und September 2026:

| Technologie | Erzeugung im Median | Parkgröße | Anteil |
|---|---|---|---|
| Braunkohle | 3,18 GW | 15 GW | 0,21 |
| Steinkohle | 0,90 GW | 13 GW | 0,07 |
| Erdgas | 2,09 GW | 20 GW (GuD) | 0,10 |

Bei Erdgas hängt der Rest vor allem an der Fernwärmeauskopplung, deshalb ist er
dem GuD-Park zugeordnet und nicht den Gasturbinen: Die lassen sich in Minuten
starten und haben keinen Grund durchzulaufen.

Unterhalb von −80 €/MWh lohnt auch das Durchhalten nicht mehr, dann fährt auch
die Mindestlast ab. Ohne diese Grenze liefe die Kohle im Modell bis −500 weiter.

**Und dann wird die Gesamtkennzahl damit schlechter.** Über 36 Wochen quer
durch drei Jahre, bei sonst gleichem Modell:

| | MAE | Korrelation | Verzerrung |
|---|---|---|---|
| mit Mindestlast | 23,92 | 0,677 | −10,40 |
| ohne | **19,94** | **0,746** | **−1,64** |

Diese eine Zahl verdeckt allerdings, was tatsächlich passiert. Teilt man
denselben Vergleich nach dem tatsächlich gezahlten Preis auf, kehrt sich das
Urteil in der Hälfte der Fälle um — hier die Verzerrung je Preisklasse:

| Ist-Preis (€/MWh) | Stunden | ohne Mindestlast | mit Mindestlast |
|---|---|---|---|
| unter 0 | 294 | +22,7 | **+5,0** |
| 0 bis 30 | 398 | +31,2 | **+16,3** |
| 30 bis 60 | 427 | +17,7 | **+1,2** |
| 60 bis 90 | 1.707 | +6,9 | **−2,5** |
| 90 bis 130 | 2.336 | **−5,4** | −11,1 |
| über 130 | 886 | **−40,3** | −46,5 |

Die Mindestlast wirkt also genau dort richtig, wo sie hingehört: In den billigen
Stunden schrumpft der Fehler auf ein Viertel bis ein Fünfzehntel. Sie verliert
in der Gesamtkennzahl nur deshalb, weil mehr als die Hälfte aller Stunden über
90 €/MWh liegt — und dort rechnet das Modell ohnehin zu billig. Die Mindestlast
verursacht diesen zweiten Fehler nicht, sie verstärkt ihn.

Drei Erklärungen für den Fehler am teuren Ende sind geprüft und widerlegt:

* **Doppelzählung mit den Geboten der Erneuerbaren.** Beide erklären, warum der
  Preis im Überschuss nicht ins Bodenlose fällt. Hebt man die EE-Gebote von
  −500 auf −60 an, ändert sich fast nichts.
* **Der hinterlegte Park sei zu groß.** Stutzt man ihn auf die gemessene
  Erzeugung, wird alles deutlich schlechter (MAE 23,1 bis 42,7). Der Grund ist
  ein Denkfehler, der sich lohnt zu merken: Die gemessene Erzeugung zeigt, was
  gelaufen ist, nicht, was bereitstand. Ein Kraftwerk, das der Markt nicht
  braucht, steht trotzdem da. Auch gezieltes Kürzen allein am teuren Ende hilft
  nicht — die Gasturbinen von 11 auf 5 GW verschlechtern den MAE von 19,94 auf
  21,00.
* **Der Knappheitsaufschlag sei zu schwach.** Über ein Raster aus Schwelle
  (0,40 bis 0,70) und Höhe (120 bis 300 €/MWh) gesucht: Jede Verstärkung
  verschlechtert MAE und Gleichlauf, mit wie ohne Mindestlast.

Was in den teuersten Stunden fehlt, liegt demnach nicht am deutschen Park. Dort
laufen real nur 33 bis 37 der rund 45 gemessenen Gigawatt, während der Preis
schon bei 300 €/MWh steht — die Knappheit ist europäisch und liegt im
Bietverhalten, und beides holt dieses Modell nicht ein.

Deshalb bleibt die Mindestlast ein Schalter (Kästchen im Formular,
`min_load=true` an der API) und bleibt standardmäßig aus: Für die Voreinstellung
zählt die Gesamtkennzahl. Wer negative Preise verstehen will, schaltet sie ein —
dafür ist sie da.

### Außenhandel: die Ursache nicht umdrehen

Deutschland ist keine Insel, und der erste Versuch, das abzubilden, ging
gründlich schief — auf eine lehrreiche Weise. Naheliegend war, den von SMARD
gemessenen Nettoexport als zusätzliche Nachfrage einzusetzen. Das Ergebnis war
deutlich schlechter als vorher, und der Grund ist die Kausalität: Mittags
exportiert Deutschland zwölf Gigawatt, **weil** der Preis bei −10 €/MWh liegt.
Rechnet man diese zwölf Gigawatt als Nachfrage hinein, verschwindet der
Überschuss, und das Modell sagt +80 statt −10.

Richtig ist der Außenhandel eine **preisabhängige Nachfrage**. Die Kurve dafür
ist gemessen, nicht angenommen: medianer Nettoexport je Preisklasse über 32.711
Stunden (SMARD-Filter 4629 gegen den Day-Ahead-Preis derselben Stunde).

| Preis (€/MWh) | medianer Nettoexport |
|---|---|
| unter −20 | +9,4 GW |
| −20 bis 0 | +7,9 GW |
| 0 bis 20 | +5,3 GW |
| 20 bis 40 | +2,4 GW |
| 40 bis 60 | +0,6 GW |
| 60 bis 80 | −2,1 GW |
| 80 bis 100 | −3,7 GW |
| 100 bis 150 | −4,6 GW |
| über 150 | −6,9 GW |

Über den ganzen Bereich monoton fallend. Damit wirkt der Handel in beide
Richtungen als Puffer: Im Überschuss saugt der Export ihn ab, statt den Preis
ins Bodenlose fallen zu lassen; in der Knappheit entlastet der Import den
heimischen Park. Die Markträumung sucht jetzt den Schnittpunkt zweier Kurven —
das Angebot steigt mit dem Preis, die Nachfrage fällt.

### Knappheitsaufschlag

Ein reines Grenzkostenmodell nimmt an, dass jedes Kraftwerk zu seinen variablen
Kosten bietet. Das stimmt, solange reichlich Leistung da ist. Wird es eng, weiß
der letzte verfügbare Block, dass ohne ihn niemand liefert — und bietet darüber.

Auch hier zuerst gemessen. Über den Prüfsatz quer durch 2023 bis 2025, sortiert
nach dem tatsächlichen Preis:

| Ist-Preis (€/MWh) | mittlere Reserve | Modell ohne Aufschlag |
|---|---|---|
| unter 0 | 59 % | −13 |
| 60 bis 90 | 48 % | 74 |
| 90 bis 130 | 40 % | 88 |
| 130 bis 200 | 31 % | 95 |
| über 200 | 24 % | 106 |

Die Reserve fällt selbst in den teuersten Stunden nie unter ein Fünftel. Eine
Schwelle von zehn Prozent, wie sie zunächst naheliegt, hätte also nie gegriffen.
Gewählt sind 40 % Schwelle und bis zu 120 €/MWh Aufschlag, linear mit der Enge
wachsend — auf 2023/24 gesucht, an 2025 geprüft und über alle 36 Wochen
gegengerechnet:

| Schwelle / Höhe | MAE | Korrelation | Verzerrung |
|---|---|---|---|
| ohne Aufschlag | 21,33 | 0,705 | −7,03 |
| **0,40 / 120** | **19,94** | 0,746 | **−1,64** |
| 0,35 / 180 | 20,05 | **0,759** | −2,73 |
| 0,45 / 120 | 20,58 | 0,734 | +1,89 |

Mehr Aufschlag ist nicht besser: Über ein Raster bis 0,70 Schwelle und 300 €/MWh
gesucht, verschlechtert jede weitere Verstärkung beide Kennzahlen.

Der Aufschlag steckt **in** der Markträumung, nicht dahinter. Würde er erst auf
den geräumten Preis aufgeschlagen, gehörte die Handelsmenge zu einem anderen
Preis als dem am Ende ausgewiesenen. Und er verändert nur den Zuschlagspreis,
nicht den Einsatz: Wer läuft, entscheidet weiter der Preis ohne Aufschlag, denn
der Aufschlag ist Knappheitsrente, kein Kostenblock.

### Brennstoffpreise des jeweiligen Monats

Ein fester Gaspreis von 32 €/MWh ist für einen Regler richtig — wer wissen will,
was ein CO₂-Preis von 150 Euro anrichtet, soll ihn einstellen können. Für den
Vergleich mit echten Preisen ist er falsch: Im Frühjahr 2023 kostete Gas das
Doppelte, und das Modell rechnete entsprechend zu billig.

Bei echten Messwerten gelten deshalb die Preise des jeweiligen Monats. Sie
stehen in `static_data/fuel_prices.json` und werden außerhalb der Website
gebaut:

    python -m backend.data.fuel_ingest          Tabelle neu bauen
    python -m backend.data.fuel_ingest --show   zeigen, was vorliegt

Drei Quellen, alle ohne Anmeldung und ohne Lizenzvorbehalt:

| Größe | Quelle |
|---|---|
| CO₂ | EEX, Zuschlagspreise der Auktionen im europäischen Emissionshandel |
| Gas | Weltbank *Pink Sheet*, Reihe „Natural gas, Europe" (TTF), in USD/mmbtu |
| Kohle | Weltbank ebenda, Reihe „Coal, South African", in USD/t |
| Wechselkurs | EZB, Referenzkurs USD/EUR |

Die XLSX-Dateien werden mit der Standardbibliothek gelesen — ein XLSX ist ein
ZIP-Archiv mit XML darin, dafür braucht es keine Fremdbibliothek.

Wie gut treffen die freien Quellen? Gegengeprüft an einer lizenzpflichtigen
Terminpreisreihe, 72 Monate:

| Größe | Korrelation | mittlere Abweichung |
|---|---|---|
| Gas | 0,9999 | 0,45 €/MWh |
| CO₂ | 0,9995 | 0,45 €/t |
| Kohle | 0,972 | 2,08 €/MWh_th |

Bei Gas und CO₂ ist der Unterschied also vernachlässigbar. Bei Kohle nicht ganz:
Südafrikanische Kohle ist ein Näherungswert für den europäischen Importpreis,
Rotterdam (API 2) ist nicht frei zu haben.

Braunkohle steht nicht in der Tabelle. Sie wird im Tagebau neben dem Kraftwerk
gefördert und nicht gehandelt, ihr Preis ist keine Marktgröße und bleibt beim
festen Wert.

Ein bewegter Regler gilt immer vor dem gemessenen Wert — sonst ließe sich keine
Was-wäre-wenn-Frage mehr stellen. Damit das Backend beides unterscheiden kann,
schickt das Formular CO₂- und Gaspreis **gar nicht** mit, solange das Kästchen
„stattdessen die Preise des Zeitraums verwenden" gesetzt ist. Ein mitgesendeter
Wert ist von einer Eingabe nicht zu unterscheiden und würde die Monatswerte
stillschweigend verdrängen.

Womit gerechnet wurde, steht in der Herkunftszeile unter dem Formular — und
zwar je Größe getrennt: Ein Reglerwert wird dort nicht als „Preis des Zeitraums"
ausgegeben.

Die beiden Quellen hinken unterschiedlich weit hinterher. Die CO₂-Auktionen
laufen wöchentlich, die Weltbank-Tabelle erscheint mit einigen Monaten Verzug.
Für den jüngsten Monat fehlt deshalb oft der Gaspreis — ausgerechnet für den
Zeitraum, den die Seite ohne Zutun zeigt. Ein fehlender Wert wird daher aus dem
jüngsten davorliegenden Monat fortgeschrieben, höchstens drei Monate weit und
immer sichtbar vermerkt. Ohne diese Fortschreibung stünde dort der Vorgabewert
von 32 €/MWh, während Gas zuletzt bei 62 lag.

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

Als Maßstab dient ein fester Prüfsatz: **36 Wochen, jeweils der 6. eines Monats
von Januar 2023 bis Dezember 2025**, je 168 Stunden — also jeder Monat dreier
Jahre, keine Auswahl nach Eignung. Jede Zahl dieses Abschnitts kommt aus einem
Programm und lässt sich nachrechnen:

```bash
cd website
backend/.venv/bin/python -m backend.benchmark              # die Tabelle unten
backend/.venv/bin/python -m backend.benchmark --ablation   # jeden Baustein weglassen
backend/.venv/bin/python -m backend.benchmark --klassen    # aufgeteilt nach Ist-Preis
```

Eine Woche, die nicht vollständig vorliegt, zählt dabei nicht mit. Das Modell
würde sie an den Rand des Bestands rücken — zwei solche Wochen wären dieselbe,
und der Prüfsatz zählte sie doppelt, ohne dass es an den Zahlen zu sehen wäre.

Stand heute:

| Jahr | MAE | Korrelation | Verzerrung |
|---|---|---|---|
| 2023 | 16,04 | 0,817 | −1,52 |
| 2024 | 23,47 | 0,720 | −0,11 |
| 2025 | 20,30 | 0,768 | −3,29 |
| **gesamt** | **19,94** | **0,746** | **−1,64** |

Zum Vergleich: Ohne Außenhandel, Monatspreise und Knappheitsaufschlag liegen
dieselben 36 Wochen bei MAE 24,06, Korrelation 0,654 und einer Verzerrung von
−8,03. Der Fehler ist also um ein Sechstel gesunken, der Gleichlauf deutlich
gestiegen, und die systematische Unterschätzung ist praktisch verschwunden.

Ein Schritt dorthin war kein Modellbaustein, sondern eine veraltete Zahl: Die
Voreinstellung von 90 GW Photovoltaik stammte aus einer Momentaufnahme, im
Januar 2025 standen aber 101 GW. Seit unberührte Regler bei echten Messwerten
den tatsächlichen Ausbaustand meinen (siehe unten), rechnet das Modell mit dem
Park des jeweiligen Zeitraums statt mit einem festen.

Was jedes Stück beiträgt, jeweils weggelassen aus dem Modell in seiner
Voreinstellung:

| weggelassen | MAE | Korrelation | Verzerrung |
|---|---|---|---|
| nichts (Voreinstellung) | **19,94** | **0,746** | **−1,64** |
| Außenhandel | 23,11 | 0,739 | +3,54 |
| Monatspreise | 22,54 | 0,720 | −1,98 |
| echter Ausbaustand | 21,49 | 0,719 | −1,01 |
| Knappheitsaufschlag | 21,33 | 0,705 | −7,03 |
| *zusätzlich* Mindestlast | 23,92 | 0,677 | −10,40 |

Weggelassen heißt hier jeweils: dieses eine Stück aus dem vollständigen Modell
entfernt, nicht nacheinander abgebaut. Eine Kettenablation würde das zweite
Stück an einem Modell messen, dem schon das erste fehlt, und Wechselwirkungen
verstecken.

Der Außenhandel trägt am meisten; die Mindestlast verschlechtert die
Gesamtkennzahl und verbessert trotzdem die billigen Stunden (siehe oben).

Die früheren Zahlen dieses Abschnitts beruhten auf 24 der 36 Wochen. Der
Prüfsatz ist bewusst auf alle Monate erweitert worden: Ein Maßstab, bei dem ein
Drittel der Zeiträume fehlt, lädt dazu ein, unbemerkt die bequemen behalten zu
haben.

Nicht abgebildet: An- und Abfahrkosten im Einzelnen, Kraft-Wärme-Kopplung,
Reservemärkte, Netzengpässe innerhalb Deutschlands. Auch der Außenhandel bleibt
eine Verhaltenskurve — warum die Nachbarn gerade kaufen oder verkaufen, hinge an
ihren eigenen Preisen, und dafür bräuchte es ein europäisches Modell. Die
Ergebnisse sind Größenordnungen zum Verstehen der Mechanik — keine Prognose.

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

### Regler, die die Wirklichkeit meinen

Bei echten Messwerten stehen vier Regler standardmäßig auf dem, was in diesem
Zeitraum wirklich war: Wind- und Solarleistung aus der interpolierten
Ausbaureihe, CO₂- und Gaspreis aus der Monatstabelle. Das Formular schickt sie
dann gar nicht erst mit — ein mitgesendeter Wert wäre für das Backend von einer
Eingabe nicht zu unterscheiden. Nach der Antwort ziehen die Schieber auf die
tatsächlich benutzten Werte nach, damit man sieht, womit gerechnet wurde.

Wer einen dieser Regler bewegt, rechnet eine andere Welt durch. Das Häkchen geht
dabei von selbst weg, und der Vergleich mit dem tatsächlich gezahlten Preis
bekommt eine Warnung davor:

> **Achtung:** Dieses Szenario bildet nicht ab, was in diesem Zeitraum wirklich
> war — Wind 140 GW statt der tatsächlichen 73 GW. Die Zahlen unten messen
> deshalb nicht die Güte des Modells, sondern den Abstand zwischen dieser
> Rechnung und dem, was tatsächlich passiert ist.

Ohne diesen Hinweis liest sich eine mittlere Abweichung von 59 €/MWh wie ein
schlechtes Modell, obwohl sie nur bedeutet: In dieser Woche standen eben keine
140 Gigawatt Wind. Als abweichend gilt ein Regler ab zehn Prozent Unterschied —
darunter fallen Rundung und Interpolation der Ausbaureihe.

## Geführte Geschichten

Der Simulator beantwortet jede Frage, die man ihm stellt — aber er stellt keine.
Wer zum ersten Mal auf acht Regler schaut, weiß nicht, an welchem er drehen
soll. Die Geschichten liefern die Frage mit: Jeder Schritt setzt einen
Parametersatz, erklärt ihn und sagt dazu, worauf im Diagramm zu achten ist.

Sie liegen in `static_data/stories.json` und kommen über `/api/stories`. Eine
Geschichte ist an jedem Schritt teilbar: `?story=co2-preis&schritt=2`.

Fünf Stück, jede zwei bis drei Schritte lang:

| Kennung | Frage |
|---|---|
| `negative-preise` | Wie kann ein Preis unter null entstehen? |
| `co2-preis` | Warum trifft ein Zertifikatspreis die Braunkohle härter als das Gaskraftwerk? |
| `gaskrise` | War Strom 2023 wegen des Atomausstiegs teuer — oder wegen etwas anderem? |
| `mehr-wind` | Wie weit trägt ein Ausbau, den es heute noch nicht gibt? |
| `modell-und-wirklichkeit` | Kann man einem Kraftwerkspark aus sieben Blöcken glauben? |

### Behauptungen werden nachgerechnet

Der wichtigste Teil daran ist ein Test. Jeder Schritt darf neben Text und
Parametern ein Feld `expect` tragen, und `tests/test_stories.py` rechnet die
Simulation und prüft die Behauptung nach:

```json
"text": "… und der Preis bleibt über null, abgeregelt werden muss nichts.",
"expect": {"negative_price_hours": 0, "curtailed_gwh": 0}
```

Das ist keine Formsache. Beim ersten Schreiben dieser fünf Geschichten
behaupteten vier von ihnen etwas, das nicht stimmte — ein Schritt versprach
„der Preis bleibt über null", während er in siebzehn Stunden darunter lag; ein
anderer sagte „die teuersten Stunden bleiben teuer", obwohl sie um ein Fünftel
fielen. Solche Fehler fallen im Betrieb niemandem auf, weil trotzdem eine
Simulation herauskommt und plausibel aussieht. Auf einer Lernseite sind sie das
Schlimmste, was passieren kann.

Prüfbar sind zurzeit `negative_price_hours`, `curtailed_gwh`, `mean_price`,
`renewable_share`, `emissions_kt`, `validation_mae` — jeweils als fester Wert
oder als `{"min": …}` / `{"max": …}` — sowie `merit_order` als erwartete
Reihenfolge von Blöcken.

## Die Viertelstunde, und was sie für Speicher ändert

Seit dem 1. Oktober 2025 räumt die europäische Day-Ahead-Auktion
Viertelstundenkontrakte statt Stundenkontrakte. SMARD liefert beides unter
demselben Filter, nur anders aufgelöst — deshalb steht die Auflösung jetzt an
der Zeitreihe und nicht mehr fest im Abrufmodul:

```python
"price_quarter": {"filter": 4169, "resolution": "quarterhour", ...}
```

Dass die Umstellung ein echter Bruch ist und keine Formsache, ist nachgemessen:
Vor Oktober 2025 stehen in der Viertelstundenreihe viermal derselbe
Stundenpreis, die Spreizung innerhalb jeder Stunde ist exakt null. Danach liegt
sie im Mittel bei 23,2 €/MWh, im Median bei 14,6 und im Höchstfall bei 460.

Für Speicher ist das die interessanteste Zahl des ganzen Projekts, weil sie
direkt am Erlös hängt. Gerechnet über 330 Tage, ein Zyklus je Tag, 88 %
Wirkungsgrad, gekauft in den billigsten und verkauft in den teuersten
Zeitscheiben:

| Vorrat | nur Stundenkontrakte | Viertelstunden | Unterschied |
|---|---|---|---|
| 1 Stunde | 112,8 € | 123,8 € | +10 % |
| 2 Stunden | 107,9 € | 114,6 € | +6 % |
| 4 Stunden | 94,2 € | 98,7 € | +5 % |
| 8 Stunden | 69,1 € | 72,2 € | +4 % |

Je kürzer der Vorrat, desto mehr bringt die feinere Zeitscheibe — ein Speicher
mit einer Stunde Vorrat lebt von genau den Spitzen, die der Stundenkontrakt
wegmittelt. An nur 38 % der Tage fällt die teuerste Viertelstunde überhaupt in
die teuerste Stunde.

Das ist eine Obergrenze, kein Betriebsergebnis: Gerechnet wird mit perfekter
Voraussicht, abgezogen ist allein der Umwandlungsverlust. Netzentgelte, Abgaben,
Alterung und Kapitalkosten fehlen.

Das Messfenster endet mit dem letzten abgeschlossenen Monat (August 2026). Ein
mitlaufendes Ende würde die Zahlen mit jedem Datenabruf verschieben, und SMARD
bessert die jüngsten Wochen ohnehin nach.

## Was die Filternummern bei SMARD bedeuten

Die Datenschnittstelle von SMARD kennt nur Nummern: `/app/chart_data/4169/DE/…`
ist der deutsche Day-Ahead-Preis, `254` der französische. Welche Nummer welche
Reihe meint, steht nicht in der Schnittstelle — wohl aber in der Konfiguration,
die die SMARD-Oberfläche selbst lädt:

| Datei | Inhalt |
|---|---|
| `/app/chart_configuration/market_data_configuration.json` | jeder Baustein mit `data_id` (= Filternummer) und Namensschlüssel |
| `/app/assets/translations/lang-de.json` | löst die Namensschlüssel in Klartext auf |

Beides zusammen ergibt die vollständige Liste, etwa alle siebzehn
Großhandelspreis-Reihen mit ihren Gebotszonen. So sind die Nachbarpreise in
`smard.py` eingetragen — nachprüfbar statt geraten.

Der erste Versuch, sie über die Region abzurufen (`/4169/FR/…`), scheitert
übrigens mit 404: Die Regionsschlüssel sind die deutschen Gebots- und
Regelzonen, die Nachbarländer haben eigene Filternummern.

## Preiskonvergenz: woran man sieht, dass die Kopplung wirkt

Wenn zwei gekoppelte Zonen genug freie Leitung haben, räumt der gemeinsame
Algorithmus beide zum exakt selben Preis. Das ist zählbar. Über 23 209 Stunden
von Januar 2024 bis August 2026:

| Zone | gleicher Preis | Ø Abstand |
|---|---|---|
| Dänemark 1 | 49,0 % | 8,5 €/MWh |
| Dänemark 2 | 32,0 % | 9,6 €/MWh |
| Niederlande | 15,5 % | 6,7 €/MWh |
| Österreich | 11,8 % | 13,1 €/MWh |
| Belgien | 11,3 % | 11,6 €/MWh |
| Tschechien | 11,2 % | 10,1 €/MWh |
| Frankreich | 10,8 % | 29,3 €/MWh |
| Polen | 6,6 % | 20,4 €/MWh |
| Schweden 4 | 5,4 % | 30,7 €/MWh |
| Norwegen 2 | 1,3 % | 29,5 €/MWh |
| **Schweiz** | **0,1 %** | 20,3 €/MWh |

Die letzte Zeile ist die interessanteste: Mit der Schweiz teilt Deutschland
praktisch nie den Preis, obwohl die Leitungen da sind und das Land mitten im
Verbundnetz liegt. Der Grund ist institutionell — die Schweiz nimmt mangels
Stromabkommen am gemeinsamen Auktionsverfahren nicht teil. Marktkopplung ist
keine Eigenschaft des Netzes, sondern eine Verabredung, und man sieht in den
Daten genau, wo sie gilt.

Eine hohe Zahl heißt dabei nicht „gut verbunden", sondern nur: Zwischen diesen
Zonen war noch Platz. Nach Norwegen ist das Seekabel fast durchgehend
ausgelastet, weil deutscher Strom fast durchgehend teurer ist.

## Erklärseiten mit nachgerechneten Zahlen

Die Seiten `/speicher` und `/handel` erklären, was das Modell inzwischen kann,
und tun das mit Zahlen statt mit Adjektiven. Jede dieser Zahlen steht in
`tests/test_topics.py` noch einmal — zusammen mit der Messvorschrift, aus der
sie stammt:

```python
("/handel", "−28,3 TWh", lambda m: m.saldo_twh(2024), 0.2),
```

Der Test prüft beides: dass der Ausschnitt so auf der gerenderten Seite steht
und dass die Messung ihn bestätigt. Ändert jemand den Text, fällt es auf; ändert
jemand Text und Prüfzeile gemeinsam, ohne nachzurechnen, fällt es ebenfalls auf.
Geprüft werden nur abgeschlossene Jahre — bei einem laufenden würde sich die
Zahl mit jedem Datenabruf verschieben, und ein Test, der von selbst rot wird,
wird bald ignoriert.

Was auf den Seiten steht, ist damit dieselbe Art Behauptung wie ein `expect` in
einer geführten Geschichte: eine, die sich widerlegen lässt.

Die Handelsseite zeigt die gemessene Außenhandelskurve als Diagramm. Sie kommt
über `GET /api/exchange-curve` und ist dort gleichmäßig abgetastet: Die
Stützstellen in `power_plants.json` liegen in ungleichen Preisabständen, und
nebeneinandergesetzt ergäben sie ein verzerrtes Bild der Steigung — die Kurve
sähe flacher aus, als sie ist.

## Zwei Szenarien nebeneinander

Ein einzelnes Ergebnis beantwortet „was passiert?", nicht „was ändert sich
dadurch?". Für die zweite Frage braucht es zwei Läufe. „Szenario merken" legt
den aktuellen Lauf fest; jeder weitere wird daneben gestellt — Kennzahl für
Kennzahl, mit absoluter und relativer Differenz.

Zwei Entscheidungen dabei:

* **Bewertet wird nur, wo die Richtung unstrittig ist.** Weniger CO₂ ist besser,
  mehr Erneuerbare sind besser. Ob ein höherer Preis gut oder schlecht ist,
  hängt davon ab, wen man fragt — dazu hat das Modell keine Meinung, und die
  Zeile bleibt farblos.
* **Die Preiskurven werden nur übereinandergelegt, wenn beide Läufe denselben
  Zeitraum zeigen.** Sonst stünden zwei Kurven über einer x-Achse, die für eine
  davon nicht gilt. Die Kennzahlen bleiben trotzdem vergleichbar, und ein
  Hinweis sagt, warum nur eine Kurve zu sehen ist.

Die Farbe der gemerkten Kurve (`--s-pinned`) ist nach demselben Verfahren
gewählt wie die übrigen Serienfarben, siehe unten: Der kleinste Abstand zu den
anderen Kurven des Preisdiagramms beträgt unter allen drei Dichromasien 13,2
(hell) beziehungsweise 13,3 (dunkel) und liegt damit über dem, was die
bestehenden Farben untereinander halten (10,5 und 7,2).

## Datenquellen

| Was | Quelle | Hinweis |
|---|---|---|
| Last, Einspeisung, Großhandelspreis | [SMARD](https://www.smard.de), Bundesnetzagentur | Quelle bei Weitergabe nennen |
| Day-Ahead-Preis je Viertelstunde | SMARD, derselbe Filter viertelstündlich | aussagekräftig erst ab Oktober 2025 |
| Preise der Nachbarzonen | SMARD, eigene Filter je Zone | Zuordnung siehe Abschnitt unten |
| Installierte Leistung | [Energy-Charts](https://energy-charts.info), Fraunhofer ISE | einmal jährlich nachpflegen |
| Brennstoff- und CO₂-Preise | EEX, Weltbank, EZB | Monatswerte, siehe `fuel_ingest.py` |

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
│   ├── benchmark.py            Prüfsatz: Modell gegen tatsächlichen Preis
│   ├── region.py               Zeitzone und Regionsschlüssel
│   ├── data/
│   │   ├── sources.py          Zeitreihenquellen: erzeugt oder gemessen
│   │   ├── smard.py            Abruf von SMARD, nur Standardbibliothek
│   │   ├── store.py            lokaler Speicher (SQLite)
│   │   ├── capacity.py         installierte Leistung je Zeitpunkt
│   │   ├── fuel_prices.py      Brennstoff- und CO₂-Preise je Monat
│   │   ├── fuel_ingest.py      baut die Monatstabelle aus freien Quellen
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
            ├── topics.js       Diagramme der Handels- und der Marktseite
            ├── stories.js      geführte Geschichten
            ├── compare.js      Vergleich zweier Szenarien
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
