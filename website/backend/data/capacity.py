"""Installierte Erzeugungsleistung als Bezugsgröße für Kapazitätsfaktoren.

Ein Kapazitätsfaktor ist Einspeisung geteilt durch installierte Leistung. Weil
laufend zugebaut wird, ist der Nenner keine Konstante: 2015 standen 37 GW Wind
an Land, 2026 sind es 71 GW. Dieselbe Einspeisung von 30 GW bedeutet also in
beiden Jahren etwas völlig anderes.

Die Jahreswerte liegen in static_data/installed_capacity.json mit Quelle und
Stand. Zwischen den Jahresenden wird linear interpoliert.
"""

import json
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                     "static_data", "installed_capacity.json")

_cache: Optional[Dict] = None


def _load() -> Dict:
    global _cache
    if _cache is None:
        with open(_PATH, encoding="utf-8") as fh:
            document = json.load(fh)
        _cache = {
            "meta": {key: value for key, value in document.items() if key.startswith("_")},
            "capacity": {
                technology: sorted((int(year), float(gw)) for year, gw in values.items())
                for technology, values in document["capacity_gw"].items()
            },
        }
    return _cache


def technologies() -> List[str]:
    return sorted(_load()["capacity"])


def source_note() -> Dict:
    """Quelle und Stand — gehört bei echten Daten sichtbar in die Oberfläche."""
    return dict(_load()["meta"])


def covered_years(technology: str) -> Tuple[int, int]:
    points = _load()["capacity"][technology]
    return points[0][0], points[-1][0]


def _fractional_year(ts: int) -> float:
    """Zeitpunkt als Jahr mit Nachkommateil, etwa 2023,5 für Anfang Juli 2023."""
    moment = datetime.fromtimestamp(ts, timezone.utc)
    year_start = datetime(moment.year, 1, 1, tzinfo=timezone.utc)
    next_year = datetime(moment.year + 1, 1, 1, tzinfo=timezone.utc)
    return moment.year + (moment - year_start) / (next_year - year_start)


def installed_gw(technology: str, ts: int) -> float:
    """Installierte Leistung in GW zum Zeitpunkt ts.

    Der Jahreswert gilt als Stand zum Jahresende, liegt also bei Jahr + 1,0 auf
    der Zeitachse. Vor dem ersten und nach dem letzten bekannten Jahr wird der
    Randwert gehalten statt fortgeschrieben — Extrapolation wäre geraten.
    """
    points = _load()["capacity"][technology]
    position = _fractional_year(ts)

    if position <= points[0][0] + 1.0:
        return points[0][1]
    if position >= points[-1][0] + 1.0:
        return points[-1][1]

    for (year_a, gw_a), (year_b, gw_b) in zip(points, points[1:]):
        x_a, x_b = year_a + 1.0, year_b + 1.0
        if x_a <= position <= x_b:
            if x_b == x_a:
                return gw_b
            share = (position - x_a) / (x_b - x_a)
            return gw_a + share * (gw_b - gw_a)
    return points[-1][1]


def is_extrapolated(ts: int) -> bool:
    """Liegt der Zeitpunkt außerhalb der belegten Jahre? Dann ist der Wert gehalten."""
    technology = technologies()[0]
    first, last = covered_years(technology)
    position = _fractional_year(ts)
    return position < first + 1.0 or position > last + 1.0
