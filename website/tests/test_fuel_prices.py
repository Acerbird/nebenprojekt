"""Brennstoff- und CO₂-Preise: Tabelle, Auflösung je Stunde, Importer.

Die Tabelle entsteht außerhalb der Website aus drei öffentlichen Quellen. Hier
wird nur geprüft, was ohne Netz prüfbar ist: das Lesen, das Umrechnen und die
Frage, wann ein eingestellter Reglerwert den gemessenen schlägt.
"""

import io
import json
import os
import tempfile
import unittest
import zipfile
from datetime import datetime, timezone

from backend import analysis as anl
from backend.data import fuel_ingest, fuel_prices


class TabelleTest(unittest.TestCase):

    def test_monatsschluessel_ist_utc(self):
        """Der 1. Januar 00:30 UTC ist in Berlin schon der 1. Januar 01:30 —
        gerechnet wird trotzdem in UTC, wie überall im Modell."""
        ts = int(datetime(2024, 1, 1, 0, 30, tzinfo=timezone.utc).timestamp())
        self.assertEqual(fuel_prices.month_key(ts), "2024-01")

    def test_silvester_gehoert_noch_zum_alten_jahr(self):
        ts = int(datetime(2023, 12, 31, 23, 0, tzinfo=timezone.utc).timestamp())
        self.assertEqual(fuel_prices.month_key(ts), "2023-12")

    def test_unbekannter_monat_gibt_none(self):
        self.assertIsNone(fuel_prices.for_month("1970-01"))

    def test_spanne_nennt_anfang_und_ende(self):
        spanne = fuel_prices.span()
        if spanne is None:
            self.skipTest("Keine Tabelle vorhanden")
        self.assertLessEqual(spanne["first"], spanne["last"])
        self.assertGreater(spanne["count"], 0)

    def test_werte_sind_plausibel(self):
        """Grobe Plausibilität — kein Ersatz für die Quelle, aber ein Netz gegen
        Einheitenfehler. Ein Gaspreis von 3.200 statt 32 fiele hier auf."""
        for key, entry in fuel_prices.table().get("months", {}).items():
            if "co2_eur_per_t" in entry:
                self.assertTrue(0 < entry["co2_eur_per_t"] < 300, key)
            if "gas_eur_per_mwh_th" in entry:
                self.assertTrue(0 < entry["gas_eur_per_mwh_th"] < 400, key)
            if "coal_eur_per_mwh_th" in entry:
                self.assertTrue(0 < entry["coal_eur_per_mwh_th"] < 200, key)


class FortschreibungTest(unittest.TestCase):
    """Die Weltbank veröffentlicht mit Verzug, die CO₂-Auktionen nicht. Für den
    jüngsten Monat fehlt deshalb oft der Gaspreis — und das ist genau der
    Zeitraum, den die Seite ohne Zutun zeigt."""

    def setUp(self):
        self.original = fuel_prices._CACHE
        fuel_prices._CACHE = {"months": {
            "2026-06": {"co2_eur_per_t": 77.8, "gas_eur_per_mwh_th": 44.9,
                        "coal_eur_per_mwh_th": 12.0},
            "2026-09": {"co2_eur_per_t": 84.1},
        }, "_quellen": {}}

    def tearDown(self):
        fuel_prices._CACHE = self.original

    def test_fehlender_wert_wird_aus_dem_vormonat_geholt(self):
        eintrag = fuel_prices.for_month("2026-09")
        self.assertEqual(eintrag["gas_eur_per_mwh_th"], 44.9)
        self.assertEqual(eintrag["co2_eur_per_t"], 84.1, "Vorhandenes bleibt unangetastet")

    def test_die_fortschreibung_wird_ausgewiesen(self):
        """Eine unsichtbare Ersetzung wäre schlimmer als eine fehlende Zahl."""
        carried = fuel_prices.for_month("2026-09")["carried_forward"]
        self.assertEqual(carried["gas_eur_per_mwh_th"], "2026-06")
        self.assertNotIn("co2_eur_per_t", carried)

    def test_ohne_luecke_wird_nichts_ausgewiesen(self):
        self.assertNotIn("carried_forward", fuel_prices.for_month("2026-06"))

    def test_weiter_als_drei_monate_wird_nicht_fortgeschrieben(self):
        """Ein Rohstoffpreis von vor einem halben Jahr sagt nichts über heute.
        Der letzte Eintrag der Tabelle ist 2026-09; vier Monate später ist
        Schluss, drei Monate später geht es gerade noch."""
        self.assertIsNotNone(fuel_prices.for_month("2026-12"))
        self.assertIsNone(fuel_prices.for_month("2027-01"))

    def test_gas_faellt_frueher_aus_als_co2(self):
        """Die beiden Quellen hinken unterschiedlich weit hinterher. 2026-09
        hat nur CO₂; drei Monate später ist auch der Gaspreis aus 2026-06 zu alt."""
        spaet = fuel_prices.for_month("2026-09")
        self.assertIn("gas_eur_per_mwh_th", spaet)
        noch_spaeter = fuel_prices.for_month("2026-11")
        self.assertIn("co2_eur_per_t", noch_spaeter)
        self.assertNotIn("gas_eur_per_mwh_th", noch_spaeter)

    def test_monatsrechnung_ueber_den_jahreswechsel(self):
        self.assertEqual(fuel_prices._earlier("2026-02", 3), "2025-11")
        self.assertEqual(fuel_prices._earlier("2026-01", 1), "2025-12")
        self.assertEqual(fuel_prices._earlier("2026-12", 1), "2026-11")


