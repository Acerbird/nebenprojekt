/* Tests des Szenarienvergleichs. */

import assert from "node:assert/strict";
import { describe, it } from "node:test";

import { loadPage } from "./helpers.mjs";

loadPage();   // jsdom bereitstellen, bevor charts.js geladen wird

const {
  METRICS, buildDiffNote, buildDiffRows, buildDiffTable, curvesComparable,
  describeScenario, formatDelta, formatRelative,
} = await import("../../frontend/static/js/compare.js");

function ergebnis(kpis, overrides = {}) {
  return {
    source: "synthetic",
    season_label: "Winter",
    timestamps: ["2025-01-06T00:00+00:00", "2025-01-06T01:00+00:00"],
    params: { wind_gw: 70, solar_gw: 90, co2_price: 80, gas_price: 32, hours: 2 },
    kpis,
    ...overrides,
  };
}

describe("Unterschiede zwischen zwei Läufen", () => {
  it("rechnet die Differenz in der Richtung jetzt minus gemerkt", () => {
    const rows = buildDiffRows(ergebnis({ mean_price: 80 }), ergebnis({ mean_price: 95 }));
    const preis = rows.find((r) => r.key === "mean_price");
    assert.equal(preis.pinned, 80);
    assert.equal(preis.current, 95);
    assert.equal(preis.delta, 15);
  });

  it("lässt Kennzahlen weg, die nur einer der beiden hat", () => {
    const rows = buildDiffRows(ergebnis({ mean_price: 80 }),
                               ergebnis({ mean_price: 95, emissions_kt: 12 }));
    assert.ok(!rows.some((r) => r.key === "emissions_kt"),
      "Eine Null zu erfinden, wo nichts gemessen wurde, wäre die schlechtere Antwort");
  });

  it("bewertet nur, wo die Richtung unstrittig ist", () => {
    const rows = buildDiffRows(
      ergebnis({ mean_price: 80, emissions_kt: 20, renewable_share: 40 }),
      ergebnis({ mean_price: 95, emissions_kt: 12, renewable_share: 55 }));
    const nach = Object.fromEntries(rows.map((r) => [r.key, r.direction]));
    assert.equal(nach.emissions_kt, "better", "weniger CO₂ ist besser");
    assert.equal(nach.renewable_share, "better", "mehr Erneuerbare sind besser");
    assert.equal(nach.mean_price, "",
      "Ob ein höherer Preis gut ist, hängt davon ab, wen man fragt");
  });

  it("kennzeichnet die schlechtere Richtung", () => {
    const rows = buildDiffRows(ergebnis({ emissions_kt: 12 }), ergebnis({ emissions_kt: 20 }));
    assert.equal(rows[0].direction, "worse");
  });

  it("kommt ohne gemerktes Szenario zurecht", () => {
    assert.deepEqual(buildDiffRows(null, ergebnis({ mean_price: 80 })), []);
    assert.equal(buildDiffTable(null, ergebnis({ mean_price: 80 })), "");
  });
});

describe("Darstellung der Differenz", () => {
  it("zeigt das Vorzeichen", () => {
    assert.equal(formatDelta({ delta: 15, digits: 1 }), "+15,0");
    assert.equal(formatDelta({ delta: -15, digits: 1 }), "−15,0");
  });

  it("nennt eine Differenz unterhalb der Anzeigegenauigkeit nicht 0,0", () => {
    assert.equal(formatDelta({ delta: 0.004, digits: 1 }), "±0");
    assert.equal(formatDelta({ delta: 0, digits: 0 }), "±0");
  });

  it("gibt die relative Änderung nur an, wo sie etwas aussagt", () => {
    assert.equal(formatRelative({ pinned: 80, delta: 8 }), "+10 %");
    assert.equal(formatRelative({ pinned: 0.01, delta: 5 }), "",
      "Eine Verfünfhundertfachung von fast nichts ist keine Aussage");
    assert.equal(formatRelative({ pinned: 100, delta: 0.1 }), "");
  });

  it("baut eine Tabelle mit beiden Werten und der Differenz", () => {
    const html = buildDiffTable(ergebnis({ mean_price: 80, emissions_kt: 20 }),
                                ergebnis({ mean_price: 95, emissions_kt: 12 }));
    assert.match(html, /Mittlerer Börsenpreis/);
    assert.match(html, /is-better/);
    assert.match(html, /\+15,0/);
    assert.match(html, /−8/);
  });

  it("jede Kennzahl trägt eine Einheit", () => {
    for (const m of METRICS) assert.ok(m.unit, m.key);
  });
});

describe("Beschriftung eines Szenarios", () => {
  it("nennt Zeitraum und Ausbau", () => {
    const text = describeScenario(ergebnis({ mean_price: 80 }));
    assert.match(text, /Winter/);
    assert.match(text, /70 GW Wind/);
    assert.match(text, /90 GW PV/);
  });

  it("nennt einen gemessenen Monatspreis nicht als Einstellung", () => {
    const text = describeScenario(ergebnis({ mean_price: 80 }, {
      source: "historical",
      fuel_costs: { months: { "2023-04": {
        used: { co2_price: "historical", gas_price: "historical" } } } },
    }));
    assert.doesNotMatch(text, /CO₂/);
    assert.doesNotMatch(text, /Gas/);
  });

  it("nennt einen eingestellten Preis sehr wohl", () => {
    const text = describeScenario(ergebnis({ mean_price: 80 }, {
      source: "historical",
      fuel_costs: { months: { "2023-04": {
        used: { co2_price: "fixed", gas_price: "historical" } } } },
    }));
    assert.match(text, /CO₂ 80 €\/t/);
    assert.doesNotMatch(text, /Gas/);
  });

  it("weist die eingeschaltete Mindestlast aus", () => {
    const r = ergebnis({ mean_price: 80 });
    r.params.min_load = true;
    assert.match(describeScenario(r), /mit Mindestlast/);
  });
});

describe("Lassen sich die Kurven übereinanderlegen?", () => {
  const a = ergebnis({ mean_price: 80 });

  it("ja, bei gleichem Beginn und gleicher Länge", () => {
    assert.equal(curvesComparable(a, ergebnis({ mean_price: 95 })), true);
  });

  it("nein, bei verschobenem Beginn", () => {
    const b = ergebnis({ mean_price: 95 });
    b.timestamps = ["2024-01-06T00:00+00:00", "2024-01-06T01:00+00:00"];
    assert.equal(curvesComparable(a, b), false);
  });

  it("nein, bei ungleicher Länge", () => {
    const b = ergebnis({ mean_price: 95 });
    b.timestamps = [...b.timestamps, "2025-01-06T02:00+00:00"];
    assert.equal(curvesComparable(a, b), false);
  });

  it("der Hinweis sagt, warum nur eine Kurve zu sehen ist", () => {
    const b = ergebnis({ mean_price: 95 });
    b.timestamps = ["2024-01-06T00:00+00:00", "2024-01-06T01:00+00:00"];
    assert.match(buildDiffNote(a, b), /Zeiträume unterscheiden sich/);
    assert.match(buildDiffNote(a, ergebnis({ mean_price: 95 })), /übereinander/);
  });
});
