/* Diagramme der Themenseiten, die nicht auf Profilen beruhen.
 *
 * Bislang eines: die gemessene Außenhandelskurve auf /handel. Sie kommt
 * gleichmäßig abgetastet aus dem Backend — die Stützstellen liegen in
 * ungleichen Preisabständen, und nebeneinandergesetzt ergäben sie ein
 * verzerrtes Bild der Steigung.
 */

import { renderChart, fmt } from "./charts.js";

async function loadExchange() {
  const response = await fetch("/api/exchange-curve");
  if (!response.ok) throw new Error(String(response.status));
  return response.json();
}

function renderExchange(data) {
  const container = document.getElementById("chart-exchange");
  if (!container || !data.prices.length) return;
  renderChart(container, {
    type: "line",
    title: "Außenhandel nach Preisklasse",
    x: data.prices,
    series: [{
      id: "net_export",
      label: "Nettoexport",
      color: "var(--s-erdgas)",
      values: data.net_export_gw,
    }],
    formatX: (value) => `${fmt.plain(value, 0)} €`,
    formatY: (v) => fmt.plain(v, 0),
    formatValue: (v) => (v >= 0
      ? `${fmt.plain(v, 1)} GW Ausfuhr`
      : `${fmt.plain(-v, 1)} GW Einfuhr`),
    xLabel: "Day-Ahead-Preis (€/MWh)",
    yLabel: "Nettoexport (GW)",
    height: 260,
    ariaLabel: "Medianer Nettoexport Deutschlands je Preisklasse. Die Kurve fällt "
      + "über den ganzen Bereich: Bei tiefen Preisen führt Deutschland aus, bei hohen ein.",
  });
}

loadExchange()
  .then(renderExchange)
  .catch((error) => {
    const container = document.getElementById("chart-exchange");
    if (container) {
      container.insertAdjacentHTML("beforeend",
        '<p class="chart-error">Die Handelskurve konnte nicht geladen werden. '
        + 'Die Zahlen im Text darunter gelten unabhängig davon.</p>');
    }
    console.error("Außenhandelskurve:", error);
  });

/* Der zweite Fall: Stunden- gegen Viertelstundenpreis eines Tages.
 *
 * Die Aussage der Marktseite in einem Bild — die Treppe des Stundenkontrakts
 * gegen die Zacken der Viertelstunden. Was zwischen beiden liegt, ist die
 * Spanne, die ein Speicher verliert, der nur Stunden handelt.
 */

function stundeAus(stempel, zone) {
  return new Date(stempel * 1000).toLocaleTimeString("de-DE", {
    hour: "2-digit", minute: "2-digit", timeZone: zone,
  });
}

function renderQuarterDay(data) {
  const container = document.getElementById("chart-quarter");
  if (!container || !data.quarter || !data.quarter.length) return;
  const zone = data.display_timezone || "Europe/Berlin";
  renderChart(container, {
    type: "line",
    title: "Stunden- und Viertelstundenpreis",
    x: data.timestamps,
    series: [
      { id: "hourly", label: "Stundenkontrakt", color: "var(--s-wind)", values: data.hourly },
      { id: "quarter", label: "Viertelstunden", color: "var(--s-erdgas)", values: data.quarter },
    ],
    formatX: (value) => stundeAus(value, zone),
    formatY: (v) => fmt.plain(v, 0),
    formatValue: (v) => `${fmt.plain(v, 1)} €/MWh`,
    xLabel: "Uhrzeit",
    yLabel: "€/MWh",
    height: 260,
    ariaLabel: "Day-Ahead-Preis eines Tages, einmal als Stundenkontrakt und einmal "
      + "je Viertelstunde. Die Viertelstunden schwingen deutlich weiter aus als die "
      + "Stundentreppe, besonders am Morgen und am Abend.",
  });
}

fetch("/api/quarter-prices")
  .then((response) => (response.ok ? response.json() : Promise.reject(response.status)))
  .then(renderQuarterDay)
  .catch((error) => {
    const container = document.getElementById("chart-quarter");
    if (container) {
      container.insertAdjacentHTML("beforeend",
        '<p class="chart-error">Der Beispieltag konnte nicht geladen werden. '
        + 'Die Zahlen im Text darunter gelten unabhängig davon.</p>');
    }
    console.error("Viertelstundenpreise:", error);
  });
