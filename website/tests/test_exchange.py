"""Außenhandel als preisabhängige Nachfrage.

Der erste Anlauf hat den gemessenen Nettoexport als feste Zusatznachfrage
eingesetzt und das Modell dadurch verschlechtert — der Export ist die Folge
niedriger Preise, nicht ihre Ursache. Diese Tests halten die Richtung fest,
damit dieser Fehler nicht zurückkommt.
"""

import unittest

from backend import analysis as anl


class TestHandelskurve(unittest.TestCase):

    def test_export_faellt_monoton_mit_dem_preis(self):
        preise = [-200, -60, -20, 0, 20, 50, 80, 120, 200, 400]
        werte = [anl.exchange_at_price(p) for p in preise]
        for vorher, nachher in zip(werte, werte[1:]):
            self.assertLessEqual(
                nachher, vorher + 1e-9,
                "Bei höherem Preis darf nicht mehr exportiert werden: %r" % (werte,))

    def test_billiger_strom_wird_ausgefuehrt_teurer_eingefuehrt(self):
        self.assertGreater(anl.exchange_at_price(-20), 5.0)
        self.assertLess(anl.exchange_at_price(150), -3.0)

    def test_kuppelleistung_begrenzt_beide_richtungen(self):
        grenze_aus = anl.EXCHANGE["max_export_gw"]
        grenze_ein = anl.EXCHANGE["max_import_gw"]
        self.assertLessEqual(anl.exchange_at_price(anl.PRICE_FLOOR), grenze_aus)
        self.assertGreaterEqual(anl.exchange_at_price(anl.SCARCITY_PRICE), -grenze_ein)

    def test_zwischen_stuetzstellen_wird_interpoliert(self):
        (p0, v0), (p1, v1) = anl.EXCHANGE["curve"][2], anl.EXCHANGE["curve"][3]
        mitte = anl.exchange_at_price((p0 + p1) / 2)
        self.assertAlmostEqual(mitte, (v0 + v1) / 2, places=6)

    def test_ohne_kurve_kein_handel(self):
        original = anl.EXCHANGE.get("curve")
        anl.EXCHANGE["curve"] = None
        try:
            self.assertEqual(anl.exchange_at_price(50), 0.0)
        finally:
            anl.EXCHANGE["curve"] = original


class TestHandelImMarkt(unittest.TestCase):
    """Die Kurve muss den Räumungspreis in die richtige Richtung schieben."""

    def setUp(self):
        self.blocks = anl.merit_order(80.0, 32.0, wind_gw=40.0, solar_gw=60.0)
        self.available = {"wind": 5.0, "solar": 0.0, "hydro_factor": 1.0}

    def test_handel_hebt_den_preis_im_ueberschuss(self):
        """Viel Wind, wenig Nachfrage: Der Export saugt den Überschuss ab."""
        reichlich = {"wind": 55.0, "solar": 40.0, "hydro_factor": 1.0}
        ohne = anl.clear_market(self.blocks, 30.0, reichlich, trade=False)
        mit = anl.clear_market(self.blocks, 30.0, reichlich, trade=True)
        self.assertGreater(mit, ohne)

    def test_handel_senkt_den_preis_in_der_knappheit(self):
        """Kaum Wind, hohe Nachfrage: Der Import entlastet den Park."""
        ohne = anl.clear_market(self.blocks, 68.0, self.available, trade=False)
        mit = anl.clear_market(self.blocks, 68.0, self.available, trade=True)
        self.assertLess(mit, ohne)

    def test_geraeumter_preis_passt_zum_gehandelten_umfang(self):
        """Preis und Handelsmenge müssen zueinander passen, nicht bloß nebeneinander
        stehen — sonst räumte der Markt eine andere Menge, als am Ende fließt."""
        stunde = anl.dispatch_hour(55.0, self.blocks, self.available, trade=True)
        erwartet = anl.exchange_at_price(stunde["price"])
        self.assertAlmostEqual(stunde["net_export_gw"], round(erwartet, 3), places=3)


