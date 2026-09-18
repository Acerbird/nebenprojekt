/* Tests der Formularlogik der Analyseseite.
 *
 * Geprüft wird gegen die echte, vom Template gerenderte Seite: Welche Parameter
 * gehen an die API, welche Felder gehören zu welcher Datengrundlage, was steht
 * in der Herkunftszeile.
 */

import assert from "node:assert/strict";
import { beforeEach, describe, it } from "node:test";

import { loadPage, historicalResult, syntheticResult } from "./helpers.mjs";

const scenarioModule = await import("../../frontend/static/js/scenario.js");
const {
  applySourceVisibility, buildDataNote, buildFuelNote, buildStatusText,
  buildValidationNote,
  buildTradeNote, formatDate, hoursHintText, readParams, restoreFromUrl,
  sourceHintText, MissingData,
} = scenarioModule;

let form;

beforeEach(() => {
  ({ form } = loadPage());
});

/** Quelle umschalten wie ein Benutzer: Wert setzen und Sichtbarkeit nachziehen. */
function chooseSource(value) {
  form.elements.source.value = value;
  applySourceVisibility(form, value);
}

describe("Parameter für die API", () => {
  it("schickt bei erzeugten Profilen die Jahreszeit mit", () => {
    chooseSource("synthetic");
    const params = readParams(form);
    assert.equal(params.source, "synthetic");
    assert.ok(params.season, "Jahreszeit fehlt");
    assert.ok("peak_load_gw" in params, "Höchstlast fehlt");
  });

  it("schickt bei erzeugten Profilen kein Startdatum", () => {
    chooseSource("synthetic");
    assert.equal("start" in readParams(form), false);
  });

  it("schickt bei Messwerten keine Jahreszeit", () => {
    chooseSource("historical");
    assert.equal("season" in readParams(form), false);
  });

  it("lässt die Höchstlast bei Messwerten weg, solange nichts angekreuzt ist", () => {
    // Sonst hielte man eine gestreckte Kurve für eine gemessene.
    chooseSource("historical");
    form.elements.scale_load.checked = false;
    assert.equal("peak_load_gw" in readParams(form), false);
  });

  it("schickt die Höchstlast, sobald das Strecken gewünscht ist", () => {
    chooseSource("historical");
    form.elements.scale_load.checked = true;
    form.elements.peak_load_gw.value = "90";
    assert.equal(readParams(form).peak_load_gw, "90");
  });

  it("übernimmt das gewählte Startdatum", () => {
    chooseSource("historical");
    form.elements.start.value = "2026-08-01";
    assert.equal(readParams(form).start, "2026-08-01");
  });

  it("gibt die Reglerwerte unverändert weiter", () => {
    chooseSource("synthetic");
    form.elements.wind_gw.value = "123";
    form.elements.co2_price.value = "45";
    const params = readParams(form);
    assert.equal(params.wind_gw, "123");
    assert.equal(params.co2_price, "45");
  });

  it("wählt bei unbekannter Quelle die erzeugten Profile", () => {
    form.elements.source.value = "";
    assert.equal(readParams(form).source, "synthetic");
  });
});

describe("Sichtbarkeit der Felder", () => {
  it("zeigt die Jahreszeit nur bei erzeugten Profilen", () => {
    const feld = form.querySelector('[data-when="synthetic"]');
    chooseSource("synthetic");
    assert.equal(feld.hidden, false);
    chooseSource("historical");
    assert.equal(feld.hidden, true);
  });

  it("zeigt Startdatum und Ankreuzfeld nur bei Messwerten", () => {
    const felder = [...form.querySelectorAll('[data-when="historical"]')];
    assert.ok(felder.length >= 2, "erwartet werden Startdatum und Ankreuzfeld");
    chooseSource("historical");
    assert.ok(felder.every((f) => f.hidden === false));
    chooseSource("synthetic");
    assert.ok(felder.every((f) => f.hidden === true));
  });

  it("deaktiviert ausgeblendete Eingaben, damit sie nicht mitgesendet werden", () => {
    chooseSource("synthetic");
    assert.equal(form.elements.start.disabled, true);
    chooseSource("historical");
    assert.equal(form.elements.start.disabled, false);
    assert.equal(form.elements.season.disabled, true);
  });

  it("erklärt die gewählte Datengrundlage im Hinweistext", () => {
    assert.match(sourceHintText("historical"), /Bundesnetzagentur/);
    assert.match(sourceHintText("synthetic"), /nachgebildete/);
    assert.notEqual(hoursHintText("historical"), hoursHintText("synthetic"));
  });
});

