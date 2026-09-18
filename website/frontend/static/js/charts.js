/* Kleine SVG-Diagrammbibliothek.
 *
 * Bewusst ohne externe Abhängigkeit: die drei Diagrammtypen dieser Seite
 * (Linie, gestapelte Fläche, Blöcke) lassen sich direkt als SVG zeichnen.
 * Farben werden als CSS-Variablen durchgereicht — dadurch folgt jedes
 * Diagramm dem Hell-/Dunkelmodus, ohne neu gezeichnet zu werden.
 */

const SVG_NS = "http://www.w3.org/2000/svg";

const numberFormat = (digits = 0) =>
  new Intl.NumberFormat("de-DE", { minimumFractionDigits: digits, maximumFractionDigits: digits });

export const fmt = {
  gw: (v) => numberFormat(1).format(v) + " GW",
  eur: (v) => numberFormat(0).format(v) + " €/MWh",
  plain: (v, d = 1) => numberFormat(d).format(v),
  percent: (v) => numberFormat(0).format(v * 100) + " %",
};

/* Zeitstempel kommen vom Backend in UTC. Angezeigt werden sie in der Zeitzone
 * der Region, nicht in der des Betrachters: Die Mittagsspitze der Photovoltaik
 * gehört auf 13 Uhr, auch wenn jemand von Kalifornien aus zuschaut. Die Zone
 * liefert das Backend als `display_timezone` mit. */
export const DEFAULT_TIMEZONE = "Europe/Berlin";

function timeParts(iso, timeZone, options) {
  const formatter = new Intl.DateTimeFormat("de-DE", { timeZone, ...options });
  const out = {};
  for (const part of formatter.formatToParts(new Date(iso))) out[part.type] = part.value;
  return out;
}

/** "Mo 14:00" — kompakt genug für eine Achse. */
export function formatHour(iso, timeZone = DEFAULT_TIMEZONE) {
  const p = timeParts(iso, timeZone, {
    weekday: "short", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  });
  return `${p.weekday.replace(".", "")} ${p.hour}:00`;
}

export function formatDay(iso, timeZone = DEFAULT_TIMEZONE) {
  const p = timeParts(iso, timeZone, {
    weekday: "short", day: "numeric", month: "numeric",
  });
  return `${p.weekday.replace(".", "")} ${p.day}.${p.month}.`;
}

function el(name, attrs = {}, parent = null) {
  const node = document.createElementNS(SVG_NS, name);
  for (const [key, value] of Object.entries(attrs)) {
    if (value !== null && value !== undefined) node.setAttribute(key, String(value));
  }
  if (parent) parent.appendChild(node);
  return node;
}

/** Achsenteilung auf runde Schritte (1, 2, 2.5, 5, 10 ...). */
function niceTicks(min, max, target = 5) {
  if (min === max) { min -= 1; max += 1; }
  const rawStep = (max - min) / target;
  const magnitude = Math.pow(10, Math.floor(Math.log10(rawStep)));
  const normalised = rawStep / magnitude;
  const step = (normalised <= 1 ? 1 : normalised <= 2 ? 2 : normalised <= 2.5 ? 2.5 : normalised <= 5 ? 5 : 10) * magnitude;
  // Start und Ende auf Schrittvielfache legen, damit die Achse den Wertebereich
  // immer vollständig einschließt — sonst ragen Marken oben aus dem Diagramm.
  const start = Math.floor(min / step) * step;
  const end = Math.ceil(max / step) * step;
  const ticks = [];
  for (let v = start; v <= end + step * 0.001; v += step) ticks.push(Math.round(v * 1e6) / 1e6);
  return ticks;
}

/* ------------------------------------------------------------------ Gerüst */

function scaffold(container) {
  let plot = container.querySelector(".chart-plot");
  if (!plot) {
    plot = document.createElement("div");
    plot.className = "chart-plot";
    container.appendChild(plot);
  }
  let legend = container.querySelector(".chart-legend");
  if (!legend) {
    legend = document.createElement("div");
    legend.className = "chart-legend";
    container.appendChild(legend);
  }
  let tableWrap = container.querySelector(".chart-table");
  if (!tableWrap) {
    tableWrap = document.createElement("details");
    tableWrap.className = "chart-table";
    tableWrap.innerHTML = "<summary>Werte als Tabelle</summary><div class=\"table-scroll\"></div>";
    container.appendChild(tableWrap);
  }
  let tooltip = plot.querySelector(".chart-tooltip");
  if (!tooltip) {
    tooltip = document.createElement("div");
    tooltip.className = "chart-tooltip";
    tooltip.setAttribute("role", "status");
    plot.appendChild(tooltip);
  }
  return { plot, legend, tableWrap, tooltip };
}

