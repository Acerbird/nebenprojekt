"""Tests der HTTP-Schicht: Seiten, Endpunkte, Fehlerverhalten.

Getestet wird hier nicht das Modell (das tun test_dispatch und test_merit_order),
sondern der Vertrag nach außen: Welche Antwort kommt bei welcher Anfrage, was
passiert mit unsinnigen Parametern, und liefert derselbe Link wirklich immer
dasselbe Szenario.

Braucht FastAPI und httpx. Fehlen sie, überspringt sich die Datei — die
Modelltests laufen weiter ohne Installation:

    ./backend/.venv/bin/pip install -r backend/requirements-dev.txt
"""

import unittest

try:
    from fastapi.testclient import TestClient
    from backend.main import app, NAV
    from backend import analysis as anl
    DEPENDENCIES_MISSING = ""
except ImportError as exc:  # pragma: no cover - hängt an der Umgebung
    DEPENDENCIES_MISSING = str(exc)


def setUpModule():
    if DEPENDENCIES_MISSING:
        raise unittest.SkipTest(
            "HTTP-Tests übersprungen (%s) — Abhängigkeiten installieren mit: "
            "backend/.venv/bin/pip install -r backend/requirements-dev.txt"
            % DEPENDENCIES_MISSING)


PAGES = ["/", "/stromsystem", "/erneuerbare", "/analysen", "/glossar"]


class ApiTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def get(self, path, **kwargs):
        return self.client.get(path, **kwargs)

    def json(self, path, **kwargs):
        response = self.get(path, **kwargs)
        self.assertEqual(response.status_code, 200, path)
        return response.json()


class PageTest(ApiTestCase):
    def test_alle_seiten_liefern_html(self):
        for path in PAGES:
            response = self.get(path)
            self.assertEqual(response.status_code, 200, path)
            self.assertIn("text/html", response.headers["content-type"], path)
            self.assertIn("<!doctype html>", response.text.lower(), path)

    def test_jede_seite_zeigt_die_vollstaendige_navigation(self):
        for path in PAGES:
            body = self.get(path).text
            for item in NAV:
                self.assertIn('href="%s"' % item["href"], body,
                              "%s fehlt in %s" % (item["href"], path))

    def test_aktive_seite_ist_fuer_screenreader_markiert(self):
        for item in NAV:
            body = self.get(item["href"]).text
            self.assertEqual(body.count('aria-current="page"'), 1, item["href"])
            marker = '<a href="%s" aria-current="page">' % item["href"]
            self.assertIn(marker, body, item["href"])

    def test_jede_seite_hat_titel_und_beschreibung(self):
        for path in PAGES:
            body = self.get(path).text
            self.assertIn("— Energiewende</title>", body, path)
            self.assertIn('<meta name="description"', body, path)

    def test_unbekannte_seite_liefert_404(self):
        self.assertEqual(self.get("/gibtesnicht").status_code, 404)


