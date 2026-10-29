// ForgeSound UI skeleton: layers on a timeline, an amplitude envelope per
// layer, render-on-change through the local backend. Every change bumps the
// project revision; a render result is shown only if it is for the newest one.

const COLORS = ["#ff7a1a", "#4cc9f0", "#a78bfa", "#4ade80", "#f472b6", "#facc15"];
const KIND_LABEL = { surge: "Surge", noise: "Шум", sample: "Сэмпл" };
const NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"];

const SCHEMA = {
  surge: [
    { key: "osc", label: "Осциллятор", type: "select", options: [
      ["sine", "Sine"], ["classic", "Classic"], ["modern", "Modern"], ["wavetable", "Wavetable"], ["window", "Window"],
      ["fm2", "FM2"], ["fm3", "FM3"], ["twist", "Twist"], ["alias", "Alias"]] },
    { key: "note", label: "Нота", min: 24, max: 96, step: 1, fmt: v => NOTE_NAMES[v % 12] + (Math.floor(v / 12) - 1) },
    { key: "fine", label: "Подстройка, полутоны", min: -7, max: 7, step: 0.1, fmt: v => v.toFixed(1) },
    { key: "gate_ms", label: "Длина ноты, мс", min: 5, max: 600, step: 1, fmt: v => v.toFixed(0) },
    { key: "decay_ms", label: "Спад огибающей Surge, мс", min: 5, max: 2000, log: true, fmt: v => v.toFixed(0) },
    { key: "sustain", label: "Сустейн", min: 0, max: 1, step: 0.01, fmt: v => Math.round(v * 100) + " %" },
    { key: "filter", label: "Фильтр", type: "select", options: [
      ["off", "выкл"], ["lp12", "ФНЧ 12 дБ"], ["lp24", "ФНЧ 24 дБ"], ["hp12", "ФВЧ 12 дБ"], ["bp12", "Полосовой 12 дБ"]] },
    { key: "cutoff_hz", label: "Срез, Гц", min: 40, max: 18000, log: true, fmt: v => v.toFixed(0) },
    { key: "resonance", label: "Резонанс", min: 0, max: 1, step: 0.01, fmt: v => Math.round(v * 100) + " %" },
  ],
  noise: [
    { key: "freq_hz", label: "Частота полосы, Гц", min: 200, max: 12000, log: true, fmt: v => v.toFixed(0) },
    { key: "q", label: "Добротность", min: 0.3, max: 8, step: 0.1, fmt: v => v.toFixed(1) },
    { key: "decay_ms", label: "Затухание, мс", min: 0.5, max: 200, log: true, fmt: v => v.toFixed(1) },
    { key: "seed", label: "Зерно шума (seed)", min: 1, max: 99, step: 1, fmt: v => v.toFixed(0) },
  ],
  sample: [
    { key: "semitones", label: "Высота (с темпом), полутоны", min: -24, max: 24, step: 0.5, fmt: v => v.toFixed(1) },
    { key: "trim_ms", label: "Обрезать начало, мс", min: 0, max: 1000, step: 1, fmt: v => v.toFixed(0) },
    { key: "reverse", label: "Задом наперёд", type: "check" },
  ],
};
const COMMON = [
  { key: "gain_db", label: "Громкость слоя, дБ", min: -36, max: 12, step: 0.5, fmt: v => v.toFixed(1), top: true },
  { key: "start_ms", label: "Начало, мс", min: 0, max: 1500, step: 0.5, fmt: v => v.toFixed(1), top: true },
];

const $ = id => document.getElementById(id);
const state = {
  project: null, sel: null, rev: 0, shownRev: -1, result: null, buffer: null, ctx: null, source: null,
};

