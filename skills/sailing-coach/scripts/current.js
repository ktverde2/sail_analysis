// Current page: CurrentMap(root, data). Needs Plotly (basic bundle). Data from current.py build().
function CurrentMap(root, D) {
  const S = { metric: "cross", boat: "All", race: "All", mode: "All", side: "All", cell: 100 };
  const $ = r => root.querySelector(`[data-r="${r}"]`);
  const css = n => getComputedStyle(root).getPropertyValue(n).trim();
  const boatVar = b => `--tk-series-${Math.min(Math.max(D.boats.indexOf(b), 0), 3) + 1}`;
  const med = a => {
    const v = a.filter(x => x != null && !isNaN(x)).sort((p, q) => p - q);
    if (!v.length) return null;
    const m = v.length >> 1;
    return v.length % 2 ? v[m] : (v[m - 1] + v[m]) / 2;
  };
  const pct = (a, p) => { const v = a.filter(x => x != null).sort((x, y) => x - y); return v.length ? v[Math.min(v.length - 1, Math.floor(p * v.length))] : null; };
  const sgn = (v, dp = 1, u = "") => (v == null ? "–" : (v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(v).toFixed(dp) + u);
  const short = r => r.replace("Race ", "R");
  // Directions are stored true (GPS); show them magnetic, like the compass, when the variation is known
  const MAG = D.mag_var != null;
  const mdir = v => (v == null ? v : Math.round(((v - (MAG ? D.mag_var : 0)) % 360 + 360) % 360));
  const NTH = MAG ? "° mag" : "° true";
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
          `That's the compass, not the water. ` + (MAG && Math.abs(Math.abs(o) - D.mag_var) < 2
            ? `It's the size of the local variation (${D.mag_var.toFixed(1)}°), so check the unit's variation setting: it looks like variation is being ${o > 0 ? "subtracted from" : "added to"} a heading that's already magnetic. `
            : `The unit is mounted or calibrated off. `) +
          `Wind directions and tacking headings from this boat are shifted by the same amount.`);
    }
    for (const b of D.boats.filter(b => D.fits[b].suspect))
      notes.push(`<b>${b}'s compass doesn't match its track the same way on every heading:</b> it ${D.fits[b].suspect}. ` +
        `The water was the same for every boat, so that's the heading sensor (uncompensated deviation, or the unit moving), not current. ` +
        `${b} is left off the map and out of the comparisons below; judge its angles over the ground (COG), not by its compass.`);
    const good = D.boats.filter(b => !D.fits[b].suspect);
    if (good.length > 1) {
      const s = good.map(b => [b, D.fits[b].slip]).sort((a, c) => c[1] - a[1]);
      const [hi, lo] = [s[0], s[s.length - 1]];
      if (hi[1] - lo[1] >= 1.5)
        notes.push(`<b>${hi[0]} slips ${(hi[1] - lo[1]).toFixed(1)}° more than ${lo[0]} upwind</b> (${hi[1].toFixed(1)}° against ${lo[1].toFixed(1)}°). ` +
          `They sailed the same water, so the difference is leeway, not current: usually pinching, or too little speed for the angle.`);
      const c = good.map(b => D.fits[b].cross_kt);
      if (c.every(v => Math.abs(v) < 0.2))
        notes.push(`<b>No measurable current across the course.</b> The boats' estimates (${good.map(b => `${b} ${sgn(D.fits[b].cross_kt, 2)} kt`).join(", ")}) ` +
          `are within the noise (about ±0.2 kt). Any current ran along the wind, which shows up in slip and speed, not here.`);
      else if (c.every(v => v > 0) || c.every(v => v < 0))
        notes.push(`<b>Current across the course: about ${Math.abs(c.reduce((a, v) => a + v, 0) / c.length).toFixed(2)} kt toward the ` +
          `${c[0] > 0 ? "right" : "left"} side</b> (looking upwind), and every boat agrees.`);
      else
        notes.push(`<b>The boats disagree on the current across the course</b> (${good.map(b => `${b} ${sgn(D.fits[b].cross_kt, 2)} kt`).join(", ")}): treat it as unmeasured.`);
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

  // ---- NOAA tide and current ------------------------------------------------------------------
  const hm = x => x.slice(11, 16);
  function raceShapes(N) {
    return N.races.map(r => ({ type: "rect", xref: "x", yref: "paper", x0: r.start, x1: r.end, y0: 0, y1: 1,
      fillcolor: css("--tk-grid"), opacity: 0.8, line: { width: 0 }, layer: "below" }));
  }
  function raceLabels(N) {
    return N.races.map(r => ({ xref: "x", yref: "paper", x: r.start, y: 1, xanchor: "left", yanchor: "top", showarrow: false,
      text: short(r.race), font: { size: 10, color: css("--tk-muted") } }));
  }
  function noaaPart() {
    const N = D.noaa;
    if (!N) return;
    $("noaa-card").hidden = false;
    const C = N.current, T = N.tide;
    const src = [];
    if (C) src.push(`Current: <b>${C.station.name}</b> (${C.station.id}), ${C.station.km} km from the course` +
      (C.station.depth_ft ? `, ${C.station.depth_ft} ft down` : "") + `. ${C.method}; flood toward ${mdir(C.flood_dir)}${NTH}, ebb toward ${mdir(C.ebb_dir)}${NTH}.`);
    if (T) src.push(`Tide: <b>${T.station.name}</b> (${T.station.id}), ${T.station.km} km, feet above ${T.datum}.`);
    src.push(`${N.source}, fetched ${N.fetched.slice(0, 10)}. Times are local.`);
    $("noaa-src").innerHTML = src.join(" ");
    const lay = (yt, extra) => base(Object.assign({ margin: { l: 48, r: 10, t: 16, b: 32 }, shapes: raceShapes(N), annotations: raceLabels(N),
      xaxis: axis("", { type: "date", tickformat: "%H:%M" }), yaxis: axis(yt),
      showlegend: true, legend: { orientation: "h", x: 0, y: -0.18, font: { size: 11 } } }, extra || {}));

    if (T) {
      const tr = [{ x: T.x, y: T.pred, type: "scatter", mode: "lines", name: "predicted", line: { color: css("--tk-ink2"), width: 2, dash: "dash" },
        hovertemplate: "%{x|%H:%M}: %{y:.2f} ft predicted<extra></extra>" }];
      if (T.obs.length) tr.push({ x: T.obs_x, y: T.obs, type: "scatter", mode: "lines", name: "observed", line: { color: css("--tk-series-1"), width: 2 },
        hovertemplate: "%{x|%H:%M}: %{y:.2f} ft observed<extra></extra>" });
      const hl = T.hilo.filter(h => h[0] >= T.x[0] && h[0] <= T.x[T.x.length - 1]);
      tr.push({ x: hl.map(h => h[0]), y: hl.map(h => h[1]), type: "scatter", mode: "markers+text", name: "high / low",
        text: hl.map(h => `${h[2] === "H" ? "High" : "Low"} ${hm(h[0])}`), textposition: "top center", textfont: { size: 10, color: css("--tk-muted") },
        marker: { size: 8, color: css("--tk-ink2") }, hovertemplate: "%{text}: %{y:.2f} ft<extra></extra>" });
      Plotly.react($("tide"), tr, lay(`ft above ${T.datum}`), CFG);
    } else $("tide").parentElement.hidden = true;

    if (C) {
      const ev = C.events.filter(e => e[0] >= C.x[0] && e[0] <= C.x[C.x.length - 1]);
      const tr = [
        { x: C.x, y: C.v, type: "scatter", mode: "lines", name: "predicted", line: { color: css("--tk-series-1"), width: 2 },
          hovertemplate: `%{x|%H:%M}: %{y:.2f} kt<extra></extra>` },
        { x: ev.map(e => e[0]), y: ev.map(e => e[1]), type: "scatter", mode: "markers+text", name: "max / slack",
          text: ev.map(e => e[2] === "slack" ? `slack ${hm(e[0])}` : `max ${e[2]} ${Math.abs(e[1]).toFixed(2)} kt ${hm(e[0])}`),
          textposition: "bottom center", textfont: { size: 10, color: css("--tk-muted") }, marker: { size: 8, color: css("--tk-ink2") },
          hovertemplate: "%{text}<extra></extra>" },
      ];
      Plotly.react($("cur"), tr, lay(`kt (+ flood ${mdir(C.flood_dir)}${NTH}, − ebb ${mdir(C.ebb_dir)}${NTH})`, {
        shapes: [...raceShapes(N), { type: "line", xref: "paper", x0: 0, x1: 1, y0: 0, y1: 0, line: { color: css("--tk-muted"), width: 1, dash: "dot" } }] }), CFG);

      // Predicted across the course against each boat's measurement per race
      const cmp = [{ x: C.x, y: C.across, type: "scatter", mode: "lines", name: "NOAA predicted, across", line: { color: css("--tk-ink2"), width: 2 },
        customdata: C.up, hovertemplate: "%{x|%H:%M}: %{y:+.2f} kt across · %{customdata:+.2f} kt up the course<extra>NOAA</extra>" }];
      for (const b of D.boats) {
        const pts = D.per_race.filter(p => p.boat === b).map(p => [N.races.find(r => r.race === p.race), p]).filter(([r]) => r && r.mid);
        if (!pts.length) continue;
        cmp.push({ x: pts.map(([r]) => r.mid), y: pts.map(([, p]) => p.cross_kt), type: "scatter", mode: "markers", name: `${b}, measured`,
          marker: { size: 11, color: css(boatVar(b)), line: { color: css("--tk-surface-1"), width: 1.5 } },
          text: pts.map(([r, p]) => `${b} · ${r.race}: measured ${sgn(p.cross_kt, 2)} kt; NOAA ${sgn(r.across, 2)} kt`),
          hovertemplate: "%{text}<extra></extra>" });
      }
      Plotly.react($("cmp"), cmp, lay("kt across (+ right)", {
        shapes: [...raceShapes(N), { type: "line", xref: "paper", x0: 0, x1: 1, y0: 0, y1: 0, line: { color: css("--tk-muted"), width: 1, dash: "dot" } }] }), CFG);
      $("leg-cmp").innerHTML = "";
    } else { $("cur").parentElement.hidden = true; $("cmp").hidden = true; }

    // Notes
    const notes = [], R = N.races.filter(r => r.v != null);
    if (C && R.length) {
      const phase = r => (Math.abs(r.v) < 0.15 ? "near slack" : r.v > 0 ? "flood" : "ebb");
      const ph = [...new Set(R.map(phase))];
      const inWin = C.events.filter(e => e[0] >= R[0].start && e[0] <= R[R.length - 1].end);
      notes.push(`<b>${ph.length === 1 ? `All ${R.length > 1 ? R.length + " races" : "the racing"} on the ${ph[0]}` : "Races: " + R.map(r => `${short(r.race)} ${phase(r)}`).join(", ")}</b> at the station` +
        (inWin.length ? `: ${inWin.map(e => (e[2] === "slack" ? `slack at ${hm(e[0])}` : `max ${e[2]} ${Math.abs(e[1]).toFixed(2)} kt toward ${mdir(e[2] === "flood" ? C.flood_dir : C.ebb_dir)}${NTH} at ${hm(e[0])}`)).join(", ")}.` : "."));
    }
    if (T && N.races.length) {
      const at = x => { let k = T.x.findIndex(t => t >= x); return k < 0 ? null : T.pred[k]; };
      const a = at(N.races[0].start), b = at(N.races[N.races.length - 1].end);
      const hl = T.hilo.filter(h => h[0] >= N.races[0].start && h[0] <= N.races[N.races.length - 1].end);
      if (a != null && b != null)
        notes.push(`<b>Tide ${b < a ? "falling" : "rising"} from ${a.toFixed(1)} to ${b.toFixed(1)} ft</b> over the racing` +
          (hl.length ? ` (${hl.map(h => `${h[2] === "H" ? "high" : "low"} ${h[1].toFixed(1)} ft at ${hm(h[0])}`).join(", ")}).` : "."));
      if (T.obs.length) {
        const diff = T.obs_x.map((x, i) => { const k = T.x.indexOf(x); return k < 0 ? null : T.obs[i] - T.pred[k]; }).filter(v => v != null);
        const m = med(diff);
        if (m != null && Math.abs(m) >= 0.3)
          notes.push(`The gauge read <b>${sgn(m)} ft ${m > 0 ? "above" : "below"} the prediction</b> through the day (weather, swell or a surge). The timing of the tide, not its height, is what drives the current.`);
      }
    }
    if (C && R.length) {
      const pred = R.reduce((s, r) => s + r.across, 0) / R.length;
      const ok = D.boats.filter(b => !D.fits[b].suspect);
      const meas = ok.length ? ok.reduce((s, b) => s + D.fits[b].cross_kt, 0) / ok.length : null;
      const side = v => (v > 0 ? "right" : "left");
      if (meas != null) {
        if (Math.abs(pred) < 0.25 && Math.abs(meas) < 0.25)
          notes.push(`<b>NOAA and the boats agree: little current across the course</b> (predicted ${sgn(pred, 2)} kt, measured ${sgn(meas, 2)} kt).`);
        else if (Math.sign(pred) === Math.sign(meas) && Math.abs(meas) >= 0.5 * Math.abs(pred) && Math.abs(meas) <= 2 * Math.abs(pred))
          notes.push(`<b>The boats felt what NOAA predicted:</b> about ${Math.abs(meas).toFixed(2)} kt across the course toward the ${side(meas)} (NOAA ${sgn(pred, 2)} kt).`);
        else
          notes.push(`<b>NOAA's station predicts ${Math.abs(pred).toFixed(2)} kt across the course toward the ${side(pred)} during the races; the boats measured ${sgn(meas, 2)} kt.</b> ` +
            `The station is ${C.station.km} km away (${C.station.name}), so its current is that water's, not the race area's: use it for timing (when the tide turns), not for how hard it pushed on the course.`);
      }
      const up = R.reduce((s, r) => s + r.up, 0) / R.length;
      if (Math.abs(up) >= 0.2)
      {
        // Along-course current would show as slip rising and falling with it on every boat
        const spread = D.boats.map(b => { const v = D.per_race.filter(p => p.boat === b).map(p => p.slip); return v.length > 1 ? Math.max(...v) - Math.min(...v) : null; }).filter(v => v != null);
        const steady = spread.length && Math.max(...spread) < 1;
        notes.push(`NOAA's current along the course averaged ${Math.abs(up).toFixed(2)} kt ${up > 0 ? "up the course (against a boat going downwind)" : "down the course (against a boat going upwind)"}. ` +
          `COG vs heading can't measure that part directly: it shows up as slip on every boat. ` +
          (steady ? `Each boat's slip stayed within ${Math.max(...spread).toFixed(1)}° from race to race while NOAA's current changed, so it didn't show on the course either.`
                  : `Slip varied by up to ${spread.length ? Math.max(...spread).toFixed(1) : "?"}° between races: compare it race by race with NOAA's along-course number.`));
      }
    }
    $("noaa-notes").innerHTML = notes.length ? "<ul>" + notes.map(n => `<li>${n}</li>`).join("") + "</ul>" : "";
  }

  // ---- upwind/downwind x port/starboard -------------------------------------------------------
  // ---- tack to tack: SOG port vs starboard, and whether it looks like current ---------------
  function t2t() {
    const T = D.t2t;
    if (!T || !T.rows || !T.rows.length) { $("t2t").textContent = "Not enough steady sailing on both tacks."; return; }
    const races = [...new Set(T.rows.map(r => r.race))].sort((a, b) => a.length - b.length || (a < b ? -1 : 1));
    const legKey = r => short(r.race) + " L" + r.leg;
    const order = [...new Set(races.flatMap(rc => T.rows.filter(r => r.race === rc).sort((a, b) => a.leg - b.leg).map(legKey)))];
    const tr = D.boats.map(b => {
      const rows = T.rows.filter(r => r.boat === b);
      return { x: rows.map(legKey), y: rows.map(r => r.d_sog), type: "scatter", mode: "markers", name: b + (D.fits[b].suspect ? " (compass suspect)" : ""),
        marker: { size: 9, color: css(boatVar(b)), symbol: rows.map(r => (r.mode === "U" ? "circle" : "diamond")), line: { width: 0 } },
        text: rows.map(r => `<b>${b}</b> · ${r.race}, leg ${r.leg} (${r.mode === "U" ? "beat" : "run"})<br>SOG starboard ${r.sog_s.toFixed(2)} · port ${r.sog_p.toFixed(2)} kt` +
          `<br>As current: ${r.c_sog == null ? "–" : sgn(r.c_sog, 2, " kt")} across · drift says ${r.c_drift == null ? "–" : sgn(r.c_drift, 2, " kt")}`),
        hovertemplate: "%{text}<extra></extra>" };
    });
    Plotly.react($("t2t"), tr, base({
      margin: { l: 52, r: 10, t: 10, b: 40 }, height: 300,
      xaxis: axis("", { type: "category", categoryorder: "array", categoryarray: order }),
      yaxis: axis("SOG starboard − port (kt)"),
      shapes: [{ type: "line", xref: "paper", x0: 0, x1: 1, y0: 0, y1: 0, line: { color: css("--tk-muted"), width: 1 } }],
    }), CFG);
    $("leg-t2t").innerHTML = D.boats.map(b => `<span><i class="tk-dot" style="background:var(${boatVar(b)})"></i>${b}</span>`).join("") +
      `<span>● beat</span><span>◆ run</span>`;
    const n = [], fu = T.faster_up || {}, sh = T.shared || {}, he = T.held || {}, rf = T.run_flips || {}, ag = T.agree || {};
    if (fu.n) {
      const tk = fu.mean_kt < 0 ? "port" : "starboard";
      n.push(`<b>Upwind, ${tk} was the faster tack over the ground</b>: ${tk === "port" ? fu.port : fu.stbd} of ${fu.n} beats (boat by boat), ` +
        `${Math.abs(fu.mean_kt).toFixed(2)} kt on average.` + (sh.n ? ` On ${sh.same} of ${sh.n} beats where the tacks differed, every boat agreed, ` +
        `so it's the water, the waves or the wind, not the boats.` : ""));
    }
    const current = he.n && he.same / he.n >= 0.7 && ag.r != null && ag.r >= 0.5;
    const parts = [];
    if (he.n) parts.push(`it held from the beat to the run in ${he.same} of ${he.n} races`);
    if (rf.n) parts.push(`the two runs of the same race disagreed in ${rf.flipped} of ${rf.n}`);
    if (ag.r != null) parts.push(`and it ${Math.abs(ag.r) < 0.3 ? "doesn't line up with" : ag.r > 0 ? "lines up with" : "runs against"} the drift (r = ${ag.r.toFixed(2)}, ${ag.n} legs)`);
    if (parts.length)
      n.push(current
        ? `<b>It behaves like current:</b> ${parts.join(", ")}. About ${sgn(ag.sog_median, 2, " kt")} across the course (+ toward the right, looking upwind).`
        : `<b>It doesn't behave like current:</b> ${parts.join(", ")}. A current would favour the same side of the course on beats and runs and show in the drift. ` +
          `More likely waves (one tack sailing into the sea) or pressure. Coach it as a mode question on that tack, not a current to play.`);
    $("t2tnotes").innerHTML = n.length ? "<ul>" + n.map(x => `<li>${x}</li>`).join("") + "</ul>" : "";
  }

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
        text: `↑ upwind (${mdir(D.axis)}${NTH})`, font: { size: 11, color: muted } },
    ];
    // NOAA's predicted current for the race(s) in view, drawn in the map's frame (x across, y up)
    const NC = D.noaa && D.noaa.current && D.noaa.races.filter(r => r.across != null && (S.race === "All" || r.race === S.race));
    if (NC && NC.length) {
      const ac = NC.reduce((s, r) => s + r.across, 0) / NC.length, up = NC.reduce((s, r) => s + r.up, 0) / NC.length;
      const spd = Math.hypot(ac, up), L = 70 * Math.min(1.5, spd) / Math.max(spd, 1e-6) ;
      const brg = ((D.axis + Math.atan2(ac, up) * 180 / Math.PI) % 360 + 360) % 360;
      ann.push({ xref: "paper", yref: "paper", x: 0.16, y: 0.14, ax: -ac * L, ay: up * L, axref: "pixel", ayref: "pixel",
        showarrow: spd >= 0.05, arrowhead: 2, arrowwidth: 2.5, arrowcolor: css("--tk-ink"),
        text: "", hovertext: "NOAA prediction at the station, not a measurement" },
        { xref: "paper", yref: "paper", x: 0.01, y: 0.01, xanchor: "left", yanchor: "bottom", showarrow: false, align: "left",
          text: `NOAA, ${D.noaa.current.station.name}: ${spd.toFixed(2)} kt toward ${mdir(brg)}${NTH}<br>(${S.race === "All" ? "average of the races" : S.race}, at the station)`,
          font: { size: 11, color: muted } });
    }
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
    fits(); t2t(); noaaPart(); breakdown(); legs(); segs(); renderMap();
  }
  render();
  window.addEventListener("hashchange", () => setTimeout(render, 0));
  document.addEventListener("DOMContentLoaded", () => setTimeout(render, 0));
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", render);
  new MutationObserver(render).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  return { render };
}
