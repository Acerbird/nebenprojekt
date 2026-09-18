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
from typing import Dict, List, Optional, Tuple

from .data import sources
from .utils import profiles

_DATA_PATH = os.path.join(os.path.dirname(__file__), "static_data", "power_plants.json")

with open(_DATA_PATH, encoding="utf-8") as fh:
    FLEET = json.load(fh)

STORAGE_UNITS = FLEET.get("storage", [])

# Preis, zu dem die Nachfrage rechnerisch nicht mehr gedeckt werden kann.
SCARCITY_PRICE = 400.0
# Preis in Stunden, in denen erneuerbare Leistung abgeregelt werden muss.
# Vereinfachung: ein fester negativer Wert statt einer Gebotskurve.
SURPLUS_PRICE = -10.0
# Preis, wenn selbst Biomasse und Laufwasser gedrosselt werden müssen. Solche
# Stunden gibt es real, und die Preise fallen dann deutlich tiefer.
DEEP_SURPLUS_PRICE = -60.0
# Anzeigenamen der Vorhersagemodelle. Ihre Funktionsweise gehört nicht in die
# Oberfläche — für die Benutzung zählt, wie gut sie treffen.
FORECAST_LABELS = {
    "model_1": "Forecast-Modell 1",
    "model_2": "Forecast-Modell 2",
}

# Untergrenze der Preissuche. Tiefer bietet niemand, weil dort auch die
# Anlagen mit Einspeisevergütung aussteigen.
PRICE_FLOOR = -500.0

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


def block_costs(plant: Dict, co2_price: float,
                gas_price: Optional[float] = None) -> Tuple[float, float]:
    """Gebotsspanne eines Blocks in €/MWh_el.

    Ein Kraftwerkspark besteht nicht aus einem einzigen Block je Technologie,
    sondern aus vielen Anlagen unterschiedlichen Alters und Wirkungsgrads. Statt
    einer Treppenstufe entsteht dadurch eine Rampe: Der modernste Block der
    Technologie bietet zum unteren Preis, der älteste zum oberen.

    Das ist der Grund, warum eine echte Merit-Order eine Kurve ist und keine
    Treppe — und warum ein Modell mit festen Einzelkosten nur eine Handvoll
    verschiedener Preise hervorbringen kann.

    Bei Anlagen ohne Brennstoffkosten beschreibt die Spanne kein Wirkungsgrad-,
    sondern ein Gebotsverhalten: Wer Einspeisevergütung bekommt, bietet auch bei
    deutlich negativen Preisen noch an, statt abzuschalten.
    """
    low = float(plant.get("var_om_low", 0.0))
    high = float(plant.get("var_om_high", low))

    if plant["kind"] != "must_run" and plant.get("fuel"):
        fuel = FLEET["fuels"][plant["fuel"]]
        price_th = fuel["price_eur_per_mwh_th"]
        if plant["fuel"] == "erdgas" and gas_price is not None:
            price_th = gas_price
        emission_th = fuel["emission_t_per_mwh_th"]
        variable_th = price_th + co2_price * emission_th
        # Günstigster Preis beim besten Wirkungsgrad, teuerster beim schlechtesten.
        low += variable_th / plant["efficiency_high"]
        high += variable_th / plant["efficiency_low"]

    return round(low, 2), round(max(high, low), 2)


def marginal_cost(plant: Dict, co2_price: float, gas_price: Optional[float] = None) -> float:
    """Mittlere Grenzkosten eines Blocks — für Vergleiche und Erklärseiten."""
    low, high = block_costs(plant, co2_price, gas_price)
    return round((low + high) / 2, 2)


def emission_intensity(plant: Dict) -> float:
    """CO₂-Ausstoß in t je MWh_el; erneuerbare Blöcke gelten als emissionsfrei.

    Gerechnet wird mit dem mittleren Wirkungsgrad der Bandbreite — der Ausstoß
    einer Technologie schwankt mit dem Anlagenalter genauso wie ihre Kosten.
    """
    if plant["kind"] == "must_run" or not plant.get("fuel"):
        return 0.0
    mean_efficiency = (plant["efficiency_low"] + plant["efficiency_high"]) / 2
    return FLEET["fuels"][plant["fuel"]]["emission_t_per_mwh_th"] / mean_efficiency


