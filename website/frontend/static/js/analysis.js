/* Analyseseite: Parameter -> Backend -> Merit-Order, Erzeugung, Preis.
 *
 * Diese Datei verdrahtet nur. Die Logik — welche Parameter gesendet werden,
 * welche Felder zur Quelle gehören, was in der Herkunftszeile steht — liegt in
 * scenario.js und wird dort getestet.
 */

import { renderChart, fmt, formatHour, DEFAULT_TIMEZONE } from "./charts.js";
import {
  MissingData, applySourceVisibility, buildComparisonTable, buildDataNote,
  buildStatusText, buildTradeNote, buildValidationNote, formatDate, hoursHintText,
  readParams, restoreFromUrl, sourceHintText,
} from "./scenario.js";
import {
  applyStoryStep, buildStoryList, buildStoryPanel, findStory, readStoryFromUrl,
} from "./stories.js";
import { buildDiffNote, buildDiffTable, curvesComparable } from "./compare.js";

const form = document.getElementById("sim-form");
const statusLine = document.getElementById("sim-status");
const kpiRow = document.getElementById("kpi-row");
const dataNote = document.getElementById("data-note");
const sourceHint = document.getElementById("source-hint");
const storyList = document.getElementById("story-list");
const storyPanel = document.getElementById("story-panel");
const diffBox = document.getElementById("diff-box");

/* Zustand, der nicht im Formular steht: die geöffnete Geschichte und das
 * gemerkte Szenario. Beides bewusst nur im Arbeitsspeicher — ein gemerktes
 * Szenario, das einen Seitenwechsel überlebt, wäre eine Überraschung. Die
 * Geschichte steht dafür in der Adresszeile und ist so teilbar. */
let stories = [];
let openStory = null;
let openStep = 0;
let pinned = null;

/** Kategorien tragen ihre Farbe als CSS-Variable — so folgt sie dem Farbschema. */
const categoryColor = (id) => `var(--s-${id})`;

const currentSource = () => form.elements.source.value;

/** Bereichsregler zeigen ihren Wert daneben an. */
function bindRangeOutputs() {
  form.querySelectorAll('input[type="range"]').forEach((input) => {
    const output = form.querySelector(`[data-for="${input.name}"]`);
    if (!output) return;
    const sync = () => {
      const digits = Number(input.step) < 1 ? 1 : 0;
      output.textContent = new Intl.NumberFormat("de-DE", {
        minimumFractionDigits: digits, maximumFractionDigits: digits,
      }).format(Number(input.value));
    };
    input.addEventListener("input", sync);
    sync();
  });
}

/** Felder der gewählten Datengrundlage anzeigen und die Hinweise nachziehen. */
function syncSourceFields() {
  const source = currentSource();
  applySourceVisibility(form, source);
  sourceHint.textContent = sourceHintText(source);
  document.getElementById("hours-hint").textContent = hoursHintText(source);
}

/** Grenzen des Datumsfelds aus dem vorliegenden Bestand setzen. */
async function prepareDataRange() {
  try {
    const status = await fetch("/api/data/status").then((r) => r.json());
    const option = form.querySelector('#source option[value="historical"]');
    if (!status.available) {
      option.disabled = true;
      option.textContent = "Echte Messwerte — noch nicht abgerufen";
      return;
    }
    const first = status.range.first.slice(0, 10);
    const last = status.range.last.slice(0, 10);
    const field = form.elements.start;
    field.min = first;
    field.max = last;
    if (!field.value) field.value = last;
    document.getElementById("start-hint").textContent =
      `verfügbar ${formatDate(first)} bis ${formatDate(last)}`;
  } catch (error) {
    console.error("Datenbestand nicht abrufbar", error);
  }
}

/* Die vier Regler, die bei echten Messwerten aus dem Zeitraum kommen können. */
const REALWERT_REGLER = ["wind_gw", "solar_gw", "co2_price", "gas_price"];

/**
 * Die Regler auf das nachziehen, womit wirklich gerechnet wurde.
 *
 * Sonst stünde am Schieber 90 GW, während das Modell mit 101 rechnet — und der
 * Benutzer hätte keine Möglichkeit, den Unterschied zu bemerken. Der Regler
 * zeigt so immer die Wirklichkeit, und wer ihn wegzieht, sieht wovon.
 */
function syncRealValues(result) {
  const kasten = form.elements.real_values;
  if (!kasten || !kasten.checked) return;
  const p = result.params || {};
  const monat = Object.values((result.fuel_costs || {}).months || {})[0] || {};
  const benutzt = monat.used || {};
  const uebernehmen = {
    wind_gw: p.capacity_source === "historical",
    solar_gw: p.capacity_source === "historical",
    co2_price: benutzt.co2_price === "historical",
    gas_price: benutzt.gas_price === "historical",
  };
  for (const name of REALWERT_REGLER) {
    if (!uebernehmen[name] || p[name] == null) continue;
    const feld = form.elements[name];
    if (feld) feld.value = String(Math.round(p[name]));
  }
  bindRangeOutputs();
}

