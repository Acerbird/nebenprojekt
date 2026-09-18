import pandas as pd
import numpy as np
import datetime as dt
import pytz
import statsmodels.api as sm

from utils.feature_engineering import (
    potentiating_regressor, lagged_regressor, comm_prices_two_days_ago, fuel_costs_co2_adjusted,
    gen_hour_dummies, gen_month_dummies, gen_weekday_dummies,
    gen_workday_dummy, gen_annual_sincos, residual_load_change,
    price_normalization, price_denormalization,
    transform_target_func, detransform_target_func
)


def _validate_and_prepare(df, forecast_day, gran, training_period, columns_needed):
    """
    Validates inputs, copies df, parses Time/Date columns, selects needed columns,
    and adjusts gran for DST days.

    :param df: Raw input DataFrame.
    :param forecast_day: Forecast target date.
    :param gran: Granularity (24 or 96).
    :param training_period: Number of training days required.
    :param columns_needed: List of required column names.
    :return: Tuple (data_df, gran_adj) or None if validation fails.
    """
    if not all(col in df.columns for col in columns_needed):
        print("Error: The data does not contain all needed features for the expert model.")
        for col in columns_needed:
            if col not in df.columns:
                print(f"Missing column: {col}")
        return None
    if pytz.timezone("Europe/Berlin").localize(forecast_day) - dt.timedelta(days=training_period) < df["Timestamp Berlin"].min():
        print("Error: The data does not contain enough datapoints for the given training period.")
        return None
    if gran != 24 and gran != 96:
        print("Error: Please set 'gran' to 24 for hourly forecasts or 96 for quarter-hourly forecasts.")
        return None
    data_df = df.copy()
    data_df['Time'] = pd.to_datetime(data_df['Time'], format='%H:%M:%S').dt.time
    data_df['Date'] = data_df['Date'].dt.date
    data_df = data_df.loc[:, columns_needed]
    gran_adj = gran
    if len(data_df[data_df["Date"] == forecast_day.date()]) != gran:
        gran_adj = len(data_df[data_df["Date"] == forecast_day.date()])
    return data_df, gran_adj


def _apply_lr_features(data_df, features, gran):
    """
    Applies feature engineering to data_df based on the features flag dictionary.
    Handles residual load computation, lags, polynomial powers, fuel prices, and dummy variables.

    :param data_df: Prepared DataFrame with at least Load, Solar, Wind, and fuel price columns.
    :param features: Dict of boolean feature flags (keys: "load_wind_solar", "residual_load",
                     "residual_load_lags", "residual_load_share", "residual_load_share_powers",
                     "fuels_separate", "fuels_combined", "hour_dummies", "month_dummies",
                     "weekday_dummies", "workday_dummy", "seasonality_continuous_dummy").
    :param gran: Granularity used for lagged regressor computations.
    :return: data_df with all requested feature columns added.
    """
    needs_rl = (features["residual_load"] or features["residual_load_lags"] or
                features["residual_load_share"] or features["residual_load_share_powers"] or
                bool(features["residual_load_change"]))
    if needs_rl:
        data_df["Residual Load"] = (data_df["Load"]
                                    - data_df["Solar"] - data_df["Wind Offshore"] - data_df["Wind Onshore"])
        data_df["Residual Load Forecast"] = (data_df["Load Forecast"]
                                              - data_df["Solar Generation Forecast"]
                                              - data_df["Wind Offshore Generation Forecast"]
                                              - data_df["Wind Onshore Generation Forecast"])
    if features["residual_load_lags"]:
        data_df = lagged_regressor(data_df, "Residual Load", lags=features["residual_load_lags"], gran=gran)
        data_df = lagged_regressor(data_df, "Residual Load Forecast", lags=features["residual_load_lags"], gran=gran)
    if features["residual_load_change"]:
        data_df = residual_load_change(data_df, lags=features["residual_load_change"], gran=gran)
    if features["residual_load_lags_mean"]:
        print("Error: 'residual_load_lags_mean' feature is not implemented yet.")
    if features["residual_load_share"] or features["residual_load_share_powers"]:
        data_df["Residual Load Share"] = data_df["Residual Load"] / data_df["Load"]
        data_df["Residual Load Forecast Share"] = data_df["Residual Load Forecast"] / data_df["Load Forecast"]
    if features["residual_load_share_powers"]:
        data_df = potentiating_regressor(data_df, "Residual Load Share", features["residual_load_share_powers"])
        data_df = potentiating_regressor(data_df, "Residual Load Forecast Share", features["residual_load_share_powers"])
    if needs_rl and not features["residual_load"]:
        cols_to_drop = [c for c in ["Residual Load", "Residual Load Forecast"] if c in data_df.columns]
        data_df.drop(columns=cols_to_drop, inplace=True)
    if features["fuels_separate"]:
        data_df = comm_prices_two_days_ago(data_df)
    if features.get("fuels_adjusted"):
        data_df = comm_prices_two_days_ago(data_df)
        data_df = fuel_costs_co2_adjusted(data_df)
    if features["fuels_combined"]:
        print("Error: 'fuels_combined' feature is not implemented yet.")
    if features["hour_dummies"]:
        data_df = gen_hour_dummies(data_df)
    if features["month_dummies"]:
        data_df = gen_month_dummies(data_df)
    if features["weekday_dummies"]:
        data_df = gen_weekday_dummies(data_df)
    if features["workday_dummy"]:
        data_df = gen_workday_dummy(data_df)
    if features["seasonality_continuous_dummy"]:
        data_df = gen_annual_sincos(data_df)
    return data_df


