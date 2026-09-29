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

  // c: {ink, ink2, line}; name: shown in hovers ("Beat 2", ...)
  function traces(lad, c, name, visible) {
    if (!lad || !lad.layline) return [];
    const who = name ? name + ': ' : '';
    const every = lad.labels.length > 12 ? 500 : 200;
    const lab = lad.labels.filter(l => l.rung_m % every === 0);
    return [
      { x: lad.rungs.x, y: lad.rungs.y, mode: 'lines', line: { color: c.ink2, width: 0.8 }, opacity: 0.35,
        hoverinfo: 'skip', showlegend: false, visible: !!visible, meta: 'ladder' },
      { x: lad.layline.x, y: lad.layline.y, mode: 'lines', line: { color: c.ink2, width: 1.5, dash: 'dash' },
        hovertemplate: who + 'layline (' + (2 * lad.half_deg).toFixed(0) + '° ' + (lad.type === 'upwind' ? 'tacking' : 'gybing') +
          ' angle over the ground)<extra></extra>', showlegend: false, visible: !!visible, meta: 'ladder' },
      { x: lab.map(l => l.x), y: lab.map(l => l.y), mode: 'text', text: lab.map(l => l.rung_m + ' m'),
        textposition: 'middle left', textfont: { size: 10, color: c.ink2 },
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

  return { toGo, traces, button };
})();
