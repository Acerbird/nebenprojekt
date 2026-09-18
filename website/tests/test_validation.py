"""Tests des Vergleichs mit dem tatsächlichen Börsenpreis.

Der ehrlichste Prüfstein für ein Strommarktmodell: Wie nah kommt die Rechnung
an das, was wirklich bezahlt wurde? Die Kennzahlen müssen stimmen, sonst wäre
der Vergleich wertlos — gerade weil er dem Modell Schwächen nachweisen soll.
"""

import os
import tempfile
import unittest
from datetime import datetime, timezone

from backend import analysis as anl
from backend.data import sources, store

START = int(datetime(2024, 6, 3, tzinfo=timezone.utc).timestamp())


class CompareTest(unittest.TestCase):
    def test_gleiche_reihen_ergeben_keine_abweichung(self):
        preise = [10.0, 50.0, 90.0, 30.0]
        got = anl.compare_with_actual(preise, list(preise))
        self.assertEqual(got["mean_absolute_error"], 0.0)
        self.assertEqual(got["bias"], 0.0)
        self.assertAlmostEqual(got["correlation"], 1.0, places=6)

    def test_mittlere_abweichung_ist_der_durchschnittliche_abstand(self):
        got = anl.compare_with_actual([10.0, 20.0], [20.0, 40.0])
        self.assertEqual(got["mean_absolute_error"], 15.0)

    def test_abweichung_kennt_kein_vorzeichen(self):
        """Zu hoch und zu tief heben sich nicht auf — sonst sähe ein schlechtes
        Modell mit gleich großen Fehlern in beide Richtungen perfekt aus."""
        got = anl.compare_with_actual([10.0, 30.0], [20.0, 20.0])
        self.assertEqual(got["mean_absolute_error"], 10.0)
        self.assertEqual(got["bias"], 0.0)

    def test_verzerrung_zeigt_die_richtung(self):
        zu_tief = anl.compare_with_actual([10.0, 20.0], [50.0, 60.0])
        zu_hoch = anl.compare_with_actual([50.0, 60.0], [10.0, 20.0])
        self.assertLess(zu_tief["bias"], 0)
        self.assertGreater(zu_hoch["bias"], 0)

    def test_korrelation_erkennt_den_verlauf_trotz_falschem_niveau(self):
        """Ein Modell kann den Rhythmus treffen und im Niveau danebenliegen."""
        markt = [10.0, 50.0, 90.0, 30.0]
        modell = [v * 2 + 100 for v in markt]
        got = anl.compare_with_actual(modell, markt)
        self.assertAlmostEqual(got["correlation"], 1.0, places=6)
        self.assertGreater(got["mean_absolute_error"], 100)

    def test_gegenlaeufiger_verlauf_ergibt_negative_korrelation(self):
        got = anl.compare_with_actual([10.0, 50.0, 90.0], [90.0, 50.0, 10.0])
        self.assertAlmostEqual(got["correlation"], -1.0, places=6)

    def test_fehlende_stunden_werden_uebersprungen(self):
        got = anl.compare_with_actual([10.0, 20.0, 30.0], [10.0, None, 30.0])
        self.assertEqual(got["hours_compared"], 2)
        self.assertEqual(got["mean_absolute_error"], 0.0)

    def test_ohne_vergleichbare_stunden_gibt_es_kein_ergebnis(self):
        self.assertIsNone(anl.compare_with_actual([10.0, 20.0], [None, None]))
        self.assertIsNone(anl.compare_with_actual([10.0], [10.0]))

    def test_konstante_reihe_liefert_keine_korrelation(self):
        """Ohne Schwankung ist eine Korrelation nicht definiert."""
        got = anl.compare_with_actual([50.0, 50.0, 50.0], [10.0, 20.0, 30.0])
        self.assertIsNone(got["correlation"])
        self.assertIsNotNone(got["mean_absolute_error"])

    def test_tatsaechliche_preise_kommen_mit_zurueck(self):
        got = anl.compare_with_actual([10.0, 20.0], [15.0, 25.0])
        self.assertEqual(got["actual_price_eur_mwh"], [15.0, 25.0])


