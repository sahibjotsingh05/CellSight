/* Selected-module charts share an elapsed-time axis and never fill data gaps. */
(() => {
  const charts = new Map();
  // x is seconds since the bus started its current file; show at least two minutes, then slide.
  const WINDOW_S = 120;
  const clock = seconds => `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`;
  let lastDraw = null;
  const reducedMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  function colors() {
    const style = getComputedStyle(document.documentElement);
    const get = (name, fallback) => style.getPropertyValue(name).trim() || fallback;
    return {
      text: get('--text', '#1b3030'),
      secondary: get('--secondary', '#607877'),
      action: get('--action', '#147d70'),
      line: get('--line', '#d9e1e1'),
      warning: get('--warning', '#a96b12'),
      critical: get('--critical', '#bf433c'),
    };
  }

  function tint(color) {
    if (/^#[0-9a-f]{6}$/i.test(color)) return `${color}18`;
    const rgb = color.match(/^rgba?\(\s*(\d+)[, ]+\s*(\d+)[, ]+\s*(\d+)/i);
    return rgb ? `rgba(${rgb[1]}, ${rgb[2]}, ${rgb[3]}, 0.09)` : 'rgba(20, 125, 112, 0.09)';
  }

  function sourceTime(timestamp) {
    const value = String(timestamp || '');
    const match = value.match(/[T\s](\d{2}:\d{2}:\d{2}(?:\.\d+)?)/);
    return match ? match[1] : value;
  }

  function range(values, padding) {
    const valid = values.filter(Number.isFinite);
    if (!valid.length) return null;
    const low = Math.min(...valid);
    const high = Math.max(...valid);
    const pad = Math.max(padding, (high - low) * 0.1);
    const min = low - pad;
    const max = high + pad;
    return Number.isFinite(min) && Number.isFinite(max) && min < max ? { min, max } : null;
  }

  const limits = {
    temp: [
      { label: 'Warning 40 °C', value: 40, color: 'warning' },
      { label: 'Critical 55 °C', value: 55, color: 'critical' },
      { label: 'Immediate 60 °C', value: 60, color: 'critical' },
    ],
    voltage: [
      { label: 'Low critical 3.35 V', value: 3.35, color: 'critical' },
      { label: 'Low warning 3.55 V', value: 3.55, color: 'warning' },
      { label: 'High warning 4.15 V', value: 4.15, color: 'warning' },
      { label: 'High critical 4.20 V', value: 4.2, color: 'critical' },
    ],
  };

  const displayPlugin = {
    id: 'voltDisplay',
    afterDraw(chart, _args, options) {
      const { ctx, chartArea, scales } = chart;
      if (!chartArea) return;
      const palette = colors();
      ctx.save();
      if (!options.hasData) {
        ctx.fillStyle = palette.secondary;
        ctx.textAlign = 'center';
        ctx.textBaseline = 'middle';
        ctx.font = '500 14px Manrope, sans-serif';
        ctx.fillText(options.message || 'Waiting for samples', (chartArea.left + chartArea.right) / 2, (chartArea.top + chartArea.bottom) / 2);
      } else {
        ctx.font = '500 10px Manrope, sans-serif';
        ctx.textAlign = 'right';
        ctx.textBaseline = 'bottom';
        let lastLabelY = -Infinity;
        const visible = limits[options.kind]
          .filter(limit => limit.value >= scales.y.min && limit.value <= scales.y.max)
          .map(limit => ({ ...limit, y: scales.y.getPixelForValue(limit.value) }))
          .sort((a, b) => a.y - b.y);
        for (const limit of visible) {
          ctx.strokeStyle = palette[limit.color];
          ctx.globalAlpha = 0.45;
          ctx.lineWidth = 1;
          ctx.beginPath();
          ctx.moveTo(chartArea.left, limit.y);
          ctx.lineTo(chartArea.right, limit.y);
          ctx.stroke();
          // Omit overlapping text; the complete policy remains outside the chart.
          if (limit.y - lastLabelY >= 14) {
            ctx.globalAlpha = 0.9;
            ctx.fillStyle = palette[limit.color];
            ctx.fillText(limit.label, chartArea.right - 3, Math.max(chartArea.top + 11, limit.y - 2));
            lastLabelY = limit.y;
          }
        }
      }
      ctx.restore();
    },
  };

  function drawOne(history, prefix, selectedModule, kind, animate) {
    const canvas = document.getElementById(`${prefix}-${kind}`);
    const previous = charts.get(kind);
    if (!canvas) {
      previous?.destroy();
      charts.delete(kind);
      return;
    }
    if (previous && previous.canvas !== canvas) {
      previous.destroy();
      charts.delete(kind);
    }
    const labels = Array.isArray(history?.timestamps) ? history.timestamps : [];
    const field = kind === 'temp' ? 'temp_c' : 'voltage_v';
    const readings = history?.modules?.[selectedModule]?.[field] || [];
    const elapsed = Array.isArray(history?.elapsed) ? history.elapsed : labels.map((_, index) => index);
    const latest = elapsed.length ? elapsed[elapsed.length - 1] : 0;
    const xMin = Math.max(0, latest - WINDOW_S);
    const xMax = Math.max(WINDOW_S, latest);
    const values = labels.map((_, index) => Number.isFinite(readings[index]) ? readings[index] : null);
    const points = values.map((y, index) => ({ x: elapsed[index], y }));
    const bounds = range(values, kind === 'temp' ? 0.5 : 0.015);
    const lastValid = values.findLastIndex(value => value !== null);
    const palette = colors();
    const unit = kind === 'temp' ? '°C' : 'V';
    const precision = kind === 'temp' ? 1 : 3;
    const dataset = {
      label: selectedModule || 'Module',
      data: points,
      borderColor: palette.action,
      backgroundColor: palette.action,
      borderWidth: 2.5,
      tension: 0,
      fill: { target: 'origin', above: tint(palette.action), below: tint(palette.action) },
      spanGaps: false,
      pointRadius: context => context.dataIndex === lastValid ? 4 : 0,
      pointHoverRadius: 5,
      pointBackgroundColor: palette.action,
      pointBorderColor: palette.action,
    };
    const config = {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      interaction: { mode: 'nearest', intersect: false },
      plugins: {
        legend: { display: false },
        voltDisplay: { hasData: Boolean(bounds), kind, message: values.some(Number.isFinite) ? 'Values outside display range' : 'Waiting for samples' },
        tooltip: {
          callbacks: {
            title: items => {
              const index = items[0]?.dataIndex;
              return index == null ? '' : `${clock(elapsed[index])} · source ${sourceTime(labels[index])}`;
            },
            label: item => `${selectedModule || 'Module'}: ${item.parsed.y.toFixed(precision)} ${unit}`,
          },
        },
      },
      scales: {
        x: {
          type: 'linear',
          min: xMin,
          max: xMax,
          display: Boolean(bounds),
          title: { display: true, text: 'Time since start (m:ss)', color: palette.secondary, font: { family: 'Manrope', size: 12 } },
          ticks: { color: palette.secondary, maxTicksLimit: 5, maxRotation: 0, font: { family: 'Manrope', size: 11 }, callback(value) { return clock(value); } },
          grid: { display: false },
          border: { color: palette.line },
        },
        y: {
          display: Boolean(bounds),
          min: bounds?.min,
          max: bounds?.max,
          title: { display: true, text: kind === 'temp' ? 'Temperature (°C)' : 'Voltage (V)', color: palette.secondary, font: { family: 'Manrope', size: 12 } },
          ticks: { color: palette.secondary, maxTicksLimit: 4, font: { family: 'Manrope', size: 11 }, callback(value) { return Number(value).toFixed(precision); } },
          grid: { color: palette.line },
          border: { display: false },
        },
      },
    };
    let chart = charts.get(kind);
    if (!chart) {
      chart = new window.Chart(canvas, { type: 'line', data: { datasets: [dataset] }, options: config, plugins: [displayPlugin] });
      charts.set(kind, chart);
    } else {
      chart.data = { datasets: [dataset] };
      chart.options = config;
      chart.update('none');
    }
  }

  function draw(history, prefix, selectedModule) {
    if (!window.Chart) return;
    const signature = JSON.stringify([history?.timestamps, history?.modules?.[selectedModule], prefix, selectedModule]);
    const changed = Boolean(lastDraw && lastDraw.prefix === prefix && lastDraw.selectedModule === selectedModule && signature !== lastDraw.signature);
    lastDraw = { history, prefix, selectedModule, signature };
    ['temp', 'voltage'].forEach(kind => drawOne(history, prefix, selectedModule, kind, changed));
  }

  function destroy() {
    charts.forEach(chart => chart.destroy());
    charts.clear();
    lastDraw = null;
  }

  function refreshTheme() {
    if (lastDraw) draw(lastDraw.history, lastDraw.prefix, lastDraw.selectedModule);
  }

  window.VoltCharts = { draw, destroy, refreshTheme };
  document.fonts?.ready.then(refreshTheme);
})();