class SimulateEndpointTest(ApiTestCase):
    def test_antwort_ist_vollstaendig(self):
        data = self.json("/api/simulate")
        for key in ("timestamps", "demand_gw", "residual_load_gw", "generation_gw",
                    "curtailed_gw", "price_eur_mwh", "available_gw", "categories",
                    "kpis", "params", "season_label"):
            self.assertIn(key, data)

    def test_alle_reihen_haben_die_angeforderte_laenge(self):
        data = self.json("/api/simulate?hours=48")
        self.assertEqual(data["params"]["hours"], 48)
        for key in ("timestamps", "demand_gw", "residual_load_gw",
                    "curtailed_gw", "price_eur_mwh"):
            self.assertEqual(len(data[key]), 48, key)
        for category, series in data["generation_gw"].items():
            self.assertEqual(len(series), 48, category)

    def test_parameter_kommen_im_modell_an(self):
        data = self.json("/api/simulate?wind_gw=123&solar_gw=45&co2_price=99"
                         "&gas_price=60&peak_load_gw=88&season=sommer")
        params = data["params"]
        self.assertEqual(params["wind_gw"], 123.0)
        self.assertEqual(params["solar_gw"], 45.0)
        self.assertEqual(params["co2_price"], 99.0)
        self.assertEqual(params["gas_price"], 60.0)
        self.assertEqual(params["peak_load_gw"], 88.0)
        self.assertEqual(params["season"], "sommer")
        self.assertEqual(data["season_label"], "Sommer")

    def test_werte_ausserhalb_des_bereichs_werden_geklemmt_nicht_abgelehnt(self):
        """Zusage aus dem README: Die API rechnet immer, statt Fehler zu werfen."""
        response = self.get("/api/simulate?wind_gw=99999&co2_price=-500&hours=1")
        self.assertEqual(response.status_code, 200)
        params = response.json()["params"]
        self.assertEqual(params["wind_gw"], 300.0)
        self.assertEqual(params["co2_price"], 0.0)
        self.assertEqual(params["hours"], 24)

    def test_unbekannte_jahreszeit_faellt_auf_den_standard(self):
        data = self.json("/api/simulate?season=fruehling")
        self.assertEqual(data["params"]["season"], "winter")

    def test_text_statt_zahl_wird_abgelehnt(self):
        """Grenze der Zusage: Geklemmt wird der Zahlenbereich, nicht der Typ."""
        response = self.get("/api/simulate?wind_gw=ordentlich%20viel")
        self.assertEqual(response.status_code, 422)

    def test_ohne_parameter_gelten_die_standardwerte(self):
        params = self.json("/api/simulate")["params"]
        for key, expected in anl.DEFAULTS.items():
            self.assertEqual(params[key], expected, key)

    def test_gleiche_anfrage_liefert_dieselbe_antwort(self):
        """Ein geteilter Szenario-Link muss bei jedem beim Gleichen landen."""
        query = "/api/simulate?wind_gw=150&solar_gw=200&co2_price=120&hours=96&season=sommer"
        self.assertEqual(self.json(query), self.json(query))

    def test_kennzahlen_sind_in_sich_stimmig(self):
        kpis = self.json("/api/simulate?hours=72")["kpis"]
        self.assertLessEqual(kpis["min_price"], kpis["max_price"])
        self.assertGreaterEqual(kpis["renewable_share"], 0.0)
        self.assertLessEqual(kpis["renewable_share"], 100.0)
        self.assertGreaterEqual(kpis["curtailed_gwh"], 0.0)
        self.assertGreaterEqual(kpis["emissions_kt"], 0.0)


class MeritOrderEndpointTest(ApiTestCase):
    def test_bloecke_sind_nach_beginn_der_gebotsspanne_sortiert(self):
        blocks = self.json("/api/merit-order")["blocks"]
        starts = [b["cost_low"] for b in blocks]
        self.assertEqual(starts, sorted(starts))

    def test_jeder_block_liefert_seine_gebotsspanne(self):
        for block in self.json("/api/merit-order")["blocks"]:
            self.assertIn("cost_low", block)
            self.assertIn("cost_high", block)
            self.assertLessEqual(block["cost_low"], block["cost_high"], block["id"])

    def test_jeder_block_hat_die_felder_fuer_die_darstellung(self):
        for block in self.json("/api/merit-order")["blocks"]:
            for key in ("id", "name", "category", "cost", "cost_low", "cost_high",
                        "capacity_gw", "from_gw", "to_gw", "emission", "note"):
                self.assertIn(key, block, block.get("id"))

    def test_co2_preis_verschiebt_die_reihenfolge(self):
        def order(co2):
            return [b["id"] for b in
                    self.json("/api/merit-order?co2_price=%s" % co2)["blocks"]]
        self.assertNotEqual(order(0), order(200))

    def test_zubau_erscheint_als_kapazitaet(self):
        blocks = self.json("/api/merit-order?wind_gw=200&solar_gw=250")["blocks"]
        by_id = {b["id"]: b for b in blocks}
        self.assertEqual(by_id["wind"]["capacity_gw"], 200.0)
        self.assertEqual(by_id["solar"]["capacity_gw"], 250.0)


