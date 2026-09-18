"""Holt SMARD-Daten in die lokale Datenbank.

Läuft als eigenes Programm, nicht im Webserver — der soll nur lesen. So bleibt
die Website auch dann bedienbar, wenn SMARD gerade langsam ist oder das Netz
fehlt, und der Abruf lässt sich auf einem Raspberry Pi einfach per cron takten:

    # jeden Tag um 6:15 Uhr die neuen Stunden nachladen
    15 6 * * * /pfad/zu/website/backend/.venv/bin/python -m backend.data.ingest --quiet

Aufrufbeispiele:

    python -m backend.data.ingest --weeks 8          letzte 8 Wochen
    python -m backend.data.ingest --from 2022-01-01  ab Datum (einmaliger Aufbau)
    python -m backend.data.ingest --all              alle Zeitreihen statt nur der nötigen
    python -m backend.data.ingest --status           nur zeigen, was vorliegt

Bereits geholte Wochen werden übersprungen. Die jüngsten Wochen holt das Skript
trotzdem erneut, weil SMARD vorläufige Werte nachträglich korrigiert.
"""

import argparse
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Sequence

from . import smard, store

# So viele der letzten Wochen werden immer neu geholt, auch wenn sie schon
# einmal geladen wurden — SMARD liefert zunächst vorläufige Werte.
REFRESH_WEEKS = 3

# Kurze Pause zwischen Abrufen, um SMARD nicht unnötig zu belasten.
PAUSE_SECONDS = 0.3


def log(message: str, quiet: bool = False) -> None:
    if not quiet:
        stamp = datetime.now().strftime("%H:%M:%S")
        print("[%s] %s" % (stamp, message), flush=True)


def parse_date(text: str) -> int:
    """'2022-01-01' als Unix-Sekunde (UTC)."""
    return int(datetime.strptime(text, "%Y-%m-%d")
               .replace(tzinfo=timezone.utc).timestamp())


def plan_weeks(available: Sequence[int], known: Sequence[int],
               since: Optional[int] = None, limit: Optional[int] = None,
               refresh: int = REFRESH_WEEKS) -> List[int]:
    """Welche Wochen sollen geholt werden?

    Reine Rechenlogik ohne Netz und Datenbank, damit sie direkt prüfbar ist.

    Reihenfolge ist wichtig: Erst wird der betrachtete Zeitraum eingegrenzt
    (--from, --weeks), dann wird innerhalb dieses Zeitraums entschieden, was
    fehlt. Andersherum würden bei `--weeks 8` uralte Lücken die acht Plätze
    belegen, statt die acht jüngsten Wochen zu betrachten.

    Immer erneuert werden nur die `refresh` jüngsten Wochen des Gesamtbestands,
    weil SMARD dort noch vorläufige Werte nachbessert. Ältere Wochen gelten als
    endgültig und werden kein zweites Mal geholt.
    """
    if not available:
        return []
    provisional = set(available[-refresh:]) if refresh > 0 else set()

    window = list(available)
    if since is not None:
        window = [week for week in window if week >= since]
    if limit is not None:
        window = window[-limit:]

    known_set = set(known)
    return [week for week in window if week not in known_set or week in provisional]


def weeks_to_fetch(conn, name: str, since: Optional[int],
                   limit: Optional[int]) -> List[int]:
    """Wie plan_weeks, aber mit echtem Bestand und echtem SMARD-Index."""
    return plan_weeks(available=smard.available_weeks(name),
                      known=list(store.fetched_weeks(conn, name)),
                      since=since, limit=limit)


