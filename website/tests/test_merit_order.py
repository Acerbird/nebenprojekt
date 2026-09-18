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
    """Mittlere Grenzkosten der Gebotsspanne."""
    return anl.marginal_cost(plant(plant_id), co2_price, gas_price)


def span(plant_id, co2_price, gas_price=GAS_PRICE):
    """Gebotsspanne (unteres und oberes Ende) eines Blocks."""
    return anl.block_costs(plant(plant_id), co2_price, gas_price)


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

    def test_formel_stimmt_an_beiden_enden_der_spanne(self):
        """Unteres Ende beim besten Wirkungsgrad, oberes beim schlechtesten."""
        gud = plant("gud")
        brennstoff = 32.0 + 80.0 * 0.201          # €/MWh thermisch inklusive CO₂
        low, high = span("gud", 80.0)
        self.assertAlmostEqual(low, brennstoff / gud["efficiency_high"] + gud["var_om_low"], places=2)
        self.assertAlmostEqual(high, brennstoff / gud["efficiency_low"] + gud["var_om_high"], places=2)

    def test_spanne_ist_geordnet_und_nicht_leer(self):
        """Ein Kraftwerkspark hat alte und neue Anlagen — daher eine Spanne."""
        for plant_id in ("braunkohle", "steinkohle", "gud", "gasturbine"):
            low, high = span(plant_id, 80.0)
            self.assertLess(low, high, plant_id)

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

    def test_umschlagpunkt_steinkohle_gas_liegt_bei_rund_50_euro(self):
        # Steht als Hinweis am CO₂-Regler der Analyseseite. Seit die Blöcke in
        # Spannen bieten, verschiebt sich der Punkt etwas — verglichen wird die
        # Mitte der jeweiligen Spanne.
        self.assertAlmostEqual(switch_point("steinkohle", "gud"), 57.0, delta=3.0)

    def test_umschlagpunkt_braunkohle_gas_liegt_darueber(self):
        """Braunkohle hält länger durch als Steinkohle: billiger Brennstoff,
        aber die höchsten Emissionen."""
        self.assertGreater(switch_point("braunkohle", "gud"),
                           switch_point("steinkohle", "gud"))

    def test_teures_gas_verschiebt_den_umschlagpunkt_nach_oben(self):
        """Gaskrise 2022: teures Gas hält die Kohle trotz CO₂-Preis im Geld."""
        self.assertGreater(switch_point("steinkohle", "gud", gas_price=80.0),
                           switch_point("steinkohle", "gud", gas_price=32.0))


class MeritOrderStructureTest(unittest.TestCase):
    def setUp(self):
        self.blocks = anl.merit_order(co2_price=80.0, gas_price=32.0,
                                      wind_gw=70.0, solar_gw=90.0)

    def test_nach_beginn_der_gebotsspanne_sortiert(self):
        """Die Spannen überlappen sich — sortiert wird nach ihrem Beginn."""
        starts = [b["cost_low"] for b in self.blocks]
        self.assertEqual(starts, sorted(starts))

    def test_jeder_block_hat_eine_geordnete_spanne(self):
        for block in self.blocks:
            self.assertLessEqual(block["cost_low"], block["cost_high"], block["id"])
            self.assertLessEqual(block["cost_low"], block["cost"], block["id"])
            self.assertLessEqual(block["cost"], block["cost_high"], block["id"])

    def test_kumulation_ist_lueckenlos(self):
        previous_to = 0.0
        for block in self.blocks:
            self.assertAlmostEqual(block["from_gw"], previous_to, places=2)
            self.assertAlmostEqual(block["to_gw"] - block["from_gw"],
                                   block["capacity_gw"], places=2)
            previous_to = block["to_gw"]

    def test_erneuerbare_bieten_unter_null(self):
        """Wer Einspeisevergütung bekommt, bietet auch bei negativen Preisen an,
        statt abzuschalten — deshalb gibt es überhaupt negative Börsenpreise."""
        by_id = {b["id"]: b for b in self.blocks}
        for block_id in ("wind", "solar"):
            self.assertLess(by_id[block_id]["cost_low"], 0.0, block_id)
        self.assertLess(by_id["solar"]["cost_low"], by_id["wind"]["cost_low"])

    def test_erneuerbare_stehen_vor_jedem_brennstoffgebot(self):
        """Wind und PV bieten unter jedem Block, der zu Grenzkosten anbietet.

        Nicht unter *jedem* Block: Ist die Mindestlast eingeschaltet, liegt
        dieser Teil der thermischen Blöcke noch unter dem Windgebot. Das ist
        gewollt — ein Braunkohleblock, der nachts durchläuft, um morgens nicht
        neu anfahren zu müssen, bietet tiefer als eine Windanlage, die einfach
        stehenbleiben kann.
        """
        reihenfolge = [b["id"] for b in self.blocks]
        zu_grenzkosten = [b["id"] for b in self.blocks
                          if b["kind"] == "thermal" and not b["id"].endswith("_mindestlast")]
        self.assertTrue(zu_grenzkosten)
        for block_id in ("wind", "solar"):
            for thermisch in zu_grenzkosten:
                self.assertLess(reihenfolge.index(block_id), reihenfolge.index(thermisch),
                                "%s müsste vor %s stehen" % (block_id, thermisch))

    def test_mindestlast_steht_noch_vor_dem_wind(self):
        blocks = anl.merit_order(co2_price=80.0, gas_price=32.0,
                                 wind_gw=70.0, solar_gw=90.0, min_load=True)
        reihenfolge = [b["id"] for b in blocks]
        mindestlast = [b["id"] for b in blocks if b["id"].endswith("_mindestlast")]
        self.assertTrue(mindestlast, "Kein Mindestlastblock vorhanden")
        for block_id in mindestlast:
            self.assertLess(reihenfolge.index(block_id), reihenfolge.index("wind"), block_id)

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