describe("Herkunftszeile", () => {
  it("bleibt bei erzeugten Profilen leer", () => {
    assert.equal(buildDataNote(syntheticResult()), "");
  });

  it("nennt Quelle und tatsächlichen Ausbaustand", () => {
    const text = buildDataNote(historicalResult());
    assert.match(text, /SMARD/);
    assert.match(text, /81 GW Wind/);
    assert.match(text, /126 GW Photovoltaik/);
  });

  it("weist überbrückte Lücken aus", () => {
    const result = historicalResult();
    result.series_meta.gaps_filled = { load: 1, wind_onshore: 2 };
    assert.match(buildDataNote(result), /3 fehlende Stundenwerte/);
  });

  it("verschweigt keine Verschiebung des Zeitraums", () => {
    const result = historicalResult();
    result.params.adjustments = { reason: "Der gewünschte Zeitraum liegt außerhalb der Messwerte." };
    assert.match(buildDataNote(result), /außerhalb der Messwerte/);
  });

  it("weist eine gestreckte Last aus", () => {
    const result = historicalResult();
    result.series_meta.load_scaled_by = 1.47;
    assert.match(buildDataNote(result), /Faktor 1,47 gestreckt/);
  });

  it("kommt ohne Zusatzangaben aus", () => {
    const text = buildDataNote({ source: "historical", series_meta: {}, params: {} });
    assert.match(text, /SMARD/);
  });
});

describe("Statuszeile", () => {
  it("nennt die Herkunft der Zahlen", () => {
    assert.match(buildStatusText(historicalResult()), /Messwerte/);
    assert.match(buildStatusText(syntheticResult()), /erzeugte Profile/);
  });

  it("nennt Dauer und Verbrauch", () => {
    const text = buildStatusText(syntheticResult());
    assert.match(text, /72 Stunden/);
    assert.match(text, /3,90 TWh/);
  });
});

describe("Szenario aus der Adresszeile", () => {
  it("stellt die Regler wieder her", () => {
    restoreFromUrl(form, "?wind_gw=150&co2_price=120&hours=168");
    assert.equal(form.elements.wind_gw.value, "150");
    assert.equal(form.elements.co2_price.value, "120");
    assert.equal(form.elements.hours.value, "168");
  });

  it("stellt Quelle und Startdatum wieder her", () => {
    restoreFromUrl(form, "?source=historical&start=2026-08-01");
    assert.equal(form.elements.source.value, "historical");
    assert.equal(form.elements.start.value, "2026-08-01");
  });

  it("erkennt an der mitgegebenen Höchstlast, dass gestreckt werden soll", () => {
    restoreFromUrl(form, "?source=historical&peak_load_gw=90");
    assert.equal(form.elements.scale_load.checked, true);
  });

  it("kreuzt ohne Höchstlast nichts an", () => {
    restoreFromUrl(form, "?source=historical");
    assert.equal(form.elements.scale_load.checked, false);
  });

  it("überlebt eine leere Adresszeile", () => {
    restoreFromUrl(form, "");
    assert.equal(form.elements.source.value, "synthetic");
  });

  it("ignoriert unbekannte Parameter", () => {
    restoreFromUrl(form, "?unfug=1&wind_gw=99");
    assert.equal(form.elements.wind_gw.value, "99");
  });

  it("schließt den Kreis: gelesene Parameter lassen sich wiederherstellen", () => {
    chooseSource("historical");
    form.elements.start.value = "2026-08-01";
    form.elements.scale_load.checked = true;
    form.elements.peak_load_gw.value = "88";
    const query = new URLSearchParams(readParams(form)).toString();

    const { form: zweites } = loadPage();
    restoreFromUrl(zweites, `?${query}`);
    applySourceVisibility(zweites, zweites.elements.source.value);
    assert.deepEqual(readParams(zweites), readParams(form));
  });
});

describe("Fehlende Messwerte", () => {
  it("tragen Meldung und Hinweis", () => {
    const error = new MissingData("Keine Messwerte.", "ingest ausführen");
    assert.equal(error.name, "MissingData");
    assert.match(error.message, /Keine Messwerte/);
    assert.match(error.hint, /ingest/);
    assert.ok(error instanceof Error);
  });
});

