/* Zusammenspiel auf der Analyseseite.
 *
 * Die Module einzeln zu prüfen sagt nichts darüber, ob sie auch verdrahtet
 * sind. Dieser Test lädt analysis.js in jsdom, legt untergeschobene Antworten
 * für fetch bereit und bedient die Seite wie ein Besucher: Geschichte öffnen,
 * weiterblättern, Szenario merken.
 *
 * Gerechnet wird dabei nichts — was das Modell liefert, prüfen die Python-Tests.
 * Hier geht es allein darum, ob ein Klick ankommt.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, it } from "node:test";

import { loadPage } from "./helpers.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const stories = JSON.parse(readFileSync(
  join(here, "../../backend/static_data/stories.json"), "utf-8"));

/** Ein Ergebnis, das reicht, damit die Seite es zeichnen kann. */
function simulation(overrides = {}) {
  const n = 3;
  const reihe = (v) => Array.from({ length: n }, () => v);
  return {
    source: "synthetic",
    season_label: "Winter",
    display_timezone: "Europe/Berlin",
    timestamps: ["2025-01-06T00:00+00:00", "2025-01-06T01:00+00:00", "2025-01-06T02:00+00:00"],
    demand_gw: reihe(60), residual_load_gw: reihe(30), curtailed_gw: reihe(0),
    storage_charge_gw: reihe(0), storage_discharge_gw: reihe(0), net_export_gw: reihe(0),
    price_eur_mwh: [80, 90, 100],
    available_gw: { wind: reihe(10), solar: reihe(5) },
    generation_gw: { wind: reihe(10), solar: reihe(5), sonstige_ee: reihe(5),
                     speicher: reihe(0), braunkohle: reihe(15), steinkohle: reihe(10),
                     erdgas: reihe(15) },
    categories: [{ id: "wind", label: "Wind" }, { id: "solar", label: "Photovoltaik" },
                 { id: "sonstige_ee", label: "Sonstige EE" }, { id: "speicher", label: "Speicher" },
                 { id: "braunkohle", label: "Braunkohle" }, { id: "steinkohle", label: "Steinkohle" },
                 { id: "erdgas", label: "Erdgas" }],
    storage: { units: [], cheap_threshold: 0, expensive_threshold: 0 },
    fuel_costs: { source: "fixed", months: {}, table: null },
    kpis: { mean_price: 90, renewable_share: 33.3, emissions_kt: 12,
            emission_intensity_g_kwh: 300, curtailed_gwh: 0, surplus_hours: 0,
            negative_price_hours: 0, scarcity_hours: 0, demand_twh: 0.18,
            storage_charged_gwh: 0, storage_discharged_gwh: 0, storage_losses_gwh: 0,
            net_export_gwh: 0, export_hours: 0, import_hours: 0 },
    params: { hours: 3, adjustments: {}, wind_gw: 70, solar_gw: 90,
              co2_price: 80, gas_price: 32, min_load: false },
    series_meta: {}, validation: null, forecasts: {},
    ...overrides,
  };
}

const meritOrder = {
  blocks: [{ id: "wind", name: "Wind", category: "wind", capacity_gw: 70,
             from_gw: 0, to_gw: 70, cost: 0, note: "" }],
  categories: [{ id: "wind", label: "Wind" }],
  total_capacity_gw: 232.3,
  params: {},
};

/** Seite laden, fetch unterschieben und analysis.js laufen lassen. */
async function bootPage() {
  const { window, document } = loadPage();
  const aufrufe = [];
  let naechsteSimulation = simulation();

  window.ResizeObserver = class { observe() {} disconnect() {} };
  globalThis.ResizeObserver = window.ResizeObserver;

  const antwort = (body) => Promise.resolve({
    ok: true, status: 200, json: () => Promise.resolve(body),
  });
  globalThis.fetch = (url) => {
    aufrufe.push(String(url));
    if (String(url).startsWith("/api/stories")) return antwort(stories);
    if (String(url).startsWith("/api/data/status")) {
      return antwort({ available: true,
                       range: { first: "2023-01-01T00:00+00:00", last: "2026-09-20T00:00+00:00" } });
    }
    if (String(url).startsWith("/api/merit-order")) return antwort(meritOrder);
    return antwort(naechsteSimulation);
  };

  // Cachebrecher, damit jeder Test ein frisches Modul mit frischem Zustand bekommt.
  await import(`../../frontend/static/js/analysis.js?t=${Math.random()}`);
  const warte = () => new Promise((r) => window.setTimeout(r, 0));
  for (let i = 0; i < 12; i += 1) await warte();

  return { window, document, aufrufe, warte,
           setSimulation: (s) => { naechsteSimulation = s; } };
}