def _hourly_setup(data_df, forecast_day, training_period, gran_orig, output_col_name, extra_cols=None):
    """
    Trims data_df to the training window, builds the output scaffold DataFrame, and
    extracts the unique delivery times for per-time-of-day modelling.

    :param data_df: Feature-engineered DataFrame with Date and Time columns.
    :param forecast_day: Forecast target date.
    :param training_period: Number of training days.
    :param gran_orig: Original (unadjusted) granularity, used for the initial cutoff.
    :param output_col_name: Column name for forecast values in the output scaffold.
    :param extra_cols: Optional list of additional column names to include in the scaffold.
    :return: Tuple (data_df, times, scaffold, forecast_day_first_index, training_period_start_index).
    """
    data_df = data_df.loc[gran_orig:, :].copy()
    fdfi = data_df[data_df["Date"] == forecast_day.date()].index[0]
    tpsi = data_df[data_df["Date"] == (forecast_day - dt.timedelta(days=training_period)).date()].index[0]
    data_df = data_df.loc[tpsi:, :].copy()
    scaffold = data_df[data_df["Date"] == forecast_day.date()][["Timestamp Berlin"]].reset_index(drop=True)
    scaffold["Time"] = scaffold["Timestamp Berlin"].dt.time
    scaffold[output_col_name] = np.nan
    scaffold["Real Price"] = np.nan
    if extra_cols:
        for col in extra_cols:
            scaffold[col] = np.nan
    times = scaffold["Time"].unique()
    return data_df, times, scaffold, fdfi, tpsi


def _fit_hourly_ols(data_df, times, fdfi, tpsi, training_columns, goal_variable="Price"):
    """
    Fits a separate OLS model for each time-of-day using the training window.

    :param data_df: DataFrame trimmed to the training period, with Time column.
    :param times: Array of unique time values to model.
    :param fdfi: Index of the first row of the forecast day.
    :param tpsi: Index of the first row of the training period.
    :param training_columns: Column names for the training design matrix.
    :return: Dict mapping each time-of-day to its fitted OLS results object.
    """
    fitted_time_models = {}
    for time in times:
        data_time_df = data_df[data_df["Time"] == time].copy()
        train_df = data_time_df.loc[tpsi:fdfi, training_columns].copy()
        train_Y = train_df[goal_variable].copy()
        train_X = train_df.drop(columns=[goal_variable, "Timestamp Berlin", "Date", "Time"])
        train_X_sm = sm.add_constant(train_X, has_constant="add")
        fitted_time_models[time] = sm.OLS(train_Y, train_X_sm).fit()
    return fitted_time_models


