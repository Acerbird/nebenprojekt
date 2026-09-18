"""Tests des lokalen Messwertspeichers.

Läuft ausschließlich auf einer Wegwerf-Datenbank im Temp-Verzeichnis, nie auf
dem echten Bestand, und braucht kein Netz.
"""

import os
import tempfile
import unittest

from backend.data import store


class StoreTestCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.directory.name, "test.sqlite3")
        self.addCleanup(self.directory.cleanup)

    def rows(self, count, start=0, step=3600, value=lambda i: float(i)):
        return [(start + i * step, value(i)) for i in range(count)]


class WriteAndReadTest(StoreTestCase):
    def test_werte_kommen_unveraendert_zurueck(self):
        with store.open_db(self.path) as conn:
            store.write_observations(conn, "load", self.rows(5))
            self.assertEqual(store.read_series(conn, "load", 0, 10 * 3600),
                             [(i * 3600, float(i)) for i in range(5)])

    def test_zeitraum_grenzen_sind_start_inklusive_ende_exklusive(self):
        with store.open_db(self.path) as conn:
            store.write_observations(conn, "load", self.rows(5))
            got = store.read_series(conn, "load", 3600, 3 * 3600)
            self.assertEqual([ts for ts, _ in got], [3600, 2 * 3600])

    def test_luecken_bleiben_luecken(self):
        """Ein fehlender Messwert ist eine Information und wird nicht zu null."""
        with store.open_db(self.path) as conn:
            store.write_observations(conn, "load", [(0, None), (3600, 42.0)])
            self.assertEqual(store.read_series(conn, "load", 0, 7200),
                             [(0, None), (3600, 42.0)])

    def test_spaetere_korrektur_ersetzt_den_wert(self):
        """SMARD bessert vorläufige Werte nach — dann muss der neue gelten."""
        with store.open_db(self.path) as conn:
            store.write_observations(conn, "load", [(0, 10.0)])
            store.write_observations(conn, "load", [(0, 99.0)])
            self.assertEqual(store.read_series(conn, "load", 0, 3600), [(0, 99.0)])

    def test_zeitreihen_stoeren_einander_nicht(self):
        with store.open_db(self.path) as conn:
            store.write_observations(conn, "load", [(0, 1.0)])
            store.write_observations(conn, "solar", [(0, 2.0)])
            self.assertEqual(store.read_series(conn, "load", 0, 3600), [(0, 1.0)])
            self.assertEqual(store.read_series(conn, "solar", 0, 3600), [(0, 2.0)])

    def test_mehrere_zeitreihen_auf_einmal(self):
        with store.open_db(self.path) as conn:
            store.write_observations(conn, "load", [(0, 1.0)])
            store.write_observations(conn, "solar", [(0, 2.0)])
            got = store.read_many(conn, ("load", "solar"), 0, 3600)
            self.assertEqual(got, {"load": {0: 1.0}, "solar": {0: 2.0}})


class CoverageTest(StoreTestCase):
    def test_meldet_zeitraum_und_luecken(self):
        with store.open_db(self.path) as conn:
            store.write_observations(conn, "load", [(0, 1.0), (3600, None), (7200, 3.0)])
            info = store.coverage(conn)["load"]
            self.assertEqual(info["first_ts"], 0)
            self.assertEqual(info["last_ts"], 7200)
            self.assertEqual(info["points"], 3)
            self.assertEqual(info["gaps"], 1)

    def test_trennt_zeitstempel_von_echtem_messwert(self):
        """Die laufende Woche enthält leere Stunden in der Zukunft.

        Würden die als Datenende gelten, rechnete das Modell auf Werten, die es
        gar nicht gibt.
        """
        with store.open_db(self.path) as conn:
            store.write_observations(conn, "load",
                                     [(0, 1.0), (3600, 2.0), (7200, None), (10800, None)])
            info = store.coverage(conn)["load"]
            self.assertEqual(info["last_ts"], 10800)
            self.assertEqual(info["last_value_ts"], 3600)
            self.assertEqual(info["first_value_ts"], 0)

    def test_leere_datenbank_meldet_nichts(self):
        with store.open_db(self.path) as conn:
            self.assertEqual(store.coverage(conn), {})


class FetchLogTest(StoreTestCase):
    def test_merkt_sich_geholte_wochen(self):
        with store.open_db(self.path) as conn:
            store.note_fetch(conn, "load", 1000, 5000, 168)
            store.note_fetch(conn, "load", 2000, 5000, 168)
            self.assertEqual(store.fetched_weeks(conn, "load"), {1000: 168, 2000: 168})

    def test_erneuter_abruf_ueberschreibt_den_eintrag(self):
        with store.open_db(self.path) as conn:
            store.note_fetch(conn, "load", 1000, 5000, 100)
            store.note_fetch(conn, "load", 1000, 9999, 168)
            self.assertEqual(store.fetched_weeks(conn, "load"), {1000: 168})

    def test_zeitreihen_haben_eigene_protokolle(self):
        with store.open_db(self.path) as conn:
            store.note_fetch(conn, "load", 1000, 5000, 168)
            self.assertEqual(store.fetched_weeks(conn, "solar"), {})


class DatabaseFileTest(StoreTestCase):
    def test_datenbank_entsteht_erst_beim_oeffnen(self):
        self.assertFalse(store.exists(self.path))
        with store.open_db(self.path):
            pass
        self.assertTrue(store.exists(self.path))

    def test_fehlendes_verzeichnis_wird_angelegt(self):
        nested = os.path.join(self.directory.name, "tief", "verschachtelt", "db.sqlite3")
        with store.open_db(nested) as conn:
            store.write_observations(conn, "load", [(0, 1.0)])
        self.assertTrue(os.path.exists(nested))


if __name__ == "__main__":
    unittest.main()