// ------------------------------------------------------------ project
function save() {
  try { localStorage.setItem("forgesound.project", JSON.stringify(state.project)); } catch (e) { /* private mode */ }
}
async function load() {
  let p = null;
  try { p = JSON.parse(localStorage.getItem("forgesound.project")); } catch (e) { p = null; }
  if (!p || !Array.isArray(p.layers)) p = await (await fetch("/api/project/default")).json();
  state.project = p;
  state.rev = p.rev || 0;
  state.sel = p.layers[0] ? p.layers[0].id : null;
}
const layer = id => state.project.layers.find(l => l.id === id);
const color = id => COLORS[state.project.layers.findIndex(l => l.id === id) % COLORS.length];
function newId() {
  let i = 1;
  while (state.project.layers.some(l => l.id === "L" + i)) i++;
  return "L" + i;
}

// ------------------------------------------------------------ status
function setStatus(st, text) {
  $("status").dataset.state = st;
  $("statusText").textContent = text;
}
function toast(msg, ms = 4000) {
  const t = $("toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toast.t);
  toast.t = setTimeout(() => (t.hidden = true), ms);
}

// ------------------------------------------------------------ render
function changed() {
  // something was edited and released: a new revision, render it
  state.rev += 1;
  state.project.rev = state.rev;
  save();
  render();
}
function editing() {
  // dragging: the sound on screen is older than the project
  setStatus("stale", "изменено — отпустите, чтобы пересчитать");
}
async function render() {
  const rev = state.rev;
  setStatus("rendering", "рендер · rev " + rev);
  let data;
  try {
    const r = await fetch("/api/render", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(state.project) });
    data = await r.json();
    if (!r.ok) throw new Error(data.detail || r.statusText);
  } catch (e) {
    if (rev === state.rev) setStatus("error", "ошибка · " + e.message);
    return;
  }
  // the newest revision wins: an older answer is dropped, whatever its arrival order
  if (data.stale || data.rev !== state.rev) return;
  state.result = data;
  state.shownRev = data.rev;
  state.buffer = await decode(data.wav_b64);
  setStatus("ok", "актуально · rev " + data.rev + " · " + data.render_ms + " мс");
  showMetrics(data);
  drawAll();
  if (state.autoplay) { state.autoplay = false; play(); }
}
function showMetrics(d) {
  const m = d.metrics;
  $("metrics").innerHTML =
    `<span>пик <b>${m.peak_db}</b> дБ</span><span>громкость A·50 мс <b>${m.loud_a50_db}</b> дБ</span>` +
    `<span>атака <b>${m.attack_ms}</b> мс</span><span>хвост до −60 дБ <b>${m.tail_ms}</b> мс</span>` +
    (d.guard ? `<span style="color:var(--warn)">перегруз: микс приглушён</span>` : "");
}

// ------------------------------------------------------------ audio
function audioCtx() {
  if (!state.ctx) state.ctx = new AudioContext();
  return state.ctx;
}
async function decode(b64) {
  const bytes = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
  return await audioCtx().decodeAudioData(bytes.buffer);
}
function play() {
  if (!state.buffer) return;
  const ctx = audioCtx();
  if (state.source) { try { state.source.stop(); } catch (e) { /* already stopped */ } }
  const s = ctx.createBufferSource();
  s.buffer = state.buffer;
  s.loop = $("loopChk").checked;
  s.connect(ctx.destination);
  s.start();
  state.source = s;
}

