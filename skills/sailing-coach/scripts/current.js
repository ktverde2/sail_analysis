// Current page: CurrentMap(root, data). Needs Plotly (basic bundle). Data from current.py build().
function CurrentMap(root, D) {
  const S = { metric: "cross", boat: "All", race: "All", mode: "All", side: "All", cell: 100 };
  const $ = r => root.querySelector(`[data-r="${r}"]`);
  const css = n => getComputedStyle(root).getPropertyValue(n).trim();
  const boatVar = b => (D.boats.indexOf(b) === 0 ? "--tk-series-1" : "--tk-series-2");
  const med = a => {
    const v = a.filter(x => x != null && !isNaN(x)).sort((p, q) => p - q);
    if (!v.length) return null;
    const m = v.length >> 1;
    return v.length % 2 ? v[m] : (v[m - 1] + v[m]) / 2;
  };
  const pct = (a, p) => { const v = a.filter(x => x != null).sort((x, y) => x - y); return v.length ? v[Math.min(v.length - 1, Math.floor(p * v.length))] : null; };
  const sgn = (v, dp = 1, u = "") => (v == null ? "–" : (v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(v).toFixed(dp) + u);
  const short = r => r.replace("Race ", "R");
  const CFG = { displaylogo: false, responsive: true, modeBarButtonsToRemove: ["select2d", "lasso2d", "autoScale2d"] };
  const axis = (title, extra) => Object.assign({
    title: { text: title, font: { size: 12 } }, gridcolor: css("--tk-grid"), zeroline: false,
    linecolor: css("--tk-border"), tickfont: { color: css("--tk-muted") },
  }, extra || {});
  const base = extra => Object.assign({
    paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)",
    font: { family: "system-ui, sans-serif", size: 12, color: css("--tk-ink2") },
    showlegend: false, hovermode: "closest",
    hoverlabel: { bgcolor: css("--tk-surface-1"), bordercolor: css("--tk-border"), font: { color: css("--tk-ink") } },
  }, extra || {});

  // ---- what the gap is made of ------------------------------------------------------------
  function fits() {
    const rows = D.boats.map(b => {
      const f = D.fits[b];
      const pr = D.per_race.filter(p => p.boat === b)
        .map(p => `${short(p.race)} ${sgn(p.offset)}° / ${p.slip.toFixed(1)}° / ${sgn(p.cross_kt, 2)}`).join(" · ");
      return `<tr class="${D.focus === b ? "focus" : ""}"><td><i class="tk-dot" style="background:var(${boatVar(b)})"></i>${b}</td>
        <td>${sgn(f.offset)}°</td><td>${f.slip.toFixed(1)}°</td><td>${sgn(f.cross_kt, 2)} kt</td>
        <td style="text-align:left"><span class="cur-sub">${pr}</span></td></tr>`;
    }).join("");
    $("fits").innerHTML = `<thead><tr><th>Boat</th><th>Compass offset</th><th>Upwind slip</th><th>Current across the course</th>
      <th style="text-align:left">Race by race (offset / slip / across)</th></tr></thead><tbody>${rows}</tbody>`;

    const notes = [];
    for (const b of D.boats) {
      const o = D.fits[b].offset;
      if (Math.abs(o) >= 5)
        notes.push(`<b>${b}'s heading reads ${Math.abs(o).toFixed(0)}° ${o > 0 ? "left" : "right"} of its track on every heading.</b> ` +
          `That's the compass, not the water: close to the local magnetic variation it usually means variation is added twice ` +
          `(the unit already gives true heading); otherwise the unit is mounted or calibrated off. Wind directions and tacking ` +
          `headings from this boat are shifted by the same amount.`);
    }
    if (D.boats.length > 1) {
      const s = D.boats.map(b => [b, D.fits[b].slip]).sort((a, c) => c[1] - a[1]);
      const [hi, lo] = [s[0], s[s.length - 1]];
      if (hi[1] - lo[1] >= 1.5)
        notes.push(`<b>${hi[0]} slips ${(hi[1] - lo[1]).toFixed(1)}° more than ${lo[0]} upwind</b> (${hi[1].toFixed(1)}° against ${lo[1].toFixed(1)}°). ` +
          `They sailed the same water, so the difference is leeway, not current: usually pinching, or too little speed for the angle.`);
      const c = D.boats.map(b => D.fits[b].cross_kt);
      if (c.every(v => Math.abs(v) < 0.2))
        notes.push(`<b>No measurable current across the course.</b> The boats' estimates (${D.boats.map(b => `${b} ${sgn(D.fits[b].cross_kt, 2)} kt`).join(", ")}) ` +
          `are within the noise (about ±0.2 kt). Any current ran along the wind, which shows up in slip and speed, not here.`);
      else if (c.every(v => v > 0) || c.every(v => v < 0))
        notes.push(`<b>Current across the course: about ${Math.abs(c.reduce((a, v) => a + v, 0) / c.length).toFixed(2)} kt toward the ` +
          `${c[0] > 0 ? "right" : "left"} side</b> (looking upwind), and every boat agrees.`);
      else
        notes.push(`<b>The boats disagree on the current across the course</b> (${D.boats.map(b => `${b} ${sgn(D.fits[b].cross_kt, 2)} kt`).join(", ")}): treat it as unmeasured.`);
    }
    // One boat drifting differently on its two gybes, in water another boat didn't find: a compass error
    const corr = (b, m, p) => (D.breakdown.find(x => x.boat === b && x.mode === m && x.side === p) || {}).corr;
    const split = b => { const a = corr(b, "D", "P"), c = corr(b, "D", "S"); return a == null || c == null ? null : a - c; };
    for (const b of D.boats) {
      const d = split(b);
      if (d == null || Math.abs(d) < 3) continue;
      const calm = D.boats.filter(o => o !== b && split(o) != null && Math.abs(split(o)) < 1.5);
      notes.push(calm.length
        ? `<b>${b} tracks ${Math.abs(d).toFixed(1)}° further ${d > 0 ? "right" : "left"} on port gybe than on starboard; ${calm.join(", ")} doesn't</b> in the same water. ` +
          `That points to compass deviation on those headings (worth swinging the compass), not current.`
        : `<b>${b} tracks ${Math.abs(d).toFixed(1)}° further ${d > 0 ? "right" : "left"} on port gybe than on starboard.</b> With no other boat to compare, that could be current or compass deviation.`);
    }
    $("fitnotes").innerHTML = notes.length ? "<ul>" + notes.map(n => `<li>${n}</li>`).join("") + "</ul>" : "";
  }

  // ---- upwind/downwind x port/starboard -------------------------------------------------------
  function breakdown() {
    const cols = [["U", "P", "Upwind port"], ["U", "S", "Upwind starboard"], ["D", "P", "Downwind port"], ["D", "S", "Downwind starboard"]];
    const cell = (b, m, p) => {
      const x = D.breakdown.find(r => r.boat === b && r.mode === m && r.side === p);
      return x ? `<td>${sgn(x.corr)}°<span class="cur-sub">raw ${sgn(x.raw)}° · ${Math.round(x.n * 2 / 60)} min</span></td>` : "<td>–</td>";
    };
    $("brk").innerHTML = `<thead><tr><th>Boat</th>${cols.map(c => `<th>${c[2]}</th>`).join("")}</tr></thead><tbody>` +
      D.boats.map(b => `<tr class="${D.focus === b ? "focus" : ""}"><td><i class="tk-dot" style="background:var(${boatVar(b)})"></i>${b}</td>${cols.map(c => cell(b, c[0], c[1])).join("")}</tr>`).join("") +
      "</tbody>";
  }

  // ---- leg by leg -----------------------------------------------------------------------------
  function legs() {
    const label = {};
    for (const r of D.races) {
      const ls = [...new Map(D.legs.filter(l => l.race === r).sort((a, b) => a.leg - b.leg).map(l => [l.leg, l.mode])).entries()];
      let u = 0, d = 0;
      for (const [leg, m] of ls) label[r + "|" + leg] = `${short(r)} ${m === "U" ? "Beat " + ++u : "Run " + ++d}`;
    }
    const order = [...new Set(D.races.flatMap(r => D.legs.filter(l => l.race === r).sort((a, b) => a.leg - b.leg).map(l => label[r + "|" + l.leg])))];
    const traces = [];
    for (const b of D.boats) for (const p of ["P", "S"]) {
      const pts = D.legs.filter(l => l.boat === b && l.side === p);
      if (!pts.length) continue;
      const col = css(boatVar(b));
      traces.push({
        x: pts.map(l => label[l.race + "|" + l.leg]), y: pts.map(l => l.corr), type: "scatter", mode: "markers",
        marker: { size: 11, symbol: p === "P" ? "circle" : "diamond", color: col, line: { color: css("--tk-surface-1"), width: 1.5 } },
        text: pts.map(l => `<b>${b}</b> · ${label[l.race + "|" + l.leg]} · ${p === "P" ? "port" : "starboard"}<br>COG − heading ${sgn(l.corr)}° (raw ${sgn(l.raw)}°)<br>${Math.round(l.n * 2)} s · SOG ${l.sog.toFixed(1)} kt`),
        hovertemplate: "%{text}<extra></extra>",
      });
    }
    const shapes = [{ type: "line", xref: "paper", x0: 0, x1: 1, y0: 0, y1: 0, line: { color: css("--tk-muted"), width: 1, dash: "dot" } }];
    Plotly.react($("legs"), traces, base({
      margin: { l: 52, r: 12, t: 8, b: 48 }, shapes,
      xaxis: axis("", { type: "category", categoryorder: "array", categoryarray: order }),
      yaxis: axis("COG − heading (°), offset removed"),
    }), CFG);
    $("leg-legs").innerHTML = D.boats.map(b => `<span><i class="tk-sw" style="background:var(${boatVar(b)})"></i>${b}</span>`).join("") +
      `<span><span class="tk-mk">●</span>port</span><span><span class="tk-mk">◆</span>starboard</span>`;
  }

  // ---- the course -----------------------------------------------------------------------------
  const METRICS = {
    cross: { label: "Current across", note: "Current across the course, from each sample's drift left after its boat's compass offset and slip. Red: water pushing toward the right side of the course (looking upwind); blue: toward the left." },
    mag: { label: "Drift angle", note: "How far the boats tracked off their heading, either way, with the compass offset removed. Upwind this includes leeway, so compare upwind cells with upwind cells (Leg filter)." },
    count: { label: "Coverage", note: "Seconds of steady sailing in each cell, from the boats and races selected (samples every 2 s)." },
  };

  function segs() {
    const seg = (r, opts, key, fmt) => {
      const el = $(r); el.innerHTML = "";
      for (const o of opts) {
        const b = document.createElement("button");
        b.type = "button"; b.textContent = fmt ? fmt(o) : o;
        b.setAttribute("aria-pressed", S[key] === o);
        b.onclick = () => { S[key] = o; renderMap(); segs(); };
        el.appendChild(b);
      }
    };
    seg("f-metric", Object.keys(METRICS), "metric", m => METRICS[m].label);
    if (D.boats.length > 1) seg("f-boat", ["All", ...D.boats], "boat"); else $("f-boat").parentElement.hidden = true;
    seg("f-race", ["All", ...D.races], "race", r => (r === "All" ? r : short(r)));
    seg("f-mode", ["All", "U", "D"], "mode", m => ({ All: "All", U: "Upwind", D: "Downwind" }[m]));
    seg("f-side", ["All", "P", "S"], "side", m => ({ All: "All", P: "Port", S: "Stbd" }[m]));
    seg("f-cell", [50, 100, 150], "cell", c => c + " m");
    $("metric-note").textContent = METRICS[S.metric].note;
  }

  let cellTrace = -1;
  function renderMap() {
    const s = D.s, n = s.x.length, c = S.cell;
    const bi = D.boats.indexOf(S.boat), ri = D.races.indexOf(S.race);
    const cells = new Map();
    for (let i = 0; i < n; i++) {
      if ((bi >= 0 && s.b[i] !== bi) || (ri >= 0 && s.r[i] !== ri) || (S.mode !== "All" && s.m[i] !== S.mode) || (S.side !== "All" && s.p[i] !== S.side)) continue;
      const k = Math.floor(s.x[i] / c) + "," + Math.floor(s.y[i] / c);
      let e = cells.get(k);
      if (!e) cells.set(k, (e = { i: [], cx: (Math.floor(s.x[i] / c) + 0.5) * c, cy: (Math.floor(s.y[i] / c) + 0.5) * c }));
      e.i.push(i);
    }
    const X = [], Y = [], V = [], O = [], T = [];
    for (const e of cells.values()) {
      if (e.i.length < 3) continue; // under 6 s isn't a reading
      const cross = med(e.i.map(i => s.cross[i])), mag = med(e.i.map(i => Math.abs(s.corr[i]))), sog = med(e.i.map(i => s.sog[i]));
      const who = [...new Set(e.i.map(i => D.boats[s.b[i]]))].join(", ");
      const v = S.metric === "cross" ? cross : S.metric === "mag" ? mag : e.i.length * 2;
      if (v == null) continue;
      X.push(e.cx); Y.push(e.cy); V.push(v);
      O.push(Math.min(1, 0.35 + e.i.length / 25));
      T.push(`${e.i.length * 2} s from ${who}<br>Current across ${sgn(cross, 2, " kt")} ${cross > 0 ? "(toward the right)" : cross < 0 ? "(toward the left)" : ""}` +
        `<br>Drift angle ${mag == null ? "–" : mag.toFixed(1) + "°"} · SOG ${sog == null ? "–" : sog.toFixed(1) + " kt"}`);
    }
    let cs, cmin, cmax, title;
    if (S.metric === "cross") {
      const lim = Math.max(0.2, Math.ceil((pct(V.map(Math.abs), 0.95) || 0.2) * 10) / 10);
      cs = [[0, css("--cur-neg")], [0.25, css("--cur-neg2")], [0.5, css("--cur-mid")], [0.75, css("--cur-pos2")], [1, css("--cur-pos")]];
      [cmin, cmax, title] = [-lim, lim, "kt across<br>(+ right)"];
    } else {
      cs = [[0, css("--cur-seq0")], [0.35, css("--cur-seq1")], [0.7, css("--cur-seq2")], [1, css("--cur-seq3")]];
      [cmin, cmax, title] = [0, Math.max(1, pct(V, 0.95) || 1), S.metric === "mag" ? "degrees" : "seconds"];
    }
    const traces = [{
      x: X, y: Y, text: T, type: "scatter", mode: "markers", hovertemplate: "%{text}<extra></extra>",
      marker: { symbol: "square", size: 6, color: V, opacity: O, colorscale: cs, cmin, cmax,
        line: { width: 0 },
        colorbar: { title: { text: title, font: { size: 11 } }, thickness: 12, len: 0.6, tickfont: { color: css("--tk-muted") }, outlinewidth: 0 } },
    }];
    cellTrace = 0;
    // Course marks for the race(s) in view
    const ink = css("--cur-mark");
    const seen = new Set();
    for (const m of D.marks) {
      if (S.race !== "All" && m.race !== S.race) continue;
      const k = m.type + JSON.stringify(m.pts.map(p => p.map(v => Math.round(v / 10))));
      if (seen.has(k)) continue;
      seen.add(k);
      const line = m.pts.length > 1;
      traces.push({
        x: m.pts.map(p => p[0]), y: m.pts.map(p => p[1]), type: "scatter", mode: line ? "lines+markers" : "markers",
        line: { color: ink, width: 2, dash: m.type === "FinishLine" ? "dot" : "solid" },
        marker: { size: line ? 5 : 9, color: ink, symbol: line ? "circle" : "circle-open", line: { width: 2, color: ink } },
        hovertemplate: `${{ StartLine: "Start line", FinishLine: "Finish line", Gate: "Gate", Mark: "Mark", Offset: "Offset" }[m.type] || m.type}` +
          (S.race === "All" ? ` (${m.race})` : "") + "<extra></extra>",
      });
    }
    // North arrow: the page is turned so upwind is up
    const th = D.axis * Math.PI / 180, nx = -Math.sin(th), ny = Math.cos(th);
    const muted = css("--tk-muted");
    const ann = [
      { xref: "paper", yref: "paper", x: 0.98, y: 0.97, ax: -26 * nx, ay: 26 * ny, axref: "pixel", ayref: "pixel",
        showarrow: true, arrowhead: 2, arrowsize: 1, arrowwidth: 1.5, arrowcolor: muted, text: "N", font: { size: 11, color: muted } },
      { xref: "paper", yref: "paper", x: 0.01, y: 0.99, xanchor: "left", yanchor: "top", showarrow: false,
        text: `↑ upwind (${Math.round(D.axis)}°)`, font: { size: 11, color: muted } },
    ];
    const el = $("map");
    Plotly.react(el, traces, base({
      margin: { l: 44, r: 8, t: 8, b: 40 }, annotations: ann,
      // Equal metres both ways, and the plot narrows to the course instead of padding the sides
      xaxis: axis("metres across the course", { zeroline: false, constrain: "domain" }),
      yaxis: axis("metres up the course", { scaleanchor: "x", scaleratio: 1, constrain: "domain" }),
    }), CFG).then(sizeCells);
    el.removeAllListeners && el.removeAllListeners("plotly_relayout");
    el.on("plotly_relayout", () => setTimeout(sizeCells, 0));
  }

  // Square markers sized to the cell, so the grid tiles at any zoom
  function sizeCells() {
    const el = $("map"), xa = el._fullLayout && el._fullLayout.xaxis;
    if (!xa || cellTrace < 0) return;
    const px = xa._length / Math.abs(xa.range[1] - xa.range[0]) * S.cell;
    const size = Math.max(2, px - 1);
    if (Math.abs((el.data[cellTrace].marker.size || 0) - size) > 0.5) Plotly.restyle(el, { "marker.size": size }, [cellTrace]);
  }

  function render() {
    if (!root.offsetParent) return; // hidden page: Plotly can't size charts yet
    fits(); breakdown(); legs(); segs(); renderMap();
  }
  render();
  window.addEventListener("hashchange", () => setTimeout(render, 0));
  document.addEventListener("DOMContentLoaded", () => setTimeout(render, 0));
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", render);
  new MutationObserver(render).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  return { render };
}