def merit_order(co2_price: float, gas_price: float,
                wind_gw: float = 0.0, solar_gw: float = 0.0) -> List[Dict]:
    """Alle Blöcke nach Grenzkosten sortiert.

    Wind und PV stehen mit ihrer installierten Leistung und Grenzkosten von
    praktisch null am Anfang — im stündlichen Einsatz ist davon nur der gerade
    verfügbare Teil nutzbar.
    """
    variable = FLEET.get("variable", {})
    blocks = []
    for block_id, name, category, capacity in (
            ("wind", "Wind", "wind", wind_gw),
            ("solar", "Photovoltaik", "solar", solar_gw)):
        bids = variable.get(block_id, {})
        blocks.append({
            "id": block_id, "name": name, "category": category, "kind": "variable",
            "capacity_gw": capacity,
            "cost_low": float(bids.get("bid_low", 0.0)),
            "cost_high": float(bids.get("bid_high", 0.0)),
            "emission": 0.0,
            "note": bids.get("note", "Keine Brennstoffkosten."),
        })
    for plant in FLEET["plants"]:
        low, high = block_costs(plant, co2_price, gas_price)
        blocks.append({
            "id": plant["id"],
            "name": plant["name"],
            "category": plant["category"],
            "kind": plant["kind"],
            "capacity_gw": plant["capacity_gw"],
            "cost_low": low,
            "cost_high": high,
            "emission": round(emission_intensity(plant), 3),
            "note": plant.get("note", ""),
        })
    # Nach dem Beginn der Gebotsspanne sortiert; die Spannen überlappen sich,
    # genau wie bei einer echten Merit-Order.
    blocks.sort(key=lambda b: (b["cost_low"], b["cost_high"]))
    for block in blocks:
        # Mittlerer Preis der Spanne — für Darstellung und Vergleiche.
        block["cost"] = round((block["cost_low"] + block["cost_high"]) / 2, 2)

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


def block_capacity(block: Dict, available: Dict) -> float:
    """Wie viel Leistung steht dieser Block in dieser Stunde bereit?

    Wind und Photovoltaik können nur, was das Wetter hergibt; Laufwasser folgt
    dem Wasserdargebot der Jahreszeit. Alles andere steht mit seiner
    installierten Leistung bereit.
    """
    if block["id"] == "wind":
        return available.get("wind", 0.0)
    if block["id"] == "solar":
        return available.get("solar", 0.0)
    if block["id"] == "laufwasser":
        return block["capacity_gw"] * available.get("hydro_factor", 1.0)
    return block["capacity_gw"]


def bid_share(block: Dict, price: float) -> float:
    """Welcher Anteil dieses Blocks bietet bei diesem Preis an? (0 bis 1)

    Innerhalb der Gebotsspanne steigt der Anteil linear: Bei `cost_low` bietet
    nur die modernste Anlage der Technologie, bei `cost_high` der gesamte
    Bestand. Genau das macht aus der Treppenstufe eine Rampe.
    """
    low, high = block["cost_low"], block["cost_high"]
    if price < low:
        return 0.0
    if price >= high or high <= low:
        return 1.0
    return (price - low) / (high - low)


def supply_at_price(blocks: List[Dict], price: float, available: Dict) -> float:
    """Wie viel Leistung bietet der gesamte Park bei diesem Preis an?"""
    return sum(block_capacity(block, available) * bid_share(block, price)
               for block in blocks)


