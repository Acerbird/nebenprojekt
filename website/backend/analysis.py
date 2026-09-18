"""Merit-Order und stündlicher Kraftwerkseinsatz.

Das Modell ist bewusst klein gehalten, aber in der Struktur echt: Grenzkosten
werden aus Brennstoffpreis, Wirkungsgrad und CO₂-Preis gerechnet, die Nachfrage
wird Stunde für Stunde aus der billigsten verfügbaren Leistung gedeckt, und der
Preis ist der des letzten benötigten Blocks (Einheitspreisverfahren).

Abgebildet sind inzwischen auch Speicher, der Außenhandel, Mindestlasten der
thermischen Blöcke und ein Knappheitsaufschlag. Nicht abgebildet: An- und
Abfahrkosten im Einzelnen, Netzengpässe innerhalb Deutschlands, Reservemärkte.
Die Ergebnisse sind Größenordnungen, keine Prognose.
"""

import json
import os
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from .data import fuel_prices, sources
from .utils import profiles

_DATA_PATH = os.path.join(os.path.dirname(__file__), "static_data", "power_plants.json")

with open(_DATA_PATH, encoding="utf-8") as fh:
    FLEET = json.load(fh)

STORAGE_UNITS = FLEET.get("storage", [])
EXCHANGE = FLEET.get("exchange", {})

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
    "baseline": "Einfache Regel",
    "model_1": "Forecast-Modell 1",
    "model_2": "Forecast-Modell 2",
    "model_3": "Forecast-Modell 3",
}

# Untergrenze der Preissuche. Tiefer bietet niemand, weil dort auch die
# Anlagen mit Einspeisevergütung aussteigen.
PRICE_FLOOR = -500.0

# Gebot der Mindestlast thermischer Blöcke. Wer nachts abfährt, muss morgens
# wieder anfahren; das kostet Brennstoff, Material und Zeit. Solange der
# Verlust je Stunde kleiner ist als ein Start, bleibt der Block im Markt und
# nimmt dafür auch einen negativen Preis hin.
MIN_LOAD_BID = -80.0
# Auch die Mindestlast ist keine Stufe: Ältere Blöcke fahren eher ab als neue.
MIN_LOAD_BID_SPAN = 60.0
# Standardmäßig aus — und das ist ein unbequemes Ergebnis, kein Versehen.
#
# Der Effekt ist real und an SMARD gemessen (siehe power_plants.json). Trotzdem
# wird die Gesamtkennzahl damit schlechter, über 36 Wochen quer durch drei
# Jahre (der 6. jedes Monats 2023 bis 2025, je 168 Stunden):
#
#                        MAE      r     Bias
#     mit Mindestlast   23,92   0,677  -10,40
#     ohne              19,94   0,746   -1,64
#
# Diese eine Zahl verdeckt allerdings, was wirklich passiert. Nach dem
# tatsächlichen Preis aufgeteilt, ergibt sich ein völlig anderes Bild — die
# Verzerrung je Preisklasse, ohne und mit Mindestlast:
#
#     Ist-Preis      Stunden    ohne ML    mit ML
#     unter 0            294      +22,7      +5,0
#     0 bis 30           398      +31,2     +16,3
#     30 bis 60          427      +17,7      +1,2
#     60 bis 90        1.707       +6,9      -2,5
#     90 bis 130       2.336       -5,4     -11,1
#     über 130           886      -40,3     -46,5
#
# Die Mindestlast wirkt also genau dort richtig, wo sie hingehört: In den
# billigen Stunden schrumpft der Fehler auf ein Viertel bis ein Fünfzehntel.
# Sie verliert nur deshalb in der Gesamtkennzahl, weil mehr als die Hälfte
# aller Stunden über 90 Euro liegt — und dort rechnet das Modell ohnehin zu
# billig. Die Mindestlast verursacht diesen zweiten Fehler nicht, sie
# verstärkt ihn.
#
# Drei Erklärungsversuche sind geprüft und widerlegt:
#
# * Doppelzählung mit den sehr tiefen Geboten der Erneuerbaren. Werden die
#   EE-Gebote von -500 auf -60 angehoben, ändert sich fast nichts.
# * Der hinterlegte Park sei zu groß. Wird er auf die gemessene Erzeugung
#   gestutzt, wird alles deutlich schlechter (MAE 23,1 bis 42,7), weil die
#   gemessene Erzeugung zeigt, was gelaufen ist, und nicht, was bereitstand.
#   Auch das gezielte Kürzen allein am teuren Ende hilft nicht (Gasturbine von
#   11 auf 5 GW: MAE 19,94 -> 21,00).
# * Der Knappheitsaufschlag sei zu schwach, um den Preis oben zu halten. Über
#   ein Raster aus Schwelle und Höhe gesucht: Jede Verstärkung verschlechtert
#   MAE und Gleichlauf, egal ob mit oder ohne Mindestlast.
#
# Was in den teuersten Stunden fehlt, ist demnach nichts, was am deutschen
# Park liegt — dort laufen real nur 33 bis 37 der 45 gemessenen Gigawatt,
# während der Preis schon bei 300 Euro steht. Der Rest hängt am europäischen
# Verbund und am Bietverhalten und ist mit diesem Modell nicht zu holen.
#
# Deshalb bleibt die Mindestlast ein Schalter und bleibt aus: Die
# Gesamtkennzahl ist der ehrlichere Maßstab für die Voreinstellung. Wer
# dagegen negative Preise verstehen will, schaltet sie ein — dafür ist sie da.
MIN_LOAD_DEFAULT = False