describe("Datumsdarstellung", () => {
  it("dreht ISO in die hier übliche Schreibweise", () => {
    assert.equal(formatDate("2026-09-18"), "18.09.2026");
    assert.equal(formatDate("2026-09-18T10:00+00:00"), "18.09.2026");
  });
});

describe("Vergleich mit dem tatsächlichen Preis", () => {
  const mitValidierung = (overrides = {}) => historicalResult({
    validation: {
      hours_compared: 168, mean_model: 76.8, mean_actual: 177.9,
      mean_absolute_error: 101.1, bias: -101.1, correlation: 0.638,
      actual_price_eur_mwh: [100, 120, null, 90],
      ...overrides,
    },
  });

  it("bleibt leer, solange nichts zu vergleichen ist", () => {
    assert.equal(buildValidationNote(syntheticResult()), "");
    assert.equal(buildValidationNote(historicalResult()), "");
  });

  it("nennt beide Mittelwerte und die Abweichung", () => {
    const html = buildValidationNote(mitValidierung());
    assert.match(html, /76,8 €\/MWh/);
    assert.match(html, /177,9 €\/MWh/);
    assert.match(html, /101,1 €\/MWh/);
  });

  it("sagt, in welche Richtung das Modell danebenliegt", () => {
    assert.match(buildValidationNote(mitValidierung()), /zu niedrig/);
    assert.match(buildValidationNote(mitValidierung({ bias: 40 })), /zu hoch/);
  });

  it("übersetzt die Korrelation in eine Einschätzung", () => {
    assert.match(buildValidationNote(mitValidierung({ correlation: 0.9 })), /trifft den Verlauf gut/);
    assert.match(buildValidationNote(mitValidierung({ correlation: 0.638 })), /trifft den Verlauf grob/);
    assert.match(buildValidationNote(mitValidierung({ correlation: 0.05 })), /trifft den Verlauf nicht/);
  });

  it("kommt ohne bestimmbare Korrelation aus", () => {
    const html = buildValidationNote(mitValidierung({ correlation: null }));
    assert.match(html, /nicht bestimmbar/);
    assert.doesNotMatch(html, /null/);
  });

  it("nennt die Zahl der verglichenen Stunden", () => {
    assert.match(buildValidationNote(mitValidierung()), /168/);
  });
});

describe("Modelle im Vergleich", () => {
  const { buildComparisonRows, buildComparisonTable } = scenarioModule;

  const mitModellen = () => historicalResult({
    validation: {
      hours_compared: 72, mean_model: 76.8, mean_actual: 90.0,
      mean_absolute_error: 26.4, bias: -13.2, correlation: 0.625,
      actual_price_eur_mwh: [80, 95, 90],
    },
    forecasts: {
      model_1: {
        label: "Forecast-Modell 1", values: [78, 92, 91],
        comparison: { hours_compared: 72, mean_absolute_error: 13.2, correlation: 0.93 },
      },
      model_2: {
        label: "Forecast-Modell 2", values: [79, 93, 90],
        comparison: { hours_compared: 72, mean_absolute_error: 11.8, correlation: 0.95 },
      },
    },
  });

  it("bleibt leer, wenn es nichts zu vergleichen gibt", () => {
    assert.equal(buildComparisonTable(syntheticResult()), "");
    assert.deepEqual(buildComparisonRows(historicalResult()), []);
  });

  it("führt das Merit-Order-Modell und beide Vorhersagen auf", () => {
    const rows = buildComparisonRows(mitModellen());
    assert.equal(rows.length, 3);
    assert.deepEqual(rows.map((r) => r.id), ["merit", "model_1", "model_2"]);
  });

  it("nennt die Vorhersagemodelle nur bei ihrem Anzeigenamen", () => {
    const html = buildComparisonTable(mitModellen());
    assert.match(html, /Forecast-Modell 1/);
    assert.match(html, /Forecast-Modell 2/);
    // Die Funktionsweise gehört nicht in die Oberfläche.
    assert.doesNotMatch(html, /LSTR|ARX|Regime|Regression/i);
  });

  it("hebt das genaueste Modell hervor", () => {
    const html = buildComparisonTable(mitModellen());
    const zeilen = html.split("<tr").filter((z) => z.includes("Forecast-Modell 2"));
    assert.equal(zeilen.length, 1);
    assert.match(zeilen[0], /is-best/);
  });

  it("hebt nichts hervor, wenn nur ein Modell dasteht", () => {
    const nurMerit = historicalResult({
      validation: { hours_compared: 24, mean_absolute_error: 20.0, correlation: 0.6,
                    mean_model: 70, mean_actual: 80, bias: -10, actual_price_eur_mwh: [] },
    });
    assert.doesNotMatch(buildComparisonTable(nurMerit), /is-best/);
  });

  it("kommt mit fehlender Korrelation zurecht", () => {
    const result = mitModellen();
    result.forecasts.model_1.comparison.correlation = null;
    const html = buildComparisonTable(result);
    assert.doesNotMatch(html, /null/);
  });

  it("überspringt Vorhersagen ohne Vergleichswerte", () => {
    const result = mitModellen();
    delete result.forecasts.model_2.comparison;
    assert.equal(buildComparisonRows(result).length, 2);
  });
});

