"""Übernimmt vorliegende Vorhersage-Ergebnisse in die Datenbank der Website.

Die Modelle selbst zu rechnen dauert lange — Modell 2 rund zwei Minuten je Tag,
ein ganzes Jahr also mehrere Tage. Liegen die Ergebnisse bereits als Tabelle
vor, ist der Umweg unnötig: Diese Dateien enthalten für jede Stunde den
tatsächlichen Preis und die Vorhersage jedes Modells.

Das Programm braucht nur die Standardbibliothek und läuft deshalb mit jeder der
beiden virtuellen Umgebungen.

    python -m forecasting.import_results forecasting/data/lstr_run_61_forecast.csv
    python -m forecasting.import_results forecasting/data/*.csv --check
"""

import argparse
import csv
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
WEBSITE = os.path.dirname(HERE)
for path in (HERE, WEBSITE):
    if path not in sys.path:
        sys.path.insert(0, path)

from config import (MODELS, REALIZED_COLUMN, REFERENCE_SERIES,  # noqa: E402
                    RESULT_COLUMNS)

TIMESTAMP_COLUMN = "Timestamp Berlin"


def parse_timestamp(text):
    """Zeitstempel der Ergebnisdatei als Unix-Sekunde (UTC).

    Die Datei führt Ortszeit mit Zeitverschiebung, etwa
    '2025-06-01 12:00:00+02:00'. Weil die Verschiebung mitgeliefert wird, ist
    der Zeitpunkt eindeutig — auch in der doppelten Stunde der Zeitumstellung.
    """
    moment = datetime.fromisoformat(text.strip())
    if moment.tzinfo is None:
        raise ValueError("Zeitstempel ohne Zeitzone: %r" % text)
    return int(moment.timestamp())


def read_results(path):
    """Ergebnisdatei einlesen.

    Liefert (rows_by_model, realized, zeitraum). Fehlende Werte bleiben None —
    eine Lücke ist eine Information, kein Anlass zum Raten.
    """
    per_model = {model: [] for model in RESULT_COLUMNS.values()}
    realized = []
    with open(path, encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        missing = [c for c in [TIMESTAMP_COLUMN] if c not in (reader.fieldnames or [])]
        if missing:
            raise ValueError("Spalte fehlt in %s: %s" % (os.path.basename(path), missing))
        known = [c for c in RESULT_COLUMNS if c in reader.fieldnames]
        for row in reader:
            ts = parse_timestamp(row[TIMESTAMP_COLUMN])
            for column in known:
                value = (row.get(column) or "").strip()
                per_model[RESULT_COLUMNS[column]].append(
                    (ts, float(value) if value else None))
            actual = (row.get(REALIZED_COLUMN) or "").strip()
            realized.append((ts, float(actual) if actual else None))
    per_model = {model: rows for model, rows in per_model.items() if rows}
    stamps = [ts for ts, _ in realized]
    span = (min(stamps), max(stamps)) if stamps else (0, 0)
    return per_model, realized, span


def describe(ts):
    return datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")


def import_file(path, write=True, compare=False):
    from backend.data import store

    per_model, realized, span = read_results(path)
    print("%s  —  %s bis %s" % (os.path.basename(path), describe(span[0]), describe(span[1])))

    if compare:
        report_deviation(realized, span)

    written = 0
    if write:
        with store.open_db() as conn:
            for model, rows in sorted(per_model.items()):
                written += store.write_forecasts(conn, model, rows)
                gefuellt = sum(1 for _, v in rows if v is not None)
                print("   %-20s %5d Stunden" % (MODELS[model]["label"], gefuellt))
            # Die Preisreihe, gegen die die Modelle entwickelt wurden. Ohne sie
            # ließe sich ihre Güte nicht fair beurteilen.
            store.write_observations(conn, REFERENCE_SERIES, realized)
            print("   %-20s %5d Stunden"
                  % ("Referenzpreis", sum(1 for _, v in realized if v is not None)))
    return written


def report_deviation(realized, span):
    """Die in der Datei mitgelieferten Preise gegen die eigenen Messwerte halten.

    Beide sollten denselben Börsenpreis meinen. Weichen sie ab, stimmt etwas
    mit der Zeitzuordnung nicht — und jeder Vergleich danach wäre wertlos.
    """
    from backend.data import sources

    eigene = sources.actual_prices(span[0], int((span[1] - span[0]) // 3600) + 1)
    if not eigene:
        print("   (keine eigenen Messwerte für diesen Zeitraum — Abgleich entfällt)")
        return
    nach_zeit = {ts: value for ts, value in realized}
    paare = []
    for index, wert in enumerate(eigene):
        ts = span[0] + index * 3600
        anderer = nach_zeit.get(ts)
        if wert is not None and anderer is not None:
            paare.append(abs(wert - anderer))
    if not paare:
        print("   (keine gemeinsamen Stunden — Abgleich entfällt)")
        return
    schnitt = sum(paare) / len(paare)
    print("   Abgleich mit eigenen Messwerten: %d gemeinsame Stunden, "
          "mittlere Abweichung %.3f €/MWh" % (len(paare), schnitt))
    if schnitt > 1.0:
        print("   ACHTUNG: Die Preise passen nicht zusammen — Zeitzuordnung prüfen.",
              file=sys.stderr)


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="python -m forecasting.import_results",
        description="Übernimmt vorliegende Vorhersage-Ergebnisse in die Datenbank.")
    parser.add_argument("files", nargs="+", help="Ergebnisdateien (CSV)")
    parser.add_argument("--check", action="store_true",
                        help="nur prüfen und mit den eigenen Messwerten abgleichen")
    args = parser.parse_args(argv)

    total = 0
    for path in args.files:
        if not os.path.exists(path):
            print("Datei fehlt: %s" % path, file=sys.stderr)
            return 2
        total += import_file(path, write=not args.check, compare=True)
    if not args.check:
        print("\nInsgesamt %d Werte übernommen." % total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