def _predict_hourly(data_df, fitted_time_models, times, gran_adj, fdfi, test_columns, scaffold, output_col_name):
    """
    Generates per-time-of-day forecasts and fills the output scaffold DataFrame.
    If the scaffold contains a "Residual Load Share (Forecast)" column, it is filled automatically.

    :param data_df: DataFrame trimmed to the training period, with Time column.
    :param fitted_time_models: Dict of {time: fitted OLS results} from _fit_hourly_ols.
    :param times: Array of unique times in the forecast day.
    :param gran_adj: DST-adjusted granularity for test set slicing.
    :param fdfi: Index of the first row of the forecast day.
    :param test_columns: Column names for the test design matrix.
    :param scaffold: Output DataFrame pre-initialised with Timestamp and Time columns.
    :param output_col_name: Column name for the forecast values.
    :return: Tuple (scaffold, regression_results_list).
    """
    regression_results = []
    for time in times:
        results = fitted_time_models[time]
        data_time_df = data_df[data_df["Time"] == time].copy()
        test_df = data_time_df.loc[fdfi:fdfi + gran_adj - 1, test_columns].copy()
        test_Y = test_df["Price"]
        test_X = test_df.drop(columns=["Price", "Timestamp Berlin", "Date", "Time"])
        test_X_sm = sm.add_constant(test_X, has_constant="add")
        forecast_value = results.predict(test_X_sm)
        i = scaffold[scaffold["Time"] == time].index
        scaffold.loc[i, output_col_name] = forecast_value.values[0].round(2)
        scaffold.loc[i, "Real Price"] = test_Y.values[0]
        regression_results.append(results.summary())
        if "Residual Load Share (Forecast)" in scaffold.columns:
            scaffold.loc[i, "Residual Load Share (Forecast)"] = test_df["Residual Load Share Forecast"].values
    return scaffold, regression_results


###########################################################################################################
### --- HOURLY MODEL --- ###
###########################################################################################################


