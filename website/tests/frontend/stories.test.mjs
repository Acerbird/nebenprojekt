/* Tests der geführten Geschichten.
 *
 * Geprüft wird gegen die echten Geschichten aus static_data/stories.json und
 * gegen das echte Formular der gerenderten Seite — nicht gegen Nachbildungen.
 */

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { beforeEach, describe, it } from "node:test";

import { loadPage } from "./helpers.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const stories = JSON.parse(readFileSync(
  join(here, "../../backend/static_data/stories.json"), "utf-8"));

const {
  applyStoryStep, buildStoryList, buildStoryPanel, escapeHtml, findStory,
  readStoryFromUrl, storyProgress,
} = await import("../../frontend/static/js/stories.js");

let form;
beforeEach(() => { ({ form } = loadPage()); });

describe("Auswahl der Geschichten", () => {
  it("baut für jede Geschichte eine Karte", () => {
    const markup = buildStoryList(stories);
    for (const story of stories) {
      assert.ok(markup.includes(`data-story="${story.id}"`), story.id);
      assert.ok(markup.includes(story.title), story.title);
    }
  });

  it("bleibt still, wenn keine Geschichten vorliegen", () => {
    assert.equal(buildStoryList([]), "");
    assert.equal(buildStoryList(null), "");
  });

  it("findet eine Geschichte an ihrer Kennung", () => {
    assert.equal(findStory(stories, stories[0].id).title, stories[0].title);
    assert.equal(findStory(stories, "gibtsnicht"), null);
  });
});