def clear_market(blocks: List[Dict], demand_gw: float, available: Dict) -> float:
    """Markträumungspreis: der Preis, bei dem Angebot die Nachfrage deckt.

    Gesucht per Intervallhalbierung, weil die Angebotskurve monoton steigt. Das
    ist derselbe Gedanke wie beim Einheitspreisverfahren der Börse, nur ohne
    einzelne Gebote: Der Preis steigt, bis genug Leistung anbietet.
    """
    low, high = PRICE_FLOOR, SCARCITY_PRICE
    if supply_at_price(blocks, high, available) < demand_gw - 1e-9:
        return SCARCITY_PRICE          # selbst zum Höchstpreis reicht es nicht
    if supply_at_price(blocks, low, available) >= demand_gw:
        return PRICE_FLOOR             # schon zum Mindestpreis ist zu viel da
    # 40 Halbierungen bringen die Spanne von rund 900 €/MWh weit unter einen
    # Cent — mehr Schritte kosten nur Rechenzeit.
    for _ in range(40):
        middle = (low + high) / 2
        if supply_at_price(blocks, middle, available) < demand_gw:
            low = middle
        else:
            high = middle
    # `high` ist stets ein Preis, zu dem das Angebot reicht, `low` einer, zu dem
    # es nicht reicht. Zurückgegeben wird deshalb `high`: der niedrigste Preis,
    # der die Nachfrage deckt. Bei einem Block ohne Gebotsspanne springt das
    # Angebot, und die Mitte beider Werte läge womöglich knapp unterhalb des
    # Sprungs — dann bliebe der Block trotz Bedarf stehen.
    return high


def dispatch_hour(demand_gw: float, blocks: List[Dict], available: Dict) -> Dict:
    """Kraftwerkseinsatz einer einzelnen Stunde über die Angebotskurve.

    Jeder Block bietet in einer Spanne an statt zu einem festen Preis. Gesucht
    ist der Preis, bei dem das gesamte Angebot die Nachfrage deckt; wer darunter
    bietet, läuft, wer darüber bietet, steht still.

    Daraus ergeben sich mehrere Dinge von selbst, die vorher Sonderfälle waren:

    * Biomasse und Laufwasser laufen auch bei negativen Preisen weiter, weil ihr
      Gebot bis -200 beziehungsweise -500 reicht — Einspeisevergütung trägt den
      Betrieb.
    * Bei Überschuss fällt der Preis in den Gebotsbereich von Wind und
      Photovoltaik, und genau der überzählige Teil wird abgeregelt.
    * Reicht selbst der gesamte Park nicht, steht der Knappheitspreis.
    """
    demand_gw = max(demand_gw, 0.0)
    price = clear_market(blocks, demand_gw, available)

    # Einsatz je Block beim geräumten Preis.
    used_by_block = []
    produced = 0.0
    for block in blocks:
        capacity = block_capacity(block, available)
        if capacity <= 0:
            continue
        used = capacity * bid_share(block, price)
        used_by_block.append([block, capacity, used])
        produced += used

    # Am Grenzpreis bietet oft mehr an, als gebraucht wird — besonders bei einem
    # Block ohne Gebotsspanne, der ganz oder gar nicht läuft. Zurückgenommen
    # wird bei den teuersten laufenden Blöcken, denn sie stehen am Rand der
    # Einsatzreihenfolge.
    surplus = produced - demand_gw
    if surplus > 1e-9:
        for entry in sorted(used_by_block, key=lambda e: e[0]["cost_low"], reverse=True):
            if surplus <= 1e-9:
                break
            reducible = min(entry[2], surplus)
            entry[2] -= reducible
            surplus -= reducible

    generation: Dict[str, float] = {}
    emissions_t = 0.0
    curtailed_ee = 0.0
    produced = 0.0
    for block, capacity, used in used_by_block:
        if block["kind"] == "variable":
            curtailed_ee += capacity - used
        if used <= 1e-9:
            continue
        generation[block["category"]] = generation.get(block["category"], 0.0) + used
        emissions_t += used * 1000.0 * block["emission"]
        produced += used

    return {
        "generation": generation,
        "price": round(price, 2),
        "emissions_t": emissions_t,
        "curtailed_gw": max(curtailed_ee, 0.0),
        "curtailed_ee_gw": max(curtailed_ee, 0.0),
        "throttled_must_run_gw": 0.0,
        # Fließkommareste sind keine Unterdeckung.
        "unserved_gw": max(demand_gw - produced, 0.0) if demand_gw - produced > 1e-6 else 0.0,
    }


# ------------------------------------------------------------- Speicher

