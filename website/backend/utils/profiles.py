"""Zeitreihen-Profile für Last, Wind und Photovoltaik.

Bewusst ohne numpy/pandas: die Zeitreihen sind wenige hundert Stunden lang,
reines Python ist hier schnell genug und hält die Installation schlank.

Zeitrechnung: Gerechnet und ausgegeben wird in UTC. Die Tagesgänge folgen aber
dem menschlichen Rhythmus — die Morgenspitze liegt um 8 Uhr Ortszeit, nicht um
8 Uhr UTC. Deshalb wird für Lastform und Sonnenstand die Ortszeit der Region
herangezogen, und zwar über den Umweg UTC, damit Sommerzeitwechsel richtig
herauskommen.
"""

import math
import random
from datetime import datetime, timedelta, timezone
from typing import Dict, List
from zoneinfo import ZoneInfo

from ..region import timezone_name

# Jahreszeiten-Parameter. Die Werte sind grobe, aber realistische Mittelwerte
# für Deutschland (Kapazitätsfaktoren, Sonnenauf-/-untergang in Ortszeit).
SEASONS: Dict[str, Dict] = {
    "winter": {
        "label": "Winter",
        "seed": 101,
        "start": "2025-01-13",      # ein Montag
        "load_factor": 1.00,
        "sunrise": 8.3,
        "sunset": 16.5,
        "solar_peak_cf": 0.16,
        "wind_mean_cf": 0.34,
        "hydro_factor": 0.80,
    },
    "uebergang": {
        "label": "Übergang",
        "seed": 202,
        "start": "2025-04-14",
        "load_factor": 0.90,
        "sunrise": 6.3,
        "sunset": 20.3,
        "solar_peak_cf": 0.45,
        "wind_mean_cf": 0.26,
        "hydro_factor": 1.15,
    },
    "sommer": {
        "label": "Sommer",
        "seed": 303,
        "start": "2025-07-14",
        "load_factor": 0.86,
        "sunrise": 5.2,
        "sunset": 21.4,
        "solar_peak_cf": 0.58,
        "wind_mean_cf": 0.19,
        "hydro_factor": 0.95,
    },
}

DEFAULT_SEASON = "winter"

# Tagesgang der Last als Anteil der Jahreshöchstlast (Werktag, Stunde 0..23).
# Morgen- und Abendspitze, Nachtabsenkung.
_LOAD_SHAPE = [
    0.62, 0.60, 0.59, 0.59, 0.61, 0.66,
    0.74, 0.85, 0.91, 0.94, 0.96, 0.96,
    0.94, 0.92, 0.90, 0.89, 0.91, 0.95,
    0.99, 1.00, 0.96, 0.89, 0.79, 0.69,
]
_WEEKEND_FACTOR = 0.84


def season_config(season: str) -> Dict:
    """Konfiguration einer Jahreszeit; fällt auf Winter zurück."""
    return SEASONS.get(season, SEASONS[DEFAULT_SEASON])


def start_utc(season: str) -> datetime:
    """Beginn der Jahreszeit: lokale Mitternacht, ausgedrückt in UTC.

    Das hinterlegte Datum meint einen Montag um 00:00 Ortszeit. Je nachdem, ob
    gerade Sommerzeit gilt, ist das 23:00 oder 22:00 UTC am Vortag.
    """
    naive = datetime.strptime(season_config(season)["start"], "%Y-%m-%d")
    local = naive.replace(tzinfo=ZoneInfo(timezone_name()))
    return local.astimezone(timezone.utc)


def local_hours(hours: int, season: str) -> List[datetime]:
    """Die Stunden des Zeitraums als Ortszeit — für Tagesgang und Wochentag.

    Gerechnet wird in UTC und erst danach umgerechnet. Addierte man stattdessen
    Stunden auf eine Ortszeit, ginge der Sommerzeitwechsel verloren.
    """
    begin = start_utc(season)
    tz = ZoneInfo(timezone_name())
    return [(begin + timedelta(hours=h)).astimezone(tz) for h in range(hours)]


