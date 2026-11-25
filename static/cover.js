/* Chipper Studio: thumbnail editor.
   A free canvas like the video editor: a clean frame from the stream as the background, then any number of
   photos (cropped, shaped, bordered), text and stickers you can drag, resize and rotate. The cover is drawn
   in the browser at 1080x1920 and saved as the clip's thumbnail, with the design kept for later changes. */
(() => {
  const W = 1080, H = 1920;
  const CV = $('#ceCanvas'), CX = CV.getContext('2d'), BOX = $('#coverEd .edbox'), WRAP = $('#ceWrap');
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const uid = () => Math.random().toString(36).slice(2, 9);
  const phone = () => matchMedia('(max-width: 900px)').matches;
  const IMGS = {};
  const img = src => {
    if (!src) return null;
    if (!IMGS[src]) { const i = new Image(); i.onload = () => draw(); i.src = src; IMGS[src] = i; }
    return IMGS[src];
  };
  const CE = {name: '', clip: null, d: null, sel: null, hist: [], hi: -1, crop: false, dur: 10, t: 0, ptr: new Map()};
  const FONTS = () => [...new Set([...(CFG.fonts || []).map(f => f.family), 'Anton', 'Bebas Neue', 'Impact', 'Arial Black', 'Montserrat', 'Pricedown Black', 'Poppins', 'Arial'])];
  const TEXT_LOOKS = {
    beast: {font: 'Arial Black', color: '#ffe11a', stroke: '#000000', strokeW: 16, box: '', shadow: 1, upper: true, label: 'Yellow punch'},
    white: {font: 'Arial Black', color: '#ffffff', stroke: '#000000', strokeW: 14, box: '', shadow: 1, upper: true, label: 'White outline'},
    red: {font: 'Arial Black', color: '#ffffff', stroke: '#000000', strokeW: 0, box: '#e8173c', shadow: 0, upper: true, label: 'Red box'},
    clean: {font: 'Montserrat', color: '#111111', stroke: '#000000', strokeW: 0, box: '#ffffff', shadow: 0, upper: false, label: 'White box'},
    neon: {font: 'Impact', color: '#ffffff', stroke: '#ff3d6e', strokeW: 10, box: '', shadow: 1, upper: true, label: 'Pink outline'},
    gta: {font: 'Pricedown Black', color: '#ffe11a', stroke: '#000000', strokeW: 14, box: '', shadow: 1, upper: true, label: 'GTA'},
  };
  const STICKERS = ['😱', '🤯', '😂', '💀', '🔥', '😳', '🤑', '👀', '😡', '🥶', '💰', '🏆', '❌', '✅', '⚠️', '💯'];
  const SHAPES = [['arrow', 'Arrow'], ['ring', 'Circle'], ['box', 'Box'], ['x', 'Cross']];

  /* ------------------------------------------------------------------ design helpers */
  const L = () => CE.d.layers;
  const selL = () => L().find(l => l.id === CE.sel) || null;
  function newPhoto(src, nw, nh, o = {}) {
    const ar = nw && nh ? nh / nw : 0.5625, w = o.w || 620;
    return {kind: 'photo', id: uid(), src, nw: nw || 1600, nh: nh || 900, x: o.x || 540, y: o.y || 1300, w, h: o.h || Math.round(w * ar), rot: 0,
      shape: o.shape || 'round', radius: 0.08, border: o.border ?? 10, bcolor: o.bcolor || '#ffffff', shadow: 1, flip: false, crop: {z: 1, ox: 0.5, oy: 0.5}};
  }
  function newText(text, look = 'beast', o = {}) {
    return {kind: 'text', id: uid(), text, x: o.x || 540, y: o.y || 380, size: o.size || 120, rot: o.rot || 0, maxW: 960, align: 'center', ...TEXT_LOOKS[look]};
  }
  function newSticker(t, o = {}) {
    const emoji = !SHAPES.find(s => s[0] === t);
    return {kind: 'sticker', id: uid(), t, emoji, x: o.x || 760, y: o.y || 900, size: o.size || (emoji ? 220 : 300), rot: o.rot || 0, color: o.color || '#ff2a2a', lw: 22};
  }
  const hookText = () => (CE.clip && (CE.clip.hook || CE.clip.yt_title)) || 'NO WAY';

  /* ------------------------------------------------------------------ drawing */
  function rrPath(c, x, y, w, h, r) { r = Math.min(r, w / 2, h / 2); c.beginPath(); c.moveTo(x + r, y); c.arcTo(x + w, y, x + w, y + h, r); c.arcTo(x + w, y + h, x, y + h, r); c.arcTo(x, y + h, x, y, r); c.arcTo(x, y, x + w, y, r); c.closePath(); }
  function shapePath(c, l) {
    const w = l.w, h = l.h;
    if (l.shape === 'circle') { c.beginPath(); c.ellipse(0, 0, w / 2, h / 2, 0, 0, Math.PI * 2); return; }
    rrPath(c, -w / 2, -h / 2, w, h, l.shape === 'round' ? Math.min(w, h) * (l.radius || 0.08) : 0);
  }
  function drawCover(c, i, x, y, w, h, crop) {   // image fills the box; crop.z zooms in, ox/oy pick the visible part
    const iw = i.naturalWidth, ih = i.naturalHeight; if (!iw) return;
    const s = Math.max(w / iw, h / ih) * (crop.z || 1), dw = iw * s, dh = ih * s;
    c.drawImage(i, x - (dw - w) * crop.ox, y - (dh - h) * crop.oy, dw, dh);
  }
  function bgDraw(c) {
    const bg = CE.d.bg;
    c.fillStyle = bg.color || '#000'; c.fillRect(0, 0, W, H);
    const i = img(bg.src);
    if (bg.kind !== 'color' && i && i.naturalWidth) {
      c.save(); c.filter = `brightness(${bg.bright}) contrast(${bg.contrast}) saturate(${bg.sat})${bg.blur ? ` blur(${bg.blur}px)` : ''}`;
      drawCover(c, i, 0, 0, W, H, {z: bg.z, ox: bg.ox, oy: bg.oy}); c.restore();
      if (bg.vignette) { const g = c.createRadialGradient(W / 2, H / 2, H * 0.25, W / 2, H / 2, H * 0.75); g.addColorStop(0, 'rgba(0,0,0,0)'); g.addColorStop(1, `rgba(0,0,0,${bg.vignette})`); c.fillStyle = g; c.fillRect(0, 0, W, H); }
    }
  }
  function textLines(l) {   // wraps to the cover width so long hooks never run off the edge
    const t = l.upper ? String(l.text).toUpperCase() : String(l.text), mw = (l.maxW || 960) - (l.box ? l.size * 0.7 : l.strokeW), out = [];
    CX.save(); CX.font = fontOf(l);
    t.split('\n').forEach(par => { let cur = ''; par.split(/\s+/).forEach(w => { const nx = cur ? cur + ' ' + w : w; if (cur && CX.measureText(nx).width > mw) { out.push(cur); cur = w; } else cur = nx; }); out.push(cur); });
    CX.restore(); return out;
  }
  function fontOf(l) { return `900 ${l.size}px "${l.font}", "Arial Black", Impact, sans-serif`; }
  function textSize(c, l) {
    c.font = fontOf(l); const ls = textLines(l), w = Math.max(...ls.map(s => c.measureText(s).width), 10);
    return [w + (l.box ? l.size * 0.7 : l.strokeW), ls.length * l.size * 1.08 + (l.box ? l.size * 0.45 : l.strokeW)];
  }
  function layerSize(l) {
    if (l.kind === 'photo') return [l.w, l.h];
    if (l.kind === 'text') return textSize(CX, l);
    return [l.size, l.t === 'arrow' ? l.size * 0.55 : l.size];
  }
  function drawLayer(c, l) {
    c.save(); c.translate(l.x, l.y); c.rotate((l.rot || 0) * Math.PI / 180);
    if (l.kind === 'photo') {
      const i = img(l.src);
      if (l.shadow) { c.save(); c.shadowColor = 'rgba(0,0,0,.55)'; c.shadowBlur = 40; c.shadowOffsetY = 14; shapePath(c, l); c.fillStyle = '#000'; c.fill(); c.restore(); }
      c.save(); shapePath(c, l); c.clip();
      if (l.flip) c.scale(-1, 1);
      if (i && i.naturalWidth) drawCover(c, i, -l.w / 2, -l.h / 2, l.w, l.h, l.crop); else { c.fillStyle = '#333'; c.fillRect(-l.w / 2, -l.h / 2, l.w, l.h); }
      c.restore();
      if (l.border) { shapePath(c, l); c.lineWidth = l.border; c.strokeStyle = l.bcolor; c.stroke(); }
    } else if (l.kind === 'text') {
      const ls = textLines(l), lh = l.size * 1.08; c.font = fontOf(l); c.textAlign = 'center'; c.textBaseline = 'middle'; c.lineJoin = 'round';
      const y0 = -lh * (ls.length - 1) / 2;
      if (l.box) { const [w, h] = textSize(c, l); c.fillStyle = l.box; rrPath(c, -w / 2, -h / 2, w, h, l.size * 0.18); c.fill(); }
      ls.forEach((s, k) => {
        const y = y0 + k * lh;
        if (l.shadow) { c.save(); c.fillStyle = 'rgba(0,0,0,.6)'; c.fillText(s, 6, y + 10); c.restore(); }
        if (l.strokeW) { c.strokeStyle = l.stroke; c.lineWidth = l.strokeW; c.strokeText(s, 0, y); }
        c.fillStyle = l.color; c.fillText(s, 0, y);
      });
    } else if (l.emoji) {
      c.font = `${l.size}px "Segoe UI Emoji","Apple Color Emoji","Noto Color Emoji",sans-serif`; c.textAlign = 'center'; c.textBaseline = 'middle'; c.fillText(l.t, 0, 0);
    } else {
      const s = l.size, lw = l.lw; c.strokeStyle = l.color; c.fillStyle = l.color; c.lineWidth = lw; c.lineCap = 'round'; c.lineJoin = 'round';
      c.shadowColor = 'rgba(0,0,0,.45)'; c.shadowBlur = 12;
      if (l.t === 'ring') { c.beginPath(); c.ellipse(0, 0, s / 2 - lw, s / 2 - lw, 0, 0, Math.PI * 2); c.stroke(); }
      else if (l.t === 'box') { c.strokeRect(-s / 2 + lw / 2, -s / 2 + lw / 2, s - lw, s - lw); }
      else if (l.t === 'x') { c.beginPath(); c.moveTo(-s / 2 + lw, -s / 2 + lw); c.lineTo(s / 2 - lw, s / 2 - lw); c.moveTo(s / 2 - lw, -s / 2 + lw); c.lineTo(-s / 2 + lw, s / 2 - lw); c.stroke(); }
      else { const h = s * 0.55; c.beginPath(); c.moveTo(-s / 2, -h * 0.18); c.lineTo(s * 0.12, -h * 0.18); c.lineTo(s * 0.12, -h / 2); c.lineTo(s / 2, 0); c.lineTo(s * 0.12, h / 2); c.lineTo(s * 0.12, h * 0.18); c.lineTo(-s / 2, h * 0.18); c.closePath(); c.fill(); }
    }
    c.restore();
  }
  function render(c, withUi) {
    bgDraw(c);
    L().forEach(l => { if (!l.hidden) drawLayer(c, l); });
    if (!withUi) return;
    const l = selL(); if (!l) return;
    const [w, h] = layerSize(l), k = W / CV.clientWidth;
    c.save(); c.translate(l.x, l.y); c.rotate((l.rot || 0) * Math.PI / 180);
    c.strokeStyle = CE.crop ? '#ffd400' : '#2ee6c8'; c.lineWidth = 3 * k; c.setLineDash(CE.crop ? [14 * k, 10 * k] : []); c.strokeRect(-w / 2, -h / 2, w, h); c.setLineDash([]);
    if (!CE.crop) {
      const r = 13 * k;
      c.fillStyle = '#fff'; c.strokeStyle = '#2ee6c8'; c.lineWidth = 3 * k;
      c.beginPath(); c.arc(w / 2, h / 2, r, 0, Math.PI * 2); c.fill(); c.stroke();                        // resize
      c.beginPath(); c.moveTo(0, -h / 2); c.lineTo(0, -h / 2 - 54 * k); c.stroke();
      c.beginPath(); c.arc(0, -h / 2 - 54 * k, r, 0, Math.PI * 2); c.fill(); c.stroke();                  // rotate
    }
    c.restore();
  }
  let raf = 0;
  function draw() { cancelAnimationFrame(raf); raf = requestAnimationFrame(() => { if (CE.d) render(CX, true); }); }

  /* ------------------------------------------------------------------ history */
  function commit() {
    const s = JSON.stringify(CE.d); if (CE.hist[CE.hi] === s) return;
    CE.hist = CE.hist.slice(0, CE.hi + 1); CE.hist.push(s); if (CE.hist.length > 80) CE.hist.shift(); CE.hi = CE.hist.length - 1; syncUndo();
  }
  function syncUndo() { $('#ceUndo').disabled = CE.hi <= 0; $('#ceRedo').disabled = CE.hi >= CE.hist.length - 1; }
  function restore(i) { CE.hi = i; CE.d = JSON.parse(CE.hist[i]); if (!selL()) CE.sel = null; syncUndo(); props(); panel(CE.tool); draw(); }
  $('#ceUndo').onclick = () => CE.hi > 0 && restore(CE.hi - 1);
  $('#ceRedo').onclick = () => CE.hi < CE.hist.length - 1 && restore(CE.hi + 1);
  const act = fn => (...a) => { fn(...a); draw(); commit(); };

  /* ------------------------------------------------------------------ canvas interaction */
  const toCv = e => { const r = CV.getBoundingClientRect(); return [(e.clientX - r.left) / r.width * W, (e.clientY - r.top) / r.height * H]; };
  function local(l, x, y) { const a = -(l.rot || 0) * Math.PI / 180, dx = x - l.x, dy = y - l.y; return [dx * Math.cos(a) - dy * Math.sin(a), dx * Math.sin(a) + dy * Math.cos(a)]; }
  function hit(x, y) { for (const l of [...L()].reverse()) { if (l.hidden) continue; const [w, h] = layerSize(l), [u, v] = local(l, x, y); if (Math.abs(u) <= w / 2 + 10 && Math.abs(v) <= h / 2 + 10) return l; } return null; }
  function handleAt(l, x, y) {
    const [w, h] = layerSize(l), [u, v] = local(l, x, y), k = W / CV.clientWidth, r = 30 * k;
    if (Math.hypot(u - w / 2, v - h / 2) < r) return 'scale';
    if (Math.hypot(u, v + h / 2 + 54 * k) < r) return 'rotate';
    return null;
  }
  function scaleLayer(l, f, s0) {
    if (l.kind === 'photo') { l.w = clamp(s0.w * f, 60, 3000); l.h = clamp(s0.h * f, 60, 4000); }
    else l.size = clamp(s0.size * f, 20, 900);
  }
  let G = null;
  CV.addEventListener('pointerdown', e => {
    CV.setPointerCapture(e.pointerId); CE.ptr.set(e.pointerId, toCv(e));
    const [x, y] = toCv(e), s = selL();
    if (CE.ptr.size === 2 && s) {   // two fingers: pinch to resize, twist to rotate (or zoom the crop)
      const [a, b] = [...CE.ptr.values()];
      G = {mode: 'pinch', l: s, d0: Math.hypot(a[0] - b[0], a[1] - b[1]), a0: Math.atan2(b[1] - a[1], b[0] - a[0]), rot0: s.rot || 0, s0: {...s, crop: {...(s.crop || {})}}};
      return;
    }
    if (CE.crop && s && s.kind === 'photo') { G = {mode: 'crop', l: s, x, y, c0: {...s.crop}}; return; }
    const hnd = s && handleAt(s, x, y);
    if (hnd) { const [u, v] = local(s, x, y); G = {mode: hnd, l: s, d0: Math.hypot(u, v), s0: {...s}, a0: Math.atan2(y - s.y, x - s.x), rot0: s.rot || 0}; return; }
    const h = hit(x, y);
    if (h) { CE.sel = h.id; G = {mode: 'move', l: h, x, y, x0: h.x, y0: h.y}; props(); draw(); }
    else { CE.sel = null; CE.crop = false; G = null; props(); draw(); }
  });
  CV.addEventListener('pointermove', e => {
    if (!CE.ptr.has(e.pointerId)) return; CE.ptr.set(e.pointerId, toCv(e));
    if (!G) return;
    const [x, y] = toCv(e), l = G.l;
    if (G.mode === 'pinch' && CE.ptr.size === 2) {
      const [a, b] = [...CE.ptr.values()], d = Math.hypot(a[0] - b[0], a[1] - b[1]), ang = Math.atan2(b[1] - a[1], b[0] - a[0]);
      if (CE.crop && l.kind === 'photo') l.crop.z = clamp(G.s0.crop.z * d / G.d0, 1, 6);
      else { scaleLayer(l, d / G.d0, G.s0); l.rot = Math.round(G.rot0 + (ang - G.a0) * 180 / Math.PI); }
    } else if (G.mode === 'move') {
      let nx = G.x0 + x - G.x, ny = G.y0 + y - G.y;
      CE.guides = [];
      if (Math.abs(nx - W / 2) < 18) { nx = W / 2; CE.guides.push('x'); }
      if (Math.abs(ny - H / 2) < 18) { ny = H / 2; CE.guides.push('y'); }
      l.x = Math.round(nx); l.y = Math.round(ny);
    } else if (G.mode === 'scale') { const [u, v] = local(G.s0, x, y); scaleLayer(l, Math.hypot(u, v) / Math.max(10, G.d0), G.s0); }
    else if (G.mode === 'rotate') { let r = G.rot0 + (Math.atan2(y - l.y, x - l.x) - G.a0) * 180 / Math.PI; if (Math.abs(((r % 90) + 90) % 90) < 4 || Math.abs(((r % 90) + 90) % 90) > 86) r = Math.round(r / 90) * 90; l.rot = Math.round(r); }
    else if (G.mode === 'crop') {
      const [w, h] = layerSize(l), i = img(l.src), iw = i?.naturalWidth || l.nw, ih = i?.naturalHeight || l.nh;
      const sc = Math.max(w / iw, h / ih) * l.crop.z, ex = iw * sc - w, ey = ih * sc - h, [du, dv] = local({x: 0, y: 0, rot: l.rot}, x - G.x, y - G.y);
      l.crop.ox = ex > 1 ? clamp(G.c0.ox - (l.flip ? -du : du) / ex, 0, 1) : 0.5; l.crop.oy = ey > 1 ? clamp(G.c0.oy - dv / ey, 0, 1) : 0.5;
    }
    draw(); propsLive();
  });
  const up = e => { CE.ptr.delete(e.pointerId); if (G && CE.ptr.size === 0) { G = null; CE.guides = []; commit(); draw(); } else if (G && G.mode === 'pinch') G = null; };
  CV.addEventListener('pointerup', up); CV.addEventListener('pointercancel', up);
  CV.addEventListener('dblclick', e => { const l = hit(...toCv(e)); if (l && l.kind === 'photo') { CE.sel = l.id; CE.crop = true; props(); draw(); } else if (l && l.kind === 'text') { CE.sel = l.id; props(); setTimeout(() => $('#ceProps textarea')?.focus(), 30); } });
  CV.addEventListener('wheel', e => {
    const l = selL(); if (!l) return; e.preventDefault();
    const f = e.deltaY < 0 ? 1.06 : 1 / 1.06;
    if (CE.crop && l.kind === 'photo') l.crop.z = clamp(l.crop.z * f, 1, 6); else scaleLayer(l, f, {...l});
    draw(); propsLive(); clearTimeout(CE.wt); CE.wt = setTimeout(commit, 300);
  }, {passive: false});
  document.addEventListener('keydown', e => {
    if (!$('#coverEd').classList.contains('on') || /INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) return;
    const l = selL(), ctrl = e.ctrlKey || e.metaKey;
    if (ctrl && e.key.toLowerCase() === 'z') { e.preventDefault(); e.shiftKey ? $('#ceRedo').click() : $('#ceUndo').click(); return; }
    if (ctrl && e.key.toLowerCase() === 'y') { e.preventDefault(); $('#ceRedo').click(); return; }
    if (e.key === 'Escape') { if (CE.crop) { CE.crop = false; props(); draw(); } else if (l) { CE.sel = null; props(); draw(); } else closeCE(); return; }
    if (!l) return;
    if (e.key === 'Delete' || e.key === 'Backspace') { e.preventDefault(); removeSel(); }
    const st = e.shiftKey ? 20 : 4, mv = {ArrowLeft: [-st, 0], ArrowRight: [st, 0], ArrowUp: [0, -st], ArrowDown: [0, st]}[e.key];
    if (mv) { e.preventDefault(); l.x += mv[0]; l.y += mv[1]; draw(); clearTimeout(CE.kt); CE.kt = setTimeout(commit, 300); }
  });

  /* ------------------------------------------------------------------ layer actions */
  function addLayer(l) { L().push(l); CE.sel = l.id; CE.crop = false; props(); draw(); commit(); }
  function removeSel() { const l = selL(); if (!l) return; CE.d.layers = L().filter(x => x !== l); CE.sel = null; CE.crop = false; props(); draw(); commit(); }
  function order(d) { const l = selL(), a = L(), i = a.indexOf(l), j = clamp(i + d, 0, a.length - 1); if (i < 0 || i === j) return; a.splice(i, 1); a.splice(j, 0, l); draw(); commit(); if (CE.tool === 'layers') panel('layers'); }

  /* ------------------------------------------------------------------ server: frames and photos */
  async function grab(t) {   // clean frame of the stream at this moment (no captions)
    const j = await api(`/api/clips/${encodeURIComponent(CE.name)}/thumb/photo`, {t});
    return j.ok ? {src: `/thumb/${encodeURIComponent(j.file)}`, w: j.w, h: j.h} : null;
  }
  async function upload(file) {
    const fd = new FormData(); fd.append('file', file);
    const j = await fetch(`/api/clips/${encodeURIComponent(CE.name)}/thumb/photo`, {method: 'POST', body: fd}).then(r => r.json()).catch(() => ({}));
    if (!j.ok) { toast(j.error || 'Upload failed', true); return null; }
    return {src: `/thumb/${encodeURIComponent(j.file)}`, w: j.w, h: j.h};
  }

  /* ------------------------------------------------------------------ layouts */
  const LAYOUTS = {
    hook: ['Big hook', async (f) => [newText(hookText(), 'beast', {y: 360, size: 132})]],
    reaction: ['Reaction bubble', async (f) => [newPhoto(f.src, f.w, f.h, {x: 300, y: 1480, w: 460, h: 460, shape: 'circle', border: 14}), newText(hookText(), 'white', {y: 330, size: 118}), newSticker('😱', {x: 800, y: 1450, size: 240})]],
    split: ['Top and bottom', async (f) => [newPhoto(f.src, f.w, f.h, {x: 540, y: 520, w: 1080, h: 820, shape: 'rect', border: 0}), newText(hookText(), 'red', {y: 1000, size: 92})]],
    versus: ['Side by side', async (f) => [newPhoto(f.src, f.w, f.h, {x: 290, y: 1050, w: 500, h: 700, shape: 'round', border: 10, bcolor: '#ffffff'}), newPhoto(f.src, f.w, f.h, {x: 790, y: 1050, w: 500, h: 700, shape: 'round', border: 10, bcolor: '#ffe11a'}), newSticker('arrow', {x: 540, y: 1520, size: 260, color: '#ffe11a'}), newText(hookText(), 'beast', {y: 420, size: 110})]],
    card: ['Photo card', async (f) => [newPhoto(f.src, f.w, f.h, {x: 540, y: 980, w: 900, h: 506, shape: 'round', border: 12}), newText(hookText(), 'clean', {y: 560, size: 82}), newSticker('🔥', {x: 900, y: 1270, size: 200})]],
    headline: ['Headline bar', async (f) => [newText(hookText(), 'red', {y: 1500, size: 100, rot: -3})]],
    clean: ['Clean frame', async () => []],
  };
  async function applyLayout(k) {
    const f = await ensureFrame();
    const bg = CE.d.bg;
    if (k === 'split' || k === 'card' || k === 'versus') { bg.blur = k === 'split' ? 0 : 22; bg.bright = k === 'split' ? 1 : 0.7; } else { bg.blur = 0; bg.bright = 1.05; }
    CE.d.layers = await LAYOUTS[k][1](f); CE.d.layout = k; CE.sel = null; props(); draw(); commit();
  }
  async function ensureFrame() {
    if (CE.d.bg.src) return {src: CE.d.bg.src, w: CE.d.bg.nw, h: CE.d.bg.nh};
    const f = await grab(CE.t); if (f) Object.assign(CE.d.bg, {src: f.src, nw: f.w, nh: f.h}); return f || {src: '', w: 16, h: 9};
  }

  /* ------------------------------------------------------------------ panels */
  const TOOLS = {frame: 'Frame', layouts: 'Layouts', photos: 'Photos', text: 'Text', stickers: 'Stickers', layers: 'Layers'};
  CE.tool = 'layouts';
  const esc2 = s => esc(String(s ?? ''));
  function panel(t) {
    CE.tool = t; $$('#coverEd [data-cetool]').forEach(b => b.classList.toggle('on', b.dataset.cetool === t));
    $('#ceToolName').textContent = TOOLS[t];
    const P = $('#cePanel'), bg = CE.d.bg;
    if (t === 'frame') {
      P.innerHTML = `<div class="sect">Pick the moment</div>
        <video id="ceVid" muted playsinline preload="auto" style="width:100%;aspect-ratio:9/16;max-height:300px;background:#000;border-radius:12px;object-fit:contain"></video>
        <input type="range" id="ceT" min="0" max="${CE.dur}" step="0.05" value="${CE.t}" style="width:100%;margin-top:8px">
        <div class="row" style="gap:8px;margin-top:6px"><button class="btn pri" id="ceUseBg">Use as background</button><button class="btn" id="ceUseAsPhoto">Add as a photo</button></div>
        <div class="hint">Frames come from the stream itself, without captions. "Add as a photo" is handy for cropping the facecam.</div>
        <div class="sect">Background</div>
        <div class="seg" id="ceBgKind"><button data-v="frame">Frame</button><button data-v="color">Colour</button></div>
        <div class="pgrid" style="margin-top:10px">
          <span class="mut">Zoom</span><input type="range" data-bg="z" min="1" max="3" step="0.01" value="${bg.z}">
          <span class="mut">Left / right</span><input type="range" data-bg="ox" min="0" max="1" step="0.005" value="${bg.ox}">
          <span class="mut">Up / down</span><input type="range" data-bg="oy" min="0" max="1" step="0.005" value="${bg.oy}">
          <span class="mut">Brightness</span><input type="range" data-bg="bright" min="0.4" max="1.6" step="0.01" value="${bg.bright}">
          <span class="mut">Contrast</span><input type="range" data-bg="contrast" min="0.6" max="1.8" step="0.01" value="${bg.contrast}">
          <span class="mut">Colour</span><input type="range" data-bg="sat" min="0" max="2" step="0.01" value="${bg.sat}">
          <span class="mut">Blur</span><input type="range" data-bg="blur" min="0" max="40" step="1" value="${bg.blur}">
          <span class="mut">Dark edges</span><input type="range" data-bg="vignette" min="0" max="0.9" step="0.01" value="${bg.vignette}">
          <span class="mut">Colour</span><input type="color" data-bg="color" value="${bg.color}">
        </div>
        <div class="row" style="margin-top:10px"><label class="btn sm" style="cursor:pointer"><svg class="i" aria-hidden="true"><use href="#i-upload"/></svg>Upload a background<input type="file" id="ceBgUp" accept="image/*" hidden></label></div>`;
      const v = $('#ceVid'); v.src = `/media/${encodeURIComponent(CE.name)}.mp4?${CE.clip.rendered || ''}`; v.addEventListener('loadedmetadata', () => { v.currentTime = CE.t; }, {once: true});
      $('#ceT').oninput = e => { CE.t = +e.target.value; v.currentTime = CE.t; };
      $('#ceUseBg').onclick = async () => { const f = await grab(CE.t); if (f) { Object.assign(CE.d.bg, {src: f.src, nw: f.w, nh: f.h, kind: 'frame'}); CE.d.t = CE.t; draw(); commit(); toast('Background updated'); } };
      $('#ceUseAsPhoto').onclick = async () => { const f = await grab(CE.t); if (f) addLayer(newPhoto(f.src, f.w, f.h)); };
      $('#ceBgUp').onchange = async e => { const fl = e.target.files[0]; e.target.value = ''; if (!fl) return; const f = await upload(fl); if (f) { Object.assign(CE.d.bg, {src: f.src, nw: f.w, nh: f.h, kind: 'frame'}); draw(); commit(); } };
      $$('#ceBgKind button').forEach(b => { b.classList.toggle('on', (bg.kind || 'frame') === b.dataset.v); b.onclick = act(() => { bg.kind = b.dataset.v; panel('frame'); }); });
      $$('#cePanel [data-bg]').forEach(i => { i.oninput = () => { bg[i.dataset.bg] = i.type === 'color' ? i.value : +i.value; draw(); }; i.onchange = () => commit(); });
    } else if (t === 'layouts') {
      P.innerHTML = `<div class="hint" style="margin-top:0">A layout replaces the photos, text and stickers on the cover. Undo brings yours back.</div>
        <div class="celayouts">${Object.entries(LAYOUTS).map(([k, v]) => `<button data-lay="${k}" class="${CE.d.layout === k ? 'on' : ''}"><canvas width="108" height="192"></canvas><span>${esc2(v[0])}</span></button>`).join('')}</div>`;
      $$('#cePanel [data-lay]').forEach(async b => {
        b.onclick = () => applyLayout(b.dataset.lay);
        const f = {src: CE.d.bg.src || '', w: CE.d.bg.nw || 16, h: CE.d.bg.nh || 9};
        const mini = {bg: {...CE.d.bg, blur: 0}, layers: await LAYOUTS[b.dataset.lay][1](f)};
        const c = b.querySelector('canvas').getContext('2d'); c.scale(108 / W, 192 / H);
        const keep = CE.d; CE.d = mini; try { render(c, false); } finally { CE.d = keep; }
      });
    } else if (t === 'photos') {
      P.innerHTML = `<div class="row" style="gap:8px;flex-wrap:wrap"><label class="btn pri" style="cursor:pointer"><svg class="i" aria-hidden="true"><use href="#i-upload"/></svg>Upload photos<input type="file" id="cePhUp" accept="image/*" multiple hidden></label>
        <button class="btn" id="cePhGrab"><svg class="i" aria-hidden="true"><use href="#i-film"/></svg>Frame from the clip</button></div>
        <div class="hint">Drag photos on the cover, use the corner to resize, the top dot to rotate. Double-click or press Crop to choose the part that shows.</div>
        <div class="sect">Your images</div><div class="cegrid" id="ceLib"><span class="mut">Loading…</span></div>`;
      $('#cePhUp').onchange = async e => {
        const fs = [...e.target.files]; e.target.value = '';
        for (const [k, fl] of fs.entries()) { const f = await upload(fl); if (f) addLayer(newPhoto(f.src, f.w, f.h, {x: 540 + k * 40, y: 1100 + k * 60})); }
      };
      $('#cePhGrab').onclick = async () => { const f = await grab(CE.t); if (f) addLayer(newPhoto(f.src, f.w, f.h)); };
      api('/api/assets/images').then(j => {
        const fs = (j.ok && j.files) || [];
        $('#ceLib').innerHTML = fs.map(f => `<button data-f="${esc2(f)}"><img src="/assets/${encodeURIComponent(f)}" loading="lazy" alt=""></button>`).join('') || '<span class="mut">Images you upload in the video editor show here too.</span>';
        $$('#ceLib [data-f]').forEach(b => b.onclick = () => { const i = b.querySelector('img'); addLayer(newPhoto('/assets/' + encodeURIComponent(b.dataset.f), i.naturalWidth, i.naturalHeight, {shape: 'rect', border: 0, w: 520})); });
      });
    } else if (t === 'text') {
      P.innerHTML = `<button class="btn pri" id="ceAddText" style="width:100%;min-height:48px"><svg class="i" aria-hidden="true"><use href="#i-type"/></svg>Add text</button>
        <div class="sect">Text styles</div><div class="celooks">${Object.entries(TEXT_LOOKS).map(([k, v]) => `<button data-look="${k}" style="--c:${v.color};--s:${v.stroke};--b:${v.box || 'transparent'};font-family:'${v.font}'"><span>${esc2(v.label)}</span></button>`).join('')}</div>
        <div class="hint">Tap a style to add text in it, or to restyle the selected text.</div>
        <div class="sect">Quick words</div><div class="chips">${['NO WAY', 'GONE WRONG', 'HE DID IT', 'INSANE', '1 IN A MILLION', 'WAIT FOR IT', '$1,000,000', 'CAUGHT ON STREAM'].map(w => `<button data-qw="${esc2(w)}">${esc2(w)}</button>`).join('')}</div>`;
      $('#ceAddText').onclick = () => addLayer(newText(hookText()));
      $$('#cePanel [data-look]').forEach(b => b.onclick = () => { const l = selL(); if (l && l.kind === 'text') { Object.assign(l, TEXT_LOOKS[b.dataset.look]); delete l.label; props(); draw(); commit(); } else addLayer(newText(hookText(), b.dataset.look)); });
      $$('#cePanel [data-qw]').forEach(b => b.onclick = () => addLayer(newText(b.dataset.qw, 'beast', {y: 760})));
    } else if (t === 'stickers') {
      P.innerHTML = `<div class="sect">Shapes</div><div class="chips">${SHAPES.map(([k, n]) => `<button data-stk="${k}">${n}</button>`).join('')}</div>
        <div class="sect">Emoji</div><div class="ceemoji">${STICKERS.map(s => `<button data-stk="${s}">${s}</button>`).join('')}</div>`;
      $$('#cePanel [data-stk]').forEach(b => b.onclick = () => addLayer(newSticker(b.dataset.stk)));
    } else if (t === 'layers') {
      const names = {photo: 'Photo', text: 'Text', sticker: 'Sticker'};
      P.innerHTML = [...L()].reverse().map(l => `<div class="lyr ${l.id === CE.sel ? 'sel' : ''} ${l.hidden ? 'off' : ''}" data-id="${l.id}"><b>${esc2(l.kind === 'text' ? l.text.slice(0, 30) : l.kind === 'sticker' ? (l.emoji ? l.t : SHAPES.find(s => s[0] === l.t)[1]) : names[l.kind])}</b>
        <button data-la="up" aria-label="Bring forward">▲</button><button data-la="down" aria-label="Send back">▼</button><button data-la="vis" aria-label="Show or hide">${l.hidden ? '○' : '●'}</button><button data-la="del" aria-label="Delete">✕</button></div>`).join('')
        + `<div class="lyr" style="opacity:.6"><b>Background</b></div><div class="hint">Top of the list is in front.</div>`;
      $$('#cePanel .lyr[data-id]').forEach(row => {
        const l = L().find(x => x.id === row.dataset.id);
        row.onclick = e => {
          const a = e.target.dataset.la; CE.sel = l.id;
          if (a === 'up') return order(1); if (a === 'down') return order(-1);
          if (a === 'vis') { l.hidden = !l.hidden; commit(); }
          if (a === 'del') return removeSel();
          props(); draw(); panel('layers');
        };
      });
    }
    $('#ceBody').scrollTop = 0;
  }
  $$('#coverEd [data-cetool]').forEach(b => b.onclick = () => {
    if (phone() && CE.tool === b.dataset.cetool && BOX.classList.contains('sheet')) return sheet(false);
    panel(b.dataset.cetool); if (phone()) sheet(true);
  });

  /* ------------------------------------------------------------------ properties of the selected layer */
  function props() {
    const l = selL(), P = $('#ceProps');
    if (!l) { P.innerHTML = ''; if (phone() && BOX.classList.contains('propsonly')) sheet(false); return; }
    const rng = (k, label, min, max, step, v) => `<span class="mut">${label}</span><input type="range" data-p="${k}" min="${min}" max="${max}" step="${step}" value="${v}">`;
    let h = `<h4>${l.kind === 'photo' ? 'Photo' : l.kind === 'text' ? 'Text' : 'Sticker'}<span class="sp2">
      ${l.kind === 'photo' ? `<button class="btn sm ${CE.crop ? 'pri' : ''}" data-a="crop">${CE.crop ? 'Done' : 'Crop'}</button>` : ''}
      <button class="btn sm" data-a="center" title="Centre">⇔</button><button class="btn sm" data-a="up" title="Bring forward">▲</button><button class="btn sm" data-a="down" title="Send back">▼</button>
      <button class="btn sm" data-a="dup" title="Duplicate">⧉</button><button class="btn sm" data-a="del" title="Delete"><svg class="i" aria-hidden="true" style="margin:0"><use href="#i-trash"/></svg></button></span></h4><div class="pgrid">`;
    if (l.kind === 'photo') {
      h += (CE.crop ? rng('crop.z', 'Crop zoom', 1, 6, 0.01, l.crop.z) : '') + rng('w', 'Width', 60, 1600, 1, l.w) + rng('h', 'Height', 60, 2200, 1, l.h)
        + `<span class="mut">Shape</span><div class="seg" data-seg="shape">${[['rect', 'Square'], ['round', 'Rounded'], ['circle', 'Circle']].map(([k, n]) => `<button data-v="${k}" class="${l.shape === k ? 'on' : ''}">${n}</button>`).join('')}</div>`
        + rng('border', 'Border', 0, 40, 1, l.border) + `<span class="mut">Border colour</span><input type="color" data-p="bcolor" value="${l.bcolor}">`
        + rng('rot', 'Rotate', -180, 180, 1, l.rot || 0)
        + `<span class="mut">More</span><div class="row"><label><input type="checkbox" data-p="shadow" ${l.shadow ? 'checked' : ''}> Shadow</label><label><input type="checkbox" data-p="flip" ${l.flip ? 'checked' : ''}> Mirror</label>
           <button class="btn sm" data-a="square">Square</button><button class="btn sm" data-a="fit">Original shape</button></div>`;
    } else if (l.kind === 'text') {
      h += `<span class="mut">Words</span><textarea data-p="text" rows="2" style="width:100%">${esc2(l.text)}</textarea>
        <span class="mut">Font</span><select data-p="font">${FONTS().map(f => `<option ${f === l.font ? 'selected' : ''}>${esc2(f)}</option>`).join('')}</select>`
        + rng('size', 'Size', 24, 400, 1, l.size)
        + `<span class="mut">Colours</span><div class="row"><label class="mut">Text <input type="color" data-p="color" value="${l.color}"></label><label class="mut">Outline <input type="color" data-p="stroke" value="${l.stroke}"></label><label class="mut">Box <input type="color" data-p="boxc" value="${l.box || '#000000'}"></label></div>`
        + rng('strokeW', 'Outline', 0, 40, 1, l.strokeW) + rng('rot', 'Rotate', -180, 180, 1, l.rot || 0)
        + `<span class="mut">More</span><div class="row"><label><input type="checkbox" data-p="boxon" ${l.box ? 'checked' : ''}> Box</label><label><input type="checkbox" data-p="shadow" ${l.shadow ? 'checked' : ''}> Shadow</label><label><input type="checkbox" data-p="upper" ${l.upper ? 'checked' : ''}> CAPITALS</label></div>`;
    } else {
      h += rng('size', 'Size', 40, 1000, 1, l.size) + rng('rot', 'Rotate', -180, 180, 1, l.rot || 0) + (l.emoji ? '' : `<span class="mut">Colour</span><input type="color" data-p="color" value="${l.color}">` + rng('lw', 'Line', 6, 60, 1, l.lw));
    }
    P.innerHTML = h + '</div>';
    $$('#ceProps [data-p]').forEach(i => {
      const k = i.dataset.p;
      const set = () => {
        const v = i.type === 'checkbox' ? i.checked : i.type === 'range' ? +i.value : i.value;
        if (k === 'crop.z') l.crop.z = v; else if (k === 'boxon') l.box = v ? ($('#ceProps [data-p="boxc"]').value || '#000000') : '';
        else if (k === 'boxc') { if (l.box) l.box = v; } else l[k] = v;
        draw();
      };
      i.addEventListener('input', set); i.addEventListener('change', () => { set(); commit(); });
    });
    $$('#ceProps [data-seg] button').forEach(b => b.onclick = act(() => { l[b.closest('[data-seg]').dataset.seg] = b.dataset.v; if (b.dataset.v === 'circle' && Math.abs(l.w - l.h) > 4) l.h = l.w; props(); }));
    $$('#ceProps [data-a]').forEach(b => b.onclick = () => {
      const a = b.dataset.a;
      if (a === 'crop') { CE.crop = !CE.crop; props(); draw(); return; }
      if (a === 'del') return removeSel();
      if (a === 'up') return order(1); if (a === 'down') return order(-1);
      if (a === 'dup') return addLayer({...JSON.parse(JSON.stringify(l)), id: uid(), x: l.x + 40, y: l.y + 40});
      if (a === 'center') l.x = W / 2;
      if (a === 'square') l.h = l.w;
      if (a === 'fit') { const i = img(l.src); if (i && i.naturalWidth) l.h = Math.round(l.w * i.naturalHeight / i.naturalWidth); l.crop = {z: 1, ox: 0.5, oy: 0.5}; }
      props(); draw(); commit();
    });
    if (phone() && !BOX.classList.contains('sheet')) { BOX.classList.add('propsonly'); sheet(true, 'compact'); }
  }
  function propsLive() {
    const l = selL(); if (!l) return;
    $$('#ceProps input[type=range][data-p]').forEach(i => { const k = i.dataset.p, v = k === 'crop.z' ? l.crop.z : l[k]; if (v !== undefined && document.activeElement !== i) i.value = v; });
  }

  /* ------------------------------------------------------------------ sheet on phones, sizing */
  function sheet(on, size = 'mid') {
    BOX.classList.toggle('sheet', on); BOX.classList.remove('sheet-compact', 'sheet-mid', 'sheet-full');
    if (!on) BOX.classList.remove('propsonly');
    else {
      BOX.classList.add('sheet-' + size);
      const bar = $('#coverEd .edbar').offsetHeight; BOX.style.setProperty('--bar-px', bar + 'px');
      BOX.style.setProperty('--sheet-h', Math.round((BOX.clientHeight - bar) * (size === 'compact' ? 0.34 : 0.5)) + 'px');
      if (size !== 'compact') BOX.classList.remove('propsonly');
    }
    setTimeout(fit, 240);
  }
  $('#ceSheetX').onclick = () => { if (phone()) sheet(false); else { CE.sel = null; CE.crop = false; props(); draw(); } };
  function fit() {
    const b = $('#cePreview'); if (!b) return;
    const w = b.clientWidth - 24, h = b.clientHeight - 24; if (w <= 0 || h <= 0) return;
    const ww = Math.min(w, h * 9 / 16); WRAP.style.width = Math.floor(ww) + 'px'; WRAP.style.height = Math.floor(ww * 16 / 9) + 'px'; draw();
  }
  new ResizeObserver(fit).observe($('#cePreview'));

  /* ------------------------------------------------------------------ open, save, close */
  function defaults() { return {v: 2, t: 0, layout: '', bg: {kind: 'frame', src: '', nw: 1920, nh: 1080, z: 1, ox: 0.5, oy: 0.5, bright: 1.05, contrast: 1.1, sat: 1.15, blur: 0, vignette: 0.35, color: '#101018'}, layers: []}; }
  window.openCoverEditor = async clip => {
    CE.clip = clip; CE.name = clip.name; CE.sel = null; CE.crop = false; CE.hist = []; CE.hi = -1;
    $('#coverEd').classList.add('on'); $('#ceName').textContent = clip.yt_title || clip.hook || clip.name;
    const j = await api(`/api/clips/${encodeURIComponent(clip.name)}/thumb`);
    CE.dur = (j.ok && j.duration) || clip.duration || 10;
    if (j.ok && j.design && j.design.v === 2) { CE.d = j.design; CE.t = CE.d.t || 0; }
    else {
      CE.d = defaults(); CE.t = +(CE.dur * 0.35).toFixed(2); CE.d.t = CE.t;
      const f = await grab(CE.t); if (f) Object.assign(CE.d.bg, {src: f.src, nw: f.w, nh: f.h});
      CE.d.layers = await LAYOUTS.hook[1](f); CE.d.layout = 'hook';
    }
    commit(); props(); panel(phone() ? 'layouts' : 'layouts');
    if (phone()) sheet(false);
    fit(); setTimeout(fit, 120);
  };
  function closeCE() { $('#coverEd').classList.remove('on'); sheet(false); const v = $('#ceVid'); if (v) { v.pause(); v.removeAttribute('src'); } }
  $('#ceClose').onclick = () => { if (CE.hi > 0 && !confirm('Close without saving the thumbnail?')) return; closeCE(); };
  $('#ceSave').onclick = async () => {
    const off = document.createElement('canvas'); off.width = W; off.height = H;
    const keep = CE.sel; CE.sel = null; render(off.getContext('2d'), false); CE.sel = keep;
    let data; try { data = off.toDataURL('image/jpeg', 0.92); } catch (e) { return toast('Could not draw the thumbnail (a picture failed to load)', true); }
    $('#ceSave').disabled = true;
    const j = await api(`/api/clips/${encodeURIComponent(CE.name)}/cover/design`, {image: data, design: {...CE.d, t: CE.t}});
    $('#ceSave').disabled = false;
    if (j.ok) { toast('Thumbnail saved'); closeCE(); if (window.onCoverSaved) window.onCoverSaved(CE.name, j.v); }
  };
  $('#ceDl').onclick = () => {
    const off = document.createElement('canvas'); off.width = W; off.height = H; const keep = CE.sel; CE.sel = null; render(off.getContext('2d'), false); CE.sel = keep;
    const a = document.createElement('a'); a.href = off.toDataURL('image/jpeg', 0.92); a.download = CE.name + '_thumbnail.jpg'; a.click();
  };
})();
