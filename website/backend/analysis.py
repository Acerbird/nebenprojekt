"""Merit-Order und stündlicher Kraftwerkseinsatz.

Das Modell ist bewusst klein gehalten, aber in der Struktur echt: Grenzkosten
werden aus Brennstoffpreis, Wirkungsgrad und CO₂-Preis gerechnet, die Nachfrage
wird Stunde für Stunde aus der billigsten verfügbaren Leistung gedeckt, und der
Preis ist der des letzten benötigten Blocks (Einheitspreisverfahren).

Nicht abgebildet: Speicher, Import/Export, Mindestlasten, An- und Abfahrkosten,
Netzengpässe. Die Ergebnisse sind Größenordnungen, keine Prognose.
"""

import json
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional

from .data import sources
from .utils import profiles

_DATA_PATH = os.path.join(os.path.dirname(__file__), "static_data", "power_plants.json")

with open(_DATA_PATH, encoding="utf-8") as fh:
    FLEET = json.load(fh)

# Preis, zu dem die Nachfrage rechnerisch nicht mehr gedeckt werden kann.
SCARCITY_PRICE = 400.0
# Preis in Stunden, in denen erneuerbare Leistung abgeregelt werden muss.
# Vereinfachung: ein fester negativer Wert statt einer Gebotskurve.
SURPLUS_PRICE = -10.0

DEFAULTS = {
    "wind_gw": 70.0,
    "solar_gw": 90.0,
    "co2_price": 80.0,
    "gas_price": 32.0,
    "peak_load_gw": 75.0,
    "hours": 72,
    "season": profiles.DEFAULT_SEASON,
    "source": sources.SYNTHETIC,
    "start": None,
}

LIMITS = {
    "wind_gw": (0.0, 300.0),
    "solar_gw": (0.0, 400.0),
    "co2_price": (0.0, 300.0),
    "gas_price": (5.0, 200.0),
    "peak_load_gw": (30.0, 150.0),
    "hours": (24, 336),
}


def clamp(value: float, bounds) -> float:
    low, high = bounds
    return max(low, min(high, value))


def normalise_params(**kwargs) -> Dict:
    """Werte in gültige Bereiche zwingen, damit die API nie mit Müll rechnet."""
    params = dict(DEFAULTS)
    for key, value in kwargs.items():
        if value is not None:
            params[key] = value
    for key, bounds in LIMITS.items():
        params[key] = clamp(float(params[key]), bounds)
    params["hours"] = int(params["hours"])
    if params["season"] not in profiles.SEASONS:
        params["season"] = profiles.DEFAULT_SEASON
    if params["source"] not in (sources.SYNTHETIC, sources.HISTORICAL):
        params["source"] = sources.SYNTHETIC
    if params["source"] == sources.HISTORICAL:
        window = resolve_window(params)
        params["start_ts"] = window["start_ts"]
        params["hours"] = window["hours"]
        params["adjustments"] = window["adjustments"]
    else:
        params["start_ts"] = None
    return params