// ------------------------------------------------------------ layers panel
function drawLayerList() {
  const ul = $("layerList");
  ul.innerHTML = "";
  for (const L of state.project.layers) {
    const li = document.createElement("li");
    li.className = "layer" + (L.id === state.sel ? " sel" : "") + (L.mute ? " muted-layer" : "");
    li.innerHTML = `<span class="sw" style="background:${color(L.id)}"></span>
      <span><span class="lname"></span><span class="lkind">${KIND_LABEL[L.kind]} · ${L.gain_db.toFixed(1)} дБ</span></span>
      <button class="icon-btn" title="Выключить/включить">${L.mute ? "🔇" : "🔊"}</button>
      <button class="icon-btn" title="Удалить слой">✕</button>`;
    li.querySelector(".lname").textContent = L.name;
    li.onclick = () => { state.sel = L.id; drawAll(); };
    const [muteB, delB] = li.querySelectorAll("button");
    muteB.onclick = e => { e.stopPropagation(); L.mute = !L.mute; drawAll(); changed(); };
    delB.onclick = e => {
      e.stopPropagation();
      state.project.layers = state.project.layers.filter(x => x !== L);
      if (state.sel === L.id) state.sel = state.project.layers[0] ? state.project.layers[0].id : null;
      drawAll(); changed();
    };
    ul.appendChild(li);
  }
}
function addLayer(kind, extra = {}) {
  const d = state.project.duration_ms;
  const base = {
    surge: { name: "Surge", params: { osc: "sine", note: 50, fine: 0, gate_ms: 60, decay_ms: 120, sustain: 0, filter: "lp12", cutoff_hz: 2400, resonance: 0.15 },
      env: [[0, 0], [0.5, 1], [40, 0.45], [Math.min(260, d), 0]] },
    noise: { name: "Шум", params: { freq_hz: 3500, q: 1.2, decay_ms: 6, seed: 1 }, env: [[0, 0], [0.2, 1], [8, 0.3], [30, 0]] },
    sample: { name: "Сэмпл", params: { asset: null, semitones: 0, trim_ms: 0, reverse: false }, env: [[0, 1], [d * 0.8, 1], [d, 0]] },
  }[kind];
  const L = Object.assign({ id: newId(), kind, mute: false, gain_db: -6, start_ms: 0 }, base, extra);
  L.params = Object.assign({}, base.params, extra.params || {});
  state.project.layers.push(L);
  state.sel = L.id;
  drawAll();
  changed();
}

// ------------------------------------------------------------ params panel
function slider(spec, value, onInput, onChange) {
  const wrap = document.createElement("label");
  wrap.className = "field";
  const lo = spec.log ? Math.log(spec.min) : spec.min, hi = spec.log ? Math.log(spec.max) : spec.max;
  const toPos = v => spec.log ? Math.round(1000 * (Math.log(v) - lo) / (hi - lo)) : v;
  const fromPos = p => spec.log ? Math.exp(lo + (hi - lo) * p / 1000) : Number(p);
  const inp = document.createElement("input");
  inp.type = "range";
  if (spec.log) { inp.min = 0; inp.max = 1000; inp.step = 1; } else { inp.min = spec.min; inp.max = spec.max; inp.step = spec.step; }
  inp.value = toPos(value);
  const out = document.createElement("output");
  out.textContent = spec.fmt(value);
  inp.oninput = () => { const v = fromPos(inp.value); out.textContent = spec.fmt(v); onInput(v); };
  inp.onchange = () => onChange(fromPos(inp.value));
  wrap.append(spec.label, out, inp);
  return wrap;
}
function drawParams() {
  const box = $("params");
  box.innerHTML = "";
  const L = layer(state.sel);
  if (!L) { box.innerHTML = '<div class="empty">Нет выбранного слоя</div>'; return; }
  const name = document.createElement("label");
  name.className = "field";
  const ni = document.createElement("input");
  ni.value = L.name;
  ni.style.cssText = "grid-column:1/-1;background:var(--panel-2);border:1px solid var(--line);border-radius:6px;padding:5px";
  ni.onchange = () => { L.name = ni.value; drawAll(); save(); };
  name.append("Название", document.createElement("span"), ni);
  box.appendChild(name);
  for (const spec of COMMON) {
    const s = Object.assign({}, spec, spec.key === "start_ms" ? { max: state.project.duration_ms } : {});
    box.appendChild(slider(s, L[spec.key], v => { L[spec.key] = v; editing(); drawTimeline(); drawEnvelope(); },
      v => { L[spec.key] = v; drawLayerList(); changed(); }));
  }
  if (L.kind === "sample" && !L.params.asset) {
    const d = document.createElement("div");
    d.className = "hint";
    d.textContent = "Нет файла: загрузите сэмпл кнопкой «+ Сэмпл».";
    box.appendChild(d);
  }
  for (const spec of SCHEMA[L.kind]) {
    const v = L.params[spec.key];
    if (spec.type === "select") {
      const wrap = document.createElement("label");
      wrap.className = "field";
      const sel = document.createElement("select");
      for (const [val, lab] of spec.options) sel.add(new Option(lab, val, false, val === v));
      sel.onchange = () => { L.params[spec.key] = sel.value; changed(); };
      wrap.append(spec.label, document.createElement("span"), sel);
      box.appendChild(wrap);
    } else if (spec.type === "check") {
      const wrap = document.createElement("label");
      wrap.className = "chk";
      const c = document.createElement("input");
      c.type = "checkbox";
      c.checked = !!v;
      c.onchange = () => { L.params[spec.key] = c.checked; changed(); };
      wrap.append(c, spec.label);
      box.appendChild(wrap);
    } else {
      box.appendChild(slider(spec, v, nv => { L.params[spec.key] = nv; editing(); },
        nv => { L.params[spec.key] = nv; changed(); }));
    }
  }
  if (L.kind === "surge") {
    const d = document.createElement("div");
    d.className = "hint";
    d.textContent = "Retrigger всегда включён: звук Surge повторяется бит-в-бит. S&H Noise и String скрыты — они случайны при каждом рендере.";
    box.appendChild(d);
  }
}

