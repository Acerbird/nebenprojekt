/* Analyseseite: Parameter -> Backend -> Merit-Order, Erzeugung, Preis. */

import { renderChart, fmt, formatHour } from "./charts.js";

const form = document.getElementById("sim-form");
const statusLine = document.getElementById("sim-status");
const kpiRow = document.getElementById("kpi-row");

/** Kategorien tragen ihre Farbe als CSS-Variable — so folgt sie dem Farbschema. */
const categoryColor = (id) => `var(--s-${id})`;

const PARAM_KEYS = ["wind_gw", "solar_gw", "co2_price", "gas_price", "peak_load_gw", "hours", "season"];

function readParams() {
  const data = new FormData(form);
  const params = {};
  for (const key of PARAM_KEYS) params[key] = data.get(key);
  return params;
}

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
  const order = ["wind", "solar", "sonstige_ee", "braunkohle", "steinkohle", "erdgas"];
  const labels = Object.fromEntries(result.categories.map((c) => [c.id, c.label]));
  renderChart(document.getElementById("chart-generation"), {
    type: "stack",
    title: "Erzeugung nach Technologie",
    x: result.timestamps,
    series: order
      .filter((id) => result.generation_gw[id].some((v) => v > 0.01))
      .map((id) => ({ id, label: labels[id], color: categoryColor(id), values: result.generation_gw[id] })),
    formatX: formatHour,
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
  renderChart(document.getElementById("chart-price"), {
    type: "line",
    title: "Börsenpreis",
    x: result.timestamps,
    series: [{
      id: "price", label: "Börsenpreis", color: "var(--accent)",
      values: result.price_eur_mwh, fill: true,
    }],
    formatX: formatHour,
    formatY: (v) => fmt.plain(v, 0),
    formatValue: (v) => fmt.eur(v),
    xLabel: "Zeit",
    yLabel: "€/MWh",
    yMin: 0,
    height: 240,
    ariaLabel: "Börsenpreis je Stunde in Euro pro Megawattstunde.",
  });
}

function setBusy(busy) {
  document.querySelectorAll(".chart").forEach((c) => { c.dataset.loading = String(busy); });
}

async function run() {
  const params = readParams();
  const query = new URLSearchParams(params).toString();
  statusLine.dataset.state = "";
  statusLine.textContent = "Berechne ...";
  setBusy(true);

  try {
    const [simulation, merit] = await Promise.all([
      fetch(`/api/simulate?${query}`).then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); }),
      fetch(`/api/merit-order?${query}`).then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); }),
    ]);

    const meanResidual = simulation.residual_load_gw.reduce((a, b) => a + Math.max(b, 0), 0) /
      simulation.residual_load_gw.length;

    renderKpis(simulation);
    renderMeritOrder(merit, meanResidual);
    renderGeneration(simulation);
    renderPrice(simulation);

    statusLine.textContent =
      `${simulation.params.hours} Stunden, ${simulation.season_label} — ${fmt.plain(simulation.kpis.demand_twh, 2)} TWh Verbrauch.`;

    // Szenario in der Adresszeile ablegen, damit es teilbar und neu ladbar ist.
    history.replaceState(null, "", `?${query}`);
  } catch (error) {
    statusLine.dataset.state = "error";
    statusLine.textContent = "Die Simulation konnte nicht geladen werden. Läuft das Backend?";
    console.error(error);
  } finally {
    setBusy(false);
  }
}

/** Parameter aus der Adresszeile ins Formular zurückschreiben. */
function restoreFromUrl() {
  const query = new URLSearchParams(location.search);
  for (const key of PARAM_KEYS) {
    const value = query.get(key);
    const field = form.elements[key];
    if (value !== null && field) field.value = value;
  }
}

let timer = null;
form.addEventListener("input", () => {
  clearTimeout(timer);
  timer = setTimeout(run, 250);      // Regler nicht bei jedem Pixel neu rechnen
});
form.addEventListener("submit", (event) => { event.preventDefault(); run(); });
form.addEventListener("reset", () => { setTimeout(() => { bindRangeOutputs(); run(); }, 0); });

restoreFromUrl();
bindRangeOutputs();
run();
