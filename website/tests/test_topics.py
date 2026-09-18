"""Die Erklärseiten zu Speicher und Handel — und ihre Zahlen.

Auf einer Lernseite ist eine falsche Zahl schlimmer als keine: Wer hier etwas
lernt, kann sie nicht nachprüfen. Diese Tests rechnen deshalb jede genannte
Zahl an denselben Messwerten nach, aus denen sie stammt. Steht im Text etwas
anderes als in der Datenbank, schlägt der Test fehl — egal, welches von beiden
sich geändert hat.

Ohne lokale Messwerte wird übersprungen; die Struktur der Seiten wird trotzdem
geprüft.
"""

import statistics
import unittest
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from backend.main import app
from backend.data import store


def jahr_von(ts: int) -> int:
    return datetime.fromtimestamp(ts, timezone.utc).year


# Seit dem 1. Oktober 2025 räumt die Day-Ahead-Auktion Viertelstunden statt
# Stunden. Das Fenster endet mit dem letzten abgeschlossenen Monat: Ein
# mitlaufendes Ende würde die Zahlen mit jedem Datenabruf verschieben, und
# SMARD bessert die jüngsten Wochen ohnehin nach.
QUARTER_VON = datetime(2025, 10, 1, tzinfo=timezone.utc)
QUARTER_BIS = datetime(2026, 9, 1, tzinfo=timezone.utc)

# Für den Preisvergleich mit den Nachbarzonen. Auch hier ein festes Ende, aus
# demselben Grund.
KOPPLUNG_VON = datetime(2024, 1, 1, tzinfo=timezone.utc)
KOPPLUNG_BIS = datetime(2026, 9, 1, tzinfo=timezone.utc)

# Reihe je Zone, wie sie auf der Handelsseite steht.
ZONEN = {
    "Dänemark 1": "price_dk1", "Dänemark 2": "price_dk2", "Niederlande": "price_nl",
    "Österreich": "price_at", "Belgien": "price_be", "Tschechien": "price_cz",
    "Frankreich": "price_fr", "Polen": "price_pl", "Schweden 4": "price_se4",
    "Norwegen 2": "price_no2", "Schweiz": "price_ch",
}


