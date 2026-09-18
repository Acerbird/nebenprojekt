"""Tests der installierten Leistung.

Der Kapazitätsfaktor hängt am Nenner: 30 GW Windeinspeisung bedeuten 2015
etwas anderes als 2026, weil zwischendurch fast doppelt so viel zugebaut wurde.
Diese Tests sichern, dass der Nenner zum Zeitpunkt passt.
"""

import unittest
from datetime import datetime, timezone

from backend.data import capacity


def ts(date: str) -> int:
    return int(datetime.strptime(date, "%Y-%m-%d")
               .replace(tzinfo=timezone.utc).timestamp())


class DataFileTest(unittest.TestCase):
    def test_alle_noetigen_technologien_sind_hinterlegt(self):
        self.assertEqual(set(capacity.technologies()),
                         {"wind_onshore", "wind_offshore", "solar"})

    def test_quelle_und_stand_sind_dokumentiert(self):
        note = capacity.source_note()
        self.assertIn("_quelle", note)
        self.assertIn("_abgerufen", note)
        self.assertTrue(note["_quelle"])

    def test_zeitraum_deckt_die_smard_historie_ab(self):
        """SMARD liefert ab Ende 2014 — so weit muss der Nenner zurückreichen."""
        for technology in capacity.technologies():
            first, last = capacity.covered_years(technology)
            self.assertLessEqual(first, 2015, technology)
            self.assertGreaterEqual(last, 2025, technology)


class InterpolationTest(unittest.TestCase):
    def test_zubau_waechst_ueber_die_jahre(self):
        for technology in capacity.technologies():
            werte = [capacity.installed_gw(technology, ts("%d-07-01" % year))
                     for year in range(2016, 2026)]
            self.assertEqual(werte, sorted(werte), technology)

    def test_jahreswert_gilt_zum_jahresende(self):
        # Der hinterlegte Wert für 2024 muss Ende 2024 erreicht sein.
        silvester = capacity.installed_gw("solar", ts("2024-12-31"))
        self.assertAlmostEqual(silvester, 100.8, delta=0.3)

    def test_zwischen_den_jahren_wird_interpoliert(self):
        ende_2023 = capacity.installed_gw("solar", ts("2023-12-31"))
        mitte_2024 = capacity.installed_gw("solar", ts("2024-07-01"))
        ende_2024 = capacity.installed_gw("solar", ts("2024-12-31"))
        self.assertLess(ende_2023, mitte_2024)
        self.assertLess(mitte_2024, ende_2024)

    def test_vor_dem_ersten_jahr_wird_der_randwert_gehalten(self):
        """Extrapolation wäre geraten — lieber den bekannten Randwert halten."""
        frueh = capacity.installed_gw("solar", ts("2010-01-01"))
        erstes_jahr = capacity.covered_years("solar")[0]
        self.assertEqual(frueh, capacity.installed_gw("solar", ts("%d-12-31" % erstes_jahr)))

    def test_nach_dem_letzten_jahr_wird_der_randwert_gehalten(self):
        spaet = capacity.installed_gw("solar", ts("2040-01-01"))
        letztes_jahr = capacity.covered_years("solar")[1]
        # Der Jahreswert gilt zum Jahresende, also erst am 1. Januar 00:00 des
        # Folgejahres vollständig — am 31.12. fehlt das letzte Tagesstück.
        self.assertAlmostEqual(
            spaet, capacity.installed_gw("solar", ts("%d-12-31" % letztes_jahr)), delta=0.1)
        self.assertEqual(spaet, capacity.installed_gw("solar", ts("%d-01-01" % (letztes_jahr + 1))))

    def test_ausserhalb_des_bestands_wird_gekennzeichnet(self):
        self.assertTrue(capacity.is_extrapolated(ts("2010-01-01")))
        self.assertTrue(capacity.is_extrapolated(ts("2040-01-01")))
        self.assertFalse(capacity.is_extrapolated(ts("2023-06-01")))

    def test_werte_sind_plausibel(self):
        """Grobe Plausibilität — schützt vor vertauschten Spalten."""
        moment = ts("2024-06-01")
        wind_on = capacity.installed_gw("wind_onshore", moment)
        wind_off = capacity.installed_gw("wind_offshore", moment)
        solar = capacity.installed_gw("solar", moment)
        self.assertTrue(55 < wind_on < 75, wind_on)
        self.assertTrue(5 < wind_off < 15, wind_off)
        self.assertTrue(80 < solar < 130, solar)
        self.assertGreater(wind_on, wind_off)


if __name__ == "__main__":
    unittest.main()