class ProfilesEndpointTest(ApiTestCase):
    def test_liefert_einen_vollen_tag(self):
        data = self.json("/api/profiles/day")
        self.assertEqual(data["hours"], list(range(24)))
        self.assertEqual(len(data["load"]["werktag"]), 24)
        self.assertEqual(len(data["load"]["wochenende"]), 24)

    def test_kennt_alle_jahreszeiten(self):
        data = self.json("/api/profiles/day")
        self.assertEqual(set(data["solar_cf"]), set(data["seasons"]))
        for season, series in data["solar_cf"].items():
            self.assertEqual(len(series), 24, season)

    def test_jahreszeit_wird_uebernommen_und_notfalls_ersetzt(self):
        self.assertEqual(self.json("/api/profiles/day?season=sommer")["selected_season"],
                         "sommer")
        self.assertEqual(self.json("/api/profiles/day?season=fruehling")["selected_season"],
                         "winter")


class GlossaryEndpointTest(ApiTestCase):
    def setUp(self):
        self.entries = self.json("/api/glossary")

    def test_liefert_eintraege(self):
        self.assertGreaterEqual(len(self.entries), 20)

    def test_jeder_eintrag_ist_vollstaendig(self):
        for entry in self.entries:
            self.assertTrue(entry.get("term"), entry)
            self.assertTrue(entry.get("definition"), entry.get("term"))
            self.assertTrue(entry.get("group"), entry.get("term"))

    def test_begriffe_sind_eindeutig(self):
        terms = [e["term"] for e in self.entries]
        self.assertEqual(len(terms), len(set(terms)))

    def test_querverweise_zeigen_auf_vorhandene_begriffe(self):
        """Ein Verweis ins Leere wäre auf der Glossarseite ein toter Link."""
        terms = {e["term"] for e in self.entries}
        for entry in self.entries:
            for reference in entry.get("see_also", []):
                self.assertIn(reference, terms,
                              "%s verweist auf unbekannten Begriff" % entry["term"])

    def test_seite_zeigt_dieselben_begriffe_wie_die_api(self):
        body = self.get("/glossar").text
        for entry in self.entries:
            self.assertIn(entry["term"], body, entry["term"])


class HealthAndDocsTest(ApiTestCase):
    def test_health_meldet_status_und_version(self):
        data = self.json("/health")
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["version"], app.version)

    def test_openapi_kennt_alle_endpunkte(self):
        paths = self.json("/openapi.json")["paths"]
        for path in ("/api/simulate", "/api/merit-order", "/api/profiles/day",
                     "/api/glossary", "/health"):
            self.assertIn(path, paths)

    def test_dokumentationsseite_ist_erreichbar(self):
        self.assertEqual(self.get("/docs").status_code, 200)


class StaticFilesTest(ApiTestCase):
    def test_stylesheet_und_skripte_werden_ausgeliefert(self):
        css = self.get("/static/css/style.css")
        self.assertEqual(css.status_code, 200)
        self.assertIn("css", css.headers["content-type"])
        for name in ("main.js", "charts.js", "analysis.js", "explain.js", "glossary.js"):
            response = self.get("/static/js/" + name)
            self.assertEqual(response.status_code, 200, name)
            self.assertIn("javascript", response.headers["content-type"], name)

    def test_unbekannte_datei_liefert_404(self):
        self.assertEqual(self.get("/static/js/gibtesnicht.js").status_code, 404)


if __name__ == "__main__":
    unittest.main()


class DataEndpointTestCase(ApiTestCase):
    """Endpunkte rund um die echten Messwerte.

    Läuft gegen eine künstliche Datenbank, nicht gegen den lokal abgerufenen
    Bestand — sonst hinge das Ergebnis davon ab, wann zuletzt ingest lief.
    """

    HOURS = 96

    def setUp(self):
        import os
        import tempfile
        from datetime import datetime, timezone
        from unittest import mock

        from backend.data import store

        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        path = os.path.join(self.directory.name, "api.sqlite3")

        start = int(datetime(2024, 6, 3, tzinfo=timezone.utc).timestamp())
        self.start_ts = start
        stamps = [start + h * 3600 for h in range(self.HOURS)]
        self.stamps = stamps
        with store.open_db(path) as conn:
            store.write_observations(conn, "load", [(t, 50000.0) for t in stamps])
            store.write_observations(conn, "wind_onshore", [(t, 20000.0) for t in stamps])
            store.write_observations(conn, "wind_offshore", [(t, 4000.0) for t in stamps])
            store.write_observations(conn, "solar", [(t, 10000.0) for t in stamps])

        patcher = mock.patch.object(store, "DEFAULT_PATH", path)
        patcher.start()
        self.addCleanup(patcher.stop)