class Messwerte:
    """Die Reihen einmal lesen, danach je Jahr auswerten."""

    def __init__(self, conn):
        namen = (("net_export", "pumped_storage", "pumped_load", "price", "price_quarter")
                 + tuple(ZONEN.values()))
        self.reihen = {}
        for name in namen:
            self.reihen[name] = {ts: value for ts, value in
                                 conn.execute("SELECT ts, value FROM observations "
                                              "WHERE series = ? AND value IS NOT NULL", (name,))}

    def jahr(self, name, jahr):
        return {ts: v for ts, v in self.reihen[name].items() if jahr_von(ts) == jahr}

    def vollstaendig(self, jahr):
        """Ein Jahr zählt nur, wenn es fast lückenlos vorliegt."""
        return len(self.jahr("price", jahr)) > 8000

    # --- die Größen, die auf den Seiten stehen ---

    def ausfuhr_twh(self, jahr):
        return sum(v for v in self.jahr("net_export", jahr).values() if v > 0) / 1e6

    def einfuhr_twh(self, jahr):
        return -sum(v for v in self.jahr("net_export", jahr).values() if v < 0) / 1e6

    def saldo_twh(self, jahr):
        return self.ausfuhr_twh(jahr) - self.einfuhr_twh(jahr)

    def importstunden_anteil(self, jahr):
        werte = list(self.jahr("net_export", jahr).values())
        return 100.0 * sum(1 for v in werte if v < 0) / len(werte)

    def pumpspeicher_twh(self, richtung, jahr):
        name = "pumped_load" if richtung == "laden" else "pumped_storage"
        return sum(self.jahr(name, jahr).values()) / 1e6

    def pumpspeicher_preis(self, richtung, jahr):
        """Mengengewichteter Preis der Lade- oder Entladestunden."""
        name = "pumped_load" if richtung == "laden" else "pumped_storage"
        menge, wert = 0.0, 0.0
        for ts, v in self.jahr(name, jahr).items():
            preis = self.reihen["price"].get(ts)
            if preis is not None:
                menge += v
                wert += v * preis
        return wert / menge if menge else float("nan")

    # --- Viertelstunden, seit die Auktion sie kennt ---

    def _fenster(self, name):
        von, bis = QUARTER_VON.timestamp(), QUARTER_BIS.timestamp()
        return {ts: v for ts, v in self.reihen[name].items() if von <= ts < bis}

    def _tage(self, name, mindestens):
        tage = {}
        for ts in sorted(self._fenster(name)):
            tag = datetime.fromtimestamp(ts, timezone.utc).date()
            tage.setdefault(tag, []).append((ts, self.reihen[name][ts]))
        return {tag: werte for tag, werte in tage.items() if len(werte) >= mindestens}

    def _gemeinsame_tage(self):
        viertel = self._tage("price_quarter", 92)
        stunden = self._tage("price", 23)
        return sorted(set(viertel) & set(stunden)), viertel, stunden

    def spreizung_in_der_stunde(self, art):
        """Teuerste minus billigste Viertelstunde derselben Stunde."""
        viertel = self._fenster("price_quarter")
        werte = []
        for ts in sorted(self._fenster("price")):
            vier = [viertel.get(ts + i * 900) for i in range(4)]
            if all(v is not None for v in vier):
                werte.append(max(vier) - min(vier))
        if art == "stunden":
            return float(len(werte))
        if art == "median":
            return statistics.median(werte)
        if art == "max":
            return max(werte)
        return statistics.mean(werte)

    def hoechstpreis_in_teuerster_stunde(self):
        """Anteil der Tage, an denen beide Höchstwerte in dieselbe Stunde fallen."""
        tage, viertel, stunden = self._gemeinsame_tage()
        treffer = 0
        for tag in tage:
            beste_stunde = max(stunden[tag], key=lambda p: p[1])[0]
            beste_viertel = max(viertel[tag], key=lambda p: p[1])[0]
            treffer += beste_stunde <= beste_viertel < beste_stunde + 3600
        return 100.0 * treffer / len(tage)

    def tage_im_fenster(self):
        return float(len(self._gemeinsame_tage()[0]))

    def deckungsbeitrag(self, stunden_vorrat, produkt):
        """Kaufen in den billigsten, verkaufen in den teuersten Zeitscheiben.

        Perfekte Voraussicht und nur der Umwandlungsverlust abgezogen — eine
        Obergrenze dessen, was der Markt hergegeben hätte, kein Betriebsergebnis.
        """
        tage, viertel, stunden = self._gemeinsame_tage()
        quelle = viertel if produkt == "viertel" else stunden
        bloecke = stunden_vorrat * 4 if produkt == "viertel" else stunden_vorrat
        ergebnisse = []
        for tag in tage:
            preise = sorted(preis for _, preis in quelle[tag])
            kauf = sum(preise[:bloecke]) / bloecke
            verkauf = sum(preise[-bloecke:]) / bloecke
            ergebnisse.append(verkauf * 0.88 - kauf)
        return statistics.mean(ergebnisse)

    # --- Preisvergleich mit den Nachbarzonen ---

    def _kopplung(self, zone):
        """Gemeinsame Stunden von Deutschland und der Zone im festen Fenster."""
        von, bis = KOPPLUNG_VON.timestamp(), KOPPLUNG_BIS.timestamp()
        nachbar = self.reihen[ZONEN[zone]]
        paare = []
        for ts, wert in self.reihen["price"].items():
            if von <= ts < bis and ts in nachbar:
                paare.append((wert, nachbar[ts]))
        return paare

    def gleicher_preis(self, zone):
        """Anteil der Stunden mit identischem Preis, in Prozent.

        Identisch heißt hier wirklich identisch: SMARD führt zwei Nachkommastellen,
        und wenn die Kopplung ohne Engpass räumt, steht in beiden Zonen dieselbe Zahl.
        """
        paare = self._kopplung(zone)
        return 100.0 * sum(1 for a, b in paare if abs(a - b) < 0.01) / len(paare)

    def mittlerer_abstand(self, zone):
        paare = self._kopplung(zone)
        return statistics.mean(abs(a - b) for a, b in paare)

    def deutschland_billiger(self, zone):
        paare = self._kopplung(zone)
        return 100.0 * sum(1 for a, b in paare if b - a > 0.01) / len(paare)

    def stunden_im_vergleich(self):
        return float(len(self._kopplung("Frankreich")))

    def hoechster_austausch(self, richtung):
        werte = [v / 1000.0 for v in self.reihen["net_export"].values()]
        return max(werte) if richtung == "ausfuhr" else -min(werte)

    def tagesspreizung(self, jahr):
        """Mittlerer Abstand zwischen teuerster und billigster Stunde eines Tages."""
        tage = {}
        for ts, v in self.jahr("price", jahr).items():
            tag = datetime.fromtimestamp(ts, timezone.utc).date()
            tage.setdefault(tag, []).append(v)
        spannen = [max(w) - min(w) for w in tage.values() if len(w) >= 20]
        return sum(spannen) / len(spannen)


