/* Logik der Analyseseite, getrennt von ihrer Verdrahtung.
 *
 * Nichts in dieser Datei greift beim Laden auf das Dokument zu — alle
 * Funktionen bekommen, worauf sie wirken, als Parameter. Dadurch lässt sich
 * die Logik testen, ohne eine Seite zu öffnen: Welche Parameter gehen an die
 * API, welche Felder gehören zu welcher Datengrundlage, was steht in der
 * Herkunftszeile.
 */

import { fmt } from "./charts.js";

export const SYNTHETIC = "synthetic";
export const HISTORICAL = "historical";

/** Fehlende Messwerte: ein erklärbarer Zustand, kein Absturz. */
export class MissingData extends Error {
  constructor(message, hint) {
    super(message);
    this.name = "MissingData";
    this.hint = hint || "";
  }
}

/**
 * Parameter für die API zusammenstellen.
 *
 * Bei echten Messwerten gilt die gemessene Last. Der Höchstlast-Regler wird
 * deshalb nur mitgeschickt, wenn ausdrücklich das Strecken gewünscht ist —
 * sonst hielte man eine skalierte Kurve für eine gemessene. Die Jahreszeit
 * gehört umgekehrt nur zu den erzeugten Profilen.
 */
export function readParams(form) {
  const data = new FormData(form);
  const source = data.get("source") === HISTORICAL ? HISTORICAL : SYNTHETIC;
  const params = { source };

  for (const key of ["wind_gw", "solar_gw", "co2_price", "gas_price", "hours"]) {
    const value = data.get(key);
    if (value !== null) params[key] = value;
  }

  if (source === HISTORICAL) {
    if (data.get("start")) params.start = data.get("start");
    if (data.get("scale_load") && data.get("peak_load_gw") !== null) {
      params.peak_load_gw = data.get("peak_load_gw");
    }
  } else {
    if (data.get("season")) params.season = data.get("season");
    if (data.get("peak_load_gw") !== null) params.peak_load_gw = data.get("peak_load_gw");
  }
  return params;
}

export function sourceHintText(source) {
  return source === HISTORICAL
    ? "gemessene Last, Wind- und Solareinspeisung der Bundesnetzagentur"
    : "nachgebildete Tagesgänge, jederzeit verfügbar";
}

export function hoursHintText(source) {
  return source === HISTORICAL
    ? "Länge des Zeitraums ab dem gewählten Tag"
    : "Stunden ab Montag 00:00";
}

/**
 * Felder ein- und ausblenden, die nur zu einer Quelle passen.
 *
 * Ausgeblendete Eingaben werden zusätzlich deaktiviert, damit sie nicht im
 * Formulardatensatz landen.
 */
export function applySourceVisibility(form, source) {
  form.querySelectorAll("[data-when]").forEach((element) => {
    const visible = element.dataset.when === source;
    element.hidden = !visible;
    element.querySelectorAll("input, select").forEach((field) => { field.disabled = !visible; });
  });
}

/** Datum aus ISO in die hier übliche Schreibweise bringen. */
export function formatDate(iso) {
  const [year, month, day] = iso.slice(0, 10).split("-");
  return `${day}.${month}.${year}`;
}

/**
 * Herkunftszeile für echte Messwerte.
 *
 * Gibt eine leere Zeichenkette zurück, wenn die Zahlen aus erzeugten Profilen
 * stammen — dann gibt es keine Quelle zu nennen. Jede Abweichung vom
 * Gewünschten (verschobener Zeitraum, gestreckte Last, überbrückte Lücken)
 * gehört hier hinein, damit niemand Modellzahlen für Messwerte hält.
 */
export function buildDataNote(result) {
  if (!result || result.source !== HISTORICAL) return "";

  const meta = result.series_meta || {};
  const parts = [`Messwerte: ${meta.data_source || "SMARD"}.`];

  if (meta.installed_gw) {
    parts.push(`Tatsächlich installiert im Zeitraum: ${fmt.plain(meta.installed_gw.wind, 0)} GW Wind, ` +
               `${fmt.plain(meta.installed_gw.solar, 0)} GW Photovoltaik.`);
  }
  const gaps = meta.gaps_filled
    ? Object.values(meta.gaps_filled).reduce((a, b) => a + b, 0) : 0;
  if (gaps) parts.push(`${gaps} fehlende Stundenwerte wurden überbrückt.`);

  const adjustments = (result.params && result.params.adjustments) || {};
  if (adjustments.reason) parts.push(adjustments.reason);
  if (meta.load_scaled_by) {
    parts.push(`Die gemessene Last wurde mit Faktor ${fmt.plain(meta.load_scaled_by, 2)} gestreckt.`);
  }
  return parts.join(" ");
}

/** Statuszeile unter dem Formular. */
export function buildStatusText(result) {
  const herkunft = result.source === HISTORICAL ? "Messwerte" : "erzeugte Profile";
  return `${result.params.hours} Stunden, ${result.season_label} (${herkunft}) — ` +
         `${fmt.plain(result.kpis.demand_twh, 2)} TWh Verbrauch.`;
}

/**
 * Formular aus einer Adresszeile füllen, damit geteilte Szenarien wieder
 * dasselbe zeigen.
 */
export function restoreFromUrl(form, search) {
  const query = new URLSearchParams(search);
  const keys = ["source", "start", "wind_gw", "solar_gw", "co2_price", "gas_price",
                "peak_load_gw", "hours", "season"];
  for (const key of keys) {
    const value = query.get(key);
    const field = form.elements[key];
    if (value === null || !field) continue;
    field.value = value;
  }
  // Wurde zu echten Messwerten eine Höchstlast mitgegeben, war das Strecken gewollt.
  const scale = form.elements.scale_load;
  if (scale) {
    scale.checked = query.get("source") === HISTORICAL && query.get("peak_load_gw") !== null;
  }
  return query;
}
