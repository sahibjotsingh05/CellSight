'use strict';
(() => {
  const $ = id => document.getElementById(id);
  const escape = value => String(value ?? '—').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const num = (value, digits = 1, unit = '') => value == null || !Number.isFinite(Number(value)) ? '—' : `${Number(value).toFixed(digits)}${unit}`;
  const exact = value => value == null || !Number.isFinite(Number(value)) ? '—' : escape(value);
  const time = value => value ? String(value).replace('T', ' ').replace('Z', ' UTC') : 'Unavailable';
  const level = value => ['NORMAL', 'WARNING', 'CRITICAL'].includes(value) ? value.toLowerCase() : 'unknown';
  const units = {TEMPERATURE:' °C', TEMPERATURE_RISE:' °C/min', LOW_VOLTAGE:' V', HIGH_VOLTAGE:' V'};
  let last = null, history = null, selected = null, expanded = false, announcement = null;
  let statusRequest = 0, historyRequest = 0;
  const pendingAcks = new Set(), ackErrors = new Map();
  async function api(path, options = {}) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 5000);
    try {
      const response = await fetch(path, {...options, signal: controller.signal});
      if (!response.ok) throw new Error(`Request failed (${response.status})`);
      return await response.json();
    } finally { clearTimeout(timeout); }
  }
  function announce(key, text) {
    if (key !== announcement && (announcement !== null || key.startsWith('CRITICAL'))) $('driver-announcement').textContent = text;
    announcement = key;
  }
  function close(automatic = false) {
    const hadFocus = $('expanded').contains(document.activeElement);
    expanded = false;
    historyRequest++;
    $('expanded').hidden = true;
    $('expand').setAttribute('aria-expanded', 'false');
    $('expand').textContent = 'View diagnostics';
    window.VoltCharts?.destroy();
    if (hadFocus) (automatic ? $('main') : $('expand')).focus();
  }
  $('collapse').addEventListener('click', () => close());
  $('expand').addEventListener('click', () => {
    if (!last?.expanded_allowed || last.link_state !== 'LIVE') return;
    if (expanded) return close();
    expanded = true;
    $('expanded').hidden = false;
    $('expand').setAttribute('aria-expanded', 'true');
    $('expand').textContent = 'Hide diagnostics';
    renderDetail();
  });
  document.addEventListener('voltwatch:theme', () => window.VoltCharts?.refreshTheme());
  function alertMarkup(alerts, active) {
    return alerts.length ? alerts.map(a => `<div class="alert ${level(a.severity)}"><strong>${escape(a.severity)} · ${escape(a.module_id || 'Battery')}</strong><div>${escape(a.message || a.type)}</div><small>Value: ${escape(num(a.value, 3, units[a.type] || ''))} · Threshold: ${escape(num(a.threshold, 3, units[a.type] || ''))} · First seen: ${escape(time(a.first_seen))}${a.injected ? ' · Injected' : ''}${a.acknowledged ? ' · Acknowledged' : ''}</small>${active && a.active && !a.acknowledged ? `<div><button data-ack="${escape(a.id)}" aria-disabled="${pendingAcks.has(a.id)}">${pendingAcks.has(a.id) ? 'Acknowledging…' : 'Acknowledge'}</button>${ackErrors.has(a.id) ? `<span role="status"> ${escape(ackErrors.get(a.id))}</span>` : ''}</div>` : ''}</div>`).join('') : '<p class="muted">No alerts.</p>';
  }
  function buildDetail() {
    $('driver-detail').innerHTML = `<div data-active></div><div class="diagnostic-grid"><div><h3>Module readings</h3><div class="modules" data-modules></div><p data-module-detail></p></div><div><h3>Temperature history</h3><div class="chart-box"><canvas id="driver-temp" role="img" aria-label="Selected module temperature history"></canvas></div><h3>Voltage history</h3><div class="chart-box"><canvas id="driver-voltage" role="img" aria-label="Selected module voltage history"></canvas></div></div></div><p data-sync class="muted" hidden>History is synchronizing with these readings.</p><p data-continuity class="notice" role="status" hidden></p><details><summary>Read chart data as a table</summary><div data-table></div></details><details><summary>Chart limits</summary><p>Prototype limits: temperature warning ≥40 °C, critical ≥55 °C, immediate ≥60 °C. Voltage warning ≤3.55 V or ≥4.15 V; critical ≤3.35 V or &gt;4.20 V. These are not certified bus limits.</p></details><details><summary>Recent alerts and source details</summary><div data-recent></div><p data-source></p></details>`;
  }
  function updateDetail() {
    if (!expanded || !last || last.link_state !== 'LIVE') return;
    const root = $('driver-detail');
    if (!root.querySelector('[data-modules]')) buildDetail();
    const modules = last.modules || [];
    if (!modules.some(m => m.module_id === selected)) selected = (last.alerts || []).find(a => a.severity === last.severity && modules.some(m => m.module_id === a.module_id))?.module_id || modules[0]?.module_id || null;
    const active = root.querySelector('[data-active]');
    const focusedAlert = active.contains(document.activeElement);
    const focusedAck = document.activeElement?.dataset.ack;
    active.innerHTML = `<h3 tabindex="-1" data-alert-heading>Active alerts</h3>${alertMarkup(last.alerts || [], true)}`;
    if (focusedAlert) {
      const target = [...active.querySelectorAll('[data-ack]')].find(button => button.dataset.ack === focusedAck);
      (target || active.querySelector('[data-alert-heading]')).focus({preventScroll:true});
    }
    const grid = root.querySelector('[data-modules]');
    const focus = document.activeElement?.dataset.moduleId;
    grid.innerHTML = modules.map(m => `<button class="module ${level(m.severity)}" data-module-id="${escape(m.module_id)}" aria-pressed="${m.module_id === selected}"><b>${escape(m.module_id)}</b> <span>${num(m.temp_c, 1, ' °C')}</span> <span>${num(m.voltage_v, 3, ' V')}</span> <span>${escape(m.severity)}</span></button>`).join('') || '<p>No module readings available.</p>';
    if (focus) [...grid.querySelectorAll('button')].find(b => b.dataset.moduleId === focus)?.focus();
    const m = modules.find(item => item.module_id === selected);
    const flags = Object.entries(m?.quality || {}).filter(([,v]) => v !== 'ok').map(([k,v]) => `${k.replaceAll('_',' ')}: ${v}`).join(' · ');
    root.querySelector('[data-module-detail]').textContent = m ? `${m.module_id} · Current ${num(m.current_a,1,' A')} · SoC ${num(m.soc_pct,0,'%')} · SoH ${num(m.soh_pct,0,'%')} · Sensor: ${m.sensor_status || 'unknown'} · ${flags || 'No quality flags'}` : 'Select a module.';
    root.querySelector('[data-sync]').hidden = !!history;
    const continuity = root.querySelector('[data-continuity]');
    continuity.hidden = !last.continuity_gap && history?.history_complete !== false;
    continuity.textContent = last.warming_up ? 'Some earlier readings were lost. Trend detection is rebuilding its sample window; active alerts remain visible.' : 'Some earlier readings were lost. This run’s history is incomplete.';
    root.querySelector('[data-recent]').innerHTML = alertMarkup(last.recent_alerts || [], false);
    root.querySelector('[data-source]').textContent = `Source time: ${time(last.source_time)} · Updated: ${time(last.updated_at)} · ${last.link_state} · Run: ${last.run_id ?? '—'} · Event: ${last.event_sequence ?? '—'}`;
    const series = history?.modules?.[selected];
    const rows = (history?.timestamps || []).map((stamp,i) => `<tr><td>${escape(time(stamp))}</td><td>${exact(series?.temp_c?.[i])}</td><td>${exact(series?.voltage_v?.[i])}</td></tr>`).join('');
    root.querySelector('[data-table]').innerHTML = rows ? `<table><caption>${escape(selected)} history</caption><thead><tr><th scope="col">Source time</th><th scope="col">Temperature (°C)</th><th scope="col">Voltage (V)</th></tr></thead><tbody>${rows}</tbody></table>` : '<p>Matching history is not yet available.</p>';
    window.VoltCharts?.draw(history, 'driver', selected);
  }
  $('driver-detail').addEventListener('click', async event => {
    const module = event.target.closest('[data-module-id]');
    if (module) { selected = module.dataset.moduleId; updateDetail(); return; }
    const ack = event.target.closest('[data-ack]');
    if (!ack || pendingAcks.has(ack.dataset.ack)) return;
    const id = ack.dataset.ack;
    pendingAcks.add(id);
    ackErrors.delete(id);
    updateDetail();
    try {
      const result = await api(`/api/alerts/${encodeURIComponent(id)}/ack`, {method:'POST'});
      const acknowledged = result.alerts?.find(alert => alert.id === id && alert.acknowledged);
      if (acknowledged && last?.session_id === result.session_id && last?.run_id === result.run_id) {
        const current = last.alerts?.find(alert => alert.id === id);
        if (current) current.acknowledged = true;
        updateDetail();
      }
      await refresh();
    } catch (error) {
      ackErrors.set(id, error.message);
    } finally {
      pendingAcks.delete(id);
      updateDetail();
    }
  });
  async function renderDetail() {
    if (!expanded || !last) return;
    const snapshot = last, request = ++historyRequest;
    let result = null;
    try { result = await api('/api/history'); } catch {}
    if (!expanded || last !== snapshot || request !== historyRequest) return;
    history = result && ['session_id','run_id','event_sequence'].every(key => result[key] === snapshot[key]) ? result : null;
    updateDetail();
  }
  function lost() {
    last = null; history = null; close(true);
    $('status-panel').className = 'surface status-panel unknown';
    $('driver-status').textContent = 'NO SIGNAL'; $('driver-title').textContent = 'Status unavailable';
    $('driver-message').textContent = 'Battery data connection lost.'; $('driver-affected').textContent = '';
    $('driver-charge').textContent = 'Unavailable'; $('driver-charge').style.fontSize = '2rem'; $('driver-coverage').textContent = 'Coverage unavailable'; $('driver-quality').textContent = 'Unknown';
    $('expand').disabled = true;
    $('gate-note').textContent = 'Diagnostics unavailable while the data link is lost.';
    announce('NO_SIGNAL', 'Battery data connection lost. Status unavailable.');
  }
  async function refresh() {
    const request = ++statusRequest;
    try {
      const s = await api('/api/status');
      if (request !== statusRequest) return;
      if (s.link_state !== 'LIVE') { lost(); return; }
      last = s;
      const allowed = !!s.expanded_allowed;
      if (!allowed && expanded) close(true);
      // Same "Bus <route number>" name the fleet map uses.
      $('driver-bus').textContent = s.route?.number ? `Bus ${s.route.number}` : s.bus_id ? `Bus ${s.bus_id}` : 'Driver display';
      $('driver-mode').textContent = s.demo_override ? 'Diagnostic demo enabled' : s.vehicle_state === 'parked' ? 'Parked' : s.vehicle_state === 'moving' ? 'Moving' : 'Vehicle state unavailable';
      $('status-panel').className = `surface status-panel ${level(s.severity)}`;
      $('driver-status').textContent = s.severity || 'UNKNOWN';
      $('driver-title').textContent = ({NORMAL:'Battery normal',WARNING:'Attention needed',CRITICAL:'Critical attention',UNKNOWN:'Status unknown'})[s.severity] || 'Status unknown';
      $('driver-message').textContent = s.driver_message || 'Battery status unavailable.';
      const priority = ['CRITICAL','WARNING','UNKNOWN'].map(l => (s.alerts || []).find(a => a.severity === l)).find(Boolean);
      $('driver-affected').textContent = priority ? `${priority.module_id || 'Battery'} · ${priority.type?.replaceAll('_',' ') || 'Alert'}${priority.value != null ? ` · ${num(priority.value, 3, units[priority.type] || '')}` : ''}${priority.injected ? ' · Injected' : ''}` : '';
      announce([s.severity,priority?.module_id,priority?.type].join(':'), `${$('driver-title').textContent}. ${$('driver-message').textContent}`);
      $('driver-charge').textContent = num(s.summary?.soc,0,'%');
      $('driver-charge').style.fontSize = '';
      $('driver-coverage').textContent = `Temperature coverage ${s.summary?.coverage == null ? '—' : Math.round(s.summary.coverage * 12)}/12 modules`;
      $('driver-quality').textContent = s.severity === 'UNKNOWN' ? 'Incomplete or unknown' : 'See diagnostics for sensor quality';
      $('expand').disabled = !allowed;
      $('gate-note').textContent = allowed ? s.demo_override ? 'Diagnostic demo override enabled.' : 'Diagnostics available while parked.' : 'Diagnostics available when parked or in diagnostic demo mode.';
      if (expanded) {
        history = null;
        updateDetail();
        renderDetail();
      }
    } catch { if (request === statusRequest) lost(); }
  }
  async function poll() { await refresh(); setTimeout(poll, 2000); }
  poll();
})();