# Jede Zeile: Seite, wörtlicher Ausschnitt aus dem Text, gemessener Wert, Toleranz.
# Der Ausschnitt muss auf der Seite stehen, der Wert muss stimmen.
BEHAUPTUNGEN = [
    ("/handel", "19,8 TWh", lambda m: m.ausfuhr_twh(2023), 0.2),
    ("/handel", "31,5 TWh", lambda m: m.einfuhr_twh(2023), 0.2),
    ("/handel", "−11,7 TWh", lambda m: m.saldo_twh(2023), 0.2),
    ("/handel", "58 %", lambda m: m.importstunden_anteil(2023), 1.0),
    ("/handel", "12,4 TWh", lambda m: m.ausfuhr_twh(2024), 0.2),
    ("/handel", "40,7 TWh", lambda m: m.einfuhr_twh(2024), 0.2),
    ("/handel", "−28,3 TWh", lambda m: m.saldo_twh(2024), 0.2),
    ("/handel", "73 %", lambda m: m.importstunden_anteil(2024), 1.0),
    ("/handel", "12,2 TWh", lambda m: m.ausfuhr_twh(2025), 0.2),
    ("/handel", "34,1 TWh", lambda m: m.einfuhr_twh(2025), 0.2),
    ("/handel", "−21,9 TWh", lambda m: m.saldo_twh(2025), 0.2),
    ("/handel", "70 %", lambda m: m.importstunden_anteil(2025), 1.0),
    ("/speicher", "99 €/MWh", lambda m: m.tagesspreizung(2023), 3.0),
    ("/speicher", "112 €/MWh", lambda m: m.tagesspreizung(2024), 3.0),
    ("/speicher", "125 €/MWh", lambda m: m.tagesspreizung(2025), 3.0),
    ("/speicher", "12,7 TWh eingespeichert", lambda m: m.pumpspeicher_twh("laden", 2025), 0.2),
    ("/speicher", "9,9 TWh wieder abgegeben", lambda m: m.pumpspeicher_twh("entladen", 2025), 0.2),
    ("/speicher", "48 €/MWh", lambda m: m.pumpspeicher_preis("laden", 2025), 2.0),
    ("/speicher", "130 €/MWh", lambda m: m.pumpspeicher_preis("entladen", 2025), 2.0),
    # Seit der Umstellung auf Viertelstunden — die Zahlen der Marktseite.
    ("/maerkte", "7 922 Stunden", lambda m: m.spreizung_in_der_stunde("stunden"), 0.5),
    ("/maerkte", "23,2 €/MWh", lambda m: m.spreizung_in_der_stunde("mittel"), 0.3),
    ("/maerkte", "14,6 €/MWh", lambda m: m.spreizung_in_der_stunde("median"), 0.3),
    ("/maerkte", "460 €/MWh", lambda m: m.spreizung_in_der_stunde("max"), 1.0),
    ("/maerkte", "An nur 38 %", lambda m: m.hoechstpreis_in_teuerster_stunde(), 0.6),
    ("/maerkte", "330 Tage", lambda m: m.tage_im_fenster(), 0.5),
    ("/maerkte", "<td>112,8 €</td>", lambda m: m.deckungsbeitrag(1, "stunde"), 0.3),
    ("/maerkte", "<td>123,8 €</td>", lambda m: m.deckungsbeitrag(1, "viertel"), 0.3),
    ("/maerkte", "<td>107,9 €</td>", lambda m: m.deckungsbeitrag(2, "stunde"), 0.3),
    ("/maerkte", "<td>114,6 €</td>", lambda m: m.deckungsbeitrag(2, "viertel"), 0.3),
    ("/maerkte", "<td>94,2 €</td>", lambda m: m.deckungsbeitrag(4, "stunde"), 0.3),
    ("/maerkte", "<td>98,7 €</td>", lambda m: m.deckungsbeitrag(4, "viertel"), 0.3),
    ("/maerkte", "<td>69,1 €</td>", lambda m: m.deckungsbeitrag(8, "stunde"), 0.3),
    ("/maerkte", "<td>72,2 €</td>", lambda m: m.deckungsbeitrag(8, "viertel"), 0.3),
    ("/speicher", "23,2 €/MWh", lambda m: m.spreizung_in_der_stunde("mittel"), 0.3),
    # Die Handelsseite nennt die tatsächlich erreichten Höchstwerte — nicht die
    # Grenzen der Modellkurve, das war hier schon einmal verwechselt.
    # Preiskonvergenz je Nachbarzone — die Tabelle der Handelsseite.
    ("/handel", "23 209 Stunden", lambda m: m.stunden_im_vergleich(), 0.5),
    ("/handel", "<td>49,0 %</td><td>8,5 €/MWh</td><td>11,0 %</td>",
     lambda m, z="Dänemark 1": m.gleicher_preis(z), 0.06),
    ("/handel", "<td>8,5 €/MWh</td>", lambda m, z="Dänemark 1": m.mittlerer_abstand(z), 0.06),
    ("/handel", "<td>32,0 %</td><td>9,6 €/MWh</td><td>22,3 %</td>",
     lambda m, z="Dänemark 2": m.gleicher_preis(z), 0.06),
    ("/handel", "<td>9,6 €/MWh</td>", lambda m, z="Dänemark 2": m.mittlerer_abstand(z), 0.06),
    ("/handel", "<td>15,5 %</td><td>6,7 €/MWh</td><td>28,9 %</td>",
     lambda m, z="Niederlande": m.gleicher_preis(z), 0.06),
    ("/handel", "<td>6,7 €/MWh</td>", lambda m, z="Niederlande": m.mittlerer_abstand(z), 0.06),
    ("/handel", "<td>11,8 %</td><td>13,1 €/MWh</td><td>59,7 %</td>",
     lambda m, z="Österreich": m.gleicher_preis(z), 0.06),
    ("/handel", "<td>13,1 €/MWh</td>", lambda m, z="Österreich": m.mittlerer_abstand(z), 0.06),
    ("/handel", "<td>11,3 %</td><td>11,6 €/MWh</td><td>31,2 %</td>",
     lambda m, z="Belgien": m.gleicher_preis(z), 0.06),
    ("/handel", "<td>11,6 €/MWh</td>", lambda m, z="Belgien": m.mittlerer_abstand(z), 0.06),
    ("/handel", "<td>11,2 %</td><td>10,1 €/MWh</td><td>61,7 %</td>",
     lambda m, z="Tschechien": m.gleicher_preis(z), 0.06),
    ("/handel", "<td>10,1 €/MWh</td>", lambda m, z="Tschechien": m.mittlerer_abstand(z), 0.06),
    ("/handel", "<td>10,8 %</td><td>29,3 €/MWh</td><td>24,4 %</td>",
     lambda m, z="Frankreich": m.gleicher_preis(z), 0.06),
    ("/handel", "<td>29,3 €/MWh</td>", lambda m, z="Frankreich": m.mittlerer_abstand(z), 0.06),
    ("/handel", "<td>6,6 %</td><td>20,4 €/MWh</td><td>70,8 %</td>",
     lambda m, z="Polen": m.gleicher_preis(z), 0.06),
    ("/handel", "<td>20,4 €/MWh</td>", lambda m, z="Polen": m.mittlerer_abstand(z), 0.06),
    ("/handel", "<td>5,4 %</td><td>30,7 €/MWh</td><td>18,6 %</td>",
     lambda m, z="Schweden 4": m.gleicher_preis(z), 0.06),
    ("/handel", "<td>30,7 €/MWh</td>", lambda m, z="Schweden 4": m.mittlerer_abstand(z), 0.06),
    ("/handel", "<td>1,3 %</td><td>29,5 €/MWh</td><td>22,5 %</td>",
     lambda m, z="Norwegen 2": m.gleicher_preis(z), 0.06),
    ("/handel", "<td>29,5 €/MWh</td>", lambda m, z="Norwegen 2": m.mittlerer_abstand(z), 0.06),
    ("/handel", "<td>0,1 %</td><td>20,3 €/MWh</td><td>61,6 %</td>",
     lambda m, z="Schweiz": m.gleicher_preis(z), 0.06),
    ("/handel", "<td>20,3 €/MWh</td>", lambda m, z="Schweiz": m.mittlerer_abstand(z), 0.06),
    ("/handel", "19,4 GW Ausfuhr", lambda m: m.hoechster_austausch("ausfuhr"), 0.1),
    ("/handel", "17,1 GW Einfuhr", lambda m: m.hoechster_austausch("einfuhr"), 0.1),
]

