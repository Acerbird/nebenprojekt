"""Energiewende — Stromsystem erklärt und simuliert.

Start (aus dem Ordner website/):  ./run.sh
oder:                             uvicorn backend.main:app --reload
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import analysis as anl, region
from .data import smard, sources, store
from .utils import profiles

# Pfade hängen an der Position dieser Datei, nicht am Arbeitsverzeichnis —
# so startet die App aus jedem Ordner heraus.
BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = BASE_DIR / "frontend" / "templates"
STATIC_DIR = BASE_DIR / "frontend" / "static"

app = FastAPI(
    title="Energiewende — Stromsystem",
    description="Erklärseiten und ein kleines Merit-Order-Modell des deutschen Strommarkts.",
    version="1.0.0",
)

templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Navigation an einer Stelle definiert, damit Header und Footer nie auseinanderlaufen.
NAV = [
    {"href": "/", "label": "Start", "key": "start"},
    {"href": "/stromsystem", "label": "Stromsystem", "key": "stromsystem"},
    {"href": "/erneuerbare", "label": "Erneuerbare", "key": "erneuerbare"},
    {"href": "/speicher", "label": "Speicher", "key": "speicher"},
    {"href": "/maerkte", "label": "Märkte", "key": "maerkte"},
    {"href": "/handel", "label": "Handel", "key": "handel"},
    {"href": "/analysen", "label": "Analysen", "key": "analysen"},
    {"href": "/glossar", "label": "Glossar", "key": "glossar"},
]


def page(request: Request, template: str, key: str, title: str, **extra):
    context = {"request": request, "nav": NAV, "active": key, "title": title}
    context.update(extra)
    return templates.TemplateResponse(template, context)


# ---------------------------------------------------------------- Seiten

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return page(request, "index.html", "start", "Start")


@app.get("/stromsystem", response_class=HTMLResponse)
async def system_page(request: Request):
    return page(request, "subpages/system.html", "stromsystem", "Stromsystem")


@app.get("/erneuerbare", response_class=HTMLResponse)
async def renewables_page(request: Request):
    return page(request, "subpages/renewables.html", "erneuerbare", "Erneuerbare")


@app.get("/speicher", response_class=HTMLResponse)
async def storage_page(request: Request):
    return page(request, "subpages/storage.html", "speicher", "Speicher")


@app.get("/maerkte", response_class=HTMLResponse)
async def markets_page(request: Request):
    return page(request, "subpages/markets.html", "maerkte", "Märkte")


@app.get("/handel", response_class=HTMLResponse)
async def trade_page(request: Request):
    return page(request, "subpages/trade.html", "handel", "Handel")


@app.get("/analysen", response_class=HTMLResponse)
async def analysis_page(request: Request):
    return page(request, "subpages/analysis.html", "analysen", "Analysen",
                defaults=anl.DEFAULTS, limits=anl.LIMITS, seasons=profiles.SEASONS)


@app.get("/glossar", response_class=HTMLResponse)
async def glossary_page(request: Request):
    entries = anl.glossary()
    groups = sorted({e["group"] for e in entries})
    return page(request, "subpages/glossary.html", "glossar", "Glossar",
                entries=entries, groups=groups)


# ------------------------------------------------------------------- API

@app.get("/api/merit-order")
async def api_merit_order(
    co2_price: float = Query(anl.DEFAULTS["co2_price"], description="CO₂-Preis in €/t"),
    gas_price: float = Query(anl.DEFAULTS["gas_price"], description="Gaspreis in €/MWh thermisch"),
    wind_gw: float = Query(anl.DEFAULTS["wind_gw"], description="Installierte Windleistung in GW"),
    solar_gw: float = Query(anl.DEFAULTS["solar_gw"], description="Installierte PV-Leistung in GW"),
):
    """Merit-Order-Kurve für die übergebenen Parameter."""
    return JSONResponse(anl.merit_order_payload(co2_price, gas_price, wind_gw, solar_gw))


@app.get("/api/simulate")
async def api_simulate(
    wind_gw: Optional[float] = Query(
        None, description="Installierte Windleistung in GW. Nicht gesetzt heißt bei "
                          "echten Daten: Es gilt der tatsächliche Ausbaustand."),
    solar_gw: Optional[float] = Query(
        None, description="Installierte PV-Leistung in GW. Nicht gesetzt heißt bei "
                          "echten Daten: Es gilt der tatsächliche Ausbaustand."),
    co2_price: Optional[float] = Query(
        None, description="CO₂-Preis in €/t. Nicht gesetzt heißt bei echten Daten: "
                          "Es gilt der gemessene Monatswert."),
    gas_price: Optional[float] = Query(
        None, description="Gaspreis in €/MWh thermisch. Nicht gesetzt heißt bei "
                          "echten Daten: Es gilt der gemessene Monatswert."),
    peak_load_gw: Optional[float] = Query(
        None, description="Höchstlast in GW. Bei echten Daten wird die gemessene "
                          "Lastkurve nur dann gestreckt, wenn dieser Wert gesetzt ist."),
    hours: int = Query(anl.DEFAULTS["hours"]),
    season: str = Query(anl.DEFAULTS["season"], description="nur bei source=synthetic"),
    source: str = Query(anl.DEFAULTS["source"],
                        description="synthetic = erzeugte Profile, historical = SMARD-Messwerte"),
    start: Optional[str] = Query(None, description="Startdatum JJJJ-MM-TT, nur bei source=historical"),
    min_load: Optional[bool] = Query(
        None, description="Mindestlast der thermischen Blöcke berücksichtigen. "
                          "Standardmäßig aus — der Effekt ist real, verschlechtert "
                          "aber die Treffgenauigkeit; siehe README."),
):
    """Stündlicher Kraftwerkseinsatz, Börsenpreis und Kennzahlen."""
    try:
        return JSONResponse(anl.simulate(wind_gw, solar_gw, co2_price, gas_price,
                                         peak_load_gw, hours, season, source, start,
                                         min_load))
    except sources.InsufficientData as error:
        # Lieber ein klarer Hinweis als stillschweigend erzeugte Profile —
        # sonst hält man Modellzahlen für Messwerte.
        return JSONResponse(status_code=409, content={
            "error": "insufficient_data",
            "detail": str(error),
            "hint": "Messwerte holen mit: python -m backend.data.ingest --weeks 52",
        })


@app.get("/api/data/status")
async def api_data_status():
    """Welche Messwerte liegen lokal vor — für die Oberfläche und den Betrieb."""
    available = sources.available_range()
    settings = region.config()
    payload = {
        "available": available is not None,
        "source": "SMARD.de, Bundesnetzagentur",
        "region": settings["code"],
        # Alle Zeitstempel sind UTC; diese Zone gilt nur für die Anzeige.
        "display_timezone": settings["timezone"],
        "database_mb": round(store.database_size_bytes() / (1024 * 1024), 2),
        "series": {name: smard.describe(name) for name in smard.CORE_SERIES},
    }
    if available:
        payload["range"] = {
            "first_ts": available["first_ts"],
            "last_ts": available["last_ts"],
            "first": datetime.fromtimestamp(available["first_ts"], timezone.utc).isoformat(timespec="minutes"),
            "last": datetime.fromtimestamp(available["last_ts"], timezone.utc).isoformat(timespec="minutes"),
        }
        payload["coverage"] = available["series"]
    else:
        payload["hint"] = "Noch keine Messwerte. Abrufen mit: python -m backend.data.ingest --weeks 52"
    return JSONResponse(payload)


@app.get("/api/profiles/day")
async def api_day_profiles(season: Optional[str] = Query(None)):
    """Tagesgänge für die Erklärseiten: Last (Werktag/Wochenende) und PV je Jahreszeit."""
    return JSONResponse({
        "hours": list(range(24)),
        "load": {
            "werktag": profiles.load_day_profile(weekend=False),
            "wochenende": profiles.load_day_profile(weekend=True),
        },
        "solar_cf": {key: profiles.solar_day_profile(key) for key in profiles.SEASONS},
        "seasons": {key: cfg["label"] for key, cfg in profiles.SEASONS.items()},
        "selected_season": season if season in profiles.SEASONS else profiles.DEFAULT_SEASON,
    })


@app.get("/api/stories")
async def api_stories():
    """Geführte Fragen mit fertigen Parametersätzen für die Analyseseite."""
    return JSONResponse(anl.stories())


@app.get("/api/quarter-prices")
async def api_quarter_prices(date: Optional[str] = Query(None)):
    """Stunden- und Viertelstundenpreis eines Tages für die Marktseite."""
    return JSONResponse(anl.quarter_prices(date))


@app.get("/api/exchange-curve")
async def api_exchange_curve():
    """Gemessene Außenhandelskurve für die Erklärseite."""
    return JSONResponse(anl.exchange_curve())


@app.get("/api/glossary")
async def api_glossary():
    return JSONResponse(anl.glossary())


@app.get("/health")
async def health():
    return {"status": "ok", "version": app.version}
