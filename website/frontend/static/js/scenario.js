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
 *
 * Dasselbe gilt für Ausbau und Brennstoffpreise: Solange die Werte des
 * Zeitraums gewünscht sind, dürfen diese vier Regler gar nicht mitgeschickt
 * werden. Ein mitgesendeter Wert ist für das Backend nicht von einer Eingabe zu
 * unterscheiden — er würde die gemessenen Werte stillschweigend verdrängen, und
 * niemand sähe, warum das Frühjahr 2023 zu billig herauskommt oder warum mit
 * 90 statt 101 Gigawatt Photovoltaik gerechnet wird.
 */
export function readParams(form) {
  const data = new FormData(form);
  const source = data.get("source") === HISTORICAL ? HISTORICAL : SYNTHETIC;
  const params = { source };

  const realwerte = source === HISTORICAL && Boolean(data.get("real_values"));
  const regler = realwerte
    ? ["hours"]
    : ["wind_gw", "solar_gw", "co2_price", "gas_price", "hours"];
  for (const key of regler) {
    const value = data.get(key);
    if (value !== null) params[key] = value;
  }
  if (data.get("min_load")) params.min_load = "true";

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
  const brennstoff = buildFuelNote(result);
  if (brennstoff) parts.push(brennstoff);
  return parts.join(" ");
}

/**
 * Womit gerechnet wurde: gemessene Monatspreise oder eingestellte Werte.
 *
 * Das gehört sichtbar gemacht, weil sonst niemand erklären kann, warum
 * derselbe Kraftwerkspark im Frühjahr 2023 andere Preise liefert als 2024 —
 * der Unterschied steckt im Gaspreis, nicht im Modell.
 */
export function buildFuelNote(result) {
  const fuel = result && result.fuel_costs;
  if (!fuel) return "";
  const monate = Object.entries(fuel.months || {})
    .filter(([, werte]) => werte.origin === "historical");
  if (!monate.length) {
    return "Brennstoff- und CO₂-Preise: die eingestellten Werte.";
  }
  // Nur nennen, was wirklich gemessen ist. Ein Reglerwert als „Preis des
  // Zeitraums" auszugeben wäre die schlimmere Sorte Fehler: nicht falsch
  // gerechnet, aber falsch behauptet.
  const beschreibung = monate.map(([monat, werte]) => {
    const herkunft = werte.used || {};
    const teile = [];
    if (herkunft.co2_price === "historical") teile.push(`CO₂ ${fmt.plain(werte.co2_price, 0)} €/t`);
    if (herkunft.gas_price === "historical") teile.push(`Gas ${fmt.plain(werte.gas_price, 0)} €/MWh`);
    return teile.length ? `${monat}: ${teile.join(", ")}` : "";
  }).filter(Boolean);
  if (!beschreibung.length) return "Brennstoff- und CO₂-Preise: die eingestellten Werte.";

  const fortgeschrieben = monate.flatMap(([, werte]) =>
    Object.entries(werte.carried_forward || {})
      .filter(([feld]) => (werte.used || {})[feld] === "historical")
      .map(([feld, herkunftsmonat]) =>
        `${feld === "gas_price" ? "Gas" : feld === "co2_price" ? "CO₂" : "Kohle"} aus ${herkunftsmonat}`));

  let text = `Brennstoffpreise des Zeitraums (${beschreibung.join("; ")}). ` +
             `Ein bewegter Regler gilt dennoch vor.`;
  if (fortgeschrieben.length) {
    text += ` Fortgeschrieben, weil die Quelle noch nicht so weit reicht: ` +
            `${[...new Set(fortgeschrieben)].join(", ")}.`;
  }
  return text;
}

/**
 * Was der Außenhandel im Zeitraum bewirkt hat.
 *
 * Wichtig für das Verständnis: Das Modell rechnet den Handel nicht als
 * gegebene Menge, sondern als preisabhängige Nachfrage. Die Nachbarn kaufen,
 * wenn Deutschland billig ist. Andersherum — den gemessenen Export als feste
 * Nachfrage einzusetzen — dreht die Ursache um und macht das Modell schlechter.
 */