class DataStatusTest(DataEndpointTestCase):
    def test_meldet_vorhandenen_bestand(self):
        data = self.json("/api/data/status")
        self.assertTrue(data["available"])
        self.assertIn("SMARD", data["source"])
        self.assertEqual(data["range"]["first_ts"], self.stamps[0])
        self.assertEqual(data["range"]["last_ts"], self.stamps[-1])

    def test_nennt_die_zeitreihen_mit_klarnamen(self):
        series = self.json("/api/data/status")["series"]
        self.assertIn("load", series)
        self.assertIn("Netzlast", series["load"]["label"])


class SimulateHistoricalTest(DataEndpointTestCase):
    def test_echte_daten_werden_als_solche_gekennzeichnet(self):
        data = self.json("/api/simulate?source=historical&hours=48")
        self.assertEqual(data["source"], "historical")
        self.assertIn("SMARD", data["series_meta"]["data_source"])

    def test_ohne_startdatum_kommt_der_juengste_zeitraum(self):
        data = self.json("/api/simulate?source=historical&hours=24")
        letzte_stunde = self.stamps[-1]
        self.assertEqual(data["params"]["start_ts"], letzte_stunde - 23 * 3600)

    def test_startdatum_wird_uebernommen(self):
        data = self.json("/api/simulate?source=historical&start=2024-06-04&hours=24")
        self.assertEqual(data["params"]["start_ts"], self.start_ts + 24 * 3600)

    def test_zeitraum_vor_dem_bestand_wird_sichtbar_verschoben(self):
        """Wer 2019 anfragt und 2024 bekommt, muss das erfahren."""
        data = self.json("/api/simulate?source=historical&start=2019-01-01&hours=24")
        self.assertIn("start_shifted_to", data["params"]["adjustments"])

    def test_zu_langer_zeitraum_wird_sichtbar_gekuerzt(self):
        data = self.json("/api/simulate?source=historical&start=2024-06-06&hours=336")
        self.assertIn("hours_shortened_to", data["params"]["adjustments"])
        self.assertEqual(len(data["price_eur_mwh"]), data["params"]["hours"])

    def test_normalfall_meldet_keine_anpassung(self):
        data = self.json("/api/simulate?source=historical&start=2024-06-04&hours=24")
        self.assertEqual(data["params"]["adjustments"], {})

    def test_echte_last_gilt_ohne_vorgabe_einer_hoechstlast(self):
        data = self.json("/api/simulate?source=historical&hours=24")
        self.assertAlmostEqual(max(data["demand_gw"]), 50.0, delta=0.01)

    def test_hoechstlast_streckt_die_echte_kurve(self):
        data = self.json("/api/simulate?source=historical&hours=24&peak_load_gw=90")
        self.assertAlmostEqual(max(data["demand_gw"]), 90.0, delta=0.01)
        self.assertIn("load_scaled_by", data["series_meta"])

    def test_zubau_wirkt_auf_echtes_wetter(self):
        wenig = self.json("/api/simulate?source=historical&hours=48&wind_gw=20&solar_gw=20")
        viel = self.json("/api/simulate?source=historical&hours=48&wind_gw=200&solar_gw=200")
        self.assertGreater(viel["kpis"]["renewable_share"], wenig["kpis"]["renewable_share"])
        self.assertLess(viel["kpis"]["emissions_kt"], wenig["kpis"]["emissions_kt"])

    def test_ohne_preisangabe_gelten_die_marktpreise_des_monats(self):
        """Der Durchstich, der beinahe schiefgegangen wäre: Solange die API die
        Regler mit ihren Vorgabewerten füllt, sind sie von einer Eingabe nicht
        zu unterscheiden — und die gemessenen Monatspreise kämen nie zum Zug."""
        from backend.data import fuel_prices
        if not fuel_prices.available():
            self.skipTest("Keine Brennstofftabelle vorhanden")
        data = self.json("/api/simulate?source=historical&start=2024-06-04&hours=24")
        herkunft = [m["origin"] for m in data["fuel_costs"]["months"].values()]
        self.assertIn("historical", herkunft)

    def test_angegebener_preis_schlaegt_den_marktpreis(self):
        data = self.json("/api/simulate?source=historical&start=2024-06-04&hours=24&co2_price=150")
        monate = list(data["fuel_costs"]["months"].values())
        self.assertTrue(monate)
        self.assertEqual(monate[0]["co2_price"], 150.0)

    def test_ohne_reglerangabe_gilt_der_tatsaechliche_ausbau(self):
        """Derselbe Durchstich wie bei den Brennstoffpreisen: Füllt die API die
        Regler mit ihren Vorgabewerten, ist das von einer Eingabe nicht zu
        unterscheiden — und der echte Ausbaustand käme über die Website nie an."""
        data = self.json("/api/simulate?source=historical&start=2024-06-04&hours=24")
        self.assertEqual(data["params"]["capacity_source"], "historical")
        echt = data["series_meta"]["installed_gw"]
        self.assertAlmostEqual(data["params"]["solar_gw"], echt["solar"], places=1)
        self.assertNotAlmostEqual(data["params"]["solar_gw"], anl.DEFAULTS["solar_gw"], places=0)

    def test_angegebener_ausbau_schlaegt_den_tatsaechlichen(self):
        data = self.json("/api/simulate?source=historical&start=2024-06-04&hours=24&wind_gw=140")
        self.assertEqual(data["params"]["wind_gw"], 140.0)
        echt = data["series_meta"]["installed_gw"]
        self.assertAlmostEqual(data["params"]["solar_gw"], echt["solar"], places=1,
                               msg="Ein gesetzter Regler darf den anderen nicht verdrängen")

    def test_hoher_co2_preis_treibt_den_strompreis(self):
        billig = self.json("/api/simulate?source=historical&start=2024-06-04&hours=48&co2_price=10")
        teuer = self.json("/api/simulate?source=historical&start=2024-06-04&hours=48&co2_price=250")
        self.assertGreater(teuer["kpis"]["mean_price"], billig["kpis"]["mean_price"])

    def test_mindestlast_ist_abschaltbar_und_standardmaessig_aus(self):
        aus = self.json("/api/simulate?source=historical&start=2024-06-04&hours=24")
        an = self.json("/api/simulate?source=historical&start=2024-06-04&hours=24&min_load=true")
        self.assertFalse(aus["params"]["min_load"])
        self.assertTrue(an["params"]["min_load"])

    def test_aussenhandel_wird_ausgewiesen(self):
        data = self.json("/api/simulate?source=historical&start=2024-06-04&hours=24")
        self.assertEqual(len(data["net_export_gw"]), data["params"]["hours"])
        self.assertIn("net_export_gwh", data["kpis"])

    def test_unbekannte_quelle_faellt_auf_erzeugte_profile_zurueck(self):
        data = self.json("/api/simulate?source=phantasie")
        self.assertEqual(data["source"], "synthetic")

    def test_erzeugte_profile_bleiben_unveraendert_erreichbar(self):
        data = self.json("/api/simulate?source=synthetic&hours=24")
        self.assertEqual(data["source"], "synthetic")
        self.assertEqual(data["params"]["start_ts"], None)


