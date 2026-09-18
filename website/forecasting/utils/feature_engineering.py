import pandas as pd
import numpy as np
import datetime as dt
import os
from dateutil.easter import easter
from scipy.interpolate import interp1d
from scipy.stats import norm

# ─── ARX price-lag helpers ────────────────────────────────────────────────────

def daily_min_max(df: pd.DataFrame):
    """
    daily_min_max adds daily maximum and minimum price features from the previous day to df.

    :param df: DataFrame containing at least "Price" and "Date" columns.
    :return: DataFrame with added columns "Max Price d-1" and "Min Price d-1".
    """
    if "Max Price d-1" in df.columns:
        return df

    dmax = df.loc[:, ["Price", "Date"]].groupby("Date").agg("max")["Price"]
    dmin = df.loc[:, ["Price", "Date"]].groupby("Date").agg("min")["Price"]
    df["temp_col"] = df["Date"] - dt.timedelta(days=1)
    df["Max Price d-1"] = df["temp_col"].map(dmax)
    df["Min Price d-1"] = df["temp_col"].map(dmin)
    df.drop(columns=["temp_col"], inplace=True)
    return df


def last_price_yesterday(df: pd.DataFrame):
    """
    last_price_yesterday adds the last price of the previous day as a feature column.

    :param df: DataFrame containing "Price", "Date", and "Time" columns.
    :return: DataFrame with added column "Last Price d-1".
    """
    if "Last Price d-1" in df.columns:
        return df

    gran = len(df[df["Date"] == df.loc[df[df["Time"] == dt.time(0)].index[0], "Date"]])
    temp_df = df[df["Time"] == dt.time(hour=23, minute=(15 * ((gran // 24) - 1)))]
    lpy = temp_df.loc[:, ["Price", "Date"]].groupby("Date").agg("first")["Price"]
    df["temp_col"] = df["Date"] - dt.timedelta(days=1)
    df["Last Price d-1"] = df["temp_col"].map(lpy)
    df.drop(columns=["temp_col"], inplace=True)
    return df


def lagged_prices(df: pd.DataFrame, max_lag=14):
    """
    lagged_prices adds autoregressive price lag features for lags 1 through max_lag days.

    :param df: DataFrame containing "Price", "Date", and "Time" columns.
    :param max_lag: Maximum number of days to lag the price (default 14).
    :return: DataFrame with added columns "Price d-1" through "Price d-{max_lag}".
    """
    if "Price d-1" in df.columns:
        return df

    gran = len(df[df["Date"] == df.loc[df[df["Time"] == dt.time(0)].index[0], "Date"]])
    for lag in range(1, max_lag + 1):
        df[f"Price d-{lag}"] = df["Price"].shift(lag * gran)
    return df


# ─── Commodity and regressor lags ─────────────────────────────────────────────

def comm_prices_two_days_ago(df: pd.DataFrame):
    """
    comm_prices_two_days_ago adds commodity price features (Gas, Coal, Oil, CO2) lagged by two days.

    :param df: DataFrame with columns "Timestamp Berlin", "Gas Price MTF", "Coal Price MTF",
               "Oil Price MTF", "CO2 Price MTF".
    :return: DataFrame with added columns "Gas d-2", "Coal d-2", "Oil d-2", "CO2 d-2".
    """
    comms = {"Gas": "MWh", "Coal": "ton", "Oil": "kL", "CO2": "tCO2"}
    if "Gas d-2" in df.columns:
        return df
    df["temp_col"] = df["Timestamp Berlin"] - dt.timedelta(days=2)
    for comm in comms:
        tcs = (
            df.loc[:, ["Timestamp Berlin", f"{comm} Price MTF"]]
              .groupby("Timestamp Berlin")
              .agg("first")[f"{comm} Price MTF"]
        )
        df[f"{comm} d-2"] = df["temp_col"].map(tcs)

    # Summertime change hour has no commodity prices — fill from the preceding hour
    miss = df[df[[f"{comm} d-2" for comm in comms]].isna().all(axis=1)].index.tolist()
    for comm in comms:
        for m in miss:
            if m >= 192:
                df.loc[m, f"{comm} d-2"] = df.loc[m - 1, f"{comm} d-2"]
    df.drop(columns=["temp_col"], inplace=True)
    return df


def fuel_costs_co2_adjusted(df: pd.DataFrame,
                            eta_gas: float = 0.202,
                            eta_coal: float = 0.341,
                            eta_oil: float = 0.264) -> pd.DataFrame:
    """
    fuel_costs_co2_adjusted adds CO2-adjusted fuel cost features to df.

    The adjusted cost represents the total variable cost of burning one unit of fuel,
    combining the fuel price with the CO2 allowance cost:
        Gas Adj d-2  = Gas d-2  + CO2 d-2 * eta_gas
        Coal Adj d-2 = Coal d-2 + CO2 d-2 * eta_coal
        Oil Adj d-2  = Oil d-2  + CO2 d-2 * eta_oil

    All three inputs must be in consistent units: fuel price in €/MWh_th and CO2 price in
    €/tCO2, so that eta (tCO2/MWh_th) yields an adjusted cost in €/MWh_th.
    Requires comm_prices_two_days_ago to have been applied to df beforehand.

    Default eta values are IPCC 2006 emission factors for combustion:
        eta_gas  = 0.202 tCO2/MWh_th  (natural gas)
        eta_coal = 0.341 tCO2/MWh_th  (hard coal)
        eta_oil  = 0.264 tCO2/MWh_th  (light fuel oil)

    :param df: DataFrame containing columns "Gas d-2", "Coal d-2", "Oil d-2", "CO2 d-2".
    :param eta_gas: CO2 emission factor for natural gas in tCO2/MWh_th (default 0.202).
    :param eta_coal: CO2 emission factor for hard coal in tCO2/MWh_th (default 0.341).
    :param eta_oil: CO2 emission factor for oil in tCO2/MWh_th (default 0.264).
    :return: DataFrame with added columns "Gas Adj d-2", "Coal Adj d-2", "Oil Adj d-2".
    """
    if "Gas Adj d-2" in df.columns:
        return df
    df["Gas Adj d-2"]  = df["Gas d-2"]  + df["CO2 d-2"] * eta_gas
    df["Coal Adj d-2"] = df["Coal d-2"] + df["CO2 d-2"] * eta_coal
    df["Oil Adj d-2"]  = df["Oil d-2"]  + df["CO2 d-2"] * eta_oil
    return df


def potentiating_regressor(df: pd.DataFrame, regressor: str, powers: list):
    """
    potentiating_regressor adds polynomial powers of a given regressor column to df.

    :param df: DataFrame containing the regressor column.
    :param regressor: Name of the column to be potentiated.
    :param powers: List of integer exponents to apply, e.g. [2, 3].
    :return: DataFrame with added columns "{regressor} Power {p}" for each p in powers.
    """
    if powers and f"{regressor} Power {powers[0]}" in df.columns:
        return df

    for power in powers:
        df[f"{regressor} Power {power}"] = df[regressor] ** power
    return df


def lagged_regressor(df: pd.DataFrame, regressor, lags, gran=24):
    """
    lagged_regressor adds time-shifted copies of a regressor column to df.

    :param df: DataFrame containing the regressor column.
    :param regressor: Name of the column to lag.
    :param lags: Explicit list of lag values in time steps, e.g. [1, 2, 3].
    :param gran: Granularity — 24 for hourly data, 96 for quarter-hourly data (default 24).
    :return: DataFrame with added columns "{regressor} Hour-{lag}" (gran=24) or
             "{regressor} Quarter-{lag}" (gran=96).
    """
    if f"{regressor} h-1" in df.columns:
        return df

    if gran == 24:
        factor = 1
    elif gran == 96:
        factor = 4
    else:
        print("Error: Please set 'gran' to 24 for hourly forecasts or 96 for quarter-hourly forecasts.")
        return df
    timestep = "Hour" if gran == 24 else "Quarter"
    for lag in lags:
        df[f"{regressor} {timestep}-{int(lag)}"] = df[regressor].shift(int(lag * factor))
    return df


def residual_load_change(df: pd.DataFrame, lags: list, gran: int = 24):
    """
    residual_load_change adds features capturing the rate-of-change of residual load over
    specified lag horizons.

    For each lag h, the feature is the difference between the current value and the value
    h time-steps ago:
        ΔRL_h           = Residual Load_t        − Residual Load_{t−h}
        ΔRL_Forecast_h  = Residual Load Forecast_t − Residual Load Forecast_{t−h}

    A positive value indicates rising residual load (decreasing renewable share, upward price
    pressure); a negative value indicates falling residual load.

    Compared to raw residual load lags, change features have lower multicollinearity with the
    level and with each other, require fewer coefficients to encode multi-timescale momentum,
    and produce directly interpretable regression coefficients.

    :param df: DataFrame containing "Residual Load" and "Residual Load Forecast" columns,
               which must be computed before calling this function (e.g. via _apply_lr_features).
    :param lags: List of lag lengths in time-steps (hours for gran=24, quarter-hours for gran=96).
                 E.g. [1, 4] computes the 1-hour and 4-hour change.
    :param gran: Granularity — 24 for hourly data, 96 for quarter-hourly data (default 24).
    :return: DataFrame with added columns "RL Change {timestep}-{h}" and
             "RL Forecast Change {timestep}-{h}" for each h in lags.
    """
    if gran not in (24, 96):
        print("Error: Please set 'gran' to 24 for hourly forecasts or 96 for quarter-hourly forecasts.")
        return df

    timestep = "Hour" if gran == 24 else "Quarter"
    factor = 1 if gran == 24 else 4
    check_col = f"RL Change {timestep}-{int(lags[0])}"
    if check_col in df.columns:
        return df

    for lag in lags:
        shift = int(lag * factor)
        df[f"RL Change {timestep}-{int(lag)}"] = (
            df["Residual Load"] - df["Residual Load"].shift(shift)
        )
        df[f"RL Forecast Change {timestep}-{int(lag)}"] = (
            df["Residual Load Forecast"] - df["Residual Load Forecast"].shift(shift)
        )
    return df


# ─── Dummy / categorical feature generators ───────────────────────────────────

def gen_hour_dummies(df: pd.DataFrame):
    """
    gen_hour_dummies adds one-hot encoded dummy variables for each hour of the day to df.

    :param df: DataFrame containing an "Hour" column with integer hour values (0–23).
    :return: DataFrame with added dummy columns "Hour_0" through "Hour_23".
    """
    if "Hour_0" in df.columns:
        return df
    dummies_hour = pd.get_dummies(df["Hour"], dtype=int, prefix="Hour")
    df = pd.concat([df, dummies_hour], axis=1)
    return df


def gen_month_dummies(df: pd.DataFrame):
    """
    gen_month_dummies adds one-hot encoded dummy variables for each calendar month to df.

    :param df: DataFrame containing a "Timestamp Berlin" column (datetime).
    :return: DataFrame with added dummy columns "Month_1" through "Month_12".
    """
    if "Month_0" in df.columns:
        return df
    temp_df = df.copy()
    temp_df["Month"] = temp_df["Timestamp Berlin"].dt.month
    dummies_month = pd.get_dummies(temp_df["Month"], dtype=int, prefix="Month")
    df = pd.concat([df, dummies_month], axis=1)
    return df


def gen_weekday_dummies(df: pd.DataFrame):
    """
    gen_weekday_dummies adds one-hot encoded dummy variables for each weekday to df.

    :param df: DataFrame containing a "Weekday" column (0=Monday … 6=Sunday).
    :return: DataFrame with added dummy columns "Weekday_0" through "Weekday_6".
    """
    if "Weekday_0" in df.columns:
        return df
    dummies_weekday = pd.get_dummies(df["Weekday"], dtype=int, prefix="Weekday")
    df = pd.concat([df, dummies_weekday], axis=1)
    return df


def season_dummies(df: pd.DataFrame):
    """
    season_dummies adds one-hot encoded dummy variables for each meteorological season to df.
    Seasons: Spring=0 (Mar–May), Summer=1 (Jun–Aug), Autumn=2 (Sep–Nov), Winter=3 (Dec–Feb).

    :param df: DataFrame containing a "Timestamp Berlin" column (datetime).
    :return: DataFrame with added dummy columns "Season_0" through "Season_3".
    """
    if "Season_0" in df.columns:
        return df
    season_dict = {1: 3, 2: 3, 3: 0, 4: 0, 5: 0, 6: 1, 7: 1, 8: 1, 9: 2, 10: 2, 11: 2, 12: 3}
    df["Season"] = [season_dict[d.month] for d in df["Timestamp Berlin"]]
    dummies_season = pd.get_dummies(df["Season"], dtype=int, prefix="Season")
    df = pd.concat([df, dummies_season], axis=1)
    return df


def gen_peak_offpeak_dummies(df: pd.DataFrame):
    """
    gen_peak_offpeak_dummies adds hour-based dummy variables to df as a proxy for peak/offpeak periods.

    :param df: DataFrame containing an "Hour" column with integer hour values.
    :return: DataFrame with added dummy columns based on the "Hour" column.
    """
    if "Peak" in df.columns:
        return df
    dummies_peak = pd.get_dummies(df["Hour"], dtype=int, prefix="Hour")
    df = pd.concat([df, dummies_peak], axis=1)
    return df


def gen_holiday_dummies(df: pd.DataFrame):
    """
    gen_holiday_dummies adds one-hot encoded dummy variables for public holidays to df.

    :param df: DataFrame containing a "Holiday" column with integer indicators.
    :return: DataFrame with added dummy columns "Holiday_0" and "Holiday_1".
    """
    if "Holiday" in df.columns:
        return df
    dummies_holiday = pd.get_dummies(df["Holiday"], dtype=int, prefix="Holiday")
    df = pd.concat([df, dummies_holiday], axis=1)
    return df


# ─── Workday / holiday helpers ────────────────────────────────────────────────

def german_holidays(year: int):
    """
    german_holidays returns German federal public holidays for a given year.

    :param year: Calendar year (e.g. 2024).
    :return: List of datetime.date objects representing German public holidays for that year.
    """
    e = easter(year)
    return [
        dt.date(year, 1, 1),
        e - dt.timedelta(days=2),
        e + dt.timedelta(days=1),
        dt.date(year, 5, 1),
        e + dt.timedelta(days=39),
        e + dt.timedelta(days=50),
        dt.date(year, 10, 3),
        dt.date(year, 12, 25),
        dt.date(year, 12, 26),
    ]


def german_holidays_2015_to_2025():
    """
    german_holidays_2015_to_2025 returns all German public holidays from 2015 to 2025, reading
    from a cache file if it exists and creating it otherwise.

    :return: List of strings in "YYYY-MM-DD" format representing holiday dates.
    """
    if not os.path.exists("german_holidays_2015_to_2025.txt"):
        with open("german_holidays_2015_to_2025.txt", "w") as f:
            for year in range(2015, 2026):
                holidays = german_holidays(year)
                f.write(f"{','.join(map(str,holidays))}")
    with open("german_holidays_2015_to_2025.txt", "r") as f:
        all_holidays = f.read().split(",")
    return all_holidays


def gen_workday_dummy(df: pd.DataFrame):
    """
    gen_workday_dummy adds a binary workday indicator column to df (1 if workday, 0 otherwise).
    A workday is defined as Monday–Friday excluding German public holidays from 2015 to 2025.

    :param df: DataFrame containing "Timestamp Berlin" and "Weekday" columns.
    :return: DataFrame with added column "Workday".
    """
    if "Workday" in df.columns:
        return df
    HOLIDAYS = german_holidays_2015_to_2025()
    df["Holiday"] = (df["Timestamp Berlin"].dt.date.isin(HOLIDAYS)).astype(int)
    df["Workday"] = ((df["Weekday"] < 5) & (df["Holiday"] == 0)).astype(int)
    df = df.drop(columns=["Holiday"])
    return df


def gen_annual_sincos(df: pd.DataFrame):
    """
    gen_annual_sincos adds sine and cosine annual seasonality features to df.

    Both features encode the position within the calendar year as a continuous cycle,
    using the first-order Fourier pair: sin(2π * doy / T) and cos(2π * doy / T),
    where doy is the day-of-year and T is 365 or 366 for leap years.

    Together the two columns span a linear subspace that can represent any annual
    periodic signal (any amplitude and phase) through ordinary least-squares regression,
    without requiring the modeller to choose a phase upfront.

    :param df: DataFrame containing a "Timestamp Berlin" column (datetime).
    :return: DataFrame with added columns "Seasonality Sin" and "Seasonality Cos"
             (values in [-1, 1]).
    """
    if "Seasonality Sin" in df.columns:
        return df

    leap = df["Timestamp Berlin"].dt.is_leap_year
    day_of_year = df["Timestamp Berlin"].dt.dayofyear
    cycle = 2 * np.pi * day_of_year / (365 + leap)
    df["Seasonality Sin"] = np.sin(cycle)
    df["Seasonality Cos"] = np.cos(cycle)
    return df


def gen_seasonality_dummies(df: pd.DataFrame):
    """
    gen_seasonality_dummies adds a continuous cosine-based annual seasonality feature to df.

    :param df: DataFrame containing a "Timestamp Berlin" column (datetime).
    :return: DataFrame with added column "Seasonality" (values in [-1, 1]).
    """
    if "Seasonality" in df.columns:
        return df
    df["LeapYear"] = df["Timestamp Berlin"].dt.is_leap_year
    df["DayOfYear"] = df["Timestamp Berlin"].dt.dayofyear
    df["Seasonality"] = np.cos(2 * np.pi * df["DayOfYear"] / (365 + df["LeapYear"]))
    df.drop(columns=["LeapYear", "DayOfYear"], inplace=True)
    return df


def price_normalization(df: pd.DataFrame, method="(median,MAD)", target_col="Price"):
    """
    price_normalization applies a specified normalization method to the "Price" column of df.

    Supported methods:
    - "(median,MAD)": Median and Median Absolute Deviation (MAD) normalization, robust to outliers.
    - "(mean,std)": Mean and standard deviation normalization (z-score), sensitive to outliers.

    :param df: DataFrame containing a "Price" column.
    :param method: Normalization method to apply (default "(median,MAD)").
    :param target_col: Name of the column to normalize (default "Price").
    :return: DataFrame with normalized "Price" column.
    """
    if method == "(median,MAD)":
        a = df[target_col].median()
        b = np.median(np.abs(df[target_col] - a)) * 1.4826 # Scale MAD to be consistent with std for normal distribution
        df[target_col] = (df[target_col] - a) / b
    elif method == "(mean,std)":
        a = df[target_col].mean()
        b = df[target_col].std()
        df[target_col] = (df[target_col] - a) / b
    else:
        print("Error: Unsupported normalization method. Use '(median,MAD)' or '(mean,std)'.")
    return df, a, b

def price_denormalization(df: pd.DataFrame, target_col="Price", a=0, b=1):
    """
    price_denormalization applies a specified denormalization method to the "Price" column of df.

    Supported methods:
    - "(median,MAD)": Median and Median Absolute Deviation (MAD) denormalization, robust to outliers.
    - "(mean,std)": Mean and standard deviation denormalization (z-score), sensitive to outliers.

    :param df: DataFrame containing a "Price" column.
    :param target_col: Name of the column to denormalize (default "Price").
    :return: DataFrame with denormalized "Price" column.
    """
    df[target_col] = df[target_col] * b + a
    return df


def transform_target_func(df: pd.DataFrame, method="asinh", target_col="Price", c_asinh=1):
    """
    transform_target_func applies a specified transformation to the target variable column of df.

    Supported methods:
    - "asinh": Inverse hyperbolic sine transformation, which behaves like log for large values and is defined at zero.
    - "N-PIT": Normalized Probability Integral Transform.

    :param df: DataFrame containing the target column.
    :param method: Transformation method to apply (default "asinh").
    :param target_col: Name of the column to transform (default "Price").
    :return: DataFrame with transformed target column.
    """
    z_train = None
    if method == "asinh":
        df[target_col] = np.arcsinh(c_asinh * df[target_col])
    elif method == "N-PIT":
        z_train = np.asarray(df[target_col])
        z_sorted = np.sort(z_train)
        n = len(z_sorted)
        F_emp = np.arange(1, n + 1) / (n + 1)
        F_emp_func = interp1d(z_sorted, F_emp, kind="linear", bounds_error=False, fill_value=(0.0, 1.0))
        u_train = F_emp_func(z_train)
        df[target_col] = norm.ppf(u_train)
    else:
        print("Error: Unsupported transformation method. Use 'asinh' or 'N-PIT'.")
    return df, z_train


def detransform_target_func(df: pd.DataFrame, method="asinh", target_col="Price", z_train=None, c_asinh=1):
    """
    detransform_target_func applies the inverse of a specified transformation to the target variable column of df.

    Supported methods:
    - "asinh": Hyperbolic sine function, the inverse of arcsinh.
    - "N-PIT": Normalized Probability Integral Transform.

    :param df: DataFrame containing the target column.
    :param method: Detransformation method to apply (default "asinh").
    :param target_col: Name of the column to detransform (default "Price").
    :return: DataFrame with detransformed target column.
    """
    if method == "asinh":
        df[target_col] = (1 / c_asinh) * np.sinh(df[target_col])
    elif method == "N-PIT":
        if z_train is None:
            print("Error: z_train must be provided for N-PIT detransformation.")
            return df
        z_sorted = np.sort(z_train)
        F_emp_inv = np.arange(1, len(z_sorted) + 1) / (len(z_sorted) + 1)
        F_emp_inv_func = interp1d(F_emp_inv, z_sorted, kind="linear", bounds_error=False, fill_value=(z_sorted[0], z_sorted[-1]))
        df[target_col] = F_emp_inv_func(norm.cdf(df[target_col]))
    else:
        print("Error: Unsupported detransformation method. Use 'asinh' or 'N-PIT'.")
    return df