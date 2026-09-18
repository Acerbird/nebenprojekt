"""Konfiguration der beiden Preisvorhersage-Modelle.

Die Modelle stammen aus einem eigenständigen Forschungsprojekt. Nach außen
heißen sie schlicht "Forecast-Modell 1" und "Forecast-Modell 2" — wer die
Website benutzt, muss ihre Funktionsweise nicht kennen.

Die Einstellungen von Modell 2 entsprechen dem dort zuletzt gerechneten Lauf.
Sie hier zu verändern heißt, ein anderes Modell zu rechnen als dort.
"""

import os

DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
TRAINING_DATA = os.path.join(DATA_DIR, "model_df_hour_20181001_20260101.csv.gz")

# Kennungen, unter denen die Vorhersagen in der Datenbank liegen.
BASELINE = "baseline"
MODEL_1 = "model_1"
MODEL_2 = "model_2"
MODEL_3 = "model_3"

MODELS = {
    BASELINE: {
        "label": "Einfache Regel",
        "hint": "derselbe Wochentag der Vorwoche — der Maßstab, den ein Modell schlagen muss",
        "seconds_per_day": 0,
    },
    MODEL_1: {
        "label": "Forecast-Modell 1",
        "hint": "statistisches Modell auf Preishistorie, Brennstoffpreisen und Kalender",
        "seconds_per_day": 1,
    },
    MODEL_2: {
        "label": "Forecast-Modell 2",
        "hint": "nichtlineares Regimemodell — rechnet deutlich länger, trifft meist besser",
        "seconds_per_day": 120,
    },
    MODEL_3: {
        "label": "Forecast-Modell 3",
        "hint": "lineares Modell je Tagesstunde",
        "seconds_per_day": 2,
    },
}

# Die Modelle, die selbst gerechnet werden können. Die übrigen kommen aus
# vorliegenden Ergebnisdateien (siehe import_results.py).
COMPUTABLE = (MODEL_1, MODEL_2)

# Spalten der Ergebnisdateien des Forschungsprojekts und ihre Zuordnung.
RESULT_COLUMNS = {
    "Naive Forecast": BASELINE,
    "Expert Forecast": MODEL_1,
    "LSTR Hourly Forecast": MODEL_2,
    "LR Hourly Forecast": MODEL_3,
}
REALIZED_COLUMN = "Realized Price"

# Unter diesem Namen landet die Preisreihe, auf die die Modelle trainiert
# wurden. Sie entsteht in der Aufbereitung des Forschungsprojekts als Mittel
# der vier viertelstündlichen Day-Ahead-Preise einer Stunde (ENTSO-E, Gebotszone
# DE/LU). Das ist nicht der Stundenkontrakt, den SMARD ausweist: Innerhalb einer
# Stunde laufen die Viertelstundenpreise im Mittel um rund 45 €/MWh auseinander.
# Beide Reihen korrelieren mit etwa 0,96, weichen je Stunde aber um rund
# 9 €/MWh ab. Wer Modelle bewertet, muss sie an der Reihe messen, die sie
# vorhersagen.
REFERENCE_SERIES = "price_reference"

# Einstellungen von Modell 2, übernommen aus dem letzten Lauf des
# Forschungsprojekts. Ohne sie rechnet das Modell etwas anderes.
MODEL_2_FEATURES = {
    "load_wind_solar": True,
    "residual_load": True,
    "residual_load_lags": None,
    "residual_load_lags_mean": False,
    "residual_load_change": [1, 2],
    "residual_load_share": False,
    "residual_load_share_powers": False,
    "fuels_separate": False,
    "fuels_adjusted": True,
    "no_oil": True,
    "fuels_combined": False,
    "imports_exports": True,
    "hour_dummies": False,
    "month_dummies": False,
    "weekday_dummies": False,
    "workday_dummy": True,
    "seasonality_continuous_dummy": True,
}

MODEL_2_SETTINGS = {
    "training_period": 1095,
    "z_name": "Residual Load",
    "z2_name": "Gas Adj d-2",
    "z_as_feature": False,
    "z2_as_feature": True,
    "multiply_g1_into_g2": True,
    "g2_gate": "softmin",
    "softmin_k": 4.0,
    "normalize_features": True,
    "z2_norm_window_days": 365,
    "add_intercept": True,
}

MODEL_1_SETTINGS = {
    "training_period": 84,
    "max_price_lag": 14,
}
