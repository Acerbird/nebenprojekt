"""Wann rechnet das Modell die Wirklichkeit — und wann eine andere Welt?

Zwei Dinge hängen daran und sind leicht zu verwechseln:

* Ein unberührter Regler soll bei echten Messwerten den tatsächlichen Stand
  meinen, nicht einen Vorgabewert von vorgestern. Die Voreinstellung von 90 GW
  Photovoltaik war 2024 richtig; im Januar 2025 standen 101 GW, und das Modell
  rechnete deshalb mit elf Gigawatt zu wenig.
* Ein bewegter Regler soll gelten — dann ist das Ergebnis aber kein Prüfstein
  mehr für das Modell, sondern eine Rechnung über eine Welt, die es nicht gab.
  Genau das muss dabeistehen.
"""

import unittest

from backend import analysis as anl
from backend.data import capacity, sources


def hat_daten():
    return sources.available_range() is not None


class AusbauTest(unittest.TestCase):
    START = "2025-01-06"

    def setUp(self):
        if not hat_daten():
            self.skipTest("Keine Messwerte vorhanden")

    def lauf(self, **kwargs):
        return anl.simulate(source="historical", start=self.START, hours=48, **kwargs)

    def test_ohne_regler_gilt_der_tatsaechliche_ausbau(self):
        ergebnis = self.lauf()
        echt = ergebnis["series_meta"]["installed_gw"]
        self.assertEqual(ergebnis["params"]["capacity_source"], "historical")
        self.assertAlmostEqual(ergebnis["params"]["wind_gw"], echt["wind"], places=1)
        self.assertAlmostEqual(ergebnis["params"]["solar_gw"], echt["solar"], places=1)

    def test_der_tatsaechliche_ausbau_ist_nicht_der_vorgabewert(self):
        """Wäre er es, liefe dieser Test durch, ohne etwas zu prüfen."""
        ergebnis = self.lauf()
        self.assertNotAlmostEqual(ergebnis["params"]["solar_gw"],
                                  anl.DEFAULTS["solar_gw"], places=0)

    def test_ein_gesetzter_regler_gilt(self):
        ergebnis = self.lauf(wind_gw=140.0)
        self.assertEqual(ergebnis["params"]["wind_gw"], 140.0)

    def test_ein_gesetzter_regler_verdraengt_den_anderen_nicht(self):
        ergebnis = self.lauf(wind_gw=140.0)
        echt = ergebnis["series_meta"]["installed_gw"]
        self.assertAlmostEqual(ergebnis["params"]["solar_gw"], echt["solar"], places=1)

    def test_erzeugte_profile_behalten_die_vorgabewerte(self):
        ergebnis = anl.simulate(source="synthetic", hours=48)
        self.assertEqual(ergebnis["params"]["capacity_source"], "fixed")
        self.assertEqual(ergebnis["params"]["wind_gw"], anl.DEFAULTS["wind_gw"])

    def test_der_tatsaechliche_ausbau_wandert_mit_den_jahren(self):
        frueh = anl.simulate(source="historical", start="2023-01-06", hours=24)
        spaet = anl.simulate(source="historical", start="2025-10-06", hours=24)
        self.assertGreater(spaet["params"]["solar_gw"], frueh["params"]["solar_gw"] + 20,
                           "Zwischen 2023 und 2025 sind über 40 GW Photovoltaik dazugekommen")

    def test_der_echte_ausbau_trifft_besser(self):
        """Der eigentliche Grund für die ganze Mechanik."""
        echt = self.lauf()["validation"]
        vorgabe = self.lauf(wind_gw=anl.DEFAULTS["wind_gw"],
                            solar_gw=anl.DEFAULTS["solar_gw"])["validation"]
        self.assertLess(echt["mean_absolute_error"], vorgabe["mean_absolute_error"])


class KontrafaktischTest(unittest.TestCase):
    """Misst der Vergleich das Modell — oder den Abstand zu einer anderen Welt?"""

    START = "2025-01-06"

    def setUp(self):
        if not hat_daten():
            self.skipTest("Keine Messwerte vorhanden")

    def gruende(self, **kwargs):
        ergebnis = anl.simulate(source="historical", start=self.START, hours=48, **kwargs)
        return ergebnis["validation"]["counterfactual"]

    def test_der_unberuehrte_lauf_bildet_die_wirklichkeit_ab(self):
        self.assertEqual(self.gruende(), [])

    def test_ein_anderer_ausbau_wird_genannt(self):
        gruende = self.gruende(wind_gw=140.0)
        self.assertTrue(any("Wind" in g for g in gruende), gruende)
        self.assertTrue(any("140" in g for g in gruende), gruende)

    def test_kleine_abweichungen_zaehlen_nicht(self):
        """Sonst meldete jede Rundung eine andere Welt."""
        echt = anl.simulate(source="historical", start=self.START,
                            hours=48)["series_meta"]["installed_gw"]
        self.assertEqual(self.gruende(wind_gw=round(echt["wind"] * 1.02, 1)), [])
        self.assertTrue(self.gruende(wind_gw=round(echt["wind"] * 1.5, 1)))

    def test_eingestellte_brennstoffpreise_werden_genannt(self):
        gruende = self.gruende(gas_price=29.0)
        self.assertTrue(any("Gaspreis" in g for g in gruende), gruende)
        self.assertFalse(any("CO₂" in g for g in gruende), gruende)

    def test_eine_gestreckte_last_wird_genannt(self):
        gruende = self.gruende(peak_load_gw=95.0)
        self.assertTrue(any("gestreckt" in g for g in gruende), gruende)

    def test_mehrere_abweichungen_werden_alle_genannt(self):
        gruende = self.gruende(wind_gw=140.0, gas_price=29.0, peak_load_gw=95.0)
        self.assertGreaterEqual(len(gruende), 3, gruende)


if __name__ == "__main__":
    unittest.main()
