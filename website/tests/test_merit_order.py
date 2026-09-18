"""Tests der Merit-Order: Grenzkosten, Reihenfolge, Umschlagpunkte.

Die Schwellenwerte sind bewusst hart hinterlegt, denn sie stehen so auch in den
Hinweistexten der Analyseseite. Ändert sich der Kraftwerkspark in
static_data/power_plants.json, fallen diese Tests — und erinnern daran, dass die
Texte mitgezogen werden müssen.
"""

import unittest

from backend import analysis as anl

GAS_PRICE = anl.DEFAULTS["gas_price"]


def plant(plant_id):
    return next(p for p in anl.FLEET["plants"] if p["id"] == plant_id)


def cost(plant_id, co2_price, gas_price=GAS_PRICE):
    return anl.marginal_cost(plant(plant_id), co2_price, gas_price)


def switch_point(cheap_id, expensive_id, gas_price=GAS_PRICE, low=0.0, high=300.0):
    """CO₂-Preis, ab dem `cheap_id` teurer wird als `expensive_id` (Bisektion)."""
    for _ in range(60):
        mid = (low + high) / 2
        if cost(cheap_id, mid, gas_price) < cost(expensive_id, mid, gas_price):
            low = mid
        else:
            high = mid
    return (low + high) / 2


class MarginalCostTest(unittest.TestCase):
    """Grenzkosten = Brennstoff/Wirkungsgrad + CO₂-Kosten/Wirkungsgrad + var. Betriebskosten."""

    def test_formel_stimmt_fuer_gud(self):
        expected = 32.0 / 0.58 + 80.0 * 0.201 / 0.58 + 1.5
        self.assertAlmostEqual(cost("gud", 80.0), expected, places=2)

    def test_hoeherer_wirkungsgrad_senkt_kosten_und_emissionen(self):
        # GuD und Gasturbine verbrennen denselben Brennstoff, nur unterschiedlich gut.
        self.assertLess(cost("gud", 80.0), cost("gasturbine", 80.0))
        self.assertLess(anl.emission_intensity(plant("gud")),
                        anl.emission_intensity(plant("gasturbine")))

    def test_gaspreis_wirkt_nur_auf_gasbloecke(self):
        self.assertEqual(cost("steinkohle", 50.0, 32.0), cost("steinkohle", 50.0, 120.0))
        self.assertGreater(cost("gud", 50.0, 120.0), cost("gud", 50.0, 32.0))

    def test_must_run_bloecke_haengen_nicht_am_co2_preis(self):
        self.assertEqual(cost("biomasse", 0.0), cost("biomasse", 300.0))
        self.assertEqual(anl.emission_intensity(plant("biomasse")), 0.0)

    def test_co2_preis_verteuert_jeden_thermischen_block(self):
        for plant_id in ("braunkohle", "steinkohle", "gud", "gasturbine"):
            costs = [cost(plant_id, c) for c in (0.0, 50.0, 100.0, 200.0)]
            self.assertEqual(costs, sorted(costs), plant_id)

    def test_emissionsintensitaet_folgt_der_erwarteten_rangfolge(self):
        braun = anl.emission_intensity(plant("braunkohle"))
        stein = anl.emission_intensity(plant("steinkohle"))
        gud = anl.emission_intensity(plant("gud"))
        self.assertGreater(braun, stein)
        self.assertGreater(stein, gud)


class FuelSwitchTest(unittest.TestCase):
    """Der CO₂-Preis dreht die Reihenfolge von Kohle und Gas um.

    Das ist die zentrale Aussage der Analyseseite: Ein hoher Zertifikatspreis
    trifft den schmutzigen Brennstoff härter, weil die Emissionen je erzeugter
    MWh mit dem Wirkungsgrad skalieren.
    """

    def test_ohne_co2_preis_ist_kohle_am_billigsten(self):
        self.assertLess(cost("braunkohle", 0.0), cost("steinkohle", 0.0))
        self.assertLess(cost("steinkohle", 0.0), cost("gud", 0.0))

    def test_steinkohle_vor_gas_bei_niedrigem_co2_preis(self):
        self.assertLess(cost("steinkohle", 40.0), cost("gud", 40.0))

    def test_gas_vor_steinkohle_bei_hohem_co2_preis(self):
        self.assertLess(cost("gud", 70.0), cost("steinkohle", 70.0))

    def test_umschlagpunkt_steinkohle_gas_liegt_bei_53_euro(self):
        # Steht als Hinweis am CO₂-Regler der Analyseseite.
        self.assertAlmostEqual(switch_point("steinkohle", "gud"), 52.6, delta=1.0)

    def test_umschlagpunkt_braunkohle_gas_liegt_bei_62_euro(self):
        self.assertAlmostEqual(switch_point("braunkohle", "gud"), 62.0, delta=1.0)

    def test_teures_gas_verschiebt_den_umschlagpunkt_nach_oben(self):
        """Gaskrise 2022: teures Gas hält die Kohle trotz CO₂-Preis im Geld."""
        self.assertGreater(switch_point("steinkohle", "gud", gas_price=80.0),
                           switch_point("steinkohle", "gud", gas_price=32.0))


class MeritOrderStructureTest(unittest.TestCase):
    def setUp(self):
        self.blocks = anl.merit_order(co2_price=80.0, gas_price=32.0,
                                      wind_gw=70.0, solar_gw=90.0)

    def test_nach_grenzkosten_sortiert(self):
        costs = [b["cost"] for b in self.blocks]
        self.assertEqual(costs, sorted(costs))

    def test_kumulation_ist_lueckenlos(self):
        previous_to = 0.0
        for block in self.blocks:
            self.assertAlmostEqual(block["from_gw"], previous_to, places=2)
            self.assertAlmostEqual(block["to_gw"] - block["from_gw"],
                                   block["capacity_gw"], places=2)
            previous_to = block["to_gw"]

    def test_erneuerbare_stehen_ohne_grenzkosten_am_anfang(self):
        first_two = {b["id"] for b in self.blocks[:2]}
        self.assertEqual(first_two, {"wind", "solar"})
        self.assertEqual([b["cost"] for b in self.blocks[:2]], [0.0, 0.0])

    def test_installierte_leistung_wird_uebernommen(self):
        by_id = {b["id"]: b for b in self.blocks}
        self.assertEqual(by_id["wind"]["capacity_gw"], 70.0)
        self.assertEqual(by_id["solar"]["capacity_gw"], 90.0)

    def test_payload_meldet_gesamtkapazitaet(self):
        payload = anl.merit_order_payload(co2_price=80.0, gas_price=32.0,
                                          wind_gw=70.0, solar_gw=90.0)
        expected = 70.0 + 90.0 + sum(p["capacity_gw"] for p in anl.FLEET["plants"])
        self.assertAlmostEqual(payload["total_capacity_gw"], expected, places=2)


if __name__ == "__main__":
    unittest.main()
