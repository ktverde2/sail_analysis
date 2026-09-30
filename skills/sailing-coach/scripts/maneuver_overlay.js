// Tack/gybe overlay component: TackOverlay(root, data). Needs Plotly (basic bundle) on the page.
// data = { kind: "tack"|"gybe", words: {...}, boats: [...all boats, fixes each boat's colour], items: [...], note }
function TackOverlay(root, DATA) {
  const W = DATA.words, TACK = DATA.kind === "tack";
  // VMC (speed toward the mark) is what a maneuver is scored on; SOG explains it
  const HAS_VMC = DATA.items.some(d => d.vmc);
  const S = { boat: "All", race: "All", onto: "All", pct: 10, angle: "cog", sel: null, speed: HAS_VMC ? "vmc" : "sog" };
  const $ = r => root.querySelector(`[data-r="${r}"]`);
  const css = n => getComputedStyle(root).getPropertyValue(n).trim();
  const boatVar = b => `--tk-series-${Math.min(Math.max(DATA.boats.indexOf(b), 0), 3) + 1}`;
  const med = a => {
    const v = a.filter(x => x != null && !isNaN(x)).sort((p, q) => p - q);
    if (!v.length) return null;
    const m = v.length >> 1;
    return v.length % 2 ? v[m] : (v[m - 1] + v[m]) / 2;
  };
  const f1 = v => (v == null ? "–" : (+v).toFixed(1));
  const f0 = v => (v == null ? "–" : Math.round(v).toString());
  const inView = [...new Set(DATA.items.map(d => d.boat))];
  const boatsHere = DATA.boats.filter(b => inView.includes(b));

  function seg(r, opts, key, fmt) {
    const el = $(r);
    el.innerHTML = "";
    for (const o of opts) {
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = fmt ? fmt(o) : o;
      b.setAttribute("aria-pressed", S[key] === o);
      b.onclick = () => { S[key] = o; S.sel = null; render(); };
      el.appendChild(b);
    }
  }

  function view() {
    const t = DATA.items.filter(d =>
      (S.boat === "All" || d.boat === S.boat) &&
      (S.race === "All" || d.race === S.race) &&
      (S.onto === "All" || d.onto === S.onto));
    const ranked = [...t].sort((a, b) => a.secs_lost - b.secs_lost);
    const n = Math.max(1, Math.round(ranked.length * S.pct / 100));
    const top = new Set(ranked.slice(0, n).map(d => d.id));
    ranked.forEach((d, i) => (d.rank = i + 1));
    return { ranked, top, n };
  }

  function axis(title, extra) {
    return Object.assign({
      title: { text: title, font: { size: 12 } }, gridcolor: css("--tk-grid"), zeroline: false,
      linecolor: css("--tk-border"), tickfont: { color: css("--tk-muted") },
    }, extra || {});
  }
  function layout(ytitle, xtitle, extra) {
    return Object.assign({
      paper_bgcolor: "rgba(0,0,0,0)", plot_bgcolor: "rgba(0,0,0,0)",
      font: { family: "system-ui, sans-serif", size: 12, color: css("--tk-ink2") },
      margin: { l: 48, r: 12, t: 8, b: 40 }, showlegend: false, hovermode: "closest",
      hoverlabel: { bgcolor: css("--tk-surface-1"), bordercolor: css("--tk-border"), font: { color: css("--tk-ink") } },
      xaxis: axis(xtitle), yaxis: axis(ytitle),
    }, extra || {});
  }
  const CFG = { displaylogo: false, responsive: true, modeBarButtonsToRemove: ["select2d", "lasso2d", "autoScale2d"] };

  function clickable(el) {
    el.removeAllListeners && el.removeAllListeners("plotly_click");
    el.on("plotly_click", e => {
      const id = e.points[0].customdata;
      if (id) { S.sel = S.sel === id ? null : id; render(); }
    });
  }

  function overlay(r, key, ytitle, v) {
    const ctx = css("--tk-context"), sec = css("--tk-ink2"), surf = css("--tk-surface-1");
    const traces = [];
    const order = [...v.ranked].sort((a, b) => (v.top.has(a.id) - v.top.has(b.id)) || (b.secs_lost - a.secs_lost));
    for (const d of order) {
      const isTop = v.top.has(d.id), isSel = S.sel === d.id;
      traces.push({
        x: d.x, y: d[key] || [], mode: "lines", type: "scatter", customdata: d.x.map(() => d.id),
        line: { color: isTop || isSel ? css(boatVar(d.boat)) : ctx, width: isSel ? 4 : isTop ? 2.5 : 1.2, shape: "spline", smoothing: 0.4 },
        opacity: S.sel && !isSel ? 0.55 : isTop ? 1 : 0.8,
        hovertemplate: `<b>${d.id}</b> · onto ${d.onto}<br>%{x} s: %{y:.1f}${key === "hdg" ? "°" : " kt"}<extra>#${d.rank}</extra>`,
      });
    }
    // Median of everything in view
    const xs = [];
    for (let x = -15; x <= 40; x++) xs.push(x);
    const my = xs.map(x => med(v.ranked.map(d => { const i = d.x.indexOf(x); return i < 0 || !d[key] ? null : d[key][i]; })));
    traces.push({ x: xs, y: my, mode: "lines", type: "scatter", line: { color: sec, width: 2, dash: "dash" },
      hovertemplate: `Median of ${v.ranked.length} ${W.many}<br>%{x} s: %{y:.1f}<extra></extra>` });

    if (key === "sog") {
      // Entry, lowest and recovered markers on the highlighted (or selected) tacks
      const mk = { entry: [[], [], [], []], min: [[], [], [], []], rec: [[], [], [], []] };
      for (const d of v.ranked) {
        if (!(v.top.has(d.id) || S.sel === d.id)) continue;
        const c = css(boatVar(d.boat));
        mk.entry[0].push(-7); mk.entry[1].push(d.entry); mk.entry[2].push(c);
        mk.entry[3].push(`${d.id}<br>Entry ${d.entry.toFixed(2)} kt`);
        mk.min[0].push(d.t_min); mk.min[1].push(d.min); mk.min[2].push(c);
        mk.min[3].push(`${d.id}<br>Lowest ${d.min.toFixed(2)} kt at ${d.t_min} s (−${d.loss_pct}%)`);
        if (d.t_rec != null) {
          mk.rec[0].push(d.t_rec); mk.rec[1].push(d.sog[d.x.indexOf(d.t_rec)]); mk.rec[2].push(c);
          mk.rec[3].push(`${d.id}<br>Back to 95% at ${d.t_rec} s`);
        }
      }
      const sym = { entry: "circle", min: "triangle-down", rec: "diamond" };
      for (const k of ["entry", "min", "rec"]) {
        traces.push({ x: mk[k][0], y: mk[k][1], text: mk[k][3], mode: "markers", type: "scatter",
          marker: { symbol: sym[k], size: 11, color: mk[k][2], line: { color: surf, width: 2 } },
          hovertemplate: "%{text}<extra></extra>" });
      }
    }
    const muted = css("--tk-muted");
    const extra = {
      shapes: [{ type: "line", x0: 0, x1: 0, yref: "paper", y0: 0, y1: 1, line: { color: muted, width: 1, dash: "dot" } }],
      annotations: [{ x: 0, yref: "paper", y: 1, text: W.mid, showarrow: false, xanchor: "left", yanchor: "top", font: { size: 11, color: muted } }],
      xaxis: axis(`seconds from ${W.mid}`, { range: [-15, 40] }),
      // A maneuver followed by a bear-away or another turn runs off the scale: keep the turn itself readable
      yaxis: axis(ytitle, key === "hdg" ? { range: [-20, 130] } : {}),
    };
    const el = $(r);
    Plotly.react(el, traces, layout(ytitle, "", extra), CFG);
    clickable(el);
  }

  function scatter(v) {
    const key = S.angle === "cog" ? "cog_angle" : "hdg_angle";
    const surf = css("--tk-surface-1");
    const traces = [];
    for (const b of boatsHere) {
      const pts = v.ranked.filter(d => d.boat === b);
      if (!pts.length) continue;
      const col = css(boatVar(b));
      const hl = d => v.top.has(d.id) || S.sel === d.id;
      traces.push({
        x: pts.map(d => d[key]), y: pts.map(d => d.secs_lost), customdata: pts.map(d => d.id),
        text: pts.map(d => `<b>${d.id}</b> · onto ${d.onto}<br>Angle ${f1(d[key])}° (${S.angle === "cog" ? "compass " + f1(d.hdg_angle) : "over ground " + f1(d.cog_angle)}°)<br>Time lost ${d.secs_lost} s · ${d.m_lost} m`),
        mode: "markers", type: "scatter", name: b,
        marker: {
          size: pts.map(d => (S.sel === d.id ? 16 : v.top.has(d.id) ? 13 : 9)),
          color: pts.map(d => (hl(d) ? col : "rgba(0,0,0,0)")),
          line: { color: pts.map(d => (hl(d) ? surf : col)), width: 2 },
        },
        hovertemplate: "%{text}<extra></extra>",
      });
    }
    const el = $("ang");
    Plotly.react(el, traces, layout("time lost (s)", S.angle === "cog" ? `${W.angle} over the ground (°)` : `${W.angle} by compass (°)`), CFG);
    clickable(el);
  }

  function legends(v) {
    const boats = boatsHere.filter(b => v.ranked.some(d => d.boat === b));
    const base = boats.map(b => `<span><i class="tk-sw" style="background:var(${boatVar(b)})"></i>${b}, best ${S.pct}%</span>`).join("")
      + `<span><i class="tk-sw" style="background:var(--tk-context)"></i>other ${W.many}</span>`
      + `<span><i class="tk-sw" style="background:repeating-linear-gradient(90deg,var(--tk-ink2) 0 5px,transparent 5px 8px)"></i>median</span>`;
    $("leg-hdg").innerHTML = base;
    $("leg-sog").innerHTML = base
      + `<span><span class="tk-mk">●</span>entry</span><span><span class="tk-mk">▼</span>lowest</span><span><span class="tk-mk">◆</span>back to 95%</span>`;
    $("leg-ang").innerHTML = boats.map(b =>
      `<span><span class="tk-mk" style="color:var(${boatVar(b)})">●</span>${b}, best ${S.pct}%</span>`
      + `<span><span class="tk-mk" style="color:var(${boatVar(b)})">○</span>${b}, other</span>`).join("");
  }

  function tiles(v) {
    const T = v.ranked.filter(d => v.top.has(d.id)), R = v.ranked.filter(d => !v.top.has(d.id));
    const tile = (k, key, fmt, unit) => `<div class="tk-tile"><div class="k">${k}</div>
      <div class="v">${fmt(med(T.map(d => d[key])))}${unit}</div>
      <div class="d">best ${S.pct}% · rest ${fmt(med(R.map(d => d[key])))}${unit}</div></div>`;
    const kt = x => (x == null ? "–" : x.toFixed(2));
    $("tiles").innerHTML =
      `<div class="tk-tile"><div class="k">${W.Many} in view</div><div class="v">${v.ranked.length}</div><div class="d">${v.n} highlighted</div></div>`
      + tile(HAS_VMC ? "Time lost to the mark" : "Time lost", "secs_lost", f1, " s")
      + (HAS_VMC ? tile("VMC going in", "vmc_in", kt, " kt") : "")
      + tile("Entry speed", "entry", kt, " kt")
      + tile("Lowest speed", "min", kt, " kt")
      + tile("Speed lost", "loss_pct", f0, "%")
      + tile("Back to 95%", "t_rec", f0, " s");
  }

  // Metrics compared best vs rest: label, key, unit, which way is better (+1 higher, -1 lower, 0 neutral), threshold, decimals
  const CMP = [
    [HAS_VMC ? "Time lost toward the mark" : "Time lost", "secs_lost", " s", -1, 0, 1],
    [HAS_VMC ? "Metres lost toward the mark" : "Metres lost", "m_lost", " m", -1, 2, 0],
    ...(HAS_VMC ? [["VMC going in", "vmc_in", " kt", 0, 0.1, 2], ["VMC once settled", "vmc_out", " kt", 0, 0.1, 2],
      ["Call: changing tack, metres (− = gained)", "call_m", " m", 0, 5, 0]] : []),
    ["Entry speed", "entry", " kt", +1, 0.1, 2],
    ["Lowest speed", "min", " kt", +1, 0.1, 2],
    ["Speed lost at the bottom", "loss_pct", "%", -1, 3, 0],
    [`Lowest point, after ${W.mid}`, "t_min", " s", 0, 1, 0],
    ["Back to 95% of entry", "t_rec", " s", -1, 2, 0],
    ["Turn time (10→90% of the turn)", "turn_s", " s", 0, 1, 0],
    ["Overshoot past the new heading", "overshoot", "°", -1, 2, 0],
    ["Heel at +10 s", "heel_at10", "°", TACK ? +1 : 0, 1.5, 1],
    [`Heel settled on the new ${W.one}`, "heel_post", "°", 0, 1.5, 1],
    [`${W.Angle}, compass`, "hdg_angle", "°", 0, 2, 1],
    [`${W.Angle}, over the ground`, "cog_angle", "°", -1, 2, 1],
  ];

  function wellDone(v) {
    const T = v.ranked.filter(d => v.top.has(d.id)), R = v.ranked.filter(d => !v.top.has(d.id));
    const rows = CMP.map(([lab, k, u, dir, thr, dp]) => {
      const a = med(T.map(d => d[k])), b = med(R.map(d => d[k]));
      return { lab, k, u, dir, thr, dp, a, b, d: a == null || b == null ? null : a - b };
    });
    const fmt = (x, dp) => (x == null ? "–" : x.toFixed(dp));
    $("cmp").innerHTML =
      `<thead><tr><th>Median</th><th>Best ${S.pct}% (${T.length})</th><th>Rest (${R.length})</th><th>Difference</th></tr></thead><tbody>`
      + rows.map(r => {
        const good = r.d != null && r.dir !== 0 && Math.abs(r.d) >= r.thr && Math.sign(r.d) === r.dir;
        return `<tr><td>${r.lab}</td><td class="${good ? "better" : ""}">${fmt(r.a, r.dp)}${r.u}</td><td>${fmt(r.b, r.dp)}${r.u}</td><td>${r.d == null ? "–" : (r.d > 0 ? "+" : "") + r.d.toFixed(r.dp) + r.u}</td></tr>`;
      }).join("") + "</tbody>";

    if (!R.length) { $("well").innerHTML = `<p class='tk-note'>Not enough ${W.many} in view to compare.</p>`; return; }
    const g = k => rows.find(r => r.k === k);
    const out = [];
    const L = g("loss_pct"), M = g("min"), E = g("entry"), Rc = g("t_rec"), H = g("heel_at10"), O = g("overshoot"),
      Tn = g("turn_s"), C = g("cog_angle"), TL = g("secs_lost"), ML = g("m_lost");
    // The result first: what the best ones cost toward the mark. Everything after explains it.
    if (TL.a != null && TL.b != null)
      out.push(`<b>${HAS_VMC ? "Cost toward the mark" : "Cost"}: ${f1(TL.a)} s against ${f1(TL.b)} s for the rest</b>` +
        (ML.a != null ? ` (${f0(ML.a)} m against ${f0(ML.b)} m${HAS_VMC ? ": the dip in VMC through the turn, against a line from the VMC going in to the VMC once settled. What the change of tack itself gained or lost is the call, in the table" : ""}).` : ".") +
        (HAS_VMC ? "" : " No marks in this data, so this is measured in speed, not progress to the mark."));
    if (L.d != null && L.d <= -3) out.push(`<b>Kept more speed through the turn.</b> They lost ${f0(L.a)}% at the bottom against ${f0(L.b)}% for the rest (lowest ${M.a.toFixed(2)} kt against ${M.b.toFixed(2)} kt).`);
    if (E.d != null && E.d >= 0.1) out.push(`<b>Went in faster.</b> Entry speed was ${E.a.toFixed(2)} kt against ${E.b.toFixed(2)} kt: build speed before you put the helm down.`);
    else if (E.d != null && E.d < 0.1) out.push(`<b>Not just a faster entry.</b> Entry speed was no higher (${E.a.toFixed(2)} against ${E.b.toFixed(2)} kt), so the gain is in the turn and the exit.`);
    if (Rc.d != null && Rc.d <= -2) out.push(`<b>Back up to speed sooner.</b> They were back to 95% at ${f0(Rc.a)} s after ${W.mid}, against ${f0(Rc.b)} s.`);
    if (TACK && H.d != null && H.d >= 1.5) out.push(`<b>Loaded the boat up quickly on the new tack.</b> Heel at +10 s was ${f1(H.a)}° against ${f1(H.b)}°: the sails were trimmed and the crew was hiking early.`);
    if (O.d != null && O.d <= -2) out.push(TACK
      ? `<b>Less overshoot.</b> They turned ${f1(O.a)}° past the new heading against ${f1(O.b)}°. Stop the turn on the new close-hauled course instead of sailing low to build speed.`
      : `<b>Less overshoot.</b> They turned ${f1(O.a)}° past the new heading against ${f1(O.b)}°. Stop the turn on the new downwind angle instead of rounding up past it.`);
    if (O.d != null && O.d >= 2) out.push(TACK
      ? `<b>Came out a little low, then climbed.</b> They overshot by ${f1(O.a)}° against ${f1(O.b)}°: they built speed low before coming up to course.`
      : `<b>Came out a little hot, then bore away.</b> They overshot by ${f1(O.a)}° against ${f1(O.b)}°: they carried speed by heading up out of the gybe before settling deep.`);
    if (Tn.d != null && Math.abs(Tn.d) >= 1) out.push(`<b>Turn rate:</b> they took ${f0(Tn.a)} s from 10% to 90% of the turn against ${f0(Tn.b)} s (${Tn.d < 0 ? "a quicker" : "a slower, more rolled"} turn).`);
    if (C.d != null && C.d <= -2) out.push(`<b>Tighter over the ground.</b> The ${W.angle} over the ground was ${f1(C.a)}° against ${f1(C.b)}°.`);
    const onto = { Port: T.filter(d => d.onto === "Port").length, Stbd: T.filter(d => d.onto === "Stbd").length };
    if (S.onto === "All" && T.length >= 3 && (onto.Port === 0 || onto.Stbd === 0))
      out.push(`<b>All onto ${onto.Port ? "port" : "starboard"}.</b> Filter by "Onto" to see whether the other side has a handling issue.`);
    out.push(`<span class="tk-note">Medians of ${T.length} highlighted against ${R.length} other ${W.many}. With this few ${W.many}, treat gaps as patterns to check on the water, not proof. ${TACK ? "A puff or lift on the new tack also makes a tack look good." : "A puff on the new gybe, or a gybe that sails a different angle, also changes speed over the ground."}</span>`);
    $("well").innerHTML = "<ul>" + out.map(s => `<li>${s}</li>`).join("") + "</ul>";
  }

  function table(v) {
    const cols = [
      ["#", d => d.rank],
      [TACK ? "Tack" : "Gybe", d => `<i class="tk-dot" style="background:var(${boatVar(d.boat)})"></i>${d.id} ${v.top.has(d.id) ? '<span class="tk-badge">best ' + S.pct + "%</span>" : ""}`],
      ["Onto", d => d.onto], ["Time lost (s)", d => f1(d.secs_lost)], ["Metres", d => f0(d.m_lost)],
      ...(HAS_VMC ? [["VMC in → out kt", d => (d.vmc_in == null ? "–" : d.vmc_in.toFixed(2) + " → " + (d.vmc_out == null ? "–" : d.vmc_out.toFixed(2)))],
        ["Call m", d => f0(d.call_m)]] : []),
      ["Entry kt", d => d.entry.toFixed(2)], ["Lowest kt", d => d.min.toFixed(2)], ["Lost %", d => f0(d.loss_pct)],
      ["Back to 95% (s)", d => f0(d.t_rec)],
      ["Angle compass °", d => f0(d.hdg_angle)], ["Angle ground °", d => f0(d.cog_angle)], ["Turn s", d => f0(d.turn_s)],
      ["Overshoot °", d => f0(d.overshoot)], ["Heel +10 s °", d => f0(d.heel_at10)],
    ];
    $("tbl").innerHTML = `<thead><tr>${cols.map(c => `<th>${c[0]}</th>`).join("")}</tr></thead><tbody>`
      + v.ranked.map(d => `<tr data-id="${d.id}" class="${S.sel === d.id ? "sel" : ""}">${cols.map(c => `<td>${c[1](d)}</td>`).join("")}</tr>`).join("")
      + "</tbody>";
    $("tbl").querySelectorAll("tbody tr").forEach(tr => (tr.onclick = () => {
      S.sel = S.sel === tr.dataset.id ? null : tr.dataset.id;
      render();
      $("sog").scrollIntoView({ behavior: "smooth", block: "center" });
    }));
    $("excl").textContent = DATA.note || "";
  }

  function render() {
    // Plotly can't size a chart inside a hidden page: wait until it's shown
    if (!root.offsetParent) return;
    const races = ["All", ...[...new Set(DATA.items.map(d => d.race))].sort()];
    if (boatsHere.length > 1) seg("f-boat", ["All", ...boatsHere], "boat");
    else $("f-boat").parentElement.hidden = true;
    seg("f-race", races, "race", r => r.replace("Race ", "R"));
    seg("f-onto", ["All", "Port", "Stbd"], "onto");
    seg("f-pct", [10, 20, 25], "pct", p => p + "%");
    seg("f-angle", ["cog", "hdg"], "angle", a => (a === "cog" ? "Over the ground" : "Compass"));
    seg("f-speed", HAS_VMC ? ["vmc", "sog"] : ["sog"], "speed", k => (k === "vmc" ? "VMC (to the mark)" : "SOG"));
    $("speed-note").textContent = S.speed === "vmc"
      ? `VMC: speed toward the mark, 1 Hz (5 s average). This is what the ${W.one} is scored on. The dashed line is the median of every ${W.one} in view.`
      : `SOG, 1 Hz. Entry speed is the average from −10 to −4 s. SOG explains the ${W.one} (how much speed it lost and how fast it came back); VMC scores it. Speed over the ground includes current and any puff or lull.`;
    const v = view();
    const thin = $("thin");
    thin.hidden = v.ranked.length >= 10;
    thin.textContent = `Only ${v.ranked.length} ${v.ranked.length === 1 ? W.one : W.many} in view: too few to call a pattern. Use this to look at each one rather than to rank them.`;
    legends(v);
    tiles(v);
    overlay("sog", S.speed, S.speed === "vmc" ? "VMC to the mark (kt)" : "SOG (kt)", v);
    overlay("hdg", "hdg", "degrees turned", v);
    scatter(v);
    wellDone(v);
    table(v);
  }

  render();
  // In a report the page may only become visible after its own nav script runs
  window.addEventListener("hashchange", () => setTimeout(render, 0));
  document.addEventListener("DOMContentLoaded", () => setTimeout(render, 0));
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", render);
  new MutationObserver(render).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  return { render };
}
