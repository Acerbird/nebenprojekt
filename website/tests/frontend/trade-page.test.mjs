/* Das Diagramm der Handelsseite.
 *
 * topics.js holt die Kurve und reicht sie an charts.js weiter. Ob diese
 * Übergabe passt, sagt kein Modultest der beiden Seiten für sich: Eine
 * Spezifikation, die charts.js nicht akzeptiert, fällt erst beim Zeichnen auf.
 * Deshalb wird hier die gerenderte Seite geladen, fetch untergeschoben und
 * geprüft, ob am Ende ein SVG im Dokument steht.
 */

import assert from "node:assert/strict";
import { describe, it, before } from "node:test";

import { loadPage } from "./helpers.mjs";

/** Eine Antwort, wie /api/exchange-curve sie liefert. */
function kurve(overrides = {}) {
  const prices = [];
  const werte = [];
  for (let p = -60; p <= 200; p += 5) {
    prices.push(p);
    werte.push(9.35 - (p + 60) * 0.0626);       // fallend, wie die echte Kurve
  }
  return {
    prices,
    net_export_gw: werte,
    points: [{ price_eur_mwh: -60, net_export_gw: 9.35 },
             { price_eur_mwh: 200, net_export_gw: -6.92 }],
    max_export_gw: 11.0,
    max_import_gw: 7.0,
    note: "Preisabhängiger Nettoexport in GW, positiv bei Ausfuhr.",
    source: "SMARD-Filter 4629",
    ...overrides,
  };
}

async function ladeMitAntwort(antwort) {
  const { window, document } = loadPage("trade-page.html", "/handel");
  globalThis.fetch = async () => antwort;
  // Der Frischeanhang erzwingt ein neues Modul je Testfall — sonst liefe der
  // Rumpf von topics.js nur beim ersten Import.
  await import("../../frontend/static/js/topics.js?v=" + Math.random());
  await new Promise((resolve) => window.setTimeout(resolve, 0));
  return document;
}

describe("Handelsseite: Außenhandelskurve", () => {
  it("zeichnet die Kurve in das vorgesehene Gerüst", async () => {
    const document = await ladeMitAntwort({ ok: true, json: async () => kurve() });
    const figur = document.getElementById("chart-exchange");
    assert.ok(figur, "die Figur steht nicht im Markup");
    assert.ok(figur.querySelector("svg"), "es wurde kein SVG gezeichnet");
  });

  it("beschriftet die Achsen mit Preis und Leistung", async () => {
    const document = await ladeMitAntwort({ ok: true, json: async () => kurve() });
    const text = document.getElementById("chart-exchange").textContent;
    assert.match(text, /€\/MWh/);
    assert.match(text, /GW/);
  });

  it("sagt Bescheid, statt still leer zu bleiben", async () => {
    const document = await ladeMitAntwort({ ok: false, status: 500 });
    const figur = document.getElementById("chart-exchange");
    assert.ok(figur.querySelector(".chart-error"),
              "ohne Daten fehlt der Hinweis");
  });

  it("zeichnet nichts, wenn die Kurve leer ist", async () => {
    const leer = kurve({ prices: [], net_export_gw: [] });
    const document = await ladeMitAntwort({ ok: true, json: async () => leer });
    const figur = document.getElementById("chart-exchange");
    assert.equal(figur.querySelector("svg"), null);
  });
});
