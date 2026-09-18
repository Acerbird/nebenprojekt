"""Geführte Geschichten: Struktur und Rechenbarkeit.

Der wichtigste Test ist der letzte. Eine Geschichte, deren Parameter das Modell
stillschweigend verbiegt — ein Startdatum außerhalb des Bestands, eine Jahreszeit,
die es nicht gibt —, erzählt etwas anderes als das, was der Text behauptet. Das
fiele im Betrieb niemandem auf, weil eine Simulation trotzdem herauskommt.
"""

import unittest

from backend import analysis as anl
from backend.data import sources


PFLICHTFELDER = ("id", "title", "question", "lead", "steps")
SCHRITTFELDER = ("label", "params", "text", "watch")


class StruktureTest(unittest.TestCase):
    def setUp(self):
        self.stories = anl.stories()

    def test_es_gibt_geschichten(self):
        self.assertGreaterEqual(len(self.stories), 3)

    def test_jede_geschichte_ist_vollstaendig(self):
        for story in self.stories:
            for feld in PFLICHTFELDER:
                self.assertIn(feld, story, story.get("id"))
                self.assertTrue(story[feld], "%s: %s ist leer" % (story.get("id"), feld))

    def test_kennungen_sind_eindeutig_und_url_tauglich(self):
        ids = [s["id"] for s in self.stories]
        self.assertEqual(len(ids), len(set(ids)))
        for kennung in ids:
            self.assertRegex(kennung, r"^[a-z0-9-]+$")

    def test_jeder_schritt_ist_vollstaendig(self):
        for story in self.stories:
            self.assertGreaterEqual(len(story["steps"]), 2,
                                    "%s: eine Geschichte braucht mehr als einen Schritt" % story["id"])
            for index, step in enumerate(story["steps"]):
                for feld in SCHRITTFELDER:
                    self.assertIn(feld, step, "%s/%d" % (story["id"], index))
                    self.assertTrue(step[feld], "%s/%d: %s ist leer" % (story["id"], index, feld))

    def test_schritte_unterscheiden_sich(self):
        """Zwei gleiche Parametersätze hintereinander wären eine Behauptung ohne Beleg."""
        for story in self.stories:
            gesehen = [step["params"] for step in story["steps"]]
            for index, params in enumerate(gesehen[1:], start=1):
                self.assertNotEqual(params, gesehen[index - 1],
                                    "%s: Schritt %d ändert nichts" % (story["id"], index + 1))

    def test_nur_bekannte_parameter(self):
        erlaubt = set(anl.DEFAULTS) | {"start", "scale_load"}
        for story in self.stories:
            for step in story["steps"]:
                unbekannt = set(step["params"]) - erlaubt
                self.assertFalse(unbekannt, "%s: %s" % (story["id"], unbekannt))

    def test_nur_bekannte_erwartungen(self):
        erlaubt = set(ERWARTUNGEN)
        for story in self.stories:
            for step in story["steps"]:
                unbekannt = set(step.get("expect", {})) - erlaubt
                self.assertFalse(unbekannt, "%s: %s" % (story["id"], unbekannt))


# Welche Behauptung lässt sich woran messen? Der Schlüssel steht in der
# Geschichte, die Funktion holt den Wert aus dem Ergebnis.
ERWARTUNGEN = {
    "negative_price_hours": lambda r: r["kpis"]["negative_price_hours"],
    "curtailed_gwh": lambda r: r["kpis"]["curtailed_gwh"],
    "mean_price": lambda r: r["kpis"]["mean_price"],
    "renewable_share": lambda r: r["kpis"]["renewable_share"],
    "emissions_kt": lambda r: r["kpis"]["emissions_kt"],
    "validation_mae": lambda r: (r.get("validation") or {}).get("mean_absolute_error"),
    "merit_order": None,      # Sonderfall, siehe unten
}