# Welcher Anteil der Stunden gilt als "billig" beziehungsweise "teuer"?
# Mehrere Paare, weil ein einzelnes bei schiefen Verteilungen versagt: Liegen
# zwei Drittel aller Stunden im Überschuss, sind das 30-%- und das 70-%-Quantil
# beide derselbe negative Preis — dann sähe der Speicher keine Spreizung, obwohl
# es teure Stunden gibt. Deshalb wird nötigenfalls weiter aufgemacht.
QUANTILE_PAIRS = ((0.30, 0.70), (0.15, 0.85), (0.05, 0.95))
# Darunter lohnt kein Speicherbetrieb — und ohne diese Schwelle wäre in einem
# flachen Preisverlauf dieselbe Stunde zugleich billig und teuer.
MIN_PRICE_SPREAD = 1.0


def quantile(sorted_values: List[float], share: float) -> float:
    """Einfaches Quantil ohne numpy — die Reihen sind wenige hundert Stunden lang."""
    if not sorted_values:
        return 0.0
    index = min(int(share * (len(sorted_values) - 1)), len(sorted_values) - 1)
    return sorted_values[index]


def price_thresholds(prices: List[float]) -> Tuple[float, float]:
    """Ab welchem Preis gilt eine Stunde als billig, ab welchem als teuer?

    Zuerst wird das engste Quantilpaar versucht. Ergibt es keine brauchbare
    Spreizung — etwa weil die meisten Stunden im Überschuss liegen und denselben
    negativen Preis tragen —, wird weiter aufgemacht, zuletzt bis zu den
    Extremen. So findet der Speicher auch in einem schiefen Preisverlauf die
    wenigen Stunden, in denen sich der Einsatz lohnt.
    """
    if not prices:
        return 0.0, 0.0
    sorted_prices = sorted(prices)
    for low_share, high_share in QUANTILE_PAIRS:
        low = quantile(sorted_prices, low_share)
        high = quantile(sorted_prices, high_share)
        if high - low >= MIN_PRICE_SPREAD:
            return low, high
    return sorted_prices[0], sorted_prices[-1]


def plan_storage(prices: List[float], units: List[Dict]) -> Dict:
    """Wann lädt und entlädt der Speicher?

    Bewusst eine nachvollziehbare Faustregel statt einer Optimierung: Laden in
    den billigsten Stunden, entladen in den teuersten. Genau das tun
    Speicherbetreiber am Markt auch, und es lässt sich erklären, ohne einen
    Optimierer mitzuliefern.

    Zwei Dinge begrenzen den Einsatz. Erstens der Wirkungsgrad: Wer 100 MWh
    einspeichert und 88 davon zurückbekommt, braucht einen Preisabstand, der
    diesen Verlust deckt. Zweitens der Vorrat — ein Batteriespeicher mit
    1,6 Stunden Volllast überbrückt einen Abend, aber keine Dunkelflaute.

    Die Reihenfolge ist chronologisch, der Speicher startet leer. Er kann also
    nur abgeben, was er in diesem Zeitraum vorher aufgenommen hat; Energie
    entsteht nicht aus dem Nichts.
    """
    hours = len(prices)
    charge = [0.0] * hours
    discharge = [0.0] * hours
    per_unit = []

    cheap, expensive = price_thresholds(prices)

    for unit in units:
        efficiency = unit["efficiency"]
        power = unit["power_gw"]
        capacity = unit["energy_gwh"]

        # Ohne Preisspreizung gibt es nichts zu verdienen. Ohne diese Prüfung
        # gälte in einem flachen Preisverlauf dieselbe Stunde als billig und als
        # teuer, und der Speicher liefe ohne Sinn und Zweck.
        spread = expensive - cheap
        # Lohnt sich der Zyklus? Aus einer eingespeicherten MWh wird nur
        # `efficiency` MWh, dazu kommen die variablen Kosten.
        worthwhile = expensive * efficiency - cheap - unit.get("var_om", 0.0)
        if power <= 0 or capacity <= 0 or spread <= MIN_PRICE_SPREAD or worthwhile <= 0:
            per_unit.append({"id": unit["id"], "name": unit["name"], "used": False,
                             "charged_gwh": 0.0, "discharged_gwh": 0.0, "cycles": 0.0,
                             "reason": "Der Preisabstand deckt die Speicherverluste nicht."})
            continue

        level = 0.0
        charged = 0.0
        discharged = 0.0
        for hour, price in enumerate(prices):
            if price <= cheap and level < capacity - 1e-9:
                # Was ankommt, ist um den Wirkungsgrad kleiner als das Entnommene.
                amount = min(power, (capacity - level) / efficiency)
                charge[hour] += amount
                level += amount * efficiency
                charged += amount
            elif price >= expensive and level > 1e-9:
                amount = min(power, level)
                discharge[hour] += amount
                level -= amount
                discharged += amount

        per_unit.append({
            "id": unit["id"], "name": unit["name"], "used": discharged > 0,
            "charged_gwh": round(charged, 2), "discharged_gwh": round(discharged, 2),
            "cycles": round(discharged / capacity, 2) if capacity else 0.0,
            "final_level_gwh": round(level, 2),
        })

    return {
        "charge_gw": charge,
        "discharge_gw": discharge,
        "cheap_threshold": round(cheap, 2),
        "expensive_threshold": round(expensive, 2),
        "units": per_unit,
    }


