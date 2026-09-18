"""Historische Brennstoff- und CO₂-Preise — was eine Kilowattstunde Wärme kostet.

Bis hierher rechnete das Modell mit einem festen Gaspreis und einem festen
CO₂-Preis. Für einen Regler ist das richtig: Wer wissen will, was ein CO₂-Preis
von 150 Euro anrichtet, soll ihn einstellen können. Für den Vergleich mit echten
Preisen ist es falsch. 2023 kostete Gas zeitweise das Doppelte von 2024, und ein
Modell mit festem Gaspreis rechnet dann systematisch zu billig — im Frühjahr
2023 lag es um gut 40 Euro je Megawattstunde daneben.

Deshalb liegt hier eine Monatstabelle. Sie wird nicht zur Laufzeit geholt,
sondern von `fuel_ingest.py` aus öffentlichen Quellen gebaut und als JSON
mitgeliefert. Die Website liest nur.

Quellen und warum gerade diese:

    CO₂    EEX-Auktionen des europäischen Emissionshandels. Die Auktionen sind
           der Primärmarkt; ihre Zuschlagspreise folgen dem Börsenpreis eng und
           sind ohne Lizenz frei verfügbar.
    Gas    Weltbank-Rohstofftabelle ("Pink Sheet"), Reihe "Natural gas, Europe".
           Das ist der TTF-Preis in US-Dollar je mmbtu, umgerechnet über den
           EZB-Referenzkurs.
    Kohle  ebenda, Reihe "Coal, South African". Ein Näherungswert für den
           europäischen Importpreis; Rotterdam (API 2) ist nicht frei zu haben.

Braunkohle steht nicht in der Tabelle. Sie wird im Tagebau neben dem Kraftwerk
gefördert und nicht gehandelt, ihr Preis ist deshalb keine Marktgröße und
bleibt beim festen Wert aus power_plants.json.
"""

import json
import os
from datetime import datetime, timezone
from typing import Dict, Optional

_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                     "static_data", "fuel_prices.json")

_CACHE: Optional[Dict] = None


def table() -> Dict:
    """Die Monatstabelle, einmal geladen."""
    global _CACHE
    if _CACHE is None:
        if os.path.exists(_PATH):
            with open(_PATH, encoding="utf-8") as handle:
                _CACHE = json.load(handle)
        else:
            _CACHE = {"months": {}, "_quellen": {}}
    return _CACHE


def available() -> bool:
    return bool(table().get("months"))


def month_key(ts: int) -> str:
    """Zu welchem Monat gehört dieser Zeitpunkt? Format 'JJJJ-MM', UTC."""
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m")


# Wie viele Monate darf ein Preis fortgeschrieben werden, wenn der aktuelle
# fehlt? Die Weltbank veröffentlicht ihre Tabelle mit einigen Monaten Verzug,
# die CO₂-Auktionen laufen dagegen wöchentlich. Ohne Fortschreibung stünde für
# den jüngsten Zeitraum — also genau den, den die Seite zuerst zeigt — der
# Vorgabewert von 32 €/MWh, während Gas tatsächlich das Doppelte kostete.
# Drei Monate sind eine Grenze, jenseits derer ein Rohstoffpreis nichts mehr
# über die Gegenwart aussagt.
MAX_CARRY_FORWARD = 3

FIELDS = ("co2_eur_per_t", "gas_eur_per_mwh_th", "coal_eur_per_mwh_th")


def _earlier(key: str, months: int) -> str:
    """Monatsschlüssel, `months` Monate vor `key`."""
    year, month = int(key[:4]), int(key[5:7])
    total = year * 12 + (month - 1) - months
    return "%04d-%02d" % (total // 12, total % 12 + 1)


def for_month(key: str) -> Optional[Dict]:
    """Preise eines Monats, oder None, wenn gar nichts dazu vorliegt.

    Fehlt ein einzelner Wert, wird er aus dem jüngsten davorliegenden Monat
    fortgeschrieben — aber höchstens drei Monate weit, und immer sichtbar: Der
    Rückgabewert nennt unter `carried_forward`, welcher Wert von wann stammt.
    Stillschweigend ersetzt wird nichts; genau solche unsichtbaren Ersetzungen
    haben die Bewertung des Modells schon einmal verfälscht.
    """
    months = table().get("months", {})
    entry = dict(months.get(key) or {})
    carried = {}
    for field in FIELDS:
        if field in entry:
            continue
        for back in range(1, MAX_CARRY_FORWARD + 1):
            older = months.get(_earlier(key, back))
            if older and field in older:
                entry[field] = older[field]
                carried[field] = _earlier(key, back)
                break
    if not entry:
        return None
    if carried:
        entry["carried_forward"] = carried
    return entry


def for_timestamp(ts: int) -> Optional[Dict]:
    return for_month(month_key(ts))


def span() -> Optional[Dict]:
    """Von wann bis wann reicht die Tabelle?"""
    months = sorted(table().get("months", {}))
    if not months:
        return None
    return {"first": months[0], "last": months[-1], "count": len(months)}


def sources() -> Dict:
    return dict(table().get("_quellen", {}))
