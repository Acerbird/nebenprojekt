"""Tests des stündlichen Kraftwerkseinsatzes.

Geprüft wird nicht, ob konkrete Zahlen herauskommen, sondern ob die
Zusammenhänge stimmen, die die Website behauptet: Energiebilanz, Preisbildung
nach dem Einheitspreisverfahren und der Merit-Order-Effekt.
"""

import unittest

from backend import analysis as anl
from backend.utils import profiles

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

    def test_erzeugung_deckt_last_und_speicherladung(self):
        """Seit es Speicher gibt, ist die Ladung ein Verbraucher wie jeder andere."""
        demand = self.result["demand_gw"]
        generation = self.result["generation_gw"]
        charge = self.result["storage_charge_gw"]
        for hour in range(len(demand)):
            produced = sum(series[hour] for series in generation.values())
            self.assertAlmostEqual(produced, demand[hour] + charge[hour], delta=0.02,
                                   msg="Stunde %d" % hour)

    def test_ohne_speicherbetrieb_deckt_die_erzeugung_genau_die_last(self):
        demand = self.result["demand_gw"]
        generation = self.result["generation_gw"]
        for hour in range(len(demand)):
            if self.result["storage_charge_gw"][hour] > 0.01:
                continue
            produced = sum(series[hour] for series in generation.values())
            self.assertAlmostEqual(produced, demand[hour], delta=0.02,
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
        # Speicher steht nicht in der Merit-Order, hat aber eine Entladeleistung.
        for unit in anl.STORAGE_UNITS:
            capacity[unit["category"]] = capacity.get(unit["category"], 0.0) + unit["power_gw"]
        for category, series in self.result["generation_gw"].items():
            # Wind und PV sind durch das Wetter zusätzlich begrenzt, hier reicht
            # die installierte Leistung als Obergrenze.
            self.assertLessEqual(max(series), capacity[category] + 0.01, category)


class PriceFormationTest(unittest.TestCase):
    """Einheitspreisverfahren: Der letzte benötigte Block setzt den Preis."""

    def test_preis_liegt_in_der_gebotsspanne_eines_eingesetzten_blocks(self):
        """Seit die Blöcke in Spannen bieten, ist der Preis nicht mehr einer von
        wenigen festen Werten, sondern liegt irgendwo innerhalb einer Spanne."""
        result = run()
        self.assertEqual(result["kpis"]["scarcity_hours"], 0)
        blocks = anl.merit_order(SAFE["co2_price"], SAFE["gas_price"],
                                 SAFE["wind_gw"], SAFE["solar_gw"])
        for hour, price in enumerate(result["price_eur_mwh"]):
            passend = any(b["cost_low"] - 0.01 <= price <= b["cost_high"] + 0.01
                          for b in blocks)
            self.assertTrue(passend, "Stunde %d: Preis %.2f" % (hour, price))

    def test_preise_sind_fein_abgestuft(self):
        """Der Grund für den Umbau: Feste Blockkosten ergaben nur eine Handvoll
        verschiedener Preise über eine ganze Woche."""
        result = run(hours=168)
        self.assertGreater(len(set(result["price_eur_mwh"])), 30)

    def test_ueberschuss_druckt_den_preis_unter_null(self):
        """Muss abgeregelt werden, fällt der Preis in den Gebotsbereich der
        Erneuerbaren — also unter null."""
        result = run(wind_gw=300.0, solar_gw=400.0, peak_load_gw=30.0)
        self.assertGreater(result["kpis"]["curtailed_gwh"], 0.0)
        self.assertGreater(result["kpis"]["negative_price_hours"], 0)
        for hour, spill in enumerate(result["curtailed_gw"]):
            if spill > 0.01:
                self.assertLess(result["price_eur_mwh"][hour], 0.0, "Stunde %d" % hour)

    def test_je_groesser_der_ueberschuss_desto_tiefer_der_preis(self):
        """Der Minimalpreis stößt irgendwann an das tiefste Gebot; der
        Durchschnitt sinkt weiter, weil immer mehr Stunden dort landen."""
        wenig = run(wind_gw=120.0, solar_gw=150.0, peak_load_gw=60.0)["kpis"]["mean_price"]
        viel = run(wind_gw=300.0, solar_gw=400.0, peak_load_gw=60.0)["kpis"]["mean_price"]
        self.assertLess(viel, wenig)

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


class MustRunTest(unittest.TestCase):
    """Biomasse und Laufwasser lassen sich nicht stündlich abstellen.

    Sie speisen ein, ob ihre Leistung gebraucht wird oder nicht — abgeregelt
    werden zuerst Wind und Photovoltaik, die fernsteuerbar sind.
    """

    def must_run_capacity(self, hydro_factor=1.0):
        total = 0.0
        for plant in anl.FLEET["plants"]:
            if plant["kind"] != "must_run":
                continue
            factor = hydro_factor if plant["id"] == "laufwasser" else 1.0
            total += plant["capacity_gw"] * factor
        return total

    def test_laeuft_auch_bei_riesigem_erneuerbaren_ueberschuss_weiter(self):
        """Laufwasser bietet bis -500 €/MWh und läuft deshalb praktisch immer."""
        result = run(wind_gw=300.0, solar_gw=400.0, peak_load_gw=60.0)
        for hour, value in enumerate(result["generation_gw"]["sonstige_ee"]):
            self.assertGreater(value, 0.0, "Stunde %d" % hour)

    def test_biomasse_drosselt_bei_sehr_tiefen_preisen(self):
        """Biomasse bietet nur bis -200 — darunter steigt auch sie aus.
        Genau das unterscheidet ein Gebot von einer festen Rangfolge."""
        knapp = run(wind_gw=10.0, solar_gw=10.0)["generation_gw"]["sonstige_ee"]
        ueberschuss = run(wind_gw=300.0, solar_gw=400.0,
                          peak_load_gw=30.0)["generation_gw"]["sonstige_ee"]
        self.assertGreater(max(knapp), max(ueberschuss))

    def test_erneuerbare_werden_vor_dem_must_run_abgeregelt(self):
        result = run(wind_gw=300.0, solar_gw=400.0, peak_load_gw=60.0)
        self.assertGreater(result["kpis"]["curtailed_gwh"], 0.0)
        self.assertGreater(min(result["generation_gw"]["sonstige_ee"]), 0.0)

    def test_wird_gedrosselt_wenn_die_last_darunter_faellt(self):
        """Unterschreitet die Last selbst die Must-Run-Leistung, muss auch sie herunter."""
        blocks = anl.merit_order(80.0, 32.0, wind_gw=0.0, solar_gw=0.0)
        hour = anl.dispatch_hour(5.0, blocks, {"wind": 0.0, "solar": 0.0, "hydro_factor": 1.0})
        erzeugt = sum(hour["generation"].values())
        self.assertAlmostEqual(erzeugt, 5.0, delta=0.01)
        self.assertLess(hour["price"], 0.0)

    def test_je_enger_die_lage_desto_tiefer_der_preis(self):
        blocks = anl.merit_order(80.0, 32.0, wind_gw=100.0, solar_gw=0.0)
        available = {"wind": 50.0, "solar": 0.0, "hydro_factor": 1.0}
        eng = anl.dispatch_hour(5.0, blocks, available)
        locker = anl.dispatch_hour(40.0, blocks, available)
        self.assertLess(eng["price"], locker["price"])
        self.assertLess(eng["price"], 0.0)

    def test_ohne_speicher_wird_ueberschuss_abgeregelt(self):
        result = run(wind_gw=300.0, solar_gw=400.0, peak_load_gw=30.0)
        self.assertGreater(result["kpis"]["curtailed_gwh"], 0.0)


class SupplyCurveTest(unittest.TestCase):
    """Die Angebotskurve und die Preissuche darauf."""

    def setUp(self):
        self.blocks = anl.merit_order(80.0, 32.0, wind_gw=70.0, solar_gw=90.0)
        self.available = {"wind": 30.0, "solar": 20.0, "hydro_factor": 1.0}

    def test_angebot_waechst_mit_dem_preis(self):
        mengen = [anl.supply_at_price(self.blocks, p, self.available)
                  for p in range(-500, 400, 25)]
        self.assertEqual(mengen, sorted(mengen))

    def test_unter_dem_tiefsten_gebot_bietet_niemand(self):
        self.assertAlmostEqual(
            anl.supply_at_price(self.blocks, anl.PRICE_FLOOR - 1, self.available), 0.0)

    def test_ueber_dem_hoechsten_gebot_bietet_alles(self):
        alles = sum(anl.block_capacity(b, self.available) for b in self.blocks)
        self.assertAlmostEqual(
            anl.supply_at_price(self.blocks, 1000.0, self.available), alles, places=6)

    def test_geraeumter_preis_deckt_die_nachfrage_genau(self):
        for last in (10.0, 25.0, 50.0, 70.0):
            preis = anl.clear_market(self.blocks, last, self.available)
            angebot = anl.supply_at_price(self.blocks, preis, self.available)
            self.assertAlmostEqual(angebot, last, places=5, msg="Last %.0f" % last)

    def test_unerfuellbare_nachfrage_ergibt_den_knappheitspreis(self):
        self.assertEqual(anl.clear_market(self.blocks, 999.0, self.available),
                         anl.SCARCITY_PRICE)

    def test_bloecke_ohne_spanne_springen_und_werden_ausgeglichen(self):
        """Ein Block mit gleichem oberem und unterem Gebot bietet entweder ganz
        oder gar nicht. Dann bietet am Grenzpreis mehr an als gebraucht — die
        Erzeugung muss trotzdem genau die Nachfrage decken."""
        starr = [{"id": "starr", "name": "Starrer Block", "category": "erdgas",
                  "kind": "thermal", "capacity_gw": 40.0,
                  "cost_low": 50.0, "cost_high": 50.0, "cost": 50.0,
                  "emission": 0.0, "note": ""}]
        hour = anl.dispatch_hour(12.0, starr, {"wind": 0.0, "solar": 0.0, "hydro_factor": 1.0})
        self.assertAlmostEqual(sum(hour["generation"].values()), 12.0, delta=0.01)
        self.assertEqual(hour["unserved_gw"], 0.0)


class DispatchHourTest(unittest.TestCase):
    """Die einzelne Stunde für sich — ohne Zeitreihen drumherum."""

    def setUp(self):
        self.blocks = anl.merit_order(80.0, 32.0, wind_gw=60.0, solar_gw=50.0)
        self.available = {"wind": 20.0, "solar": 10.0, "hydro_factor": 1.0}

    def test_erzeugung_deckt_die_nachfrage(self):
        hour = anl.dispatch_hour(50.0, self.blocks, self.available)
        self.assertAlmostEqual(sum(hour["generation"].values()), 50.0, delta=0.01)
        self.assertEqual(hour["unserved_gw"], 0.0)

    def test_preis_liegt_in_einer_gebotsspanne(self):
        hour = anl.dispatch_hour(50.0, self.blocks, self.available)
        passend = any(b["cost_low"] - 0.01 <= hour["price"] <= b["cost_high"] + 0.01
                      for b in self.blocks)
        self.assertTrue(passend, "Preis %.2f" % hour["price"])

    def test_hoehere_nachfrage_hebt_den_preis(self):
        günstig = anl.dispatch_hour(35.0, self.blocks, self.available)["price"]
        teuer = anl.dispatch_hour(70.0, self.blocks, self.available)["price"]
        self.assertLess(günstig, teuer)

    def test_nicht_deckbare_last_wird_ausgewiesen(self):
        hour = anl.dispatch_hour(500.0, self.blocks, self.available)
        self.assertGreater(hour["unserved_gw"], 0.0)
        self.assertEqual(hour["price"], anl.SCARCITY_PRICE)

    def test_wetter_begrenzt_wind_und_sonne(self):
        hour = anl.dispatch_hour(200.0, self.blocks, self.available)
        self.assertAlmostEqual(hour["generation"]["wind"], 20.0, delta=0.01)
        self.assertAlmostEqual(hour["generation"]["solar"], 10.0, delta=0.01)

    def test_ungenutztes_wetter_gilt_als_abgeregelt(self):
        hour = anl.dispatch_hour(20.0, self.blocks, self.available)
        self.assertGreater(hour["curtailed_ee_gw"], 0.0)

    def test_ohne_nachfrage_laeuft_nur_das_noetigste(self):
        hour = anl.dispatch_hour(0.0, self.blocks, self.available)
        self.assertAlmostEqual(sum(hour["generation"].values()), 0.0, delta=0.01)


if __name__ == "__main__":
    unittest.main()