class ActualPriceLoadingTestCase(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = os.path.join(self.directory.name, "preise.sqlite3")
        self.stamps = [START + h * 3600 for h in range(24)]

    def write(self, values):
        with store.open_db(self.path) as conn:
            store.write_observations(conn, "price", list(zip(self.stamps, values)))


class ActualPriceTest(ActualPriceLoadingTestCase):
    def test_liest_die_preisreihe(self):
        self.write([float(i) for i in range(24)])
        got = sources.actual_prices(START, 24, db_path=self.path)
        self.assertEqual(got, [float(i) for i in range(24)])

    def test_luecken_bleiben_luecken(self):
        """Für einen Vergleich zählt nur, was wirklich gemessen wurde."""
        values = [50.0] * 24
        values[5] = None
        self.write(values)
        got = sources.actual_prices(START, 24, db_path=self.path)
        self.assertIsNone(got[5])

    def test_ohne_datenbank_gibt_es_nichts_zu_vergleichen(self):
        self.assertIsNone(sources.actual_prices(
            START, 24, db_path=os.path.join(self.directory.name, "fehlt.sqlite3")))

    def test_zeitraum_ohne_preise_gibt_nichts_zurueck(self):
        self.write([50.0] * 24)
        weit_weg = int(datetime(2001, 1, 1, tzinfo=timezone.utc).timestamp())
        self.assertIsNone(sources.actual_prices(weit_weg, 24, db_path=self.path))


class ValidationInSimulationTest(unittest.TestCase):
    def test_erzeugte_profile_werden_nicht_verglichen(self):
        """Gegen ein erfundenes Wetter gibt es keinen echten Preis."""
        self.assertIsNone(anl.simulate(hours=24)["validation"])


if __name__ == "__main__":
    unittest.main()


class ForecastStorageTest(ActualPriceLoadingTestCase):
    """Vorberechnete Vorhersagen liegen in derselben Datenbank wie die Messwerte."""

    def test_werte_kommen_unveraendert_zurueck(self):
        with store.open_db(self.path) as conn:
            store.write_forecasts(conn, "model_1", [(s, 50.0 + i)
                                                    for i, s in enumerate(self.stamps)])
            got = store.read_forecast(conn, "model_1", self.stamps[0], self.stamps[-1] + 1)
        self.assertEqual(len(got), 24)
        self.assertEqual(got[self.stamps[0]], 50.0)

    def test_neuer_lauf_ersetzt_den_alten(self):
        """Verglichen wird immer gegen den jüngsten Stand eines Modells."""
        with store.open_db(self.path) as conn:
            store.write_forecasts(conn, "model_1", [(self.stamps[0], 10.0)], created_at=1)
            store.write_forecasts(conn, "model_1", [(self.stamps[0], 99.0)], created_at=2)
            got = store.read_forecast(conn, "model_1", self.stamps[0], self.stamps[0] + 1)
        self.assertEqual(got[self.stamps[0]], 99.0)

    def test_modelle_stoeren_einander_nicht(self):
        with store.open_db(self.path) as conn:
            store.write_forecasts(conn, "model_1", [(self.stamps[0], 10.0)])
            store.write_forecasts(conn, "model_2", [(self.stamps[0], 20.0)])
            self.assertEqual(store.read_forecast(conn, "model_1", 0, 1 << 40)[self.stamps[0]], 10.0)
            self.assertEqual(store.read_forecast(conn, "model_2", 0, 1 << 40)[self.stamps[0]], 20.0)

    def test_uebersicht_nennt_zeitraum_je_modell(self):
        with store.open_db(self.path) as conn:
            store.write_forecasts(conn, "model_1", list(zip(self.stamps, [42.0] * 24)))
            info = store.forecast_coverage(conn)["model_1"]
        self.assertEqual(info["first_ts"], self.stamps[0])
        self.assertEqual(info["last_ts"], self.stamps[-1])
        self.assertEqual(info["points"], 24)

    def test_leere_stunden_zaehlen_nicht_als_vorhersage(self):
        with store.open_db(self.path) as conn:
            store.write_forecasts(conn, "model_1", [(self.stamps[0], None), (self.stamps[1], 5.0)])
            self.assertEqual(store.forecast_coverage(conn)["model_1"]["points"], 1)


class ForecastLoadingTest(ActualPriceLoadingTestCase):
    def test_liefert_nur_modelle_mit_werten_im_zeitraum(self):
        with store.open_db(self.path) as conn:
            store.write_forecasts(conn, "model_1", list(zip(self.stamps, [60.0] * 24)))
            store.write_forecasts(conn, "model_2", [(self.stamps[0] - 10 * 86400, 70.0)])
        got = sources.model_forecasts(START, 24, db_path=self.path)
        self.assertIn("model_1", got)
        self.assertNotIn("model_2", got)

    def test_reihe_hat_die_laenge_des_zeitraums(self):
        with store.open_db(self.path) as conn:
            store.write_forecasts(conn, "model_1", list(zip(self.stamps[:12], [60.0] * 12)))
        got = sources.model_forecasts(START, 24, db_path=self.path)["model_1"]
        self.assertEqual(len(got), 24)
        self.assertEqual(got[0], 60.0)
        self.assertIsNone(got[20])

    def test_ohne_datenbank_gibt_es_keine_vorhersagen(self):
        self.assertEqual(sources.model_forecasts(
            START, 24, db_path=os.path.join(self.directory.name, "fehlt.sqlite3")), {})

    def test_erzeugte_profile_bekommen_keine_vorhersagen(self):
        """Eine Vorhersage gibt es nur für einen echten Zeitraum."""
        self.assertEqual(anl.simulate(hours=24)["forecasts"], {})


class BenchmarkChoiceTest(ActualPriceLoadingTestCase):
    """Alle Modelle müssen an derselben Reihe gemessen werden.

    Die Vorhersagemodelle wurden auf einer Preisreihe entwickelt, die
    Viertelstundenpreise mittelt; SMARD weist den Stundenkontrakt aus. Beide
    laufen eng beieinander, weichen je Stunde aber spürbar ab. Ein Modell an
    der falschen Reihe zu messen, lastet ihm einen Fehler an, den es nicht
    gemacht hat.
    """

    def write_reference(self, values):
        with store.open_db(self.path) as conn:
            store.write_observations(conn, sources.REFERENCE_SERIES,
                                     list(zip(self.stamps, values)))

    def test_referenzreihe_wird_gelesen(self):
        self.write_reference([float(i) for i in range(24)])
        got = sources.reference_prices(START, 24, db_path=self.path)
        self.assertEqual(got[0], 0.0)
        self.assertEqual(got[23], 23.0)

    def test_ohne_referenzreihe_gibt_es_nichts(self):
        self.write([50.0] * 24)          # nur SMARD-Preise
        self.assertIsNone(sources.reference_prices(START, 24, db_path=self.path))

    def test_referenz_und_smard_sind_getrennte_reihen(self):
        self.write([50.0] * 24)
        self.write_reference([60.0] * 24)
        self.assertEqual(sources.actual_prices(START, 24, db_path=self.path)[0], 50.0)
        self.assertEqual(sources.reference_prices(START, 24, db_path=self.path)[0], 60.0)

    def test_luecken_bleiben_luecken(self):
        werte = [50.0] * 24
        werte[7] = None
        self.write_reference(werte)
        self.assertIsNone(sources.reference_prices(START, 24, db_path=self.path)[7])


class BenchmarkInSimulationTest(unittest.TestCase):
    """Zusammenspiel in der Simulation, gegen eine künstliche Datenbank."""

    HOURS = 48

    def setUp(self):
        import tempfile
        from unittest import mock

        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        path = os.path.join(self.directory.name, "bench.sqlite3")
        self.stamps = [START + h * 3600 for h in range(self.HOURS)]

        with store.open_db(path) as conn:
            for name, wert in (("load", 50000.0), ("wind_onshore", 20000.0),
                               ("wind_offshore", 4000.0), ("solar", 10000.0)):
                store.write_observations(conn, name, [(t, wert) for t in self.stamps])
            store.write_observations(conn, "price", [(t, 100.0) for t in self.stamps])
            self.conn_path = path

        patcher = mock.patch.object(store, "DEFAULT_PATH", path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def add_reference_and_forecast(self):
        with store.open_db(self.conn_path) as conn:
            store.write_observations(conn, sources.REFERENCE_SERIES,
                                     [(t, 80.0) for t in self.stamps])
            store.write_forecasts(conn, "model_1", [(t, 82.0) for t in self.stamps])

    def run_case(self):
        return anl.simulate(source="historical", start="2024-06-03",
                            hours=self.HOURS, wind_gw=70.0, solar_gw=90.0)

    def test_ohne_vorhersagen_gilt_der_smard_preis(self):
        result = self.run_case()
        self.assertEqual(result["validation"]["benchmark"], "smard")
        self.assertAlmostEqual(result["validation"]["mean_actual"], 100.0, places=1)

    def test_mit_vorhersagen_gilt_deren_referenzreihe(self):
        self.add_reference_and_forecast()
        result = self.run_case()
        self.assertEqual(result["validation"]["benchmark"], "reference")
        self.assertAlmostEqual(result["validation"]["mean_actual"], 80.0, places=1)

    def test_angezeigt_bleibt_der_smard_preis(self):
        """Der Maßstab ändert die Bewertung, nicht die gezeichnete Kurve."""
        self.add_reference_and_forecast()
        result = self.run_case()
        self.assertEqual(result["validation"]["actual_price_eur_mwh"][0], 100.0)

    def test_alle_modelle_teilen_den_maßstab(self):
        self.add_reference_and_forecast()
        result = self.run_case()
        vergleich = result["forecasts"]["model_1"]["comparison"]
        # Vorhersage 82 gegen Referenz 80 ergibt genau 2 — nicht 18 gegen SMARD.
        self.assertAlmostEqual(vergleich["mean_absolute_error"], 2.0, places=1)