// ------------------------------------------------------------ canvases
function fitCanvas(c, h) {
  const dpr = window.devicePixelRatio || 1;
  const w = c.clientWidth;
  if (h) c.style.height = h + "px";
  const ch = h || c.clientHeight;
  if (c.width !== Math.round(w * dpr) || c.height !== Math.round(ch * dpr)) {
    c.width = Math.round(w * dpr);
    c.height = Math.round(ch * dpr);
  }
  const g = c.getContext("2d");
  g.setTransform(dpr, 0, 0, dpr, 0, 0);
  return { g, w, h: ch };
}
function drawPeaks(g, pk, x0, y0, w, h, col, alpha = 1, fit = false) {
  if (!pk || !pk.length) return;
  // fit: scale a quiet layer up to its lane (display only, the sound is not changed)
  let k = 1;
  if (fit) {
    const m = pk.reduce((a, [lo, hi]) => Math.max(a, -lo, hi), 0);
    k = m > 0 ? Math.min(30, 0.9 / m) : 1;
  }
  g.fillStyle = col;
  g.globalAlpha = alpha;
  const mid = y0 + h / 2;
  const bw = w / pk.length;
  for (let i = 0; i < pk.length; i++) {
    const [lo, hi] = pk[i];
    const top = mid - k * hi * h / 2, bot = mid - k * lo * h / 2;
    g.fillRect(x0 + i * bw, top, Math.max(1, bw), Math.max(1, bot - top));
  }
  g.globalAlpha = 1;
}
function ruler(g, x0, w, y, ms) {
  g.fillStyle = "#8a93a1";
  g.font = "11px system-ui";
  g.strokeStyle = "#2f343e";
  const step = ms <= 200 ? 20 : ms <= 600 ? 50 : 100;
  for (let t = 0; t <= ms + 0.01; t += step) {
    const x = x0 + t / ms * w;
    g.beginPath(); g.moveTo(x, y + 12); g.lineTo(x, 9999); g.stroke();
    const label = t + (t === 0 ? " мс" : "");
    const tw = g.measureText(label).width;
    g.fillText(label, x + 3 + tw > x0 + w ? x - tw - 3 : x + 3, y + 10);
  }
}

const TL = { pad: 10, rulerH: 18, masterH: 64, laneH: 46 };
let drag = null;

