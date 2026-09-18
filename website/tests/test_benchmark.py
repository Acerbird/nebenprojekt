"""Der Prüfsatz selbst.

Die Kennzahlen in README und Codekommentaren stammen aus backend/benchmark.py.
Rechnet dieses Programm falsch, sind alle Aussagen darüber wertlos — und
niemandem fiele es auf, weil plausible Zahlen herauskommen. Der volle Lauf
dauert Minuten und gehört nicht in den Testlauf; geprüft werden hier die
Bausteine und ein einzelner Zeitraum.
"""

import unittest

from backend import benchmark
from backend.data import sources


class PruefsatzTest(unittest.TestCase):
    def test_36_wochen_ueber_drei_jahre(self):
        alle = benchmark.wochen()
        self.assertEqual(len(alle), 36)
        self.assertEqual(len(set(alle)), 36)

    def test_jeder_monat_kommt_genau_einmal_vor(self):
        """Fehlten Monate, wäre der Maßstab nach Jahreszeit schief."""
        for jahr in benchmark.JAHRE:
            monate = sorted(int(w[5:7]) for w in benchmark.wochen() if w.startswith(str(jahr)))
            self.assertEqual(monate, list(range(1, 13)), jahr)

    def test_wochen_liegen_im_erwarteten_format(self):
        for woche in benchmark.wochen():
            self.assertRegex(woche, r"^\d{4}-\d{2}-06$")


class KennzahlenTest(unittest.TestCase):
    def test_gleiche_reihen_ergeben_keine_abweichung(self):
        paare = [(10.0, 10.0), (50.0, 50.0), (90.0, 90.0)]
        got = benchmark.kennzahlen(paare)
        self.assertEqual(got["mae"], 0.0)
        self.assertEqual(got["bias"], 0.0)
        self.assertAlmostEqual(got["correlation"], 1.0, places=6)

    def test_verzerrung_kennt_ein_vorzeichen_die_abweichung_nicht(self):
        """Zu hoch und zu tief heben sich im Bias auf, im MAE nicht."""
        paare = [(30.0, 20.0), (10.0, 20.0)]
        got = benchmark.kennzahlen(paare)
        self.assertEqual(got["bias"], 0.0)
        self.assertEqual(got["mae"], 10.0)

    def test_zu_wenige_paare_ergeben_nichts_statt_einer_scheinzahl(self):
        self.assertIsNone(benchmark.kennzahlen([]))
        self.assertIsNone(benchmark.kennzahlen([(10.0, 20.0)]))

    def test_stundenzahl_wird_mitgefuehrt(self):
        self.assertEqual(benchmark.kennzahlen([(1.0, 2.0), (3.0, 4.0)])["hours"], 2)


class EineWocheTest(unittest.TestCase):
    """Ein einzelner Zeitraum — der volle Satz wäre für den Testlauf zu lang."""

    @classmethod
    def setUpClass(cls):
        cls.hat_daten = sources.available_range() is not None

    def test_eine_woche_liefert_paare_aus_modell_und_messwert(self):
        if not self.hat_daten:
            self.skipTest("Keine Messwerte vorhanden")
        paare = benchmark.paare_der_woche("2024-06-06")
        self.assertGreater(len(paare), 100)
        for modell, ist in paare:
            self.assertIsInstance(modell, float)
            self.assertIsInstance(ist, float)

    def test_die_kennzahlen_einer_woche_sind_plausibel(self):
        """Kein Gütetest — ein Fangnetz gegen offensichtlich Falsches."""
        if not self.hat_daten:
            self.skipTest("Keine Messwerte vorhanden")
        werte = benchmark.kennzahlen(benchmark.paare_der_woche("2024-06-06"))
        self.assertLess(werte["mae"], 100.0)
        self.assertGreater(werte["correlation"], 0.0)

    def test_verschobene_wochen_zaehlen_nicht_mit(self):
        """Sonst ginge derselbe Zeitraum mehrfach in die Kennzahl ein.

        Liegt eine Woche außerhalb des Bestands, rückt das Modell sie an den
        Rand des Vorhandenen. Zwei solche Wochen wären dann dieselbe — der
        Prüfsatz zählte sie doppelt und niemand sähe es an den Zahlen.
        """
        self.assertEqual(benchmark.paare_der_woche("1990-01-06"), [])


if __name__ == "__main__":
    unittest.main()