class SimulateWithoutDataTest(ApiTestCase):
    """Ohne Datenbank muss die Anfrage klar scheitern, nicht still etwas anderes liefern."""

    def setUp(self):
        import os
        import tempfile
        from unittest import mock

        from backend.data import store

        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        patcher = mock.patch.object(
            store, "DEFAULT_PATH", os.path.join(self.directory.name, "leer.sqlite3"))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_historische_anfrage_meldet_fehlende_daten(self):
        response = self.get("/api/simulate?source=historical&hours=24")
        self.assertEqual(response.status_code, 409)
        body = response.json()
        self.assertEqual(body["error"], "insufficient_data")
        self.assertIn("ingest", body["hint"])

    def test_status_meldet_leeren_bestand_mit_hinweis(self):
        data = self.json("/api/data/status")
        self.assertFalse(data["available"])
        self.assertIn("ingest", data["hint"])

    def test_erzeugte_profile_funktionieren_weiterhin(self):
        data = self.json("/api/simulate?hours=24")
        self.assertEqual(data["source"], "synthetic")


class AnalysisFormTest(ApiTestCase):
    """Die Analyseseite muss die Wahl der Datengrundlage anbieten."""

    def setUp(self):
        self.body = self.get("/analysen").text

    def test_datengrundlage_ist_waehlbar(self):
        self.assertIn('id="source"', self.body)
        self.assertIn('value="synthetic"', self.body)
        self.assertIn('value="historical"', self.body)

    def test_zeitraum_und_jahreszeit_haengen_an_der_quelle(self):
        """Jahreszeit gibt es nur bei erzeugten Profilen, ein Startdatum nur bei Messwerten."""
        self.assertIn('data-when="historical"', self.body)
        self.assertIn('data-when="synthetic"', self.body)

    def test_startdatum_ist_ein_datumsfeld(self):
        self.assertIn('type="date"', self.body)
        self.assertIn('id="start"', self.body)

    def test_lastanpassung_ist_ausdruecklich_zu_bestaetigen(self):
        """Sonst hielte man eine gestreckte Kurve für eine gemessene."""
        self.assertIn('id="scale_load"', self.body)

    def test_platz_fuer_die_herkunftsangabe_ist_vorgesehen(self):
        self.assertIn('id="data-note"', self.body)

    def test_seite_nennt_smard_als_quelle(self):
        self.assertIn("SMARD", self.body)


