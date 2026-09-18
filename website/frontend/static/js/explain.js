/* Diagramme auf den Erklärseiten: Tagesgänge von Last und Photovoltaik. */

import { renderChart, fmt } from "./charts.js";

const hourLabel = (_value, index) => `${String(index).padStart(2, "0")}:00`;

async function load() {
  const response = await fetch("/api/profiles/day");
  if (!response.ok) throw new Error(String(response.status));
  return response.json();
}

function renderLoadProfile(data) {
  const container = document.getElementById("chart-load-profile");
  if (!container) return;
  renderChart(container, {
    type: "line",
    title: "Tagesgang der Last",
    x: data.hours,
    series: [
      { id: "werktag", label: "Werktag", color: "var(--s-wind)", values: data.load.werktag.map((v) => v * 100) },
      { id: "wochenende", label: "Wochenende", color: "var(--s-erdgas)", values: data.load.wochenende.map((v) => v * 100) },
    ],
    formatX: hourLabel,
    formatY: (v) => fmt.plain(v, 0),
    formatValue: (v) => `${fmt.plain(v, 0)} %`,
    xLabel: "Uhrzeit",
    yLabel: "% der Höchstlast",
    height: 240,
    ariaLabel: "Tagesgang der Stromlast an Werktagen und am Wochenende, in Prozent der Jahreshöchstlast.",
  });
}

function renderSolarProfile(data) {
  const container = document.getElementById("chart-solar-profile");
  if (!container) return;
  const order = ["winter", "uebergang", "sommer"];
  const colors = { winter: "var(--s-wind)", uebergang: "var(--s-sonstige_ee)", sommer: "var(--s-solar)" };
  renderChart(container, {
    type: "line",
    title: "Kapazitätsfaktor der Photovoltaik",
    x: data.hours,
    series: order.map((key) => ({
      id: key, label: data.seasons[key], color: colors[key],
      values: data.solar_cf[key].map((v) => v * 100),
    })),
    formatX: hourLabel,
    formatY: (v) => fmt.plain(v, 0),
    formatValue: (v) => `${fmt.plain(v, 0)} %`,
    xLabel: "Uhrzeit",
    yLabel: "% der Nennleistung",
    height: 240,
    ariaLabel: "Tagesgang der Photovoltaik-Einspeisung je Jahreszeit, in Prozent der installierten Leistung.",
  });
}

load()
  .then((data) => { renderLoadProfile(data); renderSolarProfile(data); })
  .catch((error) => {
    // Die Diagrammgerueste entstehen erst beim Zeichnen — hier steht nur die
    // leere <figure> aus dem Template, also wird die Meldung dort angehaengt.
    console.error("Profile konnten nicht geladen werden:", error);
    document.querySelectorAll(".chart").forEach((figure) => {
      const message = document.createElement("p");
      message.className = "muted small";
      message.textContent = "Diagramm konnte nicht geladen werden — läuft das Backend?";
      figure.appendChild(message);
    });
  });