/** Herkunft der Zahlen sichtbar machen — und jede Anpassung des Zeitraums. */
function renderDataNote(result) {
  const text = [buildDataNote(result), buildTradeNote(result)]
    .filter(Boolean).join(" ");
  dataNote.textContent = text;
  dataNote.hidden = text === "";
}

function kpiTile(label, value, unit, hint) {
  return `<div class="kpi">
    <span class="kpi-label">${label}</span>
    <span class="kpi-value">${value}<span class="kpi-unit">${unit}</span></span>
    <span class="kpi-hint">${hint}</span>
  </div>`;
}

function renderKpis(result) {
  const k = result.kpis;
  kpiRow.innerHTML = [
    kpiTile("Mittlerer Börsenpreis", fmt.plain(k.mean_price, 1), "€/MWh", "lastgewichtet"),
    kpiTile("Erneuerbaren-Anteil", fmt.plain(k.renewable_share, 1), "%", "an der Erzeugung"),
    kpiTile("CO₂-Ausstoß", fmt.plain(k.emissions_kt, 0), "kt", `${fmt.plain(k.emission_intensity_g_kwh, 0)} g/kWh`),
    kpiTile("Abgeregelt", fmt.plain(k.curtailed_gwh, 0), "GWh", `in ${k.surplus_hours} von ${result.params.hours} Stunden`),
    kpiTile("Negative Preise", fmt.plain(k.negative_price_hours, 0), "h", k.scarcity_hours ? `${k.scarcity_hours} h Unterdeckung` : "keine Unterdeckung"),
    kpiTile("Speicher", fmt.plain(k.storage_discharged_gwh, 0), "GWh",
            k.storage_discharged_gwh > 0
              ? `${fmt.plain(k.storage_losses_gwh, 0)} GWh Verluste`
              : "Preisabstand zu klein"),
    ...(k.net_export_gwh == null || (!k.export_hours && !k.import_hours) ? [] : [
      kpiTile(k.net_export_gwh >= 0 ? "Nettoexport" : "Nettoimport",
              fmt.plain(Math.abs(k.net_export_gwh), 0), "GWh",
              `${k.export_hours} h aus, ${k.import_hours} h ein`),
    ]),
  ].join("");
}

function renderMeritOrder(payload, meanResidual) {
  renderChart(document.getElementById("chart-merit"), {
    type: "blocks",
    title: "Merit-Order",
    blocks: payload.blocks
      .filter((b) => b.capacity_gw > 0)
      .map((b) => ({
        id: b.id, label: b.name, color: categoryColor(b.category),
        from: b.from_gw, to: b.to_gw, value: b.cost, note: b.note,
      })),
    legend: payload.categories.map((c) => ({ label: c.label, color: categoryColor(c.id) })),
    marker: { value: meanResidual, label: "Ø Residuallast" },
    xLabel: "kumulierte Leistung (GW)",
    yLabel: "€/MWh",
    formatY: (v) => fmt.plain(v, 0),
    ariaLabel: "Merit-Order: Kraftwerksblöcke nach Grenzkosten sortiert. " +
      payload.blocks.map((b) => `${b.name} ${fmt.plain(b.cost, 0)} Euro pro Megawattstunde`).join(", "),
    height: 300,
  });
  document.getElementById("merit-note").textContent =
    `Installierte Leistung insgesamt: ${fmt.gw(payload.total_capacity_gw)}. Die gestrichelte Linie markiert ` +
    "die mittlere Residuallast — den Teil der Last, den Wind und Sonne im Mittel nicht decken.";
}

function renderGeneration(result) {
  const tz = result.display_timezone || DEFAULT_TIMEZONE;
  const order = ["wind", "solar", "sonstige_ee", "speicher", "braunkohle", "steinkohle", "erdgas"];
  const labels = Object.fromEntries(result.categories.map((c) => [c.id, c.label]));
  renderChart(document.getElementById("chart-generation"), {
    type: "stack",
    title: "Erzeugung nach Technologie",
    x: result.timestamps,
    series: order
      .filter((id) => result.generation_gw[id].some((v) => v > 0.01))
      .map((id) => ({ id, label: labels[id], color: categoryColor(id), values: result.generation_gw[id] })),
    formatX: (iso) => formatHour(iso, tz),
    formatY: (v) => fmt.plain(v, 0),
    formatValue: (v) => fmt.gw(v),
    xLabel: "Zeit",
    yLabel: "GW",
    height: 280,
    tooltipExtra: (i) => {
      const spill = result.curtailed_gw[i];
      return spill > 0.01
        ? `<div class="tooltip-row"><span class="tooltip-key">davon abgeregelt</span><span>${fmt.gw(spill)}</span></div>`
        : "";
    },
    ariaLabel: "Gestapelte Erzeugung nach Technologie über den simulierten Zeitraum in Gigawatt.",
  });
}