def timestamps(hours: int, season: str) -> List[str]:
    """ISO-Zeitstempel in UTC, beginnend Montag 00:00 Ortszeit."""
    begin = start_utc(season)
    return [(begin + timedelta(hours=h)).isoformat(timespec="minutes")
            for h in range(hours)]


def load_series(hours: int, peak_gw: float, season: str) -> List[float]:
    """Stündliche Last in GW. Tagesgang und Wochenende folgen der Ortszeit."""
    cfg = season_config(season)
    out = []
    for moment in local_hours(hours, season):
        shape = _LOAD_SHAPE[moment.hour]
        weekday = _WEEKEND_FACTOR if moment.weekday() >= 5 else 1.0
        out.append(round(peak_gw * cfg["load_factor"] * shape * weekday, 3))
    return out


def solar_cf_series(hours: int, season: str) -> List[float]:
    """Stündlicher Kapazitätsfaktor der PV (0..1, glatter Tagesgang ohne Wolken).

    Kapazitätsfaktoren statt fertiger Einspeisung: So lässt sich dasselbe
    Wetter auf eine beliebige installierte Leistung umrechnen — die Grundlage
    für hypothetische Zubau-Szenarien.
    """
    cfg = season_config(season)
    sunrise, sunset = cfg["sunrise"], cfg["sunset"]
    daylight = sunset - sunrise
    out = []
    for moment in local_hours(hours, season):
        # Sonnenauf- und -untergang stehen als Ortszeit in der Konfiguration.
        hour_of_day = moment.hour + 0.5
        if sunrise < hour_of_day < sunset:
            cf = cfg["solar_peak_cf"] * math.sin(math.pi * (hour_of_day - sunrise) / daylight)
        else:
            cf = 0.0
        out.append(round(max(cf, 0.0), 5))
    return out


def solar_series(hours: int, installed_gw: float, season: str) -> List[float]:
    """Stündliche PV-Einspeisung in GW."""
    return [round(installed_gw * cf, 3) for cf in solar_cf_series(hours, season)]


def wind_cf_series(hours: int, season: str, seed: int = 7) -> List[float]:
    """Stündlicher Kapazitätsfaktor des Winds (0..0,92).

    AR(1)-Prozess: aufeinanderfolgende Stunden sind stark korreliert, damit
    Flauten und Starkwindphasen über mehrere Tage entstehen statt reinem Rauschen.
    """
    cfg = season_config(season)
    # Fester Seed je Jahreszeit: hash() auf Strings ist pro Prozess zufällig
    # und würde bei jedem Serverstart andere Zeitreihen liefern.
    rng = random.Random(seed + cfg["seed"])
    rho, sigma = 0.93, 0.42
    state = rng.gauss(0.0, 1.0)
    out = []
    for _ in range(hours):
        state = rho * state + math.sqrt(1 - rho ** 2) * rng.gauss(0.0, 1.0)
        cf = cfg["wind_mean_cf"] * math.exp(sigma * state - 0.5 * sigma ** 2)
        out.append(round(min(max(cf, 0.0), 0.92), 5))
    return out


def wind_series(hours: int, installed_gw: float, season: str, seed: int = 7) -> List[float]:
    """Stündliche Windeinspeisung in GW."""
    return [round(installed_gw * cf, 3) for cf in wind_cf_series(hours, season, seed)]


def solar_day_profile(season: str) -> List[float]:
    """Kapazitätsfaktor der PV über einen Tag (24 Werte) — für die Erklärseite."""
    cfg = season_config(season)
    sunrise, sunset = cfg["sunrise"], cfg["sunset"]
    daylight = sunset - sunrise
    out = []
    for hour in range(24):
        h = hour + 0.5
        if sunrise < h < sunset:
            out.append(round(cfg["solar_peak_cf"] * math.sin(math.pi * (h - sunrise) / daylight), 4))
        else:
            out.append(0.0)
    return out


def load_day_profile(weekend: bool = False) -> List[float]:
    """Lastgang über einen Tag als Anteil der Höchstlast (24 Werte)."""
    factor = _WEEKEND_FACTOR if weekend else 1.0
    return [round(v * factor, 4) for v in _LOAD_SHAPE]
