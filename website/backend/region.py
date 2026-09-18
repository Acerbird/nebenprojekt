"""Regionsabhängige Einstellungen.

Alle Zeitpunkte im Modell sind UTC — in der Datenbank als Unix-Sekunden, in der
API als ISO-Zeitstempel mit Zonenangabe. Das ist die einzige Darstellung, die
ohne Rückfrage eindeutig bleibt, wenn eines Tages Frankreich oder Spanien
danebensteht.

Eine Ortszeit wird trotzdem gebraucht, aber nur für zwei Dinge: für die
Darstellung (die Mittagsspitze der Photovoltaik gehört auf 13 Uhr, nicht auf
11 Uhr UTC) und für die erzeugten Tagesgänge, die einem lokalen Rhythmus
folgen. Deshalb reist die Zeitzone als Angabe mit den Daten mit, statt dass
irgendwo im Code "Europe/Berlin" fest verdrahtet steht.
"""

from typing import Dict, List

REGIONS: Dict[str, Dict] = {
    "DE": {
        "code": "DE",
        "label": "Deutschland",
        # IANA-Name, nicht "CET": Nur so sind Sommerzeitwechsel korrekt.
        "timezone": "Europe/Berlin",
        # Regionsschlüssel, den SMARD in seinen URLs verwendet.
        "smard_region": "DE",
        "currency": "EUR",
    },
}

DEFAULT_REGION = "DE"


def config(code: str = DEFAULT_REGION) -> Dict:
    """Einstellungen einer Region; fällt auf die Standardregion zurück."""
    return REGIONS.get(code, REGIONS[DEFAULT_REGION])


def timezone_name(code: str = DEFAULT_REGION) -> str:
    return config(code)["timezone"]


def available() -> List[str]:
    return sorted(REGIONS)
