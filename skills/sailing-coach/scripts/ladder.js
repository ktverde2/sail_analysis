// Laylines and ladder rungs on course maps, with a toggle button per chart (ladder.py makes the
// geometry: each leg's wind axis and half tacking/gybing angle from the boats' GPS tracks).
// LADDER.traces(lad, c) -> hidden traces tagged 'ladder'; LADDER.button(traces, c) -> the
// updatemenus entry that shows and hides them; LADDER.toGo(x, y, lad) mirrors ladder.to_go.
window.LADDER = (function () {
  const rad = d => (d * Math.PI) / 180;

  function toGo(x, y, lad) {
    if (!lad || !lad.targets || !lad.targets.length) return null;
    const u = rad(lad.up_deg), tx = Math.sin(u), ty = Math.cos(u), nx = Math.cos(u), ny = -Math.sin(u);
    const k = 1 / Math.cos(rad(lad.half_deg));
    let best = Infinity;
    for (const [px, py] of lad.targets) {
      const dx = px - x, dy = py - y, a = dx * tx + dy * ty, c = dx * nx + dy * ny, d = Math.hypot(a, c);
      best = Math.min(best, a > 0 ? Math.max(a * k, d) : d);
    }
    return best;
  }

  // Rungs every `step` metres (ladder.py draws them every 100 m), the same geometry as
  // ladder.rungs. focus [x, y]: put each label on its rung as near that point as it can be
  // (the start chart is zoomed on the line, far from the left layline where labels go).
  function rungsAt(lad, step, focus) {
    const u = rad(lad.up_deg), tx = Math.sin(u), ty = Math.cos(u), nx = Math.cos(u), ny = -Math.sin(u);
    const t = Math.tan(rad(lad.half_deg)), k = 1 / Math.cos(rad(lad.half_deg));
    const ends = [lad.targets[0], lad.targets[lad.targets.length - 1]]
      .map(p => [p[0] * tx + p[1] * ty, p[0] * nx + p[1] * ny]).sort((a, b) => a[1] - b[1]);
    const [[al, cl], [ar, cr]] = ends, a0 = Math.min(al, ar);
    const leftAt = L => cl - (al - L) * t, rightAt = L => cr + (ar - L) * t;
    const xy = (A, C) => [A * tx + C * nx, A * ty + C * ny];
    const reach = Math.max(0, ...lad.labels.map(l => l.rung_m)) + 99;  // as far as ladder.py drew them
    const fc = focus ? focus[0] * nx + focus[1] * ny : null;
    const r1 = v => Math.round(v * 10) / 10;
    const out = { rungs: { x: [], y: [] }, labels: [] };
    for (let a = step; a <= reach; a += step) {
      const L = a0 - a, lo = leftAt(L), hi = rightAt(L);
      const [x0, y0] = xy(L, lo), [x1, y1] = xy(L, hi);
      out.rungs.x.push(r1(x0), r1(x1), null);
      out.rungs.y.push(r1(y0), r1(y1), null);
      const [lx, ly] = fc === null ? [x0, y0] : xy(L, Math.min(hi, Math.max(lo, fc)));
      out.labels.push({ x: r1(lx), y: r1(ly), rung_m: a, to_go_m: Math.round(a * k) });
    }
    return out;
  }

  // c: {ink, ink2, line}; name: shown in hovers ("Beat 2", ...); opt: {step, focus} for finer
  // rungs (labelled every 100 m)
  function traces(lad, c, name, visible, opt) {
    if (!lad || !lad.layline) return [];
    const who = name ? name + ': ' : '';
    const fine = opt && opt.step && lad.targets && lad.targets.length ? rungsAt(lad, opt.step, opt.focus) : null;
    const rungs = fine ? fine.rungs : lad.rungs, labels = fine ? fine.labels : lad.labels;
    const every = fine ? 100 : labels.length > 12 ? 500 : 200;
    const lab = labels.filter(l => l.rung_m % every === 0);
    return [
      { x: rungs.x, y: rungs.y, mode: 'lines', line: { color: c.ink2, width: 0.8 }, opacity: 0.35,
        hoverinfo: 'skip', showlegend: false, visible: !!visible, meta: 'ladder' },
      { x: lad.layline.x, y: lad.layline.y, mode: 'lines', line: { color: c.ink2, width: 1.5, dash: 'dash' },
        hovertemplate: who + 'layline (' + (2 * lad.half_deg).toFixed(0) + '° ' + (lad.type === 'upwind' ? 'tacking' : 'gybing') +
          ' angle over the ground)<extra></extra>', showlegend: false, visible: !!visible, meta: 'ladder' },
      { x: lab.map(l => l.x), y: lab.map(l => l.y), mode: 'text', text: lab.map(l => l.rung_m + ' m'),
        textposition: fine && opt.focus ? 'top center' : 'middle left', textfont: { size: 10, color: c.ink2 },
        hovertemplate: lab.map(l => who + 'rung ' + l.rung_m + ' m from the mark: ' + l.to_go_m + ' m to sail<extra></extra>'),
        showlegend: false, visible: !!visible, meta: 'ladder' },
    ];
  }

  // One toggle button, top left inside the plot, for every trace tagged 'ladder'
  function button(all, c, label) {
    const idx = all.map((t, i) => (t.meta === 'ladder' ? i : -1)).filter(i => i >= 0);
    if (!idx.length) return [];
    return [{
      // active -1: the button starts off, so the first click shows the laylines and rungs
      type: 'buttons', direction: 'right', showactive: false, active: -1,
      // inside the plot's top-left corner: clear of the title and of Plotly's toolbar (top right)
      x: 0, xanchor: 'left', y: 1.0, yanchor: 'top',
      pad: { t: 2, b: 2, l: 0, r: 0 }, bgcolor: c.card || 'rgba(0,0,0,0)', bordercolor: c.line, borderwidth: 1,
      font: { size: 11, color: c.ink },
      buttons: [{ label: label || 'Laylines & rungs', method: 'restyle', args: [{ visible: true }, idx], args2: [{ visible: false }, idx] }],
    }];
  }

  return { toGo, traces, button, rungsAt };
})();
