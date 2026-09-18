"""Tests des Speichereinsatzes.

Der Speicher ist die erste Komponente, die Stunden miteinander verknüpft: Was
nachts eingespeichert wird, fehlt nachts und steht abends zur Verfügung.
Geprüft wird deshalb nicht nur, ob die Zahlen stimmen, sondern auch, dass keine
Energie aus dem Nichts entsteht.
"""

import unittest

from backend import analysis as anl


def unit(power_gw=10.0, energy_gwh=40.0, efficiency=0.9, var_om=0.0, uid="test"):
    return {"id": uid, "name": "Testspeicher", "category": "speicher",
            "power_gw": power_gw, "energy_gwh": energy_gwh,
            "efficiency": efficiency, "var_om": var_om}


# Ein Tag mit deutlichem Nacht-Tag-Unterschied, dreimal hintereinander.
SPREAD_PRICES = [10, 10, 10, 10, 10, 20, 50, 90, 120, 90, 50, 20,
                 10, 10, 20, 50, 100, 150, 180, 150, 100, 60, 30, 15] * 3


class PlanStorageTest(unittest.TestCase):
    def plan(self, prices=None, units=None):
        return anl.plan_storage(prices or SPREAD_PRICES, units or [unit()])

    def test_laedt_in_billigen_und_entlaedt_in_teuren_stunden(self):
        plan = self.plan()
        for hour, price in enumerate(SPREAD_PRICES):
            if plan["charge_gw"][hour] > 0:
                self.assertLessEqual(price, plan["cheap_threshold"], "Stunde %d" % hour)
            if plan["discharge_gw"][hour] > 0:
                self.assertGreaterEqual(price, plan["expensive_threshold"], "Stunde %d" % hour)

    def test_nie_gleichzeitig_laden_und_entladen(self):
        plan = self.plan()
        for hour in range(len(SPREAD_PRICES)):
            self.assertFalse(plan["charge_gw"][hour] > 0 and plan["discharge_gw"][hour] > 0)

    def test_leistung_wird_eingehalten(self):
        plan = self.plan(units=[unit(power_gw=5.0)])
        self.assertLessEqual(max(plan["charge_gw"]), 5.0 + 1e-9)
        self.assertLessEqual(max(plan["discharge_gw"]), 5.0 + 1e-9)

    def test_speicher_gibt_nie_mehr_ab_als_er_aufgenommen_hat(self):
        """Der Speicher startet leer — Energie entsteht nicht aus dem Nichts."""
        plan = self.plan()
        geladen = 0.0
        entladen = 0.0
        for hour in range(len(SPREAD_PRICES)):
            geladen += plan["charge_gw"][hour]
            entladen += plan["discharge_gw"][hour]
            self.assertLessEqual(entladen, geladen + 1e-6, "Stunde %d" % hour)

    def test_fuellstand_bleibt_zwischen_leer_und_voll(self):
        """Der schärfere Test: Nicht nur die Summen müssen stimmen, sondern
        jeder einzelne Zwischenstand. Sonst könnte der Speicher in einer Stunde
        mehr abgeben, als in ihm steckt, und es später wieder ausgleichen."""
        einheit = unit(power_gw=10.0, energy_gwh=40.0, efficiency=0.9)
        plan = anl.plan_storage(SPREAD_PRICES, [einheit])
        level = 0.0
        for hour in range(len(SPREAD_PRICES)):
            level += plan["charge_gw"][hour] * einheit["efficiency"]
            level -= plan["discharge_gw"][hour]
            self.assertGreaterEqual(level, -1e-6, "leergelaufen in Stunde %d" % hour)
            self.assertLessEqual(level, einheit["energy_gwh"] + 1e-6,
                                 "übergelaufen in Stunde %d" % hour)

    def test_entladung_nie_groesser_als_der_vorrat(self):
        einheit = unit(power_gw=30.0, energy_gwh=10.0, efficiency=0.9)
        plan = anl.plan_storage(SPREAD_PRICES, [einheit])
        level = 0.0
        for hour in range(len(SPREAD_PRICES)):
            self.assertLessEqual(plan["discharge_gw"][hour], level + 1e-6,
                                 "Stunde %d" % hour)
            level += plan["charge_gw"][hour] * einheit["efficiency"]
            level -= plan["discharge_gw"][hour]

    def test_gemeldeter_endstand_ist_nie_negativ(self):
        for bericht in self.plan()["units"]:
            if "final_level_gwh" in bericht:
                self.assertGreaterEqual(bericht["final_level_gwh"], 0.0)

    def test_wirkungsgrad_kostet_energie(self):
        plan = self.plan(units=[unit(efficiency=0.5)])
        geladen = sum(plan["charge_gw"])
        entladen = sum(plan["discharge_gw"])
        self.assertGreater(geladen, 0.0)
        self.assertLess(entladen, geladen)

    def test_besserer_wirkungsgrad_bringt_mehr_zurueck(self):
        schlecht = self.plan(units=[unit(efficiency=0.6, uid="a")])
        gut = self.plan(units=[unit(efficiency=0.95, uid="b")])
        self.assertGreater(sum(gut["discharge_gw"]), sum(schlecht["discharge_gw"]))

    def test_vorrat_begrenzt_den_einsatz(self):
        klein = self.plan(units=[unit(energy_gwh=5.0, uid="klein")])
        gross = self.plan(units=[unit(energy_gwh=80.0, uid="gross")])
        self.assertLess(sum(klein["discharge_gw"]), sum(gross["discharge_gw"]))

    def test_ruht_bei_flachem_preisverlauf(self):
        """Ohne Spreizung gäbe es sonst Stunden, die zugleich billig und teuer sind."""
        plan = anl.plan_storage([50.0] * 48, [unit()])
        self.assertEqual(sum(plan["charge_gw"]), 0.0)
        self.assertEqual(sum(plan["discharge_gw"]), 0.0)
        self.assertFalse(plan["units"][0]["used"])

    def test_ruht_auch_bei_durchgehend_negativen_preisen(self):
        plan = anl.plan_storage([-10.0] * 48, [unit()])
        self.assertEqual(sum(plan["discharge_gw"]), 0.0)

    def test_ruht_wenn_die_spanne_die_verluste_nicht_deckt(self):
        """Ein kleiner Preisabstand trägt einen Wirkungsgrad von 60 % nicht."""
        knapp = [50.0] * 24 + [56.0] * 24
        plan = anl.plan_storage(knapp, [unit(efficiency=0.6)])
        self.assertEqual(sum(plan["discharge_gw"]), 0.0)
        self.assertIn("Speicherverluste", plan["units"][0]["reason"])

    def test_meldet_zyklen_und_mengen_je_anlage(self):
        plan = self.plan()
        bericht = plan["units"][0]
        self.assertGreater(bericht["charged_gwh"], 0.0)
        self.assertGreater(bericht["discharged_gwh"], 0.0)
        self.assertGreater(bericht["cycles"], 0.0)
        self.assertTrue(bericht["used"])

    def test_mehrere_anlagen_arbeiten_nebeneinander(self):
        plan = anl.plan_storage(SPREAD_PRICES, [unit(uid="a"), unit(uid="b", power_gw=4.0)])
        self.assertEqual(len(plan["units"]), 2)
        self.assertLessEqual(max(plan["charge_gw"]), 14.0 + 1e-9)

    def test_ohne_anlagen_passiert_nichts(self):
        plan = anl.plan_storage(SPREAD_PRICES, [])
        self.assertEqual(sum(plan["charge_gw"]), 0.0)
        self.assertEqual(plan["units"], [])


