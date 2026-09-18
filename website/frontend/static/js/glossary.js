/* Glossar: Sofortfilter über Begriff, Definition und Gruppe. */

const search = document.getElementById("glossary-search");
const groupSelect = document.getElementById("glossary-group");
const list = document.getElementById("glossary-list");
const counter = document.getElementById("glossary-count");
const entries = Array.from(list.querySelectorAll(".glossary-entry"));

const normalise = (value) =>
  value.toLowerCase().replace(/ä/g, "ae").replace(/ö/g, "oe").replace(/ü/g, "ue").replace(/ß/g, "ss");

function apply() {
  const needle = normalise(search.value.trim());
  const group = groupSelect.value;
  let visible = 0;

  for (const entry of entries) {
    const matchesGroup = group === "alle" || entry.dataset.group === group;
    const matchesText = !needle || normalise(entry.dataset.haystack).includes(needle);
    const show = matchesGroup && matchesText;
    entry.hidden = !show;
    if (show) visible += 1;
  }

  counter.textContent = visible === entries.length
    ? `${entries.length} Begriffe`
    : `${visible} von ${entries.length} Begriffen`;
  list.nextElementSibling.hidden = visible > 0;
}

search.addEventListener("input", apply);
groupSelect.addEventListener("change", apply);

// Querverweise filtern statt zu springen — der Begriff kann ausgeblendet sein.
list.addEventListener("click", (event) => {
  const link = event.target.closest("[data-term]");
  if (!link) return;
  event.preventDefault();
  search.value = link.dataset.term;
  groupSelect.value = "alle";
  apply();
  search.focus();
});

apply();