class AufloesungTest(unittest.TestCase):
    """Wann gilt der gemessene Monatswert, wann der Regler?"""

    def params(self, **kwargs):
        return anl.normalise_params(source="historical", start="2024-03-06",
                                    hours=48, **kwargs)

    def test_ohne_reglereingabe_gelten_die_monatswerte(self):
        if not fuel_prices.available():
            self.skipTest("Keine Tabelle vorhanden")
        params = self.params()
        costs = anl.fuel_costs_for(params["start_ts"], params)
        monat = fuel_prices.for_month("2024-03")
        self.assertEqual(costs["origin"], "historical")
        self.assertEqual(costs["co2_price"], monat["co2_eur_per_t"])
        self.assertEqual(costs["gas_price"], monat["gas_eur_per_mwh_th"])

    def test_eingestellter_wert_schlaegt_den_gemessenen(self):
        """Sonst wäre die Frage 'Was macht ein CO₂-Preis von 150 Euro?' nicht
        mehr zu stellen — und genau dafür ist der Regler da."""
        params = self.params(co2_price=150.0)
        costs = anl.fuel_costs_for(params["start_ts"], params)
        self.assertEqual(costs["co2_price"], 150.0)

    def test_ein_regler_verdraengt_den_anderen_nicht(self):
        if not fuel_prices.available():
            self.skipTest("Keine Tabelle vorhanden")
        params = self.params(co2_price=150.0)
        costs = anl.fuel_costs_for(params["start_ts"], params)
        self.assertEqual(costs["gas_price"], fuel_prices.for_month("2024-03")["gas_eur_per_mwh_th"])

    def test_herkunft_wird_je_groesse_festgehalten(self):
        """Ohne diese Unterscheidung stünde der Vorgabewert von 32 €/MWh in der
        Herkunftszeile als gemessener Gaspreis des Zeitraums."""
        if not fuel_prices.available():
            self.skipTest("Keine Tabelle vorhanden")
        params = self.params(co2_price=150.0)
        costs = anl.fuel_costs_for(params["start_ts"], params)
        self.assertEqual(costs["used"]["co2_price"], "fixed")
        self.assertEqual(costs["used"]["gas_price"], "historical")

    def test_erzeugte_profile_bekommen_keine_monatswerte(self):
        params = anl.normalise_params(source="synthetic", hours=48)
        costs = anl.fuel_costs_for(None, params)
        self.assertEqual(costs["origin"], "fixed")
        self.assertEqual(costs["co2_price"], anl.DEFAULTS["co2_price"])

    def test_kohlepreis_wirkt_auf_steinkohle_nicht_auf_braunkohle(self):
        """Braunkohle wird im Tagebau gefördert und nicht gehandelt."""
        stein = next(p for p in anl.FLEET["plants"] if p["id"] == "steinkohle")
        braun = next(p for p in anl.FLEET["plants"] if p["id"] == "braunkohle")
        self.assertNotEqual(anl.block_costs(stein, 80.0, coal_price=40.0),
                            anl.block_costs(stein, 80.0, coal_price=10.0))
        self.assertEqual(anl.block_costs(braun, 80.0, coal_price=40.0),
                         anl.block_costs(braun, 80.0, coal_price=10.0))