class RechenbarkeitTest(unittest.TestCase):
    """Rechnet jeder Schritt wirklich das, was er verspricht?"""

    @classmethod
    def setUpClass(cls):
        cls.stories = anl.stories()
        cls.hat_daten = sources.available_range() is not None

    def simulate(self, params):
        kwargs = {k: v for k, v in params.items() if k != "scale_load"}
        return anl.simulate(**kwargs)

    def test_jeder_schritt_laeuft_durch(self):
        for story in self.stories:
            for index, step in enumerate(story["steps"]):
                if step["params"].get("source") == "historical" and not self.hat_daten:
                    continue
                with self.subTest(story=story["id"], schritt=index):
                    ergebnis = self.simulate(step["params"])
                    self.assertTrue(ergebnis["price_eur_mwh"])

    def test_kein_schritt_wird_stillschweigend_verbogen(self):
        """Wird ein Zeitraum verschoben oder gekürzt, passt der Text nicht mehr."""
        for story in self.stories:
            for index, step in enumerate(story["steps"]):
                if step["params"].get("source") != "historical":
                    continue
                if not self.hat_daten:
                    self.skipTest("Keine Messwerte vorhanden")
                with self.subTest(story=story["id"], schritt=index):
                    ergebnis = self.simulate(step["params"])
                    self.assertEqual(ergebnis["params"]["adjustments"], {},
                                     "%s/%d: der Zeitraum wurde angepasst" % (story["id"], index))

    def test_die_gewuenschten_parameter_kommen_auch_an(self):
        """Ein Regler, der im Text steht, muss auch im Ergebnis stehen."""
        for story in self.stories:
            for index, step in enumerate(story["steps"]):
                if step["params"].get("source") == "historical" and not self.hat_daten:
                    continue
                ergebnis = self.simulate(step["params"])
                for name in ("wind_gw", "solar_gw", "co2_price", "hours"):
                    if name not in step["params"]:
                        continue
                    with self.subTest(story=story["id"], schritt=index, param=name):
                        self.assertEqual(ergebnis["params"][name], float(step["params"][name])
                                         if name != "hours" else int(step["params"][name]))


class BehauptungenTest(unittest.TestCase):
    """Sagt der Text die Wahrheit über das, was das Modell tut?

    Das ist der Test, der beim Schreiben dieser Geschichten am meisten gefunden
    hat. Vier von fünf behaupteten zunächst etwas, das nicht stimmte — ein
    Schritt sagte „der Preis bleibt über null", während er in siebzehn Stunden
    darunter lag. Solche Fehler fallen im Betrieb niemandem auf, weil eine
    Simulation trotzdem herauskommt und plausibel aussieht.

    Die Erwartungen stehen bei der Behauptung, nicht hier. So muss, wer den Text
    ändert, auch die Zahl daneben anfassen.
    """

    @classmethod
    def setUpClass(cls):
        cls.stories = anl.stories()
        cls.hat_daten = sources.available_range() is not None

    def pruefe(self, kennung, erwartet, gemessen):
        if isinstance(erwartet, dict):
            if "min" in erwartet:
                self.assertGreaterEqual(gemessen, erwartet["min"], kennung)
            if "max" in erwartet:
                self.assertLessEqual(gemessen, erwartet["max"], kennung)
        else:
            self.assertEqual(gemessen, erwartet, kennung)

    def test_jede_behauptung_haelt_dem_modell_stand(self):
        for story in self.stories:
            for index, step in enumerate(story["steps"]):
                erwartungen = step.get("expect")
                if not erwartungen:
                    continue
                if step["params"].get("source") == "historical" and not self.hat_daten:
                    continue
                kwargs = {k: v for k, v in step["params"].items() if k != "scale_load"}
                ergebnis = anl.simulate(**kwargs)
                for kennung, erwartet in erwartungen.items():
                    with self.subTest(story=story["id"], schritt=index, wert=kennung):
                        if kennung == "merit_order":
                            self.pruefe_reihenfolge(step["params"], erwartet)
                            continue
                        gemessen = ERWARTUNGEN[kennung](ergebnis)
                        self.assertIsNotNone(gemessen, kennung)
                        self.pruefe(kennung, erwartet, gemessen)

    def pruefe_reihenfolge(self, params, erwartet):
        """Stehen die genannten Blöcke wirklich in dieser Reihenfolge?"""
        blocks = anl.merit_order(float(params.get("co2_price", anl.DEFAULTS["co2_price"])),
                                 float(params.get("gas_price", anl.DEFAULTS["gas_price"])))
        rang = [b["id"] for b in blocks if b["id"] in erwartet]
        self.assertEqual(rang, list(erwartet))

    def test_es_gibt_ueberhaupt_pruefbare_behauptungen(self):
        """Ein Test, der nichts prüft, weil niemand mehr `expect` schreibt,
        wäre schlimmer als keiner — er gäbe eine Sicherheit vor, die fehlt."""
        mit = sum(1 for s in self.stories for step in s["steps"] if step.get("expect"))
        self.assertGreaterEqual(mit, len(self.stories),
                                "Im Schnitt sollte jede Geschichte mindestens eine "
                                "nachrechenbare Behauptung enthalten")


if __name__ == "__main__":
    unittest.main()