export function buildTradeNote(result) {
  const kpis = (result && result.kpis) || {};
  if (kpis.net_export_gwh == null) return "";
  if (!kpis.export_hours && !kpis.import_hours) return "";
  const richtung = kpis.net_export_gwh >= 0 ? "ausgeführt" : "eingeführt";
  return `Außenhandel: ${fmt.plain(Math.abs(kpis.net_export_gwh), 0)} GWh netto ${richtung} ` +
         `(${kpis.export_hours} Export-, ${kpis.import_hours} Importstunden). ` +
         `Gerechnet als preisabhängige Nachfrage — die Nachbarn kaufen, wenn es hier billig ist.`;
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
  // Ein mitgegebener Wert heißt: Der Regler war gewollt, nicht der Realwert.
  const realwerte = form.elements.real_values;
  const eigene = ["co2_price", "gas_price", "wind_gw", "solar_gw"];
  if (realwerte && eigene.some((key) => query.has(key))) {
    realwerte.checked = false;
  }
  const mindestlast = form.elements.min_load;
  if (mindestlast && query.has("min_load")) {
    mindestlast.checked = query.get("min_load") !== "false";
  }
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

/**
 * Wie nah kommt das Modell an den tatsächlich gezahlten Börsenpreis?
 *
 * Gibt HTML zurück, weil die Kennzahlen als kleine Tabelle klarer lesbar sind
 * als im Fließtext. Bei erzeugten Profilen gibt es nichts zu vergleichen —
 * dann bleibt die Zeichenkette leer.
 *
 * Die Korrelation steht bewusst gleichberechtigt neben der Abweichung: Ein
 * Lernmodell mit sechs Kraftwerksblöcken wird das Preisniveau kaum treffen.
 * Ob es den Verlauf trifft, ist die interessantere Frage.
 */
export function buildValidationNote(result) {
  const v = result && result.validation;
  if (!v) return "";

  const zahl = (wert, stellen = 1) => fmt.plain(wert, stellen);
  const richtung = v.bias < 0 ? "zu niedrig" : "zu hoch";
  const guete = v.correlation === null ? "nicht bestimmbar"
    : v.correlation > 0.8 ? "trifft den Verlauf gut"
    : v.correlation > 0.5 ? "trifft den Verlauf grob"
    : v.correlation > 0.2 ? "trifft den Verlauf nur schwach"
    : "trifft den Verlauf nicht";

  // Ein Szenario, das eine andere Welt rechnet, darf seine Abweichung nicht als
  // Modellgüte ausgeben. Wer die Windleistung verdoppelt, bekommt eine mittlere
  // Abweichung von 59 €/MWh — das misst aber nicht das Modell, sondern den
  // Unterschied zwischen 140 und 73 Gigawatt.
  const anders = (v.counterfactual || []);
  const warnung = anders.length ? `
    <p class="validation-warning"><strong>Achtung:</strong> Dieses Szenario bildet nicht
    ab, was in diesem Zeitraum wirklich war — ${anders.join(", ")}. Die Zahlen unten
    messen deshalb nicht die Güte des Modells, sondern den Abstand zwischen dieser
    Rechnung und dem, was tatsächlich passiert ist.</p>` : "";

  return `
    <strong>Modell gegen Wirklichkeit</strong>${warnung}
    <dl class="validation-grid">
      <div><dt>Modell im Mittel</dt><dd>${zahl(v.mean_model)} €/MWh</dd></div>
      <div><dt>tatsächlich</dt><dd>${zahl(v.mean_actual)} €/MWh</dd></div>
      <div><dt>mittlere Abweichung</dt><dd>${zahl(v.mean_absolute_error)} €/MWh</dd></div>
      <div><dt>Verzerrung</dt><dd>${zahl(Math.abs(v.bias))} €/MWh ${richtung}</dd></div>
      <div><dt>Korrelation</dt><dd>${v.correlation === null ? "–" : zahl(v.correlation, 2)} — ${guete}</dd></div>
      <div><dt>verglichene Stunden</dt><dd>${v.hours_compared}</dd></div>
    </dl>`;
}

/**
 * Vergleichstabelle: Merit-Order-Modell und die Vorhersagemodelle nebeneinander,
 * gemessen am tatsächlich gezahlten Preis.
 *
 * Der interessante Punkt für Lesende ist nicht, welches Modell gewinnt, sondern
 * wie unterschiedlich gut zwei ganz verschiedene Herangehensweisen treffen: ein
 * Modell, das den Kraftwerkseinsatz nachrechnet, und eines, das aus der
 * Vergangenheit lernt.
 */
export function buildComparisonRows(result) {
  const rows = [];
  if (result && result.validation) {
    rows.push({ id: "merit", label: "Merit-Order-Modell", ...kennzahlen(result.validation) });
  }
  const forecasts = (result && result.forecasts) || {};
  for (const model of Object.keys(forecasts).sort()) {
    const entry = forecasts[model];
    if (entry.comparison) {
      rows.push({ id: model, label: entry.label, ...kennzahlen(entry.comparison) });
    }
  }
  return rows;
}

function kennzahlen(vergleich) {
  return {
    error: vergleich.mean_absolute_error,
    correlation: vergleich.correlation,
    hours: vergleich.hours_compared,
  };
}

/** Die Vergleichstabelle als HTML; leer, wenn es nichts zu vergleichen gibt. */
export function buildComparisonTable(result) {
  const rows = buildComparisonRows(result);
  if (rows.length === 0) return "";
  const beste = Math.min(...rows.map((r) => r.error));
  const zeilen = rows.map((r) => `
      <tr${r.error === beste && rows.length > 1 ? ' class="is-best"' : ""}>
        <th scope="row">${r.label}</th>
        <td>${fmt.plain(r.error, 1)}</td>
        <td>${r.correlation === null ? "–" : fmt.plain(r.correlation, 2)}</td>
      </tr>`).join("");
  const massstab = result.validation && result.validation.benchmark === "reference"
    ? "Gemessen wird an der Preisreihe, für die die Vorhersagemodelle gebaut wurden: " +
      "dem Mittel der vier viertelstündlichen Day-Ahead-Preise einer Stunde. Die oben " +
      "gezeichnete Kurve zeigt dagegen den Stundenkontrakt, den SMARD ausweist — beide " +
      "laufen eng beieinander, weichen je Stunde aber ab. Alle Modelle stehen dabei auf " +
      "demselben Prüfstand."
    : "Gemessen wird am Stundenkontrakt der Börse, wie SMARD ihn ausweist.";

  return `
    <strong>Modelle im Vergleich mit dem tatsächlichen Preis</strong>
    <table class="comparison">
      <thead><tr><th scope="col">Modell</th><th scope="col">Abweichung</th><th scope="col">Korrelation</th></tr></thead>
      <tbody>${zeilen}</tbody>
    </table>
    <p class="small muted">Abweichung in €/MWh, im Mittel über ${rows[0].hours} Stunden. Je kleiner, desto näher am Markt; die Korrelation sagt, ob der Verlauf stimmt. ${massstab}</p>`;
}