describe("Anzeige eines Schritts", () => {
  const story = stories[0];

  it("zeigt Text und Hinweis des richtigen Schritts", () => {
    const markup = buildStoryPanel(story, 1);
    assert.ok(markup.includes(escapeHtml(story.steps[1].text)));
    assert.ok(markup.includes(escapeHtml(story.steps[1].watch)));
    assert.ok(!markup.includes(escapeHtml(story.steps[0].text)));
  });

  it("zählt die Schritte für die Orientierung", () => {
    assert.equal(storyProgress(story, 0), `Schritt 1 von ${story.steps.length}`);
    assert.equal(storyProgress(story, story.steps.length - 1),
                 `Schritt ${story.steps.length} von ${story.steps.length}`);
  });

  it("markiert den aktuellen Schritt auch für Vorlesesoftware", () => {
    const markup = buildStoryPanel(story, 1);
    assert.match(markup, /data-step="1"[^>]*aria-current="step"/);
    assert.equal((markup.match(/aria-current/g) || []).length, 1);
  });

  it("sperrt Zurück im ersten und Weiter im letzten Schritt", () => {
    const erster = buildStoryPanel(story, 0);
    assert.match(erster, /story-prev" disabled/);
    assert.doesNotMatch(erster, /story-next" disabled/);
    const letzter = buildStoryPanel(story, story.steps.length - 1);
    assert.match(letzter, /story-next" disabled/);
    assert.doesNotMatch(letzter, /story-prev" disabled/);
  });

  it("fängt einen Schrittindex außerhalb der Geschichte ab", () => {
    assert.ok(buildStoryPanel(story, 99).includes(escapeHtml(story.steps.at(-1).text)));
    assert.ok(buildStoryPanel(story, -5).includes(escapeHtml(story.steps[0].text)));
  });

  it("bleibt still ohne Geschichte", () => {
    assert.equal(buildStoryPanel(null, 0), "");
  });

  it("entschärft Auszeichnung im Text", () => {
    const boshaft = { id: "x", title: "<script>", lead: "a & b", steps: [
      { label: "l", text: "<img onerror=x>", watch: "w" },
      { label: "l2", text: "t2", watch: "w2" }] };
    const markup = buildStoryPanel(boshaft, 0);
    assert.ok(!markup.includes("<script>"));
    assert.ok(!markup.includes("<img"));
    assert.ok(markup.includes("&amp;"));
  });
});

describe("Ein Schritt stellt das Formular ein", () => {
  it("überträgt die Parameter des Schritts", () => {
    applyStoryStep(form, { params: { source: "synthetic", season: "sommer",
                                     wind_gw: 70, solar_gw: 200, hours: 72 } });
    assert.equal(form.elements.source.value, "synthetic");
    assert.equal(form.elements.season.value, "sommer");
    assert.equal(form.elements.solar_gw.value, "200");
    assert.equal(form.elements.hours.value, "72");
  });

  it("setzt zurück, was der Schritt nicht nennt", () => {
    form.elements.co2_price.value = "240";
    applyStoryStep(form, { params: { source: "synthetic", wind_gw: 70 } });
    assert.equal(form.elements.co2_price.value, String(form.elements.co2_price.defaultValue),
      "Sonst hinge das Bild davon ab, was vorher eingestellt war");
  });

  it("nimmt einen Reglerwert im Schritt als Vorrang vor den Werten des Zeitraums", () => {
    applyStoryStep(form, { params: { source: "historical", start: "2023-04-06", gas_price: 29 } });
    assert.equal(form.elements.gas_price.value, "29");
    assert.equal(form.elements.real_values.checked, false);
  });

  it("gilt auch für den Ausbau, nicht nur für Preise", () => {
    applyStoryStep(form, { params: { source: "historical", start: "2025-01-06", wind_gw: 140 } });
    assert.equal(form.elements.real_values.checked, false,
      "Sonst würde das Backend 140 GW durch den tatsächlichen Stand ersetzen");
  });

  it("lässt die Werte des Zeitraums stehen, wenn der Schritt keinen nennt", () => {
    applyStoryStep(form, { params: { source: "historical", start: "2023-04-06" } });
    assert.equal(form.elements.real_values.checked, true);
  });

  it("schaltet Kästchen um, nicht deren Wert", () => {
    applyStoryStep(form, { params: { source: "historical", start: "2025-10-06", min_load: true } });
    assert.equal(form.elements.min_load.checked, true);
    applyStoryStep(form, { params: { source: "historical", start: "2025-10-06" } });
    assert.equal(form.elements.min_load.checked, false);
  });

  it("streckt die Last nur, wenn der Schritt eine Höchstlast nennt", () => {
    applyStoryStep(form, { params: { source: "historical", start: "2025-01-06",
                                     peak_load_gw: 95, scale_load: true } });
    assert.equal(form.elements.scale_load.checked, true);
    applyStoryStep(form, { params: { source: "historical", start: "2025-01-06" } });
    assert.equal(form.elements.scale_load.checked, false);
  });

  it("jeder echte Schritt lässt sich anwenden", () => {
    for (const story of stories) {
      for (const [i, step] of story.steps.entries()) {
        applyStoryStep(form, step);
        for (const [name, value] of Object.entries(step.params)) {
          const field = form.elements[name];
          assert.ok(field, `${story.id}/${i}: Feld ${name} fehlt im Formular`);
          if (field.type === "checkbox") {
            assert.equal(field.checked, Boolean(value), `${story.id}/${i}/${name}`);
          } else {
            assert.equal(field.value, String(value), `${story.id}/${i}/${name}`);
          }
        }
      }
    }
  });
});

describe("Geschichten in der Adresszeile", () => {
  it("liest Kennung und Schritt", () => {
    assert.deepEqual(readStoryFromUrl("?story=co2-preis&schritt=3"),
                     { id: "co2-preis", step: 2 });
  });

  it("beginnt ohne Schrittangabe vorn", () => {
    assert.deepEqual(readStoryFromUrl("?story=co2-preis"), { id: "co2-preis", step: 0 });
  });

  it("verträgt Unsinn im Schritt", () => {
    assert.equal(readStoryFromUrl("?story=x&schritt=abc").step, 0);
    assert.equal(readStoryFromUrl("?story=x&schritt=0").step, 0);
    assert.equal(readStoryFromUrl("?story=x&schritt=-4").step, 0);
  });

  it("gibt null zurück, wenn keine Geschichte gemeint ist", () => {
    assert.equal(readStoryFromUrl("?source=historical"), null);
    assert.equal(readStoryFromUrl(""), null);
  });
});