def lr_hourly_forecast(df: pd.DataFrame, forecast_day: dt.datetime, gran=24, training_period=365,
                       bidding_zone="Price", features: dict = None, 
                       transform_target: bool = False, transform_target_method: str = "asinh", c_asinh: float = 1,
                       normalization_method: str = "(median,MAD)"):
    """
    lr_hourly_forecast predicts electricity prices for each delivery time of forecast_day by fitting
    a separate OLS regression per time-of-day using a configurable set of features.

    :param df: DataFrame with at minimum columns "Timestamp Berlin", bidding_zone, "Date", "Time", "Hour",
               "Weekday", "Year", "Load", "Load Forecast", "Solar", "Wind Offshore", "Wind Onshore",
               their forecast counterparts, and fuel price MTF columns.
    :param forecast_day: Date for which the forecast shall be produced.
    :param gran: Granularity — 24 for hourly, 96 for quarter-hourly (default 24).
    :param training_period: Number of days used for model training (default 365).
    :param bidding_zone: Name of the price column in df (default "Price").
    :param features: Dictionary of boolean feature flags controlling which regressors to include.
                     Keys: "load_wind_solar", "residual_load", "residual_load_lags",
                     "residual_load_lags_mean", "residual_load_change", "residual_load_share",
                     "residual_load_share_powers", "fuels_separate", "fuels_combined",
                     "imports_exports", "hour_dummies", "month_dummies", "weekday_dummies",
                     "workday_dummy", "seasonality_continuous_dummy".
                     "residual_load_change" accepts a list of lag lengths in hours (e.g. [1, 4])
                     or False to disable.
    :param asinh_transform: If True, applies arcsinh to the price target before fitting and
                            sinh to the predictions after fitting to back-transform the forecasts.
                            Useful for handling negative prices and heavy tails (default False).
    :return: Tuple of (forecast_df, regression_results_list) where forecast_df has columns
             "Timestamp Berlin", "LR Hourly Forecast", "Real Price" and regression_results_list contains
             the OLS summary for each time-of-day regression.
    """
    HOURS = 24
    TIMESTEP = "Hour" if gran == 24 else "Quarter"

    COLUMNS_NEEDED = [
        "Timestamp Berlin", bidding_zone, "Date", "Time", "Hour", "Weekday", "Year",
        "Load", "Load Forecast",
        "Solar", "Solar Generation Forecast",
        "Wind Offshore", "Wind Offshore Generation Forecast",
        "Wind Onshore", "Wind Onshore Generation Forecast",
        "Coal Price MTF", "Gas Price MTF", "Oil Price MTF", "CO2 Price MTF"
    ]
    if gran == 96:
        COLUMNS_NEEDED.append("Minutes")

    if features is None:
        features = {
            "load_wind_solar": True, "residual_load": False, "residual_load_lags": False,
            "residual_load_lags_mean": False, "residual_load_change": False,
            "residual_load_share": False, "residual_load_share_powers": False,
            "fuels_separate": False, "fuels_adjusted": False, "fuels_combined": False,
            "imports_exports": False, "hour_dummies": False, "month_dummies": False,
            "weekday_dummies": False, "workday_dummy": False, "seasonality_continuous_dummy": False
        }

    if features["imports_exports"]:
        COLUMNS_NEEDED.append("Net Position Trade")

    TRAINING_COLUMNS = ["Timestamp Berlin", "Date", "Time", "Price"]
    TEST_COLUMNS = ["Timestamp Berlin", "Date", "Time", "Price"]

    if features["load_wind_solar"]:
        TRAINING_COLUMNS.extend(["Load", "Solar", "Wind Offshore", "Wind Onshore"])
        TEST_COLUMNS.extend(["Load Forecast", "Solar Generation Forecast",
                             "Wind Offshore Generation Forecast", "Wind Onshore Generation Forecast"])
    if features["residual_load"]:
        TRAINING_COLUMNS.append("Residual Load")
        TEST_COLUMNS.append("Residual Load Forecast")
    if features["residual_load_lags"]:
        TRAINING_COLUMNS.extend([f"Residual Load {TIMESTEP}-{int(lag)}" for lag in features["residual_load_lags"]])
        TEST_COLUMNS.extend([f"Residual Load Forecast {TIMESTEP}-{int(lag)}" for lag in features["residual_load_lags"]])
    if features["residual_load_lags_mean"]:
        TRAINING_COLUMNS.append("Residual Load Lags Mean")
        TEST_COLUMNS.append("Residual Load Forecast Lags Mean")
    if features["residual_load_change"]:
        TRAINING_COLUMNS.extend([f"RL Change {TIMESTEP}-{int(lag)}" for lag in features["residual_load_change"]])
        TEST_COLUMNS.extend([f"RL Forecast Change {TIMESTEP}-{int(lag)}" for lag in features["residual_load_change"]])
    if features["residual_load_share"]:
        TRAINING_COLUMNS.append("Residual Load Share")
        TEST_COLUMNS.append("Residual Load Forecast Share")
    if features["residual_load_share_powers"]:
        TRAINING_COLUMNS.extend([f"Residual Load Share Power {p}" for p in features["residual_load_share_powers"]])
        TEST_COLUMNS.extend([f"Residual Load Forecast Share Power {p}" for p in features["residual_load_share_powers"]])
    if features["fuels_separate"]:
        if features["no_oil"]:
            TRAINING_COLUMNS.extend([f"{comm} d-2" for comm in ["Gas", "Coal", "CO2"]])
            TEST_COLUMNS.extend([f"{comm} d-2" for comm in ["Gas", "Coal", "CO2"]])
        else:
            TRAINING_COLUMNS.extend([f"{comm} d-2" for comm in ["Gas", "Coal", "Oil", "CO2"]])
            TEST_COLUMNS.extend([f"{comm} d-2" for comm in ["Gas", "Coal", "Oil", "CO2"]])
    if features.get("fuels_adjusted"):
        comms_adj = ["Gas Adj", "Coal Adj"] if features.get("no_oil", False) else ["Gas Adj", "Coal Adj", "Oil Adj"]
        TRAINING_COLUMNS.extend([f"{comm} d-2" for comm in comms_adj])
        TEST_COLUMNS.extend([f"{comm} d-2" for comm in comms_adj])
    if features["fuels_combined"]:
        TRAINING_COLUMNS.append("Fuel Index")
        TEST_COLUMNS.append("Fuel Index")
    if features["imports_exports"]:
        TRAINING_COLUMNS.append("Net Position Trade")
        TEST_COLUMNS.append("Net Position Trade")
    if features["hour_dummies"]:
        TRAINING_COLUMNS.extend([f"Hour_{i}" for i in range(0, HOURS)])
        TEST_COLUMNS.extend([f"Hour_{i}" for i in range(0, HOURS)])
    if features["month_dummies"]:
        TRAINING_COLUMNS.extend([f"Month_{i}" for i in range(1, 13)])
        TEST_COLUMNS.extend([f"Month_{i}" for i in range(1, 13)])
    if features["weekday_dummies"]:
        TRAINING_COLUMNS.extend([f"Weekday_{i}" for i in range(0, 7)])
        TEST_COLUMNS.extend([f"Weekday_{i}" for i in range(0, 7)])
    if features["workday_dummy"]:
        TRAINING_COLUMNS.append("Workday")
        TEST_COLUMNS.append("Workday")
    if features["seasonality_continuous_dummy"]:
        TRAINING_COLUMNS.extend(["Seasonality Sin", "Seasonality Cos"])
        TEST_COLUMNS.extend(["Seasonality Sin", "Seasonality Cos"])

    # data_import
    result = _validate_and_prepare(df, forecast_day, gran, training_period, COLUMNS_NEEDED)
    if result is None:
        return
    data_df, gran_adj = result

    # feature_building
    data_df = _apply_lr_features(data_df, features, gran)
    data_df, times, scaffold, fdfi, tpsi = _hourly_setup(
        data_df, forecast_day, training_period, gran, "LR Hourly Forecast"
    )

    if transform_target:
        real_prices = data_df[data_df["Date"] == forecast_day.date()]["Price"].values
        if normalization_method:
            data_df, a, b = price_normalization(data_df, method=normalization_method, target_col=bidding_zone)
        data_df, z_train = transform_target_func(data_df, method=transform_target_method, target_col=bidding_zone, c_asinh=c_asinh)

    # fitting
    fitted_time_models = _fit_hourly_ols(data_df, times, fdfi, tpsi, TRAINING_COLUMNS)

    # predicting
    scaffold, regression_results = _predict_hourly(
        data_df, fitted_time_models, times, gran_adj, fdfi, TEST_COLUMNS, scaffold, "LR Hourly Forecast"
    )
    if transform_target:
        scaffold = detransform_target_func(scaffold, method=transform_target_method, target_col="LR Hourly Forecast", z_train=z_train, c_asinh=c_asinh)
        if normalization_method:
            scaffold = price_denormalization(scaffold, target_col="LR Hourly Forecast", a=a, b=b)
        scaffold["Real Price"] = real_prices

    return scaffold, regression_results