describe("Maßstab des Vergleichs", () => {
  const { buildComparisonTable } = scenarioModule;

  const mitMassstab = (benchmark) => historicalResult({
    validation: {
      hours_compared: 168, mean_model: 70, mean_actual: 80, mean_absolute_error: 39.0,
      bias: -10, correlation: 0.44, actual_price_eur_mwh: [70, 80], benchmark,
    },
    forecasts: {
      baseline: { label: "Einfache Regel", values: [72, 82],
                  comparison: { hours_compared: 168, mean_absolute_error: 32.1, correlation: 0.72 } },
      model_1: { label: "Forecast-Modell 1", values: [71, 81],
                 comparison: { hours_compared: 168, mean_absolute_error: 14.5, correlation: 0.94 } },
    },
  });

  it("nennt die abweichende Referenzreihe, wenn sie benutzt wird", () => {
    const html = buildComparisonTable(mitMassstab("reference"));
    assert.match(html, /viertelstündlichen Day-Ahead-Preise/);
    assert.match(html, /Stundenkontrakt/);
    assert.match(html, /demselben Prüfstand/);
  });

  it("nennt sonst den Stundenkontrakt", () => {
    const html = buildComparisonTable(mitMassstab("smard"));
    assert.match(html, /Stundenkontrakt der Börse/);
    assert.doesNotMatch(html, /viertelstündlichen Day-Ahead-Preise/);
  });

  it("führt die einfache Regel als Maßstab mit auf", () => {
    const html = buildComparisonTable(mitMassstab("reference"));
    assert.match(html, /Einfache Regel/);
  });

  it("hebt das genaueste Modell hervor, nicht die Regel", () => {
    const html = buildComparisonTable(mitMassstab("reference"));
    const beste = html.split("<tr").filter((z) => z.includes("is-best"));
    assert.equal(beste.length, 1);
    assert.match(beste[0], /Forecast-Modell 1/);
  });
});

