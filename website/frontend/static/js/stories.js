/* Geführte Geschichten: Fragen mit fertigen Parametersätzen.
 *
 * Der Simulator beantwortet jede Frage, die man ihm stellt — aber er stellt
 * keine. Wer zum ersten Mal auf sieben Regler schaut, weiß nicht, an welchem er
 * drehen soll. Eine Geschichte liefert die Frage mit und führt in wenigen
 * Schritten durch die Antwort.
 *
 * Hier steht nur reine Logik: aus einer Geschichte wird Auszeichnung, aus einem
 * Schritt werden Formularwerte. Verdrahtet wird in analysis.js.
 */

/** Auszeichnung entschärfen. Die Texte sind eigene, aber das bleibt es nicht ewig. */
export function escapeHtml(text) {
  return String(text ?? "")
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

/**
 * Einen Schritt ins Formular übertragen.
 *
 * Wichtig ist, was *nicht* im Schritt steht: Jeder Schritt setzt nur die
 * Parameter, um die es ihm geht. Alles andere wird auf den Vorgabewert
 * zurückgesetzt, damit ein Schritt nicht von dem abhängt, was der Benutzer
 * vorher eingestellt hatte — sonst zeigte dieselbe Geschichte bei zwei Leuten
 * zwei verschiedene Bilder.
 */
export function applyStoryStep(form, step) {
  form.reset();
  const params = (step && step.params) || {};

  for (const [name, value] of Object.entries(params)) {
    const field = form.elements[name];
    if (!field) continue;
    if (field.type === "checkbox") field.checked = Boolean(value);
    else field.value = String(value);
  }

  // Ein Schritt, der einen Regler vorgibt, will diesen Wert — nicht den echten.
  const realwerte = form.elements.real_values;
  if (realwerte) {
    const eigene = ["co2_price", "gas_price", "wind_gw", "solar_gw"];
    realwerte.checked = !eigene.some((key) => key in params);
  }
  // Ebenso bei der Höchstlast: Nur strecken, wenn der Schritt es ausdrücklich will.
  const strecken = form.elements.scale_load;
  if (strecken && !("scale_load" in params)) {
    strecken.checked = "peak_load_gw" in params && params.source === "historical";
  }
  return form;
}

/** "Schritt 2 von 3" — Orientierung, ohne dass man zählen muss. */
export function storyProgress(story, index) {
  const gesamt = (story && story.steps || []).length;
  return gesamt ? `Schritt ${Math.min(index + 1, gesamt)} von ${gesamt}` : "";
}

/** Auswahlkarten für alle Geschichten. */
export function buildStoryList(stories) {
  if (!stories || !stories.length) return "";
  const karten = stories.map((story) => `
      <button type="button" class="story-card" data-story="${escapeHtml(story.id)}">
        <span class="story-card-title">${escapeHtml(story.title)}</span>
        <span class="story-card-question">${escapeHtml(story.question)}</span>
      </button>`).join("");
  return `<div class="story-list">${karten}</div>`;
}

/**
 * Die geöffnete Geschichte mit dem aktuellen Schritt.
 *
 * `watch` steht bewusst getrennt vom Fließtext: Der Text erklärt, der Hinweis
 * sagt, wohin zu schauen ist. Ohne diesen Zeiger liest man die Erklärung und
 * sieht das Diagramm trotzdem nicht an.
 */
export function buildStoryPanel(story, index) {
  if (!story) return "";
  const steps = story.steps || [];
  const schritt = steps[Math.max(0, Math.min(index, steps.length - 1))];
  if (!schritt) return "";
  const aktuell = Math.max(0, Math.min(index, steps.length - 1));

  const reiter = steps.map((step, i) => `
      <button type="button" class="story-step${i === aktuell ? " is-current" : ""}"
              data-step="${i}"${i === aktuell ? ' aria-current="step"' : ""}>
        <span class="story-step-number">${i + 1}</span>${escapeHtml(step.label)}
      </button>`).join("");

  return `
    <div class="story-head">
      <div>
        <span class="eyebrow">${escapeHtml(storyProgress(story, aktuell))}</span>
        <h2 class="story-title">${escapeHtml(story.title)}</h2>
        <p class="story-lead">${escapeHtml(story.lead)}</p>
      </div>
      <button type="button" class="btn btn-secondary story-close">Geschichte schließen</button>
    </div>
    <nav class="story-steps" aria-label="Schritte der Geschichte">${reiter}</nav>
    <div class="story-body">
      <p class="story-text">${escapeHtml(schritt.text)}</p>
      <p class="story-watch"><strong>Worauf achten:</strong> ${escapeHtml(schritt.watch)}</p>
    </div>
    <div class="story-nav">
      <button type="button" class="btn btn-secondary story-prev"${aktuell === 0 ? " disabled" : ""}>Zurück</button>
      <button type="button" class="btn btn-primary story-next"${aktuell >= steps.length - 1 ? " disabled" : ""}>Weiter</button>
    </div>`;
}

/**
 * Geschichte und Schritt aus der Adresszeile lesen.
 *
 * Damit ist eine Geschichte an genau der Stelle teilbar, an der sie
 * interessant wird — nicht nur von vorn.
 */
export function readStoryFromUrl(search) {
  const query = new URLSearchParams(search);
  const id = query.get("story");
  if (!id) return null;
  const roh = Number.parseInt(query.get("schritt") || "1", 10);
  const schritt = Number.isFinite(roh) && roh > 0 ? roh - 1 : 0;
  return { id, step: schritt };
}

/** Eine Geschichte anhand ihrer Kennung finden. */
export function findStory(stories, id) {
  return (stories || []).find((story) => story.id === id) || null;
}
