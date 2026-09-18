"""Abruf der SMARD-Daten der Bundesnetzagentur — nur mit der Standardbibliothek.

SMARD veröffentlicht die realisierte Erzeugung, den Stromverbrauch und die
Großhandelspreise wochenweise als JSON. Zwei Schritte je Zeitreihe:

    1. index_hour.json  nennt alle verfügbaren Wochen (Zeitstempel in Millisekunden)
    2. je Woche eine Datei mit 168 Stundenwerten

Die Filter-Nummern sind gegen die echten Daten geprüft worden: Photovoltaik
liegt nachts bei null, Biomasse läuft nahezu konstant durch, Kernenergie
antwortet seit der Abschaltung mit 404, und die Summe der Erzeugungsarten
trifft die ausgewiesene Gesamterzeugung.

Für den Nettoexport (4629) ist die Energiebilanz nachgerechnet worden:
Erzeugung minus Netzlast minus Nettoexport minus Pumpspeicherverbrauch bleibt
im Mittel unter einem Gigawatt — der Rest sind Netzverluste und Eigenversorgung.
Positive Werte bedeuten Export, negative Import.

Quelle: SMARD.de, Bundesnetzagentur. Bei Weiterverwendung ist die Quelle zu
nennen; vor einer kommerziellen Nutzung sind die Nutzungsbedingungen zu prüfen.
"""

import json
import time
import urllib.error
import urllib.request
from typing import Dict, List, Optional, Tuple

BASE_URL = "https://www.smard.de/app/chart_data"
REGION = "DE"
RESOLUTION = "hour"
USER_AGENT = "energiewende-lernprojekt/1.0 (privates Lernprojekt)"
TIMEOUT = 30.0
RETRIES = 3

# Interner Name -> SMARD-Filter. "unit" ist die Einheit der Rohwerte.
SERIES: Dict[str, Dict] = {
    "load":               {"filter": 410,  "unit": "MW", "label": "Stromverbrauch: Gesamt (Netzlast)"},
    "residual_load":      {"filter": 4359, "unit": "MW", "label": "Stromverbrauch: Residuallast"},
    "price":              {"filter": 4169, "unit": "EUR/MWh", "label": "Großhandelspreis Day-Ahead"},
    "wind_onshore":       {"filter": 4067, "unit": "MW", "label": "Erzeugung: Wind Onshore"},
    "wind_offshore":      {"filter": 1225, "unit": "MW", "label": "Erzeugung: Wind Offshore"},
    "solar":              {"filter": 4068, "unit": "MW", "label": "Erzeugung: Photovoltaik"},
    "biomass":            {"filter": 4066, "unit": "MW", "label": "Erzeugung: Biomasse"},
    "hydro":              {"filter": 1226, "unit": "MW", "label": "Erzeugung: Wasserkraft"},
    "lignite":            {"filter": 1223, "unit": "MW", "label": "Erzeugung: Braunkohle"},
    "hard_coal":          {"filter": 4069, "unit": "MW", "label": "Erzeugung: Steinkohle"},
    "natural_gas":        {"filter": 4071, "unit": "MW", "label": "Erzeugung: Erdgas"},
    "pumped_storage":     {"filter": 4070, "unit": "MW", "label": "Erzeugung: Pumpspeicher"},
    "nuclear":            {"filter": 1224, "unit": "MW", "label": "Erzeugung: Kernenergie"},
    "other_conventional": {"filter": 1227, "unit": "MW", "label": "Erzeugung: Sonstige Konventionelle"},
    "other_renewable":    {"filter": 1228, "unit": "MW", "label": "Erzeugung: Sonstige Erneuerbare"},
    "net_export":         {"filter": 4629, "unit": "MW", "label": "Kommerzieller Nettoexport"},
    "pumped_load":        {"filter": 4387, "unit": "MW", "label": "Stromverbrauch: Pumpspeicher"},
}

# Was das Modell mindestens braucht. Der Rest ist für Vergleich und Validierung.
CORE_SERIES = ("load", "wind_onshore", "wind_offshore", "solar", "price", "net_export")

# Zeitreihen, aus denen die Merit-Order-Prüfung später den echten Mix nachbaut.
GENERATION_SERIES = ("wind_onshore", "wind_offshore", "solar", "biomass", "hydro",
                     "lignite", "hard_coal", "natural_gas", "pumped_storage",
                     "other_conventional", "other_renewable")


class SmardError(RuntimeError):
    """Abruf endgültig fehlgeschlagen — der Aufrufer entscheidet, ob das schlimm ist."""


def _request(url: str) -> Optional[bytes]:
    """Eine URL holen. None bedeutet: Es gibt dort nichts (404), das ist kein Fehler."""
    last_error = None
    for attempt in range(RETRIES):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            if error.code == 404:
                return None
            last_error = error
        except OSError as error:
            # Bewusst weit gefasst. In Python 3.9 ist `socket.timeout` nicht
            # dasselbe wie `TimeoutError`: Läuft die Wartezeit im SSL-Lesen ab,
            # kommt ein nacktes TimeoutError heraus, das eine engere Klausel
            # durchlässt — der Abruf bricht dann mitten im Lauf ab, statt es
            # noch einmal zu versuchen. Alle hier gemeinten Fehler (URLError,
            # socket.timeout, ConnectionError, TimeoutError) sind OSError.
            last_error = error
        if attempt < RETRIES - 1:
            time.sleep(2 ** attempt)  # 1s, 2s — SMARD nicht bedrängen
    raise SmardError("Abruf fehlgeschlagen: %s (%s)" % (url, last_error))


def _filter_id(name: str) -> int:
    if name not in SERIES:
        raise KeyError("Unbekannte Zeitreihe: %s" % name)
    return SERIES[name]["filter"]


def available_weeks(name: str) -> List[int]:
    """Verfügbare Wochenanfänge als Unix-Sekunden, aufsteigend."""
    filter_id = _filter_id(name)
    url = "%s/%d/%s/index_%s.json" % (BASE_URL, filter_id, REGION, RESOLUTION)
    body = _request(url)
    if body is None:
        return []
    stamps = json.loads(body).get("timestamps", [])
    return sorted(int(ms) // 1000 for ms in stamps)


def fetch_week(name: str, week_start: int) -> List[Tuple[int, Optional[float]]]:
    """Stundenwerte einer Woche als (Unix-Sekunden, Wert).

    Fehlende Werte kommen als None durch und werden auch so gespeichert —
    eine Lücke ist eine Information, kein Grund zum Raten.
    """
    filter_id = _filter_id(name)
    url = "%s/%d/%s/%d_%s_%s_%d.json" % (
        BASE_URL, filter_id, REGION, filter_id, REGION, RESOLUTION, week_start * 1000)
    body = _request(url)
    if body is None:
        return []
    series = json.loads(body).get("series", [])
    return [(int(ms) // 1000, None if value is None else float(value))
            for ms, value in series]


def describe(name: str) -> Dict:
    info = dict(SERIES[name])
    info["name"] = name
    return info
