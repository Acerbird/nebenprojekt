/* Zwei Szenarien nebeneinander.
 *
 * Ein einzelnes Ergebnis beantwortet die Frage „was passiert?", nicht die
 * Frage „was ändert sich dadurch?". Für die zweite braucht es zwei Läufe. Der
 * Vergleich merkt sich deshalb ein Szenario und stellt jedes weitere daneben.
 *
 * Reine Logik, ohne DOM und ohne Netz: Was verglichen wird, wie ein Szenario
 * beschriftet wird, und wie aus zwei Kennzahlensätzen eine Tabelle wird.
 */

import { fmt } from "./charts.js";
import { escapeHtml } from "./stories.js";

/**
 * Welche Kennzahlen werden verglichen — und was bedeutet „besser"?
 *
 * `better` ist bewusst nicht überall gesetzt. Ob ein höherer Preis gut oder
 * schlecht ist, hängt davon ab, wen man fragt; das Modell hat dazu keine
 * Meinung. Bei Emissionen und abgeregelter Energie ist die Richtung dagegen
 * unstrittig.
 */
export const METRICS = [
  { key: "mean_price", label: "Mittlerer Börsenpreis", unit: "€/MWh", digits: 1 },
  { key: "renewable_share", label: "Erneuerbaren-Anteil", unit: "%", digits: 1, better: "up" },
  { key: "emissions_kt", label: "CO₂-Ausstoß", unit: "kt", digits: 0, better: "down" },
  { key: "emission_intensity_g_kwh", label: "davon je Kilowattstunde", unit: "g/kWh", digits: 0, better: "down" },
  { key: "curtailed_gwh", label: "Abgeregelt", unit: "GWh", digits: 0, better: "down" },
  { key: "negative_price_hours", label: "Stunden mit negativem Preis", unit: "h", digits: 0 },
  { key: "storage_discharged_gwh", label: "Aus Speichern entnommen", unit: "GWh", digits: 0 },
  { key: "demand_twh", label: "Verbrauch", unit: "TWh", digits: 2 },
];

/** Kurze Beschriftung eines Szenarios — so viel, dass man die beiden auseinanderhält. */
export function describeScenario(result) {
  if (!result) return "";
  const p = result.params || {};
  const teile = [];
  teile.push(result.source === "historical" ? result.season_label : `erzeugt, ${result.season_label}`);
  teile.push(`${fmt.plain(p.wind_gw, 0)} GW Wind`);
  teile.push(`${fmt.plain(p.solar_gw, 0)} GW PV`);

  // Brennstoffpreise nur nennen, wenn sie eingestellt wurden. Der gemessene
  // Monatswert gehört zum Zeitraum und nicht zur Einstellung.
  const herkunft = Object.values((result.fuel_costs || {}).months || {})[0] || {};
  const benutzt = herkunft.used || {};
  if (benutzt.co2_price !== "historical") teile.push(`CO₂ ${fmt.plain(p.co2_price, 0)} €/t`);
  if (benutzt.gas_price !== "historical") teile.push(`Gas ${fmt.plain(p.gas_price, 0)} €/MWh`);
  if (p.min_load) teile.push("mit Mindestlast");
  return teile.join(" · ");
}

/**
 * Die Unterschiede zwischen zwei Läufen, Kennzahl für Kennzahl.
 *
 * Verglichen wird nur, was in beiden vorkommt. Fehlt eine Kennzahl in einem
 * der beiden Läufe, bleibt die Zeile weg — eine Null hineinzuschreiben, wo
 * nichts gemessen wurde, wäre die schlechtere Antwort.
 */
export function buildDiffRows(pinned, current) {
  if (!pinned || !current) return [];
  const a = pinned.kpis || {};
  const b = current.kpis || {};
  return METRICS
    .filter((m) => typeof a[m.key] === "number" && typeof b[m.key] === "number")
    .map((m) => {
      const delta = b[m.key] - a[m.key];
      let richtung = "";
      if (m.better && Math.abs(delta) > 1e-9) {
        const besser = m.better === "up" ? delta > 0 : delta < 0;
        richtung = besser ? "better" : "worse";
      }
      return {
        key: m.key, label: m.label, unit: m.unit, digits: m.digits,
        pinned: a[m.key], current: b[m.key], delta, direction: richtung,
      };
    });
}

/** Vorzeichenbehaftete Differenz — „+3,2" sagt mehr als „3,2". */
export function formatDelta(row) {
  if (Math.abs(row.delta) < 0.5 / Math.pow(10, row.digits)) return "±0";
  const zahl = fmt.plain(Math.abs(row.delta), row.digits);
  return `${row.delta > 0 ? "+" : "−"}${zahl}`;
}

/** Relative Änderung, wo sie etwas aussagt. Bei Werten nahe null tut sie es nicht. */
export function formatRelative(row) {
  if (Math.abs(row.pinned) < 0.05) return "";
  const anteil = (row.delta / Math.abs(row.pinned)) * 100;
  if (Math.abs(anteil) < 0.5) return "";
  return `${anteil > 0 ? "+" : "−"}${fmt.plain(Math.abs(anteil), 0)} %`;
}

export function buildDiffTable(pinned, current) {
  const rows = buildDiffRows(pinned, current);
  if (!rows.length) return "";
  const zeilen = rows.map((row) => `
      <tr${row.direction ? ` class="is-${row.direction}"` : ""}>
        <th scope="row">${escapeHtml(row.label)}</th>
        <td>${fmt.plain(row.pinned, row.digits)}</td>
        <td>${fmt.plain(row.current, row.digits)}</td>
        <td class="diff-delta">${formatDelta(row)}<span class="diff-share">${formatRelative(row)}</span></td>
        <td class="diff-unit">${escapeHtml(row.unit)}</td>
      </tr>`).join("");

  return `
    <table class="diff">
      <caption class="visually-hidden">Kennzahlen des gemerkten und des aktuellen Szenarios im Vergleich</caption>
      <thead>
        <tr>
          <th scope="col">Kennzahl</th>
          <th scope="col">gemerkt</th>
          <th scope="col">jetzt</th>
          <th scope="col">Differenz</th>
          <th scope="col"><span class="visually-hidden">Einheit</span></th>
        </tr>
      </thead>
      <tbody>${zeilen}</tbody>
    </table>`;
}

/**
 * Lassen sich die beiden Preiskurven überhaupt übereinanderlegen?
 *
 * Nur wenn beide Läufe gleich viele Stunden haben und zur selben Zeit beginnen.
 * Andernfalls zeigte das Diagramm zwei Kurven, die nichts miteinander zu tun
 * haben — und niemand sähe, dass die x-Achse für die eine nicht gilt.
 */
export function curvesComparable(pinned, current) {
  if (!pinned || !current) return false;
  const a = pinned.timestamps || [];
  const b = current.timestamps || [];
  return a.length > 0 && a.length === b.length && a[0] === b[0];
}

/** Hinweis über der Vergleichstabelle. */
export function buildDiffNote(pinned, current) {
  if (!pinned || !current) return "";
  const gleich = curvesComparable(pinned, current)
    ? "Beide Kurven liegen im Preisdiagramm übereinander."
    : "Die Zeiträume unterscheiden sich, deshalb steht im Diagramm nur das aktuelle Szenario. " +
      "Die Kennzahlen bleiben vergleichbar.";
  return `<p class="small muted"><strong>gemerkt:</strong> ${escapeHtml(describeScenario(pinned))}<br>` +
         `<strong>jetzt:</strong> ${escapeHtml(describeScenario(current))}<br>${gleich}</p>`;
}