# Ab welcher Reserve wird es knapp? Unterhalb dieses Anteils freier Leistung
# bieten die letzten Kraftwerke über ihren Grenzkosten — sie wissen, dass ohne
# sie niemand liefert. Ein reines Grenzkostenmodell kennt diesen Aufschlag
# nicht und rechnet Knappheitsstunden deshalb systematisch zu billig.
#
# Beide Zahlen sind gemessen, nicht geschätzt. Über zwölf Wochen quer durch
# 2023 bis 2025, nach tatsächlichem Preis sortiert:
#
#     Ist-Preis      mittlere Reserve   Modell ohne Aufschlag
#     unter 0                  59 %                     -13
#     60 bis 90                48 %                      74
#     90 bis 130               40 %                      88
#     130 bis 200              31 %                      95
#     über 200                 24 %                     106
#
# Die Reserve fällt also selbst in den teuersten Stunden nie unter ein Fünftel;
# eine Schwelle von zehn oder zwölf Prozent hätte nie gegriffen.
#
# Die beiden Werte unten sind auf 2023/24 gesucht und an 2025 geprüft worden,
# danach über alle 36 Wochen gegengerechnet. Dort schneiden sie so ab:
#
#     ohne Aufschlag      MAE 21,33   r 0,705   Bias  -7,03
#     0,40 / 120          MAE 19,94   r 0,746   Bias  -1,64
#     0,35 / 180          MAE 20,05   r 0,759   Bias  -2,73
#     0,45 / 120          MAE 20,58   r 0,734   Bias  +1,89
#
# Der Aufschlag nimmt also den größten Teil der systematischen Unterschätzung
# weg und verbessert dabei auch den Gleichlauf. Mehr ist nicht besser: Über ein
# Raster bis 0,70 Schwelle und 300 Euro gesucht, verschlechtert jede weitere
# Verstärkung beide Kennzahlen deutlich.
SCARCITY_MARGIN = 0.40
# Aufschlag, wenn die Reserve vollständig aufgebraucht wäre.
SCARCITY_MARKUP_MAX = 120.0

DEFAULTS = {
    "min_load": MIN_LOAD_DEFAULT,
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


def _as_bool(value) -> bool:
    """Wahrheitswerte aus einer Adresszeile lesen.

    Aus einer URL kommt alles als Zeichenkette an, und `bool("false")` ist wahr.
    Deshalb werden die üblichen Verneinungen ausdrücklich abgefangen.
    """
    if isinstance(value, str):
        return value.strip().lower() not in ("", "0", "false", "nein", "off")
    return bool(value)


def clamp(value: float, bounds) -> float:
    low, high = bounds
    return max(low, min(high, value))


def normalise_params(**kwargs) -> Dict:
    """Werte in gültige Bereiche zwingen, damit die API nie mit Müll rechnet."""
    params = dict(DEFAULTS)
    # Ob ein Preisregler angefasst wurde, muss vor dem Auffüllen mit
    # Standardwerten feststehen: Nur dann lässt sich später unterscheiden, ob
    # 32 Euro Gaspreis eine Eingabe sind oder bloß der Vorgabewert.
    for key in ("co2_price", "gas_price", "wind_gw", "solar_gw"):
        params[key + "_set"] = kwargs.get(key) is not None
    for key, value in kwargs.items():
        if value is not None:
            params[key] = value
    for key, bounds in LIMITS.items():
        params[key] = clamp(float(params[key]), bounds)
    params["hours"] = int(params["hours"])
    params["min_load"] = _as_bool(params["min_load"])
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
    # Echte Messwerte verdienen echte Brennstoffpreise. Bei erzeugten Profilen
    # gibt es keinen Monat, auf den man sie beziehen könnte.
    params["fuel_source"] = ("historical"
                             if params["source"] == sources.HISTORICAL
                             and fuel_prices.available()
                             else "fixed")
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
                gas_price: Optional[float] = None,
                coal_price: Optional[float] = None) -> Tuple[float, float]:
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
        # Braunkohle bleibt außen vor: Sie wird im Tagebau neben dem Kraftwerk
        # gefördert und nicht gehandelt, ihr Preis ist keine Marktgröße.
        if plant["fuel"] == "steinkohle" and coal_price is not None:
            price_th = coal_price
        emission_th = fuel["emission_t_per_mwh_th"]
        variable_th = price_th + co2_price * emission_th
        # Günstigster Preis beim besten Wirkungsgrad, teuerster beim schlechtesten.
        low += variable_th / plant["efficiency_high"]
        high += variable_th / plant["efficiency_low"]

    return round(low, 2), round(max(high, low), 2)


