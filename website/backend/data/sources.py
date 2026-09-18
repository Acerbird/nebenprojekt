"""Zeitreihenquellen für das Modell.

Das Modell rechnet nicht mit fertigen Einspeisereihen, sondern mit einer
Lastkurve und Kapazitätsfaktoren. Woher die stammen, entscheidet die Quelle:

    synthetic   erzeugte Profile (utils/profiles.py), Jahreszeit frei wählbar
    historical  echte SMARD-Daten aus der lokalen Datenbank

Beide liefern dieselbe Struktur, deshalb merkt der Kraftwerkseinsatz keinen
Unterschied. Der Zubau-Regler wirkt in beiden Fällen gleich — installierte
Leistung mal Kapazitätsfaktor. Genau daraus entsteht die interessante Frage:
Was wäre, wenn in einem echten Wetterjahr schon doppelt so viel Wind gestanden
hätte?

Alle Zeitstempel sind UTC. Für die Darstellung trägt jede Reihe zusätzlich die
Zeitzone ihrer Region mit sich — die Mittagsspitze gehört in der Anzeige auf
13 Uhr, und zwar unabhängig davon, wo der Betrachter gerade sitzt.
"""

from typing import Dict, List, NamedTuple, Optional

from ..region import timezone_name
from ..utils import profiles

SYNTHETIC = "synthetic"
HISTORICAL = "historical"


class Series(NamedTuple):
    """Ein Szenario-Zeitraum, unabhängig von seiner Herkunft.

    timestamps         ISO-Zeitstempel in UTC
    load_gw            stündliche Last in GW
    wind_cf            Kapazitätsfaktor Wind (0..1), mit installierter Leistung zu multiplizieren
    solar_cf           Kapazitätsfaktor Photovoltaik (0..1)
    display_timezone   IANA-Zeitzone, in der die Stempel angezeigt gehören
    """

    timestamps: List[str]
    load_gw: List[float]
    wind_cf: List[float]
    solar_cf: List[float]
    hydro_factor: float
    source: str
    label: str
    meta: Dict
    # Nur für die Anzeige: Die Zeitstempel oben sind UTC.
    display_timezone: str = timezone_name()

    @property
    def hours(self) -> int:
        return len(self.load_gw)

    def wind_gw(self, installed_gw: float) -> List[float]:
        return [round(installed_gw * cf, 3) for cf in self.wind_cf]

    def solar_gw(self, installed_gw: float) -> List[float]:
        return [round(installed_gw * cf, 3) for cf in self.solar_cf]


def synthetic_series(hours: int, peak_load_gw: float, season: str) -> Series:
    """Erzeugte Profile — reproduzierbar, offline, ohne Datenbestand."""
    cfg = profiles.season_config(season)
    return Series(
        timestamps=profiles.timestamps(hours, season),
        load_gw=profiles.load_series(hours, peak_load_gw, season),
        wind_cf=profiles.wind_cf_series(hours, season),
        solar_cf=profiles.solar_cf_series(hours, season),
        hydro_factor=cfg["hydro_factor"],
        source=SYNTHETIC,
        label=cfg["label"],
        meta={"season": season, "peak_load_gw": peak_load_gw,
              "timezone": timezone_name()},
        display_timezone=timezone_name(),
    )


def scale_load(series: Series, peak_load_gw: Optional[float]) -> Series:
    """Lastkurve auf eine gewünschte Höchstlast strecken.

    Hält die Form der echten Nachfrage fest und verschiebt nur ihr Niveau —
    so lässt sich fragen, was mehr Verbrauch durch Wärmepumpen und E-Autos
    im Stromsystem anrichten würde.
    """
    if peak_load_gw is None or not series.load_gw:
        return series
    current_peak = max(series.load_gw)
    if current_peak <= 0:
        return series
    factor = peak_load_gw / current_peak
    meta = dict(series.meta)
    meta["load_scaled_by"] = round(factor, 4)
    return series._replace(
        load_gw=[round(v * factor, 3) for v in series.load_gw], meta=meta)


# ------------------------------------------------------- echte Messwerte

class InsufficientData(RuntimeError):
    """Für den gewünschten Zeitraum liegen zu wenige Messwerte vor."""


# Bis zu so vielen aufeinanderfolgenden Stunden wird eine Lücke überbrückt.
# Längere Ausfälle werden nicht geraten, sondern gemeldet.
MAX_GAP_HOURS = 3


def _fill_gaps(values: List[Optional[float]], max_gap: int = MAX_GAP_HOURS) -> List[float]:
    """Kurze Lücken linear überbrücken; bei längeren abbrechen.

    SMARD hat gelegentlich einzelne fehlende Stunden. Eine Stunde zwischen zwei
    bekannten Werten zu interpolieren ist harmlos; einen halben Tag zu erfinden
    wäre es nicht.
    """
    known = [i for i, v in enumerate(values) if v is not None]
    if not known:
        raise InsufficientData("Für den Zeitraum liegt kein einziger Messwert vor.")

    out: List[float] = [0.0] * len(values)
    for index, value in enumerate(values):
        if value is not None:
            out[index] = float(value)
            continue
        before = [i for i in known if i < index]
        after = [i for i in known if i > index]
        if not before or not after:
            # Rand: nächsten bekannten Wert halten — aber nur für wenige Stunden,
            # sonst entstünde eine lange Reihe erfundener Messwerte.
            nearest = (before or after)[-1 if before else 0]
            if abs(index - nearest) > max_gap:
                raise InsufficientData(
                    "Am Rand des Zeitraums fehlen %d Stunden am Stück."
                    % abs(index - nearest))
            out[index] = float(values[nearest])
            continue
        left, right = before[-1], after[0]
        if right - left - 1 > max_gap:
            raise InsufficientData(
                "Datenlücke von %d Stunden — zu groß, um sie zu überbrücken."
                % (right - left - 1))
        share = (index - left) / (right - left)
        out[index] = float(values[left]) + share * (float(values[right]) - float(values[left]))
    return out