function drawTimeline() {
  const c = $("timeline");
  const n = state.project.layers.length;
  const H = TL.rulerH + TL.masterH + 10 + n * (TL.laneH + 6) + 8;
  const { g, w } = fitCanvas(c, H);
  const ms = state.project.duration_ms;
  const x0 = TL.pad, W = w - 2 * TL.pad;
  g.clearRect(0, 0, w, H);
  ruler(g, x0, W, 2, ms);
  const r = state.result;
  // master
  let y = TL.rulerH;
  g.fillStyle = "#161920";
  g.fillRect(x0, y, W, TL.masterH);
  g.fillStyle = "#8a93a1";
  g.font = "11px system-ui";
  g.fillText("Микс", x0 + 6, y + 13);
  if (r) drawPeaks(g, r.peaks.master, x0, y, W, TL.masterH, "#e6e9ee", 0.9);
  y += TL.masterH + 10;
  // lanes
  for (const L of state.project.layers) {
    const col = color(L.id);
    const sel = L.id === state.sel;
    g.fillStyle = sel ? "#232833" : "#191c22";
    g.fillRect(x0, y, W, TL.laneH);
    const shift = drag && drag.kind === "clip" && drag.id === L.id ? (L.start_ms - drag.orig) : 0;
    const sx = x0 + L.start_ms / ms * W;
    g.fillStyle = col + (L.mute ? "22" : "33");
    g.fillRect(sx, y + 2, x0 + W - sx, TL.laneH - 4);
    g.strokeStyle = col;
    g.lineWidth = sel ? 2 : 1;
    g.strokeRect(sx + 0.5, y + 2.5, x0 + W - sx - 1, TL.laneH - 5);
    if (r && r.peaks[L.id]) {
      g.save();
      g.beginPath(); g.rect(x0, y, W, TL.laneH); g.clip();
      drawPeaks(g, r.peaks[L.id], x0 + shift / ms * W, y + 3, W, TL.laneH - 6, col, L.mute ? 0.3 : 0.95, true);
      g.restore();
    }
    g.fillStyle = "#e6e9ee";
    g.font = "12px system-ui";
    g.fillText(L.name + (L.mute ? " (выкл)" : ""), sx + 6, y + 15);
    L._y = y;
    y += TL.laneH + 6;
  }
}

function timelineDown(e) {
  const c = $("timeline");
  const rect = c.getBoundingClientRect();
  const x = e.clientX - rect.left, y = e.clientY - rect.top;
  const L = state.project.layers.find(l => y >= l._y && y <= l._y + TL.laneH);
  if (!L) return;
  state.sel = L.id;
  drag = { kind: "clip", id: L.id, x0: x, orig: L.start_ms, w: rect.width - 2 * TL.pad };
  c.setPointerCapture(e.pointerId);
  c.style.cursor = "grabbing";
  drawAll();
}
function timelineMove(e) {
  if (!drag || drag.kind !== "clip") return;
  const rect = $("timeline").getBoundingClientRect();
  const L = layer(drag.id);
  const ms = state.project.duration_ms;
  const t = drag.orig + (e.clientX - rect.left - drag.x0) / drag.w * ms;
  L.start_ms = Math.round(Math.max(0, Math.min(ms - 1, t)) * 2) / 2;
  editing();
  drawTimeline();
  drawEnvelope();
}
function timelineUp() {
  if (!drag || drag.kind !== "clip") return;
  const moved = layer(drag.id).start_ms !== drag.orig;
  drag = null;
  $("timeline").style.cursor = "grab";
  drawParams();
  if (moved) changed();
}

