"""Der Prüfsatz: Modellpreis gegen tatsächlichen Börsenpreis.

Jede Kennzahl, die in der README oder in einem Kommentar dieses Projekts steht,
stammt aus diesem Programm. Damit ist sie nachrechenbar — und widerlegbar:

    backend/.venv/bin/python -m backend.benchmark
    backend/.venv/bin/python -m backend.benchmark --ablation
    backend/.venv/bin/python -m backend.benchmark --klassen

Der Prüfsatz umfasst 36 Wochen: den 6. jedes Monats von Januar 2023 bis
Dezember 2025, je 168 Stunden. Alle Monate dreier Jahre, keine Auswahl nach
Eignung — ein Maßstab, bei dem Zeiträume fehlen, lädt dazu ein, unbemerkt die
bequemen behalten zu haben.

Gebraucht werden lokale Messwerte (siehe backend.data.ingest). Fehlen sie, sagt
das Programm das und rechnet nicht mit erzeugten Profilen weiter — eine Zahl,
die gegen ein synthetisches Profil geprüft wurde, wäre wertlos.
"""

import argparse
import copy
import statistics
from typing import Dict, List, Optional, Sequence, Tuple

from . import analysis as anl
from .data import sources

JAHRE = (2023, 2024, 2025)
MONATE = range(1, 13)
TAG = 6
STUNDEN = 168

# Für die Aufteilung nach dem tatsächlich gezahlten Preis.
KLASSEN = ((None, 0.0), (0.0, 30.0), (30.0, 60.0), (60.0, 90.0),
           (90.0, 130.0), (130.0, None))


def wochen(jahre: Sequence[int] = JAHRE) -> List[str]:
    return ["%04d-%02d-%02d" % (jahr, monat, TAG) for jahr in jahre for monat in MONATE]


def kennzahlen(paare: Sequence[Tuple[float, float]]) -> Optional[Dict]:
    """Dieselben drei Kennzahlen wie auf der Analyseseite."""
    if len(paare) < 2:
        return None
    fehler = [modell - ist for modell, ist in paare]
    modell = [m for m, _ in paare]
    ist = [a for _, a in paare]
    mm, mi = statistics.mean(modell), statistics.mean(ist)
    zaehler = sum((m - mm) * (a - mi) for m, a in paare)
    nenner = (sum((m - mm) ** 2 for m in modell)
              * sum((a - mi) ** 2 for a in ist)) ** 0.5
    return {
        "hours": len(paare),
        "mae": sum(abs(f) for f in fehler) / len(fehler),
        "bias": sum(fehler) / len(fehler),
        "correlation": zaehler / nenner if nenner else float("nan"),
    }


def paare_der_woche(start: str, **kwargs) -> List[Tuple[float, float]]:
    """Ein Lauf über eine Woche, zurück kommen (Modell, Ist) je Stunde.

    Liegt der Zeitraum nicht vollständig vor, verschiebt oder kürzt das Modell
    ihn und sagt das unter `adjustments`. Für einen Prüfsatz wäre das
    verheerend: Zwei Wochen, die beide auf denselben Rand rutschen, gingen
    doppelt in die Kennzahl ein, und niemand sähe es. Eine verschobene Woche
    zählt deshalb gar nicht.
    """
    ergebnis = anl.simulate(source=sources.HISTORICAL, start=start,
                            hours=STUNDEN, **kwargs)
    if ergebnis["params"].get("adjustments"):
        return []
    pruefung = ergebnis.get("validation")
    if not pruefung:
        return []
    return [(m, a) for m, a
            in zip(ergebnis["price_eur_mwh"], pruefung["actual_price_eur_mwh"])
            if a is not None]


def lauf(jahre: Sequence[int] = JAHRE, **kwargs) -> Dict:
    """Der ganze Prüfsatz, zusätzlich je Jahr aufgeschlüsselt."""
    alle: List[Tuple[float, float]] = []
    je_jahr: Dict[int, List[Tuple[float, float]]] = {}
    for start in wochen(jahre):
        paare = paare_der_woche(start, **kwargs)
        alle.extend(paare)
        je_jahr.setdefault(int(start[:4]), []).extend(paare)
    return {"gesamt": kennzahlen(alle),
            "je_jahr": {jahr: kennzahlen(p) for jahr, p in sorted(je_jahr.items())},
            "paare": alle}


