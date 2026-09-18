"""Rechnet Preisvorhersagen und legt sie in der Datenbank der Website ab.

Läuft bewusst außerhalb des Webservers, genau wie der SMARD-Abruf: Die Modelle
brauchen pandas, numpy, scipy und statsmodels, die Website soll davon nichts
wissen. Sie liest nur das Ergebnis.

Das ist keine Sparsamkeit um ihrer selbst willen — Modell 2 rechnet rund zwei
Minuten für einen einzigen Tag. Zur Laufzeit einer Webseite ist das unmöglich;
einmal vorberechnet ist es sofort da.

Aufrufbeispiele:

    python -m forecasting.run_forecast --model 1 --from 2025-06-01 --days 30
    python -m forecasting.run_forecast --model 2 --from 2025-06-01 --days 3
    python -m forecasting.run_forecast --status

Der Zeitaufwand wird vor dem Start geschätzt und abgefragt, damit niemand
versehentlich einen Lauf über Stunden anstößt.
"""

import argparse
import os
import sys
import time
import warnings
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
WEBSITE = os.path.dirname(HERE)
if WEBSITE not in sys.path:
    sys.path.insert(0, WEBSITE)

from config import (DATA_DIR, MODEL_1, MODEL_1_SETTINGS, MODEL_2,  # noqa: E402
                    MODEL_2_FEATURES, MODEL_2_SETTINGS, MODELS, TRAINING_DATA)

# Die Modelle geben Konvergenzwarnungen aus, wenn eine Stunde schlecht passt.
# Sie fallen dann selbst auf ein einfacheres Verfahren zurück.
warnings.filterwarnings("ignore")


def log(message, quiet=False):
    if not quiet:
        print("[%s] %s" % (datetime.now().strftime("%H:%M:%S"), message), flush=True)


def ensure_working_directory():
    """Ins Datenverzeichnis wechseln, bevor die Modelle rechnen.

    Eine Eigenheit des übernommenen Modellcodes: Die Liste der Feiertage wird
    unter ihrem bloßen Dateinamen im Arbeitsverzeichnis gesucht und dort
    angelegt, wenn sie fehlt. Ohne diesen Wechsel landet sie irgendwo — je
    nachdem, von wo aus das Programm gestartet wurde.

    Der Modellcode bleibt dabei unangetastet, damit er identisch zu dem des
    Forschungsprojekts ist und dieselben Zahlen liefert.
    """
    os.makedirs(DATA_DIR, exist_ok=True)
    os.chdir(DATA_DIR)


def load_training_data():
    """Trainingsdaten laden und Zeitspalten aufbereiten."""
    import pandas as pd

    if not os.path.exists(TRAINING_DATA):
        raise SystemExit(
            "Trainingsdaten fehlen: %s\n"
            "Sie stammen aus dem Forschungsprojekt und liegen bewusst nicht im "
            "Repository — siehe README, Abschnitt Preisvorhersage." % TRAINING_DATA)
    frame = pd.read_csv(TRAINING_DATA, compression="gzip")
    frame["Timestamp Berlin"] = (pd.to_datetime(frame["Timestamp Berlin"], utc=True)
                                 .dt.tz_convert("Europe/Berlin"))
    frame["Date"] = pd.to_datetime(frame["Date"])
    return frame


def forecast_one_day(frame, model, day):
    """Ein Tag mit einem Modell. Liefert [(Unix-Sekunde, Preis), ...]."""
    if model == MODEL_1:
        from models.arx_model import expert_forecast
        result = expert_forecast(frame, day, **MODEL_1_SETTINGS)
    else:
        from models.lstr_model import lstr_hourly_forecast
        result = lstr_hourly_forecast(frame, day, features=MODEL_2_FEATURES,
                                      **MODEL_2_SETTINGS)
    table = result[0] if isinstance(result, tuple) else result
    if table is None or table.empty:
        return []

    column = next(c for c in table.columns if "Forecast" in c)
    rows = []
    for stamp, value in zip(table["Timestamp Berlin"], table[column]):
        # In der Datenbank stehen Zeitpunkte als Unix-Sekunden in UTC.
        rows.append((int(stamp.timestamp()), None if value != value else float(value)))
    return rows


def available_days(frame):
    """Tage, für die überhaupt Trainingsdaten vorliegen."""
    return frame["Date"].min().to_pydatetime(), frame["Date"].max().to_pydatetime()


