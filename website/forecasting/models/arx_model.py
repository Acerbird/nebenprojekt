import pandas as pd
import datetime as dt
import pytz
import statsmodels.api as sm

from utils.feature_engineering import (
    daily_min_max, last_price_yesterday, lagged_prices,
    comm_prices_two_days_ago, gen_weekday_dummies, season_dummies
)


def _validate_and_prepare(df, forecast_day, gran, training_period, max_feature_lag, columns_needed):
    """
    Validates inputs, copies df, parses Time/Date, selects columns, and adjusts gran for DST.

    :param df: Raw input DataFrame.
    :param forecast_day: Forecast target date.
    :param gran: Granularity (24 or 96).
    :param training_period: Number of training days.
    :param max_feature_lag: Maximum lag used in feature construction (days).
    :param columns_needed: List of required column names.
    :return: Tuple (data_df, gran_adj) or None if validation fails.
    """
    if not all(col in df.columns for col in columns_needed):
        print("Error: The data does not contain all needed features for the expert model.")
        for col in columns_needed:
            if col not in df.columns:
                print(f"Missing column: {col}")
        return None
    if pytz.timezone("Europe/Berlin").localize(forecast_day) - dt.timedelta(days=training_period + max_feature_lag) < df["Timestamp Berlin"].min():
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


def _build_arx_features(data_df, forecast_day, gran_adj, training_period, max_price_lag, training_columns, test_columns):
    """
    Applies ARX feature engineering and splits into train/test design matrices.

    :param data_df: Prepared DataFrame (parsed, column-selected).
    :param forecast_day: Forecast target date.
    :param gran_adj: DST-adjusted granularity.
    :param training_period: Number of training days.
    :param max_price_lag: Maximum autoregressive price lag in days.
    :param training_columns: Column names used for the training set.
    :param test_columns: Column names used for the test set.
    :return: Tuple (train_Y, train_X_sm, test_df, test_Y, test_X_sm).
    """
    max_feature_lag = max(max_price_lag, 2)
    data_df = daily_min_max(data_df)
    data_df = last_price_yesterday(data_df)
    data_df = lagged_prices(data_df, max_lag=max_price_lag)
    data_df = comm_prices_two_days_ago(data_df)
    data_df = gen_weekday_dummies(data_df)
    data_df = season_dummies(data_df)
    cutoff = gran_adj * max_feature_lag
    data_df = data_df.loc[cutoff:, :].copy()
    data_df.reset_index(inplace=True, drop="index")
    forecast_day_first_index = data_df[data_df["Date"] == forecast_day.date()].index[0]
    training_period_start_index = forecast_day_first_index - (training_period * gran_adj)
    train_df = data_df.loc[training_period_start_index:forecast_day_first_index, training_columns]
    train_Y = train_df["Price"]
    train_X = train_df.drop(columns=["Price", "Timestamp Berlin", "Date"])
    train_X_sm = sm.add_constant(train_X, has_constant="add")
    test_df = data_df.loc[forecast_day_first_index:(forecast_day_first_index + gran_adj - 1), test_columns]
    test_Y = test_df["Price"]
    test_X = test_df.drop(columns=["Price", "Timestamp Berlin", "Date"])
    test_X_sm = sm.add_constant(test_X, has_constant="add")
    return train_Y, train_X_sm, test_df, test_Y, test_X_sm


def _fit_ols(train_Y, train_X_sm):
    """
    Fits an OLS model and returns the fitted results object.

    :param train_Y: Target variable Series for training.
    :param train_X_sm: Feature matrix with constant column for training.
    :return: Fitted statsmodels OLS results.
    """
    return sm.OLS(train_Y, train_X_sm).fit()


def _create_forecast(results, test_X_sm, test_df, test_Y, output_col_name):
    """
    Generates predictions and assembles the forecast output DataFrame.

    :param results: Fitted OLS results object.
    :param test_X_sm: Feature matrix with constant for the test period.
    :param test_df: Test DataFrame containing the Timestamp Berlin column.
    :param test_Y: Actual prices for the test period.
    :param output_col_name: Column name for the forecast values in the output.
    :return: Tuple (forecast_df, regression_summary) where forecast_df has columns
             "Timestamp Berlin", output_col_name, "Real Price".
    """
    forecast_values = results.predict(test_X_sm)
    output = test_df[["Timestamp Berlin"]].reset_index(drop=True)
    output[output_col_name] = forecast_values.to_numpy().round(2)
    output["Real Price"] = test_Y.to_numpy()
    return output, results.summary()


