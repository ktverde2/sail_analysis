// 3D race replay (replay3d.py builds the data). World frame: x east, y up, z south,
// in metres from the venue's origin; the data's (x, y) are east and north.
// Replay3D.mount(host, data) draws it inside `host`, from the page's <template id="r3-template">,
// in a shadow root so its styles and ids don't mix with the page around it.
window.Replay3D = { mount: function (host, D) {
  'use strict';
  const root = host.attachShadow({ mode: 'open' });
  root.appendChild(document.getElementById('r3-template').content.cloneNode(true));
  const $ = (s) => root.querySelector(s);
  $('#title').textContent = D.title;
  let active = true, running = false;
  const RAD = Math.PI / 180;
  const wrap = (a) => ((((a + 180) % 360) + 360) % 360) - 180;
  const clamp = (v, a, b) => Math.min(b, Math.max(a, v));
  const V3 = THREE.Vector3;
  const W = (x, y, h) => new V3(x, h || 0, -y);
  const mmss = (s) => { const a = Math.abs(Math.round(s)); return Math.floor(a / 60) + ':' + String(a % 60).padStart(2, '0'); };
  const ord = (n) => n + (['th', 'st', 'nd', 'rd'][(n % 100 - 20) % 10] || ['th', 'st', 'nd', 'rd'][n % 100] || 'th');

  const BOAT = { L: 9.3, B: 2.12, mastZ: -1.05, deck: 0.62, boomY: 1.35, boomL: 3.6, headY: 10.6, houndsY: 8.2 };
  const RATES = [1, 2, 5, 10, 30];
  const TAILS = [[30, '30 s'], [120, '2 min'], [600, '10 min'], [1e6, 'All']];
  const TWS_KT = 10; // for the sails' apparent wind only; there's no wind sensor

  // ---------- state ----------
  const S = {
    race: 0, t: -180, playing: false, rate: 5, follow: 0, cam: 'chase', tail: 120,
    labels: true, ladder: false, trueSize: false, streaks: true,
  };
  const cam = { target: new V3(), pan: new V3(), r: 42, az: 25, el: 13, yawS: null, ease: 0 };

  // ---------- renderer and scene ----------
  const canvas = $('#view');
  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({ canvas, antialias: true, powerPreference: 'high-performance' });
  } catch (e) {
    $('#err').style.display = 'flex';
    $('#err').textContent = 'This browser could not start 3D graphics (WebGL). Try a recent Chrome, Safari, Edge or Firefox.';
    return;
  }
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
  renderer.outputColorSpace = THREE.SRGBColorSpace;
  const scene = new THREE.Scene();
  const HORIZON = new THREE.Color(0xd6e4ef), ZENITH = new THREE.Color(0x3f7fc4);
  scene.fog = new THREE.Fog(HORIZON, 3000, 16000);
  const camera = new THREE.PerspectiveCamera(50, 1, 0.3, 60000);
  const SUN = new V3(-0.25, 0.72, 0.65).normalize(); // high, to the south-southwest: midday in autumn

  scene.add(new THREE.HemisphereLight(0xe6f0ff, 0x5d7486, 1.6));
  const sunLight = new THREE.DirectionalLight(0xfff4e2, 2.4);
  sunLight.position.copy(SUN).multiplyScalar(100);
  scene.add(sunLight);

  // sky dome, kept centred on the camera
  const sky = new THREE.Mesh(new THREE.SphereGeometry(30000, 32, 16), new THREE.ShaderMaterial({
    side: THREE.BackSide, depthWrite: false, fog: false,
    uniforms: { top: { value: ZENITH }, horizon: { value: HORIZON }, sun: { value: SUN } },
    vertexShader: 'varying vec3 vDir; void main(){ vDir = normalize(position); gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); gl_Position.z = gl_Position.w; }',
    fragmentShader: `uniform vec3 top, horizon, sun; varying vec3 vDir;
      void main(){ vec3 d = normalize(vDir); float h = max(d.y, 0.0);
        vec3 c = mix(horizon, top, pow(h, 0.5));
        float s = max(dot(d, sun), 0.0); c += vec3(1.0, 0.95, 0.85) * (pow(s, 900.0) * 2.0 + pow(s, 10.0) * 0.12);
        gl_FragColor = vec4(c, 1.0);
        #include <colorspace_fragment>
      }`,
  }));
  sky.renderOrder = -1;
  scene.add(sky);

  // ---------- terrain: shoreline and depth ----------
  const T = D.terrain;
  let depthTex = null, terrBox = new THREE.Vector4(0, 0, 1, 1);
  if (T) {
    const dec = (b64) => Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
    const land = dec(T.land), depth = dec(T.depth);
    const nx = T.nx, ny = T.ny, dx = T.dx;
    // depth (and land as zero depth) for the water's colour
    depthTex = new THREE.DataTexture(depth, nx, ny, THREE.RedFormat, THREE.UnsignedByteType);
    depthTex.magFilter = depthTex.minFilter = THREE.LinearFilter;
    depthTex.needsUpdate = true;
    // texture u runs east, v north; world z is south
    terrBox.set(T.x0 - dx / 2, T.y0 - dx / 2, nx * dx, ny * dx);
    // land surface: a heightfield whose zero contour is the shoreline
    const pos = new Float32Array(nx * ny * 3), col = new Float32Array(nx * ny * 3);
    const sand = new THREE.Color(0xd9cba5), scrub = new THREE.Color(0xaeb08e), town = new THREE.Color(0xc9c3b6), c = new THREE.Color();
    const hash = (i, j) => { const s = Math.sin(i * 127.1 + j * 311.7) * 43758.5453; return s - Math.floor(s); };
    // smooth value noise, so the land's colour varies in soft patches
    const vnoise = (x, y) => { const i = Math.floor(x), j = Math.floor(y), u = x - i, v = y - j, su = u * u * (3 - 2 * u), sv = v * v * (3 - 2 * v);
      return (hash(i, j) * (1 - su) + hash(i + 1, j) * su) * (1 - sv) + (hash(i, j + 1) * (1 - su) + hash(i + 1, j + 1) * su) * sv; };
    for (let j = 0; j < ny; j++) for (let i = 0; i < nx; i++) {
      const k = j * nx + i, f = land[k] / 255;
      // a little coastal rise, then flat (the grid has no land heights)
      let inl = 0;
      if (f > 0.9) { for (const [a, b] of [[-2, 0], [2, 0], [0, -2], [0, 2]]) { const ii = clamp(i + a, 0, nx - 1), jj = clamp(j + b, 0, ny - 1); inl += land[jj * nx + ii] / 255; } inl /= 4; }
      const h = -2.6 + 6.5 * f + (inl > 0.97 ? 1.2 : 0);
      pos[k * 3] = T.x0 + i * dx; pos[k * 3 + 1] = h; pos[k * 3 + 2] = -(T.y0 + j * dx);
      const n = vnoise(i / 14, j / 14), n2 = vnoise(i / 3, j / 3);
      c.copy(town).lerp(scrub, clamp((n - 0.35) * 2, 0, 1));
      c.copy(sand).lerp(c, clamp((f - 0.5) * 3, 0, 1)).multiplyScalar(0.95 + 0.07 * n2);
      col[k * 3] = c.r; col[k * 3 + 1] = c.g; col[k * 3 + 2] = c.b;
    }
    const idx = [];
    for (let j = 0; j < ny - 1; j++) for (let i = 0; i < nx - 1; i++) {
      const a = j * nx + i, b = a + 1, d = a + nx, e = d + 1;
      if (land[a] > 40 || land[b] > 40 || land[d] > 40 || land[e] > 40) idx.push(a, b, d, b, e, d);
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    g.setAttribute('color', new THREE.BufferAttribute(col, 3));
    g.setIndex(idx);
    g.computeVertexNormals();
    scene.add(new THREE.Mesh(g, new THREE.MeshLambertMaterial({ vertexColors: true })));
  }

  // ---------- water ----------
  const waterU = THREE.UniformsUtils.merge([THREE.UniformsLib.fog, {
    uTime: { value: 0 }, uSun: { value: SUN }, uWind: { value: new THREE.Vector2(0, 1) },
    uDeep: { value: new THREE.Color(0x1d4f6e) }, uShallow: { value: new THREE.Color(0x3d8a8c) },
    uSky: { value: ZENITH.clone() }, uHorizon: { value: HORIZON.clone() },
    uDepth: { value: depthTex }, uTerr: { value: terrBox }, uHasTerr: { value: T ? 1 : 0 },
  }]);
  const water = new THREE.Mesh(new THREE.PlaneGeometry(80000, 80000), new THREE.ShaderMaterial({
    uniforms: waterU, fog: true,
    vertexShader: `varying vec3 vWorld;
      #include <fog_pars_vertex>
      void main(){ vec4 wp = modelMatrix * vec4(position, 1.0); vWorld = wp.xyz; vec4 mvPosition = viewMatrix * wp; gl_Position = projectionMatrix * mvPosition;
        #include <fog_vertex>
      }`,
    fragmentShader: `precision highp float;
      uniform float uTime, uHasTerr; uniform vec3 uSun, uDeep, uShallow, uSky, uHorizon; uniform vec2 uWind;
      uniform sampler2D uDepth; uniform vec4 uTerr; varying vec3 vWorld;
      #include <fog_pars_fragment>
      float hash(vec2 p){ vec3 p3 = fract(vec3(p.xyx) * 0.1031); p3 += dot(p3, p3.yzx + 33.33); return fract((p3.x + p3.y) * p3.z); }
      float noise(vec2 p){ vec2 i = floor(p), f = fract(p); vec2 u = f * f * (3.0 - 2.0 * f);
        return mix(mix(hash(i), hash(i + vec2(1.0, 0.0)), u.x), mix(hash(i + vec2(0.0, 1.0)), hash(i + vec2(1.0, 1.0)), u.x), u.y); }
      float waves(vec2 p){ float h = 0.0, a = 0.5; mat2 r = mat2(0.8, -0.6, 0.6, 0.8);
        for (int i = 0; i < 4; i++){ h += a * noise(p - uWind * uTime * (0.9 + 0.35 * float(i))); p = r * p * 2.03; a *= 0.5; } return h; }
      void main(){
        vec3 V = cameraPosition - vWorld; float dist = length(V); V /= dist;
        vec2 p = vWorld.xz * 0.22; float e = 0.06;
        float amp = 0.16 * clamp(1.0 - dist / 350.0, 0.0, 1.0);
        float h0 = waves(p), hx = waves(p + vec2(e, 0.0)), hz = waves(p + vec2(0.0, e));
        vec3 N = normalize(vec3(-(hx - h0) / e * amp, 1.0, -(hz - h0) / e * amp));
        float depth = 15.0;
        if (uHasTerr > 0.5){ vec2 uv = vec2(vWorld.x - uTerr.x, -vWorld.z - uTerr.y) / uTerr.zw;
          if (uv.x > 0.0 && uv.y > 0.0 && uv.x < 1.0 && uv.y < 1.0) depth = texture2D(uDepth, uv).r * 63.75; }
        vec3 base = mix(uShallow, uDeep, smoothstep(1.0, 10.0, depth));
        float fres = 0.02 + 0.98 * pow(1.0 - max(dot(N, V), 0.0), 5.0);
        vec3 R = reflect(-V, N);
        vec3 skyc = mix(uHorizon, uSky, clamp(R.y * 1.6, 0.0, 1.0));
        vec3 c = mix(base, skyc, clamp(fres, 0.0, 0.85));
        c += vec3(1.0, 0.96, 0.88) * pow(max(dot(R, uSun), 0.0), mix(4000.0, 150.0, amp / 0.16)) * 0.25;
        gl_FragColor = vec4(c, 1.0);
        #include <fog_fragment>
        #include <colorspace_fragment>
      }`,
  }));
  water.rotation.x = -Math.PI / 2;
  scene.add(water);

  // ---------- screen-space wide lines (trails, start and finish lines, ladder) ----------
  const lineVS = `attribute vec3 prev; attribute vec3 next; attribute float side; attribute float tt;
    uniform vec2 uRes; uniform float uWidth, uNow, uTail; varying float vA;
    void main(){
      mat4 m = projectionMatrix * modelViewMatrix;
      vec4 c = m * vec4(position, 1.0), p = m * vec4(prev, 1.0), n = m * vec4(next, 1.0);
      // a point behind the camera is dropped (its segments would wrap round the screen)
      if (c.w <= 0.0) { gl_Position = vec4(2.0, 2.0, 2.0, 1.0); vA = 0.0; return; }
      vec2 cs = c.xy / c.w * uRes, ps = p.xy / p.w * uRes, ns = n.xy / n.w * uRes;
      if (p.w <= 0.0) ps = 2.0 * cs - ns;
      if (n.w <= 0.0) ns = 2.0 * cs - ps;
      vec2 d = ns - ps; float l = length(d); d = l > 1e-4 ? d / l : vec2(1.0, 0.0);
      vec2 nrm = vec2(-d.y, d.x);
      c.xy += nrm * side * uWidth / uRes * c.w;
      float age = uNow - tt;
      vA = tt < -1e8 ? 1.0 : (age < 0.0 ? 0.0 : clamp(1.0 - age / uTail, 0.0, 1.0));
      gl_Position = c;
    }`;
  const lineFS = `uniform vec3 uColor; uniform float uOpacity; varying float vA;
    void main(){ float a = vA * uOpacity; if (a < 0.01) discard; gl_FragColor = vec4(uColor, a);
      #include <colorspace_fragment>
    }`;
  function wideLine(polys, color, width, opacity) {
    // polys: arrays of [x, y, h, t] (t undefined: always shown)
    let n = 0; for (const p of polys) n += p.length;
    const pos = new Float32Array(n * 6), prv = new Float32Array(n * 6), nxt = new Float32Array(n * 6);
    const side = new Float32Array(n * 2), tt = new Float32Array(n * 2), idx = [];
    let k = 0;
    for (const p of polys) {
      const base = k;
      for (let i = 0; i < p.length; i++, k++) {
        const a = p[Math.max(i - 1, 0)], b = p[i], c = p[Math.min(i + 1, p.length - 1)];
        for (let s = 0; s < 2; s++) {
          const o = (k * 2 + s) * 3;
          pos[o] = b[0]; pos[o + 1] = b[2]; pos[o + 2] = -b[1];
          prv[o] = a[0]; prv[o + 1] = a[2]; prv[o + 2] = -a[1];
          nxt[o] = c[0]; nxt[o + 1] = c[2]; nxt[o + 2] = -c[1];
          side[k * 2 + s] = s ? 1 : -1;
          tt[k * 2 + s] = b[3] === undefined ? -1e9 : b[3];
        }
        if (i < p.length - 1) { const v = (base + i) * 2; idx.push(v, v + 1, v + 2, v + 1, v + 3, v + 2); }
      }
    }
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
    g.setAttribute('prev', new THREE.BufferAttribute(prv, 3));
    g.setAttribute('next', new THREE.BufferAttribute(nxt, 3));
    g.setAttribute('side', new THREE.BufferAttribute(side, 1));
    g.setAttribute('tt', new THREE.BufferAttribute(tt, 1));
    g.setIndex(idx);
    const mat = new THREE.ShaderMaterial({
      vertexShader: lineVS, fragmentShader: lineFS, transparent: true, depthWrite: false, side: THREE.DoubleSide,
      uniforms: { uRes: { value: res }, uWidth: { value: width }, uNow: { value: 0 }, uTail: { value: 120 },
        uColor: { value: new THREE.Color(color) }, uOpacity: { value: opacity } },
    });
    const m = new THREE.Mesh(g, mat);
    m.frustumCulled = false;
    return m;
  }
  const res = new THREE.Vector2(1, 1);

  // ---------- boat model ----------
  function hullGeometry() {
    const { L, B } = BOAT, NS = 30, NR = 13;
    const hb = (u) => Math.max(0.015, (B / 2) * (1 - Math.pow((u - 0.42) / (u < 0.42 ? 0.66 : 0.58), 2)));
    const sheer = (u) => 0.6 + 0.9 * (u - 0.45) * (u - 0.45);
    const bottom = (u) => 0.28 - 0.72 * Math.pow(Math.sin(Math.PI * clamp(u * 1.02, 0, 1)), 0.8);
    const pos = [], idx = [];
    for (let s = 0; s <= NS; s++) {
      const u = s / NS, z = L / 2 - u * L, b = hb(u), top = sheer(u), bot = Math.min(bottom(u), top - 0.05);
      for (let r = 0; r < NR; r++) {
        const ph = (r / (NR - 1)) * Math.PI, cs = Math.cos(ph);
        pos.push(-Math.sign(cs) * Math.pow(Math.abs(cs), 0.55) * b, top - (top - bot) * Math.pow(Math.sin(ph), 0.7), z);
      }
    }
    for (let s = 0; s < NS; s++) for (let r = 0; r < NR - 1; r++) {
      const a = s * NR + r, b = a + 1, c = a + NR, d = c + 1;
      idx.push(a, b, c, b, d, c);
    }
    // transom
    const tc = pos.length / 3; pos.push(0, (sheer(0) + bottom(0)) / 2, L / 2);
    for (let r = 0; r < NR - 1; r++) idx.push(tc, r + 1, r);
    const g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.Float32BufferAttribute(pos, 3));
    g.setIndex(idx); g.computeVertexNormals();
    // deck: a cambered strip between the sheer lines
    const dp = [], di = [];
    for (let s = 0; s <= NS; s++) {
      const u = s / NS, z = L / 2 - u * L, b = hb(u), top = sheer(u);
      dp.push(-b, top, z, 0, top + 0.06 * Math.min(1, b * 2), z, b, top, z);
    }
    for (let s = 0; s < NS; s++) for (let r = 0; r < 2; r++) {
      const a = s * 3 + r, c = a + 3;
      di.push(a, c, a + 1, a + 1, c, c + 1);
    }
    const dg = new THREE.BufferGeometry();
    dg.setAttribute('position', new THREE.Float32BufferAttribute(dp, 3));
    dg.setIndex(di); dg.computeVertexNormals();
    return { hull: g, deck: dg };
  }
  const HULL = hullGeometry();
  const SAIL_N = [7, 9]; // chord x height
  function sailMesh(color, opacity) {
    const [nc, nh] = SAIL_N, g = new THREE.BufferGeometry();
    g.setAttribute('position', new THREE.BufferAttribute(new Float32Array(nc * nh * 3), 3));
    const idx = [];
    for (let j = 0; j < nh - 1; j++) for (let i = 0; i < nc - 1; i++) {
      const a = j * nc + i; idx.push(a, a + 1, a + nc, a + 1, a + nc + 1, a + nc);
    }
    g.setIndex(idx);
    const m = new THREE.Mesh(g, new THREE.MeshStandardMaterial({ color, emissive: new THREE.Color(color).multiplyScalar(0.28), roughness: 0.8, side: THREE.DoubleSide, transparent: opacity < 1, opacity }));
    m.frustumCulled = false;
    return m;
  }
  const _a = new V3(), _b = new V3(), _c = new V3(), _d = new V3(), _p = new V3(), _e = new V3(), _f = new V3();
  // fill a sail between luff (tack -> head) and leech (clew -> head), cambered along `belly`
  function shapeSail(mesh, tack, head, clew, belly, depth, twistTop) {
    const [nc, nh] = SAIL_N, arr = mesh.geometry.attributes.position.array;
    for (let j = 0; j < nh; j++) {
      const v = j / (nh - 1);
      _e.copy(tack).lerp(head, v); // luff point
      _f.copy(clew).lerp(head, v); // leech point
      const chord = _e.distanceTo(_f);
      for (let i = 0; i < nc; i++) {
        const u = i / (nc - 1);
        _p.copy(_e).lerp(_f, u);
        const k = depth * chord * 4 * u * (1 - u) * (1 + 0.35 * (0.5 - u)) * (1 - 0.25 * v);
        _p.addScaledVector(belly, k);
        if (twistTop) _p.x += twistTop * v * v * u;
        const o = (j * nc + i) * 3; arr[o] = _p.x; arr[o + 1] = _p.y; arr[o + 2] = _p.z;
      }
    }
    mesh.geometry.attributes.position.needsUpdate = true;
    mesh.geometry.computeVertexNormals();
  }
  function shapeKite(mesh, head, tack, clew, bulge) {
    const [nc, nh] = SAIL_N, arr = mesh.geometry.attributes.position.array;
    for (let j = 0; j < nh; j++) {
      const v = j / (nh - 1);
      for (let i = 0; i < nc; i++) {
        const u = i / (nc - 1);
        _p.copy(tack).lerp(clew, u).lerp(head, v);
        const k = Math.sin(Math.PI * u) * Math.pow(Math.sin(Math.PI * Math.min(1, v * 0.9 + 0.08)), 0.7);
        _p.addScaledVector(bulge, k);
        _p.y += 0.6 * k;
        const o = (j * nc + i) * 3; arr[o] = _p.x; arr[o + 1] = _p.y; arr[o + 2] = _p.z;
      }
    }
    mesh.geometry.attributes.position.needsUpdate = true;
    mesh.geometry.computeVertexNormals();
  }
  function rod(len, r, color) {
    const m = new THREE.Mesh(new THREE.CylinderGeometry(r, r, len, 8), new THREE.MeshStandardMaterial({ color, roughness: 0.4, metalness: 0.5 }));
    return m;
  }
  function makeBoat(color) {
    const g = new THREE.Group(), body = new THREE.Group();
    g.add(body);
    body.rotation.order = 'YXZ';
    const hull = new THREE.Mesh(HULL.hull, new THREE.MeshStandardMaterial({ color, roughness: 0.32, side: THREE.DoubleSide }));
    const deck = new THREE.Mesh(HULL.deck, new THREE.MeshStandardMaterial({ color: 0xf1f1ec, roughness: 0.7, side: THREE.DoubleSide }));
    const cockpit = new THREE.Mesh(new THREE.BoxGeometry(1.15, 0.06, 3.0), new THREE.MeshStandardMaterial({ color: 0x55606a, roughness: 0.9 }));
    cockpit.position.set(0, BOAT.deck + 0.09, 2.15);
    const mast = rod(BOAT.headY - BOAT.deck, 0.055, 0xc9ced4);
    mast.position.set(0, (BOAT.headY + BOAT.deck) / 2, BOAT.mastZ);
    const boom = rod(BOAT.boomL, 0.045, 0xc9ced4);
    boom.rotation.x = Math.PI / 2;
    const boomPivot = new THREE.Group(); boomPivot.position.set(0, BOAT.boomY, BOAT.mastZ); boomPivot.add(boom);
    boom.position.set(0, 0, BOAT.boomL / 2);
    const main = sailMesh(0xf6f5f0, 0.96), jib = sailMesh(0xf6f5f0, 0.96);
    const kc = new THREE.Color(color).lerp(new THREE.Color(0xffffff), 0.3);
    const kite = sailMesh(kc, 0.97);
    const fs = new THREE.BufferGeometry().setFromPoints([new V3(0, 0.85, -BOAT.L / 2 + 0.2), new V3(0, BOAT.houndsY, BOAT.mastZ)]);
    const stay = new THREE.Line(fs, new THREE.LineBasicMaterial({ color: 0x8a939c }));
    body.add(hull, deck, cockpit, mast, boomPivot, main, jib, kite, stay);
    // a bright ring on the water so a boat is easy to find from far away
    const ring = new THREE.Mesh(new THREE.RingGeometry(5.2, 6.4, 40), new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.0, depthWrite: false }));
    ring.rotation.x = -Math.PI / 2; ring.position.y = 0.12;
    g.add(ring);
    return { g, body, main, jib, kite, boomPivot, ring, boomA: null, jibA: null, kiteOn: 0 };
  }
  function makeBuoy(color, h) {
    const g = new THREE.Group();
    const m = new THREE.MeshStandardMaterial({ color, roughness: 0.5 });
    const c = new THREE.Mesh(new THREE.CylinderGeometry(0.75, 0.85, h, 18), m);
    c.position.y = h / 2;
    const top = new THREE.Mesh(new THREE.SphereGeometry(0.75, 18, 10, 0, Math.PI * 2, 0, Math.PI / 2), m);
    top.position.y = h;
    g.add(c, top);
    return g;
  }
  function makeCommittee() {
    const g = new THREE.Group();
    const hull = new THREE.Mesh(new THREE.BoxGeometry(3.2, 1.2, 11), new THREE.MeshStandardMaterial({ color: 0xf2f2f2, roughness: 0.5 }));
    hull.position.y = 0.5;
    const cab = new THREE.Mesh(new THREE.BoxGeometry(2.4, 1.5, 4), new THREE.MeshStandardMaterial({ color: 0xdedede, roughness: 0.5 }));
    cab.position.set(0, 1.8, -0.5);
    const pole = rod(5, 0.05, 0x999999); pole.position.set(0, 4.5, 2.5);
    const flag = new THREE.Mesh(new THREE.PlaneGeometry(1.4, 0.9), new THREE.MeshStandardMaterial({ color: 0xff7a1a, side: THREE.DoubleSide }));
    flag.position.set(0, 6.4, 3.2); flag.rotation.y = Math.PI / 2;
    g.add(hull, cab, pole, flag);
    return g;
  }

  // ---------- per-race objects ----------
  let R = null; // current race
  const raceGroup = new THREE.Group(); scene.add(raceGroup);
  const boatObjs = D.boats.map((b) => makeBoat(b.color));
  boatObjs.forEach((o) => scene.add(o.g));
  let trails = [], marks = [], ladderMesh = null, ladderKey = '';
  const labelsEl = $('#labels');
  const boatLabels = D.boats.map((b, i) => {
    const el = document.createElement('div');
    el.className = 'lbl'; el.style.color = b.color;
    el.innerHTML = '<span style="color:#fff">' + b.name + '</span><small></small>';
    el.addEventListener('click', (e) => { e.stopPropagation(); setFollow(i); });
    labelsEl.appendChild(el);
    return el;
  });
  let markLabels = [];

  function legTarget(race, leg) {
    const c = race.course[leg];
    if (!c) return null;
    const p = c.pts; let x = 0, y = 0;
    for (const q of p) { x += q[0]; y += q[1]; }
    return [x / p.length, y / p.length];
  }
  function startLine(race) {
    const c = race.course.find((e) => e.type === 'StartLine');
    if (!c || c.pts.length < 2) return null;
    // pin is the left end looking toward the first mark
    const m = legTarget(race, 1) || [0, 1e4];
    const [a, b] = c.pts, mx = (a[0] + b[0]) / 2, my = (a[1] + b[1]) / 2;
    const cr = (b[0] - a[0]) * (m[1] - my) - (b[1] - a[1]) * (m[0] - mx);
    const pin = cr > 0 ? a : b, boat = cr > 0 ? b : a;
    return { pin, boat, mx, my, nx: -(boat[1] - pin[1]), ny: boat[0] - pin[0], len: Math.hypot(boat[0] - pin[0], boat[1] - pin[1]) };
  }

  function buildRace(i) {
    S.race = i; R = D.races[i];
    while (raceGroup.children.length) raceGroup.remove(raceGroup.children[0]);
    markLabels.forEach((m) => m.el.remove()); markLabels = [];
    trails = D.boats.map((b) => {
      const d = R.boats[b.name];
      if (!d) return null;
      const pts = d.x.map((x, k) => [x, d.y[k], 0.25, d.t0 + k]);
      const m = wideLine([pts], b.color, 2.6, 0.95);
      raceGroup.add(m);
      return m;
    });
    marks = [];
    const sl = startLine(R);
    const addLbl = (text, p, h) => {
      const near = markLabels.find((m) => Math.hypot(m.p.x - p[0], m.p.z + p[1]) < 40);
      if (near) { near.el.textContent += ' · ' + text.replace(/^(\w+) (\d+)$/, (m0, w, n) => near.el.textContent.startsWith(w) ? n : m0); return; }
      const el = document.createElement('div'); el.className = 'lbl mark'; el.textContent = text;
      labelsEl.appendChild(el); markLabels.push({ el, p: W(p[0], p[1], h) });
    };
    R.course.forEach((c, k) => {
      const name = c.type === 'StartLine' ? 'Start' : (R.mark_names[k - 1] || c.type);
      if (c.type === 'StartLine' && sl) {
        const pin = makeBuoy(0xff7a1a, 1.4); pin.position.copy(W(sl.pin[0], sl.pin[1]));
        const cb = makeCommittee(); cb.position.copy(W(sl.boat[0], sl.boat[1]));
        raceGroup.add(pin, cb); marks.push({ obj: pin, h: 1.4 }, { obj: cb, h: 2, committee: true });
        raceGroup.add(wideLine([[[sl.pin[0], sl.pin[1], 0.2], [sl.boat[0], sl.boat[1], 0.2]]], 0xffffff, 2.0, 0.9));
        addLbl('Pin', sl.pin, 3); addLbl('Start', [sl.mx, sl.my], 1);
        return;
      }
      const line = c.type === 'FinishLine';
      for (const p of c.pts) {
        const b = makeBuoy(line ? 0x2f6fe0 : 0xff7a1a, line ? 1.4 : 1.9);
        b.position.copy(W(p[0], p[1]));
        raceGroup.add(b); marks.push({ obj: b, h: 1.9 });
      }
      if (line && c.pts.length > 1) raceGroup.add(wideLine([c.pts.map((p) => [p[0], p[1], 0.2])], 0x9cc2ff, 2.0, 0.9));
      const t = legTarget(R, k);
      addLbl(name, t, 4);
    });
    ladderKey = '';
    // timeline
    let t0 = Infinity, t1 = -Infinity;
    for (const b of D.boats) { const d = R.boats[b.name]; if (d) { t0 = Math.min(t0, d.t0); t1 = Math.max(t1, d.t0 + d.x.length - 1); } }
    R.t0 = t0; R.t1 = t1;
    const tl = $('#time'); tl.min = t0; tl.max = t1;
    S.t = clamp(-180, t0, t1);
    drawTicks();
    root.querySelectorAll('#races button').forEach((b, k) => b.classList.toggle('on', k === i));
    cam.yawS = null;
  }

  function drawTicks() {
    const el = $('#ticks'); el.innerHTML = '';
    const span = R.t1 - R.t0, add = (t, cls, txt) => {
      const d = document.createElement('div'); d.className = 'tick ' + (cls || '');
      d.style.left = (100 * (t - R.t0) / span) + '%';
      if (txt) d.innerHTML = '<i>' + txt + '</i>';
      el.appendChild(d);
    };
    add(0, 'gun', 'Gun');
    const b = D.boats[S.follow] || D.boats[0], d = R.boats[b.name];
    if (d) d.legs.forEach((l, k) => add(l[1], '', k === d.legs.length - 1 ? 'Finish' : (R.mark_names[k] || '').replace(/ \d+$/, '')));
  }

  // ---------- boat state at time t ----------
  function stateAt(d, t) {
    const n = d.x.length, f = t - d.t0;
    if (f < 0 || f > n - 1) return null;
    const i = Math.min(Math.floor(f), n - 2), u = f - i;
    const L = (a) => a[i] + (a[i + 1] - a[i]) * u;
    return { x: L(d.x), y: L(d.y), sog: L(d.sog), heel: L(d.heel), trim: L(d.trim), yaw: d.yaw[i] + wrap(d.yaw[i + 1] - d.yaw[i]) * u };
  }
  function windAt(t) {
    const w = R.wind;
    if (!w.length) return null;
    if (t <= w[0][0]) return w[0][1];
    for (let k = 1; k < w.length; k++) if (t <= w[k][0]) {
      const u = (t - w[k - 1][0]) / (w[k][0] - w[k - 1][0]);
      return (w[k - 1][1] + wrap(w[k][1] - w[k - 1][1]) * u + 360) % 360;
    }
    return w[w.length - 1][1];
  }
  function legAt(d, t) {
    if (t < 0) return 0;
    for (let k = 0; k < d.legs.length; k++) if (t < d.legs[k][1]) return k + 1;
    return d.legs.length ? -1 : 1; // -1: finished
  }

  // ---------- update boats ----------
  const _q = new V3();
  function updateBoat(o, d, t, twd, dt) {
    const s = d && stateAt(d, t);
    o.g.visible = !!s;
    if (!s) return null;
    o.g.position.copy(W(s.x, s.y, 0));
    o.body.rotation.set(clamp(s.trim, -8, 8) * 0.5 * RAD, -s.yaw * RAD, -s.heel * RAD);
    // sails: apparent wind from an assumed true wind speed and the boat's speed
    const twa = twd === null ? 40 : wrap(twd - s.yaw); // + wind from starboard
    const tw = TWS_KT, bs = s.sog, tr = Math.abs(twa) * RAD;
    const awa = Math.atan2(tw * Math.sin(tr), tw * Math.cos(tr) + bs) / RAD; // 0..180
    const side = twa >= 0 ? -1 : 1; // the boom goes to leeward: port (-x) with wind from starboard
    const leg = legAt(d, t), lt = leg > 0 ? d.legs[leg - 1] : null;
    const down = lt && lt[2] === 'downwind' && Math.abs(twa) > 100 && t - lt[0] > 15 && lt[1] - t > 12;
    o.kiteOn += ((down ? 1 : 0) - o.kiteOn) * Math.min(1, dt * 1.5);
    let boom = side * clamp(awa * 0.62 - 9, 3, 82);
    let jib = side * clamp(awa * 0.5 - 2, 8, 50);
    // swing smoothly through tacks and gybes rather than jumping
    const rate = 160 * dt;
    o.boomA = o.boomA === null ? boom : o.boomA + clamp(boom - o.boomA, -rate, rate);
    o.jibA = o.jibA === null ? jib : o.jibA + clamp(jib - o.jibA, -rate, rate);
    const bA = o.boomA * RAD, jA = o.jibA * RAD, bs2 = Math.sign(o.boomA) || side, js = Math.sign(o.jibA) || side;
    o.boomPivot.rotation.y = bA;  // positive turns the aft end of the boom to +x
    const mz = BOAT.mastZ;
    _a.set(0, BOAT.boomY + 0.05, mz);
    _b.set(0, BOAT.headY, mz);
    _c.set(Math.sin(bA) * BOAT.boomL, BOAT.boomY + 0.05, mz + Math.cos(bA) * BOAT.boomL);
    _d.set(Math.cos(bA) * bs2, 0, -Math.sin(bA) * bs2).normalize();
    shapeSail(o.main, _a, _b, _c, _d, 0.085, 0.45 * bs2);
    _a.set(0, 0.9, -BOAT.L / 2 + 0.25);
    _b.copy(_a).lerp(_q.set(0, BOAT.houndsY, mz), 0.9);
    _c.set(Math.sin(jA) * 2.9, 1.25, _a.z + Math.cos(jA) * 2.9);
    _d.set(Math.cos(jA) * js, 0, -Math.sin(jA) * js).normalize();
    shapeSail(o.jib, _a, _b, _c, _d, 0.1, 0);
    o.kite.visible = o.kiteOn > 0.05;
    if (o.kite.visible) {
      const k = o.kiteOn, pole = clamp(Math.abs(awa) - 85, 15, 80) * RAD, ws = -side; // pole to windward
      _b.set(0, 1.4 + (BOAT.houndsY + 0.4 - 1.4) * k, mz - 1.6);
      _a.set(ws * Math.sin(pole) * 2.9, 1.9, mz - Math.cos(pole) * 2.9 - 0.3);
      _c.set(side * 3.4, 2.3, mz - 1.4);
      _a.lerp(_q.set(0, 1.4, mz), 1 - k); _c.lerp(_q.set(0, 1.4, mz), 1 - k);
      // the kite fills forward and to leeward
      _d.set(side * 0.35, 0.1, -1).normalize().multiplyScalar(3.4 * k);
      shapeKite(o.kite, _b, _a, _c, _d);
    }
    return s;
  }

  // ---------- ladder for the followed boat's leg ----------
  function updateLadder(t) {
    const fb = D.boats[S.follow];
    const d = fb && R.boats[fb.name];
    let key = 'off';
    let leg = d ? legAt(d, t) : 0;
    if (S.ladder && d && leg > 0) key = S.race + ':' + leg;
    if (key === ladderKey) return;
    ladderKey = key;
    if (ladderMesh) { raceGroup.remove(ladderMesh); ladderMesh = null; }
    if (key === 'off') return;
    const lad = R.ladders[leg - 1], tgtE = R.course[leg];
    if (!tgtE) return;
    const up = lad ? lad.up : (windAt(d.legs[leg - 1][0]) || 0);
    const ux = Math.sin(up * RAD), uy = Math.cos(up * RAD), px = uy, py = -ux;
    const tg = legTarget(R, leg), polys = [];
    const downwind = lad && lad.type === 'downwind';
    for (let m = 100; m <= 2500; m += 100) {
      const cx = tg[0] - ux * m, cy = tg[1] - uy * m, hw = Math.max(150, m * 0.9);
      polys.push([[cx - px * hw, cy - py * hw, 0.18], [cx + px * hw, cy + py * hw, 0.18]]);
    }
    if (lad && lad.half) for (const q of tgtE.pts) for (const sgn of [-1, 1]) {
      const b = (up + 180 + sgn * lad.half) * RAD;
      polys.push([[q[0], q[1], 0.2], [q[0] + Math.sin(b) * 2600, q[1] + Math.cos(b) * 2600, 0.2]]);
    }
    const fine = polys.map(([p, q]) => { const n = Math.max(1, Math.ceil(Math.hypot(q[0] - p[0], q[1] - p[1]) / 25)), out = [];
      for (let i = 0; i <= n; i++) out.push([p[0] + (q[0] - p[0]) * i / n, p[1] + (q[1] - p[1]) * i / n, p[2]]); return out; });
    ladderMesh = wideLine(fine, downwind ? 0xd7f0ff : 0xffffff, 1.4, 0.4);
    raceGroup.add(ladderMesh);
  }

  // ---------- wind streaks on the water ----------
  const NST = 420;
  const stPos = new Float32Array(NST * 6), stCol = new Float32Array(NST * 8);
  const st = Array.from({ length: NST }, () => ({ x: 0, z: 0, life: Math.random(), v: 0.7 + Math.random() * 0.6, dead: true }));
  const stGeo = new THREE.BufferGeometry();
  stGeo.setAttribute('position', new THREE.BufferAttribute(stPos, 3));
  stGeo.setAttribute('color', new THREE.BufferAttribute(stCol, 4));
  const streaks = new THREE.LineSegments(stGeo, new THREE.LineBasicMaterial({ vertexColors: true, transparent: true, depthWrite: false }));
  streaks.frustumCulled = false;
  scene.add(streaks);
  function updateStreaks(dtw, twd) {
    streaks.visible = S.streaks && twd !== null;
    if (!streaks.visible) return;
    const to = (twd + 180) * RAD, wx = Math.sin(to), wz = -Math.cos(to);
    const Rr = clamp(cam.r * 1.8, 50, 4000), len = Rr * 0.035, sp = Rr * 0.05;
    const cx = cam.target.x, cz = cam.target.z, fade = clamp(1 - (cam.r - 150) / 300, 0, 1);
    for (let k = 0; k < NST; k++) {
      const s = st[k];
      s.life += dtw * 0.25 * s.v;
      s.x += wx * sp * s.v * dtw; s.z += wz * sp * s.v * dtw;
      if (s.dead || s.life > 1 || Math.hypot(s.x - cx, s.z - cz) > Rr) {
        const a = Math.random() * Math.PI * 2, r = Rr * Math.sqrt(Math.random());
        s.x = cx + Math.cos(a) * r; s.z = cz + Math.sin(a) * r; s.life = s.dead ? Math.random() : 0; s.dead = false;
      }
      const al = Math.sin(Math.PI * s.life) * 0.45 * fade, o = k * 6, c = k * 8;
      stPos[o] = s.x; stPos[o + 1] = 0.3; stPos[o + 2] = s.z;
      stPos[o + 3] = s.x + wx * len * s.v; stPos[o + 4] = 0.3; stPos[o + 5] = s.z + wz * len * s.v;
      stCol.fill(1, c, c + 8); stCol[c + 3] = 0; stCol[c + 7] = al;
    }
    stGeo.attributes.position.needsUpdate = true; stGeo.attributes.color.needsUpdate = true;
  }

  // ---------- camera ----------
  function boatPos(i, t) {
    const b = D.boats[i], d = b && R.boats[b.name], s = d && stateAt(d, clamp(t, d.t0, d.t0 + d.x.length - 1));
    return s ? { p: W(s.x, s.y, 0), yaw: s.yaw } : null;
  }
  function courseCentre() {
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const c of R.course) for (const p of c.pts) { x0 = Math.min(x0, p[0]); x1 = Math.max(x1, p[0]); y0 = Math.min(y0, p[1]); y1 = Math.max(y1, p[1]); }
    return { c: W((x0 + x1) / 2, (y0 + y1) / 2, 0), size: Math.max(x1 - x0, y1 - y0, 300) };
  }
  function setFollow(i) {
    S.follow = i; if (S.cam === 'free' || S.cam === 'course') S.cam = 'chase';
    cam.pan.set(0, 0, 0); cam.ease = 1; cam.yawS = null;
    if (cam.r > 400) cam.r = 70;
    drawTicks(); syncButtons();
  }
  function setCam(mode) {
    if (mode === 'course' || mode === 'top') {
      const cc = courseCentre(), twd = windAt(S.t) || 0;
      cam.target.copy(cc.c); cam.pan.set(0, 0, 0);
      cam.r = cc.size * (mode === 'top' ? 1.3 : 1.25); cam.el = mode === 'top' ? 89 : 45; cam.az = (twd + 180) % 360;
      // nudge the view downwind so the start isn't hidden behind the controls
      const dw = (twd + 180) * RAD; cam.target.x += Math.sin(dw) * cc.size * 0.12; cam.target.z -= Math.cos(dw) * cc.size * 0.12;
      S.cam = 'free'; cam.ease = 1;
    } else {
      if (S.cam === 'free') { cam.ease = 1; if (cam.r > 400) cam.r = 70; cam.el = Math.min(cam.el, 30); }
      if (mode === 'chase') cam.az = 25;
      if (mode === 'fixed') { const bp = boatPos(S.follow, S.t); cam.az = bp ? (bp.yaw + 180 + cam.az) % 360 : cam.az; }
      S.cam = mode;
    }
    syncButtons();
  }
  const _t = new V3();
  function updateCamera(dt) {
    let az = cam.az;
    if (S.cam !== 'free') {
      const bp = boatPos(S.follow, S.t);
      if (bp) {
        _t.copy(bp.p).add(cam.pan); _t.y = 2.5;
        if (cam.yawS === null) cam.yawS = bp.yaw;
        cam.yawS += wrap(bp.yaw - cam.yawS) * Math.min(1, dt * 1.6);
        if (S.cam === 'chase') az = cam.yawS + 180 + cam.az;
        if (cam.ease > 0) { cam.target.lerp(_t, Math.min(1, dt * 5)); cam.ease = cam.target.distanceTo(_t) > 0.5 ? 1 : 0; }
        else cam.target.copy(_t);
      }
    }
    const a = az * RAD, e = cam.el * RAD;
    camera.position.set(cam.target.x + Math.sin(a) * Math.cos(e) * cam.r, cam.target.y + Math.sin(e) * cam.r, cam.target.z - Math.cos(a) * Math.cos(e) * cam.r);
    if (camera.position.y < 1.2) camera.position.y = 1.2;
    camera.lookAt(cam.target);
    camera.updateMatrixWorld();
    camera.near = clamp(cam.r * 0.015, 0.3, 60);
    camera.far = 40000;
    camera.updateProjectionMatrix();
    scene.fog.near = Math.max(2500, cam.r * 2.2); scene.fog.far = scene.fog.near * 6;
    sky.position.copy(camera.position);
    return az;
  }

  // pointer: drag to orbit, right-drag / shift-drag / two fingers to pan, wheel / pinch to zoom
  const ptrs = new Map();
  let pinch0 = null;
  function panBy(dx, dy) {
    const a = camera.getWorldDirection(_q); a.y = 0; a.normalize();
    const right = new V3(-a.z, 0, a.x), k = cam.r * 0.0016 / Math.max(0.25, Math.sin(Math.max(cam.el, 12) * RAD) ** 0.3);
    const m = right.multiplyScalar(-dx * k).addScaledVector(a, dy * k * 1.4);
    if (S.cam === 'free') cam.target.add(m); else cam.pan.add(m);
  }
  canvas.addEventListener('contextmenu', (e) => e.preventDefault());
  canvas.addEventListener('pointerdown', (e) => {
    canvas.setPointerCapture(e.pointerId);
    ptrs.set(e.pointerId, { x: e.clientX, y: e.clientY, pan: e.button === 2 || e.shiftKey });
    canvas.classList.add('dragging');
    if (ptrs.size === 2) { const [p, q] = [...ptrs.values()]; pinch0 = { d: Math.hypot(p.x - q.x, p.y - q.y), r: cam.r, mx: (p.x + q.x) / 2, my: (p.y + q.y) / 2 }; }
  });
  canvas.addEventListener('pointermove', (e) => {
    const p = ptrs.get(e.pointerId); if (!p) return;
    const dx = e.clientX - p.x, dy = e.clientY - p.y;
    p.x = e.clientX; p.y = e.clientY;
    if (ptrs.size >= 2 && pinch0) {
      const [a, b] = [...ptrs.values()];
      const d = Math.hypot(a.x - b.x, a.y - b.y), mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
      cam.r = clamp(pinch0.r * pinch0.d / Math.max(d, 1), 6, 15000);
      panBy(mx - pinch0.mx, my - pinch0.my); pinch0.mx = mx; pinch0.my = my;
      return;
    }
    if (p.pan) panBy(dx, dy);
    else { cam.az = (cam.az - dx * 0.3 + 360) % 360; cam.el = clamp(cam.el + dy * 0.2, 2, 89); }
  });
  const up = (e) => { ptrs.delete(e.pointerId); if (ptrs.size < 2) pinch0 = null; if (!ptrs.size) canvas.classList.remove('dragging'); };
  canvas.addEventListener('pointerup', up); canvas.addEventListener('pointercancel', up);
  canvas.addEventListener('wheel', (e) => { e.preventDefault(); cam.r = clamp(cam.r * Math.exp(e.deltaY * 0.0012), 6, 15000); }, { passive: false });
  canvas.addEventListener('dblclick', () => { cam.pan.set(0, 0, 0); cam.ease = 1; });

  // ---------- HUD ----------
  function seg(el, items, onClick) {
    el.innerHTML = '';
    return items.map(([v, txt, title]) => {
      const b = document.createElement('button'); b.textContent = txt; b.dataset.v = v; if (title) b.title = title;
      b.addEventListener('click', () => onClick(v)); el.appendChild(b); return b;
    });
  }
  seg($('#races'), D.races.map((r, i) => [i, r.name.replace('Race ', 'R')]), (i) => { buildRace(+i); syncButtons(); });
  seg($('#rates'), RATES.map((r) => [r, r + '×']), (r) => { S.rate = +r; syncButtons(); });
  seg($('#cams'), [['chase', 'Behind', 'Camera stays behind the boat you follow'], ['fixed', 'Steady', 'Follow the boat without turning with it'],
    ['course', 'Course', 'Whole course, looking upwind'], ['top', 'Top', 'Straight down']], (m) => setCam(m));
  seg($('#tails'), TAILS.map(([v, t]) => [v, t]), (v) => { S.tail = +v; syncButtons(); });
  seg($('#shows'), [['labels', 'Labels'], ['ladder', 'Ladder', 'Rungs every 100 m and the laylines for the followed boat\'s leg'],
    ['streaks', 'Wind'], ['trueSize', 'True size', 'Off: boats grow when you zoom out so they stay visible']], (k) => { S[k] = !S[k]; syncButtons(); });
  if (host.clientWidth < 760) { $('#viewbar').classList.add('closed'); $('#vbtoggle').textContent = '▸'; }
  $('#vbtoggle').addEventListener('click', () => { $('#viewbar').classList.toggle('closed'); $('#vbtoggle').textContent = $('#viewbar').classList.contains('closed') ? '▸' : '▾'; });
  function syncButtons() {
    root.querySelectorAll('#rates button').forEach((b) => b.classList.toggle('on', +b.dataset.v === S.rate));
    root.querySelectorAll('#tails button').forEach((b) => b.classList.toggle('on', +b.dataset.v === S.tail));
    root.querySelectorAll('#shows button').forEach((b) => b.classList.toggle('on', !!S[b.dataset.v]));
    root.querySelectorAll('#cams button').forEach((b) => b.classList.toggle('on', b.dataset.v === S.cam));
    root.querySelectorAll('#races button').forEach((b) => b.classList.toggle('on', +b.dataset.v === S.race));
    $('#play').textContent = S.playing ? '❚❚' : '▶';
    $('#play').setAttribute('aria-label', S.playing ? 'Pause' : 'Play');
  }
  const setPlay = (p) => { S.playing = p; if (p && S.t >= R.t1) S.t = R.t0; syncButtons(); };
  $('#play').addEventListener('click', () => setPlay(!S.playing));
  $('#back').addEventListener('click', () => { S.t = clamp(S.t - 10, R.t0, R.t1); });
  $('#fwd').addEventListener('click', () => { S.t = clamp(S.t + 10, R.t0, R.t1); });
  $('#time').addEventListener('input', (e) => { S.t = +e.target.value; });
  window.addEventListener('keydown', (e) => {
    if (!active || e.ctrlKey || e.metaKey || e.altKey) return;
    const tag = (e.composedPath()[0] || e.target).tagName;
    if ((tag === 'INPUT' && e.key !== ' ') || tag === 'TEXTAREA' || tag === 'SELECT') return;
    if (e.key === ' ') { e.preventDefault(); setPlay(!S.playing); }
    else if (e.key === 'ArrowLeft') S.t = clamp(S.t - (e.shiftKey ? 60 : 10), R.t0, R.t1);
    else if (e.key === 'ArrowRight') S.t = clamp(S.t + (e.shiftKey ? 60 : 10), R.t0, R.t1);
    else if (/^[1-9]$/.test(e.key) && +e.key <= D.boats.length) setFollow(+e.key - 1);
    else if (e.key === 'c') setCam('course');
    else if (e.key === 't') setCam('top');
    else if (e.key === 'b') setCam('chase');
    else if (e.key === 's') setCam('fixed');
  });

  // boat cards
  const cards = D.boats.map((b, i) => {
    const el = document.createElement('div'); el.className = 'card';
    el.innerHTML = '<div class="row1"><span class="dot" style="background:' + b.color + '"></span>' + b.name +
      '<span class="pos"></span></div><div class="row2"></div><div class="row3"></div>';
    el.addEventListener('click', () => setFollow(i));
    $('#boats').appendChild(el);
    return el;
  });
  const magOf = (tru) => Math.round(((tru - (R.mag_var || 0)) % 360 + 360) % 360);
  let hudT = 0;
  function updateHud(states, twd, viewAz) {
    const t = S.t;
    $('#clock').textContent = (t < 0 ? '−' : '+') + mmss(t);
    let local = '';
    if (R.gun_local) { const [h, m] = R.gun_local.split(':').map(Number), s = Math.round(h * 3600 + m * 60 + t);
      local = Math.floor(s / 3600) + ':' + String(Math.floor(s / 60) % 60).padStart(2, '0') + ':' + String(((s % 60) + 60) % 60).padStart(2, '0'); }
    $('#clock2').textContent = (t < 0 ? 'to the gun' : 'since the gun') + (local ? ' · ' + local : '');
    $('#time').value = t;
    $('#twd').textContent = twd === null ? '—' : magOf(twd) + '° M';
    $('#dial').setAttribute('transform', 'rotate(' + (-viewAz + 180) + ')');
    if (twd !== null) $('#windarrow').setAttribute('transform', 'rotate(' + (twd - viewAz + 180 - 180) + ')');
    // order: finished first, then by leg, then by distance to the leg's mark (along the ladder)
    const sl = startLine(R);
    const info = D.boats.map((b, i) => {
      const d = R.boats[b.name], s = states[i];
      if (!d || !s) return { i, s: null };
      const leg = legAt(d, t);
      let togo = null, below = null;
      if (leg > 0) {
        const tg = legTarget(R, leg), lad = R.ladders[leg - 1];
        const up = lad ? lad.up : (twd || 0), ux = Math.sin(up * RAD), uy = Math.cos(up * RAD);
        togo = tg ? (tg[0] - s.x) * ux + (tg[1] - s.y) * uy : null;
      } else if (sl) {
        below = -((s.x - sl.mx) * sl.nx + (s.y - sl.my) * sl.ny) / sl.len;
      }
      const fin = leg === -1 ? d.legs[d.legs.length - 1][1] : null;
      return { i, s, leg, togo, below, fin, key: fin !== null ? -1e9 + fin : (leg > 0 ? -leg * 1e5 + (togo || 0) : 0) };
    });
    const ranked = info.filter((r) => r.s && t >= 0).sort((a, b) => a.key - b.key);
    const lead = ranked[0];
    info.forEach((r) => {
      const el = cards[r.i];
      el.classList.toggle('on', r.i === S.follow && S.cam !== 'free');
      if (!r.s) { el.querySelector('.row2').innerHTML = '<span>no data at this time</span>'; el.querySelector('.row3').textContent = ''; el.querySelector('.pos').textContent = ''; return; }
      const place = ranked.indexOf(r);
      el.querySelector('.pos').textContent = place >= 0 && ranked.length > 1 ? ord(place + 1) : '';
      el.querySelector('.row2').innerHTML = '<b>' + r.s.sog.toFixed(1) + '</b><span>kt</span><b>' + magOf(r.s.yaw) + '°</b><span>M</span><b>' +
        Math.abs(Math.round(r.s.heel)) + '°</b><span>heel</span>';
      let l3 = '', gap = '';
      if (r.leg === 0) l3 = r.below === null ? 'Pre-start' : (r.below >= 0 ? Math.round(r.below) + ' m below the line' : Math.round(-r.below) + ' m over the line');
      else if (r.leg === -1) l3 = 'Finished' + (r.fin !== null ? ' at +' + mmss(r.fin) : '');
      else {
        l3 = (R.leg_names[r.leg - 1] || 'Leg ' + r.leg) + (r.togo !== null ? ' · ' + Math.max(0, Math.round(r.togo)) + ' m to go' : '');
        if (lead && lead !== r && lead.leg === r.leg && r.togo !== null) gap = '+' + Math.round(r.togo - lead.togo) + ' m';
        else if (lead && lead !== r && lead.leg !== r.leg) gap = lead.leg === -1 ? 'leader finished' : '+1 leg';
      }
      el.querySelector('.row3').innerHTML = l3 + (gap ? '<span class="gap">' + gap + '</span>' : '');
    });
  }

  // ---------- main loop ----------
  function resize() {
    const w = host.clientWidth, h = host.clientHeight;
    if (!w || !h) return;
    renderer.setSize(w, h, false); camera.aspect = w / h;
    // keep about the same width of view on a tall phone screen
    camera.fov = w < h ? Math.min(72, 2 * Math.atan(Math.tan(25 * RAD) * 1.3 * h / w / 1.6) / RAD) : 50;
    camera.updateProjectionMatrix();
    res.set(w / 2, h / 2);
  }
  if (window.ResizeObserver) new ResizeObserver(resize).observe(host); else window.addEventListener('resize', resize);
  resize();
  // full screen (where the browser allows it for a page element)
  const fsOk = !!(host.requestFullscreen || host.webkitRequestFullscreen);
  $('#full').hidden = !fsOk;
  $('#full').addEventListener('click', () => {
    const doc = document;
    if (doc.fullscreenElement || doc.webkitFullscreenElement) (doc.exitFullscreen || doc.webkitExitFullscreen).call(doc);
    else (host.requestFullscreen || host.webkitRequestFullscreen).call(host);
  });

  const _proj = new V3();
  function placeLabel(el, p, show) {
    _proj.copy(p).project(camera);
    if (!show || _proj.z > 1 || _proj.z < -1 || Math.abs(_proj.x) > 1.2 || Math.abs(_proj.y) > 1.2) { el.style.display = 'none'; return; }
    el.style.display = '';
    el.style.transform = 'translate(' + ((_proj.x + 1) * res.x).toFixed(1) + 'px,' + ((1 - _proj.y) * res.y).toFixed(1) + 'px) translate(-50%,-100%)';
  }

  let last = performance.now(), wallT = 0;
  function frame(now) {
    if (!active) { running = false; return; }
    const dt = Math.min(0.1, (now - last) / 1000); last = now; wallT += dt;
    if (S.playing) { S.t += dt * S.rate; if (S.t >= R.t1) { S.t = R.t1; S.playing = false; syncButtons(); } }
    const t = S.t, twd = windAt(t);
    const states = D.boats.map((b, i) => updateBoat(boatObjs[i], R.boats[b.name], t, twd, S.playing ? dt * Math.min(S.rate, 4) : dt));
    const viewAz = updateCamera(dt);
    // boats grow when far away so they stay visible
    states.forEach((s, i) => {
      const o = boatObjs[i]; if (!s) return;
      const dist = camera.position.distanceTo(o.g.position);
      const k = S.trueSize ? 1 : clamp(dist / 170, 1, 30);
      o.body.scale.setScalar(k);
      o.ring.scale.setScalar(k); o.ring.material.opacity = clamp((k - 2) / 4, 0, 0.6);
    });
    marks.forEach((m) => { const dist = camera.position.distanceTo(m.obj.position); m.obj.scale.setScalar(S.trueSize ? 1 : clamp(dist / 170, 1, 30)); });
    trails.forEach((m) => { if (m) { m.material.uniforms.uNow.value = t; m.material.uniforms.uTail.value = S.tail; } });
    updateLadder(t);
    if (twd !== null) { const to = (twd + 180) * RAD; waterU.uWind.value.set(Math.sin(to), -Math.cos(to)); }
    waterU.uTime.value = wallT * 0.6;
    updateStreaks(S.playing ? dt * Math.min(S.rate, 3) : dt * 0.15, twd);
    // labels
    states.forEach((s, i) => {
      const el = boatLabels[i];
      if (!s) { el.style.display = 'none'; return; }
      const k = boatObjs[i].body.scale.x;
      placeLabel(el, _q.copy(boatObjs[i].g.position).setY(BOAT.headY * k + 1.5), S.labels);
      el.lastChild.textContent = s.sog.toFixed(1) + ' kt';
    });
    markLabels.forEach((m) => placeLabel(m.el, m.p, S.labels));
    hudT -= dt;
    if (hudT <= 0) { updateHud(states, twd, viewAz); hudT = 0.1; }
    renderer.render(scene, camera);
    requestAnimationFrame(frame);
  }

  // note about the data
  $('#note').textContent = 'Tracks: each boat\'s GPS, one point a second. Heading, heel and which side the sails are on come from the data; how far they\'re eased is drawn, not measured.' +
    (T ? ' Shoreline and depth: NOAA BlueTopo.' : '');
  buildRace(0);
  syncButtons();
  cam.yawS = null;
  const ctl = {
    S, cam, setCam, setFollow, buildRace, D, renderer, scene, camera, get trails() { return trails; },
    // stop drawing while the replay is hidden (another report page is open)
    setActive(on) {
      active = on;
      if (on && !running) { running = true; last = performance.now(); resize(); requestAnimationFrame(frame); }
      if (!on && S.playing) { S.playing = false; syncButtons(); }
    },
  };
  ctl.setActive(true);
  window.__replay = ctl;
  return ctl;
} };
