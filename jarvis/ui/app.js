/* app.js — orchestration. Fetches the graph, fills the four regions, drives
 * the reactor HUD, and wires the ask bar. Voice (mic/speak) is added in step 4;
 * here the ask bar works by text and the reactor shows idle/thinking. */
(function () {
  "use strict";

  const $ = (s) => document.querySelector(s);
  const api = async (path, opts) => {
    const r = await fetch(path, opts);
    if (!r.ok) {
      let msg = r.statusText;
      try { msg = (await r.json()).error || msg; } catch (e) {}
      throw new Error(msg);
    }
    return r;
  };

  const EXAMPLES = [
    "What's the status of the Oradell migration?",
    "Who's Priya and what do we do for her?",
    "Which invoices are part-paid?",
    "Brief me.",
    "Plan my day.",
    "What did we agree with Blue Mallow?",
  ];

  let graph = null;
  let allTypes = {};

  function toast(msg) {
    const t = $("#toast");
    t.textContent = msg;
    t.classList.add("on");
    clearTimeout(toast._t);
    toast._t = setTimeout(() => t.classList.remove("on"), 5000);
  }

  // ---------- inspector ----------
  function showNote(n) {
    const dot = (window.TYPE_COLORS[n.type] || "#888");
    const linkHtml = (n.links || []).map((id) =>
      `<a data-id="${id}">${id}</a>`).join("");
    $("#inspector").innerHTML = `
      <span class="note-type" style="--dot:${dot}">${n.type}</span>
      <h3>${escapeHtml(n.title)}</h3>
      <div class="meta">${n.degree} connection${n.degree === 1 ? "" : "s"}${n.note ? " · " + escapeHtml(n.note) : ""}</div>
      ${n.body ? `<div class="body">${escapeHtml(n.body)}</div>` : ""}
      ${linkHtml ? `<div class="links"><div class="meta" style="margin-top:12px">Links to</div>${linkHtml}</div>` : ""}
    `;
    $("#inspector").querySelectorAll("a[data-id]").forEach((a) =>
      a.addEventListener("click", () => graph.focusNode(a.dataset.id)));
  }

  async function onFocus(node) {
    try {
      const detail = await (await api("/api/note?id=" + encodeURIComponent(node.id))).json();
      showNote(detail);
    } catch (e) { showNote(node); }
  }

  async function onPath(a, b) {
    try {
      const res = await (await api(`/api/path?a=${encodeURIComponent(a)}&b=${encodeURIComponent(b)}`)).json();
      if (!res.path.length) { toast("No path between those two."); return; }
      graph.showPath(res.path);
      $("#inspector").insertAdjacentHTML("afterbegin",
        `<div class="meta">Path (${res.path.length} nodes): ${res.path.join(" → ")}</div>`);
    } catch (e) { toast(e.message); }
  }

  // ---------- hubs ----------
  function fillHubs(nodes) {
    const top = nodes.slice().sort((a, b) => b.degree - a.degree).slice(0, 10);
    $("#hubs").innerHTML = top.map((n) =>
      `<div class="hub-row" data-id="${n.id}"><span class="nm">${escapeHtml(n.title)}</span><span class="deg">${n.degree}</span></div>`
    ).join("");
    $("#hubs").querySelectorAll(".hub-row").forEach((r) =>
      r.addEventListener("click", () => graph.focusNode(r.dataset.id)));
  }

  // ---------- filters ----------
  const hidden = new Set();
  function fillFilters(types) {
    $("#filters").innerHTML = Object.entries(types).map(([t, c]) =>
      `<div class="filter-row" data-type="${t}">
        <span class="swatch" style="background:${window.TYPE_COLORS[t] || "#888"}"></span>
        <span class="name">${t}</span><span class="count">${c}</span>
      </div>`).join("");
    $("#filters").querySelectorAll(".filter-row").forEach((row) =>
      row.addEventListener("click", () => {
        const t = row.dataset.type;
        if (hidden.has(t)) { hidden.delete(t); row.classList.remove("off"); }
        else { hidden.add(t); row.classList.add("off"); }
        graph.setHidden([...hidden]);
      }));
  }

  // ---------- badges ----------
  async function loadSource() {
    try {
      const s = await (await api("/api/source")).json();
      const bm = $("#badge-mode");
      bm.textContent = (s.mode === "demo" ? "DEMO DATA" : "REAL DATA") + " · " + s.count + " notes";
      bm.className = "badge " + (s.mode === "demo" ? "demo" : "");
      const mb = $("#badge-model");
      if (!s.model.available) {
        mb.hidden = false;
        mb.textContent = "MODEL MISSING · file-scoring";
        mb.className = "badge bad";
        mb.title = s.model.reason;
      } else { mb.hidden = true; }
    } catch (e) { toast("Could not read source status: " + e.message); }
  }

  // ---------- reactor HUD ----------
  const reactor = { state: "idle", level: 0, t: 0 };
  function setReactor(state) {
    reactor.state = state;
    $("#reactor-label").textContent = state;
    $("#reactor-label").className = state;
  }
  function drawReactor() {
    const cv = $("#reactor"), ctx = cv.getContext("2d");
    const W = cv.width, H = cv.height, cx = W / 2, cy = H / 2;
    ctx.clearRect(0, 0, W, H);
    reactor.t += 0.03;
    const palette = { idle: "#5b6372", listening: "#4dd6e0", thinking: "#9a8cff", speaking: "#6fce9f" };
    const col = palette[reactor.state] || palette.idle;
    const breathe = reactor.state === "idle" ? 0.5 + 0.5 * Math.sin(reactor.t * 0.8) : 1;
    const energy = reactor.state === "listening" ? reactor.level
                  : reactor.state === "speaking" ? (0.5 + 0.5 * Math.sin(reactor.t * 6))
                  : reactor.state === "thinking" ? (0.5 + 0.5 * Math.sin(reactor.t * 3)) : breathe;

    // outer ring
    ctx.lineWidth = 3;
    ctx.strokeStyle = hexA(col, 0.25);
    ctx.beginPath(); ctx.arc(cx, cy, 120, 0, Math.PI * 2); ctx.stroke();

    // rotating arcs
    for (let i = 0; i < 3; i++) {
      const r = 70 + i * 22;
      const a0 = reactor.t * (i % 2 ? -1 : 1) * (0.4 + i * 0.2);
      ctx.beginPath();
      ctx.strokeStyle = hexA(col, 0.35 + i * 0.15);
      ctx.lineWidth = 2;
      ctx.arc(cx, cy, r, a0, a0 + Math.PI * (0.6 + 0.3 * energy));
      ctx.stroke();
    }
    // glowing core
    const cr = 30 + energy * 22;
    const grd = ctx.createRadialGradient(cx, cy, 0, cx, cy, cr + 26);
    grd.addColorStop(0, hexA(col, 0.9));
    grd.addColorStop(0.5, hexA(col, 0.35));
    grd.addColorStop(1, hexA(col, 0));
    ctx.fillStyle = grd;
    ctx.beginPath(); ctx.arc(cx, cy, cr + 26, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = col;
    ctx.beginPath(); ctx.arc(cx, cy, cr * 0.5, 0, Math.PI * 2); ctx.fill();

    requestAnimationFrame(drawReactor);
  }

  // ---------- ask bar ----------
  async function ask(text) {
    if (!text.trim()) return;
    setReactor("thinking");
    $("#ask-input").value = "";
    try {
      const res = await (await api("/api/ask", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text, history: [] }),
      })).json();
      handleReply(res);
    } catch (e) {
      toast(e.message);
      setReactor("idle");
    }
  }

  function handleReply(res) {
    // spoken line (short) + card (detail). Never the same text in both.
    if (res.spoken) {
      const s = $("#spoken");
      s.textContent = res.spoken;
      s.classList.add("on");
      clearTimeout(handleReply._t);
      handleReply._t = setTimeout(() => s.classList.remove("on"), 9000);
    }
    if (res.card) renderCard(res.card);
    if (res.focus) graph.focusNode(res.focus);
    setReactor("idle");
  }

  function renderCard(card) {
    // A tool's structured detail lands in the inspector.
    $("#inspector").innerHTML =
      `<span class="note-type">${escapeHtml(card.kind || "result")}</span>
       <h3>${escapeHtml(card.title || "")}</h3>` +
      (card.html || `<div class="body">${escapeHtml(card.text || "")}</div>`);
  }

  // rotating example prompt
  function rotateExamples() {
    let i = 0;
    const inp = $("#ask-input");
    setInterval(() => {
      if (document.activeElement === inp || inp.value) return;
      i = (i + 1) % EXAMPLES.length;
      inp.placeholder = EXAMPLES[i];
    }, 4200);
    inp.placeholder = EXAMPLES[0];
  }

  // ---------- helpers ----------
  function escapeHtml(s) {
    return String(s).replace(/[&<>"]/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  }
  function hexA(hex, a) {
    const n = parseInt(hex.slice(1), 16);
    return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${a})`;
  }

  // ---------- boot ----------
  async function boot() {
    graph = new window.Graph($("#canvas"), { onFocus, onPath });
    try {
      const g = await (await api("/api/graph")).json();
      graph.setData(g);
      allTypes = g.types;
      fillHubs(g.nodes);
      fillFilters(g.types);
    } catch (e) {
      toast("Could not load the graph: " + e.message);
    }
    loadSource();
    drawReactor();
    rotateExamples();

    $("#ask-input").addEventListener("keydown", (e) => {
      if (e.key === "Enter") ask(e.target.value);
    });
    // step-4 voice buttons are wired in voice.js-driven app later; here they
    // at least do their text-equivalent so the UI is never dead.
    $("#btn-brief").addEventListener("click", () => ask("brief me"));
    $("#btn-plan").addEventListener("click", () => ask("plan my day"));
    $("#btn-memory").addEventListener("click", () => {
      const f = prompt("Remember what? (JARVIS will say back exactly what it wrote)");
      if (f) ask("remember: " + f);
    });
    $("#btn-mic").addEventListener("click", () => toast("Voice is added in build step 4."));
    $("#btn-mute").addEventListener("click", () => { setReactor("idle"); });

    // expose for step 4
    window.JARVIS_UI = { setReactor, reactor, ask, toast, handleReply, api };
  }

  document.addEventListener("DOMContentLoaded", boot);
})();