def expert_forecast(df: pd.DataFrame, forecast_day: dt.datetime, gran=24, training_period=(7*12), bidding_zone="DE_LU", max_price_lag=14):
    """
    expert_forecast produces a day-ahead electricity price forecast using an ARX model with
    commodity prices, lagged electricity prices, and seasonal dummy variables.

    :param df: DataFrame with columns including "Timestamp Berlin", "Date", "Time", "Weekday", "Year",
               "Price", "Load", "Load Forecast", "Solar and Wind", "Solar and Wind Generation Forecast",
               "Coal Price MTF", "Gas Price MTF", "Oil Price MTF", "CO2 Price MTF".
    :param forecast_day: Date for which the forecast shall be produced.
    :param gran: Granularity — 24 for hourly, 96 for quarter-hourly (default 24).
    :param training_period: Number of days used for model training before forecast_day (default 84).
    :param bidding_zone: Bidding zone identifier (default "DE_LU").
    :param max_price_lag: Maximum autoregressive price lag in days (default 14).
    :return: Tuple of (forecast_df, regression_results) where forecast_df has columns
             "Timestamp Berlin", "Expert Forecast", "Real Price" and regression_results is the OLS summary.
    """
    MAX_PRICE_LAG = max_price_lag
    MAX_FEATURE_LAG = max(MAX_PRICE_LAG, 2)

    COLUMNS_NEEDED = [
        "Timestamp Berlin", "Price", "Date", "Time", "Weekday", "Year",
        "Load", "Load Forecast", "Solar and Wind", "Solar and Wind Generation Forecast",
        "Coal Price MTF", "Gas Price MTF", "Oil Price MTF", "CO2 Price MTF"
    ]
    if gran == 96:
        COLUMNS_NEEDED.append("Minutes")

    TRAINING_COLUMNS = [
        "Timestamp Berlin", "Date", "Price", "Load", "Solar and Wind",
        "Max Price d-1", "Min Price d-1", "Last Price d-1"
    ]
    TRAINING_COLUMNS.extend([f"Price d-{i}" for i in range(1, MAX_PRICE_LAG + 1)])
    TRAINING_COLUMNS.extend([f"{comm} d-2" for comm in ["Gas", "Coal", "Oil", "CO2"]])
    TRAINING_COLUMNS.extend([f"Weekday_{i}" for i in range(7)])
    TRAINING_COLUMNS.extend([f"Season_{i}" for i in range(4)])

    TEST_COLUMNS = [
        "Timestamp Berlin", "Date", "Price", "Load Forecast", "Solar and Wind Generation Forecast",
        "Max Price d-1", "Min Price d-1", "Last Price d-1"
    ]
    TEST_COLUMNS.extend([f"Price d-{i}" for i in range(1, MAX_PRICE_LAG + 1)])
    TEST_COLUMNS.extend([f"{comm} d-2" for comm in ["Gas", "Coal", "Oil", "CO2"]])
    TEST_COLUMNS.extend([f"Weekday_{i}" for i in range(7)])
    TEST_COLUMNS.extend([f"Season_{i}" for i in range(4)])

    # data_import
    result = _validate_and_prepare(df, forecast_day, gran, training_period, MAX_FEATURE_LAG, COLUMNS_NEEDED)
    if result is None:
        return
    data_df, gran_adj = result

    # feature_building
    train_Y, train_X_sm, test_df, test_Y, test_X_sm = _build_arx_features(
        data_df, forecast_day, gran_adj, training_period, MAX_PRICE_LAG, TRAINING_COLUMNS, TEST_COLUMNS
    )

    # fitting
    results = _fit_ols(train_Y, train_X_sm)

    # predicting
    return _create_forecast(results, test_X_sm, test_df, test_Y, "Expert Forecast")
