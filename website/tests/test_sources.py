"""Tests der Zeitreihenquellen.

Hier sitzt der Kern von Phase 2: Aus Einspeisung und installierter Leistung
wird ein Kapazitätsfaktor, mit dem sich dasselbe Wetter auf einen beliebigen
Ausbaustand umrechnen lässt. Getestet wird gegen eine künstliche Datenbank —
kein Netz, kein echter Bestand.
"""

import os
import tempfile
import unittest
from datetime import datetime, timezone

from backend.data import capacity, sources, store

# Ein Montag, gut innerhalb der belegten Kapazitätsjahre.
START = int(datetime(2024, 6, 3, 0, 0, tzinfo=timezone.utc).timestamp())


class SeriesScalingTest(unittest.TestCase):
    """Die Serie trägt Kapazitätsfaktoren; Leistung entsteht erst beim Skalieren."""

    def setUp(self):
        self.series = sources.Series(
            timestamps=["t0", "t1", "t2"], load_gw=[50.0, 60.0, 40.0],
            wind_cf=[0.0, 0.5, 1.0], solar_cf=[0.0, 0.25, 0.5],
            hydro_factor=1.0, source="test", label="Test", meta={})

    def test_stunden_zaehlen_die_lastreihe(self):
        self.assertEqual(self.series.hours, 3)

    def test_wind_skaliert_mit_installierter_leistung(self):
        self.assertEqual(self.series.wind_gw(100.0), [0.0, 50.0, 100.0])
        self.assertEqual(self.series.wind_gw(0.0), [0.0, 0.0, 0.0])

    def test_doppelter_ausbau_verdoppelt_die_einspeisung(self):
        einfach = self.series.wind_gw(70.0)
        doppelt = self.series.wind_gw(140.0)
        for a, b in zip(einfach, doppelt):
            self.assertAlmostEqual(b, 2 * a, places=3)

    def test_last_laesst_sich_auf_eine_hoechstlast_strecken(self):
        gestreckt = sources.scale_load(self.series, 120.0)
        self.assertAlmostEqual(max(gestreckt.load_gw), 120.0, places=3)
        # Die Form bleibt erhalten, nur das Niveau ändert sich.
        self.assertAlmostEqual(gestreckt.load_gw[0] / gestreckt.load_gw[1],
                               self.series.load_gw[0] / self.series.load_gw[1], places=4)
        self.assertIn("load_scaled_by", gestreckt.meta)

    def test_ohne_vorgabe_bleibt_die_last_unangetastet(self):
        self.assertEqual(sources.scale_load(self.series, None), self.series)


class GapFillingTest(unittest.TestCase):
    """Kurze Lücken überbrücken ist zulässig, lange Ausfälle erfinden nicht."""

    def test_einzelne_luecke_wird_interpoliert(self):
        self.assertEqual(sources._fill_gaps([10.0, None, 30.0]), [10.0, 20.0, 30.0])

    def test_mehrere_kurze_luecken_werden_ueberbrueckt(self):
        got = sources._fill_gaps([0.0, None, None, 30.0])
        self.assertAlmostEqual(got[1], 10.0, places=6)
        self.assertAlmostEqual(got[2], 20.0, places=6)

    def test_zu_lange_luecke_wird_gemeldet(self):
        values = [10.0] + [None] * 10 + [20.0]
        with self.assertRaises(sources.InsufficientData):
            sources._fill_gaps(values, max_gap=3)

    def test_kurze_randluecke_haelt_den_nachbarwert(self):
        self.assertEqual(sources._fill_gaps([None, 10.0, 20.0]), [10.0, 10.0, 20.0])

    def test_lange_randluecke_wird_gemeldet(self):
        with self.assertRaises(sources.InsufficientData):
            sources._fill_gaps([None] * 10 + [5.0], max_gap=3)

    def test_ohne_jeden_messwert_wird_gemeldet(self):
        with self.assertRaises(sources.InsufficientData):
            sources._fill_gaps([None, None, None])

    def test_vollstaendige_reihe_bleibt_unveraendert(self):
        self.assertEqual(sources._fill_gaps([1.0, 2.0, 3.0]), [1.0, 2.0, 3.0])