# Aus dem Ausschnitt die Zahl lesen: "−28,3 TWh" -> -28.3, "73 %" -> 73.0,
# "7 922 Stunden" -> 7922.0. Ein Leerzeichen zwischen zwei Ziffern trennt
# Tausender und gehört noch zur Zahl; eines vor einem Buchstaben beendet sie.
def zahl_aus(text: str) -> float:
    ziffern = ""
    for stelle, zeichen in enumerate(text):
        if zeichen.isdigit() or zeichen in ",.":
            ziffern += "." if zeichen == "," else zeichen
        elif zeichen == " " and ziffern and text[stelle + 1:stelle + 2].isdigit():
            continue
        elif ziffern:
            break
        elif zeichen in "-−":
            ziffern = "-"
    return float(ziffern)


class SeitenTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.seiten = {pfad: cls.client.get(pfad).text
                      for pfad in ("/speicher", "/handel", "/maerkte")}

    def test_beide_seiten_antworten(self):
        for pfad in self.seiten:
            self.assertEqual(self.client.get(pfad).status_code, 200, pfad)

    def test_jede_seite_verweist_auf_den_simulator(self):
        """Eine Erklärseite, die nicht zum Ausprobieren führt, bleibt Behauptung."""
        for pfad, html in self.seiten.items():
            self.assertIn('href="/analysen"', html, pfad)

    def test_die_handelsseite_zeigt_die_gemessene_kurve(self):
        self.assertIn('id="chart-exchange"', self.seiten["/handel"])
        self.assertIn("topics.js", self.seiten["/handel"])


class KurvenEndpunktTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = TestClient(app).get("/api/exchange-curve").json()

    def test_gleichmaessig_abgetastet(self):
        """Ungleiche Abstände ergäben ein verzerrtes Bild der Steigung."""
        preise = self.data["prices"]
        self.assertGreater(len(preise), 10)
        schritte = {round(b - a, 6) for a, b in zip(preise, preise[1:])}
        self.assertEqual(len(schritte), 1, schritte)

    def test_kurve_faellt_ueber_den_ganzen_bereich(self):
        """Das ist die Aussage der Seite: mehr Preis, weniger Ausfuhr."""
        werte = self.data["net_export_gw"]
        for links, rechts in zip(werte, werte[1:]):
            self.assertGreaterEqual(links, rechts)

    def test_stuetzstellen_kommen_mit(self):
        """Damit sichtbar bleibt, wo gemessen wurde und wo interpoliert."""
        self.assertTrue(self.data["points"])
        for punkt in self.data["points"]:
            self.assertIn("price_eur_mwh", punkt)
            self.assertIn("net_export_gw", punkt)

    def test_quelle_steht_dabei(self):
        self.assertIn("SMARD", self.data["source"])


class TagesvergleichEndpunktTest(unittest.TestCase):
    """Stunden- und Viertelstundenpreis eines Tages nebeneinander."""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.data = cls.client.get("/api/quarter-prices").json()
        cls.hat_daten = bool(cls.data.get("quarter"))

    def test_ein_voller_tag_in_viertelstunden(self):
        if not self.hat_daten:
            self.skipTest("Keine Messwerte vorhanden")
        self.assertEqual(len(self.data["quarter"]), 96)
        self.assertEqual(len(self.data["hourly"]), 96)

    def test_die_stundenlinie_ist_eine_treppe(self):
        """Vier gleiche Werte je Stunde — sonst läge sie nicht auf derselben Achse."""
        if not self.hat_daten:
            self.skipTest("Keine Messwerte vorhanden")
        stunden = self.data["hourly"]
        for beginn in range(0, 96, 4):
            gruppe = stunden[beginn:beginn + 4]
            self.assertEqual(len(set(gruppe)), 1, "Stunde ab Viertelstunde %d" % beginn)

    def test_der_beispieltag_zeigt_echte_viertelstunden(self):
        """Vor Oktober 2025 lägen beide Linien aufeinander — dann trüge die Seite nichts."""
        if not self.hat_daten:
            self.skipTest("Keine Messwerte vorhanden")
        self.assertNotEqual(self.data["quarter"], self.data["hourly"])

    def test_die_stempel_steigen_in_viertelstundenschritten(self):
        if not self.hat_daten:
            self.skipTest("Keine Messwerte vorhanden")
        stempel = self.data["timestamps"]
        self.assertEqual({b - a for a, b in zip(stempel, stempel[1:])}, {900})

    def test_zeitzone_reist_mit(self):
        """Ohne sie beschriftete das Diagramm die Achse in UTC — zwei Stunden daneben."""
        self.assertEqual(self.data.get("display_timezone"), "Europe/Berlin")

    def test_unsinniges_datum_wird_nicht_geraten(self):
        antwort = self.client.get("/api/quarter-prices?date=uebermorgen").json()
        self.assertIn("error", antwort)
        self.assertEqual(antwort["quarter"], [])

    def test_ein_tag_ohne_messwerte_liefert_leere_reihen_statt_platzhalter(self):
        antwort = self.client.get("/api/quarter-prices?date=1990-01-01").json()
        self.assertEqual(antwort["quarter"], [])


