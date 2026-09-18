"""Tests der Zeitreihen-Profile für Last, Wind und Photovoltaik.

Wichtigster Punkt: Die Profile müssen über Prozessgrenzen hinweg identisch
bleiben. Sonst zeigt ein geteilter Szenario-Link bei jedem Serverstart andere
Zahlen — und in Phase 2 wäre ein Vergleich mit echten Daten wertlos.
"""

import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from backend.region import timezone_name
from backend.utils import profiles


class DeterminismTest(unittest.TestCase):
    def test_windreihe_ist_bei_gleichen_parametern_identisch(self):
        first = profiles.wind_series(168, 70.0, "winter")
        second = profiles.wind_series(168, 70.0, "winter")
        self.assertEqual(first, second)

    def test_jahreszeiten_liefern_verschiedene_windreihen(self):
        self.assertNotEqual(profiles.wind_series(72, 70.0, "winter"),
                            profiles.wind_series(72, 70.0, "sommer"))

    def test_laengerer_zeitraum_verlaengert_die_reihe_nur(self):
        kurz = profiles.wind_series(24, 70.0, "winter")
        lang = profiles.wind_series(72, 70.0, "winter")
        self.assertEqual(lang[:24], kurz)


class WindProfileTest(unittest.TestCase):
    def setUp(self):
        self.installed = 70.0
        self.series = profiles.wind_series(336, self.installed, "winter")

    def test_bleibt_innerhalb_der_installierten_leistung(self):
        self.assertGreaterEqual(min(self.series), 0.0)
        self.assertLessEqual(max(self.series), self.installed * 0.92 + 1e-6)

    def test_mittlerer_kapazitaetsfaktor_passt_zur_jahreszeit(self):
        mean_cf = sum(self.series) / len(self.series) / self.installed
        expected = profiles.SEASONS["winter"]["wind_mean_cf"]
        # Ein logarithmisch normalverteilter Prozess streut; die Größenordnung
        # muss aber stimmen, sonst ist die Parametrierung verrutscht.
        self.assertAlmostEqual(mean_cf, expected, delta=0.12)

    def test_winter_ist_windiger_als_sommer(self):
        winter = profiles.wind_series(336, 70.0, "winter")
        sommer = profiles.wind_series(336, 70.0, "sommer")
        self.assertGreater(sum(winter), sum(sommer))

    def test_aufeinanderfolgende_stunden_haengen_zusammen(self):
        """AR(1): echte Flauten statt Rauschen — sonst gibt es keine Dunkelflaute."""
        steps = [abs(b - a) for a, b in zip(self.series, self.series[1:])]
        spread = max(self.series) - min(self.series)
        self.assertLess(sum(steps) / len(steps), spread * 0.25)


class SolarProfileTest(unittest.TestCase):
    def test_nachts_wird_nichts_erzeugt(self):
        series = profiles.solar_series(48, 90.0, "winter")
        cfg = profiles.SEASONS["winter"]
        for hour, value in enumerate(series):
            hour_of_day = hour % 24 + 0.5
            if not (cfg["sunrise"] < hour_of_day < cfg["sunset"]):
                self.assertEqual(value, 0.0, "Stunde %d" % hour)

    def test_sommer_erzeugt_mehr_als_winter(self):
        sommer = sum(profiles.solar_series(24, 90.0, "sommer"))
        winter = sum(profiles.solar_series(24, 90.0, "winter"))
        self.assertGreater(sommer, winter)

    def test_tagesgang_hat_genau_eine_mittagsspitze(self):
        day = profiles.solar_day_profile("sommer")
        peak = day.index(max(day))
        self.assertTrue(11 <= peak <= 14, "Spitze bei Stunde %d" % peak)
        self.assertEqual(day[:peak], sorted(day[:peak]))
        self.assertEqual(day[peak:], sorted(day[peak:], reverse=True))

    def test_kapazitaetsfaktor_bleibt_unter_eins(self):
        for season in profiles.SEASONS:
            self.assertLess(max(profiles.solar_day_profile(season)), 1.0)