def marginal_cost(plant: Dict, co2_price: float, gas_price: Optional[float] = None,
                  coal_price: Optional[float] = None) -> float:
    """Mittlere Grenzkosten eines Blocks — für Vergleiche und Erklärseiten."""
    low, high = block_costs(plant, co2_price, gas_price, coal_price)
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


# --------------------------------------------------- Brennstoffpreise

def fuel_costs_for(ts: Optional[int], params: Dict) -> Dict:
    """Welche Brennstoff- und CO₂-Preise gelten in dieser Stunde?

    Zwei Betriebsarten, und der Unterschied ist wichtig:

    * Hat der Benutzer einen Regler angefasst, gilt sein Wert — sonst wäre die
      Frage "Was macht ein CO₂-Preis von 150 Euro?" nicht zu stellen.
    * Sonst gelten bei echten Messwerten die Preise des jeweiligen Monats. Ein
      fester Gaspreis von 32 Euro rechnet das Frühjahr 2023 um gut 40 Euro je
      Megawattstunde zu billig, weil Gas damals das Doppelte kostete.

    Fehlt ein Monat in der Tabelle, bleibt es beim eingestellten Wert. Geraten
    wird nichts.
    """
    used = {
        "co2_price": params["co2_price"],
        "gas_price": params["gas_price"],
        "coal_price": None,
        # Je Größe, woher der Wert stammt: "fixed" für den Regler, "historical"
        # für den gemessenen Monatswert. Ohne diese Unterscheidung stünde in der
        # Herkunftszeile der Vorgabewert 32 €/MWh als gemessener Gaspreis.
        "used": {},
        "carried_forward": {},
        "origin": "fixed",
        "month": None,
    }
    if ts is None or params.get("fuel_source") != "historical":
        return used

    month = fuel_prices.for_timestamp(ts)
    used["month"] = fuel_prices.month_key(ts)
    if not month:
        return used

    carried = month.get("carried_forward", {})
    for name, field, gesetzt in (("co2_price", "co2_eur_per_t", "co2_price_set"),
                                 ("gas_price", "gas_eur_per_mwh_th", "gas_price_set"),
                                 ("coal_price", "coal_eur_per_mwh_th", None)):
        if gesetzt and params.get(gesetzt):
            used["used"][name] = "fixed"
            continue
        if field not in month:
            continue
        used[name] = month[field]
        used["used"][name] = "historical"
        if field in carried:
            used["carried_forward"][name] = carried[field]
    if "historical" in used["used"].values():
        used["origin"] = "historical"
    return used