describe("Brennstoffpreise in der Herkunftszeile", () => {
  const basis = {
    source: "historical",
    series_meta: { data_source: "SMARD.de" },
    params: { hours: 48 },
    kpis: {},
  };

  it("nennt die gemessenen Monatswerte", () => {
    const text = buildFuelNote({
      ...basis,
      fuel_costs: {
        source: "historical",
        months: { "2023-04": { co2_price: 89.6, gas_price: 42.06, origin: "historical",
                              used: { co2_price: "historical", gas_price: "historical" } } },
      },
    });
    assert.match(text, /2023-04/);
    assert.match(text, /CO₂ 90 €\/t/);
    assert.match(text, /Gas 42 €\/MWh/);
  });

  it("führt mehrere Monate einzeln auf, wenn der Zeitraum sie berührt", () => {
    const text = buildFuelNote({
      ...basis,
      fuel_costs: {
        source: "historical",
        months: {
          "2023-03": { co2_price: 89.5, gas_price: 44.0, origin: "historical",
                       used: { co2_price: "historical", gas_price: "historical" } },
          "2023-04": { co2_price: 89.6, gas_price: 42.1, origin: "historical",
                       used: { co2_price: "historical", gas_price: "historical" } },
        },
      },
    });
    assert.match(text, /2023-03/);
    assert.match(text, /2023-04/);
  });

  it("sagt es, wenn mit eingestellten Werten gerechnet wurde", () => {
    const text = buildFuelNote({
      ...basis,
      fuel_costs: { source: "fixed", months: { fixed: { co2_price: 80, gas_price: 32, origin: "fixed",
                                                        used: { co2_price: "fixed", gas_price: "fixed" } } } },
    });
    assert.match(text, /eingestellten Werte/);
  });

  it("bleibt still, wenn gar keine Angabe kommt", () => {
    assert.equal(buildFuelNote({ ...basis }), "");
  });

  it("gibt einen Reglerwert nicht als gemessenen Preis aus", () => {
    const text = buildFuelNote({
      ...basis,
      fuel_costs: {
        source: "historical",
        months: { "2026-09": { co2_price: 84, gas_price: 200, origin: "historical",
                               used: { co2_price: "historical", gas_price: "fixed" } } },
      },
    });
    assert.match(text, /CO₂ 84 €\/t/);
    assert.doesNotMatch(text, /Gas/,
      "Der eingestellte Gaspreis ist kein Preis des Zeitraums");
  });

  it("sagt, wenn ein Preis aus einem früheren Monat fortgeschrieben ist", () => {
    const text = buildFuelNote({
      ...basis,
      fuel_costs: {
        source: "historical",
        months: { "2026-09": { co2_price: 84, gas_price: 62, origin: "historical",
                               used: { co2_price: "historical", gas_price: "historical" },
                               carried_forward: { gas_price: "2026-08" } } },
      },
    });
    assert.match(text, /Fortgeschrieben/);
    assert.match(text, /Gas aus 2026-08/);
  });

  it("verschweigt die Fortschreibung, wenn der Regler ohnehin vorgeht", () => {
    const text = buildFuelNote({
      ...basis,
      fuel_costs: {
        source: "historical",
        months: { "2026-09": { co2_price: 84, gas_price: 150, origin: "historical",
                               used: { co2_price: "historical", gas_price: "fixed" },
                               carried_forward: { gas_price: "2026-08" } } },
      },
    });
    assert.doesNotMatch(text, /Fortgeschrieben/);
  });

  it("hängt an der Herkunftszeile mit dran", () => {
    const text = buildDataNote({
      ...basis,
      fuel_costs: {
        source: "historical",
        months: { "2024-03": { co2_price: 57.5, gas_price: 26.8, origin: "historical",
                              used: { co2_price: "historical", gas_price: "historical" } } },
      },
    });
    assert.match(text, /SMARD/);
    assert.match(text, /2024-03/);
  });
});

describe("Außenhandel in der Herkunftszeile", () => {
  const basis = { source: "historical", series_meta: {}, params: { hours: 48 } };

  it("nennt Richtung und Umfang", () => {
    const text = buildTradeNote({
      ...basis,
      kpis: { net_export_gwh: 420, export_hours: 30, import_hours: 18 },
    });
    assert.match(text, /420 GWh netto ausgeführt/);
    assert.match(text, /30 Export-, 18 Importstunden/);
  });

  it("unterscheidet Ausfuhr von Einfuhr", () => {
    const text = buildTradeNote({
      ...basis,
      kpis: { net_export_gwh: -120, export_hours: 5, import_hours: 43 },
    });
    assert.match(text, /120 GWh netto eingeführt/);
    assert.doesNotMatch(text, /-120/);
  });

  it("erklärt, dass der Handel preisabhängig gerechnet wird", () => {
    const text = buildTradeNote({
      ...basis,
      kpis: { net_export_gwh: 10, export_hours: 2, import_hours: 1 },
    });
    assert.match(text, /preisabhängige Nachfrage/);
  });

  it("bleibt still ohne Handelsdaten", () => {
    assert.equal(buildTradeNote({ ...basis, kpis: {} }), "");
    assert.equal(buildTradeNote({
      ...basis, kpis: { net_export_gwh: 0, export_hours: 0, import_hours: 0 },
    }), "");
  });
});

