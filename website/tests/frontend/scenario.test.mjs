/* Tests der Formularlogik der Analyseseite.
 *
 * Geprüft wird gegen die echte, vom Template gerenderte Seite: Welche Parameter
 * gehen an die API, welche Felder gehören zu welcher Datengrundlage, was steht
 * in der Herkunftszeile.
 */

import assert from "node:assert/strict";
import { beforeEach, describe, it } from "node:test";

import { loadPage, historicalResult, syntheticResult } from "./helpers.mjs";

const {
  applySourceVisibility, buildDataNote, buildStatusText, formatDate,
  hoursHintText, readParams, restoreFromUrl, sourceHintText, MissingData,
} = await import("../../frontend/static/js/scenario.js");

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
