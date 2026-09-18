import numpy as np
import pandas as pd


def mean_absolute_error(real_prices: pd.Series, forecasted_prices: pd.Series, rounding: int = 2):
    """
    mean_absolute_error computes the mean absolute error between real and forecasted prices.

    :param real_prices: Series of realized electricity prices.
    :param forecasted_prices: Series of forecasted electricity prices.
    :param rounding: Number of decimal places to round the result to.
    :return: Mean absolute error rounded to the specified number of decimal places.
    """
    if len(real_prices) != len(forecasted_prices):
        print("Error: The length of the given columns is not equal.")
    n = len(real_prices)
    s = sum(np.abs(rp - fp) for rp, fp in zip(real_prices, forecasted_prices))
    return round(s / n, rounding)


def mean_squared_error(real_prices: pd.Series, forecasted_prices: pd.Series, rounding: int = 2):
    """
    mean_squared_error computes the mean squared error between real and forecasted prices.

    :param real_prices: Series of realized electricity prices.
    :param forecasted_prices: Series of forecasted electricity prices.
    :param rounding: Number of decimal places to round the result to.
    :return: Mean squared error rounded to the specified number of decimal places.
    """
    if len(real_prices) != len(forecasted_prices):
        print("Error: The length of the given columns is not equal.")
    n = len(real_prices)
    s = sum((rp - fp) ** 2 for rp, fp in zip(real_prices, forecasted_prices))
    return round(s / n, rounding)


def root_mean_squared_error(real_prices: pd.Series, forecasted_prices: pd.Series, rounding: int = 2):
    """
    root_mean_squared_error computes the root mean squared error between real and forecasted prices.

    :param real_prices: Series of realized electricity prices.
    :param forecasted_prices: Series of forecasted electricity prices.
    :param rounding: Number of decimal places to round the result to.
    :return: Root mean squared error rounded to the specified number of decimal places.
    """
    m = mean_squared_error(real_prices, forecasted_prices, rounding)
    return round(np.sqrt(m), rounding)