// envelope editor: x is absolute time (aligned with the timeline), y is gain 0..1
const EV = { pad: 10, top: 14, bottom: 18 };
function envGeom() {
  const c = $("envelope");
  const w = c.clientWidth, h = c.clientHeight;
  return { x0: EV.pad, W: w - 2 * EV.pad, y0: EV.top, H: h - EV.top - EV.bottom, ms: state.project.duration_ms };
}
function drawEnvelope() {
  const c = $("envelope");
  const { g, w, h } = fitCanvas(c);
  g.clearRect(0, 0, w, h);
  const L = layer(state.sel);
  $("envLayerName").textContent = L ? L.name : "—";
  const { x0, W, y0, H, ms } = envGeom();
  ruler(g, x0, W, h - 14, ms);
  g.strokeStyle = "#2f343e";
  for (const v of [0.25, 0.5, 0.75]) { g.beginPath(); g.moveTo(x0, y0 + H * (1 - v)); g.lineTo(x0 + W, y0 + H * (1 - v)); g.stroke(); }
  if (!L) return;
  const col = color(L.id);
  if (state.result && state.result.peaks[L.id]) drawPeaks(g, state.result.peaks[L.id], x0, y0, W, H, col, 0.18, true);
  const px = t => x0 + (L.start_ms + t) / ms * W;
  const py = v => y0 + H * (1 - v);
  const pts = L.env;
  g.strokeStyle = "#4cc9f0";
  g.lineWidth = 2;
  g.beginPath();
  g.moveTo(x0, py(pts[0][1]));
  pts.forEach(([t, v]) => g.lineTo(px(t), py(v)));
  g.lineTo(x0 + W, py(pts[pts.length - 1][1]));
  g.stroke();
  pts.forEach(([t, v], i) => {
    g.beginPath();
    g.arc(px(t), py(v), drag && drag.kind === "env" && drag.i === i ? 7 : 5.5, 0, Math.PI * 2);
    g.fillStyle = "#0f1115";
    g.fill();
    g.strokeStyle = "#4cc9f0";
    g.stroke();
  });
  g.fillStyle = "#8a93a1";
  g.font = "11px system-ui";
  g.fillText("1.0", 2, y0 + 4);
  g.fillText("0", 2, y0 + H);
}
function envHit(e) {
  const L = layer(state.sel);
  if (!L) return null;
  const rect = $("envelope").getBoundingClientRect();
  const { x0, W, y0, H, ms } = envGeom();
  const x = e.clientX - rect.left, y = e.clientY - rect.top;
  let best = -1, bd = 12;
  L.env.forEach(([t, v], i) => {
    const d = Math.hypot(x0 + (L.start_ms + t) / ms * W - x, y0 + H * (1 - v) - y);
    if (d < bd) { bd = d; best = i; }
  });
  const t = (x - x0) / W * ms - L.start_ms;
  const v = 1 - (y - y0) / H;
  return { L, i: best, t, v };
}
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
function envDown(e) {
  if (e.button !== 0) return;
  const hit = envHit(e);
  if (!hit || hit.i < 0) return;
  drag = { kind: "env", i: hit.i };
  $("envelope").setPointerCapture(e.pointerId);
}
function envMove(e) {
  if (!drag || drag.kind !== "env") return;
  const { L, t, v } = envHit(e);
  const pts = L.env, i = drag.i;
  const lo = i > 0 ? pts[i - 1][0] + 0.1 : 0;
  const hi = i < pts.length - 1 ? pts[i + 1][0] - 0.1 : state.project.duration_ms;
  pts[i] = [Math.round(clamp(t, lo, hi) * 10) / 10, Math.round(clamp(v, 0, 1) * 1000) / 1000];
  editing();
  drawEnvelope();
}
function envUp() {
  if (!drag || drag.kind !== "env") return;
  drag = null;
  drawEnvelope();
  changed();
}
function envDbl(e) {
  const hit = envHit(e);
  if (!hit) return;
  const t = clamp(hit.t, 0, state.project.duration_ms);
  hit.L.env.push([Math.round(t * 10) / 10, clamp(hit.v, 0, 1)]);
  hit.L.env.sort((a, b) => a[0] - b[0]);
  drawEnvelope();
  changed();
}
function envMenu(e) {
  e.preventDefault();
  const hit = envHit(e);
  if (!hit || hit.i < 0 || hit.L.env.length <= 2) return;
  hit.L.env.splice(hit.i, 1);
  drawEnvelope();
  changed();
}

function drawAll() {
  drawLayerList();
  drawParams();
  drawTimeline();
  drawEnvelope();
}