function renderPrice(result) {
  const tz = result.display_timezone || DEFAULT_TIMEZONE;
  const validation = result.validation;
  // Bei echten Messwerten liegt der tatsächlich gezahlte Preis daneben — der
  // ehrlichste Prüfstein für das Modell.
  const series = [{
    id: "price", label: validation ? "Modell" : "Börsenpreis", color: "var(--accent)",
    values: result.price_eur_mwh, fill: !validation,
  }];
  if (validation) {
    series.push({
      id: "actual", label: "tatsächlich (SMARD)", color: "var(--ink-2)",
      values: validation.actual_price_eur_mwh, dashed: true,
    });
  }
  // Das gemerkte Szenario daneben — aber nur, wenn beide denselben Zeitraum
  // zeigen. Zwei Kurven über einer x-Achse, die für eine davon nicht gilt,
  // wären schlimmer als gar kein Vergleich.
  if (curvesComparable(pinned, result)) {
    series.push({
      id: "pinned", label: "gemerktes Szenario", color: "var(--s-pinned, var(--ink-2))",
      values: pinned.price_eur_mwh, dashed: true,
    });
  }
  // Vorberechnete Vorhersagen, sofern für diesen Zeitraum welche vorliegen.
  const forecasts = result.forecasts || {};
  Object.keys(forecasts).sort().forEach((model, index) => {
    series.push({
      id: model,
      label: forecasts[model].label,
      color: `var(--s-forecast-${index + 1}, var(--ink-2))`,
      values: forecasts[model].values,
      dashed: true,
    });
  });
  renderChart(document.getElementById("chart-price"), {
    type: "line",
    title: validation ? "Börsenpreis: Modell und Wirklichkeit" : "Börsenpreis",
    x: result.timestamps,
    series,
    formatX: (iso) => formatHour(iso, tz),
    formatY: (v) => fmt.plain(v, 0),
    formatValue: (v) => fmt.eur(v),
    xLabel: "Zeit",
    yLabel: "€/MWh",
    yMin: 0,
    height: 240,
    ariaLabel: "Börsenpreis je Stunde in Euro pro Megawattstunde.",
  });
}

/** Wie nah kommt das Modell an den tatsächlich gezahlten Preis? */
function renderValidation(result) {
  const box = document.getElementById("validation-note");
  if (!box) return;
  const text = buildValidationNote(result);
  box.innerHTML = text;
  box.hidden = text === "";

  const vergleich = document.getElementById("comparison-note");
  if (!vergleich) return;
  const tabelle = buildComparisonTable(result);
  vergleich.innerHTML = tabelle;
  vergleich.hidden = tabelle === "";
}

/** Gemerktes und aktuelles Szenario nebeneinander. */
function renderDiff(result) {
  if (!diffBox) return;
  const tabelle = pinned ? buildDiffTable(pinned, result) : "";
  diffBox.hidden = tabelle === "";
  if (!tabelle) return;
  document.getElementById("diff-note").innerHTML = buildDiffNote(pinned, result);
  document.getElementById("diff-table").innerHTML = tabelle;
}

/* ------------------------------------------------------------ Geschichten */

function renderStories() {
  if (!storyList) return;
  storyList.innerHTML = buildStoryList(stories);
  storyList.querySelectorAll("[data-story]").forEach((button) => {
    button.addEventListener("click", () => openStoryAt(button.dataset.story, 0));
  });
}

function renderStoryPanel() {
  if (!storyPanel) return;
  const markup = openStory ? buildStoryPanel(openStory, openStep) : "";
  storyPanel.innerHTML = markup;
  storyPanel.hidden = markup === "";
  if (!markup) return;

  storyPanel.querySelectorAll("[data-step]").forEach((button) => {
    button.addEventListener("click", () => openStoryAt(openStory.id, Number(button.dataset.step)));
  });
  storyPanel.querySelector(".story-prev")
    .addEventListener("click", () => openStoryAt(openStory.id, openStep - 1));
  storyPanel.querySelector(".story-next")
    .addEventListener("click", () => openStoryAt(openStory.id, openStep + 1));
  storyPanel.querySelector(".story-close").addEventListener("click", closeStory);
}