class StorageInSimulationTest(unittest.TestCase):
    """Der Speicher im Zusammenspiel mit dem Kraftwerkseinsatz."""

    def run_case(self, **overrides):
        params = {"peak_load_gw": 60.0, "wind_gw": 70.0, "solar_gw": 90.0,
                  "hours": 168, "season": "winter"}
        params.update(overrides)
        return anl.simulate(**params)

    def test_energiebilanz_beruecksichtigt_das_laden(self):
        """Erzeugt werden muss die Last plus das, was in die Speicher geht."""
        result = self.run_case(wind_gw=250.0, solar_gw=300.0)
        for hour in range(len(result["demand_gw"])):
            erzeugt = sum(series[hour] for series in result["generation_gw"].values())
            gebraucht = result["demand_gw"][hour] + result["storage_charge_gw"][hour]
            self.assertAlmostEqual(erzeugt, gebraucht, delta=0.02, msg="Stunde %d" % hour)

    def test_entladung_erscheint_als_erzeugung(self):
        result = self.run_case(wind_gw=250.0, solar_gw=300.0)
        for hour, value in enumerate(result["storage_discharge_gw"]):
            self.assertAlmostEqual(result["generation_gw"]["speicher"][hour], value,
                                   delta=0.01, msg="Stunde %d" % hour)

    def test_verluste_werden_ausgewiesen(self):
        kpis = self.run_case(wind_gw=250.0, solar_gw=300.0)["kpis"]
        if kpis["storage_charged_gwh"] > 0:
            self.assertGreater(kpis["storage_losses_gwh"], 0.0)
            self.assertAlmostEqual(
                kpis["storage_losses_gwh"],
                kpis["storage_charged_gwh"] - kpis["storage_discharged_gwh"], places=1)

    def test_speicher_meldet_seinen_zustand(self):
        result = self.run_case()
        self.assertIn("units", result["storage"])
        self.assertEqual(len(result["storage"]["units"]), len(anl.STORAGE_UNITS))

    def test_bei_geringer_preisspreizung_bleibt_der_speicher_stehen(self):
        """Mit dem heutigen Kraftwerkspark ist die Spreizung oft zu klein.

        Das ist kein Fehler, sondern die Aussage: Arbitrage lohnt sich erst,
        wenn die Preise weit genug auseinanderliegen.
        """
        result = self.run_case(wind_gw=40.0, solar_gw=40.0)
        spreizung = (result["storage"]["expensive_threshold"]
                     - result["storage"]["cheap_threshold"])
        if spreizung < 15.0:
            self.assertEqual(result["kpis"]["storage_discharged_gwh"], 0.0)

    def test_zubau_bringt_den_speicher_in_betrieb(self):
        """Erst wenn billige und teure Stunden auseinanderfallen, lohnt Arbitrage."""
        ruhig = self.run_case(wind_gw=40.0, solar_gw=40.0)["kpis"]["storage_discharged_gwh"]
        bewegt = self.run_case(wind_gw=100.0, solar_gw=120.0)["kpis"]["storage_discharged_gwh"]
        self.assertEqual(ruhig, 0.0)
        self.assertGreater(bewegt, 0.0)

    def test_arbeitet_auch_im_dauerueberschuss(self):
        """Seit die Blöcke in Spannen bieten, gibt es selbst bei durchgehend
        negativen Preisen noch eine Spreizung — und damit etwas zu verdienen."""
        result = self.run_case(wind_gw=300.0, solar_gw=400.0, hours=336)
        self.assertGreater(result["kpis"]["storage_discharged_gwh"], 0.0)
        self.assertLess(result["storage"]["cheap_threshold"],
                        result["storage"]["expensive_threshold"])


class PriceThresholdTest(unittest.TestCase):
    """Die Schwellen müssen auch bei schiefen Preisverteilungen etwas finden."""

    def test_gleichmaessige_verteilung_nutzt_die_engen_quantile(self):
        cheap, expensive = anl.price_thresholds(list(range(100)))
        self.assertLess(cheap, expensive)
        self.assertGreater(expensive - cheap, 20)

    def test_schiefe_verteilung_macht_weiter_auf(self):
        """Zwei Drittel Überschussstunden dürfen die teuren nicht unsichtbar machen."""
        prices = [-10.0] * 70 + [90.0] * 30
        cheap, expensive = anl.price_thresholds(prices)
        self.assertEqual(cheap, -10.0)
        self.assertEqual(expensive, 90.0)

    def test_flacher_verlauf_ergibt_keine_spreizung(self):
        cheap, expensive = anl.price_thresholds([42.0] * 50)
        self.assertEqual(cheap, expensive)

    def test_leere_reihe_stuerzt_nicht_ab(self):
        self.assertEqual(anl.price_thresholds([]), (0.0, 0.0))


if __name__ == "__main__":
    unittest.main()