class BehauptungenTest(unittest.TestCase):
    """Jede Zahl im Text gegen die Messwerte, aus denen sie stammt."""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        cls.seiten = {pfad: cls.client.get(pfad).text
                      for pfad in ("/speicher", "/handel", "/maerkte")}
        cls.messwerte = None
        if store.exists():
            with store.open_db() as conn:
                cls.messwerte = Messwerte(conn)

    def test_jede_zahl_steht_auch_auf_der_seite(self):
        """Fängt Zahlen, die im Text geändert wurden, ohne die Prüfung anzupassen."""
        for pfad, ausschnitt, _, _ in BEHAUPTUNGEN:
            with self.subTest(seite=pfad, text=ausschnitt):
                # Nicht assertIn: die Seite als Ganzes in der Fehlermeldung wäre
                # unlesbar, und die fehlende Stelle steht ohnehin im subTest.
                self.assertTrue(ausschnitt in self.seiten[pfad],
                                "%s steht nicht mehr auf %s" % (ausschnitt, pfad))

    def test_jede_zahl_stimmt_mit_den_messwerten(self):
        if self.messwerte is None:
            self.skipTest("Keine Messwerte vorhanden")
        for pfad, ausschnitt, messen, toleranz in BEHAUPTUNGEN:
            with self.subTest(seite=pfad, text=ausschnitt):
                gemessen = messen(self.messwerte)
                behauptet = zahl_aus(ausschnitt)
                self.assertAlmostEqual(
                    behauptet, gemessen, delta=toleranz,
                    msg="%s behauptet %s, gemessen sind %.2f" % (pfad, ausschnitt, gemessen))

    def test_geprueft_werden_nur_abgeschlossene_jahre(self):
        """Ein laufendes Jahr würde die Zahlen mit jedem Abruf verschieben."""
        if self.messwerte is None:
            self.skipTest("Keine Messwerte vorhanden")
        for jahr in (2023, 2024, 2025):
            self.assertTrue(self.messwerte.vollstaendig(jahr),
                            "%d liegt nicht vollständig vor" % jahr)


if __name__ == "__main__":
    unittest.main()
