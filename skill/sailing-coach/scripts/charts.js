// Interactive race charts for report.html (Plotly basic bundle, inlined by html_report.py).
// Each .chart element (data-chart = chart kind, data-race = race folder) is drawn the first time
// its page is shown. Race data comes from <script type="application/json" id="race-<id>">, written by
// analyze.py (plotdata.json): 1 Hz series in metres, upwind up, t = seconds from the gun.
(function () {
  const DATA = {};
  function race(id) {
    if (!DATA[id]) DATA[id] = prep(JSON.parse(document.getElementById('race-' + id).textContent));
    return DATA[id];
  }

  function css(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }
  function dark() {
    const t = document.documentElement.dataset.theme;
    return t ? t === 'dark' : matchMedia('(prefers-color-scheme: dark)').matches;
  }
  function colors() {
    const d = dark();
    return {
      ink: css('--ink'), ink2: css('--ink2'), line: css('--line'), card: css('--card'),
      blue: d ? '#3987e5' : '#2a78d6', orange: d ? '#d95926' : '#eb6834',
      seq: d ? [[0, '#1c5cab'], [1, '#cde2fb']] : [[0, '#9ec5f4'], [1, '#0d366b']],
    };
  }

  // "−1:23 to gun" before the start, "+12:05" after it
  function clock(t) {
    if (t === null || t === undefined) return '–';
    const a = Math.abs(Math.round(t));
    const s = Math.floor(a / 60) + ':' + String(a % 60).padStart(2, '0');
    return t < 0 ? '−' + s + ' to gun' : '+' + s;
  }
  function f(v, n, unit) {
    return v === null || v === undefined ? '–' : v.toFixed(n) + (unit || '');
  }

  function prep(d) {
    const s = d.series, n = s.t.length;
    s.heelAbs = s.heel ? s.heel.map(v => (v === null ? null : Math.abs(v))) : new Array(n).fill(null);
    s.min = s.t.map(t => (t === null ? null : t / 60));
    s.sog5 = smooth(s.sog, 5);
    s.heel5 = smooth(s.heelAbs, 5);
    // One hover block per point: time, speed, VMG, heading, heel
    s.hover = s.t.map((t, i) =>
      '<b>' + clock(t) + '</b><br>SOG ' + f(s.sog[i], 2, ' kt') + '<br>VMG ' + f(s.vmg[i], 2, ' kt') +
      '<br>Heading ' + f(s.hdg[i], 0, '°') + '<br>Heel ' + f(s.heelAbs[i], 0, '°'));
    d.legType = {};
    d.legs.forEach(l => (d.legType[l.leg] = l.type));
    d.idxAt = t => { let b = 0; s.t.forEach((v, i) => { if (Math.abs(v - t) < Math.abs(s.t[b] - t)) b = i; }); return b; };
    return d;
  }
  function smooth(a, w) {
    const h = Math.floor(w / 2);
    return a.map((_, i) => {
      let sum = 0, k = 0;
      for (let j = i - h; j <= i + h; j++) if (j >= 0 && j < a.length && a[j] !== null) { sum += a[j]; k++; }
      return k ? sum / k : null;
    });
  }
  function pick(d, keep) {
    const s = d.series, idx = [];
    s.t.forEach((t, i) => { if (keep(i, t)) idx.push(i); });
    const g = (arr) => idx.map(i => (arr ? arr[i] : null));
    return { idx, g };
  }

  function layout(c, extra) {
    return Object.assign({
      paper_bgcolor: 'rgba(0,0,0,0)', plot_bgcolor: 'rgba(0,0,0,0)',
      font: { color: c.ink2, size: 12, family: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif' },
      margin: { l: 56, r: 16, t: narrow() ? 56 : 40, b: 44 }, showlegend: false,
      hoverlabel: { bgcolor: c.card, bordercolor: c.line, font: { color: c.ink, size: 12 }, align: 'left' },
      dragmode: innerWidth < 700 ? false : 'zoom',
    }, extra);
  }
  function axis(c, extra) {
    return Object.assign({ gridcolor: c.line, zerolinecolor: c.line, linecolor: c.line, tickcolor: c.line }, extra);
  }
  const CONFIG = {
    responsive: true, displaylogo: false,
    modeBarButtonsToRemove: ['select2d', 'lasso2d', 'autoScale2d', 'toggleSpikelines'],
  };
  const narrow = () => innerWidth < 700;
  function title(c, full, short) {
    return { text: narrow() ? short : full, font: { size: 13, color: c.ink }, x: 0, xref: 'paper' };
  }
  // Height that fits an equal-aspect map to the chart's width, within sensible bounds
  function mapHeight(el, xr, yr, lo, hi) {
    const w = Math.max(el.clientWidth - 110, 200);
    return Math.round(Math.min(hi, Math.max(lo, w * (yr[1] - yr[0]) / (xr[1] - xr[0]) + 90)));
  }

  // Vertical crosshair across both panels of the time charts
  const SPIKE = { showspikes: true, spikemode: 'across', spikesnap: 'cursor', spikethickness: 1, spikedash: 'dot', spikecolor: '#888' };
  function vline(x, c, dash) {
    return { type: 'line', xref: 'x', yref: 'paper', x0: x, x1: x, y0: 0, y1: 1, line: { color: c.ink2, width: 1, dash: dash || 'dot' } };
  }

  function courseTraces(d, c) {
    const out = [];
    d.course.forEach(el => {
      if (el.pts.length === 2) {
        out.push({ x: el.pts.map(p => p[0]), y: el.pts.map(p => p[1]), mode: 'lines', line: { color: c.ink, width: 3 },
          hovertemplate: el.type.replace('Line', ' line') + '<extra></extra>' });
      } else {
        out.push({ x: [el.pts[0][0]], y: [el.pts[0][1]], mode: 'markers', marker: { symbol: 'triangle-up', size: 12, color: c.ink },
          hovertemplate: 'Mark<extra></extra>' });
      }
    });
    return out;
  }
  function lineEnds(d) {
    const line = d.course.find(e => e.type === 'StartLine');
    if (!line) return [];
    const [a, b] = line.pts[0][0] < line.pts[1][0] ? line.pts : [line.pts[1], line.pts[0]];
    return [
      { x: a[0], y: a[1], text: 'pin', showarrow: false, xanchor: 'right', xshift: -6 },
      { x: b[0], y: b[1], text: 'boat', showarrow: false, xanchor: 'left', xshift: 6 },
    ];
  }

  const CHARTS = {
    // Approach from 5 minutes: map coloured by time to the gun
    startMap(el, d, c) {
      const s = d.series, { idx, g } = pick(d, (i, t) => t >= -300 && t <= 30);
      const pre = idx.filter(i => s.t[i] <= 0), post = idx.filter(i => s.t[i] > 0);
      const hov = i => s.hover[i] + '<br>Behind line ' + f(s.below ? s.below[i] : null, 1, ' m');
      const marks = [-300, -240, -180, -120, -60, -30]
        .map(t => ({ t, i: d.idxAt(t) })).filter(o => Math.abs(s.t[o.i] - o.t) < 2).map(o => o.i);
      const gun = d.idxAt(0);
      const traces = [
        { x: g(s.x), y: g(s.y), mode: 'lines', line: { color: c.line, width: 1 }, hoverinfo: 'skip' },
        { x: pre.map(i => s.x[i]), y: pre.map(i => s.y[i]), mode: 'markers', text: pre.map(hov), hovertemplate: '%{text}<extra></extra>',
          marker: { size: 5, color: pre.map(i => s.t[i]), colorscale: c.seq, cmin: -300, cmax: 0, showscale: !narrow(),
            colorbar: { title: { text: 'to gun', side: 'right' }, tickvals: [-300, -240, -180, -120, -60, 0],
              ticktext: ['5:00', '4:00', '3:00', '2:00', '1:00', 'gun'], thickness: 10, len: 0.8, outlinewidth: 0 } } },
        { x: post.map(i => s.x[i]), y: post.map(i => s.y[i]), mode: 'markers', marker: { size: 4, color: c.ink2 },
          text: post.map(hov), hovertemplate: '%{text}<extra></extra>' },
        ...courseTraces(d, c),
        { x: marks.map(i => s.x[i]), y: marks.map(i => s.y[i]), mode: 'markers+text', text: marks.map(i => clock(s.t[i]).replace(' to gun', '')),
          textposition: 'top right', textfont: { size: 10, color: c.ink2 }, marker: { size: 6, color: c.ink2 }, hoverinfo: 'skip' },
        { x: [s.x[gun]], y: [s.y[gun]], mode: 'markers+text', text: ['gun'], textposition: 'bottom right',
          marker: { size: 12, color: c.orange, line: { color: c.card, width: 2 } }, hovertext: [hov(gun)],
          hovertemplate: '%{hovertext}<extra></extra>' },
      ];
      const line = d.course.find(e => e.type === 'StartLine');
      const xs = idx.map(i => s.x[i]).concat(line ? line.pts.map(p => p[0]) : []).filter(v => v !== null);
      const ys = idx.map(i => s.y[i]).concat(line ? line.pts.map(p => p[1]) : []).filter(v => v !== null);
      const pad = (lo, hi) => [lo - 0.08 * (hi - lo) - 10, hi + 0.08 * (hi - lo) + 10];
      const xr = pad(Math.min(...xs), Math.max(...xs)), yr = pad(Math.min(...ys), Math.max(...ys));
      Plotly.newPlot(el, traces, layout(c, {
        title: title(c, 'Approach from 5:00 (hover for time to gun, speed, VMG, heading)', 'Approach from 5:00'),
        xaxis: axis(c, { title: 'metres (upwind is up)', zeroline: false, range: xr }),
        yaxis: axis(c, { scaleanchor: 'x', zeroline: false, range: yr }), annotations: lineEnds(d).map(a => Object.assign(a, { font: { color: c.ink } })),
        hovermode: 'closest', height: mapHeight(el, xr, yr, 300, 520),
      }), CONFIG);
    },

    // Speed and distance behind the line: last 2 minutes shown, zoom out to 5
    startTime(el, d, c) {
      const s = d.series, { idx, g } = pick(d, (i, t) => t >= -300 && t <= 60);
      const below = i => (s.below ? s.below[i] : null);
      Plotly.newPlot(el, [
        { x: g(s.t), y: g(s.sog), mode: 'lines', line: { color: c.blue, width: 2 },
          text: idx.map(i => s.hover[i] + '<br>Behind line ' + f(below(i), 1, ' m')),
          hovertemplate: '%{text}<extra></extra>' },
        { x: g(s.t), y: idx.map(below), yaxis: 'y2', mode: 'lines', line: { color: c.blue, width: 2 },
          text: idx.map(i => clock(s.t[i])), hovertemplate: '%{text}: %{y:.1f} m behind the line<extra></extra>' },
      ], layout(c, {
        title: title(c, 'Speed and distance behind the line (zoom out for the full 5 minutes)', 'Last 2 minutes'),
        xaxis: axis(c, { title: 'seconds from gun', range: [-120, 30], anchor: 'y2', ...SPIKE }),
        yaxis: axis(c, { title: 'SOG (kt)', domain: [0.55, 1] }),
        yaxis2: axis(c, { title: 'm behind line', domain: [0, 0.45], zeroline: true, zerolinecolor: c.ink2 }),
        shapes: [vline(0, c, 'dash')], hovermode: 'x', height: 440,
      }), CONFIG);
    },

    // Whole race track coloured by speed, tacks and gybes marked
    track(el, d, c) {
      const s = d.series, { idx, g } = pick(d, i => s.leg[i] !== null);
      const sogs = g(s.sog).filter(v => v !== null).sort((a, b) => a - b);
      const q = p => sogs[Math.floor(p * (sogs.length - 1))];
      const man = d.maneuvers.map(m => ({ m, i: d.idxAt(m.time_s) }));
      const tx = g(s.x).filter(v => v !== null), ty = g(s.y).filter(v => v !== null);
      const txr = [Math.min(...tx) - 60, Math.max(...tx) + 60], tyr = [Math.min(...ty) - 60, Math.max(...ty) + 60];
      Plotly.newPlot(el, [
        { x: g(s.x), y: g(s.y), mode: 'lines', line: { color: c.line, width: 1 }, hoverinfo: 'skip' },
        { x: g(s.x), y: g(s.y), mode: 'markers', text: idx.map(i => s.hover[i] + '<br>Leg ' + s.leg[i] + ' ' + (d.legType[s.leg[i]] || '')),
          hovertemplate: '%{text}<extra></extra>',
          marker: { size: 4, color: g(s.sog), colorscale: c.seq, cmin: q(0.05), cmax: q(0.95), showscale: !narrow(),
            colorbar: { title: { text: 'SOG kt', side: 'right' }, thickness: 10, len: 0.6, outlinewidth: 0 } } },
        ...courseTraces(d, c),
        { x: man.map(o => s.x[o.i]), y: man.map(o => s.y[o.i]), mode: 'markers',
          marker: { size: 9, color: 'rgba(0,0,0,0)', line: { width: 1.5, color: man.map(o => (o.m.kind === 'Gybe' ? c.orange : c.ink2)) } },
          text: man.map(o => maneuverText(o.m)), hovertemplate: '%{text}<extra></extra>' },
      ], layout(c, {
        title: title(c, 'Track (circles = tacks, orange = gybes)', 'Track'),
        xaxis: axis(c, { title: 'metres (upwind is up)', zeroline: false }),
        yaxis: axis(c, { scaleanchor: 'x', zeroline: false }),
        annotations: lineEnds(d).map(a => Object.assign(a, { font: { color: c.ink } })),
        hovermode: 'closest', height: mapHeight(el, txr, tyr, 380, 720),
      }), CONFIG);
    },

    // Speed and heel through the race, upwind legs shaded
    timeline(el, d, c) {
      const s = d.series, { g } = pick(d, i => s.leg[i] !== null);
      const shapes = d.legs.filter(l => l.type === 'upwind').map(l => ({
        type: 'rect', xref: 'x', yref: 'paper', x0: l.start_s / 60, x1: (l.start_s + l.duration_s) / 60, y0: 0, y1: 1,
        fillcolor: c.line, opacity: 0.35, line: { width: 0 }, layer: 'below' }));
      d.maneuvers.forEach(m => shapes.push(Object.assign(vline(m.time_s / 60, c), { line: { color: c.line, width: 1 } })));
      const ann = d.legs.map(l => ({ x: l.start_s / 60, y: 1, xref: 'x', yref: 'paper', xanchor: 'left', yanchor: 'top',
        text: 'Leg ' + l.leg + ' ' + l.type, showarrow: false, font: { size: 11, color: c.ink2 } }));
      Plotly.newPlot(el, [
        { x: g(s.min), y: g(s.sog5), mode: 'lines', line: { color: c.blue, width: 1.5 }, text: g(s.hover), hovertemplate: '%{text}<extra></extra>' },
        { x: g(s.min), y: g(s.heel5), yaxis: 'y2', mode: 'lines', line: { color: c.blue, width: 1.5 }, hovertemplate: 'Heel (5 s avg) %{y:.0f}°<extra></extra>' },
      ], layout(c, {
        title: title(c, 'Speed and heel (shaded = upwind legs, lines = maneuvers)', 'Speed and heel'),
        xaxis: axis(c, { title: 'minutes from gun', anchor: 'y2', ...SPIKE }),
        yaxis: axis(c, { title: 'SOG (kt, 5 s avg)', domain: [0.42, 1] }),
        yaxis2: axis(c, { title: '|heel| (°)', domain: [0, 0.34] }),
        shapes, annotations: ann, hovermode: 'x', height: 460,
      }), CONFIG);
    },

    // Wind shift upwind (from headings), by tack, with each tack's call
    shifts(el, d, c) {
      const s = d.series;
      if (!s.shift || !d.beats.length) { el.remove(); return; }
      const beatLegs = new Set(d.beats.map(b => b.leg));
      const { idx, g } = pick(d, i => beatLegs.has(s.leg[i]));
      const side = k => idx.map(i => (s.tack[i] === k ? s.shift[i] : null));
      const hov = idx.map(i => s.hover[i] + '<br>Shift ' + f(s.shift[i], 1, '°') + ' (+ = right)');
      const tacks = d.maneuvers.filter(m => m.kind === 'Tack' && beatLegs.has(m.leg));
      const label = { header: 'header', lift: 'lift', no_shift: '–', layline: 'layline', start: 'start', double: 'double', mark: 'mark' };
      Plotly.newPlot(el, [
        { x: g(s.min), y: side('s'), mode: 'lines', name: 'on starboard', line: { color: c.blue, width: 2 }, text: hov, hovertemplate: '%{text}<extra>starboard</extra>' },
        { x: g(s.min), y: side('p'), mode: 'lines', name: 'on port', line: { color: c.orange, width: 2 }, text: hov, hovertemplate: '%{text}<extra>port</extra>' },
      ], layout(c, {
        title: title(c, 'Wind shifts upwind (+ = right shift: starboard lifted, port headed)', 'Wind shifts (+ = right)'),
        xaxis: axis(c, { title: 'minutes from gun' }), yaxis: axis(c, { title: 'shift (°)', zeroline: true, zerolinecolor: c.ink2 }),
        shapes: tacks.map(m => vline(m.time_s / 60, c)),
        annotations: tacks.map((m, k) => ({ x: m.time_s / 60, y: 1, xref: 'x', yref: 'paper', yanchor: 'bottom', showarrow: false,
          yshift: k % 2 ? -14 : 0, text: label[m.kind_call] || '', hovertext: maneuverText(m),
          font: { size: 10, color: c.ink, weight: ['header', 'lift'].includes(m.kind_call) ? 700 : 400 } })),
        showlegend: true, legend: { orientation: 'h', y: -0.25 }, hovermode: 'closest', height: 380,
      }), CONFIG);
    },

    // Speed down each run by gybe
    downwind(el, d, c) {
      const s = d.series;
      const runs = new Set(d.legs.filter(l => l.type === 'downwind').map(l => l.leg));
      if (!runs.size) { el.remove(); return; }
      const { idx, g } = pick(d, i => runs.has(s.leg[i]));
      const side = k => idx.map(i => (s.tack[i] === k ? s.sog5[i] : null));
      const hov = idx.map(i => s.hover[i] + '<br>Angle to wind ' + f(s.twa[i], 0, '°'));
      const gybes = d.maneuvers.filter(m => m.kind === 'Gybe');
      Plotly.newPlot(el, [
        { x: g(s.min), y: side('s'), mode: 'lines', name: 'on starboard', line: { color: c.blue, width: 2 }, text: hov, hovertemplate: '%{text}<extra>starboard</extra>' },
        { x: g(s.min), y: side('p'), mode: 'lines', name: 'on port', line: { color: c.orange, width: 2 }, text: hov, hovertemplate: '%{text}<extra>port</extra>' },
      ], layout(c, {
        title: title(c, 'Downwind speed (5 s avg), dotted = gybes', 'Downwind speed'),
        xaxis: axis(c, { title: 'minutes from gun' }), yaxis: axis(c, { title: 'SOG (kt)' }),
        shapes: gybes.map(m => vline(m.time_s / 60, c)),
        annotations: gybes.map(m => ({ x: m.time_s / 60, y: 1, xref: 'x', yref: 'paper', yanchor: 'bottom', showarrow: false,
          text: 'gybe ' + (m.distance_lost_m === null ? '' : (m.distance_lost_m >= 0 ? '+' : '') + m.distance_lost_m.toFixed(0) + ' m'),
          font: { size: 10, color: c.ink } })),
        showlegend: true, legend: { orientation: 'h', y: -0.25 }, hovermode: 'closest', height: 340,
      }), CONFIG);
    },

    // Metres lost per tack/gybe, in race order
    maneuvers(el, d, c) {
      const ms = d.maneuvers.filter(m => m.distance_lost_m !== null && !m.note);
      if (!ms.length) { el.remove(); return; }
      Plotly.newPlot(el, [{
        type: 'bar', x: ms.map(m => clock(m.time_s)), y: ms.map(m => m.distance_lost_m),
        marker: { color: ms.map(m => (m.kind === 'Gybe' ? c.orange : c.blue)) },
        text: ms.map(maneuverText), textposition: 'none', hovertemplate: '%{text}<extra></extra>',
      }], layout(c, {
        title: title(c, 'Metres lost per maneuver vs. VMG before it (blue = tack, orange = gybe)', 'Metres lost per maneuver'),
        xaxis: axis(c, { title: 'time from gun', type: 'category', tickangle: -60 }),
        yaxis: axis(c, { title: 'm lost', zeroline: true, zerolinecolor: c.ink2 }), bargap: 0.3, height: 340,
      }), CONFIG);
    },
  };

  function maneuverText(m) {
    return '<b>' + m.kind + ' onto ' + (m.onto || '?') + '</b> ' + clock(m.time_s) + '<br>Entry ' + f(m.entry_sog, 2, ' kt') +
      ', min ' + f(m.min_sog, 2, ' kt') + ' (' + (m.speed_loss_pct === null ? '–' : m.speed_loss_pct + '%') + ' loss)' +
      '<br>Recovered in ' + (m.recovery_s === null ? '–' : m.recovery_s + ' s') + ', ' + f(m.distance_lost_m, 1, ' m') + ' lost' +
      (m.call ? '<br>' + m.call : '') + (m.note ? '<br>' + m.note : '');
  }

  function render(root) {
    if (!window.Plotly) return;
    const c = colors();
    root.querySelectorAll('.chart:not([data-done])').forEach(el => {
      el.dataset.done = '1';
      try { CHARTS[el.dataset.chart](el, race(el.dataset.race), c); }
      catch (e) { el.textContent = 'Chart failed: ' + e.message; }
    });
  }
  window.renderCharts = render;
  window.addEventListener('beforeprint', () => render(document));
})();
