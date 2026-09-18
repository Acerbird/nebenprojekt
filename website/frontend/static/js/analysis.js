/* Analyseseite: Parameter -> Backend -> Merit-Order, Erzeugung, Preis.
 *
 * Diese Datei verdrahtet nur. Die Logik — welche Parameter gesendet werden,
 * welche Felder zur Quelle gehören, was in der Herkunftszeile steht — liegt in
 * scenario.js und wird dort getestet.
 */

import { renderChart, fmt, formatHour, DEFAULT_TIMEZONE } from "./charts.js";
import {
  MissingData, applySourceVisibility, buildComparisonTable, buildDataNote,
  buildStatusText, buildValidationNote, formatDate, hoursHintText, readParams,
  restoreFromUrl, sourceHintText,
} from "./scenario.js";

const form = document.getElementById("sim-form");
const statusLine = document.getElementById("sim-status");
const kpiRow = document.getElementById("kpi-row");
const dataNote = document.getElementById("data-note");
const sourceHint = document.getElementById("source-hint");

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

/** Herkunft der Zahlen sichtbar machen — und jede Anpassung des Zeitraums. */
function renderDataNote(result) {
  const text = buildDataNote(result);
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
    renderDataNote(simulation);
    renderValidation(simulation);

    statusLine.textContent = buildStatusText(simulation);

    // Szenario in der Adresszeile ablegen, damit es teilbar und neu ladbar ist.
    history.replaceState(null, "", `?${query}`);
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

let timer = null;
form.addEventListener("input", () => {
  clearTimeout(timer);
  timer = setTimeout(run, 250);      // Regler nicht bei jedem Pixel neu rechnen
});
form.addEventListener("submit", (event) => { event.preventDefault(); run(); });
form.addEventListener("reset", () => { setTimeout(() => { bindRangeOutputs(); run(); }, 0); });

form.elements.source.addEventListener("change", syncSourceFields);

restoreFromUrl(form, location.search);
syncSourceFields();
bindRangeOutputs();
prepareDataRange().then(run);
