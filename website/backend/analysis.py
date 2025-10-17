# backend/analysis.py
import numpy as np
import pandas as pd
import json
import plotly.graph_objs as go

def generate_sample_prices(hours=48):
    """Return a tiny sample series for templating examples."""
    t = np.arange(hours)
    base = 30 + 5*np.sin(t/6.0)
    noise = np.random.normal(scale=3.0, size=hours)
    prices = (base + noise).round(2).tolist()
    timestamps = pd.date_range("2025-01-01", periods=hours, freq="H").astype(str).tolist()
    return {"timestamps": timestamps, "prices": prices}

def run_simulation(hours=48, wind_share=0.25, solar_share=0.15):
    """
    Simple toy-model:
    - base price
    - negative price effect when renewable share is high
    - add diurnal solar pattern and stochastic noise for realism
    Returns JSON friendly arrays.
    """
    rng = np.random.default_rng(42)
    t = np.arange(hours)
    # diurnal solar production (peak midday)
    solar_profile = np.clip(np.sin((t - 6) / 24 * 2*np.pi), 0, None)
    wind_profile = 0.5 + 0.5*np.sin(t/7.3)  # slower variation

    # Renewable supply fraction across time
    ren_frac = wind_share * wind_profile + solar_share * solar_profile
    ren_frac = np.clip(ren_frac, 0, 0.9)

    # base price curve
    base = 40 + 8*np.sin(t/24*2*np.pi)  # daily pattern
    # price reduction proportional to renewable fraction
    price = base * (1 - 0.6 * ren_frac) + rng.normal(scale=2.5, size=hours)
    price = np.round(price, 2)

    timestamps = pd.date_range("2025-01-01", periods=hours, freq="H").astype(str).tolist()
    return {"timestamps": timestamps,
            "prices": price.tolist(),
            "renewable_fraction": np.round(ren_frac, 3).tolist(),
            "params": {"wind_share": wind_share, "solar_share": solar_share, "hours": hours}}

def generate_merit_order_figure_json():
    """Generate a sample merit-order curve as JSON-like structure"""
    # sample supply blocks
    x = [0, 10, 30, 60, 90, 120, 160]
    y = [5, 12, 25, 40, 60, 80, 120]  # price steps
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=x, y=y, mode="lines+markers", name="Merit Order"))
    fig.update_layout(title="Beispiel: Merit-Order-Kurve", xaxis_title="Kumulierte Erzeugung (MW)", yaxis_title="Preis (€/MWh)")
    # return the figure as JSON so the frontend can use Plotly to render it
    return json.loads(fig.to_json())
