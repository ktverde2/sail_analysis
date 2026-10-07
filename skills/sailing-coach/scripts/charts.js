// Interactive race charts for report.html (Plotly basic bundle, inlined by html_report.py or loaded from jsDelivr with --cdn).
// Each .chart element (data-chart = chart kind, data-race = race folder) is drawn the first time
// its page is shown. Race data comes from the report's one <script type="application/json" id="report-data">
// block (races: each race folder's plotdata.json from analyze.py, 1 Hz series in metres, upwind up,
// t = seconds from the gun; overlays: the tack and gybe overlays, read by their own script).
function reportData() {
  return reportData.d || (reportData.d = JSON.parse(document.getElementById('report-data').textContent));
}
(function () {
  const DATA = {};
  function race(id) {
    if (!DATA[id]) DATA[id] = prep(reportData().races[id]);
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
  const BOAT_LENGTH_M = 9.3;  // Etchells LOA: distances gained or lost are also given in lengths
  const ZONE_M = 3 * BOAT_LENGTH_M;  // RRS zone: three hull lengths
  function lens(m) { const x = Math.abs(m) / BOAT_LENGTH_M; return Math.round(x * 10) / 10 < 10 ? x.toFixed(1) : x.toFixed(0); }
  // '12.5 m (1.3 lengths)'
  function mL(v, n) { return v === null || v === undefined || isNaN(v) ? '–' : Number(v).toFixed(n || 0) + ' m (' + lens(v) + ' lengths)'; }
  function f(v, n, unit) {
    return v === null || v === undefined ? '–' : v.toFixed(n) + (unit || '');
  }

  function prep(d) {
    const s = d.series, n = s.t.length;
    s.heelAbs = s.heel ? s.heel.map(v => (v === null ? null : Math.abs(v))) : new Array(n).fill(null);
    s.min = s.t.map(t => (t === null ? null : t / 60));
    s.sog5 = smooth(s.sog, 5);
    s.heel5 = smooth(s.heelAbs, 5);
    // One hover block per point: time, speed, VMC (speed toward the next mark), heading, heel
    const P = d.progress || 'VMG';
    s.hover = s.t.map((t, i) =>
      '<b>' + clock(t) + '</b><br>SOG ' + f(s.sog[i], 2, ' kt') + '<br>' + P + ' ' + f(s.vmg[i], 2, ' kt') +
      '<br>Heading ' + f(s.hdg[i], 0, d.north === 'magnetic' ? '° mag' : '°') + '<br>Heel ' + f(s.heelAbs[i], 0, '°'));
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
  // Laylines and rungs (ladder.js): one leg's, or every distinct mark's for the whole race
  function ladderFor(d, c, legs) {
    if (!window.LADDER || !d.ladders) return [];
    const seen = [], out = [];
    for (const leg of legs) {
      const lad = d.ladders[leg - 1];
      if (!lad || !lad.targets || !lad.targets.length) continue;
      const cx = lad.targets.reduce((a, p) => a + p[0], 0) / lad.targets.length;
      const cy = lad.targets.reduce((a, p) => a + p[1], 0) / lad.targets.length;
      if (seen.some(([x, y]) => Math.hypot(x - cx, y - cy) < 30)) continue;
      seen.push([cx, cy]);
      out.push(...LADDER.traces(lad, c, 'Leg ' + leg + ' ' + (d.legType[leg] || '')));
    }
    return out;
  }
  function withLadder(traces, lay, c) {
    lay.updatemenus = window.LADDER ? LADDER.button(traces, c) : [];
    return lay;
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
      const hov = i => s.hover[i] + '<br>Behind line ' + mL(s.below ? s.below[i] : null, 1);
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
          text: idx.map(i => s.hover[i] + '<br>Behind line ' + mL(below(i), 1)),
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
      const tr = [
        { x: g(s.x), y: g(s.y), mode: 'lines', line: { color: c.line, width: 1 }, hoverinfo: 'skip' },
        { x: g(s.x), y: g(s.y), mode: 'markers', text: idx.map(i => s.hover[i] + '<br>Leg ' + s.leg[i] + ' ' + (d.legType[s.leg[i]] || '')),
          hovertemplate: '%{text}<extra></extra>',
          marker: { size: 4, color: g(s.sog), colorscale: c.seq, cmin: q(0.05), cmax: q(0.95), showscale: !narrow(),
            colorbar: { title: { text: 'SOG kt', side: 'right' }, thickness: 10, len: 0.6, outlinewidth: 0 } } },
        ...courseTraces(d, c),
        { x: man.map(o => s.x[o.i]), y: man.map(o => s.y[o.i]), mode: 'markers',
          marker: { size: 9, color: 'rgba(0,0,0,0)', line: { width: 1.5, color: man.map(o => (o.m.kind === 'Gybe' ? c.orange : c.ink2)) } },
          text: man.map(o => maneuverText(o.m)), hovertemplate: '%{text}<extra></extra>' },
        ...ladderFor(d, c, d.legs.map(l => l.leg)),
      ];
      Plotly.newPlot(el, tr, withLadder(tr, layout(c, {
        title: title(c, 'Track (circles = tacks, orange = gybes)', 'Track'),
        xaxis: axis(c, { title: 'metres (upwind is up)', zeroline: false, range: txr }),
        yaxis: axis(c, { scaleanchor: 'x', zeroline: false, range: tyr }),
        annotations: lineEnds(d).map(a => Object.assign(a, { font: { color: c.ink } })),
        hovermode: 'closest', height: mapHeight(el, txr, tyr, 380, 720),
      }), c), CONFIG);
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
          text: 'gybe ' + (m.distance_lost_m === null ? '' : (m.distance_lost_m >= 0 ? '+' : '') + m.distance_lost_m.toFixed(0) + ' m, ' + lens(m.distance_lost_m) + ' L'),
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
        title: title(c, 'Metres lost toward the mark per maneuver, vs. ' + (d.progress || 'VMG') + ' before it (blue = tack, orange = gybe)', 'Metres lost per maneuver'),
        xaxis: axis(c, { title: 'time from gun', type: 'category', tickangle: -60 }),
        yaxis: axis(c, { title: 'm lost', zeroline: true, zerolinecolor: c.ink2 }), bargap: 0.3, height: 340,
      }), CONFIG);
    },
  };

  function relClock(t) {
    const a = Math.abs(Math.round(t));
    return (t < 0 ? '−' : '+') + Math.floor(a / 60) + ':' + String(a % 60).padStart(2, '0');
  }
  function fit(xs, ys, pad) {
    xs = xs.filter(v => v !== null); ys = ys.filter(v => v !== null);
    return [[Math.min(...xs) - pad, Math.max(...xs) + pad], [Math.min(...ys) - pad, Math.max(...ys) + pad]];
  }

  Object.assign(CHARTS, {
    // One leg's track, coloured by speed, with its tacks or gybes
    legTrack(el, d, c) {
      const s = d.series, leg = +el.dataset.leg;
      const { idx, g } = pick(d, i => s.leg[i] === leg);
      const sogs = g(s.sog).filter(v => v !== null).sort((a, b) => a - b);
      const q = p => sogs[Math.floor(p * (sogs.length - 1))];
      const man = d.maneuvers.filter(m => m.leg === leg).map(m => ({ m, i: d.idxAt(m.time_s) }));
      const [xr, yr] = fit(g(s.x), g(s.y), 40);
      const tr = [
        { x: g(s.x), y: g(s.y), mode: 'lines', line: { color: c.line, width: 1 }, hoverinfo: 'skip' },
        { x: g(s.x), y: g(s.y), mode: 'markers', text: idx.map(i => s.hover[i]), hovertemplate: '%{text}<extra></extra>',
          marker: { size: 5, color: g(s.sog), colorscale: c.seq, cmin: q(0.05), cmax: q(0.95), showscale: !narrow(),
            colorbar: { title: { text: 'SOG kt', side: 'right' }, thickness: 10, len: 0.7, outlinewidth: 0 } } },
        ...courseTraces(d, c),
        { x: man.map(o => s.x[o.i]), y: man.map(o => s.y[o.i]), mode: 'markers',
          marker: { size: 10, color: 'rgba(0,0,0,0)', line: { width: 1.5, color: man.map(o => (o.m.kind === 'Gybe' ? c.orange : c.ink2)) } },
          text: man.map(o => maneuverText(o.m)), hovertemplate: '%{text}<extra></extra>' },
        ...ladderFor(d, c, [leg]),
      ];
      Plotly.newPlot(el, tr, withLadder(tr, layout(c, {
        title: title(c, 'Leg ' + leg + ' ' + (d.legType[leg] || '') + ' track (circles = ' +
          (d.legType[leg] === 'upwind' ? 'tacks' : 'gybes') + ')', 'Leg ' + leg + ' track'),
        xaxis: axis(c, { title: 'metres (upwind is up)', zeroline: false, range: xr }),
        yaxis: axis(c, { scaleanchor: 'x', zeroline: false, range: yr }),
        hovermode: 'closest', height: mapHeight(el, xr, yr, 320, 560),
      }), c), CONFIG);
    },

    // Speed vs. wind angle on one beat: starboard right, port left; height = upwind VMG
    polar(el, d, c) {
      const s = d.series, leg = +el.dataset.leg;
      const keep = i => s.leg[i] === leg && s.steady[i] && s.sog[i] > 2 && s.twa[i] !== null && s.twa[i] <= 60;
      const traces = [];
      let top = 6;
      // grid: speed rings and wind-angle spokes
      for (let r = 3; r <= 8; r++) {
        const th = [...Array(71).keys()].map(k => ((k - 35) * 2 * Math.PI) / 180);
        traces.push({ x: th.map(a => r * Math.sin(a)), y: th.map(a => r * Math.cos(a)), mode: 'lines',
          line: { color: c.line, width: 1 }, hoverinfo: 'skip' });
      }
      [30, 40, 50, 60].forEach(deg => [1, -1].forEach(sg => {
        const a = (deg * Math.PI) / 180;
        traces.push({ x: [0, sg * 8 * Math.sin(a)], y: [0, 8 * Math.cos(a)], mode: 'lines+text', text: ['', deg + '°'],
          textposition: 'top center', textfont: { size: 10, color: c.ink2 }, line: { color: c.line, width: 1 }, hoverinfo: 'skip' });
      }));
      const deep = dark() ? { s: '#9ec5f4', p: '#f4a582' } : { s: '#0d366b', p: '#8a2e0b' };
      [['s', 1, c.blue, 'starboard'], ['p', -1, c.orange, 'port']].forEach(([k, sg, col, name]) => {
        const idx = s.t.map((_, i) => i).filter(i => keep(i) && s.tack[i] === k);
        const rad = i => (s.twa[i] * Math.PI) / 180;
        idx.forEach(i => (top = Math.max(top, s.sog[i])));
        traces.push({ x: idx.map(i => sg * s.sog[i] * Math.sin(rad(i))), y: idx.map(i => s.sog[i] * Math.cos(rad(i))),
          mode: 'markers', name: name, marker: { size: 4, color: col, opacity: 0.35 },
          text: idx.map(i => s.hover[i] + '<br>Wind angle ' + f(s.twa[i], 0, '°')), hovertemplate: '%{text}<extra>' + name + '</extra>' });
        const bins = {};
        idx.forEach(i => { const b = Math.floor(s.twa[i] / 2) * 2; (bins[b] = bins[b] || []).push(s.sog[i]); });
        const keys = Object.keys(bins).map(Number).filter(b => bins[b].length >= 10).sort((a, b) => a - b);
        const avg = b => bins[b].reduce((x, y) => x + y, 0) / bins[b].length;
        traces.push({ x: keys.map(b => sg * avg(b) * Math.sin(((b + 1) * Math.PI) / 180)),
          y: keys.map(b => avg(b) * Math.cos(((b + 1) * Math.PI) / 180)), mode: 'lines+markers', name: name + ' average',
          line: { color: deep[k], width: 3 }, marker: { size: 5, color: deep[k] },
          text: keys.map(b => name + ' ' + b + '–' + (b + 2) + '°: ' + avg(b).toFixed(2) + ' kt avg (' + bins[b].length + ' s)'),
          hovertemplate: '%{text}<extra></extra>' });
      });
      d.targets.forEach(tg => [1, -1].forEach(sg => {
        const a = (tg.twa_tgt * Math.PI) / 180;
        traces.push({ x: [sg * tg.speed_tgt * Math.sin(a)], y: [tg.speed_tgt * Math.cos(a)], mode: 'markers',
          marker: { symbol: 'diamond', size: 9, color: c.ink },
          hovertemplate: 'Card at ' + tg.wind + ': ' + tg.speed_tgt + ' kt at ' + tg.twa_tgt + '°, heel ' + tg.heel_tgt + '°<extra></extra>' });
      }));
      // Fit to the data (and the card's diamonds), keeping a little room for the angle labels
      const pts = traces.filter(t => t.mode === 'markers' || t.mode === 'lines+markers').flatMap(t => t.x.map((x, k) => [x, t.y[k]]));
      const R = Math.ceil(top + 0.3);
      const mx = Math.max(1.5, ...pts.map(p => Math.abs(p[0]))) + 0.9;
      const xr = [-mx, mx], yr = [Math.max(0, Math.min(...pts.map(p => p[1])) - 0.8), Math.max(...pts.map(p => p[1])) + 1.0];
      Plotly.newPlot(el, traces, layout(c, {
        title: title(c, 'Leg ' + leg + ' polar: port ← wind angle → starboard (◆ = card)', 'Leg ' + leg + ' polar'),
        xaxis: axis(c, { range: xr, showgrid: false, zeroline: false, showticklabels: false }),
        yaxis: axis(c, { range: yr, scaleanchor: 'x', title: 'upwind VMG (kt)', showgrid: false, zeroline: false }),
        hovermode: 'closest', height: mapHeight(el, xr, yr, 300, 460),
      }), CONFIG);
    },

    // The rounding, zoomed on the mark: the zone (three hull lengths), when the boat entered it,
    // and the minute either side coloured by time from the rounding (a button widens the view)
    roundTrack(el, d, c) {
      const s = d.series, r = d.roundings.find(x => x.n === +el.dataset.n);
      const { idx, g } = pick(d, (i, t) => t >= r.time_s - 60 && t <= r.time_s + 60);
      const rel = i => s.t[i] - r.time_s;
      const wide = fit(g(s.x), g(s.y), 25);
      const at = d.idxAt(r.time_s);
      // the mark rounded: the course point nearest the boat at the rounding (a gate: both its marks get a zone)
      let mark = null, markEl = null;
      d.course.filter(e => e.type === 'Mark' || e.type === 'Gate').forEach(e => e.pts.forEach(p => {
        const dd = Math.hypot(p[0] - s.x[at], p[1] - s.y[at]);
        if (!mark || dd < mark.d) { mark = { x: p[0], y: p[1], d: dd }; markEl = e; }
      }));
      const shapes = [], ann = [];
      let zin = null;
      if (mark) {
        markEl.pts.forEach(p => shapes.push({ type: 'circle', xref: 'x', yref: 'y', x0: p[0] - ZONE_M, x1: p[0] + ZONE_M,
          y0: p[1] - ZONE_M, y1: p[1] + ZONE_M, line: { color: c.ink2 || c.line, width: 1, dash: 'dash' } }));
        ann.push({ x: mark.x, y: mark.y + ZONE_M, text: 'zone (3 lengths, ' + Math.round(ZONE_M) + ' m)', showarrow: false, yshift: 8,
          font: { size: 10, color: c.ink2 || c.line } });
        // zone entry: walk back from the rounding while the boat is inside the zone
        const inZone = i => s.x[i] !== null && Math.hypot(s.x[i] - mark.x, s.y[i] - mark.y) <= ZONE_M;
        let k = idx.indexOf(at);
        if (k >= 0 && inZone(idx[k])) {
          while (k > 0 && inZone(idx[k - 1])) k -= 1;
          zin = idx[k];
        }
      }
      // zoomed view: the zone and the track from 15 s before zone entry to 20 s after the rounding
      let zoom = wide;
      if (mark) {
        const from = (zin !== null ? s.t[zin] : r.time_s - 20) - 15, to = r.time_s + 20;
        const zi = idx.filter(i => s.t[i] >= from && s.t[i] <= to && s.x[i] !== null);
        const xs = zi.map(i => s.x[i]).concat([mark.x - ZONE_M, mark.x + ZONE_M]);
        const ys = zi.map(i => s.y[i]).concat([mark.y - ZONE_M, mark.y + ZONE_M]);
        const cx = (Math.min(...xs) + Math.max(...xs)) / 2, cy = (Math.min(...ys) + Math.max(...ys)) / 2;
        const half = Math.max(Math.max(...xs) - Math.min(...xs), Math.max(...ys) - Math.min(...ys)) / 2 + 10;
        zoom = [[cx - half, cx + half], [cy - half, cy + half]];
      }
      const into = d.legs.reduce((b, l) => (Math.abs(l.start_s + l.duration_s - r.time_s) < Math.abs(b.start_s + b.duration_s - r.time_s) ? l : b), d.legs[0]);
      const tr = [
        { x: g(s.x), y: g(s.y), mode: 'lines', line: { color: c.line, width: 1 }, hoverinfo: 'skip' },
        { x: g(s.x), y: g(s.y), mode: 'markers', text: idx.map(i => s.hover[i] + '<br>Rounding ' + relClock(rel(i))),
          hovertemplate: '%{text}<extra></extra>',
          marker: { size: 7, color: idx.map(rel), colorscale: c.seq, cmin: -60, cmax: 60, showscale: !narrow(),
            colorbar: { title: { text: 's from rounding', side: 'right' }, thickness: 10, len: 0.7, outlinewidth: 0 } } },
        ...courseTraces(d, c),
        { x: [s.x[at]], y: [s.y[at]], mode: 'markers', marker: { size: 12, color: c.orange, line: { color: c.card, width: 2 } },
          text: [s.hover[at]], hovertemplate: 'Rounding<br>%{text}<extra></extra>' },
      ];
      if (zin !== null) tr.push({ x: [s.x[zin]], y: [s.y[zin]], mode: 'markers', marker: { size: 13, symbol: 'circle-open', color: c.ink, line: { width: 2 } },
        text: [s.hover[zin] + '<br>' + relClock(rel(zin)) + ' before the rounding'], hovertemplate: 'Into the zone<br>%{text}<extra></extra>' });
      tr.push(...ladderFor(d, c, into ? [into.leg] : []));
      const lay = withLadder(tr, layout(c, {
        title: title(c, 'At the mark' + (zin !== null ? ' · into the zone (○) ' + (r.time_s - s.t[zin]) + ' s before rounding' : ''), 'At the mark'),
        xaxis: axis(c, { title: 'metres (upwind is up)', zeroline: false, range: zoom[0] }),
        yaxis: axis(c, { scaleanchor: 'x', zeroline: false, range: zoom[1] }),
        shapes, annotations: ann, hovermode: 'closest', height: mapHeight(el, zoom[0], zoom[1], 300, 460),
      }), c);
      if (mark) lay.updatemenus = (lay.updatemenus || []).concat([{
        type: 'buttons', direction: 'right', showactive: false, active: -1,
        x: 1.0, xanchor: 'right', y: 1.0, yanchor: 'top',
        pad: { t: 2, b: 2, l: 0, r: 0 }, bgcolor: c.card || 'rgba(0,0,0,0)', bordercolor: c.line, borderwidth: 1,
        font: { size: 11, color: c.ink },
        buttons: [{ label: 'Wider view (±60 s)', method: 'relayout',
          args: [{ 'xaxis.range': wide[0], 'yaxis.range': wide[1] }], args2: [{ 'xaxis.range': zoom[0], 'yaxis.range': zoom[1] }] }],
      }]);
      Plotly.newPlot(el, tr, lay, CONFIG);
    },

    // Speed and VMG through a rounding, against the steady VMG of the legs either side
    roundSpeed(el, d, c) {
      const s = d.series, r = d.roundings.find(x => x.n === +el.dataset.n);
      const { idx, g } = pick(d, (i, t) => t >= r.time_s - 60 && t <= r.time_s + 90);
      const x = idx.map(i => s.t[i] - r.time_s);
      const smooth3 = arr => smooth(arr, 3);
      const shapes = [vline(0, c, 'dash')];
      if (r.vmg_before !== null) shapes.push({ type: 'line', x0: -60, x1: 0, y0: r.vmg_before, y1: r.vmg_before, line: { color: c.orange, width: 1, dash: 'dot' } });
      if (r.vmg_after !== null) shapes.push({ type: 'line', x0: 0, x1: 90, y0: r.vmg_after, y1: r.vmg_after, line: { color: c.orange, width: 1, dash: 'dot' } });
      Plotly.newPlot(el, [
        { x, y: smooth3(g(s.sog)), mode: 'lines', name: 'SOG', line: { color: c.blue, width: 2 },
          text: idx.map(i => s.hover[i] + '<br>Rounding ' + relClock(s.t[i] - r.time_s)), hovertemplate: '%{text}<extra>SOG</extra>' },
        { x, y: smooth3(g(s.vmg)), mode: 'lines', name: d.progress || 'VMG', line: { color: c.orange, width: 2 },
          hovertemplate: (d.progress || 'VMG') + ' %{y:.2f} kt<extra></extra>' },
      ], layout(c, {
        title: title(c, 'SOG and ' + (d.progress || 'VMG') + ' (dotted = steady ' + (d.progress || 'VMG') + ' of the legs either side)', 'SOG and ' + (d.progress || 'VMG')),
        xaxis: axis(c, { title: 'seconds from rounding', ...SPIKE }), yaxis: axis(c, { title: 'kt', rangemode: 'tozero' }),
        shapes, showlegend: true, legend: { orientation: 'h', y: -0.3 }, hovermode: 'x', height: 340,
      }), CONFIG);
    },
  });

  function maneuverText(m) {
    return '<b>' + m.kind + ' onto ' + (m.onto || '?') + '</b> ' + clock(m.time_s) + '<br>Entry ' + f(m.entry_sog, 2, ' kt') +
      ', min ' + f(m.min_sog, 2, ' kt') + ' (' + (m.speed_loss_pct === null ? '–' : m.speed_loss_pct + '%') + ' loss)' +
      '<br>Recovered in ' + (m.recovery_s === null ? '–' : m.recovery_s + ' s') + ', ' + mL(m.distance_lost_m, 1) + ' lost' +
      (m.call ? '<br>' + m.call : '') + (m.note ? '<br>' + m.note : '');
  }

  function render(root) {
    if (!window.Plotly) {
      root.querySelectorAll('.chart:not([data-done])').forEach(el => {
        el.textContent = 'Interactive chart needs an internet connection (the chart library loads online).';
      });
      return;
    }
    const c = colors();
    root.querySelectorAll('.chart:not([data-done])').forEach(el => {
      if (el.closest('details:not([open])')) return;  // drawn when its dropdown opens
      el.dataset.done = '1';
      try { CHARTS[el.dataset.chart](el, race(el.dataset.race), c); }
      catch (e) { el.textContent = 'Chart failed: ' + e.message; }
    });
  }
  window.renderCharts = render;
  window.addEventListener('beforeprint', () => render(document));
})();