/** Eine Geschichte an einem Schritt öffnen: Formular setzen, rechnen, anzeigen. */
function openStoryAt(id, step) {
  const story = findStory(stories, id);
  if (!story) return;
  const letzter = story.steps.length - 1;
  openStory = story;
  openStep = Math.max(0, Math.min(step, letzter));
  applyStoryStep(form, story.steps[openStep]);
  syncSourceFields();
  bindRangeOutputs();
  renderStoryPanel();
  run();
}

function closeStory() {
  openStory = null;
  openStep = 0;
  renderStoryPanel();
  run();
}

/** Schritt und Geschichte gehören in die Adresszeile, damit beides teilbar ist. */
function storyQuery() {
  return openStory ? `&story=${openStory.id}&schritt=${openStep + 1}` : "";
}

async function loadStories() {
  try {
    stories = await fetch("/api/stories").then((r) => (r.ok ? r.json() : []));
  } catch (error) {
    stories = [];
    console.error("Geschichten nicht abrufbar", error);
  }
  renderStories();
}

function setBusy(busy) {
  document.querySelectorAll(".chart").forEach((c) => { c.dataset.loading = String(busy); });
}

async function run() {
  const params = readParams(form);
  const query = new URLSearchParams(params).toString();
  statusLine.dataset.state = "";
  statusLine.textContent = "Berechne ...";
  setBusy(true);

  try {
    const simulationResponse = await fetch(`/api/simulate?${query}`);
    const simulation = await simulationResponse.json();
    if (simulationResponse.status === 409) {
      // Fehlende Messwerte sind kein Programmfehler, sondern ein Betriebszustand.
      throw new MissingData(simulation.detail, simulation.hint);
    }
    if (!simulationResponse.ok) throw new Error(simulationResponse.status);

    const merit = await fetch(`/api/merit-order?${query}`)
      .then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); });

    const meanResidual = simulation.residual_load_gw.reduce((a, b) => a + Math.max(b, 0), 0) /
      simulation.residual_load_gw.length;

    renderKpis(simulation);
    renderMeritOrder(merit, meanResidual);
    renderGeneration(simulation);
    renderPrice(simulation);
    syncRealValues(simulation);
    renderDataNote(simulation);
    renderValidation(simulation);
    renderDiff(simulation);

    latest = simulation;
    statusLine.textContent = buildStatusText(simulation);

    // Szenario in der Adresszeile ablegen, damit es teilbar und neu ladbar ist.
    history.replaceState(null, "", `?${query}${storyQuery()}`);
  } catch (error) {
    statusLine.dataset.state = "error";
    statusLine.textContent = error instanceof MissingData
      ? `${error.message} ${error.hint}`
      : "Die Simulation konnte nicht geladen werden. Läuft das Backend?";
    if (!(error instanceof MissingData)) console.error(error);
  } finally {
    setBusy(false);
  }
}

let latest = null;
let timer = null;
form.addEventListener("input", () => {
  clearTimeout(timer);
  timer = setTimeout(run, 250);      // Regler nicht bei jedem Pixel neu rechnen
});
form.addEventListener("submit", (event) => { event.preventDefault(); run(); });
form.addEventListener("reset", () => { setTimeout(() => { bindRangeOutputs(); run(); }, 0); });

form.elements.source.addEventListener("change", syncSourceFields);

document.getElementById("pin-scenario").addEventListener("click", () => {
  if (!latest) return;
  pinned = latest;
  renderDiff(latest);
  renderPrice(latest);
});

document.getElementById("unpin-scenario").addEventListener("click", () => {
  pinned = null;
  diffBox.hidden = true;
  if (latest) renderPrice(latest);
});

/* Wer von Hand an den Reglern dreht, verlässt die Geschichte — der Text stünde
 * sonst neben einem Bild, das er nicht mehr beschreibt. Und wer einen der vier
 * Realwert-Regler bewegt, will offensichtlich eine andere Welt durchrechnen;
 * das Häkchen geht dann von selbst weg, statt die Eingabe zu verschlucken. */
form.addEventListener("input", (event) => {
  const kasten = form.elements.real_values;
  if (kasten && kasten.checked && REALWERT_REGLER.includes(event.target.name)) {
    kasten.checked = false;
  }
  if (!openStory) return;
  openStory = null;
  openStep = 0;
  renderStoryPanel();
});

restoreFromUrl(form, location.search);
syncSourceFields();
bindRangeOutputs();

const gewuenschteGeschichte = readStoryFromUrl(location.search);
prepareDataRange()
  .then(loadStories)
  .then(() => {
    if (gewuenschteGeschichte && findStory(stories, gewuenschteGeschichte.id)) {
      openStoryAt(gewuenschteGeschichte.id, gewuenschteGeschichte.step);
    } else {
      run();
    }
  });