class SyntheticSourceTest(unittest.TestCase):
    def test_liefert_die_vereinbarte_struktur(self):
        series = sources.synthetic_series(48, 75.0, "winter")
        self.assertEqual(series.source, sources.SYNTHETIC)
        self.assertEqual(series.hours, 48)
        for values in (series.load_gw, series.wind_cf, series.solar_cf, series.timestamps):
            self.assertEqual(len(values), 48)

    def test_kapazitaetsfaktoren_bleiben_zwischen_null_und_eins(self):
        series = sources.synthetic_series(168, 75.0, "sommer")
        for name, values in (("wind", series.wind_cf), ("solar", series.solar_cf)):
            self.assertGreaterEqual(min(values), 0.0, name)
            self.assertLessEqual(max(values), 1.0, name)

    def test_hoechstlast_wird_eingehalten(self):
        series = sources.synthetic_series(168, 75.0, "winter")
        self.assertLessEqual(max(series.load_gw), 75.0 + 1e-6)


class HistoricalSourceTestCase(unittest.TestCase):
    """Baut eine kleine Datenbank mit bekannten Werten und prüft die Umrechnung."""

    HOURS = 48

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = os.path.join(self.directory.name, "fixture.sqlite3")
        self.stamps = [START + h * 3600 for h in range(self.HOURS)]
        self.write_fixture()

    def write_fixture(self, load=None, wind_on=None, wind_off=None, solar=None):
        """Werte in MW, so wie SMARD sie liefert."""
        load = load or [50000.0] * self.HOURS
        wind_on = wind_on or [20000.0] * self.HOURS
        wind_off = wind_off or [4000.0] * self.HOURS
        solar = solar or [10000.0] * self.HOURS
        with store.open_db(self.path) as conn:
            store.write_observations(conn, "load", list(zip(self.stamps, load)))
            store.write_observations(conn, "wind_onshore", list(zip(self.stamps, wind_on)))
            store.write_observations(conn, "wind_offshore", list(zip(self.stamps, wind_off)))
            store.write_observations(conn, "solar", list(zip(self.stamps, solar)))

    def series(self, hours=None):
        return sources.historical_series(START, hours or self.HOURS, db_path=self.path)


class HistoricalSourceTest(HistoricalSourceTestCase):
    def test_last_wird_von_megawatt_in_gigawatt_umgerechnet(self):
        series = self.series()
        self.assertEqual(series.source, sources.HISTORICAL)
        self.assertTrue(all(abs(v - 50.0) < 1e-6 for v in series.load_gw))

    def test_kapazitaetsfaktor_ist_einspeisung_durch_installierte_leistung(self):
        series = self.series()
        cap_on = capacity.installed_gw("wind_onshore", START)
        cap_off = capacity.installed_gw("wind_offshore", START)
        erwartet = 24.0 / (cap_on + cap_off)   # 20 GW onshore + 4 GW offshore
        self.assertAlmostEqual(series.wind_cf[0], erwartet, places=4)

    def test_wind_onshore_und_offshore_werden_zusammengefasst(self):
        """Das Modell kennt nur einen Wind-Regler, die Daten zwei Quellen."""
        series = self.series()
        gesamt = series.wind_gw(capacity.installed_gw("wind_onshore", START)
                                + capacity.installed_gw("wind_offshore", START))
        self.assertAlmostEqual(gesamt[0], 24.0, delta=0.05)

    def test_kapazitaetsfaktoren_bleiben_zwischen_null_und_eins(self):
        self.write_fixture(wind_on=[999999.0] * self.HOURS)
        series = self.series()
        self.assertLessEqual(max(series.wind_cf), 1.0)
        self.assertGreaterEqual(min(series.wind_cf), 0.0)

    def test_hypothetischer_zubau_nutzt_dasselbe_wetter(self):
        """Der Kern der Sache: echtes Wetter, frei wählbarer Ausbaustand."""
        series = self.series()
        echt = capacity.installed_gw("wind_onshore", START) + capacity.installed_gw("wind_offshore", START)
        verdoppelt = series.wind_gw(2 * echt)
        real = series.wind_gw(echt)
        for a, b in zip(real, verdoppelt):
            self.assertAlmostEqual(b, 2 * a, places=2)

    def test_zeitstempel_stehen_in_utc(self):
        """UTC ist die einzige Darstellung, die auch bei mehreren Ländern eindeutig bleibt."""
        series = self.series()
        self.assertTrue(series.timestamps[0].endswith("+00:00"), series.timestamps[0])
        self.assertEqual(len(series.timestamps), self.HOURS)
        erster = datetime.fromisoformat(series.timestamps[0])
        self.assertEqual(int(erster.timestamp()), START)

    def test_anzeigezeitzone_reist_mit(self):
        """Ohne sie wüsste die Oberfläche nicht, in welcher Zone sie anzeigen soll."""
        series = self.series()
        self.assertTrue(series.display_timezone)
        self.assertIn("/", series.display_timezone)   # IANA-Name, nicht "CET"
        self.assertEqual(series.meta["timezone"], series.display_timezone)

    def test_zeitstempel_folgen_im_stundentakt(self):
        stamps = [datetime.fromisoformat(s) for s in self.series().timestamps]
        for earlier, later in zip(stamps, stamps[1:]):
            self.assertEqual((later - earlier).total_seconds(), 3600)

    def test_herkunft_und_ausbaustand_stehen_im_ergebnis(self):
        meta = self.series().meta
        self.assertIn("SMARD", meta["data_source"])
        self.assertIn("wind", meta["installed_gw"])
        self.assertIn("solar", meta["installed_gw"])

    def test_einzelne_luecke_wird_ueberbrueckt_und_vermerkt(self):
        load = [50000.0] * self.HOURS
        load[10] = None
        self.write_fixture(load=load)
        series = self.series()
        self.assertAlmostEqual(series.load_gw[10], 50.0, places=3)
        self.assertEqual(series.meta["gaps_filled"]["load"], 1)

    def test_langer_ausfall_wird_gemeldet_statt_geraten(self):
        load = [50000.0] * self.HOURS
        for index in range(5, 20):
            load[index] = None
        self.write_fixture(load=load)
        with self.assertRaises(sources.InsufficientData):
            self.series()

    def test_ohne_datenbank_gibt_es_eine_verstaendliche_meldung(self):
        with self.assertRaises(sources.InsufficientData) as caught:
            sources.historical_series(START, 24, db_path=os.path.join(
                self.directory.name, "gibtesnicht.sqlite3"))
        self.assertIn("ingest", str(caught.exception))

    def test_zeitraum_ohne_messwerte_wird_gemeldet(self):
        weit_weg = int(datetime(2001, 1, 1, tzinfo=timezone.utc).timestamp())
        with self.assertRaises(sources.InsufficientData):
            sources.historical_series(weit_weg, 24, db_path=self.path)


