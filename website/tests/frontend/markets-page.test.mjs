/* Das Tagesdiagramm der Marktseite.
 *
 * Zwei Linien aus derselben Auktion: die Treppe des Stundenkontrakts und die
 * Zacken der Viertelstunden. Geprüft wird, dass topics.js die richtige Abfrage
 * stellt, beide Linien übergibt und die Achse in der mitgelieferten Zeitzone
 * beschriftet — in UTC läge sie im Sommer zwei Stunden daneben.
 */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { loadPage } from "./helpers.mjs";

const START = Date.UTC(2026, 3, 7) / 1000;          // 7. April 2026, 00:00 UTC

function tag(overrides = {}) {
  const timestamps = Array.from({ length: 96 }, (_, i) => START + i * 900);
  const hourly = [];
  const quarter = [];
  for (let i = 0; i < 96; i += 4) {
    const basis = 60 + 40 * Math.sin((i / 96) * 2 * Math.PI);
    for (let k = 0; k < 4; k += 1) {
      hourly.push(basis);                            // je Stunde derselbe Wert
      quarter.push(basis + (k - 1.5) * 12);          // Zacken um die Stunde
    }
  }
  return {
    date: "2026-04-07", timestamps, hourly, quarter,
    display_timezone: "Europe/Berlin",
    source: "SMARD.de, Bundesnetzagentur",
    ...overrides,
  };
}

/** Lädt die Seite und beantwortet jede Abfrage nach ihrem Pfad. */
async function ladeSeite(antworten) {
  const { window, document } = loadPage("markets-page.html", "/maerkte");
  const gefragt = [];
  globalThis.fetch = async (url) => {
    gefragt.push(String(url));
    const treffer = Object.entries(antworten)
      .find(([teil]) => String(url).includes(teil));
    if (!treffer) return { ok: false, status: 404 };
    return { ok: true, json: async () => treffer[1] };
  };
  await import("../../frontend/static/js/topics.js?v=" + Math.random());
  await new Promise((resolve) => window.setTimeout(resolve, 0));
  return { document, gefragt };
}

describe("Marktseite: Stunden gegen Viertelstunden", () => {
  it("fragt den Tagesvergleich ab", async () => {
    const { gefragt } = await ladeSeite({ "quarter-prices": tag() });
    assert.ok(gefragt.some((url) => url.includes("/api/quarter-prices")));
  });

  it("zeichnet beide Linien", async () => {
    const { document } = await ladeSeite({ "quarter-prices": tag() });
    const figur = document.getElementById("chart-quarter");
    assert.ok(figur.querySelector("svg"), "es wurde kein SVG gezeichnet");
    const text = figur.textContent;
    assert.match(text, /Stundenkontrakt/);
    assert.match(text, /Viertelstunden/);
  });

  it("beschriftet die Achse in der gelieferten Zeitzone", async () => {
    const { document } = await ladeSeite({ "quarter-prices": tag() });
    // Die Wertetabelle des Diagramms führt die x-Achse Zeile für Zeile. Der
    // erste Zeitpunkt ist 00:00 UTC — im April in Berlin also 02:00. Geprüft
    // wird genau diese Zelle: Irgendwo im Diagramm steht "02:00" auch dann,
    // wenn die Achse in UTC läuft.
    const erste = document.querySelector("#chart-quarter tbody tr th");
    assert.ok(erste, "die Wertetabelle des Diagramms fehlt");
    assert.equal(erste.textContent.trim(), "02:00");
  });

  it("sagt Bescheid, statt still leer zu bleiben", async () => {
    const { document } = await ladeSeite({});
    const figur = document.getElementById("chart-quarter");
    assert.ok(figur.querySelector(".chart-error"));
  });

  it("zeichnet nichts, wenn der Tag keine Werte hat", async () => {
    const leer = tag({ quarter: [], hourly: [], timestamps: [] });
    const { document } = await ladeSeite({ "quarter-prices": leer });
    assert.equal(document.getElementById("chart-quarter").querySelector("svg"), null);
  });
});
