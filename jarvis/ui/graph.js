/* graph.js — force-directed note graph on a Canvas (not SVG).
 *
 * SVG needs a DOM node per element and stalls past ~1,500 nodes, so everything
 * here is drawn to one <canvas>. Repulsion uses a spatial grid with a distance
 * cutoff, so cost stays near-linear instead of O(n²). Labels are placed
 * most-connected-first and any label whose box collides with a placed one is
 * skipped, so hub clusters stay legible.
 */
(function () {
  "use strict";

  // ---- tunables ----
  const REPULSION = 5200;      // node-node push strength
  const CUTOFF = 170;          // ignore repulsion past this (world px) -> grid size
  const SPRING = 0.011;        // edge attraction
  const SPRING_LEN = 74;       // ideal edge length
  const GRAVITY = 0.0016;      // pull toward centre
  const DAMP = 0.86;           // velocity damping
  const ALPHA_MIN = 0.018;     // never fully stop -> "keeps breathing"
  const ALPHA_DECAY = 0.985;
  const MIN_R = 4, MAX_R = 20;

  const TYPE_COLORS = {
    client: "#4dd6e0", project: "#9a8cff", meeting: "#6f8cc0",
    person: "#6fce9f", invoice: "#e0b64d", note: "#8b93a3",
    pdf: "#c08f6f", file: "#6b7280",
  };
  function colorFor(t) { return TYPE_COLORS[t] || TYPE_COLORS.file; }

  class Graph {
    constructor(canvas, opts) {
      this.canvas = canvas;
      this.ctx = canvas.getContext("2d");
      this.opts = opts || {};
      this.nodes = [];
      this.edges = [];
      this.byId = new Map();
      this.scale = 1;
      this.tx = 0; this.ty = 0;      // pan
      this.alpha = 1;
      this.hover = null;
      this.focus = null;
      this.pathSet = new Set();      // node ids on a traced path
      this.pathEdges = new Set();    // "a|b" keys on the path
      this.hidden = new Set();       // hidden types (filters)
      this.pulses = [];
      this.dragging = null;          // node being dragged
      this.panning = false;
      this.down = null;              // {x,y,moved}
      this._resize();
      window.addEventListener("resize", () => this._resize());
      this._wire();
      this._idleTimer = setInterval(() => this._spawnPulse(), 2800);
      requestAnimationFrame(() => this._frame());
    }

    // ---- data ----
    setData(graph) {
      const cx = this.w / 2, cy = this.h / 2;
      this.nodes = graph.nodes.map((n, i) => {
        const ang = (i / graph.nodes.length) * Math.PI * 2;
        const rad = 60 + Math.random() * 220;
        return {
          id: n.id, title: n.title, type: n.type, degree: n.degree,
          excerpt: n.excerpt, note: n.note, links: n.links || [],
          x: cx + Math.cos(ang) * rad, y: cy + Math.sin(ang) * rad,
          vx: 0, vy: 0, fixed: false,
          r: MIN_R + Math.min(1, n.degree / 12) * (MAX_R - MIN_R),
          color: colorFor(n.type),
        };
      });
      this.byId = new Map(this.nodes.map((n) => [n.id, n]));
      this.edges = [];
      for (const e of graph.edges) {
        const a = this.byId.get(e.source), b = this.byId.get(e.target);
        if (a && b) this.edges.push({ a, b });
      }
      this.alpha = 1;
    }

    setHidden(types) { this.hidden = new Set(types); }
    _visible(n) { return !this.hidden.has(n.type); }

    focusNode(id) {
      const n = this.byId.get(id);
      if (!n) return;
      this.focus = n;
      this.pathSet.clear(); this.pathEdges.clear();
      this._center(n);
      if (this.opts.onFocus) this.opts.onFocus(n);
    }

    showPath(ids) {
      this.pathSet = new Set(ids);
      this.pathEdges = new Set();
      for (let i = 0; i < ids.length - 1; i++) {
        this.pathEdges.add(this._ekey(ids[i], ids[i + 1]));
      }
    }

    _ekey(a, b) { return a < b ? a + "|" + b : b + "|" + a; }

    // ---- view ----
    _resize() {
      const dpr = window.devicePixelRatio || 1;
      this.w = window.innerWidth; this.h = window.innerHeight;
      this.canvas.width = this.w * dpr;
      this.canvas.height = this.h * dpr;
      this.canvas.style.width = this.w + "px";
      this.canvas.style.height = this.h + "px";
      this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    }
    _toWorld(sx, sy) { return { x: (sx - this.tx) / this.scale, y: (sy - this.ty) / this.scale }; }
    _center(n) {
      this.tx = this.w * 0.52 - n.x * this.scale;
      this.ty = this.h / 2 - n.y * this.scale;
    }

    // ---- physics (spatial grid, near-linear) ----
    _step() {
      if (this.alpha < ALPHA_MIN) this.alpha = ALPHA_MIN; // breathe forever
      const cell = CUTOFF;
      const grid = new Map();
      const key = (cx, cy) => cx + "," + cy;
      for (const n of this.nodes) {
        const cx = Math.floor(n.x / cell), cy = Math.floor(n.y / cell);
        const k = key(cx, cy);
        (grid.get(k) || grid.set(k, []).get(k)).push(n);
        n._cx = cx; n._cy = cy;
      }
      // repulsion: only against nodes in the 9 neighbouring cells
      for (const n of this.nodes) {
        let fx = 0, fy = 0;
        for (let gx = n._cx - 1; gx <= n._cx + 1; gx++) {
          for (let gy = n._cy - 1; gy <= n._cy + 1; gy++) {
            const bucket = grid.get(key(gx, gy));
            if (!bucket) continue;
            for (const m of bucket) {
              if (m === n) continue;
              let dx = n.x - m.x, dy = n.y - m.y;
              let d2 = dx * dx + dy * dy;
              if (d2 > CUTOFF * CUTOFF) continue;
              if (d2 < 0.01) { dx = Math.random() - 0.5; dy = Math.random() - 0.5; d2 = 0.01; }
              const d = Math.sqrt(d2);
              const f = REPULSION / d2;
              fx += (dx / d) * f; fy += (dy / d) * f;
            }
          }
        }
        n.vx = (n.vx + fx * this.alpha) ;
        n.vy = (n.vy + fy * this.alpha) ;
      }
      // spring attraction along edges
      for (const e of this.edges) {
        let dx = e.b.x - e.a.x, dy = e.b.y - e.a.y;
        const d = Math.sqrt(dx * dx + dy * dy) || 0.01;
        const f = SPRING * (d - SPRING_LEN);
        const ox = (dx / d) * f, oy = (dy / d) * f;
        e.a.vx += ox; e.a.vy += oy;
        e.b.vx -= ox; e.b.vy -= oy;
      }
      // gravity + integrate
      const cx = this.w / 2, cy = this.h / 2;
      for (const n of this.nodes) {
        n.vx += (cx - n.x) * GRAVITY;
        n.vy += (cy - n.y) * GRAVITY;
        n.vx *= DAMP; n.vy *= DAMP;
        if (n === this.dragging || n.fixed) { n.vx = 0; n.vy = 0; continue; }
        n.x += n.vx; n.y += n.vy;
      }
      this.alpha *= ALPHA_DECAY;
    }

    // ---- idle pulse along a random link ----
    _spawnPulse() {
      if (!this.edges.length || document.hidden) return;
      const e = this.edges[(Math.random() * this.edges.length) | 0];
      if (!this._visible(e.a) || !this._visible(e.b)) return;
      this.pulses.push({ e, t: 0 });
    }

    // ---- render ----
    _frame() {
      this._step();
      const ctx = this.ctx;
      ctx.clearRect(0, 0, this.w, this.h);
      ctx.save();
      ctx.translate(this.tx, this.ty);
      ctx.scale(this.scale, this.scale);

      const hv = this.hover;
      const hoodl = hv ? new Set(hv.links) : null;
      const isLit = (n) => !hv || n === hv || (hoodl && hoodl.has(n.id)) ||
                           (n.links && n.links.indexOf(hv.id) >= 0);

      // edges
      ctx.lineWidth = 1 / this.scale;
      for (const e of this.edges) {
        if (!this._visible(e.a) || !this._visible(e.b)) continue;
        const onPath = this.pathEdges.has(this._ekey(e.a.id, e.b.id));
        const lit = !hv || isLit(e.a) && isLit(e.b) || e.a === hv || e.b === hv;
        if (onPath) { ctx.strokeStyle = "rgba(77,214,224,0.9)"; ctx.lineWidth = 2 / this.scale; }
        else if (hv) { ctx.strokeStyle = (e.a === hv || e.b === hv) ? "rgba(77,214,224,0.45)" : "rgba(255,255,255,0.03)"; ctx.lineWidth = 1 / this.scale; }
        else { ctx.strokeStyle = "rgba(255,255,255,0.07)"; ctx.lineWidth = 1 / this.scale; }
        ctx.beginPath();
        ctx.moveTo(e.a.x, e.a.y);
        ctx.lineTo(e.b.x, e.b.y);
        ctx.stroke();
      }

      // pulses
      for (let i = this.pulses.length - 1; i >= 0; i--) {
        const p = this.pulses[i];
        p.t += 0.012;
        if (p.t >= 1) { this.pulses.splice(i, 1); continue; }
        const { a, b } = p.e;
        const x = a.x + (b.x - a.x) * p.t, y = a.y + (b.y - a.y) * p.t;
        ctx.beginPath();
        ctx.arc(x, y, 2.4 / this.scale, 0, Math.PI * 2);
        ctx.fillStyle = "rgba(77,214,224," + (0.8 * (1 - Math.abs(0.5 - p.t) * 2)) + ")";
        ctx.fill();
      }

      // nodes
      for (const n of this.nodes) {
        if (!this._visible(n)) continue;
        const lit = isLit(n);
        const onPath = this.pathSet.has(n.id);
        ctx.globalAlpha = lit ? 1 : 0.1;
        if (n === this.focus || onPath || n === hv) {
          ctx.beginPath();
          ctx.arc(n.x, n.y, n.r + 5, 0, Math.PI * 2);
          ctx.fillStyle = "rgba(77,214,224,0.16)";
          ctx.fill();
        }
        ctx.beginPath();
        ctx.arc(n.x, n.y, n.r, 0, Math.PI * 2);
        ctx.fillStyle = n.color;
        if (n === hv) { ctx.shadowColor = n.color; ctx.shadowBlur = 18; }
        ctx.fill();
        ctx.shadowBlur = 0;
      }
      ctx.globalAlpha = 1;

      // labels — most-connected first, reject collisions
      const placed = [];
      ctx.font = "12px ui-sans-serif, system-ui, sans-serif";
      ctx.textBaseline = "middle";
      const sorted = this.nodes.slice().sort((a, b) => b.degree - a.degree);
      for (const n of sorted) {
        if (!this._visible(n)) continue;
        const lit = isLit(n);
        if (!lit && this.scale < 1.4) continue;
        // only label the bigger/hovered/focused nodes unless zoomed in
        if (n.degree < 3 && this.scale < 1.25 && n !== hv && n !== this.focus) continue;
        const label = n.title.length > 26 ? n.title.slice(0, 25) + "…" : n.title;
        const w = ctx.measureText(label).width;
        const lx = n.x + n.r + 5 / this.scale * this.scale; // screen-space-ish offset
        const sx = n.x * this.scale + this.tx + n.r + 6;
        const sy = n.y * this.scale + this.ty;
        const box = { x: sx, y: sy - 8, w: w + 6, h: 16 };
        let hit = false;
        for (const p of placed) {
          if (box.x < p.x + p.w && box.x + box.w > p.x && box.y < p.y + p.h && box.y + box.h > p.y) { hit = true; break; }
        }
        if (hit) continue;
        placed.push(box);
        // draw in screen space for crisp text
        ctx.save();
        ctx.setTransform(window.devicePixelRatio || 1, 0, 0, window.devicePixelRatio || 1, 0, 0);
        ctx.globalAlpha = lit ? 0.92 : 0.28;
        ctx.fillStyle = (n === this.focus || this.pathSet.has(n.id)) ? "#4dd6e0" : "#cfd4de";
        ctx.fillText(label, box.x, box.y + 8);
        ctx.restore();
      }
      ctx.globalAlpha = 1;
      ctx.restore();
      requestAnimationFrame(() => this._frame());
    }

    // ---- interaction ----
    _nodeAt(sx, sy) {
      const w = this._toWorld(sx, sy);
      let best = null, bestD = 1e9;
      for (const n of this.nodes) {
        if (!this._visible(n)) continue;
        const dx = n.x - w.x, dy = n.y - w.y;
        const d = Math.sqrt(dx * dx + dy * dy);
        if (d < n.r + 6 / this.scale && d < bestD) { best = n; bestD = d; }
      }
      return best;
    }

    _wire() {
      const c = this.canvas;
      c.addEventListener("mousemove", (ev) => {
        const sx = ev.clientX, sy = ev.clientY;
        if (this.dragging) {
          const w = this._toWorld(sx, sy);
          this.dragging.x = w.x; this.dragging.y = w.y;
          this.dragging.fixed = true;
          this.alpha = Math.max(this.alpha, 0.3);
          return;
        }
        if (this.panning && this.down) {
          this.tx += sx - this.down.x; this.ty += sy - this.down.y;
          this.down.x = sx; this.down.y = sy; this.down.moved = true;
          return;
        }
        this.hover = this._nodeAt(sx, sy);
        c.style.cursor = this.hover ? "pointer" : "grab";
      });
      c.addEventListener("mousedown", (ev) => {
        const n = this._nodeAt(ev.clientX, ev.clientY);
        this.down = { x: ev.clientX, y: ev.clientY, moved: false, shift: ev.shiftKey, node: n };
        if (n) { this.dragging = n; }
        else { this.panning = true; c.classList.add("grabbing"); }
      });
      window.addEventListener("mouseup", (ev) => {
        const d = this.down;
        c.classList.remove("grabbing");
        if (d && d.node && !d.moved) {
          // a click (not a drag) on a node
          if (d.shift && this.focus && this.focus !== d.node) {
            if (this.opts.onPath) this.opts.onPath(this.focus.id, d.node.id);
          } else {
            this.focusNode(d.node.id);
          }
        }
        if (this.dragging) this.dragging.fixed = false;
        this.dragging = null; this.panning = false; this.down = null;
      });
      c.addEventListener("wheel", (ev) => {
        ev.preventDefault();
        const factor = ev.deltaY < 0 ? 1.12 : 1 / 1.12;
        const ns = Math.max(0.25, Math.min(4, this.scale * factor));
        // zoom toward cursor
        const wx = (ev.clientX - this.tx) / this.scale;
        const wy = (ev.clientY - this.ty) / this.scale;
        this.scale = ns;
        this.tx = ev.clientX - wx * ns;
        this.ty = ev.clientY - wy * ns;
      }, { passive: false });
    }
  }

  window.Graph = Graph;
  window.TYPE_COLORS = TYPE_COLORS;
})();