def resolve_window(params: Dict) -> Dict:
    """Start und Dauer für echte Messwerte festlegen.

    Ohne Startdatum wird der jüngste vorliegende Zeitraum genommen, damit die
    Seite ohne Zutun die aktuelle Lage zeigt.

    Liegt der Wunsch außerhalb des Bestands, wird er in den Bestand geschoben
    beziehungsweise gekürzt — aber nie stillschweigend: Jede Anpassung steht
    anschließend im Ergebnis. Wer Januar 2019 anfragt und Juli 2026 bekommt,
    muss das sehen können.
    """
    available = sources.available_range()
    if available is None:
        raise sources.InsufficientData(
            "Noch keine Messwerte vorhanden. Abrufen mit: "
            "python -m backend.data.ingest --weeks 52")

    requested_hours = params["hours"]
    if params.get("start"):
        try:
            moment = datetime.strptime(str(params["start"])[:10], "%Y-%m-%d")
        except ValueError:
            raise sources.InsufficientData(
                "Startdatum nicht lesbar: %r — erwartet wird JJJJ-MM-TT." % params["start"])
        requested_start = int(moment.replace(tzinfo=timezone.utc).timestamp())
    else:
        requested_start = available["last_ts"] - (requested_hours - 1) * 3600

    start_ts = min(max(requested_start, available["first_ts"]), available["last_ts"])
    # Nicht über den letzten Messwert hinaus rechnen: Sonst würden fehlende
    # Stunden mit dem letzten bekannten Wert gefüllt und sähen aus wie Messung.
    possible_hours = int((available["last_ts"] - start_ts) // 3600) + 1
    hours = max(1, min(requested_hours, possible_hours))

    notes = {}
    if start_ts != requested_start:
        notes["start_shifted_to"] = datetime.fromtimestamp(
            start_ts, timezone.utc).strftime("%Y-%m-%d")
        notes["reason"] = "Der gewünschte Zeitraum liegt außerhalb der vorliegenden Messwerte."
    if hours != requested_hours:
        notes["hours_shortened_to"] = hours
        notes.setdefault("reason", "Bis zum Ende der Messwerte reichen nur %d Stunden." % hours)

    return {"start_ts": start_ts, "hours": hours, "adjustments": notes}


def marginal_cost(plant: Dict, co2_price: float, gas_price: Optional[float] = None) -> float:
    """Grenzkosten eines Blocks in €/MWh_el."""
    if plant["kind"] == "must_run":
        return float(plant["marginal_cost"])
    fuel = FLEET["fuels"][plant["fuel"]]
    price_th = fuel["price_eur_per_mwh_th"]
    if plant["fuel"] == "erdgas" and gas_price is not None:
        price_th = gas_price
    eff = plant["efficiency"]
    fuel_cost = price_th / eff
    co2_cost = co2_price * fuel["emission_t_per_mwh_th"] / eff
    return round(fuel_cost + co2_cost + plant["var_om"], 2)


def emission_intensity(plant: Dict) -> float:
    """CO₂-Ausstoß in t je MWh_el; erneuerbare Blöcke gelten als emissionsfrei."""
    if plant["kind"] == "must_run":
        return 0.0
    return FLEET["fuels"][plant["fuel"]]["emission_t_per_mwh_th"] / plant["efficiency"]


def merit_order(co2_price: float, gas_price: float,
                wind_gw: float = 0.0, solar_gw: float = 0.0) -> List[Dict]:
    """Alle Blöcke nach Grenzkosten sortiert.

    Wind und PV stehen mit ihrer installierten Leistung und Grenzkosten von
    praktisch null am Anfang — im stündlichen Einsatz ist davon nur der gerade
    verfügbare Teil nutzbar.
    """
    blocks = [
        {"id": "wind", "name": "Wind", "category": "wind", "kind": "variable",
         "capacity_gw": wind_gw, "cost": 0.0, "emission": 0.0,
         "note": "Keine Brennstoffkosten — speist ein, wann der Wind weht."},
        {"id": "solar", "name": "Photovoltaik", "category": "solar", "kind": "variable",
         "capacity_gw": solar_gw, "cost": 0.0, "emission": 0.0,
         "note": "Keine Brennstoffkosten — speist tagsüber ein."},
    ]
    for plant in FLEET["plants"]:
        blocks.append({
            "id": plant["id"],
            "name": plant["name"],
            "category": plant["category"],
            "kind": plant["kind"],
            "capacity_gw": plant["capacity_gw"],
            "cost": marginal_cost(plant, co2_price, gas_price),
            "emission": round(emission_intensity(plant), 3),
            "note": plant.get("note", ""),
        })
    blocks.sort(key=lambda b: b["cost"])

    cumulative = 0.0
    for block in blocks:
        block["from_gw"] = round(cumulative, 2)
        cumulative += block["capacity_gw"]
        block["to_gw"] = round(cumulative, 2)
    return blocks


def merit_order_payload(co2_price: Optional[float] = None, gas_price: Optional[float] = None,
                        wind_gw: Optional[float] = None, solar_gw: Optional[float] = None) -> Dict:
    params = normalise_params(co2_price=co2_price, gas_price=gas_price,
                              wind_gw=wind_gw, solar_gw=solar_gw)
    blocks = merit_order(params["co2_price"], params["gas_price"],
                         params["wind_gw"], params["solar_gw"])
    return {
        "blocks": blocks,
        "categories": FLEET["categories"],
        "total_capacity_gw": round(blocks[-1]["to_gw"], 2) if blocks else 0.0,
        "params": {k: params[k] for k in ("co2_price", "gas_price", "wind_gw", "solar_gw")},
    }


def resolve_series(params: Dict, scale_to_peak: bool = True):
    """Zeitreihen der gewählten Quelle besorgen.

    Bei erzeugten Profilen bestimmt der Höchstlast-Regler die Nachfrage. Bei
    echten Messwerten gilt zunächst die tatsächliche Last; nur wenn ausdrücklich
    eine Höchstlast verlangt wird, wird die echte Kurve darauf gestreckt — damit
    lässt sich fragen, was zusätzlicher Verbrauch angerichtet hätte.
    """
    if params["source"] == sources.HISTORICAL:
        series = sources.historical_series(params["start_ts"], params["hours"])
        if scale_to_peak:
            series = sources.scale_load(series, params["peak_load_gw"])
        return series
    return sources.synthetic_series(params["hours"], params["peak_load_gw"],
                                    params["season"])


def simulate(wind_gw: Optional[float] = None, solar_gw: Optional[float] = None,
             co2_price: Optional[float] = None, gas_price: Optional[float] = None,
             peak_load_gw: Optional[float] = None, hours: Optional[int] = None,
             season: Optional[str] = None, source: Optional[str] = None,
             start: Optional[str] = None) -> Dict:
    """Stündlicher Einsatz über den gewählten Zeitraum."""
    # Ob der Höchstlast-Regler angefasst wurde, muss vor dem Auffüllen mit
    # Standardwerten feststehen — sonst wäre bei echten Daten nicht mehr
    # erkennbar, ob die Last gestreckt werden soll.
    scale_to_peak = peak_load_gw is not None
    params = normalise_params(wind_gw=wind_gw, solar_gw=solar_gw, co2_price=co2_price,
                              gas_price=gas_price, peak_load_gw=peak_load_gw,
                              hours=hours, season=season, source=source, start=start)
    n = params["hours"]

    # Die Zeitreihen kommen aus einer austauschbaren Quelle. Ab hier ist
    # gleichgültig, ob sie erzeugt oder aus echten Messwerten gelesen wurden.
    series = resolve_series(params, scale_to_peak)
    n = series.hours
    params["hours"] = n
    demand = series.load_gw
    wind_avail = series.wind_gw(params["wind_gw"])
    solar_avail = series.solar_gw(params["solar_gw"])

    blocks = merit_order(params["co2_price"], params["gas_price"],
                         params["wind_gw"], params["solar_gw"])
    categories = [c["id"] for c in FLEET["categories"]]

    generation = {cat: [0.0] * n for cat in categories}
    prices: List[float] = []
    curtailed: List[float] = []
    residual: List[float] = []

    emissions_t = 0.0
    curtailed_gwh = 0.0
    surplus_hours = 0
    scarcity_hours = 0

    for h in range(n):
        remaining = demand[h]
        price = 0.0
        available_ee = wind_avail[h] + solar_avail[h]
        residual.append(round(demand[h] - available_ee, 3))

        for block in blocks:
            if block["id"] == "wind":
                capacity = wind_avail[h]
            elif block["id"] == "solar":
                capacity = solar_avail[h]
            elif block["category"] == "sonstige_ee":
                capacity = block["capacity_gw"] * series.hydro_factor if block["id"] == "laufwasser" \
                    else block["capacity_gw"]
            else:
                capacity = block["capacity_gw"]

            if remaining <= 1e-9 or capacity <= 0:
                continue
            used = min(capacity, remaining)
            generation[block["category"]][h] += used
            remaining -= used
            price = block["cost"]
            emissions_t += used * 1000.0 * block["emission"]

        if remaining > 1e-6:
            price = SCARCITY_PRICE
            scarcity_hours += 1

        used_ee = generation["wind"][h] + generation["solar"][h]
        spill = max(available_ee - used_ee, 0.0)
        curtailed.append(round(spill, 3))
        curtailed_gwh += spill
        if spill > 0.01:
            # Überschuss: Anlagen mit Einspeisevorrang drücken den Preis unter null,
            # bis sich das Abregeln lohnt.
            surplus_hours += 1
            price = SURPLUS_PRICE

        prices.append(round(price, 2))

    generation = {cat: [round(v, 3) for v in series] for cat, series in generation.items()}
    total_demand = sum(demand)
    renewable_gen = sum(generation["wind"]) + sum(generation["solar"]) + sum(generation["sonstige_ee"])
    price_weighted = sum(p * d for p, d in zip(prices, demand)) / total_demand if total_demand else 0.0

    return {
        "timestamps": series.timestamps,
        "demand_gw": demand,
        "residual_load_gw": residual,
        "generation_gw": generation,
        "curtailed_gw": curtailed,
        "price_eur_mwh": prices,
        "available_gw": {"wind": wind_avail, "solar": solar_avail},
        "categories": FLEET["categories"],
        "kpis": {
            "mean_price": round(price_weighted, 2),
            "min_price": min(prices) if prices else 0.0,
            "max_price": max(prices) if prices else 0.0,
            "renewable_share": round(100.0 * renewable_gen / total_demand, 1) if total_demand else 0.0,
            "emissions_kt": round(emissions_t / 1000.0, 1),
            "emission_intensity_g_kwh": round(emissions_t / total_demand, 0) if total_demand else 0.0,
            "curtailed_gwh": round(curtailed_gwh, 1),
            "surplus_hours": surplus_hours,
            "negative_price_hours": sum(1 for p in prices if p < 0),
            "scarcity_hours": scarcity_hours,
            "demand_twh": round(total_demand / 1000.0, 2),
        },
        "params": params,
        "season_label": series.label,
        "source": series.source,
        "series_meta": series.meta,
        # Zeitstempel oben sind UTC — hierin gehören sie angezeigt.
        "display_timezone": series.display_timezone,
    }


def glossary() -> List[Dict]:
    path = os.path.join(os.path.dirname(__file__), "static_data", "glossary.json")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)
