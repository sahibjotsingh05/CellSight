"use strict";
(() => {
  const root = document.documentElement;
  const read = key => { try { return localStorage.getItem(key); } catch { return null; } };
  const save = (key, value) => { try { localStorage.setItem(key, value); } catch {} };
  function setTheme(value) {
    const theme = value === "dark" ? "dark" : "light";
    root.classList.add("theme-changing");
    root.dataset.theme = theme;
    document.body.dataset.theme = theme;
    const button = document.getElementById("theme-toggle");
    if (button) {
      // Two-position sun/moon pill (Lucide icons); the active theme gets the raised circle.
      button.innerHTML = `<span class="theme-option ${theme === "light" ? "active" : ""}"><svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"/></svg></span><span class="theme-option ${theme === "dark" ? "active" : ""}"><svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/></svg></span>`;
      button.title = `Switch to ${theme === "dark" ? "light" : "dark"} mode`;
      button.setAttribute("aria-label", `Switch to ${theme === "dark" ? "light" : "dark"} mode`);
      button.setAttribute("aria-pressed", String(theme === "dark"));
    }
    save("voltwatch-theme", theme);
    document.dispatchEvent(new CustomEvent("voltwatch:theme", { detail: { theme } }));
    void document.body.offsetHeight;
    requestAnimationFrame(() => requestAnimationFrame(() => root.classList.remove("theme-changing")));
  }
  function setTransparency(value) {
    const material = value === "opaque" ? "opaque" : "glass";
    root.dataset.transparency = material;
    const button = document.getElementById("effects-toggle");
    if (button) {
      button.textContent = material === "glass" ? "Reduce transparency" : "Use glass surfaces";
      button.setAttribute("aria-pressed", String(material === "opaque"));
    }
    save("voltwatch-transparency", material);
    document.dispatchEvent(new CustomEvent("voltwatch:transparency", { detail: { material } }));
  }
  window.VoltTheme = { set: setTheme, setTransparency };
  document.getElementById("theme-toggle")?.addEventListener("click", () => setTheme(root.dataset.theme === "dark" ? "light" : "dark"));
  document.getElementById("effects-toggle")?.addEventListener("click", () => setTransparency(root.dataset.transparency === "opaque" ? "glass" : "opaque"));
  setTheme(read("voltwatch-theme") || root.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"));
  // Glass surfaces are the default while the transparency toggle is commented out;
  // an older saved "opaque" choice would otherwise be impossible to undo.
  // setTransparency(read("voltwatch-transparency") || (matchMedia("(prefers-reduced-transparency: reduce)").matches ? "opaque" : "glass"));
  setTransparency("glass");
})();