function renderLegend(legend, items) {
  legend.innerHTML = "";
  if (items.length < 2) return;           // eine Serie trägt der Titel
  for (const item of items) {
    const span = document.createElement("span");
    span.className = "legend-item";
    span.innerHTML =
      `<span class="legend-swatch" data-shape="${item.shape || "area"}" style="background:${item.color}"></span>` +
      `<span>${item.label}</span>`;
    legend.appendChild(span);
  }
}

function renderTable(wrap, columns, rows) {
  const head = columns.map((c) => `<th scope="col">${c}</th>`).join("");
  const body = rows
    .map((r) => "<tr>" + r.map((cell, i) => (i === 0 ? `<th scope="row">${cell}</th>` : `<td>${cell}</td>`)).join("") + "</tr>")
    .join("");
  wrap.querySelector(".table-scroll").innerHTML =
    `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

/* --------------------------------------------------------------- Zeichnen */

const MARGIN = { top: 26, right: 18, bottom: 48, left: 58 };

function drawFrame(svg, width, height, xTicks, yTicks, xScale, yScale, yFormat, xLabel, yLabel) {
  const inner = { w: width - MARGIN.left - MARGIN.right, h: height - MARGIN.top - MARGIN.bottom };

  for (const tick of yTicks) {
    const y = yScale(tick);
    el("line", {
      x1: MARGIN.left, x2: MARGIN.left + inner.w, y1: y, y2: y,
      stroke: "var(--grid)", "stroke-width": 1, "shape-rendering": "crispEdges",
    }, svg);
    el("text", {
      x: MARGIN.left - 10, y: y + 4, "text-anchor": "end",
      fill: "var(--ink-muted)", "font-size": 11, "font-variant-numeric": "tabular-nums",
    }, svg).textContent = yFormat(tick);
  }

  // Grundlinie
  el("line", {
    x1: MARGIN.left, x2: MARGIN.left + inner.w,
    y1: MARGIN.top + inner.h, y2: MARGIN.top + inner.h,
    stroke: "var(--axis)", "stroke-width": 1, "shape-rendering": "crispEdges",
  }, svg);

  for (const tick of xTicks) {
    el("text", {
      x: xScale(tick.value), y: height - 26, "text-anchor": tick.anchor || "middle",
      fill: "var(--ink-muted)", "font-size": 11,
    }, svg).textContent = tick.label;
  }

  if (yLabel) {                       // Einheit über der obersten Beschriftung
    el("text", {
      x: MARGIN.left - 10, y: 12, "text-anchor": "end",
      fill: "var(--ink-muted)", "font-size": 11,
    }, svg).textContent = yLabel;
  }
  if (xLabel) {                       // mittig unter den Ticks, kollidiert dort mit nichts
    el("text", {
      x: MARGIN.left + inner.w / 2, y: height - 8, "text-anchor": "middle",
      fill: "var(--ink-muted)", "font-size": 11,
    }, svg).textContent = xLabel;
  }
  return inner;
}

/* ------------------------------------------------------- Linie und Fläche */

function drawTimeChart(spec, plot, tooltip, width) {
  const height = spec.height || 260;
  const n = spec.x.length;
  const svg = el("svg", {
    viewBox: `0 0 ${width} ${height}`, width, height,
    role: "img", tabindex: "0",
    "aria-label": spec.ariaLabel || spec.title,
  });

  const stacked = spec.type === "stack";
  let yMin = 0;
  let yMax = 0;
  if (stacked) {
    for (let i = 0; i < n; i++) {
      let sum = 0;
      for (const s of spec.series) sum += s.values[i];
      yMax = Math.max(yMax, sum);
    }
  } else {
    for (const s of spec.series) {
      for (const v of s.values) { yMax = Math.max(yMax, v); yMin = Math.min(yMin, v); }
    }
  }
  if (spec.yMin !== undefined) yMin = Math.min(yMin, spec.yMin);
  if (yMax === yMin) yMax = yMin + 1;

  const yTicks = niceTicks(yMin, yMax, 5);
  const yLo = Math.min(yTicks[0], yMin);
  const yHi = Math.max(yTicks[yTicks.length - 1], yMax);

  const innerW = width - MARGIN.left - MARGIN.right;
  const innerH = height - MARGIN.top - MARGIN.bottom;
  const xScale = (i) => MARGIN.left + (n <= 1 ? innerW / 2 : (i / (n - 1)) * innerW);
  const yScale = (v) => MARGIN.top + innerH - ((v - yLo) / (yHi - yLo)) * innerH;

  // Höchstens sechs X-Beschriftungen, sonst überlappen sie.
  const stride = Math.max(1, Math.ceil(n / 6));
  const xTicks = [];
  for (let i = 0; i < n; i += stride) {
    xTicks.push({
      value: i,
      label: spec.formatX(spec.x[i], i),
      anchor: i === 0 ? "start" : "middle",
    });
  }

  drawFrame(svg, width, height, xTicks, yTicks, xScale, yScale, spec.formatY || ((v) => fmt.plain(v, 0)),
            spec.xLabel, spec.yLabel);

  if (yLo < 0) {                                  // Nulllinie hervorheben
    el("line", {
      x1: MARGIN.left, x2: MARGIN.left + innerW, y1: yScale(0), y2: yScale(0),
      stroke: "var(--axis)", "stroke-width": 1.5, "shape-rendering": "crispEdges",
    }, svg);
  }

  const clipId = `clip-${Math.random().toString(36).slice(2, 9)}`;
  const clip = el("clipPath", { id: clipId }, svg);
  el("rect", { x: MARGIN.left, y: MARGIN.top - 4, width: innerW, height: innerH + 4 }, clip);
  const body = el("g", { "clip-path": `url(#${clipId})` }, svg);

  if (stacked) {
    const baseline = new Array(n).fill(0);
    const tops = [];
    for (const s of spec.series) {
      const top = s.values.map((v, i) => baseline[i] + v);
      let d = `M ${xScale(0)} ${yScale(top[0])}`;
      for (let i = 1; i < n; i++) d += ` L ${xScale(i)} ${yScale(top[i])}`;
      for (let i = n - 1; i >= 0; i--) d += ` L ${xScale(i)} ${yScale(baseline[i])}`;
      d += " Z";
      el("path", { d, fill: s.color, "fill-opacity": 0.92 }, body);
      tops.push(top);
      for (let i = 0; i < n; i++) baseline[i] = top[i];
    }
    // 2px Lücke in Flächenfarbe zwischen den Bändern statt Konturlinien.
    for (let k = 0; k < tops.length - 1; k++) {
      let d = `M ${xScale(0)} ${yScale(tops[k][0])}`;
      for (let i = 1; i < n; i++) d += ` L ${xScale(i)} ${yScale(tops[k][i])}`;
      el("path", { d, fill: "none", stroke: "var(--surface)", "stroke-width": 2 }, body);
    }
  } else {
    for (const s of spec.series) {
      if (s.fill) {
        let d = `M ${xScale(0)} ${yScale(Math.max(yLo, 0))}`;
        for (let i = 0; i < n; i++) d += ` L ${xScale(i)} ${yScale(s.values[i])}`;
        d += ` L ${xScale(n - 1)} ${yScale(Math.max(yLo, 0))} Z`;
        el("path", { d, fill: s.color, "fill-opacity": 0.12 }, body);
      }
      // Lücken in den Daten unterbrechen die Linie, statt sie durch null zu
      // ziehen: Eine fehlende Messstunde ist keine Messung von null.
      let d = "";
      let pendingMove = true;
      for (let i = 0; i < n; i++) {
        const value = s.values[i];
        if (value === null || value === undefined || Number.isNaN(value)) {
          pendingMove = true;
          continue;
        }
        d += `${pendingMove ? "M" : " L"} ${xScale(i)} ${yScale(value)}`;
        pendingMove = false;
      }
      const attrs = {
        d, fill: "none", stroke: s.color, "stroke-width": 2,
        "stroke-linejoin": "round", "stroke-linecap": "round",
      };
      if (s.dashed) attrs["stroke-dasharray"] = "6 4";
      el("path", attrs, body);
    }
  }

  // Fadenkreuz mit Punkten je Serie
  const crosshair = el("g", { opacity: 0 }, svg);
  const rule = el("line", {
    y1: MARGIN.top, y2: MARGIN.top + innerH,
    stroke: "var(--axis)", "stroke-width": 1, "shape-rendering": "crispEdges",
  }, crosshair);
  const dots = spec.series.map((s) =>
    el("circle", { r: 4.5, fill: s.color, stroke: "var(--surface)", "stroke-width": 2 }, crosshair));

  plot.appendChild(svg);

  const show = (index) => {
    const i = Math.max(0, Math.min(n - 1, index));
    const x = xScale(i);
    rule.setAttribute("x1", x);
    rule.setAttribute("x2", x);
    const stackSum = [];
    let running = 0;
    for (const s of spec.series) { running += s.values[i]; stackSum.push(running); }
    spec.series.forEach((s, k) => {
      dots[k].setAttribute("cx", x);
      dots[k].setAttribute("cy", yScale(stacked ? stackSum[k] : s.values[i]));
    });
    crosshair.setAttribute("opacity", 1);

    const rows = spec.series
      .map((s) => `<div class="tooltip-row"><span class="tooltip-key">` +
        `<span class="legend-swatch" style="background:${s.color}"></span>${s.label}</span>` +
        `<span>${(spec.formatValue || spec.formatY || fmt.plain)(s.values[i])}</span></div>`)
      .reverse()
      .join("");
    tooltip.innerHTML = `<div class="tooltip-title">${spec.formatX(spec.x[i], i)}</div>${rows}` +
      (spec.tooltipExtra ? spec.tooltipExtra(i) : "");
    tooltip.dataset.visible = "true";
    const box = tooltip.getBoundingClientRect();
    const ratio = plot.clientWidth / width;
    const left = Math.min(Math.max(x * ratio + 14, 4), plot.clientWidth - box.width - 4);
    tooltip.style.left = `${left}px`;
    tooltip.style.top = `${MARGIN.top * ratio + 4}px`;
    svg.setAttribute("aria-label",
      `${spec.title}: ${spec.formatX(spec.x[i], i)}, ` +
      spec.series.map((s) => `${s.label} ${(spec.formatY || fmt.plain)(s.values[i])}`).join(", "));
  };

  const hide = () => {
    crosshair.setAttribute("opacity", 0);
    tooltip.dataset.visible = "false";
  };

  const indexFromEvent = (event) => {
    const rect = svg.getBoundingClientRect();
    const x = ((event.clientX - rect.left) / rect.width) * width;
    return Math.round(((x - MARGIN.left) / innerW) * (n - 1));
  };

  svg.addEventListener("pointermove", (e) => show(indexFromEvent(e)));
  svg.addEventListener("pointerleave", hide);
  svg.addEventListener("blur", hide);

  let cursor = 0;
  svg.addEventListener("keydown", (e) => {
    if (e.key === "ArrowRight") cursor = Math.min(n - 1, cursor + 1);
    else if (e.key === "ArrowLeft") cursor = Math.max(0, cursor - 1);
    else if (e.key === "Home") cursor = 0;
    else if (e.key === "End") cursor = n - 1;
    else if (e.key === "Escape") { hide(); return; }
    else return;
    e.preventDefault();
    show(cursor);
  });
}

/* ---------------------------------------------------------------- Blöcke */

function drawBlockChart(spec, plot, tooltip, width) {
  const height = spec.height || 300;
  const svg = el("svg", {
    viewBox: `0 0 ${width} ${height}`, width, height,
    role: "img", "aria-label": spec.ariaLabel || spec.title,
  });

  const xMax = Math.max(...spec.blocks.map((b) => b.to)) * 1.02;
  const yMax = Math.max(...spec.blocks.map((b) => b.value)) * 1.12;
  const yTicks = niceTicks(0, yMax, 5);
  const yHi = yTicks[yTicks.length - 1];

  const innerW = width - MARGIN.left - MARGIN.right;
  const innerH = height - MARGIN.top - MARGIN.bottom;
  const xScale = (v) => MARGIN.left + (v / xMax) * innerW;
  const yScale = (v) => MARGIN.top + innerH - (v / yHi) * innerH;

  const xTickValues = niceTicks(0, xMax, 5).filter((v) => v <= xMax);
  drawFrame(svg, width, height,
    xTickValues.map((v, i) => ({ value: v, label: fmt.plain(v, 0), anchor: i === 0 ? "start" : "middle" })),
    yTicks, xScale, yScale, spec.formatY || ((v) => fmt.plain(v, 0)), spec.xLabel, spec.yLabel);

  spec.blocks.forEach((block) => {
    const x = xScale(block.from);
    const w = Math.max(xScale(block.to) - x - 2, 1);   // 2px Lücke statt Kontur
    // Wind und PV haben Grenzkosten von null — ohne Mindesthöhe wären sie
    // unsichtbar, obwohl sie den größten Teil der Leistung stellen.
    const MIN_HEIGHT = 4;
    const top = Math.min(yScale(block.value), MARGIN.top + innerH - MIN_HEIGHT);
    el("rect", {
      x, y: top, width: w, height: MARGIN.top + innerH - top,
      fill: block.color, "fill-opacity": 0.92, rx: 3,
      "data-block": block.id,
    }, svg);
    // Direktbeschriftung nur, wenn sie wirklich in den Block passt.
    if (w > block.label.length * 6.6 + 12) {
      el("text", {
        x: x + w / 2, y: top - 7, "text-anchor": "middle",
        fill: "var(--ink-2)", "font-size": 11,
      }, svg).textContent = block.label;
    }
  });

  if (spec.marker && spec.marker.value > 0 && spec.marker.value < xMax) {
    const x = xScale(spec.marker.value);
    el("line", {
      x1: x, x2: x, y1: MARGIN.top, y2: MARGIN.top + innerH,
      stroke: "var(--ink)", "stroke-width": 2, "stroke-dasharray": "5 4",
    }, svg);
    el("text", {
      x: x - 7, y: MARGIN.top + 11, "text-anchor": "end",
      fill: "var(--ink)", "font-size": 11, "font-weight": 600,
    }, svg).textContent = spec.marker.label;
  }

  plot.appendChild(svg);

  const showBlock = (block, event) => {
    tooltip.innerHTML =
      `<div class="tooltip-title"><span class="legend-swatch" style="background:${block.color}"></span> ${block.label}</div>` +
      `<div class="tooltip-row"><span>Grenzkosten</span><span>${fmt.eur(block.value)}</span></div>` +
      `<div class="tooltip-row"><span>Leistung</span><span>${fmt.gw(block.to - block.from)}</span></div>` +
      (block.note ? `<div class="small muted" style="margin-top:6px;max-width:26ch">${block.note}</div>` : "");
    tooltip.dataset.visible = "true";
    const rect = svg.getBoundingClientRect();
    const box = tooltip.getBoundingClientRect();
    const left = Math.min(Math.max(event.clientX - rect.left + 12, 4), plot.clientWidth - box.width - 4);
    tooltip.style.left = `${left}px`;
    tooltip.style.top = "8px";
  };

  svg.addEventListener("pointermove", (event) => {
    const rect = svg.getBoundingClientRect();
    const valueX = (((event.clientX - rect.left) / rect.width) * width - MARGIN.left) / innerW * xMax;
    const block = spec.blocks.find((b) => valueX >= b.from && valueX <= b.to);
    if (block) showBlock(block, event);
    else tooltip.dataset.visible = "false";
  });
  svg.addEventListener("pointerleave", () => { tooltip.dataset.visible = "false"; });
}

/* ------------------------------------------------------------ Öffentliche API */

/** Zeichnet `spec` in das `.chart`-Element und hält es bei Größenänderung aktuell. */
export function renderChart(container, spec) {
  const { plot, legend, tableWrap, tooltip } = scaffold(container);

  const paint = () => {
    const width = Math.max(plot.clientWidth || container.clientWidth || 640, 320);
    plot.querySelectorAll("svg").forEach((node) => node.remove());
    tooltip.dataset.visible = "false";
    if (spec.type === "blocks") drawBlockChart(spec, plot, tooltip, width);
    else drawTimeChart(spec, plot, tooltip, width);
  };

  paint();

  if (spec.type === "blocks") {
    renderLegend(legend, spec.legend || []);
    renderTable(tableWrap, ["Technologie", "von (GW)", "bis (GW)", "Grenzkosten (€/MWh)"],
      spec.blocks.map((b) => [b.label, fmt.plain(b.from, 1), fmt.plain(b.to, 1), fmt.plain(b.value, 2)]));
  } else {
    renderLegend(legend, spec.series.map((s) => ({
      label: s.label, color: s.color, shape: spec.type === "stack" ? "area" : "line",
    })));
    const stride = Math.max(1, Math.ceil(spec.x.length / 200));   // Tabelle bleibt lesbar
    const rows = [];
    for (let i = 0; i < spec.x.length; i += stride) {
      rows.push([spec.formatX(spec.x[i], i), ...spec.series.map((s) => fmt.plain(s.values[i], 1))]);
    }
    renderTable(tableWrap, [spec.xLabel || "Zeit", ...spec.series.map((s) => s.label)], rows);
  }

  if (container._vizObserver) container._vizObserver.disconnect();
  let lastWidth = plot.clientWidth;
  const observer = new ResizeObserver(() => {
    if (Math.abs(plot.clientWidth - lastWidth) < 8) return;   // Rundungsrauschen ignorieren
    lastWidth = plot.clientWidth;
    paint();
  });
  observer.observe(plot);
  container._vizObserver = observer;
}