def zeile(label: str, werte: Optional[Dict], breite: int = 30) -> str:
    if not werte:
        return "%-*s  keine Messwerte" % (breite, label)
    return ("%-*s  MAE %6.2f   r %.3f   Bias %+7.2f   (%d h)"
            % (breite, label, werte["mae"], werte["correlation"],
               werte["bias"], werte["hours"]))


def bericht() -> None:
    ergebnis = lauf()
    print("Prüfsatz: %d Wochen à %d Stunden, der %d. jedes Monats %d bis %d"
          % (len(wochen()), STUNDEN, TAG, JAHRE[0], JAHRE[-1]))
    print()
    for jahr, werte in ergebnis["je_jahr"].items():
        print(zeile(str(jahr), werte))
    print(zeile("gesamt", ergebnis["gesamt"]))


def ablation() -> None:
    """Was jeder Baustein beiträgt — jeweils einer weggelassen.

    Nacheinander weglassen (Kettenablation) würde Wechselwirkungen verstecken:
    Der zweite Baustein wird dann an einem Modell gemessen, dem schon der erste
    fehlt. Deshalb wird jedes Mal vom vollständigen Modell aus gerechnet.
    """
    handel = copy.deepcopy(anl.EXCHANGE)
    aufschlag = anl.SCARCITY_MARKUP_MAX

    def zuruecksetzen():
        anl.EXCHANGE = copy.deepcopy(handel)
        anl.SCARCITY_MARKUP_MAX = aufschlag

    print("Prüfsatz: %d Wochen, jeweils ein Baustein weggelassen" % len(wochen()))
    print()
    print(zeile("Voreinstellung", lauf()["gesamt"]))

    anl.EXCHANGE = {}
    print(zeile("  ohne Außenhandel", lauf()["gesamt"]))
    zuruecksetzen()

    feste = {"co2_price": anl.DEFAULTS["co2_price"],
             "gas_price": anl.DEFAULTS["gas_price"]}
    print(zeile("  ohne Monatspreise", lauf(**feste)["gesamt"]))

    anl.SCARCITY_MARKUP_MAX = 0.0
    print(zeile("  ohne Knappheitsaufschlag", lauf()["gesamt"]))
    zuruecksetzen()

    regler = {"wind_gw": anl.DEFAULTS["wind_gw"], "solar_gw": anl.DEFAULTS["solar_gw"]}
    print(zeile("  ohne echten Ausbaustand", lauf(**regler)["gesamt"]))

    print(zeile("  zusätzlich mit Mindestlast", lauf(min_load=True)["gesamt"]))


def klassen() -> None:
    """Die Verzerrung je Preisklasse, ohne und mit Mindestlast.

    Die Gesamtkennzahl mittelt über sehr verschiedene Marktlagen. Erst diese
    Aufteilung zeigt, dass die Mindestlast die billigen Stunden verbessert und
    nur deshalb verliert, weil die teuren in der Überzahl sind.
    """
    def sammeln(min_load: bool):
        eimer: Dict[int, List[Tuple[float, float]]] = {}
        for start in wochen():
            for modell, ist in paare_der_woche(start, min_load=min_load):
                for index, (unten, oben) in enumerate(KLASSEN):
                    if (unten is None or ist >= unten) and (oben is None or ist < oben):
                        eimer.setdefault(index, []).append((modell, ist))
                        break
        return eimer

    ohne, mit = sammeln(False), sammeln(True)
    print("%-16s %8s %9s %12s %11s" % ("Ist-Preis", "Stunden", "Ø Ist",
                                       "Bias ohne ML", "Bias mit ML"))
    for index, (unten, oben) in enumerate(KLASSEN):
        a, b = ohne.get(index, []), mit.get(index, [])
        if not a:
            continue
        name = ("unter %g" % oben if unten is None
                else "über %g" % unten if oben is None
                else "%g bis %g" % (unten, oben))
        print("%-16s %8d %9.1f %12.1f %11.1f"
              % (name, len(a), statistics.mean(x[1] for x in a),
                 kennzahlen(a)["bias"], kennzahlen(b)["bias"] if b else float("nan")))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--ablation", action="store_true",
                        help="jeden Baustein einmal weglassen")
    parser.add_argument("--klassen", action="store_true",
                        help="Verzerrung je Preisklasse, ohne und mit Mindestlast")
    args = parser.parse_args()

    if sources.available_range() is None:
        print("Keine Messwerte vorhanden — erst 'python -m backend.data.ingest' laufen lassen.")
        return
    if args.ablation:
        ablation()
    elif args.klassen:
        klassen()
    else:
        bericht()


if __name__ == "__main__":
    main()
