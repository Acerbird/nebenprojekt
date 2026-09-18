"""Tests der Abrufplanung.

Geprüft wird plan_weeks — die Entscheidung, welche Wochen geholt werden. Sie
ist bewusst von Netz und Datenbank getrennt, damit genau das hier ohne beides
prüfbar ist.

Die Wochen sind als einfache Zahlen dargestellt (0, 1, 2, ...); für die Logik
zählt nur ihre Reihenfolge.
"""

import unittest

from backend.data import smard
from backend.data.ingest import plan_weeks

WEEKS = list(range(100))  # 100 verfügbare Wochen


class PlanWeeksTest(unittest.TestCase):
    def test_ohne_bestand_wird_alles_geholt(self):
        self.assertEqual(plan_weeks(WEEKS, known=[], refresh=3), WEEKS)

    def test_ohne_verfuegbare_wochen_passiert_nichts(self):
        self.assertEqual(plan_weeks([], known=[], refresh=3), [])

    def test_bekannte_wochen_werden_uebersprungen(self):
        # Alles bekannt außer Woche 50; die jüngsten drei kommen trotzdem mit.
        known = [w for w in WEEKS if w != 50]
        self.assertEqual(plan_weeks(WEEKS, known=known, refresh=3), [50, 97, 98, 99])

    def test_die_juengsten_wochen_werden_immer_erneuert(self):
        """SMARD liefert zunächst vorläufige Werte und bessert sie nach."""
        self.assertEqual(plan_weeks(WEEKS, known=WEEKS, refresh=3), [97, 98, 99])

    def test_ohne_erneuerung_bleibt_bekanntes_unangetastet(self):
        self.assertEqual(plan_weeks(WEEKS, known=WEEKS, refresh=0), [])

    def test_limit_betrachtet_die_juengsten_wochen(self):
        """`--weeks 8` heißt: die letzten acht Wochen ansehen.

        Nicht: acht beliebige Wochen aus der gesamten Historie holen. Sonst
        belegen uralte Lücken die Plätze und der Bestand wird nie aktuell.
        """
        self.assertEqual(plan_weeks(WEEKS, known=[], limit=8), WEEKS[-8:])

    def test_limit_mit_vollstaendigem_bestand_erneuert_nur_die_juengsten(self):
        self.assertEqual(plan_weeks(WEEKS, known=WEEKS, limit=8, refresh=3), [97, 98, 99])

    def test_alte_luecke_verdraengt_die_aktuellen_wochen_nicht(self):
        known = [w for w in WEEKS if w != 5]
        planned = plan_weeks(WEEKS, known=known, limit=8, refresh=3)
        self.assertNotIn(5, planned)
        self.assertEqual(planned, [97, 98, 99])

    def test_since_begrenzt_nach_unten(self):
        planned = plan_weeks(WEEKS, known=[], since=90)
        self.assertEqual(planned, list(range(90, 100)))

    def test_since_und_limit_wirken_zusammen(self):
        planned = plan_weeks(WEEKS, known=[], since=90, limit=4)
        self.assertEqual(planned, [96, 97, 98, 99])

    def test_abgeschlossene_wochen_werden_nicht_erneut_geholt(self):
        """Nur die jüngsten Wochen sind vorläufig; ältere gelten als endgültig.

        Auch wenn `since` und `limit` ein großes Fenster aufspannen, wird darin
        nur nachgeladen, was fehlt — plus die drei vorläufigen Wochen am Rand.
        """
        planned = plan_weeks(WEEKS, known=WEEKS, since=50, limit=30, refresh=3)
        self.assertEqual(planned, [97, 98, 99])

    def test_ergebnis_ist_aufsteigend_und_ohne_dubletten(self):
        known = [w for w in WEEKS if w % 3 == 0]
        planned = plan_weeks(WEEKS, known=known, refresh=5)
        self.assertEqual(planned, sorted(planned))
        self.assertEqual(len(planned), len(set(planned)))


if __name__ == "__main__":
    unittest.main()


class AufloesungTest(unittest.TestCase):
    """Nicht jede Reihe kommt stündlich.

    Der Day-Ahead-Preis liegt bei SMARD unter demselben Filter auch
    viertelstündlich. Die Auflösung steht deshalb an der Reihe und nicht mehr
    fest im Modul — sonst holte `price_quarter` stumm die Stundenwerte, und die
    Zahlen der Marktseite wären ohne sichtbaren Fehler falsch.
    """

    def test_ohne_angabe_gilt_stuendlich(self):
        self.assertEqual(smard.resolution("price"), "hour")
        self.assertEqual(smard.resolution("load"), "hour")

    def test_viertelstundenreihe_ist_als_solche_eingetragen(self):
        self.assertEqual(smard.resolution("price_quarter"), "quarterhour")

    def test_beide_preisreihen_nutzen_denselben_filter(self):
        """Es ist dieselbe Auktion — nur anders aufgelöst abgerufen."""
        self.assertEqual(smard.SERIES["price_quarter"]["filter"],
                         smard.SERIES["price"]["filter"])

    def test_unbekannte_reihe_faellt_auf(self):
        with self.assertRaises(KeyError):
            smard.resolution("gibtesnicht")

    def test_die_viertelstundenreihe_wird_mitgeholt(self):
        """Die Marktseite beruht darauf — sie darf im cron nicht fehlen."""
        self.assertIn("price_quarter", smard.CORE_SERIES)


class NachbarpreiseTest(unittest.TestCase):
    """Die Preisreihen der Nachbarzonen.

    Ihre Filternummern stehen nicht in der Datenschnittstelle, sondern in der
    Konfiguration, die die SMARD-Oberfläche lädt (siehe Modulkopf). Geraten
    wäre hier besonders gefährlich: Eine falsche Nummer liefert trotzdem
    plausible Preise, nur eben die eines anderen Landes.
    """

    def test_jede_nachbarzone_ist_auch_eine_zeitreihe(self):
        for name in smard.NEIGHBOUR_PRICES:
            self.assertIn(name, smard.SERIES, name)

    def test_jede_nachbarzone_hat_eine_eigene_filternummer(self):
        nummern = [smard.SERIES[name]["filter"] for name in smard.NEIGHBOUR_PRICES]
        self.assertEqual(len(nummern), len(set(nummern)))

    def test_keine_nachbarzone_teilt_die_nummer_mit_deutschland(self):
        deutsch = smard.SERIES["price"]["filter"]
        for name in smard.NEIGHBOUR_PRICES:
            self.assertNotEqual(smard.SERIES[name]["filter"], deutsch, name)

    def test_alle_liefern_preise_in_euro_je_megawattstunde(self):
        for name in smard.NEIGHBOUR_PRICES:
            self.assertEqual(smard.SERIES[name]["unit"], "EUR/MWh", name)

    def test_die_zonennamen_stehen_im_label(self):
        """Sonst wäre in der Statusanzeige nicht zu sehen, welches Land gemeint ist."""
        for name, zone in smard.NEIGHBOUR_PRICES.items():
            self.assertIn(zone, smard.SERIES[name]["label"], name)