class LoadProfileTest(unittest.TestCase):
    def test_hoechstlast_wird_nicht_ueberschritten(self):
        peak = 75.0
        series = profiles.load_series(336, peak, "winter")
        self.assertLessEqual(max(series), peak + 1e-6)
        self.assertGreater(min(series), 0.0)

    def test_wochenende_liegt_unter_werktag(self):
        werktag = profiles.load_day_profile(weekend=False)
        wochenende = profiles.load_day_profile(weekend=True)
        self.assertGreater(sum(werktag), sum(wochenende))

    def test_nachts_wird_weniger_verbraucht_als_mittags(self):
        day = profiles.load_day_profile()
        self.assertLess(day[3], day[11])

    def test_winter_verbraucht_mehr_als_sommer(self):
        winter = sum(profiles.load_series(168, 75.0, "winter"))
        sommer = sum(profiles.load_series(168, 75.0, "sommer"))
        self.assertGreater(winter, sommer)


class TimestampTest(unittest.TestCase):
    """Zeitstempel sind UTC; der Tagesgang folgt trotzdem der Ortszeit."""

    def test_stempel_sind_utc(self):
        for season in profiles.SEASONS:
            first = datetime.fromisoformat(profiles.timestamps(24, season)[0])
            self.assertEqual(first.utcoffset(), timedelta(0), season)

    def test_reihe_beginnt_montags_um_null_uhr_ortszeit(self):
        """In UTC ist das der Sonntagabend — lokal aber Montag 00:00."""
        for season in profiles.SEASONS:
            first = datetime.fromisoformat(profiles.timestamps(24, season)[0])
            local = first.astimezone(ZoneInfo(timezone_name()))
            self.assertEqual(local.weekday(), 0, season)
            self.assertEqual(local.hour, 0, season)

    def test_sommer_und_winter_haben_verschiedene_zeitverschiebungen(self):
        """Der Umweg über UTC muss die Sommerzeit berücksichtigen."""
        winter = datetime.fromisoformat(profiles.timestamps(1, "winter")[0])
        sommer = datetime.fromisoformat(profiles.timestamps(1, "sommer")[0])
        tz = ZoneInfo(timezone_name())
        self.assertNotEqual(winter.astimezone(tz).utcoffset(),
                            sommer.astimezone(tz).utcoffset())

    def test_stempel_folgen_im_stundentakt(self):
        stamps = [datetime.fromisoformat(s) for s in profiles.timestamps(72, "sommer")]
        for earlier, later in zip(stamps, stamps[1:]):
            self.assertEqual((later - earlier).total_seconds(), 3600)

    def test_tagesgang_richtet_sich_nach_der_ortszeit(self):
        """Die Abendspitze liegt um 19 Uhr Ortszeit, nicht um 19 Uhr UTC."""
        last = profiles.load_series(24, 75.0, "winter")
        lokal = profiles.local_hours(24, "winter")
        spitze = lokal[last.index(max(last))]
        self.assertEqual(spitze.hour, 19)

    def test_sonnenhoechststand_liegt_mittags_ortszeit(self):
        werte = profiles.solar_cf_series(24, "sommer")
        lokal = profiles.local_hours(24, "sommer")
        spitze = lokal[werte.index(max(werte))]
        self.assertTrue(11 <= spitze.hour <= 14, spitze.hour)


class SeasonFallbackTest(unittest.TestCase):
    def test_unbekannte_jahreszeit_liefert_den_standard(self):
        self.assertEqual(profiles.season_config("fruehling"),
                         profiles.SEASONS[profiles.DEFAULT_SEASON])

    def test_jede_jahreszeit_ist_vollstaendig_konfiguriert(self):
        needed = {"label", "seed", "start", "load_factor", "sunrise", "sunset",
                  "solar_peak_cf", "wind_mean_cf", "hydro_factor"}
        for name, cfg in profiles.SEASONS.items():
            self.assertTrue(needed.issubset(cfg.keys()), name)
            self.assertLess(cfg["sunrise"], cfg["sunset"], name)


if __name__ == "__main__":
    unittest.main()
