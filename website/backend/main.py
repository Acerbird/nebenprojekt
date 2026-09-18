"""Energiewende — Stromsystem erklärt und simuliert.

Start (aus dem Ordner website/):  ./run.sh
oder:                             uvicorn backend.main:app --reload
"""

from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import analysis as anl
from utils import profiles

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
    wind_gw: float = Query(anl.DEFAULTS["wind_gw"]),
    solar_gw: float = Query(anl.DEFAULTS["solar_gw"]),
    co2_price: float = Query(anl.DEFAULTS["co2_price"]),
    gas_price: float = Query(anl.DEFAULTS["gas_price"]),
    peak_load_gw: float = Query(anl.DEFAULTS["peak_load_gw"]),
    hours: int = Query(anl.DEFAULTS["hours"]),
    season: str = Query(anl.DEFAULTS["season"]),
):
    """Stündlicher Kraftwerkseinsatz, Börsenpreis und Kennzahlen."""
    return JSONResponse(anl.simulate(wind_gw, solar_gw, co2_price, gas_price,
                                     peak_load_gw, hours, season))


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


@app.get("/api/glossary")
async def api_glossary():
    return JSONResponse(anl.glossary())


@app.get("/health")
async def health():
    return {"status": "ok", "version": app.version}
