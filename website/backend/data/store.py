"""Lokaler Speicher für Messwerte — eine SQLite-Datei, keine Fremdbibliothek.

Warum SQLite: Die Daten sollen offline verfügbar sein, auch wenn gerade kein
Netz da ist oder SMARD nicht antwortet. Eine Datei lässt sich sichern, kopieren
und auf einen Raspberry Pi mitnehmen. Stündliche Werte über zehn Jahre sind
rund 90.000 Zeilen je Zeitreihe — für SQLite eine Kleinigkeit.

Geschrieben wird ausschließlich vom Abrufskript (ingest.py, per cron), gelesen
vom Webserver. Der WAL-Modus erlaubt genau das gleichzeitig.
"""

import os
import sqlite3
from contextlib import contextmanager
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

# Ort der Datenbank: per Umgebungsvariable überschreibbar, damit auf dem Pi
# ein anderer Pfad (etwa eine SSD statt der SD-Karte) genutzt werden kann.
DEFAULT_PATH = os.environ.get(
    "ENERGIEWENDE_DB",
    os.path.join(os.path.dirname(os.path.dirname(__file__)), "data_store", "smard.sqlite3"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
    ts     INTEGER NOT NULL,          -- Unix-Sekunden, UTC
    series TEXT    NOT NULL,          -- interner Name, siehe smard.SERIES
    value  REAL,                      -- MW bzw. EUR/MWh; NULL ist eine echte Lücke
    PRIMARY KEY (ts, series)
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_observations_series_ts ON observations (series, ts);

CREATE TABLE IF NOT EXISTS fetch_log (
    series     TEXT    NOT NULL,      -- welche Zeitreihe
    week_start INTEGER NOT NULL,      -- Wochenbeginn in Unix-Sekunden
    fetched_at INTEGER NOT NULL,      -- wann zuletzt geholt
    points     INTEGER NOT NULL,      -- wie viele Werte ankamen
    PRIMARY KEY (series, week_start)
) WITHOUT ROWID;
"""


def connect(path: Optional[str] = None) -> sqlite3.Connection:
    """Verbindung öffnen und Schema sicherstellen."""
    path = path or DEFAULT_PATH
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    # WAL: Der Webserver darf lesen, während das Abrufskript schreibt.
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(SCHEMA)
    return conn


@contextmanager
def open_db(path: Optional[str] = None):
    conn = connect(path)
    try:
        yield conn
    finally:
        conn.close()


def exists(path: Optional[str] = None) -> bool:
    """Gibt es überhaupt schon eine Datenbank? Ohne sie bleibt nur die Simulation."""
    return os.path.exists(path or DEFAULT_PATH)


def write_observations(conn: sqlite3.Connection, series: str,
                       rows: Iterable[Tuple[int, Optional[float]]]) -> int:
    """Werte schreiben; vorhandene Zeitpunkte werden überschrieben.

    SMARD korrigiert Werte nachträglich, deshalb ersetzen wir statt zu ignorieren.
    """
    payload = [(ts, series, value) for ts, value in rows]
    with conn:
        conn.executemany(
            "INSERT INTO observations (ts, series, value) VALUES (?, ?, ?) "
            "ON CONFLICT(ts, series) DO UPDATE SET value=excluded.value",
            payload)
    return len(payload)


def note_fetch(conn: sqlite3.Connection, series: str, week_start: int,
               fetched_at: int, points: int) -> None:
    with conn:
        conn.execute(
            "INSERT INTO fetch_log (series, week_start, fetched_at, points) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(series, week_start) DO UPDATE SET "
            "fetched_at=excluded.fetched_at, points=excluded.points",
            (series, week_start, fetched_at, points))


def fetched_weeks(conn: sqlite3.Connection, series: str) -> Dict[int, int]:
    """Bereits geholte Wochen als {week_start: points} — Grundlage für inkrementelles Nachladen."""
    rows = conn.execute(
        "SELECT week_start, points FROM fetch_log WHERE series = ?", (series,)).fetchall()
    return {row["week_start"]: row["points"] for row in rows}


def read_series(conn: sqlite3.Connection, series: str,
                start_ts: int, end_ts: int) -> List[Tuple[int, Optional[float]]]:
    """Werte eines Zeitraums, aufsteigend nach Zeit. Grenzen inklusive Start, exklusive Ende."""
    rows = conn.execute(
        "SELECT ts, value FROM observations WHERE series = ? AND ts >= ? AND ts < ? "
        "ORDER BY ts", (series, start_ts, end_ts)).fetchall()
    return [(row["ts"], row["value"]) for row in rows]


def read_many(conn: sqlite3.Connection, names: Sequence[str],
              start_ts: int, end_ts: int) -> Dict[str, Dict[int, Optional[float]]]:
    """Mehrere Zeitreihen auf einmal, je als {timestamp: value}."""
    return {name: dict(read_series(conn, name, start_ts, end_ts)) for name in names}


def coverage(conn: sqlite3.Connection, series: Optional[str] = None) -> Dict[str, Dict]:
    """Welcher Zeitraum liegt je Zeitreihe vor? Für Statusanzeige und Abrufplanung."""
    # Zwischen "Zeitstempel vorhanden" und "Wert vorhanden" wird streng
    # unterschieden: Die laufende Woche enthält bereits Stunden, die noch in der
    # Zukunft liegen und deshalb leer sind. Wer darauf rechnet, rechnet auf
    # gehaltenen Randwerten statt auf Messwerten.
    query = ("SELECT series, MIN(ts) AS first_ts, MAX(ts) AS last_ts, "
             "COUNT(*) AS points, COUNT(value) AS filled, "
             "MIN(CASE WHEN value IS NOT NULL THEN ts END) AS first_value_ts, "
             "MAX(CASE WHEN value IS NOT NULL THEN ts END) AS last_value_ts "
             "FROM observations")
    params: Tuple = ()
    if series:
        query += " WHERE series = ?"
        params = (series,)
    query += " GROUP BY series ORDER BY series"
    out = {}
    for row in conn.execute(query, params).fetchall():
        out[row["series"]] = {
            "first_ts": row["first_ts"],
            "last_ts": row["last_ts"],
            "first_value_ts": row["first_value_ts"],
            "last_value_ts": row["last_value_ts"],
            "points": row["points"],
            "filled": row["filled"],
            "gaps": row["points"] - row["filled"],
        }
    return out


def database_size_bytes(path: Optional[str] = None) -> int:
    path = path or DEFAULT_PATH
    return os.path.getsize(path) if os.path.exists(path) else 0
