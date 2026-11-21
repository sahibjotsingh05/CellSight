"use strict";
const $ = (id) => document.getElementById(id);
const esc = (value) =>
  String(value ?? "—").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const number = (value, digits = 1, suffix = "") =>
  value == null || !Number.isFinite(Number(value))
    ? "—"
    : `${Number(value).toFixed(digits)}${suffix}`;
const coverage = (value) =>
  value == null || !Number.isFinite(Number(value))
    ? "—"
    : Math.round(Number(value) * 12);
const severity = (value) =>
  ["NORMAL", "WARNING", "CRITICAL"].includes(value)
    ? value.toLowerCase()
    : "unknown";
const badge = (value) =>
  `<span class="pill ${severity(value)}">${esc(value || "UNKNOWN")}</span>`;
const time = (value) =>
  value
    ? esc(String(value).replace("T", " ").replace("Z", " UTC"))
    : "Unavailable";
async function api(path, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 5000);
  try {
    const response = await fetch(path, {
      ...options,
      signal: controller.signal,
    });
    let data;
    try {
      data = await response.json();
    } catch {
      throw new Error(`Request failed (${response.status})`);
    }
    if (!response.ok)
      throw new Error(data.detail || `Request failed (${response.status})`);
    return data;
  } finally {
    clearTimeout(timeout);
  }
}
const post = (path, data) =>
  api(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
// Buses are known by their ETS route number everywhere in the interface (internal IDs stay in the API).
const busLabel = (b) => (b?.route?.number ? `Bus ${b.route.number}` : `Bus ${b?.bus_id ?? ""}`);
let selectedModule = null;
// true while the zoomed-in single-module view (with graphs) is shown.
let moduleOpen = false;
let latestHistory = null;
let latestSnapshot = null;

function moduleGrid(modules = []) {
  return `<div class="modules">${
    modules
      .map(
        (m) => `
    <button class="module ${severity(m.severity)}" data-module-id="${esc(m.module_id)}"
      aria-pressed="${m.module_id === selectedModule}">
      <b>${esc(m.module_id)}</b><span>${number(m.temp_c, 1, " °C")}</span>
      <span>${number(m.voltage_v, 3, " V")}</span><span>${esc(m.severity || "UNKNOWN")}</span>
    </button>`,
      )
      .join("") || '<p class="muted">No module readings available.</p>'
  }</div>`;
}
function selectedModuleMarkup(snapshot) {
  const m = (snapshot.modules || []).find(
    (item) => item.module_id === selectedModule,
  );
  if (!m)
    return '<p class="muted">Select a module to inspect its readings.</p>';
  const flags = Object.entries(m.quality || {}).filter(
    ([, value]) => value !== "ok",
  );
  const quality = flags.length
    ? flags
        .map(([key, value]) => `${key.replaceAll("_", " ")}: ${value}`)
        .join(" · ")
    : "No quality flags";
  return `<strong>${esc(m.module_id)} selected</strong> · Current ${number(m.current_a, 1, " A")} · SoC ${number(m.soc_pct, 0, "%")} · SoH ${number(m.soh_pct, 0, "%")}<br>Sensor: ${esc(m.sensor_status)} · ${esc(quality)}`;
}
function alertsMarkup(alerts = [], prefix = "") {
  return alerts.length
    ? alerts
        .map(
          (a) =>
            `<div class="alert ${severity(a.severity)}"><strong>${esc(a.severity)} · ${esc(a.module_id || "Battery")}</strong><div>${esc(a.message || a.type)}</div><small>Value: ${esc(a.value)} · Threshold: ${esc(a.threshold)} · First seen: ${time(a.first_seen)}${a.injected ? " · Injected" : ""}${a.acknowledged ? " · Acknowledged" : ""}</small>${a.active && !a.acknowledged && prefix ? `<div><button data-ack="${esc(a.id)}">Acknowledge</button></div>` : ""}</div>`,
        )
        .join("")
    : '<p class="muted">No active alerts.</p>';
}
function detailMarkup(s, prefix) {
  // Active alerts and recent alert evidence were removed from this panel on request.
  return `<div data-overview><div data-summary></div><div class="section-title"><h3>Module readings</h3><span class="muted">Select a module for its graphs</span></div>
    <div data-modules>${moduleGrid(s.modules)}</div><p class="module-detail" data-module-detail></p></div>
    <div class="module-view" data-module-view hidden>
    <button class="back-button" data-back>‹ All modules</button>
    <div class="section-title"><h3 data-module-title tabindex="-1"></h3></div>
    <p class="module-detail" data-module-detail-zoom></p>
    <div data-module-alerts></div>
    <div class="section-title"><h3>Temperature history</h3><span class="muted" data-chart-label></span></div>
    <div class="chart-box" data-expand="temp" role="button" tabindex="0" title="Open full temperature history" aria-label="Open full temperature history, range and alerts"><canvas id="${prefix}-temp" aria-label="Module temperature history" role="img"></canvas></div>
    <div class="section-title"><h3>Voltage history</h3><span class="muted" data-chart-label></span></div>
    <div class="chart-box" data-expand="voltage" role="button" tabindex="0" title="Open full voltage history" aria-label="Open full voltage history, range and alerts"><canvas id="${prefix}-voltage" aria-label="Module voltage history" role="img"></canvas></div>
    <p class="muted" data-history-sync hidden>History is synchronizing with these readings.</p>
    <p class="notice" data-continuity role="status" hidden></p>
    </div>`;
}
let charts = [];
const pendingAcks = new Set();
const ackErrors = new Map();
const CHART_WINDOW_S = 120;
function drawCharts(history, prefix) {
  if (window.VoltCharts) return window.VoltCharts.draw(history, prefix, selectedModule);
  if (!window.Chart) return;
  history = history || { timestamps: [], modules: {} };
  // x is seconds since the bus started its current file; the view shows the latest two minutes.
  const elapsed = history.elapsed || (history.timestamps || []).map((_, i) => i);
  const latest = elapsed.length ? elapsed[elapsed.length - 1] : 0;
  const xMin = Math.max(0, latest - CHART_WINDOW_S);
  const xMax = Math.max(CHART_WINDOW_S, latest);
  const points = (values = []) => values.map((y, i) => ({ x: elapsed[i], y }));
  const clock = (seconds) =>
    `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;
  ["temp", "voltage"].forEach((kind, index) => {
    const canvas = $(`${prefix}-${kind}`);
    if (!canvas) return;
    const selectedColor =
      document.body.dataset.theme === "dark" ? "#77d3bf" : "#147d70";
    const dimColor =
      document.body.dataset.theme === "dark" ? "#72888a55" : "#879b9b55";
    const datasets = Object.entries(history.modules || {}).map(
      ([id, values]) => ({
        label: id,
        data: points(values[kind === "temp" ? "temp_c" : "voltage_v"]),
        borderColor: id === selectedModule ? selectedColor : dimColor,
        borderWidth: id === selectedModule ? 3 : 1,
        pointRadius: 0,
        spanGaps: false,
        order: id === selectedModule ? 0 : 1,
      }),
    );
    const limits =
      kind === "temp"
        ? [
            ["Warning ≥40 °C", 40],
            ["Critical ≥55 °C", 55],
          ]
        : [
            ["Low warning ≤3.55 V", 3.55],
            ["Low critical ≤3.35 V", 3.35],
            ["High warning ≥4.15 V", 4.15],
            ["High critical >4.20 V", 4.2],
          ];
    limits.forEach(([label, value]) =>
      datasets.push({
        label,
        data: [
          { x: xMin, y: value },
          { x: xMax, y: value },
        ],
        borderColor: label.toLowerCase().includes("critical")
          ? "#d15b50"
          : "#bd8b2e",
        borderWidth: 1,
        borderDash: label.toLowerCase().includes("critical") ? [2, 3] : [8, 4],
        pointRadius: 0,
        order: 2,
      }),
    );
    const x = {
      type: "linear",
      min: xMin,
      max: xMax,
      ticks: {
        color: document.body.dataset.theme === "dark" ? "#98aaa9" : "#647778",
        maxTicksLimit: 5,
        callback: (value) => clock(value),
      },
      grid: { display: false },
    };
    if (charts[index] && charts[index].canvas === canvas) {
      charts[index].data = { datasets };
      charts[index].options.scales.x = x;
      charts[index].options.scales.y.ticks.color = document.body.dataset.theme === "dark" ? "#98aaa9" : "#647778";
      charts[index].options.scales.y.grid.color = document.body.dataset.theme === "dark" ? "#35474b" : "#d9e1e1";
      charts[index].update();
    } else {
      charts[index]?.destroy();
      charts[index] = new Chart(canvas, {
        type: "line",
        data: { datasets },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          animation: false,
          plugins: { legend: { display: false } },
          scales: {
            x,
            y: {
              ticks: {
                color:
                  document.body.dataset.theme === "dark"
                    ? "#98aaa9"
                    : "#647778",
                maxTicksLimit: 4,
              },
              grid: {
                color:
                  document.body.dataset.theme === "dark"
                    ? "#35474b"
                    : "#d9e1e1",
              },
            },
          },
        },
      });
    }
  });
}
function renderDetails(container, snapshot, history, prefix, ackPath) {
  // At one row per second the history request often lands a reading or two after the status
  // request. Accept any history from the same run, and keep the last good one if a fetch fails,
  // so the charts never fall back to "Waiting for samples" between polls.
  const sameRun = (h) => h && ["session_id", "run_id"].every((key) => h[key] === snapshot[key]);
  const matching = sameRun(history);
  history = matching ? history : sameRun(latestHistory) ? latestHistory : null;
  latestSnapshot = snapshot;
  latestHistory = history;
  if (
    !selectedModule ||
    !(snapshot.modules || []).some((m) => m.module_id === selectedModule)
  ) {
    selectedModule = (snapshot.alerts || []).find(a => a.severity === snapshot.severity && snapshot.modules?.some(m => m.module_id === a.module_id))?.module_id || snapshot.modules?.[0]?.module_id || null;
  }
  if (!container.querySelector("[data-modules]")) {
    container.innerHTML = detailMarkup(snapshot, prefix);
  }
  if (!container.dataset.moduleHandler) {
    container.dataset.moduleHandler = "true";
    const expand = (event) => {
      const box = event.target.closest("[data-expand]");
      if (!box) return false;
      openChartDialog(box.dataset.expand, container.dataset.ackPath);
      return true;
    };
    container.addEventListener("keydown", (event) => {
      if ((event.key === "Enter" || event.key === " ") && event.target.matches("[data-expand]")) {
        event.preventDefault();
        expand(event);
      }
    });
    container.addEventListener("click", (event) => {
      if (expand(event)) return;
      const button = event.target.closest("[data-module-id]");
      const back = event.target.closest("[data-back]");
      if (!button && !back) return;
      const previous = selectedModule;
      if (button) selectedModule = button.dataset.moduleId;
      moduleOpen = !!button;
      showModuleView(container, button);
      updateModules(container, latestSnapshot);
      if (moduleOpen) {
        renderModuleAlerts(container, latestSnapshot);
        drawCharts(latestHistory, prefix);
        updateHistoryTable(container, latestHistory);
        container.querySelector("[data-back]").focus({ preventScroll: true });
      } else
        container.querySelector(`[data-module-id="${previous}"]`)?.focus({ preventScroll: true });
    });
  }
  showModuleView(container);
  const v = snapshot.summary || {};
  container.querySelector("[data-history-sync]").hidden = !!matching;
  const continuity = container.querySelector("[data-continuity]");
  continuity.hidden =
    !snapshot.continuity_gap && history?.history_complete !== false;
  const continuityMessage = snapshot.warming_up
    ? "Some earlier readings were lost. Trend detection is rebuilding its sample window; active alerts remain visible."
    : "Some earlier readings were lost. This run’s history is incomplete.";
  if (continuity.textContent !== continuityMessage)
    continuity.textContent = continuityMessage;
  container.querySelector("[data-summary]").innerHTML =
    `<div class="stats"><div class="stat"><span class="label">Max temperature</span><strong>${number(v.max_temp, 1, " °C")}</strong></div><div class="stat"><span class="label">Min voltage</span><strong>${number(v.min_voltage, 3, " V")}</strong></div><div class="stat"><span class="label">Charge estimate</span><strong>${number(v.soc, 0, "%")}</strong></div></div>`;
  container.dataset.ackPath = ackPath;
  updateModules(container, snapshot);
  if (!moduleOpen) return;
  renderModuleAlerts(container, snapshot);
  updateChartDialog();
  drawCharts(history, prefix);
  updateHistoryTable(container, history);
}
// The open module's active alerts sit at the top of the module view, with their Acknowledge buttons.
function renderModuleAlerts(container, snapshot) {
  const box = container.querySelector("[data-module-alerts]");
  const path = container.dataset.ackPath;
  box.dataset.ackPath = path;
  const focusedId = box.contains(document.activeElement) ? document.activeElement.dataset.ack : null;
  const alerts = (snapshot?.alerts || []).filter((a) => a.module_id === selectedModule);
  box.innerHTML = alerts.length ? alertsMarkup(alerts, "city") : "";
  bindAck(box, path);
  if (focusedId)
    [...box.querySelectorAll("[data-ack]")].find((b) => b.dataset.ack === focusedId)?.focus({ preventScroll: true });
}
function showModuleView(container, origin) {
  const overview = container.querySelector("[data-overview]");
  const view = container.querySelector("[data-module-view]");
  overview.hidden = moduleOpen;
  view.hidden = !moduleOpen;
  if (!origin || !moduleOpen) return;
  // Zoom from the clicked module tile into the module view.
  const box = container.getBoundingClientRect();
  const cell = origin.getBoundingClientRect();
  view.style.transformOrigin = `${cell.left - box.left + cell.width / 2}px ${cell.top - box.top + cell.height / 2}px`;
  view.classList.remove("zoom-in");
  void view.offsetWidth;
  view.classList.add("zoom-in");
}
// Expanded chart: full history for the open module, a selectable range, its alerts and a table.
const DIALOG_RANGES = [["2 min", 120], ["5 min", 300], ["15 min", 900], ["All", Infinity]];
const DIALOG_KINDS = {
  temp: { field: "temp_c", label: "Temperature", unit: "°C", digits: 2, limits: [["Warning", 40, "warning"], ["Critical", 55, "critical"], ["Immediate", 60, "critical"]] },
  voltage: { field: "voltage_v", label: "Voltage", unit: "V", digits: 3, limits: [["Low critical", 3.35, "critical"], ["Low warning", 3.55, "warning"], ["High warning", 4.15, "warning"], ["High critical", 4.2, "critical"]] },
};
// view: a zoomed/panned x window in seconds, or null to follow the selected range live.
const chartDialog = { kind: null, range: 300, chart: null, ackPath: "", view: null };
const clockText = (seconds) =>
  `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, "0")}`;
function openChartDialog(kind, ackPath) {
  let dialog = $("chart-dialog");
  if (!dialog) {
    dialog = document.createElement("dialog");
    dialog.id = "chart-dialog";
    dialog.className = "chart-dialog";
    dialog.setAttribute("aria-labelledby", "chart-dialog-title");
    dialog.innerHTML = `<div class="chart-dialog-head"><h2 id="chart-dialog-title"></h2>
      <button class="close" data-close aria-label="Close chart details"><svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M18 6 6 18M6 6l12 12"/></svg></button></div>
      <div class="chart-dialog-body">
        <div class="chart-dialog-toolbar"><div class="range-picker" role="group" aria-label="Time range">${DIALOG_RANGES.map(([label, seconds]) => `<button data-range="${seconds}">${label}</button>`).join("")}</div>
        <button class="reset-zoom" data-reset-zoom hidden>Reset zoom</button><span class="muted zoom-hint">Scroll to zoom · drag to pan · double-click to reset</span></div>
        <p class="muted chart-dialog-stats" data-dialog-stats></p>
        <div class="chart-dialog-canvas"><canvas id="chart-dialog-canvas" role="img" aria-label="Expanded module history"></canvas></div>
        <div class="section-title"><h3>Active alerts</h3></div><div data-dialog-alerts></div>
        <div class="section-title"><h3>Alert history</h3></div><div data-dialog-history-alerts></div>
        <details><summary>Readings in this range</summary><div data-dialog-table></div></details>
      </div>`;
    document.body.append(dialog);
    dialog.querySelector("[data-close]").onclick = () => dialog.close();
    dialog.addEventListener("click", (event) => { if (event.target === dialog) dialog.close(); });
    dialog.querySelectorAll("[data-range]").forEach((button) =>
      button.addEventListener("click", () => { chartDialog.range = Number(button.dataset.range); chartDialog.view = null; updateChartDialog(); }),
    );
    dialog.querySelector("[data-reset-zoom]").onclick = () => { chartDialog.view = null; updateChartDialog(); };
    bindChartZoom($("chart-dialog-canvas"));
    dialog.addEventListener("close", () => { chartDialog.chart?.destroy(); chartDialog.chart = null; chartDialog.kind = null; });
  }
  chartDialog.kind = kind;
  chartDialog.ackPath = ackPath;
  chartDialog.view = null;
  if (!dialog.open) dialog.showModal();
  updateChartDialog();
}
function updateChartDialog() {
  const dialog = $("chart-dialog");
  if (!dialog?.open || !chartDialog.kind) return;
  const spec = DIALOG_KINDS[chartDialog.kind];
  const history = latestHistory || { elapsed: [], timestamps: [], modules: {} };
  const elapsed = history.elapsed || [];
  const values = history.modules?.[selectedModule]?.[spec.field] || [];
  const latest = elapsed.length ? elapsed[elapsed.length - 1] : 0;
  const rangeFrom = Number.isFinite(chartDialog.range) ? Math.max(0, latest - chartDialog.range) : 0;
  const from = chartDialog.view ? chartDialog.view.min : rangeFrom;
  const to = chartDialog.view ? chartDialog.view.max : Math.max(latest, rangeFrom + 60);
  chartDialog.bounds = { min: elapsed.length ? elapsed[0] : 0, max: latest };
  dialog.querySelector("[data-reset-zoom]").hidden = !chartDialog.view;
  const rows = elapsed.map((t, i) => ({ t, stamp: history.timestamps?.[i], y: values[i] })).filter((r) => r.t >= from && r.t <= to);
  const valid = rows.map((r) => r.y).filter((y) => y != null && Number.isFinite(y));
  $("chart-dialog-title").textContent = `${selectedModule || "Module"} · ${spec.label} history`;
  dialog.querySelectorAll("[data-range]").forEach((button) =>
    button.setAttribute("aria-pressed", String(Number(button.dataset.range) === chartDialog.range)),
  );
  dialog.querySelector("[data-dialog-stats]").textContent = valid.length
    ? `${clockText(from)}–${clockText(Math.min(to, latest))} · ${valid.length} readings · min ${Math.min(...valid).toFixed(spec.digits)} ${spec.unit} · max ${Math.max(...valid).toFixed(spec.digits)} ${spec.unit} · avg ${(valid.reduce((a, b) => a + b, 0) / valid.length).toFixed(spec.digits)} ${spec.unit}`
    : "No readings in this range yet.";
  const style = getComputedStyle(document.documentElement);
  const color = (name, fallback) => style.getPropertyValue(name).trim() || fallback;
  const low = valid.length ? Math.min(...valid) : 0, high = valid.length ? Math.max(...valid) : 1;
  const pad = Math.max((high - low) * 0.15, chartDialog.kind === "temp" ? 0.5 : 0.01);
  const xMax = to;
  const datasets = [{
    label: spec.label, data: rows.map((r) => ({ x: r.t, y: r.y })), borderColor: color("--action", "#2459c4"),
    backgroundColor: color("--action", "#2459c4") + "22", fill: true, borderWidth: 2, pointRadius: 0, spanGaps: false,
  }];
  // Limit lines appear only when inside the plotted range, so they never flatten the data.
  spec.limits.filter(([, v]) => v >= low - pad && v <= high + pad).forEach(([name, v, tone]) =>
    datasets.push({ label: name, data: [{ x: from, y: v }, { x: xMax, y: v }], borderColor: color(`--${tone}`, "#bd8b2e"), borderDash: tone === "critical" ? [3, 3] : [8, 4], borderWidth: 1, pointRadius: 0, fill: false }),
  );
  const options = {
    responsive: true, maintainAspectRatio: false, animation: false,
    interaction: { mode: "nearest", intersect: false },
    plugins: { legend: { display: false }, tooltip: { callbacks: { title: (items) => clockText(items[0]?.parsed.x ?? 0) } } },
    scales: {
      x: { type: "linear", min: from, max: xMax, title: { display: true, text: "Time since start (m:ss)", color: color("--secondary", "#526179") },
           ticks: { color: color("--secondary", "#526179"), maxTicksLimit: 8, callback: (v) => clockText(v) }, grid: { display: false } },
      y: { min: low - pad, max: high + pad, title: { display: true, text: `${spec.label} (${spec.unit})`, color: color("--secondary", "#526179") },
           ticks: { color: color("--secondary", "#526179"), callback: (v) => Number(v).toFixed(spec.digits) }, grid: { color: color("--line", "#dce3ec") } },
    },
  };
  if (window.Chart) {
    if (chartDialog.chart) { chartDialog.chart.data = { datasets }; chartDialog.chart.options = options; chartDialog.chart.update("none"); }
    else chartDialog.chart = new Chart($("chart-dialog-canvas"), { type: "line", data: { datasets }, options });
  }
  const moduleAlerts = (list) => (list || []).filter((a) => a.module_id === selectedModule);
  const active = dialog.querySelector("[data-dialog-alerts]");
  if (!active.contains(document.activeElement)) {
    active.innerHTML = alertsMarkup(moduleAlerts(latestSnapshot?.alerts), "city");
    active.dataset.ackPath = chartDialog.ackPath;
    bindAck(active, chartDialog.ackPath);
  }
  const past = moduleAlerts(latestSnapshot?.recent_alerts);
  dialog.querySelector("[data-dialog-history-alerts]").innerHTML = past.length ? alertsMarkup(past) : '<p class="muted">No closed alerts for this module in this run.</p>';
  dialog.querySelector("[data-dialog-table]").innerHTML = rows.length
    ? `<table><thead><tr><th scope="col">Time</th><th scope="col">Source time</th><th scope="col">${spec.label} (${spec.unit})</th></tr></thead><tbody>${rows.slice().reverse().map((r) => `<tr><td>${clockText(r.t)}</td><td>${time(r.stamp)}</td><td>${number(r.y, spec.digits)}</td></tr>`).join("")}</tbody></table>`
    : "<p>No readings in this range yet.</p>";
}
// Wheel zooms the time axis around the cursor; dragging pans; double-click resets.
function bindChartZoom(canvas) {
  const current = () => {
    const x = chartDialog.chart?.scales?.x;
    return x ? { min: x.min, max: x.max, x } : null;
  };
  const clamp = (min, max) => {
    const { min: lo, max: hi } = chartDialog.bounds || { min: 0, max: max };
    const width = Math.max(10, Math.min(max - min, Math.max(hi - lo, 10)));
    let start = Math.max(lo, Math.min(min, hi - width));
    return { min: start, max: start + width };
  };
  canvas.addEventListener("wheel", (event) => {
    const view = current();
    if (!view) return;
    event.preventDefault();
    const at = view.x.getValueForPixel(event.offsetX);
    const factor = event.deltaY > 0 ? 1.25 : 0.8;
    chartDialog.view = clamp(at - (at - view.min) * factor, at + (view.max - at) * factor);
    updateChartDialog();
  }, { passive: false });
  let drag = null;
  canvas.addEventListener("pointerdown", (event) => {
    const view = current();
    if (!view) return;
    drag = { x: event.clientX, min: view.min, max: view.max, width: view.x.width };
    canvas.setPointerCapture(event.pointerId);
    canvas.classList.add("panning");
  });
  canvas.addEventListener("pointermove", (event) => {
    if (!drag) return;
    const shift = ((drag.x - event.clientX) / drag.width) * (drag.max - drag.min);
    if (Math.abs(shift) < 0.01) return;
    chartDialog.view = clamp(drag.min + shift, drag.max + shift);
    updateChartDialog();
  });
  const end = () => { drag = null; canvas.classList.remove("panning"); };
  canvas.addEventListener("pointerup", end);
  canvas.addEventListener("pointercancel", end);
  canvas.addEventListener("dblclick", () => { chartDialog.view = null; updateChartDialog(); });
}
function updateHistoryTable(container, history) {
  // The chart data table was removed from the panel; the expanded chart dialog lists readings.
  if (!container.querySelector("[data-history-table]")) return;
  const series = history?.modules?.[selectedModule];
  const rows = (history?.timestamps || [])
    .map(
      (stamp, index) =>
        `<tr><td>${time(stamp)}</td><td>${esc(series?.temp_c?.[index])}</td><td>${esc(series?.voltage_v?.[index])}</td></tr>`,
    )
    .join("");
  container.querySelector("[data-history-table]").innerHTML = rows
    ? `<table><caption>${esc(selectedModule)} history</caption><thead><tr><th scope="col">Source time</th><th scope="col">Temperature (°C)</th><th scope="col">Voltage (V)</th></tr></thead><tbody>${rows}</tbody></table>`
    : "<p>Matching history is not yet available.</p>";
}
function updateModules(container, snapshot) {
  const grid = container.querySelector("[data-modules]");
  const modules = snapshot.modules || [];
  const buttons = [...grid.querySelectorAll("[data-module-id]")];
  if (
    buttons.length !== modules.length ||
    buttons.some((b, i) => b.dataset.moduleId !== modules[i].module_id)
  ) {
    grid.innerHTML = moduleGrid(modules);
  } else {
    buttons.forEach((button, i) => {
      const m = modules[i];
      button.className = `module ${severity(m.severity)}`;
      button.setAttribute(
        "aria-pressed",
        String(m.module_id === selectedModule),
      );
      button.querySelectorAll("span").forEach((span, j) => {
        span.textContent = [
          number(m.temp_c, 1, " °C"),
          number(m.voltage_v, 3, " V"),
          m.severity || "UNKNOWN",
        ][j];
      });
    });
  }
  container.querySelector("[data-module-detail]").innerHTML =
    selectedModuleMarkup(snapshot);
  container.querySelector("[data-module-detail-zoom]").innerHTML =
    selectedModuleMarkup(snapshot);
  container.querySelector("[data-module-title]").textContent = `${selectedModule || "Module"} detail`;
  container.querySelectorAll("[data-chart-label]").forEach((label) => {
    label.textContent = selectedModule
      ? `${selectedModule} highlighted`
      : "No selection";
  });
}
function bindAck(container, path) {
  container.querySelectorAll("[data-ack]").forEach(button => {
    const key = `${path}/${button.dataset.ack}`;
    const busy = pendingAcks.has(key);
    button.setAttribute("aria-disabled", String(busy));
    button.setAttribute("aria-busy", String(busy));
    button.textContent = busy ? "Acknowledging…" : ackErrors.get(key) || "Acknowledge";
    button.onclick = async () => {
      if (pendingAcks.has(key)) return;
      pendingAcks.add(key); ackErrors.delete(key); bindAck(container, path);
      try {
        await post(`${path}/${encodeURIComponent(button.dataset.ack)}/ack`, {});
      } catch (e) {
        ackErrors.set(key, `Retry: ${e.message}`);
      } finally {
        pendingAcks.delete(key);
        if (container.dataset.ackPath === path) bindAck(container, path);
      }
    };
  });
}
document.querySelectorAll("[data-nav]").forEach((link) => {
  const port =
    Number(location.port) + (link.dataset.nav === "utility" ? -200 : 200);
  const destination = new URL(location.href);
  destination.port = String(port);
  destination.pathname = "/";
  destination.search = "";
  destination.hash = "";
  link.href = destination.href;
});
async function city() {
  let selected = null, fleet = [], frozen = false, opener = null, detailRequest = 0, fleetRequest = 0;
  function updateMapFocus() {
    const cover = selected ? $("drawer").getBoundingClientRect() : null;
    document.querySelectorAll("#map [tabindex], #map a[href], #map button, .map-modes button, #map-retry").forEach(element => {
      if (!element.hasAttribute("data-map-tab")) element.dataset.mapTab = element.getAttribute("tabindex") ?? "none";
      const rect = element.getBoundingClientRect();
      const covered = cover && rect.width && rect.height && rect.left < cover.right && rect.right > cover.left && rect.top < cover.bottom && rect.bottom > cover.top;
      if (covered) element.tabIndex = -1;
      else if (element.dataset.mapTab === "none") element.removeAttribute("tabindex");
      else element.setAttribute("tabindex", element.dataset.mapTab);
    });
  }
  const map = createFleetMap({select, updateFocus: updateMapFocus});
  window.addEventListener("resize", updateMapFocus);
  function routeText(bus) {
    return bus?.route ? `Route ${bus.route.number} · ${bus.route.name}` : "Route unavailable";
  }
  function state(bus) {
    return ["NO_SIGNAL", "OFFLINE"].includes(bus.link_state) ? "NO SIGNAL" : bus.severity || "UNKNOWN";
  }
  function select(id) {
    opener = document.activeElement; selectedModule = null; moduleOpen = false; selected = id;
    $("drawer").hidden = false;
    const bus = fleet.find(item => item.bus_id === id);
    $("drawer-name").textContent = bus?.route ? `Bus ${bus.route.number}` : `Bus ${id}`;
    $("drawer-route").textContent = routeText(bus);
    $("drawer-status").innerHTML = bus ? badge(state(bus)) : badge("UNKNOWN");
    charts.forEach(chart => chart?.destroy()); charts = [];
    latestSnapshot = null; latestHistory = null;
    $("drawer-body").innerHTML = '<p class="muted">Loading vehicle data…</p>';
    map.update(fleet, selected); map.focusSelected(id);
    if (matchMedia("(max-width: 700px)").matches) $("drawer").scrollIntoView({block:"start", behavior:"instant"});
    $("close-drawer").focus({preventScroll:true});
    renderList(); updateMapFocus();
    if (frozen) {
      const snapshot = fleet.find(bus => bus.bus_id === id);
      if (snapshot) showDetail(snapshot, null);
    } else updateDetail();
  }
  function renderList() {
    const focused = $("fleet-list").contains(document.activeElement) ? document.activeElement.dataset.bus : null;
    const query = $("fleet-search").value.trim().toLowerCase();
    const visible = fleet.filter(b => `${b.name || ""} ${b.bus_id} ${b.route?.number || ""} ${b.route?.name || ""}`.toLowerCase().includes(query));
    $("fleet-count").textContent = query ? `${visible.length} / ${fleet.length}` : fleet.length;
    const attention = fleet.filter(b => state(b) !== "NORMAL").length;
    $("fleet-summary").textContent = `${attention} need attention · ${fleet.length - attention} normal`;
    $("fleet-list").innerHTML = visible.length ? visible.map(b => `<button class="bus-row ${selected === b.bus_id ? "selected" : ""}" data-bus="${esc(b.bus_id)}" aria-pressed="${selected === b.bus_id}"><span class="bus-info"><strong>${esc(routeText(b))}</strong></span>${badge(state(b))}</button>`).join("") : `<div class="empty">${query ? "No vehicles match your search. Clear the search to see all vehicles." : "No vehicles available."}</div>`;
    $("fleet-list").querySelectorAll("[data-bus]").forEach(button => button.onclick = () => select(button.dataset.bus));
    if (focused) [...$("fleet-list").querySelectorAll("[data-bus]")].find(button => button.dataset.bus === focused)?.focus({preventScroll:true});
  }
  function showDetail(s, h) {
    const bus = fleet.find(b => b.bus_id === s.bus_id) || s;
    $("drawer-name").textContent = bus.route ? `Bus ${bus.route.number}` : `Bus ${s.bus_id}`;
    $("drawer-route").textContent = routeText(bus);
    $("drawer-status").innerHTML = `${badge(state(bus))} <span class="muted">${esc((bus.link_state || "UNKNOWN").replaceAll("_", " "))} · ${bus.position_stale ? "Position stale" : "Simulated position"}</span>`;
    renderDetails($("drawer-body"), s, h, "city", `/api/buses/${encodeURIComponent(s.bus_id)}/alerts`);
  }
  async function updateDetail() {
    if (!selected || frozen) return;
    const id = selected, request = ++detailRequest;
    try {
      const [s,h] = await Promise.all([api(`/api/buses/${encodeURIComponent(id)}`), api(`/api/buses/${encodeURIComponent(id)}/history`).catch(() => null)]);
      if (!frozen && id === selected && request === detailRequest) showDetail(s,h);
    } catch (e) {
      if (!frozen && id === selected && request === detailRequest) $("drawer-body").innerHTML = `<p class="notice error">Vehicle data unavailable: ${esc(e.message)}</p>`;
    }
  }
  function closeDrawer() {
    const previous = selected; detailRequest++; selected = null; $("drawer").hidden = true;
    map.update(fleet, null); updateMapFocus(); renderList();
    if (opener && opener.isConnected) opener.focus();
    else [...document.querySelectorAll("[data-bus]")].find(el => el.dataset.bus === previous)?.focus();
  }
  $("fleet-search").addEventListener("input", renderList);
  // Freeze updates is disabled for now; its button is commented out in index.html.
  // $("freeze").addEventListener("click", () => {
  //   frozen = !frozen; detailRequest++; fleetRequest++;
  //   $("freeze").textContent = frozen ? "Resume updates" : "Freeze updates";
  //   $("freeze").setAttribute("aria-pressed", String(frozen));
  //   $("frozen-notice").hidden = !frozen;
  //   if (frozen && selected) {
  //     const snapshot = fleet.find(bus => bus.bus_id === selected);
  //     if (snapshot) {
  //       const same = latestSnapshot && ["session_id", "run_id", "event_sequence"].every(key => latestSnapshot[key] === snapshot[key]);
  //       showDetail(snapshot, same ? latestHistory : null);
  //     }
  //   }
  //   if (!frozen) refresh();
  // });
  $("close-drawer").onclick = closeDrawer;
  document.addEventListener("keydown", e => {if (e.key === "Escape" && selected) closeDrawer();});
  async function refresh() {
    if (frozen) return;
    const request = ++fleetRequest;
    try {
      const data = await api("/api/fleet");
      if (frozen || request !== fleetRequest) return;
      fleet = data.buses || [];
      $("fleet-note").hidden = true;
      $("fleet-status").className = `pill connection ${data.utility_online === false ? "critical" : "normal"}`;
      $("fleet-status").textContent = data.utility_online === false ? "Utility offline" : "Fleet connected";
      renderList(); map.update(fleet, selected);
      if (selected) await updateDetail();
    } catch (e) {
      if (frozen || request !== fleetRequest) return;
      $("fleet-status").className = "pill connection critical"; $("fleet-status").textContent = "Connection lost";
      $("fleet-note").hidden = false; $("fleet-note").textContent = "Fleet connection lost. Displayed positions may be stale.";
    }
  }
  async function poll() {await refresh(); setTimeout(poll, 2000);}
  poll();
}
async function utility() {
  let buses = [],
    scenarios = [],
    fleet = {},
    selectedBus = null;
  const drafts = new Map();
  const SOURCE_LABEL = { config: "Config", upload: "Uploaded", supplied: "Supplied" };
  const SOURCE_ORDER = ["config", "upload", "supplied"];
  const notice = (message, error = false) => {
    $("utility-notice").innerHTML =
      `<div class="notice ${error ? "error" : ""}">${esc(message)}</div>`;
  };
  const scenarioName = (id) => scenarios.find((s) => s.id === id)?.name || "Unknown file";
  const sorted = () =>
    [...scenarios].sort(
      (a, b) =>
        SOURCE_ORDER.indexOf(a.source) - SOURCE_ORDER.indexOf(b.source) ||
        a.name.localeCompare(b.name),
    );
  function filesMarkup() {
    return `<section class="panel files-panel" aria-labelledby="files-heading">
      <div class="panel-top"><h2 id="files-heading">CSV files</h2><span class="count">${scenarios.length}</span></div>
      <label class="field">Upload a CSV<input type="file" accept=".csv,text/csv" data-file></label>
      <div class="controls"><button data-action="upload">Upload</button></div>
      <ul class="file-list">${sorted()
        .map(
          (s) =>
            `<li><span class="file-name">${esc(s.name)}</span><span class="tag">${esc(SOURCE_LABEL[s.source] || "File")}</span><small>${esc(s.report?.frame_count ?? "—")} rows</small></li>`,
        )
        .join("")}</ul>
    </section>`;
  }
  function busMarkup(b) {
    const configs = scenarios.filter((s) => s.fault);
    return `<section class="panel bus-panel" data-id="${esc(b.bus_id)}" aria-labelledby="bus-heading">
      <div class="panel-top"><div><div class="eyebrow">${esc(busLabel(b))}</div><h2 id="bus-heading">${esc(b.name || busLabel(b))}</h2></div><span class="pill ${b.state === "offline" ? "unknown" : "normal"}">${esc(b.state || "Available")}</span></div>
      <p class="source" data-meta>Loading source…</p>
      <div class="label">CSV source</div>
      <label class="field">Assign a CSV<select data-scenario>${sorted()
        .map((s) => `<option value="${esc(s.id)}">${esc(s.name)}</option>`)
        .join("")}</select></label>
      <div class="controls"><button class="primary" data-action="assign">Assign CSV</button><button data-action="default">Reset to default (${esc(scenarioName(b.default_scenario_id))})</button></div>
      <div class="label">Simulate faults</div>
      <div class="fault-grid">${configs
        .map(
          (s) =>
            `<button data-action="fault" data-scenario-id="${esc(s.id)}" aria-pressed="false">${esc(s.fault)}</button>`,
        )
        .join("")}</div>
      <div class="label">Vehicle</div>
      <div class="controls"><button data-action="parked" aria-pressed="false">Set bus to parked</button></div>
    </section>`;
  }
  async function assign(id, scenarioId) {
    await post(`/api/buses/${encodeURIComponent(id)}/scenario`, { scenario_id: scenarioId });
    drafts.delete(id);
    notice(`${buses.find((b) => b.bus_id === id)?.name || id}: now reading ${scenarioName(scenarioId)} from time 0.`);
  }
  function render() {
    const grid = $("utility-grid");
    $("utility-count").textContent = buses.length;
    $("utility-buses").innerHTML = buses
      .map(
        (b) =>
          `<button class="bus-row ${selectedBus === b.bus_id ? "selected" : ""}" data-select-bus="${esc(b.bus_id)}"><span class="bus-info"><strong>${esc(busLabel(b))}</strong><small data-utility-route>${esc(b.name || "Route unavailable")}</small></span></button>`,
      )
      .join("");
    $("utility-buses")
      .querySelectorAll("[data-select-bus]")
      .forEach((button) =>
        button.addEventListener("click", () => {
          selectedBus = button.dataset.selectBus;
          render();
          updateMeta();
          [...$("utility-buses").querySelectorAll("[data-select-bus]")]
            .find((item) => item.dataset.selectBus === selectedBus)
            ?.focus();
        }),
      );
    const bus = buses.find((b) => b.bus_id === selectedBus);
    grid.innerHTML = filesMarkup() + (bus ? busMarkup(bus) : '<div class="empty">No vehicles registered.</div>');
    const panel = grid.querySelector(".bus-panel");
    if (panel) {
      const select = panel.querySelector("[data-scenario]");
      if (drafts.has(panel.dataset.id)) select.value = drafts.get(panel.dataset.id);
      select.addEventListener("change", () => drafts.set(panel.dataset.id, select.value));
    }
    grid.querySelectorAll("button[data-action]").forEach(
      (button) =>
        (button.onclick = async () => {
          const id = panel?.dataset.id,
            action = button.dataset.action;
          button.disabled = true;
          try {
            if (action === "upload") {
              const file = grid.querySelector("[data-file]").files[0];
              if (!file) throw new Error("Choose a CSV file first.");
              const form = new FormData();
              form.append("file", file);
              const uploaded = await api("/api/scenarios", { method: "POST", body: form });
              scenarios = (await api("/api/scenarios")).scenarios || scenarios;
              notice(`Uploaded ${uploaded.name}. Select it under Assign a CSV to use it.`);
              render();
            } else if (action === "assign")
              await assign(id, panel.querySelector("[data-scenario]").value);
            else if (action === "default")
              await assign(id, buses.find((b) => b.bus_id === id).default_scenario_id);
            else if (action === "fault") await assign(id, button.dataset.scenarioId);
            else if (action === "parked")
              await post(`/api/buses/${encodeURIComponent(id)}/vehicle-state`, {
                vehicle_state: button.getAttribute("aria-pressed") === "true" ? "moving" : "parked",
                demo_override: false,
              });
            await updateMeta();
          } catch (e) {
            notice(e.message, true);
          } finally {
            button.disabled = false;
          }
        }),
    );
    showMeta();
  }
  function showMeta() {
    const panel = document.querySelector(".bus-panel");
    const b = panel && fleet[panel.dataset.id];
    if (!b) return;
    const m = b.playback || {};
    panel.querySelector("[data-meta]").innerHTML =
      `Reading <strong>${esc(m.scenario_name || "—")}</strong><br>Row ${esc(m.frame_index == null ? "—" : m.frame_index + 1)} of ${esc(m.frame_count ?? "—")} · Time ${esc(Math.floor(m.elapsed_s ?? 0))} s · Loop ${esc((m.loop_count ?? 0) + 1)}`;
    const select = panel.querySelector("[data-scenario]");
    if (document.activeElement !== select && m.scenario_id && !drafts.has(b.bus_id))
      select.value = m.scenario_id;
    panel.querySelectorAll('[data-action="fault"]').forEach((button) =>
      button.setAttribute("aria-pressed", String(button.dataset.scenarioId === m.scenario_id)),
    );
    const parked = panel.querySelector('[data-action="parked"]');
    parked.setAttribute("aria-pressed", String(b.vehicle_state === "parked"));
    parked.textContent = b.vehicle_state === "parked" ? "Parked (select to set moving)" : "Set bus to parked";
  }
  async function updateMeta() {
    try {
      const data = await api("/api/fleet");
      fleet = Object.fromEntries((data.buses || []).map((b) => [b.bus_id, b]));
      showMeta();
      // Jaskaran's vehicle rows show the route under the bus number.
      Object.values(fleet).forEach((b) => {
        const row = [...document.querySelectorAll("[data-select-bus]")].find((item) => item.dataset.selectBus === b.bus_id);
        const route = row?.querySelector("[data-utility-route]");
        if (route) route.textContent = b.route ? `Route ${b.route.number} · ${b.route.name}` : b.name || "Route unavailable";
      });
    } catch {}
  }
  try {
    const [registry, source] = await Promise.all([
      api("/api/registry"),
      api("/api/scenarios"),
    ]);
    buses = registry.buses || [];
    selectedBus = buses[0]?.bus_id || null;
    scenarios = source.scenarios || [];
    $("utility-status").textContent = "Connected";
    $("utility-status").className = "pill connection normal";
    render();
    async function pollMeta() {
      await updateMeta();
      setTimeout(pollMeta, 1000);
    }
    pollMeta();
  } catch (e) {
    $("utility-status").textContent = "Connection lost";
    $("utility-status").className = "pill connection critical";
    notice(
      `${e.message}. Check that the utility is running, then retry the connection.`,
      true,
    );
    $("utility-buses").innerHTML =
      '<p class="empty">Vehicle list unavailable.</p>';
    $("utility-grid").innerHTML =
      '<div class="empty"><p>Controls could not load.</p><button id="retry-utility">Retry connection</button></div>';
    $("retry-utility").onclick = () => location.reload();
  }
}
if (document.body.dataset.page === "city") city();
else if (document.body.dataset.page === "utility") utility();

document.addEventListener("voltwatch:theme", () => {
  if (latestSnapshot && latestHistory) drawCharts(latestHistory, "city");
});