class ExcelLesenTest(unittest.TestCase):
    """XLSX ohne Fremdbibliothek: ein ZIP-Archiv mit XML darin."""

    def build(self, rows):
        """Eine minimale, echte XLSX-Datei bauen."""
        zellen = []
        for r, row in enumerate(rows, start=1):
            inner = "".join(
                '<c r="%s%d"><v>%s</v></c>' % (chr(ord("A") + c), r, value)
                for c, value in enumerate(row) if value is not None)
            zellen.append('<row r="%d">%s</row>' % (r, inner))
        sheet = ('<?xml version="1.0"?><worksheet '
                 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                 '<sheetData>%s</sheetData></worksheet>' % "".join(zellen))
        book = ('<?xml version="1.0"?><workbook '
                'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                '<sheets><sheet name="Blatt" sheetId="1" r:id="rId1"/></sheets></workbook>')
        rels = ('<?xml version="1.0"?><Relationships '
                'xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("xl/workbook.xml", book)
            archive.writestr("xl/_rels/workbook.xml.rels", rels)
            archive.writestr("xl/worksheets/sheet1.xml", sheet)
        return buffer.getvalue()

    def test_blatt_wird_zeilenweise_gelesen(self):
        data = self.build([["1", "2"], ["3", None, "5"]])
        rows = fuel_ingest.read_sheet(data)
        self.assertEqual(rows[0]["A"], "1")
        self.assertEqual(rows[0]["B"], "2")
        self.assertEqual(rows[1]["C"], "5")
        self.assertNotIn("B", rows[1], "Leere Zellen dürfen nicht erfunden werden")

    def test_unbekanntes_blatt_faellt_auf(self):
        with self.assertRaises(KeyError):
            fuel_ingest.read_sheet(self.build([["1"]]), "Gibtsnicht")


class ExcelDatumTest(unittest.TestCase):

    def test_bekannte_seriennummern(self):
        """45278 ist die letzte CO₂-Auktion des Jahres 2023, 45292 der
        Jahreswechsel — beide aus dem echten EEX-Bericht abgelesen."""
        from datetime import date
        self.assertEqual(fuel_ingest.excel_date(45278), date(2023, 12, 18))
        self.assertEqual(fuel_ingest.excel_date(45292), date(2024, 1, 1))

    def test_umrechnung_bleibt_tagweise_stimmig(self):
        from datetime import date, timedelta
        for serial in (45000, 45278, 45292, 45658, 46000):
            self.assertEqual(fuel_ingest.excel_date(serial + 1),
                             fuel_ingest.excel_date(serial) + timedelta(days=1))
        self.assertEqual(fuel_ingest.excel_date(
            (date(2020, 1, 1) - date(1899, 12, 30)).days), date(2020, 1, 1))

    def test_nur_fuer_daten_ab_1901_gedacht(self):
        """Excel hält 1900 fälschlich für ein Schaltjahr; Seriennummer 60 meint
        dort einen 29. Februar, den es nie gab. Ab März 1900 gleichen sich
        Zählfehler und Startpunkt aus, und die Auktionsdaten beginnen 2012 —
        deshalb bleibt es bei der einfachen Umrechnung."""
        from datetime import date
        self.assertEqual(fuel_ingest.excel_date(61), date(1900, 3, 1))


class UmrechnungTest(unittest.TestCase):

    def test_dollar_je_mmbtu_wird_euro_je_mwh(self):
        """10 $/mmbtu bei 1,10 $/€ sind rund 31 €/MWh."""
        wert = 10.0 / fuel_ingest.MWH_PER_MMBTU / 1.10
        self.assertAlmostEqual(wert, 31.0, delta=0.5)

    def test_dollar_je_tonne_kohle_wird_euro_je_mwh(self):
        """100 $/t bei 1,10 $/€ sind rund 13 €/MWh thermisch."""
        wert = 100.0 / fuel_ingest.MWH_PER_TONNE_COAL / 1.10
        self.assertAlmostEqual(wert, 13.0, delta=0.5)


class SchreibenTest(unittest.TestCase):

    def test_leere_tabelle_wird_nicht_geschrieben(self):
        """Lieber die alte Tabelle behalten als sie durch eine leere ersetzen —
        ein Abruf, der nichts findet, darf den Bestand nicht löschen."""
        with tempfile.TemporaryDirectory() as ordner:
            ziel = os.path.join(ordner, "fuel_prices.json")
            original = fuel_ingest.build
            fuel_ingest.build = lambda *a, **k: {"months": {}, "_quellen": {}}
            try:
                code = fuel_ingest.main(["--out", ziel, "--quiet"])
            finally:
                fuel_ingest.build = original
            self.assertEqual(code, 1)
            self.assertFalse(os.path.exists(ziel))

    def test_geschriebene_tabelle_ist_wieder_lesbar(self):
        with tempfile.TemporaryDirectory() as ordner:
            ziel = os.path.join(ordner, "fuel_prices.json")
            original = fuel_ingest.build
            fuel_ingest.build = lambda *a, **k: {
                "months": {"2024-03": {"co2_eur_per_t": 57.46}}, "_quellen": {}}
            try:
                self.assertEqual(fuel_ingest.main(["--out", ziel, "--quiet"]), 0)
            finally:
                fuel_ingest.build = original
            with open(ziel, encoding="utf-8") as handle:
                self.assertEqual(json.load(handle)["months"]["2024-03"]["co2_eur_per_t"], 57.46)


if __name__ == "__main__":
    unittest.main()