// ------------------------------------------------------------ files
async function uploadFile(file) {
  toast("Читаю " + file.name + " …");
  try {
    const r = await fetch("/api/assets", { method: "POST", body: file,
      headers: { "X-Filename": encodeURIComponent(file.name) } });
    const a = await r.json();
    if (!r.ok) throw new Error(a.detail || r.statusText);
    const dur = Math.round(a.audio.duration_s * 1000);
    toast(`${file.name}: ${a.audio.format.toUpperCase()}, ${a.audio.sample_rate} Гц, ${a.audio.channels} кан., ${dur} мс. Лицензия не указана.`, 6000);
    addLayer("sample", { name: file.name.replace(/\.[^.]+$/, ""), params: { asset: a.sha256 } });
  } catch (e) {
    toast("Не удалось загрузить: " + e.message, 6000);
  }
}

async function exportWav() {
  try {
    const r = await fetch("/api/export", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(state.project) });
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || r.statusText);
    const blob = new Blob([Uint8Array.from(atob(d.wav_b64), c => c.charCodeAt(0))], { type: "audio/wav" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = d.file;
    a.click();
    toast("Сохранено: " + d.dir + "/" + d.file + " (WAV 16 бит, моно, 44,1 кГц)", 7000);
  } catch (e) {
    toast("Экспорт не удался: " + e.message, 6000);
  }
}

// ------------------------------------------------------------ start
async function main() {
  await load();
  const h = await (await fetch("/api/health")).json();
  $("ver").textContent = h.forgesound;
  $("engine").textContent = h.surge ? "Surge " + h.surge.split(".git")[0] + " ✓" : "Surge недоступен";
  if (!h.surge) { $("engine").style.color = "var(--err)"; $("engine").title = h.surge_error || ""; }

  $("projName").value = state.project.name;
  $("projName").onchange = () => { state.project.name = $("projName").value; save(); };
  const dur = $("durMs"), durOut = $("durOut");
  dur.value = state.project.duration_ms; durOut.textContent = dur.value;
  dur.oninput = () => { durOut.textContent = dur.value; state.project.duration_ms = Number(dur.value); editing(); drawTimeline(); drawEnvelope(); };
  dur.onchange = () => { drawParams(); changed(); };
  const mdb = $("masterDb"), mOut = $("masterOut");
  mdb.value = state.project.master.gain_db; mOut.textContent = Number(mdb.value).toFixed(1);
  mdb.oninput = () => { mOut.textContent = Number(mdb.value).toFixed(1); state.project.master.gain_db = Number(mdb.value); editing(); };
  mdb.onchange = () => changed();

  document.querySelectorAll("[data-add]").forEach(b => (b.onclick = () => addLayer(b.dataset.add)));
  $("fileInput").onchange = e => { if (e.target.files[0]) uploadFile(e.target.files[0]); e.target.value = ""; };
  $("playBtn").onclick = play;
  $("exportBtn").onclick = exportWav;
  document.addEventListener("keydown", e => {
    if (e.code === "Space" && !["INPUT", "TEXTAREA", "SELECT"].includes(e.target.tagName)) { e.preventDefault(); play(); }
  });

  const tl = $("timeline");
  tl.addEventListener("pointerdown", timelineDown);
  tl.addEventListener("pointermove", timelineMove);
  tl.addEventListener("pointerup", timelineUp);
  const ev = $("envelope");
  ev.addEventListener("pointerdown", envDown);
  ev.addEventListener("pointermove", envMove);
  ev.addEventListener("pointerup", envUp);
  ev.addEventListener("dblclick", envDbl);
  ev.addEventListener("contextmenu", envMenu);

  const drop = $("drop");
  window.addEventListener("dragover", e => { e.preventDefault(); drop.hidden = false; });
  drop.addEventListener("dragleave", () => (drop.hidden = true));
  window.addEventListener("drop", e => {
    e.preventDefault(); drop.hidden = true;
    const f = e.dataTransfer.files[0];
    if (f) uploadFile(f);
  });
  window.addEventListener("resize", () => { drawTimeline(); drawEnvelope(); });

  drawAll();
  changed();
}
main();