# ---------------------------------------------------- Vergleich mit der Realität


def compare_with_actual(model_prices: List[float],
                        actual_prices: List[Optional[float]]) -> Optional[Dict]:
    """Modellpreis gegen tatsächlichen Börsenpreis.

    Der ehrlichste Prüfstein für ein Strommarktmodell: Wie nah kommt die
    Rechnung an das, was wirklich bezahlt wurde? Verglichen werden nur Stunden,
    für die beide Werte vorliegen.

    Drei Kennzahlen, die Verschiedenes aussagen:

    * mittlere Abweichung (MAE) — wie weit daneben, ohne Vorzeichen
    * Verzerrung (Bias) — rechnet das Modell systematisch zu hoch oder zu tief?
    * Korrelation — trifft es wenigstens den Verlauf, auch wenn das Niveau
      danebenliegt? Für ein Lernmodell ist das oft der interessantere Wert.
    """
    paired = [(m, a) for m, a in zip(model_prices, actual_prices) if a is not None]
    if len(paired) < 2:
        return None

    model = [m for m, _ in paired]
    actual = [a for _, a in paired]
    count = len(paired)

    mean_model = sum(model) / count
    mean_actual = sum(actual) / count

    absolute_error = sum(abs(m - a) for m, a in paired) / count
    bias = mean_model - mean_actual

    covariance = sum((m - mean_model) * (a - mean_actual) for m, a in paired)
    spread_model = sum((m - mean_model) ** 2 for m in model) ** 0.5
    spread_actual = sum((a - mean_actual) ** 2 for a in actual) ** 0.5
    correlation = (covariance / (spread_model * spread_actual)
                   if spread_model > 0 and spread_actual > 0 else None)

    return {
        "hours_compared": count,
        "mean_model": round(mean_model, 2),
        "mean_actual": round(mean_actual, 2),
        "mean_absolute_error": round(absolute_error, 2),
        "bias": round(bias, 2),
        "correlation": round(correlation, 3) if correlation is not None else None,
        "actual_price_eur_mwh": actual_prices,
    }


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

    hourly_available = [{
        "wind": wind_avail[h],
        "solar": solar_avail[h],
        "hydro_factor": series.hydro_factor,
    } for h in range(n)]

    for h in range(n):
        residual.append(round(demand[h] - wind_avail[h] - solar_avail[h], 3))

    # Speicher koppelt die Stunden: Was nachts eingespeichert wird, fehlt nachts
    # und steht abends zur Verfügung. Deshalb zwei Durchgänge — erst die Preise
    # ohne Speicher als Entscheidungsgrundlage, dann der Einsatz mit ihm.
    # Die Vereinfachung dabei: Der Speicher plant anhand der Preise, die ohne
    # ihn entstanden wären, und sieht seine eigene Wirkung nicht voraus.
    preliminary_prices = [dispatch_hour(demand[h], blocks, hourly_available[h])["price"]
                          for h in range(n)]
    storage = plan_storage(preliminary_prices, STORAGE_UNITS)
    charge = storage["charge_gw"]
    discharge = storage["discharge_gw"]

    for h in range(n):
        # Laden erhöht die Nachfrage, Entladen bedient einen Teil davon.
        net_demand = max(demand[h] + charge[h] - discharge[h], 0.0)
        hour = dispatch_hour(net_demand, blocks, hourly_available[h])

        for category, value in hour["generation"].items():
            generation[category][h] += value
        generation["speicher"][h] = discharge[h]

        emissions_t += hour["emissions_t"]
        curtailed.append(round(hour["curtailed_gw"], 3))
        curtailed_gwh += hour["curtailed_gw"]
        if hour["curtailed_gw"] > 0.01:
            surplus_hours += 1
        if hour["unserved_gw"] > 1e-6:
            scarcity_hours += 1
        prices.append(round(hour["price"], 2))

    # Bei echten Messwerten lässt sich das Ergebnis am Markt messen — und
    # daneben stellen, wie gut eigens dafür gebaute Vorhersagemodelle treffen.
    validation = None
    forecasts: Dict[str, Dict] = {}
    if params["source"] == sources.HISTORICAL and params.get("start_ts") is not None:
        actual = sources.actual_prices(params["start_ts"], n)
        if actual:
            validation = compare_with_actual(prices, actual)
            for model, values in sources.model_forecasts(params["start_ts"], n).items():
                vergleich = compare_with_actual(
                    [v if v is not None else 0.0 for v in values],
                    [a if v is not None else None for v, a in zip(values, actual)])
                forecasts[model] = {
                    "label": FORECAST_LABELS.get(model, model),
                    "values": values,
                    "comparison": vergleich,
                }

    generation = {cat: [round(v, 3) for v in series] for cat, series in generation.items()}
    total_demand = sum(demand)
    charged_gwh = sum(charge)
    discharged_gwh = sum(discharge)
    # Erzeugt werden muss die Nachfrage plus das, was in die Speicher geht.
    total_generation = total_demand + charged_gwh
    renewable_gen = sum(generation["wind"]) + sum(generation["solar"]) + sum(generation["sonstige_ee"])
    price_weighted = sum(p * d for p, d in zip(prices, demand)) / total_demand if total_demand else 0.0

    return {
        "timestamps": series.timestamps,
        "demand_gw": demand,
        "residual_load_gw": residual,
        "generation_gw": generation,
        "curtailed_gw": curtailed,
        "storage_charge_gw": [round(v, 3) for v in charge],
        "storage_discharge_gw": [round(v, 3) for v in discharge],
        "storage": {"units": storage["units"],
                    "cheap_threshold": storage["cheap_threshold"],
                    "expensive_threshold": storage["expensive_threshold"]},
        "price_eur_mwh": prices,
        "available_gw": {"wind": wind_avail, "solar": solar_avail},
        "categories": FLEET["categories"],
        "kpis": {
            "mean_price": round(price_weighted, 2),
            "min_price": min(prices) if prices else 0.0,
            "max_price": max(prices) if prices else 0.0,
            "renewable_share": round(100.0 * renewable_gen / total_generation, 1) if total_generation else 0.0,
            "emissions_kt": round(emissions_t / 1000.0, 1),
            "emission_intensity_g_kwh": round(emissions_t / total_demand, 0) if total_demand else 0.0,
            "curtailed_gwh": round(curtailed_gwh, 1),
            "surplus_hours": surplus_hours,
            "negative_price_hours": sum(1 for p in prices if p < 0),
            "scarcity_hours": scarcity_hours,
            "demand_twh": round(total_demand / 1000.0, 2),
            "storage_charged_gwh": round(charged_gwh, 1),
            "storage_discharged_gwh": round(discharged_gwh, 1),
            "storage_losses_gwh": round(charged_gwh - discharged_gwh, 1),
        },
        "params": params,
        "season_label": series.label,
        "source": series.source,
        "series_meta": series.meta,
        "validation": validation,
        "forecasts": forecasts,
        # Zeitstempel oben sind UTC — hierin gehören sie angezeigt.
        "display_timezone": series.display_timezone,
    }


def glossary() -> List[Dict]:
    path = os.path.join(os.path.dirname(__file__), "static_data", "glossary.json")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)
