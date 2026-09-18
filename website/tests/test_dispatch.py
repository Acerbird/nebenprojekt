"""Tests des stündlichen Kraftwerkseinsatzes.

Geprüft wird nicht, ob konkrete Zahlen herauskommen, sondern ob die
Zusammenhänge stimmen, die die Website behauptet: Energiebilanz, Preisbildung
nach dem Einheitspreisverfahren und der Merit-Order-Effekt.
"""

import unittest

from backend import analysis as anl

# Parameter, bei denen der steuerbare Park die Last immer decken kann
# (72,3 GW installiert gegenüber 60 GW Höchstlast) — so stört keine
# Knappheitsstunde die Bilanz- und Preisprüfungen.
SAFE = {"peak_load_gw": 60.0, "wind_gw": 70.0, "solar_gw": 90.0,
        "co2_price": 80.0, "gas_price": 32.0, "hours": 72, "season": "winter"}


def run(**overrides):
    params = dict(SAFE)
    params.update(overrides)
    return anl.simulate(**params)


class EnergyBalanceTest(unittest.TestCase):
    """Nichts darf verschwinden: Was erzeugt wird, deckt genau die Last."""

    def setUp(self):
        self.result = run()
        self.assertEqual(self.result["kpis"]["scarcity_hours"], 0,
                         "Testaufbau kaputt: es sollte keine Knappheitsstunde geben")

    def test_erzeugung_deckt_die_last_in_jeder_stunde(self):
        demand = self.result["demand_gw"]
        generation = self.result["generation_gw"]
        for hour in range(len(demand)):
            produced = sum(series[hour] for series in generation.values())
            self.assertAlmostEqual(produced, demand[hour], delta=0.01,
                                   msg="Stunde %d" % hour)

    def test_abregelung_ist_genau_die_ungenutzte_erneuerbare_leistung(self):
        available = self.result["available_gw"]
        generation = self.result["generation_gw"]
        for hour, spill in enumerate(self.result["curtailed_gw"]):
            unused = ((available["wind"][hour] - generation["wind"][hour])
                      + (available["solar"][hour] - generation["solar"][hour]))
            self.assertAlmostEqual(spill, max(unused, 0.0), delta=0.01,
                                   msg="Stunde %d" % hour)

    def test_residuallast_ist_last_minus_erneuerbares_angebot(self):
        available = self.result["available_gw"]
        for hour, residual in enumerate(self.result["residual_load_gw"]):
            expected = (self.result["demand_gw"][hour]
                        - available["wind"][hour] - available["solar"][hour])
            self.assertAlmostEqual(residual, expected, delta=0.01)

    def test_keine_negative_erzeugung(self):
        for category, series in self.result["generation_gw"].items():
            self.assertTrue(all(v >= 0.0 for v in series), category)

    def test_kein_block_erzeugt_mehr_als_seine_kapazitaet(self):
        blocks = anl.merit_order(SAFE["co2_price"], SAFE["gas_price"],
                                 SAFE["wind_gw"], SAFE["solar_gw"])
        capacity = {}
        for block in blocks:
            capacity[block["category"]] = capacity.get(block["category"], 0.0) + block["capacity_gw"]
        for category, series in self.result["generation_gw"].items():
            # Wind und PV sind durch das Wetter zusätzlich begrenzt, hier reicht
            # die installierte Leistung als Obergrenze.
            self.assertLessEqual(max(series), capacity[category] + 0.01, category)


class PriceFormationTest(unittest.TestCase):
    """Einheitspreisverfahren: Der letzte benötigte Block setzt den Preis."""

    def test_preis_ist_immer_grenzkosten_eines_eingesetzten_blocks(self):
        result = run()
        self.assertEqual(result["kpis"]["scarcity_hours"], 0)
        blocks = anl.merit_order(SAFE["co2_price"], SAFE["gas_price"],
                                 SAFE["wind_gw"], SAFE["solar_gw"])
        known_costs = {b["cost"] for b in blocks}
        for hour, price in enumerate(result["price_eur_mwh"]):
            if result["curtailed_gw"][hour] > 0.01:
                continue  # Überschussstunden haben einen eigenen Preis
            self.assertIn(price, known_costs, "Stunde %d" % hour)

    def test_ueberschuss_druckt_den_preis_unter_null(self):
        result = run(wind_gw=300.0, solar_gw=400.0, peak_load_gw=30.0)
        self.assertGreater(result["kpis"]["curtailed_gwh"], 0.0)
        self.assertGreater(result["kpis"]["negative_price_hours"], 0)
        for hour, spill in enumerate(result["curtailed_gw"]):
            if spill > 0.01:
                self.assertEqual(result["price_eur_mwh"][hour], anl.SURPLUS_PRICE)

    def test_unterdeckung_erzeugt_den_knappheitspreis(self):
        result = run(wind_gw=0.0, solar_gw=0.0, peak_load_gw=150.0)
        self.assertGreater(result["kpis"]["scarcity_hours"], 0)
        self.assertEqual(result["kpis"]["max_price"], anl.SCARCITY_PRICE)

    def test_teurer_brennstoff_hebt_den_preis(self):
        billig = run(gas_price=20.0)["kpis"]["mean_price"]
        teuer = run(gas_price=150.0)["kpis"]["mean_price"]
        self.assertGreater(teuer, billig)


