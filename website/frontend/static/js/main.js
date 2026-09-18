/* Seitenweite Kleinigkeiten: Farbschema-Umschalter und ein Hinweis, falls das
 * Backend nicht erreichbar ist. */

const STORAGE_KEY = "energiewende-theme";

function systemTheme() {
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  const button = document.querySelector(".theme-toggle");
  if (!button) return;
  const dark = theme === "dark";
  button.textContent = dark ? "☀" : "☾";
  button.setAttribute("aria-label", dark ? "Zu hellem Farbschema wechseln" : "Zu dunklem Farbschema wechseln");
  button.setAttribute("aria-pressed", String(dark));
}

function initTheme() {
  let stored = null;
  try { stored = localStorage.getItem(STORAGE_KEY); } catch { /* privater Modus */ }
  applyTheme(stored || systemTheme());

  const button = document.querySelector(".theme-toggle");
  if (!button) return;
  button.addEventListener("click", () => {
    const next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
    applyTheme(next);
    try { localStorage.setItem(STORAGE_KEY, next); } catch { /* egal */ }
  });
}

async function checkBackend() {
  try {
    const response = await fetch("/health");
    if (!response.ok) throw new Error(String(response.status));
  } catch (error) {
    console.warn("Backend nicht erreichbar:", error);
  }
}

initTheme();
checkBackend();