class StoriesApiTest(ApiTestCase):
    def test_endpunkt_liefert_die_geschichten(self):
        data = self.json("/api/stories")
        self.assertGreaterEqual(len(data), 3)
        self.assertIn("steps", data[0])

    def test_seite_bringt_platz_fuer_geschichten_und_vergleich(self):
        html = self.client.get("/analysen").text
        for marke in ('id="story-list"', 'id="story-panel"',
                      'id="pin-scenario"', 'id="diff-box"', 'id="unpin-scenario"'):
            self.assertIn(marke, html, marke)

    def test_die_neuen_module_werden_geladen(self):
        """Sie hängen an analysis.js — fehlt dort der Import, bleibt die Seite stumm."""
        quelle = self.client.get("/static/js/analysis.js").text
        self.assertIn("./stories.js", quelle)
        self.assertIn("./compare.js", quelle)


class StylesheetTest(ApiTestCase):
    def test_die_vergleichsfarbe_ist_in_beiden_modi_gesetzt(self):
        """Ohne Token fiele die gemerkte Kurve auf eine Ersatzfarbe zurück, die
        neben den Vorhersagekurven nicht mehr zu unterscheiden wäre."""
        css = self.client.get("/static/css/style.css").text
        self.assertGreaterEqual(css.count("--s-pinned"), 3)

    def test_ausgeblendete_felder_werden_wirklich_versteckt(self):
        """`.field` setzt display:flex und würde das hidden-Attribut sonst überstimmen."""
        css = self.get("/static/css/style.css").text
        self.assertIn("[hidden]", css)
        self.assertIn("display: none !important", css)


class TimezoneContractTest(ApiTestCase):
    """Zeitstempel sind UTC, die Anzeigezone reist als eigene Angabe mit.

    Das ist die Voraussetzung dafür, dass später weitere Länder danebenstehen
    können, ohne dass Zeitpunkte mehrdeutig werden.
    """

    def test_simulate_liefert_utc_stempel(self):
        data = self.json("/api/simulate?hours=24")
        for stamp in data["timestamps"][:5]:
            self.assertTrue(stamp.endswith("+00:00"), stamp)

    def test_simulate_nennt_die_anzeigezeitzone(self):
        data = self.json("/api/simulate?hours=24")
        self.assertEqual(data["display_timezone"], "Europe/Berlin")

    def test_datenstatus_liefert_utc_und_zone_getrennt(self):
        data = self.json("/api/data/status")
        self.assertIn("display_timezone", data)
        self.assertIn("region", data)
        if data["available"]:
            self.assertTrue(data["range"]["first"].endswith("+00:00"))

    def test_zeitstempel_sind_lueckenlos_stuendlich(self):
        from datetime import datetime
        stamps = [datetime.fromisoformat(s) for s in self.json("/api/simulate?hours=48")["timestamps"]]
        for earlier, later in zip(stamps, stamps[1:]):
            self.assertEqual((later - earlier).total_seconds(), 3600)