class MeritOrderEffectTest(unittest.TestCase):
    """Mehr Erneuerbare schieben teure Blöcke aus dem Markt: Preis und
    Emissionen sinken, der erneuerbare Anteil steigt."""

    def setUp(self):
        self.wenig = run(wind_gw=20.0, solar_gw=30.0)
        self.viel = run(wind_gw=150.0, solar_gw=200.0)

    def test_mehr_erneuerbare_senken_den_preis(self):
        self.assertLess(self.viel["kpis"]["mean_price"], self.wenig["kpis"]["mean_price"])

    def test_mehr_erneuerbare_senken_die_emissionen(self):
        self.assertLess(self.viel["kpis"]["emissions_kt"], self.wenig["kpis"]["emissions_kt"])

    def test_mehr_erneuerbare_heben_den_gruenen_anteil(self):
        self.assertGreater(self.viel["kpis"]["renewable_share"],
                           self.wenig["kpis"]["renewable_share"])

    def test_zubau_erzeugt_irgendwann_abregelung(self):
        self.assertEqual(run(wind_gw=10.0, solar_gw=10.0)["kpis"]["curtailed_gwh"], 0.0)
        self.assertGreater(self.viel["kpis"]["curtailed_gwh"], 0.0)

    def test_hoeherer_co2_preis_hebt_den_strompreis(self):
        ohne = run(co2_price=0.0, wind_gw=40.0, solar_gw=60.0)["kpis"]["mean_price"]
        mit = run(co2_price=150.0, wind_gw=40.0, solar_gw=60.0)["kpis"]["mean_price"]
        self.assertGreater(mit, ohne)

    def test_hoeherer_co2_preis_senkt_die_emissionsintensitaet(self):
        ohne = run(co2_price=0.0, wind_gw=40.0, solar_gw=60.0)
        mit = run(co2_price=150.0, wind_gw=40.0, solar_gw=60.0)
        self.assertLess(mit["kpis"]["emission_intensity_g_kwh"],
                        ohne["kpis"]["emission_intensity_g_kwh"])


class ParameterLimitsTest(unittest.TestCase):
    """Unsinnige Eingaben werden begrenzt, nicht abgelehnt — die API soll
    nie mit Müll rechnen und nie mit einem Fehler antworten."""

    def test_werte_unterhalb_des_bereichs_werden_angehoben(self):
        params = anl.simulate(wind_gw=-50.0, gas_price=-1.0, hours=1)["params"]
        self.assertEqual(params["wind_gw"], anl.LIMITS["wind_gw"][0])
        self.assertEqual(params["gas_price"], anl.LIMITS["gas_price"][0])
        self.assertEqual(params["hours"], anl.LIMITS["hours"][0])

    def test_werte_oberhalb_des_bereichs_werden_gekappt(self):
        params = anl.simulate(wind_gw=9999.0, solar_gw=9999.0,
                              co2_price=9999.0, hours=99999)["params"]
        self.assertEqual(params["wind_gw"], anl.LIMITS["wind_gw"][1])
        self.assertEqual(params["solar_gw"], anl.LIMITS["solar_gw"][1])
        self.assertEqual(params["co2_price"], anl.LIMITS["co2_price"][1])
        self.assertEqual(params["hours"], anl.LIMITS["hours"][1])

    def test_unbekannte_jahreszeit_faellt_auf_den_standard(self):
        self.assertEqual(anl.simulate(season="fruehling")["params"]["season"],
                         anl.DEFAULTS["season"])

    def test_laenge_aller_zeitreihen_entspricht_den_stunden(self):
        result = run(hours=48)
        self.assertEqual(len(result["demand_gw"]), 48)
        self.assertEqual(len(result["price_eur_mwh"]), 48)
        self.assertEqual(len(result["timestamps"]), 48)
        for series in result["generation_gw"].values():
            self.assertEqual(len(series), 48)


class KnownSimplificationTest(unittest.TestCase):
    """Hält bewusste Vereinfachungen fest, damit sie sichtbar bleiben.

    Diese Tests dürfen fallen, sobald das Modell in Phase 3 wächst — dann sind
    sie der Ort, an dem die neue Erwartung beschrieben wird.
    """

    def test_must_run_bloecke_laufen_derzeit_nicht_zwingend_mit(self):
        # Biomasse und Laufwasser sind als "must_run" deklariert, werden aber
        # wie alle anderen Blöcke nach Grenzkosten eingesetzt. Bei großem
        # Überschuss stehen sie deshalb still.
        result = run(wind_gw=300.0, solar_gw=400.0, peak_load_gw=30.0)
        must_run_capacity = sum(p["capacity_gw"] for p in anl.FLEET["plants"]
                                if p["kind"] == "must_run")
        self.assertLess(min(result["generation_gw"]["sonstige_ee"]), must_run_capacity)

    def test_ohne_speicher_wird_ueberschuss_vollstaendig_abgeregelt(self):
        result = run(wind_gw=300.0, solar_gw=400.0, peak_load_gw=30.0)
        self.assertGreater(result["kpis"]["curtailed_gwh"], 0.0)


if __name__ == "__main__":
    unittest.main()