class AvailableRangeTest(HistoricalSourceTestCase):
    def test_meldet_den_vorliegenden_zeitraum(self):
        info = sources.available_range(self.path)
        self.assertEqual(info["first_ts"], self.stamps[0])
        self.assertEqual(info["last_ts"], self.stamps[-1])

    def test_leere_stunden_am_ende_zaehlen_nicht_als_bestand(self):
        """Die laufende Woche enthält Stunden, die noch nicht stattgefunden haben.

        SMARD liefert die angefangene Woche komplett aus, die künftigen Stunden
        aber leer — und zwar in allen Zeitreihen gleichzeitig. Gälten die als
        Datenende, rechnete das Modell auf fortgeschriebenen Randwerten und
        gäbe sie als Messwerte aus.
        """
        zukunft = [(self.stamps[-1] + h * 3600, None) for h in range(1, 25)]
        with store.open_db(self.path) as conn:
            for name in ("load", "wind_onshore", "wind_offshore", "solar"):
                store.write_observations(conn, name, zukunft)
        self.assertEqual(sources.available_range(self.path)["last_ts"], self.stamps[-1])

    def test_leere_stunden_am_anfang_zaehlen_nicht_als_bestand(self):
        vergangenheit = [(self.stamps[0] - h * 3600, None) for h in range(1, 25)]
        with store.open_db(self.path) as conn:
            for name in ("load", "wind_onshore", "wind_offshore", "solar"):
                store.write_observations(conn, name, vergangenheit)
        self.assertEqual(sources.available_range(self.path)["first_ts"], self.stamps[0])

    def test_ohne_datenbank_gibt_es_keinen_zeitraum(self):
        self.assertIsNone(sources.available_range(
            os.path.join(self.directory.name, "gibtesnicht.sqlite3")))

    def test_unvollstaendige_zeitreihen_gelten_nicht_als_bestand(self):
        leer = os.path.join(self.directory.name, "leer.sqlite3")
        with store.open_db(leer) as conn:
            store.write_observations(conn, "load", [(START, 50000.0)])
        self.assertIsNone(sources.available_range(leer))


if __name__ == "__main__":
    unittest.main()