def available_range(db_path: Optional[str] = None) -> Optional[Dict]:
    """Welcher Zeitraum liegt lokal vor? None, wenn noch nichts geholt wurde."""
    from . import store
    if not store.exists(db_path):
        return None
    with store.open_db(db_path) as conn:
        rows = store.coverage(conn)
    needed = ("load", "wind_onshore", "wind_offshore", "solar")
    if not all(name in rows for name in needed):
        return None
    # Nur Zeitpunkte, für die jede benötigte Reihe einen echten Wert hat.
    if any(rows[name]["last_value_ts"] is None for name in needed):
        return None
    return {
        "first_ts": max(rows[name]["first_value_ts"] for name in needed),
        "last_ts": min(rows[name]["last_value_ts"] for name in needed),
        "series": {name: rows[name] for name in rows},
    }


def historical_series(start_ts: int, hours: int,
                      db_path: Optional[str] = None) -> Series:
    """Echte Messwerte als Szenario-Zeitraum.

    Aus Einspeisung und installierter Leistung entsteht ein Kapazitätsfaktor.
    Damit trägt die Reihe das echte Wetter, lässt sich aber auf eine beliebige
    installierte Leistung umrechnen — die Grundlage für die Frage, wie dasselbe
    Wetterjahr mit mehr Wind- und Solarleistung ausgegangen wäre.

    Wind onshore und offshore werden zu einem gemeinsamen Kapazitätsfaktor
    verrechnet, gewichtet mit ihrer jeweils installierten Leistung — das Modell
    kennt nur einen Wind-Regler.
    """
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo

    from . import capacity, store

    if hours <= 0:
        raise InsufficientData("Es wurde kein Zeitraum angefragt.")

    names = ("load", "wind_onshore", "wind_offshore", "solar")
    stamps = [start_ts + h * 3600 for h in range(hours)]
    end_ts = start_ts + hours * 3600

    if not store.exists(db_path):
        raise InsufficientData(
            "Noch keine Messwerte vorhanden. Abrufen mit: "
            "python -m backend.data.ingest --weeks 52")

    with store.open_db(db_path) as conn:
        raw = store.read_many(conn, names, start_ts, end_ts)

    missing = {name: sum(1 for ts in stamps if raw[name].get(ts) is None)
               for name in names}
    if all(count == hours for count in missing.values()):
        raise InsufficientData(
            "Für diesen Zeitraum liegen keine Messwerte vor.")

    filled = {name: _fill_gaps([raw[name].get(ts) for ts in stamps]) for name in names}

    load_gw = [round(v / 1000.0, 3) for v in filled["load"]]

    wind_cf: List[float] = []
    solar_cf: List[float] = []
    for index, ts in enumerate(stamps):
        cap_on = capacity.installed_gw("wind_onshore", ts)
        cap_off = capacity.installed_gw("wind_offshore", ts)
        cap_wind = cap_on + cap_off
        generated = (filled["wind_onshore"][index] + filled["wind_offshore"][index]) / 1000.0
        wind_cf.append(round(min(generated / cap_wind, 1.0), 5) if cap_wind > 0 else 0.0)

        cap_solar = capacity.installed_gw("solar", ts)
        solar_gen = filled["solar"][index] / 1000.0
        solar_cf.append(round(min(solar_gen / cap_solar, 1.0), 5) if cap_solar > 0 else 0.0)

    timestamps = [datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="minutes")
                  for ts in stamps]

    # Die Beschriftung ist Anzeige und darf deshalb Ortszeit zeigen — sonst
    # begänne ein deutscher Wintertag laut Überschrift am Vorabend.
    tz = ZoneInfo(timezone_name())
    begin = datetime.fromtimestamp(stamps[0], tz)
    finish = datetime.fromtimestamp(stamps[-1], tz)
    label = "%s bis %s" % (begin.strftime("%d.%m.%Y"), finish.strftime("%d.%m.%Y"))

    return Series(
        timestamps=timestamps,
        load_gw=load_gw,
        wind_cf=wind_cf,
        solar_cf=solar_cf,
        # Laufwasser schwankt real übers Jahr; hier bleibt es beim Normalwert,
        # solange die Wasserkraft-Zeitreihe nicht mitgelesen wird.
        hydro_factor=1.0,
        source=HISTORICAL,
        label=label,
        meta={
            "start_ts": start_ts,
            "hours": hours,
            "gaps_filled": {name: missing[name] for name in names if missing[name]},
            "installed_gw": {
                "wind": round(capacity.installed_gw("wind_onshore", start_ts)
                              + capacity.installed_gw("wind_offshore", start_ts), 2),
                "solar": round(capacity.installed_gw("solar", start_ts), 2),
            },
            "capacity_source": capacity.source_note().get("_quelle"),
            "data_source": "SMARD.de, Bundesnetzagentur",
            "timezone": timezone_name(),
        },
        display_timezone=timezone_name(),
    )