def ingest_series(conn, name: str, since: Optional[int], limit: Optional[int],
                  quiet: bool = False) -> dict:
    """Eine Zeitreihe aktualisieren. Fehler werden gemeldet, nicht geworfen."""
    label = smard.SERIES[name]["label"]
    try:
        weeks = weeks_to_fetch(conn, name, since, limit)
    except smard.SmardError as error:
        log("  %-18s Index nicht erreichbar: %s" % (name, error), quiet)
        return {"series": name, "weeks": 0, "points": 0, "error": str(error)}

    if not weeks:
        log("  %-18s aktuell" % name, quiet)
        return {"series": name, "weeks": 0, "points": 0, "error": None}

    total_points = 0
    failed = 0
    for week in weeks:
        try:
            rows = smard.fetch_week(name, week)
        except smard.SmardError as error:
            failed += 1
            log("  %-18s Woche %s fehlgeschlagen: %s"
                % (name, datetime.fromtimestamp(week, timezone.utc).date(), error), quiet)
            continue
        if rows:
            store.write_observations(conn, name, rows)
            total_points += len(rows)
        store.note_fetch(conn, name, week, int(time.time()), len(rows))
        time.sleep(PAUSE_SECONDS)

    log("  %-18s %3d Wochen, %6d Werte%s  (%s)"
        % (name, len(weeks) - failed, total_points,
           "" if not failed else ", %d fehlgeschlagen" % failed, label), quiet)
    return {"series": name, "weeks": len(weeks) - failed, "points": total_points,
            "error": None if not failed else "%d Wochen fehlgeschlagen" % failed}


def show_status(conn, db_path: str) -> None:
    rows = store.coverage(conn)
    if not rows:
        print("Noch keine Daten. Aufbau zum Beispiel mit:")
        print("    python -m backend.data.ingest --weeks 52")
        return
    size_mb = store.database_size_bytes(db_path) / (1024 * 1024)
    print("Datenbank: %s  (%.1f MB)\n" % (db_path, size_mb))
    print("%-20s %-12s %-12s %8s %7s" % ("Zeitreihe", "von", "bis", "Werte", "Lücken"))
    for name, info in sorted(rows.items()):
        print("%-20s %-12s %-12s %8d %7d" % (
            name,
            datetime.fromtimestamp(info["first_ts"], timezone.utc).date(),
            datetime.fromtimestamp(info["last_ts"], timezone.utc).date(),
            info["points"], info["gaps"]))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m backend.data.ingest",
        description="Holt Messwerte von SMARD in die lokale Datenbank.")
    parser.add_argument("--weeks", type=int, default=None,
                        help="nur die letzten N Wochen betrachten")
    parser.add_argument("--from", dest="since", type=str, default=None,
                        help="erst ab diesem Datum, Format JJJJ-MM-TT")
    parser.add_argument("--series", nargs="+", default=None,
                        help="bestimmte Zeitreihen statt der Standardauswahl")
    parser.add_argument("--all", action="store_true",
                        help="alle bekannten Zeitreihen holen, nicht nur die nötigen")
    parser.add_argument("--db", default=None, help="abweichender Pfad zur Datenbank")
    parser.add_argument("--status", action="store_true",
                        help="nur anzeigen, was vorliegt")
    parser.add_argument("--quiet", action="store_true",
                        help="nur Fehler ausgeben — sinnvoll für cron")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    db_path = args.db or store.DEFAULT_PATH

    if args.series:
        unknown = [name for name in args.series if name not in smard.SERIES]
        if unknown:
            print("Unbekannte Zeitreihe(n): %s" % ", ".join(unknown), file=sys.stderr)
            print("Bekannt sind: %s" % ", ".join(sorted(smard.SERIES)), file=sys.stderr)
            return 2
        names = args.series
    elif args.all:
        names = sorted(smard.SERIES)
    else:
        names = list(smard.CORE_SERIES)

    since = parse_date(args.since) if args.since else None

    with store.open_db(db_path) as conn:
        if args.status:
            show_status(conn, db_path)
            return 0

        log("Aktualisiere %d Zeitreihen in %s" % (len(names), db_path), args.quiet)
        started = time.time()
        results = [ingest_series(conn, name, since, args.weeks, args.quiet)
                   for name in names]

        points = sum(r["points"] for r in results)
        errors = [r for r in results if r["error"]]
        log("Fertig: %d Werte in %.1f s%s"
            % (points, time.time() - started,
               "" if not errors else ", %d Zeitreihen mit Fehlern" % len(errors)),
            args.quiet)

        if errors:
            for result in errors:
                print("FEHLER %s: %s" % (result["series"], result["error"]), file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
