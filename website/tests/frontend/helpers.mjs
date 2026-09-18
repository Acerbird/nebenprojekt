/* Gemeinsame Hilfen für die Frontend-Tests.
 *
 * Geladen wird die echte Analyseseite — gerendert vom laufenden Jinja-Template,
 * abgelegt als Datei. So prüfen die Tests gegen das Markup, das die Anwendung
 * wirklich ausliefert, und nicht gegen eine Nachbildung, die auseinanderläuft.
 */

import { existsSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { JSDOM } from "jsdom";

const here = dirname(fileURLToPath(import.meta.url));
const snapshotPath = join(here, "analysis-page.html");

/** Die gerenderte Analyseseite in ein Dokument laden. */
export function loadPage() {
  if (!existsSync(snapshotPath)) {
    throw new Error(
      "Die gerenderte Analyseseite fehlt. Sie entsteht beim Testlauf über ./test.sh; " +
      "einzeln erzeugen mit:\n" +
      "  backend/.venv/bin/python -c \"from fastapi.testclient import TestClient; " +
      "from backend.main import app; " +
      "open('tests/frontend/analysis-page.html','w').write(TestClient(app).get('/analysen').text)\"");
  }
  const html = readFileSync(snapshotPath, "utf-8");
  const dom = new JSDOM(html, { url: "https://example.org/analysen" });
  const { window } = dom;

  // FormData und URLSearchParams stammen aus dem Fenster, damit sie das
  // Formular dieses Dokuments kennen. location und history kommen dazu, weil
  // die Seite das Szenario in der Adresszeile ablegt — ohne sie bräche jeder
  // Test, der analysis.js als Ganzes lädt.
  globalThis.window = window;
  globalThis.document = window.document;
  globalThis.FormData = window.FormData;
  globalThis.URLSearchParams = window.URLSearchParams;
  globalThis.location = window.location;
  globalThis.history = window.history;
  globalThis.Event = window.Event;

  return { dom, window, document: window.document,
           form: window.document.getElementById("sim-form") };
}

/** Ein Ergebnis, wie es /api/simulate für echte Messwerte liefert. */
export function historicalResult(overrides = {}) {
  return {
    source: "historical",
    season_label: "16.09.2026 bis 18.09.2026",
    display_timezone: "Europe/Berlin",
    params: { hours: 48, adjustments: {} },
    kpis: { demand_twh: 2.4 },
    series_meta: {
      data_source: "SMARD.de, Bundesnetzagentur",
      installed_gw: { wind: 80.9, solar: 126.4 },
      gaps_filled: {},
      timezone: "Europe/Berlin",
    },
    ...overrides,
  };
}

/** Ein Ergebnis aus erzeugten Profilen. */
export function syntheticResult(overrides = {}) {
  return {
    source: "synthetic",
    season_label: "Winter",
    display_timezone: "Europe/Berlin",
    params: { hours: 72, adjustments: {} },
    kpis: { demand_twh: 3.9 },
    series_meta: { season: "winter", timezone: "Europe/Berlin" },
    ...overrides,
  };
}