describe("Die Seite startet", () => {
  it("holt Datenbestand, Geschichten und eine erste Simulation", async () => {
    const { aufrufe } = await bootPage();
    assert.ok(aufrufe.some((u) => u.startsWith("/api/data/status")));
    assert.ok(aufrufe.some((u) => u.startsWith("/api/stories")));
    assert.ok(aufrufe.some((u) => u.startsWith("/api/simulate")));
  });

  it("zeigt für jede Geschichte eine Karte", async () => {
    const { document } = await bootPage();
    const karten = document.querySelectorAll("#story-list [data-story]");
    assert.equal(karten.length, stories.length);
  });

  it("hält Geschichtsfeld und Vergleich zunächst geschlossen", async () => {
    const { document } = await bootPage();
    assert.equal(document.getElementById("story-panel").hidden, true);
    assert.equal(document.getElementById("diff-box").hidden, true);
  });
});

describe("Eine Geschichte lässt sich bedienen", () => {
  it("öffnet beim Klick den ersten Schritt und stellt das Formular ein", async () => {
    const { document, warte } = await bootPage();
    const story = stories.find((s) => s.id === "co2-preis");
    document.querySelector(`[data-story="${story.id}"]`).click();
    await warte();

    const panel = document.getElementById("story-panel");
    assert.equal(panel.hidden, false);
    assert.ok(panel.textContent.includes(story.title));
    assert.ok(panel.textContent.includes(story.steps[0].text.slice(0, 40)));
    assert.equal(document.getElementById("co2_price").value,
                 String(story.steps[0].params.co2_price));
  });

  it("blättert weiter und zieht das Formular nach", async () => {
    const { document, warte } = await bootPage();
    const story = stories.find((s) => s.id === "co2-preis");
    document.querySelector(`[data-story="${story.id}"]`).click();
    await warte();
    document.querySelector(".story-next").click();
    await warte();

    assert.equal(document.getElementById("co2_price").value,
                 String(story.steps[1].params.co2_price));
    assert.ok(document.getElementById("story-panel").textContent
      .includes(`Schritt 2 von ${story.steps.length}`));
  });

  it("springt über die Schrittleiste", async () => {
    const { document, warte } = await bootPage();
    document.querySelector('[data-story="co2-preis"]').click();
    await warte();
    document.querySelector('.story-steps [data-step="2"]').click();
    await warte();
    assert.ok(document.getElementById("story-panel").textContent.includes("Schritt 3 von 3"));
  });

  it("legt Geschichte und Schritt in die Adresszeile", async () => {
    const { window, document, warte } = await bootPage();
    document.querySelector('[data-story="co2-preis"]').click();
    await warte();
    document.querySelector(".story-next").click();
    await warte();
    assert.match(window.location.search, /story=co2-preis/);
    assert.match(window.location.search, /schritt=2/);
  });

  it("schließt sich, wenn jemand selbst an den Reglern dreht", async () => {
    const { window, document, warte } = await bootPage();
    document.querySelector('[data-story="co2-preis"]').click();
    await warte();
    assert.equal(document.getElementById("story-panel").hidden, false);

    const regler = document.getElementById("wind_gw");
    regler.value = "120";
    regler.dispatchEvent(new window.Event("input", { bubbles: true }));
    await warte();
    assert.equal(document.getElementById("story-panel").hidden, true,
      "Der Text stünde sonst neben einem Bild, das er nicht mehr beschreibt");
  });

  it("lässt sich über den Schließen-Knopf beenden", async () => {
    const { document, warte } = await bootPage();
    document.querySelector('[data-story="co2-preis"]').click();
    await warte();
    document.querySelector(".story-close").click();
    await warte();
    assert.equal(document.getElementById("story-panel").hidden, true);
  });
});

describe("Zwei Szenarien nebeneinander", () => {
  it("zeigt den Vergleich erst, nachdem etwas gemerkt wurde", async () => {
    const { document, warte, setSimulation } = await bootPage();
    assert.equal(document.getElementById("diff-box").hidden, true);

    document.getElementById("pin-scenario").click();
    await warte();
    assert.equal(document.getElementById("diff-box").hidden, false);

    setSimulation(simulation({ kpis: { ...simulation().kpis, mean_price: 120, emissions_kt: 20 } }));
    document.getElementById("sim-form").dispatchEvent(
      new document.defaultView.Event("submit", { bubbles: true, cancelable: true }));
    for (let i = 0; i < 8; i += 1) await warte();

    const tabelle = document.getElementById("diff-table").textContent;
    assert.ok(tabelle.includes("Mittlerer Börsenpreis"));
    assert.ok(tabelle.includes("+30,0"), `Differenz fehlt in: ${tabelle}`);
  });

  it("beendet den Vergleich wieder", async () => {
    const { document, warte } = await bootPage();
    document.getElementById("pin-scenario").click();
    await warte();
    document.getElementById("unpin-scenario").click();
    await warte();
    assert.equal(document.getElementById("diff-box").hidden, true);
  });
});