describe("Regler gegen die Werte des Zeitraums", () => {
  const REAL = ["wind_gw", "solar_gw", "co2_price", "gas_price"];

  function alsMesswerte() {
    form.elements.source.value = "historical";
    applySourceVisibility(form, "historical");
  }

  it("schickt die vier Realwert-Regler nicht mit, solange das Kästchen gesetzt ist", () => {
    alsMesswerte();
    form.elements.real_values.checked = true;
    const params = readParams(form);
    for (const key of REAL) {
      assert.equal(params[key], undefined,
        `${key}: ein mitgesendeter Wert ist vom Backend nicht von einer Eingabe zu unterscheiden`);
    }
    assert.ok(params.hours, "Der Zeitraum geht weiter mit");
  });

  it("schickt sie mit, sobald das Kästchen aus ist", () => {
    alsMesswerte();
    form.elements.real_values.checked = false;
    const params = readParams(form);
    for (const key of REAL) assert.ok(params[key], key);
  });

  it("schickt sie bei erzeugten Profilen immer mit", () => {
    form.elements.source.value = "synthetic";
    applySourceVisibility(form, "synthetic");
    form.elements.real_values.checked = true;
    const params = readParams(form);
    for (const key of REAL) {
      assert.ok(params[key], `${key}: erzeugte Profile haben keinen Zeitraum, auf den man sich beziehen könnte`);
    }
  });

  it("das Kästchen gehört nur zu den echten Messwerten", () => {
    const { form: frisch } = loadPage();
    const feld = frisch.elements.real_values.closest(".field");
    applySourceVisibility(frisch, "synthetic");
    assert.equal(feld.hidden, true);
    applySourceVisibility(frisch, "historical");
    assert.equal(feld.hidden, false);
  });

  it("ein geteilter Link mit eigenem Wert schaltet das Kästchen ab", () => {
    for (const key of REAL) {
      ({ form } = loadPage());
      restoreFromUrl(form, `?source=historical&${key}=120`);
      assert.equal(form.elements.real_values.checked, false, key);
      assert.equal(form.elements[key].value, "120", key);
    }
  });

  it("ein geteilter Link ohne eigenen Wert lässt es an", () => {
    restoreFromUrl(form, "?source=historical&hours=168");
    assert.equal(form.elements.real_values.checked, true);
  });
});

describe("Mindestlast als Schalter", () => {
  it("ist standardmäßig aus und wird dann nicht mitgeschickt", () => {
    assert.equal(form.elements.min_load.checked, false);
    assert.equal(readParams(form).min_load, undefined);
  });

  it("geht mit, wenn sie eingeschaltet ist", () => {
    form.elements.min_load.checked = true;
    assert.equal(readParams(form).min_load, "true");
  });

  it("wird aus einem geteilten Link wiederhergestellt", () => {
    restoreFromUrl(form, "?min_load=true");
    assert.equal(form.elements.min_load.checked, true);
    restoreFromUrl(form, "?min_load=false");
    assert.equal(form.elements.min_load.checked, false);
  });
});

describe("Warnung bei einem hypothetischen Szenario", () => {
  const basis = {
    source: "historical",
    series_meta: {},
    params: { hours: 168 },
    kpis: {},
    validation: {
      mean_model: 90, mean_actual: 85, mean_absolute_error: 12,
      bias: 5, correlation: 0.8, hours_compared: 168,
    },
  };

  it("schweigt, wenn das Szenario die Wirklichkeit abbildet", () => {
    const text = buildValidationNote({ ...basis,
      validation: { ...basis.validation, counterfactual: [] } });
    assert.doesNotMatch(text, /Achtung/);
    assert.match(text, /mittlere Abweichung/);
  });

  it("warnt, sobald ein Regler die Wirklichkeit verlässt", () => {
    const text = buildValidationNote({ ...basis, validation: { ...basis.validation,
      counterfactual: ["Wind 140 GW statt der tatsächlichen 73 GW"] } });
    assert.match(text, /Achtung/);
    assert.match(text, /140 GW/);
    assert.match(text, /nicht die Güte des Modells/);
  });

  it("nennt alle Abweichungen, nicht nur die erste", () => {
    const text = buildValidationNote({ ...basis, validation: { ...basis.validation,
      counterfactual: ["Wind 140 GW statt der tatsächlichen 73 GW",
                       "die gemessene Last wurde gestreckt"] } });
    assert.match(text, /140 GW/);
    assert.match(text, /gestreckt/);
  });

  it("kommt mit einem alten Ergebnis ohne das Feld zurecht", () => {
    const text = buildValidationNote(basis);
    assert.doesNotMatch(text, /Achtung/);
    assert.match(text, /Korrelation/);
  });
});
