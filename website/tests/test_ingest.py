"""Tests der Abrufplanung.

Geprüft wird plan_weeks — die Entscheidung, welche Wochen geholt werden. Sie
ist bewusst von Netz und Datenbank getrennt, damit genau das hier ohne beides
prüfbar ist.

Die Wochen sind als einfache Zahlen dargestellt (0, 1, 2, ...); für die Logik
zählt nur ihre Reihenfolge.
"""

import unittest

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
