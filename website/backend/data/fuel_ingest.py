"""Baut die Monatstabelle der Brennstoffpreise aus öffentlichen Quellen.

Läuft wie der SMARD-Abruf außerhalb des Webservers:

    python -m backend.data.fuel_ingest                 Tabelle neu bauen
    python -m backend.data.fuel_ingest --show          zeigen, was vorliegt
    python -m backend.data.fuel_ingest --dry-run       holen, aber nicht schreiben

Drei Quellen, alle ohne Anmeldung und ohne Lizenzvorbehalt:

    EEX        Zuschlagspreise der CO₂-Auktionen, eine XLSX-Datei je Jahr
    Weltbank   "Pink Sheet", Monatspreise für Gas (TTF) und Kohle in US-Dollar
    EZB        Referenzkurs Euro/US-Dollar, um die Weltbankreihen umzurechnen

Nur die Standardbibliothek: XLSX ist ein ZIP-Archiv mit XML darin, das lässt
sich mit `zipfile` und `xml.etree` lesen. Eine Fremdbibliothek nur zum Öffnen
von zwei Tabellen wäre das Abhängigkeitsrisiko nicht wert.
"""

import argparse
import io
import json
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

USER_AGENT = "energiewende-lernprojekt/1.0 (privates Lernprojekt)"
TIMEOUT = 60.0

OUT_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                        "static_data", "fuel_prices.json")

EEX_URL = ("https://public.eex-group.com/eex/eua-auction-report/"
           "emission-spot-primary-market-auction-report-%d-data.xlsx")

# Die Weltbank hängt Ausgabedatum und Prüfsumme in den Pfad, der Link wechselt
# also mit jeder Ausgabe. Deshalb wird er zuerst auf der Übersichtsseite
# gesucht und nur ersatzweise auf den zuletzt bekannten Stand zurückgefallen.
WORLDBANK_PAGE = "https://www.worldbank.org/en/research/commodity-markets"
WORLDBANK_FALLBACK = ("https://thedocs.worldbank.org/en/doc/"
                      "18675f1d1639c7a34d463f59263ba0a2-0050012025/related/"
                      "CMO-Historical-Data-Monthly.xlsx")

ECB_URL = ("https://data-api.ecb.europa.eu/service/data/EXR/M.USD.EUR.SP00.A"
           "?format=csvdata&startPeriod=%s")

# 1 mmbtu sind 0,293071 MWh — damit wird aus Dollar je mmbtu ein Preis je MWh.
MWH_PER_MMBTU = 0.293071

# Steinkohle mit 6000 kcal/kg hat rund 6,978 MWh Heizwert je Tonne. Der Wert
# gehört zur Handelsqualität, auf die sich die Weltbankreihe bezieht.
MWH_PER_TONNE_COAL = 6.978

XL = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
PKG = "{http://schemas.openxmlformats.org/package/2006/relationships}"
DOC = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


# ------------------------------------------------------------------ Abruf

def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        return response.read()


# ------------------------------------------------------------------ XLSX

