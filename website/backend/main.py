# backend/main.py
from fastapi import FastAPI, Request, Form
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from typing import Optional
import analysis as anl

app = FastAPI(title="Energiewende — Stromsystem Demo")

# Templates & static files
templates = Jinja2Templates(directory="frontend/templates")
app.mount("/static", StaticFiles(directory="frontend/static"), name="static")

# Pages
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/analysis", response_class=HTMLResponse)
async def analysis_page(request: Request):
    # pass a small static dataset or a generated sample
    sample = anl.generate_sample_prices()
    return templates.TemplateResponse("analysis.html", {"request": request, "sample": sample})

# API: run a dynamic analysis on demand
@app.post("/api/analysis/run")
async def run_analysis(wind_share: Optional[float] = Form(0.25), solar_share: Optional[float] = Form(0.15), hours: Optional[int] = Form(48)):
    """
    Runs a simple simulation with parameters supplied from the frontend form.
    Returns JSON with arrays that the frontend can plot with Plotly.
    """
    try:
        wind_share = float(wind_share)
        solar_share = float(solar_share)
        hours = int(hours)
    except Exception:
        return JSONResponse({"error": "Invalid parameters"}, status_code=400)

    result = anl.run_simulation(hours=hours, wind_share=wind_share, solar_share=solar_share)
    return JSONResponse(result)

# API: return a precomputed merit order curve (example)
@app.get("/api/analysis/merit-order")
async def merit_order():
    fig_data = anl.generate_merit_order_figure_json()
    # fig_data is dict with 'x' and 'y' or plotly JSON; just return it
    return JSONResponse(fig_data)

# health
@app.get("/health")
async def health():
    return {"status": "ok"}