def run(model, first_day, days, quiet=False, confirm=True):
    from backend.data import store

    ensure_working_directory()
    frame = load_training_data()
    first_available, last_available = available_days(frame)

    info = MODELS[model]
    estimate = days * info["seconds_per_day"]
    log("%s — %d Tage ab %s, geschätzt %s"
        % (info["label"], days, first_day.date(), format_duration(estimate)), quiet)

    if confirm and estimate > 600:
        antwort = input("Das dauert voraussichtlich %s. Fortfahren? [j/N] "
                        % format_duration(estimate))
        if antwort.strip().lower() not in ("j", "ja", "y", "yes"):
            print("Abgebrochen.")
            return 0

    written = 0
    failed = []
    started = time.time()
    with store.open_db() as conn:
        for index in range(days):
            day = first_day + timedelta(days=index)
            if not first_available <= day <= last_available:
                failed.append((day, "außerhalb der Trainingsdaten"))
                continue
            try:
                rows = forecast_one_day(frame, model, day)
            except Exception as error:               # noqa: BLE001 - ein Tag darf scheitern
                failed.append((day, "%s: %s" % (type(error).__name__, error)))
                log("  %s fehlgeschlagen: %s" % (day.date(), error), quiet)
                continue
            if rows:
                written += store.write_forecasts(conn, model, rows)
            log("  %s — %d Stunden (%d von %d)"
                % (day.date(), len(rows), index + 1, days), quiet)

    log("Fertig: %d Werte in %s%s"
        % (written, format_duration(time.time() - started),
           "" if not failed else ", %d Tage übersprungen" % len(failed)), quiet)
    for day, reason in failed:
        print("ÜBERSPRUNGEN %s: %s" % (day.date(), reason), file=sys.stderr)
    return 1 if failed and not written else 0


def format_duration(seconds):
    seconds = int(seconds)
    if seconds < 90:
        return "%d Sekunden" % seconds
    if seconds < 5400:
        return "%d Minuten" % round(seconds / 60)
    return "%.1f Stunden" % (seconds / 3600)


def show_status():
    from backend.data import store

    if not store.exists():
        print("Noch keine Datenbank. Erst SMARD-Daten holen, dann Vorhersagen rechnen.")
        return
    with store.open_db() as conn:
        rows = store.forecast_coverage(conn)
    if not rows:
        print("Noch keine Vorhersagen berechnet.")
        print("Zum Beispiel:  python -m forecasting.run_forecast --model 1 --from 2025-06-01 --days 30")
        return
    print("%-20s %-12s %-12s %8s  %s" % ("Modell", "von", "bis", "Stunden", "gerechnet"))
    for model, info in sorted(rows.items()):
        label = MODELS.get(model, {}).get("label", model)
        print("%-20s %-12s %-12s %8d  %s" % (
            label,
            datetime.fromtimestamp(info["first_ts"], timezone.utc).date(),
            datetime.fromtimestamp(info["last_ts"], timezone.utc).date(),
            info["points"],
            datetime.fromtimestamp(info["created_at"]).strftime("%Y-%m-%d %H:%M")))


def build_parser():
    parser = argparse.ArgumentParser(
        prog="python -m forecasting.run_forecast",
        description="Rechnet Preisvorhersagen und legt sie für die Website ab.")
    parser.add_argument("--model", choices=["1", "2"], default="1",
                        help="1 = schnell, 2 = genauer, aber rund zwei Minuten je Tag")
    parser.add_argument("--from", dest="first", help="erster Tag, Format JJJJ-MM-TT")
    parser.add_argument("--days", type=int, default=7, help="Anzahl der Tage (Standard 7)")
    parser.add_argument("--status", action="store_true", help="zeigen, was vorliegt")
    parser.add_argument("--yes", action="store_true", help="lange Läufe ohne Rückfrage starten")
    parser.add_argument("--quiet", action="store_true", help="nur Fehler ausgeben")
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.status:
        show_status()
        return 0
    if not args.first:
        print("Bitte einen Starttag angeben, etwa --from 2025-06-01", file=sys.stderr)
        return 2
    try:
        first_day = datetime.strptime(args.first, "%Y-%m-%d")
    except ValueError:
        print("Startdatum nicht lesbar: %r — erwartet wird JJJJ-MM-TT" % args.first,
              file=sys.stderr)
        return 2

    model = MODEL_1 if args.model == "1" else MODEL_2
    return run(model, first_day, max(args.days, 1), args.quiet, confirm=not args.yes)


if __name__ == "__main__":
    sys.exit(main())