def _shared_strings(archive: zipfile.ZipFile) -> List[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
    return ["".join(node.text or "" for node in item.iter(XL + "t"))
            for item in root.iter(XL + "si")]


def _sheet_path(archive: zipfile.ZipFile, wanted: Optional[str]) -> str:
    """Pfad des gewünschten Blattes im Archiv; ohne Namen das erste."""
    rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    targets = {node.get("Id"): node.get("Target")
               for node in rels.iter(PKG + "Relationship")}
    book = ET.fromstring(archive.read("xl/workbook.xml"))
    for node in book.iter(XL + "sheet"):
        if wanted is None or node.get("name") == wanted:
            target = targets[node.get(DOC + "id")].lstrip("/")
            return target if target.startswith("xl/") else "xl/" + target
    raise KeyError("Blatt %r nicht gefunden" % wanted)


def read_sheet(data: bytes, sheet: Optional[str] = None) -> List[Dict[str, Optional[str]]]:
    """Ein Tabellenblatt als Liste von Zeilen, je Zeile {Spaltenbuchstabe: Wert}."""
    archive = zipfile.ZipFile(io.BytesIO(data))
    shared = _shared_strings(archive)
    root = ET.fromstring(archive.read(_sheet_path(archive, sheet)))
    rows = []
    for row in root.iter(XL + "row"):
        cells: Dict[str, Optional[str]] = {}
        for cell in row.iter(XL + "c"):
            node = cell.find(XL + "v")
            value = node.text if node is not None else None
            if cell.get("t") == "s" and value is not None:
                value = shared[int(value)]
            cells[re.sub(r"\d+", "", cell.get("r") or "")] = value
        rows.append(cells)
    return rows


def excel_date(serial: float) -> date:
    """Excel zählt Tage ab dem 30.12.1899 — mit dem bekannten Schaltjahrfehler
    von 1900, der für alle Daten ab 1901 gerade wieder herausfällt."""
    return date(1899, 12, 30) + timedelta(days=int(serial))


# ------------------------------------------------------------------ CO₂

# Die Auktionen für Luftverkehrszertifikate (EUAA) laufen getrennt und in
# kleinerem Volumen. Für den Preis, den ein Kraftwerk zahlt, zählt die
# allgemeine Auktion.
AVIATION_CONTRACTS = ("EAA3", "EAA2", "EAA")


def co2_monthly(years: List[int], quiet: bool = False) -> Dict[str, float]:
    """Monatsmittel der CO₂-Auktionspreise in Euro je Tonne."""
    buckets: Dict[str, List[float]] = defaultdict(list)
    for year in years:
        try:
            raw = fetch(EEX_URL % year)
        except Exception as error:                      # noqa: BLE001
            if not quiet:
                print("  CO₂ %d: nicht abrufbar (%s)" % (year, error))
            continue
        rows = read_sheet(raw)
        header = next((r for r in rows if r.get("B") == "Date"), None)
        if header is None:
            if not quiet:
                print("  CO₂ %d: Kopfzeile nicht gefunden" % year)
            continue
        found = 0
        for row in rows[rows.index(header) + 1:]:
            serial, contract, status, price = (row.get("B"), row.get("E"),
                                               row.get("F"), row.get("G"))
            if not serial or not price or status != "successful":
                continue
            if contract in AVIATION_CONTRACTS:
                continue
            try:
                day = excel_date(float(serial))
                buckets["%04d-%02d" % (day.year, day.month)].append(float(price))
            except ValueError:
                continue
            found += 1
        if not quiet:
            print("  CO₂ %d: %d Auktionen" % (year, found))
    return {key: round(sum(v) / len(v), 2) for key, v in buckets.items()}


# --------------------------------------------------------- Gas und Kohle

def worldbank_url(quiet: bool = False) -> str:
    try:
        page = fetch(WORLDBANK_PAGE).decode("utf-8", "replace")
        links = re.findall(r'https?://[^"\']*CMO-Historical-Data-Monthly\.xlsx', page)
        if links:
            return links[0]
    except Exception as error:                          # noqa: BLE001
        if not quiet:
            print("  Weltbank: Übersichtsseite nicht lesbar (%s)" % error)
    if not quiet:
        print("  Weltbank: benutze den zuletzt bekannten Link")
    return WORLDBANK_FALLBACK


def worldbank_monthly(quiet: bool = False) -> Dict[str, Dict[str, float]]:
    """Gas in Dollar je mmbtu und Kohle in Dollar je Tonne, je Monat."""
    rows = read_sheet(fetch(worldbank_url(quiet)), "Monthly Prices")
    header = next(r for r in rows if any(
        (v or "").startswith("Natural gas, Europe") for v in r.values()))
    columns = {name: key for key, name in header.items() if name}
    gas_col = next(k for name, k in columns.items() if name.startswith("Natural gas, Europe"))
    coal_col = next(k for name, k in columns.items() if name.startswith("Coal, South African"))

    out: Dict[str, Dict[str, float]] = {}
    for row in rows:
        stamp = row.get("A") or ""
        match = re.fullmatch(r"(\d{4})M(\d{2})", stamp.strip())
        if not match:
            continue
        entry = {}
        for key, column in (("gas_usd_per_mmbtu", gas_col), ("coal_usd_per_t", coal_col)):
            value = row.get(column)
            if value and value not in ("…", ".."):
                try:
                    entry[key] = float(value)
                except ValueError:
                    pass
        if entry:
            out["%s-%s" % (match.group(1), match.group(2))] = entry
    if not quiet:
        print("  Weltbank: %d Monate" % len(out))
    return out


def ecb_monthly(since: str, quiet: bool = False) -> Dict[str, float]:
    """US-Dollar je Euro, Monatsmittel des EZB-Referenzkurses."""
    text = fetch(ECB_URL % since).decode("utf-8", "replace")
    lines = text.splitlines()
    head = lines[0].split(",")
    period, value = head.index("TIME_PERIOD"), head.index("OBS_VALUE")
    out: Dict[str, float] = {}
    for line in lines[1:]:
        parts = line.split(",")
        if len(parts) <= max(period, value):
            continue
        try:
            out[parts[period]] = float(parts[value])
        except ValueError:
            continue
    if not quiet:
        print("  EZB: %d Monate Wechselkurs" % len(out))
    return out


# ------------------------------------------------------------- Zusammenbau

def build(first_year: int, last_year: int, quiet: bool = False) -> Dict:
    if not quiet:
        print("Hole Brennstoff- und CO₂-Preise …")
    co2 = co2_monthly(list(range(first_year, last_year + 1)), quiet)
    commodities = worldbank_monthly(quiet)
    rates = ecb_monthly("%d-01" % first_year, quiet)

    months: Dict[str, Dict[str, float]] = {}
    for key in sorted(set(co2) | set(commodities)):
        if not ("%04d-01" % first_year) <= key <= ("%04d-12" % last_year):
            continue
        entry: Dict[str, float] = {}
        if key in co2:
            entry["co2_eur_per_t"] = co2[key]
        rate = rates.get(key)
        raw = commodities.get(key, {})
        if rate and "gas_usd_per_mmbtu" in raw:
            entry["gas_eur_per_mwh_th"] = round(
                raw["gas_usd_per_mmbtu"] / MWH_PER_MMBTU / rate, 2)
        if rate and "coal_usd_per_t" in raw:
            entry["coal_eur_per_mwh_th"] = round(
                raw["coal_usd_per_t"] / MWH_PER_TONNE_COAL / rate, 2)
        if entry:
            months[key] = entry

    return {
        "months": months,
        "_quellen": {
            "co2": "EEX, Zuschlagspreise der Auktionen im europäischen "
                   "Emissionshandel (Primärmarkt), Monatsmittel. "
                   "Luftverkehrszertifikate sind ausgenommen.",
            "gas": "Weltbank, Commodity Price Data (Pink Sheet), Reihe "
                   "'Natural gas, Europe' (TTF) in USD je mmbtu, umgerechnet "
                   "mit 1 mmbtu = %.6f MWh und dem EZB-Referenzkurs."
                   % MWH_PER_MMBTU,
            "kohle": "Weltbank, ebenda, Reihe 'Coal, South African' in USD je "
                     "Tonne, umgerechnet mit %.3f MWh je Tonne (6000 kcal/kg) "
                     "und dem EZB-Referenzkurs. Näherung für den europäischen "
                     "Importpreis." % MWH_PER_TONNE_COAL,
            "wechselkurs": "EZB, Referenzkurs USD/EUR, Monatsmittel.",
            "braunkohle": "Nicht enthalten — Braunkohle wird nicht gehandelt. "
                          "Ihr Preis steht fest in power_plants.json.",
            "erstellt": datetime.now().strftime("%Y-%m-%d"),
        },
    }


def show(path: str) -> int:
    if not os.path.exists(path):
        print("Noch keine Tabelle. Aufbau mit: python -m backend.data.fuel_ingest")
        return 1
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    months = data.get("months", {})
    print("%s  (%d Monate)\n" % (path, len(months)))
    print("%-9s %10s %10s %10s" % ("Monat", "CO₂ €/t", "Gas €/MWh", "Kohle €/MWh"))
    for key in sorted(months):
        entry = months[key]
        print("%-9s %10s %10s %10s" % (
            key,
            entry.get("co2_eur_per_t", "—"),
            entry.get("gas_eur_per_mwh_th", "—"),
            entry.get("coal_eur_per_mwh_th", "—")))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m backend.data.fuel_ingest",
        description="Baut die Monatstabelle der Brennstoff- und CO₂-Preise.")
    # Vor 2020 liefert EEX die Auktionsberichte nur im alten .xls-Format,
    # das sich nicht mit der Standardbibliothek lesen lässt.
    parser.add_argument("--from-year", type=int, default=2020)
    parser.add_argument("--to-year", type=int, default=datetime.now().year)
    parser.add_argument("--out", default=OUT_PATH)
    parser.add_argument("--show", action="store_true", help="nur anzeigen, nichts holen")
    parser.add_argument("--dry-run", action="store_true", help="holen, aber nicht schreiben")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    if args.show:
        return show(args.out)

    data = build(args.from_year, args.to_year, args.quiet)
    if not data["months"]:
        print("Keine Daten erhalten — nichts geschrieben.", file=sys.stderr)
        return 1

    if args.dry_run:
        print("Trockenlauf: %d Monate, nichts geschrieben." % len(data["months"]))
        return 0

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    if not args.quiet:
        keys = sorted(data["months"])
        print("Geschrieben: %d Monate (%s bis %s) nach %s"
              % (len(keys), keys[0], keys[-1], args.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