def merit_order(co2_price: float, gas_price: float,
                wind_gw: float = 0.0, solar_gw: float = 0.0,
                coal_price: Optional[float] = None,
                min_load: bool = MIN_LOAD_DEFAULT) -> List[Dict]:
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
        low, high = block_costs(plant, co2_price, gas_price, coal_price)
        emission = round(emission_intensity(plant), 3)
        # Mindestlast: Ein laufender Großblock lässt sich nicht beliebig weit
        # herunterfahren, und ihn ganz abzustellen kostet Stunden und Geld.
        # Dieser Teil der Leistung bietet deshalb weit unter seinen Grenzkosten
        # an — er will im Markt bleiben, nicht verdienen. Genau daraus entstehen
        # die Stunden mit negativen Preisen, in denen trotzdem Kohle läuft.
        floor_share = float(plant.get("min_load_share", 0.0)) if min_load else 0.0
        floor_gw = plant["capacity_gw"] * floor_share
        if floor_gw > 0:
            bid = float(plant.get("min_load_bid", MIN_LOAD_BID))
            blocks.append({
                "id": plant["id"] + "_mindestlast",
                "name": plant["name"] + " (Mindestlast)",
                "category": plant["category"],
                "kind": plant["kind"],
                "capacity_gw": round(floor_gw, 3),
                "cost_low": bid,
                "cost_high": round(min(bid + MIN_LOAD_BID_SPAN, low), 2),
                "emission": emission,
                "note": "Bleibt im Markt, statt abzufahren — bietet deshalb "
                        "auch bei negativen Preisen an.",
            })
        blocks.append({
            "id": plant["id"],
            "name": plant["name"],
            "category": plant["category"],
            "kind": plant["kind"],
            "capacity_gw": round(plant["capacity_gw"] - floor_gw, 3),
            "cost_low": low,
            "cost_high": high,
            "emission": emission,
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


def capacity_at_price(blocks: List[Dict], available: Dict) -> float:
    """Gesamte Leistung, die dieser Park in dieser Stunde aufbieten kann."""
    return sum(block_capacity(block, available) for block in blocks)


def exchange_at_price(price: float) -> float:
    """Nettoexport in GW bei diesem Preis — positiv bei Ausfuhr.

    Der erste Versuch war, den tatsächlich gemessenen Außenhandel als
    zusätzliche Nachfrage einzusetzen. Das ging gründlich schief, und der Grund
    ist lehrreich: Der Export ist nicht die Ursache des Preises, sondern seine
    Folge. Mittags exportiert Deutschland zwölf Gigawatt, *weil* der Preis bei
    minus zehn Euro liegt. Rechnet man diese zwölf Gigawatt als Nachfrage
    hinein, verschwindet der Überschuss, und das Modell sagt plus achtzig statt
    minus zehn — es dreht die Kausalität um.

    Richtig ist der Außenhandel eine preisabhängige Nachfrage: Die Nachbarn
    kaufen, wenn Deutschland billig ist, und verkaufen, wenn es teuer ist.
    Genau das leistet diese Kurve. Sie ist keine Annahme, sondern am Bestand
    gemessen — medianer Nettoexport je Preisklasse über 32.711 Stunden, siehe
    power_plants.json. Zwischen den Stützstellen wird linear interpoliert, an
    den Rändern begrenzt die Kuppelleistung.

    Damit wirkt der Handel in beide Richtungen als Puffer: Im Überschuss
    saugt der Export ihn ab, statt den Preis ins Bodenlose fallen zu lassen;
    in der Knappheit entlastet der Import den heimischen Park.
    """
    curve = EXCHANGE.get("curve")
    if not curve:
        return 0.0
    max_export = float(EXCHANGE.get("max_export_gw", curve[0][1]))
    max_import = float(EXCHANGE.get("max_import_gw", -curve[-1][1]))
    if price <= curve[0][0]:
        value = curve[0][1]
    elif price >= curve[-1][0]:
        value = curve[-1][1]
    else:
        value = curve[-1][1]
        for (p0, v0), (p1, v1) in zip(curve, curve[1:]):
            if p0 <= price <= p1:
                share = (price - p0) / (p1 - p0) if p1 > p0 else 0.0
                value = v0 + share * (v1 - v0)
                break
    return max(-max_import, min(max_export, value))


def demand_at_price(demand_gw: float, price: float, trade: bool) -> float:
    """Gesamte Nachfrage bei diesem Preis: inländische Last plus Nettoexport."""
    if not trade:
        return demand_gw
    return max(demand_gw + exchange_at_price(price), 0.0)


def scarcity_markup(demand_gw: float, capacity_gw: float) -> float:
    """Aufschlag über den Grenzkosten, wenn die Reserve knapp wird.

    Ein reines Grenzkostenmodell nimmt an, dass jedes Kraftwerk zu seinen
    variablen Kosten bietet. Das stimmt, solange reichlich Leistung da ist. Wird
    es eng, weiß der letzte verfügbare Block, dass ohne ihn niemand liefert —
    und bietet darüber. Real sind das die Stunden, in denen der Preis auf 200
    oder 400 Euro springt, ohne dass sich an den Brennstoffkosten etwas geändert
    hätte.

    Der Aufschlag wächst linear mit der Enge. Ein quadratischer Verlauf lag
    näher, traf die Messung aber schlechter: Er bleibt nahe der Schwelle zu
    flach und steigt dann zu spät.
    """
    if capacity_gw <= 0:
        return SCARCITY_MARKUP_MAX
    margin = (capacity_gw - demand_gw) / capacity_gw
    if margin >= SCARCITY_MARGIN:
        return 0.0
    tightness = (SCARCITY_MARGIN - max(margin, 0.0)) / SCARCITY_MARGIN
    return SCARCITY_MARKUP_MAX * tightness


def clear_market(blocks: List[Dict], demand_gw: float, available: Dict,
                 trade: bool = False) -> float:
    """Markträumungspreis: der Preis, bei dem Angebot die Nachfrage deckt.

    Gesucht per Intervallhalbierung. Die Angebotskurve steigt monoton mit dem
    Preis, die Nachfragekurve fällt monoton (höherer Preis, weniger Export) —
    der Schnittpunkt ist deshalb eindeutig und die Halbierung findet ihn. Das
    ist derselbe Gedanke wie beim Einheitspreisverfahren der Börse, nur ohne
    einzelne Gebote.

    Der Knappheitsaufschlag steckt in der Suche, nicht dahinter. Das ist kein
    Schönheitsfehler: Würde er erst auf den geräumten Preis aufgeschlagen,
    gehörte die Handelsmenge zu einem anderen Preis als dem am Ende
    ausgewiesenen — der Markt räumte eine Menge und wiese eine andere aus.
    Innerhalb der Suche bleibt beides beisammen. Monoton bleibt es auch: Mit
    steigendem Preis sinkt die Nachfrage, damit steigt die Reserve, damit
    fällt der Aufschlag — der um ihn bereinigte Gebotspreis steigt also.
    """
    capacity = capacity_at_price(blocks, available)

    def gap(price: float) -> float:
        """Angebot minus Nachfrage bei diesem Preis. Die Nullstelle ist gesucht."""
        wanted = demand_at_price(demand_gw, price, trade)
        extra = scarcity_markup(wanted, capacity) if price > 0 else 0.0
        return supply_at_price(blocks, price - extra, available) - wanted

    low, high = PRICE_FLOOR, SCARCITY_PRICE
    if gap(high) < -1e-9:
        return SCARCITY_PRICE          # selbst zum Höchstpreis reicht es nicht
    if gap(low) >= 0:
        return PRICE_FLOOR             # schon zum Mindestpreis ist zu viel da
    # 40 Halbierungen bringen die Spanne von rund 900 €/MWh weit unter einen
    # Cent — mehr Schritte kosten nur Rechenzeit.
    for _ in range(40):
        middle = (low + high) / 2
        if gap(middle) < 0:
            low = middle
        else:
            high = middle
    # `high` ist stets ein Preis, zu dem das Angebot reicht, `low` einer, zu dem
    # es nicht reicht. Zurückgegeben wird deshalb `high`: der niedrigste Preis,
    # der die Nachfrage deckt. Bei einem Block ohne Gebotsspanne springt das
    # Angebot, und die Mitte beider Werte läge womöglich knapp unterhalb des
    # Sprungs — dann bliebe der Block trotz Bedarf stehen.
    return high


def dispatch_hour(demand_gw: float, blocks: List[Dict], available: Dict,
                  trade: bool = False) -> Dict:
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
    price = clear_market(blocks, demand_gw, available, trade)
    # Beim geräumten Preis steht auch fest, wie viel die Nachbarn nehmen.
    exchange = exchange_at_price(price) if trade else 0.0
    demand_gw = max(demand_gw + exchange, 0.0)

    # Der Aufschlag steckt schon im geräumten Preis. Hier wird er nur noch
    # einmal ausgerechnet, um ihn ausweisen zu können — er ist der Abstand
    # zwischen dem, was der Grenzblock kostet, und dem, was gezahlt wird.
    total_capacity = capacity_at_price(blocks, available)
    markup = scarcity_markup(demand_gw, total_capacity) if price > 0 else 0.0
    # Geboten wird nach Grenzkosten. Wer läuft, entscheidet deshalb der Preis
    # ohne den Aufschlag: Der Aufschlag ist Knappheitsrente, kein Kostenblock.
    bid_price = price - markup

    # Einsatz je Block beim geräumten Preis.
    used_by_block = []
    produced = 0.0
    for block in blocks:
        capacity = block_capacity(block, available)
        if capacity <= 0:
            continue
        used = capacity * bid_share(block, bid_price)
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
        "scarcity_markup": round(markup, 2),
        "reserve_gw": round(max(total_capacity - demand_gw, 0.0), 3),
        "net_export_gw": round(exchange, 3),
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


# Um wie viel darf ein Regler vom tatsächlichen Stand abweichen, bevor das
# Szenario nicht mehr das abbildet, was in dieser Woche wirklich passiert ist?
# Zehn Prozent lassen Rundung und Interpolation der installierten Leistung
# durchgehen und fangen jeden ernstgemeinten Zubau ab.
COUNTERFACTUAL_TOLERANCE = 0.10


def counterfactual_reasons(params: Dict, series_meta: Dict,
                           fuel_used: Dict) -> List[str]:
    """Worin weicht dieses Szenario von der Wirklichkeit ab?

    Der Vergleich mit dem tatsächlich gezahlten Preis ist nur dann eine Aussage
    über die Güte des Modells, wenn das Szenario auch die Wirklichkeit meint.
    Wer die Windleistung verdoppelt, rechnet eine andere Welt durch — die
    Abweichung zum echten Preis misst dann nicht mehr das Modell, sondern den
    Unterschied der beiden Welten.

    Ohne diesen Hinweis liest sich eine mittlere Abweichung von 58 €/MWh wie ein
    schlechtes Modell, obwohl sie nur bedeutet: In dieser Woche standen eben
    keine 140 Gigawatt Wind.
    """
    gruende = []
    installed = (series_meta or {}).get("installed_gw") or {}
    for name, regler in (("wind", "wind_gw"), ("solar", "solar_gw")):
        echt = installed.get(name)
        if not echt or not params.get(regler + "_set"):
            continue
        gewaehlt = params.get(regler)
        if gewaehlt is None or abs(gewaehlt - echt) <= COUNTERFACTUAL_TOLERANCE * echt:
            continue
        gruende.append("%s %s GW statt der tatsächlichen %s GW"
                       % ("Wind" if name == "wind" else "Photovoltaik",
                          round(gewaehlt), round(echt)))

    if (series_meta or {}).get("load_scaled_by"):
        gruende.append("die gemessene Last wurde gestreckt")

    benutzt = {}
    for costs in (fuel_used or {}).values():
        benutzt.update(costs.get("used") or {})
    if benutzt.get("co2_price") == "fixed":
        gruende.append("ein eingestellter CO₂-Preis statt des tatsächlichen")
    if benutzt.get("gas_price") == "fixed":
        gruende.append("ein eingestellter Gaspreis statt des tatsächlichen")
    return gruende


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
             start: Optional[str] = None, min_load: Optional[bool] = None) -> Dict:
    """Stündlicher Einsatz über den gewählten Zeitraum."""
    # Ob der Höchstlast-Regler angefasst wurde, muss vor dem Auffüllen mit
    # Standardwerten feststehen — sonst wäre bei echten Daten nicht mehr
    # erkennbar, ob die Last gestreckt werden soll.
    scale_to_peak = peak_load_gw is not None
    params = normalise_params(wind_gw=wind_gw, solar_gw=solar_gw, co2_price=co2_price,
                              gas_price=gas_price, peak_load_gw=peak_load_gw,
                              hours=hours, season=season, source=source, start=start,
                              min_load=min_load)
    n = params["hours"]

    # Die Zeitreihen kommen aus einer austauschbaren Quelle. Ab hier ist
    # gleichgültig, ob sie erzeugt oder aus echten Messwerten gelesen wurden.
    series = resolve_series(params, scale_to_peak)
    n = series.hours
    params["hours"] = n

    # Bei echten Messwerten gilt der tatsächliche Ausbaustand, solange niemand
    # den Regler angefasst hat. Die Vorgabewerte sind eine Momentaufnahme und
    # veralten: 90 GW Photovoltaik waren 2024 richtig, im Januar 2025 standen
    # 101. Über die 36 Prüfwochen sinkt die mittlere Abweichung dadurch von
    # 21,49 auf 19,94 €/MWh — der Fehler steckte nicht im Modell, sondern in
    # einer veralteten Zahl.
    params["capacity_source"] = "fixed"
    installed = (series.meta or {}).get("installed_gw") or {}
    if params["source"] == sources.HISTORICAL and installed:
        for name, regler in (("wind", "wind_gw"), ("solar", "solar_gw")):
            if not params.get(regler + "_set") and installed.get(name):
                params[regler] = clamp(float(installed[name]), LIMITS[regler])
                params["capacity_source"] = "historical"

    demand = series.load_gw
    wind_avail = series.wind_gw(params["wind_gw"])
    solar_avail = series.solar_gw(params["solar_gw"])

    # Deutschland ist keine Insel. In Exportstunden muss der Kraftwerkspark mehr
    # decken als die inländische Last, in Importstunden weniger. Der Außenhandel
    # wird dabei als gegeben genommen, nicht erklärt: Warum die Nachbarn gerade
    # kaufen oder verkaufen, hinge an ihren eigenen Preisen — dafür bräuchte es
    # ein europäisches Modell. Was das Modell davon hat, ist trotzdem viel: Die
    # Exportstunden sind genau die, in denen der Preis sonst zu tief lag.
    # Gehandelt wird nur dort, wo der Kurvenverlauf gemessen ist: im deutschen
    # Markt mit echten Daten. Erzeugte Profile haben keinen Außenhandel.
    trade = bool(EXCHANGE.get("curve")) and params["source"] == sources.HISTORICAL
    measured_export = list(series.net_export_gw) if series.net_export_gw else []
    if len(measured_export) != n:
        measured_export = []

    # Brennstoffpreise können sich über den Zeitraum ändern — ein Fenster kann
    # zwei oder drei Monate berühren. Die Merit-Order wird deshalb je Monat
    # einmal gebaut und für die Stunden dieses Monats wiederverwendet.
    blocks_by_month: Dict[str, List[Dict]] = {}
    hour_blocks: List[List[Dict]] = []
    fuel_used: Dict[str, Dict] = {}
    for h in range(n):
        ts = params["start_ts"] + h * 3600 if params.get("start_ts") is not None else None
        costs = fuel_costs_for(ts, params)
        key = costs["month"] or "fixed"
        if key not in blocks_by_month:
            blocks_by_month[key] = merit_order(
                costs["co2_price"], costs["gas_price"],
                params["wind_gw"], params["solar_gw"], costs["coal_price"],
                params["min_load"])
            fuel_used[key] = costs
        hour_blocks.append(blocks_by_month[key])
    blocks = hour_blocks[0] if hour_blocks else merit_order(
        params["co2_price"], params["gas_price"], params["wind_gw"], params["solar_gw"],
        None, params["min_load"])
    categories = [c["id"] for c in FLEET["categories"]]

    generation = {cat: [0.0] * n for cat in categories}
    prices: List[float] = []
    curtailed: List[float] = []
    residual: List[float] = []
    net_export: List[float] = []

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
        # Residuallast nach üblicher Lesart: inländische Last minus Wind und
        # Photovoltaik. Der Außenhandel steckt bewusst nicht darin, damit die
        # Kurve dieselbe Größe zeigt wie die von SMARD.
        residual.append(round(demand[h] - wind_avail[h] - solar_avail[h], 3))

    # Speicher koppelt die Stunden: Was nachts eingespeichert wird, fehlt nachts
    # und steht abends zur Verfügung. Deshalb zwei Durchgänge — erst die Preise
    # ohne Speicher als Entscheidungsgrundlage, dann der Einsatz mit ihm.
    # Die Vereinfachung dabei: Der Speicher plant anhand der Preise, die ohne
    # ihn entstanden wären, und sieht seine eigene Wirkung nicht voraus.
    preliminary_prices = [dispatch_hour(demand[h], hour_blocks[h], hourly_available[h], trade)["price"]
                          for h in range(n)]
    storage = plan_storage(preliminary_prices, STORAGE_UNITS)
    charge = storage["charge_gw"]
    discharge = storage["discharge_gw"]

    for h in range(n):
        # Laden erhöht die Nachfrage, Entladen bedient einen Teil davon.
        net_demand = max(demand[h] + charge[h] - discharge[h], 0.0)
        hour = dispatch_hour(net_demand, hour_blocks[h], hourly_available[h], trade)

        for category, value in hour["generation"].items():
            generation[category][h] += value
        generation["speicher"][h] = discharge[h]

        net_export.append(hour["net_export_gw"])
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
        vorhersagen = sources.model_forecasts(params["start_ts"], n)

        # Alle Modelle werden an derselben Reihe gemessen, sonst wäre der
        # Vergleich wertlos. Liegen Vorhersagen vor, gilt die Reihe, gegen die
        # sie entwickelt wurden; sonst der SMARD-Preis.
        referenz = sources.reference_prices(params["start_ts"], n) if vorhersagen else None
        massstab = referenz if referenz else actual
        massstab_name = "reference" if referenz else "smard"

        if massstab:
            validation = compare_with_actual(prices, massstab)
            if validation:
                validation["benchmark"] = massstab_name
                # Misst dieser Vergleich das Modell — oder nur den Abstand zu
                # einer Welt, die es nicht gab?
                validation["counterfactual"] = counterfactual_reasons(
                    params, series.meta, fuel_used)
                # Der SMARD-Preis bleibt die angezeigte Kurve.
                validation["actual_price_eur_mwh"] = actual or massstab
            for model, values in vorhersagen.items():
                vergleich = compare_with_actual(
                    [v if v is not None else 0.0 for v in values],
                    [a if v is not None else None for v, a in zip(values, massstab)])
                forecasts[model] = {
                    "label": FORECAST_LABELS.get(model, model),
                    "values": values,
                    "comparison": vergleich,
                }

    generation = {cat: [round(v, 3) for v in series] for cat, series in generation.items()}
    total_demand = sum(demand)
    charged_gwh = sum(charge)
    discharged_gwh = sum(discharge)
    net_export_gwh = sum(net_export)
    # Erzeugt werden muss die inländische Nachfrage, das was in die Speicher
    # geht, und der Nettoexport. Letzterer ist negativ, wenn eingeführt wird.
    total_generation = total_demand + charged_gwh + net_export_gwh
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
        "net_export_gw": [round(v, 3) for v in net_export],
        "measured_net_export_gw": [round(v, 3) for v in measured_export],
        "fuel_costs": {
            "source": params["fuel_source"],
            "months": {key: {k: v for k, v in costs.items() if k != "month"}
                       for key, costs in sorted(fuel_used.items())},
            "table": fuel_prices.span(),
        },
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
            "net_export_gwh": round(net_export_gwh, 1),
            "export_hours": sum(1 for v in net_export if v > 0.01),
            "import_hours": sum(1 for v in net_export if v < -0.01),
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


# Beispieltag für die Marktseite: kräftige Viertelstundenspreizung, negative
# Mittagspreise und Abendspitze — aber ohne die Ausreißer über 500 €/MWh, an
# denen man die Form der Kurve nicht mehr erkennt.
QUARTER_EXAMPLE_DAY = "2026-04-07"


def quarter_prices(date: Optional[str] = None) -> Dict:
    """Stunden- und Viertelstundenpreis eines Tages nebeneinander.

    Beide stammen aus derselben Auktion, nur in verschiedenen Zeitscheiben.
    Nebeneinander gelegt zeigen sie, was der Stundenkontrakt wegmittelt — und
    damit die Spanne, die einem Speicher entgeht, der nur Stunden handelt.

    Vor Oktober 2025 gab es keine Viertelstundenkontrakte; dort steht in der
    Reihe viermal derselbe Stundenpreis, und beide Linien liegen aufeinander.
    """
    from .data import store

    tag = date or QUARTER_EXAMPLE_DAY
    try:
        start = datetime.strptime(tag, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return {"date": tag, "error": "Datum im Format JJJJ-MM-TT erwartet.",
                "quarter": [], "hourly": []}
    start_ts = int(start.timestamp())
    end_ts = start_ts + 24 * 3600

    if not store.exists():
        return {"date": tag, "error": "Keine Messwerte vorhanden.",
                "quarter": [], "hourly": []}
    with store.open_db() as conn:
        viertel = store.read_series(conn, "price_quarter", start_ts, end_ts)
        stunden = store.read_series(conn, "price", start_ts, end_ts)

    # Der Stundenpreis wird auf die Viertelstunden gelegt, damit beide Linien
    # dieselbe x-Achse haben — eine Treppe gegen eine Zickzacklinie.
    je_stunde = {ts: value for ts, value in stunden}
    gestreckt = [je_stunde.get(ts - (ts - start_ts) % 3600) for ts, _ in viertel]

    return {
        "date": tag,
        "timestamps": [ts for ts, _ in viertel],
        "quarter": [value for _, value in viertel],
        "hourly": gestreckt,
        "hourly_raw": [value for _, value in stunden],
        "display_timezone": sources.timezone_name(),
        "source": "SMARD.de, Bundesnetzagentur",
        "note": "Day-Ahead-Preis derselben Auktion, einmal je Stunde und "
                "einmal je Viertelstunde.",
    }


def exchange_curve(step: float = 5.0) -> Dict:
    """Die gemessene Außenhandelskurve, gleichmäßig abgetastet.

    Die Stützstellen in power_plants.json liegen in ungleichen Abständen —
    einfach nebeneinander gezeichnet ergäben sie ein verzerrtes Bild. Deshalb
    tastet diese Funktion dieselbe Kurve in festen Preisschritten ab; die
    Stützstellen kommen zusätzlich mit, damit sichtbar bleibt, wo gemessen
    wurde und wo interpoliert.
    """
    curve = EXCHANGE.get("curve") or []
    if not curve:
        return {"prices": [], "net_export_gw": [], "points": [], "note": ""}
    first, last = curve[0][0], curve[-1][0]
    prices, values = [], []
    price = float(first)
    while price <= last + 1e-9:
        prices.append(round(price, 2))
        values.append(round(exchange_at_price(price), 3))
        price += step
    return {
        "prices": prices,
        "net_export_gw": values,
        "points": [{"price_eur_mwh": p, "net_export_gw": v} for p, v in curve],
        "max_export_gw": EXCHANGE.get("max_export_gw"),
        "max_import_gw": EXCHANGE.get("max_import_gw"),
        "note": EXCHANGE.get("note", ""),
        "source": (FLEET.get("_quellen") or {}).get("aussenhandel", ""),
    }


def stories() -> List[Dict]:
    """Geführte Fragen mit fertigen Parametersätzen.

    Der Simulator beantwortet jede Frage, die man ihm stellt — aber er stellt
    keine. Wer zum ersten Mal darauf schaut, sieht sieben Regler und weiß nicht,
    an welchem er drehen soll. Die Geschichten liefern die Frage mit: Jeder
    Schritt setzt einen Parametersatz und sagt dazu, worauf zu achten ist.

    Die Parameter gehen durch dieselbe Prüfung wie jede andere Eingabe. Eine
    Geschichte kann das Modell also nicht in einen Zustand bringen, den ein
    Benutzer nicht auch von Hand herstellen könnte.
    """
    path = os.path.join(os.path.dirname(__file__), "static_data", "stories.json")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def glossary() -> List[Dict]:
    path = os.path.join(os.path.dirname(__file__), "static_data", "glossary.json")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)