class TestKnappheitsaufschlag(unittest.TestCase):

    def test_bei_reichlich_reserve_kein_aufschlag(self):
        self.assertEqual(anl.scarcity_markup(50.0, 100.0), 0.0)

    def test_aufschlag_waechst_mit_der_enge(self):
        werte = [anl.scarcity_markup(d, 100.0) for d in (50, 60, 70, 80, 90, 100)]
        for vorher, nachher in zip(werte, werte[1:]):
            self.assertGreaterEqual(nachher, vorher)
        self.assertGreater(werte[-1], werte[0])

    def test_ohne_leistung_gilt_der_hoechste_aufschlag(self):
        self.assertEqual(anl.scarcity_markup(10.0, 0.0), anl.SCARCITY_MARKUP_MAX)

    def test_aufschlag_hebt_nur_positive_preise(self):
        """Auf einen negativen Preis gehört kein Knappheitsaufschlag — dort ist
        zu viel Leistung da, nicht zu wenig."""
        blocks = anl.merit_order(80.0, 32.0, wind_gw=40.0, solar_gw=60.0)
        ueberschuss = {"wind": 40.0, "solar": 55.0, "hydro_factor": 1.0}
        stunde = anl.dispatch_hour(20.0, blocks, ueberschuss)
        self.assertLess(stunde["price"], 0.0)
        self.assertEqual(stunde["scarcity_markup"], 0.0)


class TestMindestlast(unittest.TestCase):
    """Standardmäßig ist die Mindestlast aus; hier wird sie ausdrücklich
    eingeschaltet. Warum sie aus ist, steht in analysis.py bei MIN_LOAD_DEFAULT."""

    def test_standardmaessig_ist_sie_aus(self):
        blocks = anl.merit_order(80.0, 32.0)
        self.assertFalse([b for b in blocks if b["id"].endswith("_mindestlast")])
        self.assertFalse(anl.DEFAULTS["min_load"])

    def test_mindestlast_wird_als_eigener_block_gefuehrt(self):
        blocks = anl.merit_order(80.0, 32.0, min_load=True)
        floors = [b for b in blocks if b["id"].endswith("_mindestlast")]
        self.assertTrue(floors, "Kein Mindestlastblock in der Merit-Order")
        for block in floors:
            self.assertLess(block["cost_low"], 0.0,
                            "Mindestlast muss unter null bieten: %s" % block["id"])

    def test_mindestlast_vergroessert_den_park_nicht(self):
        """Der Mindestlastteil wird vom Block abgezogen, nicht zusätzlich
        angehängt — sonst stünde plötzlich mehr Leistung im Markt."""
        blocks = anl.merit_order(80.0, 32.0, min_load=True)
        for plant in anl.FLEET["plants"]:
            teile = [b for b in blocks
                     if b["id"] == plant["id"] or b["id"] == plant["id"] + "_mindestlast"]
            self.assertAlmostEqual(sum(b["capacity_gw"] for b in teile),
                                   plant["capacity_gw"], places=3,
                                   msg="Leistung von %s stimmt nicht" % plant["id"])

    def test_mindestlast_laeuft_auch_bei_negativem_preis(self):
        """Bei mäßigem Überschuss bleibt die Kohle im Markt, statt abzufahren."""
        blocks = anl.merit_order(80.0, 32.0, wind_gw=40.0, solar_gw=60.0, min_load=True)
        ueberschuss = {"wind": 38.0, "solar": 50.0, "hydro_factor": 1.0}
        stunde = anl.dispatch_hour(65.0, blocks, ueberschuss)
        self.assertLess(stunde["price"], 0.0)
        self.assertGreater(stunde["generation"].get("braunkohle", 0.0), 0.0,
                           "Braunkohle muss bei negativem Preis ihre Mindestlast fahren")

    def test_bei_tiefem_ueberschuss_faehrt_auch_die_mindestlast_ab(self):
        """Irgendwann lohnt auch das Durchhalten nicht mehr. Ohne diese Grenze
        liefe die Kohle bis minus fünfhundert Euro weiter."""
        blocks = anl.merit_order(80.0, 32.0, wind_gw=40.0, solar_gw=60.0, min_load=True)
        ueberschuss = {"wind": 38.0, "solar": 50.0, "hydro_factor": 1.0}
        stunde = anl.dispatch_hour(30.0, blocks, ueberschuss)
        self.assertLess(stunde["price"], anl.MIN_LOAD_BID)
        self.assertEqual(stunde["generation"].get("braunkohle", 0.0), 0.0)


if __name__ == "__main__":
    unittest.main()
