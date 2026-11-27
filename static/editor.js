/* Chipper Studio - portrait clip editor.
   Everything lives in a "scene" (1080x1920 coordinates): background, video windows (a crop of the stream inside a
   shape), images, text, captions, hook and lower thirds. The server renders the same scene with ffmpeg. */
(() => {
  const W = 1080, H = 1920;
  const ED = {clip: null, scene: null, words: [], start: 0, end: 0, dur: 0, sel: null, raf: 0, safe: false,
              view: {z: 1, tx: 0, ty: 0}, hist: [], hi: -1, imgs: {}, guides: [], third: 'darkpill', plat: 'youtube', thOpts: {font: '', color: '', size: 1}, ptrs: new Map(), g: null};
  const V = document.createElement('video');
  V.playsInline = true; V.preload = 'auto'; V.muted = false;
  const PH = $('#edPrev'), PX = PH.getContext('2d');
  const K = PH.width / W;                       // canvas pixels per scene unit
  const WRAP = $('#edStageWrap'), STAGE = $('#edStage');
  const TINY = document.createElement('canvas'); TINY.width = 27; TINY.height = 48;
  const TX = TINY.getContext('2d');
  const fmt = t => `${Math.floor(t / 60)}:${(t % 60).toFixed(1).padStart(4, '0')}`;
  const uid = () => Math.random().toString(36).slice(2, 9);
  const clone = o => JSON.parse(JSON.stringify(o));
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const even = n => Math.round(n / 2) * 2;
  let FONTS = ['Arial Black', 'Impact', 'Arial', 'Segoe UI Black', 'Verdana', 'Georgia', 'Comic Sans MS', 'Courier New', 'Trebuchet MS'];
  const loadedFonts = new Set();
  function useFonts(list) {          // extra fonts from Chipper's fonts folder (e.g. Pricedown) for the live preview
    (list || []).forEach(f => {
      if (loadedFonts.has(f.family)) return; loadedFonts.add(f.family);
      try { new FontFace(f.family, `url(/fonts/${encodeURIComponent(f.file)})`).load().then(ff => document.fonts.add(ff)).catch(() => {}); } catch (e) { /* old browser */ }
    });
    FONTS = [...new Set([...(list || []).map(f => f.family), ...(CFG.system_fonts || FONTS), ...FONTS])];
    const sel = $('#edCapFont'); if (sel) { const v = sel.value; sel.innerHTML = '<option value="">Style default</option>' + FONTS.map(f => `<option>${esc(f)}</option>`).join(''); sel.value = v; }
  }

  /* ================================================================ shapes (mirrors engine.shape_points) */
  function rrPts(w, h, tl, tr, br, bl, seg = 10) {
    const pts = [];
    for (const [cx, cy, r, a0, sharp] of [[w - tr, tr, tr, -90, [w, 0]], [w - br, h - br, br, 0, [w, h]], [bl, h - bl, bl, 90, [0, h]], [tl, tl, tl, 180, [0, 0]]]) {
      if (r <= 0.5) { pts.push(sharp); continue; }
      for (let i = 0; i <= seg; i++) { const a = (a0 + 90 * i / seg) * Math.PI / 180; pts.push([cx + r * Math.cos(a), cy + r * Math.sin(a)]); }
    }
    return pts;
  }
  function fitBox(pts, w, h) {
    const xs = pts.map(p => p[0]), ys = pts.map(p => p[1]);
    const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
    return pts.map(([x, y]) => [(x - x0) / ((x1 - x0) || 1) * w, (y - y0) / ((y1 - y0) || 1) * h]);
  }
  function shapePoints(shape, w, h, radius = 0.12) {
    const m = Math.min(w, h), P = Math.PI;
    if (shape === 'round') { const r = clamp(radius, 0, 0.5) * m; return rrPts(w, h, r, r, r, r); }
    if (shape === 'pill') { const r = m / 2; return rrPts(w, h, r, r, r, r); }
    if (shape === 'arch') { const r = Math.min(w / 2, h); return rrPts(w, h, r, r, 0, 0); }
    if (shape === 'circle') return [...Array(96)].map((_, i) => [w / 2 + w / 2 * Math.cos(2 * P * i / 96), h / 2 + h / 2 * Math.sin(2 * P * i / 96)]);
    if (shape === 'squircle') return fitBox([...Array(96)].map((_, i) => { const a = 2 * P * i / 96, c = Math.cos(a), s = Math.sin(a); return [Math.sign(c) * Math.abs(c) ** 0.5, Math.sign(s) * Math.abs(s) ** 0.5]; }), w, h);
    if (shape === 'heart') return fitBox([...Array(120)].map((_, i) => { const t = 2 * P * i / 120; return [16 * Math.sin(t) ** 3, -(13 * Math.cos(t) - 5 * Math.cos(2 * t) - 2 * Math.cos(3 * t) - Math.cos(4 * t))]; }), w, h);
    if (shape === 'star') return fitBox([...Array(10)].map((_, i) => { const a = (-90 + 36 * i) * P / 180, r = i % 2 ? 0.48 : 1; return [r * Math.cos(a), r * Math.sin(a)]; }), w, h);
    if (shape === 'hexagon') return fitBox([...Array(6)].map((_, i) => [Math.cos(i * P / 3), Math.sin(i * P / 3)]), w, h);
    if (shape === 'diamond') return [[w / 2, 0], [w, h / 2], [w / 2, h], [0, h / 2]];
    return [[0, 0], [w, 0], [w, h], [0, h]];
  }
  const SHAPES = [['rect', 'Square corners'], ['round', 'Rounded'], ['pill', 'Pill'], ['circle', 'Circle / oval'], ['heart', 'Heart'],
                  ['star', 'Star'], ['hexagon', 'Hexagon'], ['diamond', 'Diamond'], ['arch', 'Arch'], ['squircle', 'Squircle']];
  const shapeSvg = s => { const pts = shapePoints(s, 26, 26, 0.25).map(p => `${(p[0] + 1).toFixed(1)},${(p[1] + 1).toFixed(1)}`).join(' ');
    return `<svg viewBox="0 0 28 28"><polygon points="${pts}" fill="#2ee6c8"/></svg>`; };
  function pathOf(pts, ox, oy) { PX.beginPath(); pts.forEach(([x, y], i) => i ? PX.lineTo((ox + x) * K, (oy + y) * K) : PX.moveTo((ox + x) * K, (oy + y) * K)); PX.closePath(); }

  /* ================================================================ text sizing (same estimate the thirds use) */
  function charW(ch) {
    if (ch === ' ') return 0.3; if (/[A-Z]/.test(ch)) return /[MW]/.test(ch) ? 0.98 : /[IJ]/.test(ch) ? 0.4 : 0.76;
    if (/[a-z]/.test(ch)) return /[mw]/.test(ch) ? 0.94 : /[ijlft]/.test(ch) ? 0.36 : 0.64;
    if (/[0-9]/.test(ch)) return 0.66; if (ch === '@') return 0.92; if (/[._,:;!'|]/.test(ch)) return 0.34; return 0.62;
  }
  const estW = (t, size, font = 'Arial Black') => [...String(t)].reduce((a, c) => a + charW(c), 0) * size * (font === 'Arial Black' ? 1 : 0.84);
  /* The video renderer (libass) treats a font size as the font's full line height, the browser as its em size,
     so the same number looks bigger in the browser. Scale the preview by each font's own ratio so it matches the video. */
  const FONT_K = {}, fkCtx = document.createElement('canvas').getContext('2d');
  const fontK = font => {
    if (FONT_K[font]) return FONT_K[font];
    fkCtx.font = `900 100px "${font}", "Arial Black", Arial, sans-serif`;
    const m = fkCtx.measureText('Hg'), h = (m.fontBoundingBoxAscent || 0) + (m.fontBoundingBoxDescent || 0);
    const k = h > 60 && h < 220 ? 100 / h : 0.72;
    if (document.fonts && document.fonts.check(`100px "${font}"`)) FONT_K[font] = k;     // cache once the real font is there
    return k;
  };
  if (document.fonts) document.fonts.addEventListener('loadingdone', () => { for (const k in FONT_K) delete FONT_K[k]; });
  const cssFont = (size, font = 'Arial Black', w = 900) => `${w} ${size * K * fontK(font)}px "${font}", "Arial Black", Arial, sans-serif`;

  /* ================================================================ lower thirds (prims rendered by the server too) */
  const THIRDS = {
    darkpill: {label: 'Dark pill', sub: '', gen: true},
    slant: {label: 'Slant card', sub: '', gen: true}, softpill: {label: 'Soft pill', sub: '', gen: true},
    tab: {label: 'Tab card', sub: '', gen: true},
  };
  const THIRD_ANIMS = {slide: 'Slide in', slider: 'From the right', rise: 'Rise up', drop: 'Drop in', glide: 'Slow glide', wipe: 'Wipe', fade: 'Fade', none: 'None'};
  const THIRD_MOVES = {slide: [-160, 0, 0.3], slider: [220, 0, 0.3], rise: [0, 70, 0.3], drop: [0, -140, 0.35], glide: [-420, 0, 0.9]};
  const PLATFORMS = {youtube: {label: 'YouTube', word: 'YOUTUBE', color: '#ff0000'}, instagram: {label: 'Instagram', word: 'INSTAGRAM', color: '#d62976'},
    tiktok: {label: 'TikTok', word: 'TIKTOK', color: '#111111'}, facebook: {label: 'Facebook', word: 'FACEBOOK', color: '#1877f2'},
    twitch: {label: 'Twitch', word: 'TWITCH', color: '#9146ff'}, x: {label: 'X', word: 'X', color: '#111111'}};
  /* simple platform glyphs drawn from shapes, centred on (cx, cy), about s units big */
  function glyph(plat, cx, cy, s, fg, bg) {
    const P = [], R = (x, y, w, h, r, color, o = {}) => P.push({t: 'rr', x, y, w, h, r, color, alpha: o.alpha ?? 1, bw: o.bw || 0, bc: o.bc || fg});
    const G = (pts, color) => P.push({t: 'poly', pts, color, alpha: 1});
    const T = (text, size, dx = 0, dy = 0) => P.push({t: 'text', x: cx + dx, y: cy + dy, text, size, color: fg, an: 5, font: 'Arial Black', alpha: 1, bw: 0, bc: fg});
    if (plat === 'youtube') { R(cx - s / 2, cy - s * 0.35, s, s * 0.7, s * 0.2, fg); G([[cx - s * 0.12, cy - s * 0.18], [cx + s * 0.2, cy], [cx - s * 0.12, cy + s * 0.18]], bg); }
    else if (plat === 'instagram') { R(cx - s * 0.44, cy - s * 0.44, s * 0.88, s * 0.88, s * 0.26, fg, {alpha: 0, bw: s * 0.06}); R(cx - s * 0.2, cy - s * 0.2, s * 0.4, s * 0.4, s * 0.2, fg, {alpha: 0, bw: s * 0.06}); R(cx + s * 0.18, cy - s * 0.3, s * 0.1, s * 0.1, s * 0.05, fg); }
    else if (plat === 'tiktok') {
      for (const [d, c] of [[-s * 0.04, '#25f4ee'], [s * 0.04, '#fe2c55'], [0, fg]]) {
        R(cx + s * 0.02 + d, cy - s * 0.45, s * 0.14, s * 0.62, 0, c); R(cx - s * 0.32 + d, cy + s * 0.02, s * 0.4, s * 0.34, s * 0.17, c);
        G([[cx + s * 0.16 + d, cy - s * 0.45], [cx + s * 0.42 + d, cy - s * 0.3], [cx + s * 0.42 + d, cy - s * 0.14], [cx + s * 0.16 + d, cy - s * 0.26]], c);
      }
    }
    else if (plat === 'facebook') T('f', s * 1.15, s * 0.04, s * 0.08);
    else if (plat === 'twitch') { R(cx - s * 0.42, cy - s * 0.45, s * 0.84, s * 0.72, s * 0.08, fg); G([[cx - s * 0.3, cy + s * 0.26], [cx - s * 0.02, cy + s * 0.26], [cx - s * 0.3, cy + s * 0.5]], fg);
      R(cx - s * 0.16, cy - s * 0.28, s * 0.1, s * 0.3, 0, bg); R(cx + s * 0.08, cy - s * 0.28, s * 0.1, s * 0.3, 0, bg); }
    else T('X', s * 0.95, 0, s * 0.04);
    return P;
  }
  const wavy = (cx, cy, r, n = 12, amp = 0.07) => [...Array(96)].map((_, i) => { const a = 2 * Math.PI * i / 96, rr = r * (1 - amp + amp * Math.cos(n * a)); return [cx + rr * Math.cos(a), cy + rr * Math.sin(a)]; });
  function buildGen(tpl, title, sub, acc, plat, opts) {
    const pf = PLATFORMS[plat] || PLATFORMS.youtube, col = acc || pf.color, head = sub || `FOLLOW ME ON ${pf.word}`;
    const O = opts || {}, zs = clamp(+O.size || 1, 0.6, 1.6);   // the channel name's own font, colour and size
    const P = [], R = (x, y, w, h, r, color, o = {}) => P.push({t: 'rr', x, y, w, h, r, color, alpha: o.alpha ?? 1, bw: o.bw || 0, bc: o.bc || '#ffffff', blur: o.blur || 0});
    const G = (pts, color, o = {}) => P.push({t: 'poly', pts, color, alpha: o.alpha ?? 1, bw: o.bw || 0, bc: o.bc || '#ffffff', blur: o.blur || 0});
    const T = (x, y, text, size, color, o = {}) => P.push({t: 'text', x, y, text, size, color, an: o.an || 4, font: o.font || 'Arial', alpha: o.alpha ?? 1, bw: 0, bc: '#000000', italic: !!o.italic, weight: o.weight || 700});
    const glyphOn = (cx, cy, s, fg, bg) => P.push(...glyph(plat, cx, cy, s, fg, bg));
    title = title || '@yourusername';
    if (tpl === 'badge') {
      const hw = estW(head, 18, 'Arial'), nw = estW(title, 30, 'Arial'), Wd = 124 + Math.max(hw, nw) + 34;
      R(104, 58, Wd - 104, 48, 0, '#000000', {alpha: 0.28, blur: 8});
      R(100, 18, Wd - 100, 32, 0, col); T(126, 34, head, 18, '#ffffff');
      R(100, 50, Wd - 100, 50, 0, '#ffffff'); T(126, 75, title.toUpperCase(), 30, '#4a4a4a');
      G(wavy(64, 64, 62).map(([x, y]) => [x + 3, y + 6]), '#000000', {alpha: 0.3, blur: 6}); G(wavy(60, 60, 62), col);
      glyphOn(60, 60, 58, '#ffffff', col);
    } else if (tpl === 'darkpill') {
      const Wd = 138 + estW(title, 38 * zs, O.font || 'Arial') * 0.92 + 56;
      R(0, 0, Wd, 112, 56, '#262c38', {alpha: 0.96});
      glyphOn(68, 56, 62, '#ffffff', '#262c38');
      T(138, 56, title, 38 * zs, O.color || '#9aa1ad', {weight: O.font ? 700 : 400, font: O.font || 'Arial'});
    } else if (tpl === 'slant') {
      const hw = estW(head, 17, 'Arial'), nw = estW(title, 32 * zs, O.font || 'Arial'), Wd = 128 + Math.max(hw, nw) + 76;
      G([[8, 6], [Wd - 40, 6], [Wd + 6, 110], [30, 110]], '#000000', {alpha: 0.22, blur: 8});
      G([[0, 0], [Wd - 46, 0], [Wd, 104], [24, 104]], '#ffffff', {bw: 3, bc: col});
      R(14, 6, 92, 92, 46, '#ffffff', {bw: 4, bc: col}); R(20, 12, 80, 80, 40, col);
      glyphOn(60, 52, 46, '#ffffff', col);
      T(128, 34, head.replace(/^FOLLOW ME ON/, 'FIND ME ON'), 17, '#222222', {weight: 400}); T(128, 70, title, 32 * zs, O.color || '#111111', {italic: !O.font, font: O.font || 'Arial'});
    } else if (tpl === 'softpill') {
      const Wd = 122 + estW(title, 30 * zs, O.font || 'Arial') * 0.92 + 44;
      R(56, 26, Wd - 56, 62, 31, '#000000', {alpha: 0.18, blur: 7}); R(52, 18, Wd - 52, 62, 31, '#f2f2f2');
      R(2, 6, 96, 96, 48, '#000000', {alpha: 0.2, blur: 6}); R(0, 0, 96, 96, 48, '#ffffff'); R(8, 8, 80, 80, 40, col);
      glyphOn(48, 48, 44, '#ffffff', col);
      T(122, 49, title, 30 * zs, O.color || '#9a9a9a', {weight: O.font ? 700 : 400, font: O.font || 'Arial'});
    } else {
      const hw = estW(head, 15, 'Arial'), nw = estW(title, 34 * zs, O.font || 'Arial Black'), Wd = 106 + nw + 40;
      R(62, 0, hw + 40, 30, 10, col); T(82, 15, head, 15, '#ffffff');
      R(40, 26, Wd - 40, 74, 18, '#ffffff', {alpha: 0.94, bw: 3, bc: col});
      R(0, 22, 82, 82, 41, col, {bw: 4, bc: '#ffffff'});
      glyphOn(41, 63, 40, '#ffffff', col);
      T(104, 63, title, 34 * zs, O.color || col, {font: O.font || 'Arial Black', weight: 900});
    }
    return P;
  }
  /* ---------------- frames: X post, hook box, breaking news, meme, cinema (drawn as one full-screen 'third') */
  const FRAMES = {xlight: 'X post', xdark: 'X post dark', hookbox: 'Hook box', news: 'Breaking news', meme: 'Meme top text', cinema: 'Cinema'};
  function wrapW(text, size, font, maxW) {
    const out = []; let cur = '';
    String(text || '').split(/\n/).forEach(par => { par.split(/\s+/).filter(Boolean).forEach(w => {
      const t = cur ? cur + ' ' + w : w; if (estW(t, size, font) > maxW && cur) { out.push(cur); cur = w; } else cur = t; }); if (cur) { out.push(cur); cur = ''; } });
    return out.length ? out : [''];
  }
  function buildFrame(kind, name, handle, acc, body) {
    const P = [], R = (x, y, w, h, r, color, o = {}) => P.push({t: 'rr', x, y, w, h, r, color, alpha: o.alpha ?? 1, bw: o.bw || 0, bc: o.bc || '#ffffff', blur: o.blur || 0});
    const G = (pts, color) => P.push({t: 'poly', pts, color, alpha: 1});
    const T = (x, y, text, size, color, o = {}) => P.push({t: 'text', x, y, text, size, color, an: o.an || 4, font: o.font || 'Arial', alpha: o.alpha ?? 1, bw: o.bw || 0, bc: o.bc || '#000000', italic: !!o.italic, weight: o.weight || 700});
    name = name || 'Your Channel'; handle = handle || '@' + name.toLowerCase().replace(/[^a-z0-9_]/g, ''); body = (body || '').trim() || 'He really did that...';
    const VW = 1000, VH = Math.round(VW * 9 / 16);
    let video, capY, bg, hideHook = true;
    if (kind === 'xlight' || kind === 'xdark') {
      const dark = kind === 'xdark', fg = dark ? '#e7e9ea' : '#0f1419', mut = dark ? '#71767b' : '#536471';
      const lines = wrapW(body, 46, 'Arial', 960), th = 150 + lines.length * 60 + 30 + VH + 90, y0 = Math.max(60, Math.round((1920 - th) / 2) - 120);
      R(40, y0, 100, 100, 50, acc); T(90, y0 + 52, name.trim()[0].toUpperCase(), 52, '#ffffff', {an: 5, font: 'Arial Black'});
      T(160, y0 + 30, name, 40, fg, {font: 'Arial Black', weight: 900});
      const cx = 160 + estW(name, 40) + 30, cy = y0 + 30;       // verified tick
      R(cx - 17, cy - 17, 34, 34, 17, '#1d9bf0'); G([[cx - 9, cy], [cx - 3, cy + 7], [cx + 10, cy - 7], [cx + 7, cy - 10], [cx - 3, cy + 1], [cx - 6, cy - 3]], '#ffffff');
      T(160, y0 + 76, `${handle} · 2h`, 32, mut, {weight: 400});
      lines.forEach((ln, i) => T(40, y0 + 160 + i * 60, ln, 46, fg, {weight: 400}));
      const vy = y0 + 150 + lines.length * 60 + 30;
      video = {x: 40, y: vy, w: VW, h: VH, shape: 'round', radius: 0.06};
      const fy = vy + VH + 50;
      [['Reply', '1.2K'], ['Repost', '8.4K'], ['Like', '52K'], ['Views', '1.1M']].forEach(([a, b], i) => T(40 + i * 250, fy, `${b} ${a}`, 30, mut, {weight: 400}));
      R(40, fy + 40, 1000, 2, 0, mut, {alpha: 0.4});
      bg = {type: 'color', color: dark ? '#000000' : '#ffffff'}; capY = Math.min(1800, fy + 220);
    } else if (kind === 'hookbox') {
      const lines = wrapW(body.toUpperCase(), 64, 'Arial Black', 880), bh = lines.length * 80 + 70, by = 330;
      R(46, by + 8, 988, bh, 34, '#000000', {alpha: 0.35, blur: 10}); R(40, by, 1000, bh, 34, '#ffffff');
      lines.forEach((ln, i) => T(540, by + 35 + 40 + i * 80, ln, 64, '#111111', {an: 5, font: 'Arial Black', weight: 900}));
      video = {x: 0, y: by + bh + 50, w: 1080, h: 608, shape: 'rect', radius: 0};
      bg = {type: 'blur'}; capY = by + bh + 50 + 608 + 170;
    } else if (kind === 'news') {
      const vy = 520; video = {x: 0, y: vy, w: 1080, h: 608, shape: 'rect', radius: 0};
      R(40, 400, 150, 60, 10, '#e0102a'); T(115, 431, 'LIVE', 34, '#ffffff', {an: 5, font: 'Arial Black'});
      T(210, 431, name.toUpperCase(), 32, '#ffffff', {font: 'Arial Black'});
      const lines = wrapW(body.toUpperCase(), 50, 'Arial Black', 960).slice(0, 3), hy = vy + 608;
      R(0, hy, 520, 70, 0, '#e0102a'); T(30, hy + 36, 'BREAKING NEWS', 44, '#ffffff', {font: 'Arial Black', weight: 900});
      R(0, hy + 70, 1080, lines.length * 64 + 40, 0, '#ffffff');
      lines.forEach((ln, i) => T(30, hy + 70 + 52 + i * 64, ln, 50, '#111111', {font: 'Arial Black', weight: 900}));
      const ty = hy + 70 + lines.length * 64 + 40;
      R(0, ty, 1080, 64, 0, '#111111'); R(0, ty, 170, 64, 0, acc); T(85, ty + 33, handle.toUpperCase().slice(0, 9), 26, '#ffffff', {an: 5, font: 'Arial Black'});
      T(190, ty + 33, `${name} · new clips every day · follow ${handle}`, 28, '#ffffff', {weight: 400});
      bg = {type: 'blur'}; capY = Math.min(1820, ty + 230); hideHook = true;
    } else if (kind === 'meme') {
      const lines = wrapW(body, 62, 'Arial', 980), th = lines.length * 76;
      const top = Math.max(360, 560 - th / 2);
      lines.forEach((ln, i) => T(540, top + i * 76, ln, 62, '#000000', {an: 5, weight: 700}));
      video = {x: 0, y: top + th + 20, w: 1080, h: 608, shape: 'rect', radius: 0};
      bg = {type: 'color', color: '#ffffff'}; capY = top + th + 20 + 608 + 170;
    } else {     // cinema
      const lines = wrapW(body, 54, 'Georgia', 900);
      lines.forEach((ln, i) => T(540, 520 + i * 70, ln, 54, '#f2f2f2', {an: 5, font: 'Georgia', weight: 400, italic: true}));
      video = {x: 0, y: 656, w: 1080, h: 608, shape: 'rect', radius: 0};
      R(440, 1330, 200, 2, 0, acc); T(540, 1380, name.toUpperCase(), 30, '#bbbbbb', {an: 5, weight: 400});
      bg = {type: 'color', color: '#050505'}; capY = 1500;
    }
    return {prims: P, video, capY, bg, hideHook};
  }
  function applyFrame(kind) {
    const name = $('#edFrName').value.trim() || $('#edThName').value.trim() || ED.clip.channel || 'Your Channel';
    const handle = $('#edFrHandle').value.trim(), acc = $('#edFrAcc').value;
    const f = buildFrame(kind, name, handle, acc, $('#edHook').value);
    try { localStorage.setItem('chipper_frame', JSON.stringify({name: $('#edFrName').value, handle, acc})); } catch (e) { /* */ }
    const old = L().find(l => l.kind === 'third' && String(l.template).startsWith('frame:'));
    const g = L().find(l => l.kind === 'video' && l.role === 'game') || L().find(l => l.kind === 'video');
    const vid = {...videoLayer('custom', f.video.x, f.video.y, f.video.w, f.video.h, f.video.shape), radius: f.video.radius, crop: [0, 0, 1, 1], name: 'Video'};
    fitCrop(vid);
    const keep = L().filter(l => l.kind !== 'video' && l !== old);
    const third = {kind: 'third', id: uid(), name: 'Frame: ' + FRAMES[kind], template: 'frame:' + kind, platform: '', title: name, sub: handle, accent: acc,
                   prims: f.prims, x: 0, y: 0, scale: 1, start: 0, dur: 0, anim: 'none'};
    ED.scene.layers = [vid, ...keep, third];
    Object.assign(ED.scene.bg, f.bg);
    const c = capL(); if (c) { c.y = f.capY; c.x = 540; }
    const h = hookL(); if (h) h.hidden = f.hideHook;
    ED.sel = null; toast(`${FRAMES[kind]} frame. Edit the name in the boxes above, press Save & render`);
  }
  function drawFrames() {
    try { const s = JSON.parse(localStorage.getItem('chipper_frame') || '{}'); if (s.name && !$('#edFrName').value) $('#edFrName').value = s.name;
      if (s.handle && !$('#edFrHandle').value) $('#edFrHandle').value = s.handle; if (s.acc) $('#edFrAcc').value = s.acc; } catch (e) { /* */ }
    $('#edFrames').innerHTML = Object.entries(FRAMES).map(([k, v]) => `<button data-k="${k}"><canvas width="108" height="192"></canvas>${esc(v)}</button>`).join('');
    $$('#edFrames button').forEach(b => {
      const k = b.dataset.k, f = buildFrame(k, $('#edFrName').value || 'Skill Issue', $('#edFrHandle').value, $('#edFrAcc').value, $('#edHook').value || 'He really did that'), c = b.querySelector('canvas').getContext('2d'), s = 108 / 1080;
      c.fillStyle = f.bg.type === 'color' ? f.bg.color : '#3a3f4c'; c.fillRect(0, 0, 108, 192);
      c.fillStyle = '#2ee6c8'; c.fillRect(f.video.x * s, f.video.y * s, f.video.w * s, f.video.h * s);
      drawPrims(f.prims, 0, 0, 1, 1, c, s);
      b.onclick = act(() => applyFrame(k));
    });
  }
  function buildOutro(name, acc) {
    const txt = `FOLLOW ${String(name || '@yourchannel').toUpperCase()} FOR MORE`, w = Math.min(1000, estW(txt, 44) + 120);
    return [{t: 'poly', pts: [[10, 14], [w + 10, 14], [w - 30, 110], [-30, 110]], color: '#000000', alpha: 0.3, bw: 0, bc: '#000000', blur: 8},
            {t: 'poly', pts: [[0, 0], [w, 0], [w - 40, 96], [-40, 96]], color: acc || '#ff3d6e', alpha: 1, bw: 0, bc: '#ffffff', blur: 0},
            {t: 'text', x: w / 2 - 20, y: 48, text: txt, size: 44, color: '#ffffff', an: 5, font: 'Arial Black', alpha: 1, bw: 0, bc: '#000000', weight: 900}];
  }
  function buildThird(tpl, title, sub, acc, plat, opts) {
    if (tpl === 'outro') return buildOutro(title, acc);
    if (String(tpl).startsWith('frame:')) return buildFrame(tpl.slice(6), title, sub, acc, $('#edHook').value).prims;
    if (THIRDS[tpl]?.gen || ['badge', 'darkpill', 'slant', 'softpill', 'tab'].includes(tpl)) return buildGen(tpl, title, sub, acc, plat, opts || ED.thOpts);
    const P = [], T = (x, y, text, size, color, o = {}) => P.push({t: 'text', x, y, text, size, color, an: o.an || 4, font: o.font || 'Arial Black', alpha: o.alpha ?? 1, bw: o.bw || 0, bc: o.bc || '#000000'});
    const R = (x, y, w, h, r, color, o = {}) => P.push({t: 'rr', x, y, w, h, r, color, alpha: o.alpha ?? 1, bw: o.bw || 0, bc: o.bc || '#ffffff'});
    const G = (pts, color) => P.push({t: 'poly', pts, color, alpha: 1});
    title = title || '@yourchannel';
    const ns = 38, ss = 24, tw = estW(title, ns), sw = sub ? estW(sub, ss, 'Arial') : 0, txw = Math.max(tw, sw);
    const ty = sub ? 40 : 55, sy = 80;
    if (tpl === 'tiktok') {
      const bx = 112 + txw + 24, Wd = bx + 150 + 22;
      R(0, 0, Wd, 110, 55, '#000000', {alpha: 0.85});
      R(14, 14, 82, 82, 41, '#ffffff');
      for (const [dx, c] of [[-3, '#25f4ee'], [3, '#fe2c55'], [0, '#111111']]) {
        R(52 + dx, 30, 10, 40, 0, c); R(31 + dx, 58, 28, 22, 11, c); G([[62 + dx, 30], [76 + dx, 36], [76 + dx, 46], [62 + dx, 40]], c);
      }
      T(110, ty, title, ns, '#25f4ee', {alpha: 0.9}); T(114, ty, title, ns, '#fe2c55', {alpha: 0.9}); T(112, ty, title, ns, '#ffffff');
      if (sub) T(112, sy, sub, ss, '#bbbbbb', {font: 'Arial'});
      R(bx, 27, 150, 56, 12, acc); T(bx + 75, 55, 'Follow', 28, '#ffffff', {an: 5});
    } else if (tpl === 'youtube') {
      const bx = 132 + txw + 26, Wd = bx + 200 + 22;
      R(0, 0, Wd, 120, 22, '#ffffff', {alpha: 0.97});
      R(18, 26, 96, 68, 18, '#ff0000'); G([[55, 42], [84, 60], [55, 78]], '#ffffff');
      T(132, sub ? 44 : 60, title, 36, '#0f0f0f'); if (sub) T(132, 84, sub, ss, '#606060', {font: 'Arial'});
      R(bx, 30, 200, 60, 30, acc); T(bx + 100, 60, 'SUBSCRIBE', 26, '#ffffff', {an: 5});
    } else if (tpl === 'instagram') {
      const Wd = 104 + txw + 36;
      R(0, 0, Wd, 106, 53, '#833ab4'); R(Wd * 0.38, 0, Wd * 0.62, 106, 53, '#fd1d1d', {alpha: 0.6}); R(Wd * 0.72, 0, Wd * 0.28, 106, 53, '#fcb045', {alpha: 0.55});
      R(26, 23, 60, 60, 18, '#ffffff', {alpha: 0, bw: 5}); R(41, 38, 30, 30, 15, '#ffffff', {alpha: 0, bw: 5}); R(69, 31, 8, 8, 4, '#ffffff');
      T(104, sub ? 40 : 53, title, ns, '#ffffff'); if (sub) T(104, 78, sub, ss, '#ffffff', {font: 'Arial', alpha: 0.9});
    } else if (tpl === 'twitch') {
      const Wd = 112 + txw + 30;
      R(0, 0, Wd, 108, 14, acc);
      R(22, 20, 66, 56, 6, '#ffffff'); G([[34, 76], [52, 76], [34, 92]], '#ffffff');
      R(44, 33, 7, 22, 0, acc); R(63, 33, 7, 22, 0, acc);
      T(108, sub ? 40 : 54, title, ns, '#ffffff'); if (sub) T(108, 78, sub, ss, '#ffffff', {font: 'Arial', alpha: 0.9});
    } else if (tpl === 'news') {
      const w1 = tw + 56, w2 = Math.max(sw + 48, w1 * 0.7);
      R(0, 0, 14, sub ? 128 : 76, 0, '#ffffff');
      R(14, 0, w1, 76, 0, acc); T(42, 38, title, 42, '#ffffff');
      if (sub) { R(14, 76, w2, 52, 0, '#111111', {alpha: 0.9}); T(42, 102, sub, 26, '#ffffff', {font: 'Arial'}); }
    } else if (tpl === 'neon') {
      const Wd = txw + 64;
      R(0, 0, Wd, sub ? 118 : 92, 20, '#0b0b14', {alpha: 0.85, bw: 4, bc: acc});
      T(32, sub ? 42 : 46, title, 40, acc, {alpha: 0.35, bw: 9, bc: acc}); T(32, sub ? 42 : 46, title, 40, '#ffffff', {bw: 2, bc: acc});
      if (sub) T(32, 86, sub, ss, acc, {font: 'Arial'});
    } else {
      T(0, 30, title, 46, '#ffffff', {bw: 3, bc: '#000000'});
      R(0, 64, estW(title, 46), 9, 4, acc);
      if (sub) T(0, 102, sub, 26, '#ffffff', {font: 'Arial', bw: 2, bc: '#000000'});
    }
    return P;
  }
  function primsBox(prims) {
    let x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9;
    for (const p of prims) {
      let a, b, c, d;
      if (p.t === 'rr') { a = p.x; b = p.y; c = p.x + p.w; d = p.y + p.h; }
      else if (p.t === 'poly') { a = Math.min(...p.pts.map(q => q[0])); c = Math.max(...p.pts.map(q => q[0])); b = Math.min(...p.pts.map(q => q[1])); d = Math.max(...p.pts.map(q => q[1])); }
      else { const w = estW(p.text, p.size, p.font), an = p.an; a = an === 5 ? p.x - w / 2 : an === 6 ? p.x - w : p.x; c = a + w; b = p.y - p.size * 0.6; d = p.y + p.size * 0.6; }
      x0 = Math.min(x0, a); y0 = Math.min(y0, b); x1 = Math.max(x1, c); y1 = Math.max(y1, d);
    }
    return x0 > x1 ? [0, 0, 100, 60] : [x0, y0, x1 - x0, y1 - y0];
  }
  function drawPrims(prims, X, Y, s, alpha, ctx = PX, k = K) {
    for (const p of prims) {
      ctx.globalAlpha = alpha * (p.alpha ?? 1);
      if (p.t === 'rr' || p.t === 'poly') {
        const pts = p.t === 'rr' ? (p.r > 0.5 ? shapePoints('round', p.w * s, p.h * s, Math.min(p.r * s, p.w * s / 2, p.h * s / 2) / Math.max(1, Math.min(p.w, p.h) * s)) : shapePoints('rect', p.w * s, p.h * s))
          : p.pts.map(q => [q[0] * s, q[1] * s]);
        const ox = p.t === 'rr' ? X + p.x * s : X, oy = p.t === 'rr' ? Y + p.y * s : Y;
        ctx.beginPath(); pts.forEach(([x, y], i) => i ? ctx.lineTo((ox + x) * k, (oy + y) * k) : ctx.moveTo((ox + x) * k, (oy + y) * k)); ctx.closePath();
        if (p.blur) ctx.filter = `blur(${p.blur * s * k}px)`;
        if ((p.alpha ?? 1) > 0) { ctx.fillStyle = p.color; ctx.fill(); }
        ctx.filter = 'none';
        if (p.bw) { ctx.globalAlpha = alpha; ctx.strokeStyle = p.bc; ctx.lineWidth = p.bw * 2 * s * k; ctx.stroke(); }
      } else {
        const it = p.italic ? 'italic ' : '';
        const fk = fontK(p.font);
        ctx.font = `${it}900 ${p.size * s * k * fk}px "${p.font}", "Arial Black", Arial, sans-serif`;
        if (p.font === 'Arial') ctx.font = `${it}${p.weight || 700} ${p.size * s * k * fk}px Arial, sans-serif`;
        ctx.textBaseline = 'middle'; ctx.textAlign = p.an === 5 ? 'center' : p.an === 6 ? 'right' : 'left';
        const x = (X + p.x * s) * k, y = (Y + p.y * s) * k;
        if (p.bw) { ctx.lineJoin = 'round'; ctx.strokeStyle = p.bc; ctx.lineWidth = p.bw * 2 * s * k; ctx.strokeText(p.text, x, y); }
        ctx.fillStyle = p.color; ctx.fillText(p.text, x, y);
      }
    }
    ctx.globalAlpha = 1;
  }

  /* ================================================================ scene helpers */
  const srcW = () => V.videoWidth || 1920, srcH = () => V.videoHeight || 1080;
  const L = () => ED.scene.layers;
  const capL = () => L().find(l => l.kind === 'captions');
  const hookL = () => L().find(l => l.kind === 'hook');
  const style = () => {
    const c = capL(), st = {...(CFG.styles[c?.style] || CFG.styles.tiktok || {})};
    if (c?.color) st.color = c.color; if (c?.hi) st.hi = c.hi; if (c?.words) st.words = c.words; if (c?.font) st.font = c.font;
    st.anim = c?.anim && c.anim !== 'auto' ? c.anim : (st.anim || '');
    if (st.anim === 'onebyone') st.words = 1;
    if (st.anim === 'full' || st.anim === 'popline') st.mode = 'plain';
    else if (st.anim === 'karaoke') st.mode = 'karaoke';
    else if (st.anim) st.mode = 'highlight';
    return st;
  };
  function fitCrop(l) {
    const [cx, cy, cw, ch] = l.crop, a = l.w / l.h, SW = srcW(), SH = srcH();
    let w = cw * SW, h = w / a;
    if (h > SH) { h = SH; w = h * a; } if (w > SW) { w = SW; h = w / a; }
    const nw = w / SW, nh = h / SH, mx = cx + cw / 2, my = cy + ch / 2;
    l.crop = [clamp(mx - nw / 2, 0, 1 - nw), clamp(my - nh / 2, 0, 1 - nh), nw, nh];
  }
  /* ---------- crop keyframes: move what a video window shows during the clip (follow the action) */
  const smooth = u => u * u * (3 - 2 * u);
  function cropAt(l, t) {                       // crop rectangle at raw-video time t
    const K = l.keys, c = l.crop;
    if (!K || !K.length) return c;
    const kv = k => [k[1], k[2], k.length === 5 ? k[3] : c[2], k.length === 5 ? k[4] : c[3]];   // position and size
    let v;
    if (t <= K[0][0]) v = kv(K[0]);
    else if (t >= K[K.length - 1][0]) v = kv(K[K.length - 1]);
    else {
      let i = 0; while (i < K.length - 2 && t >= K[i + 1][0]) i++;
      const a = kv(K[i]), b = kv(K[i + 1]);
      if (l.kmode === 'cut') v = a;
      else { const u = smooth((t - K[i][0]) / Math.max(0.01, K[i + 1][0] - K[i][0])); v = a.map((q, j) => q + (b[j] - q) * u); }
    }
    const [w, h] = [clamp(v[2], 0.02, 1), clamp(v[3], 0.02, 1)];
    return [clamp(v[0], 0, 1 - w), clamp(v[1], 0, 1 - h), w, h];
  }
  function setKey(l, t) {                       // remember the crop's current position at time t
    l.keys = l.keys || [];
    const k = [+t.toFixed(2), ...l.crop.map(v => +v.toFixed(4))], i = l.keys.findIndex(q => Math.abs(q[0] - t) < 0.15);
    if (i >= 0) l.keys[i] = k; else { l.keys.push(k); l.keys.sort((a, b) => a[0] - b[0]); }
  }
  function renderKf() {
    $$('#edTimeline .tlkf').forEach(e => e.remove());
    const l = selL(); if (!l || l.kind !== 'video' || !l.keys || !ED.dur) return;
    l.keys.forEach(k => { const d = document.createElement('div'); d.className = 'tlkf'; d.title = `Crop keyframe ${k[0].toFixed(1)} s`;
      d.style.left = (100 * k[0] / ED.dur) + '%'; d.onpointerdown = e => { e.stopPropagation(); V.currentTime = k[0]; }; $('#edTimeline').appendChild(d); });
  }
  const videoLayer = (role, x, y, w, h, shape = 'rect', extra = {}) => {
    const t = {...CFG.defaults, ...(ED.clip.template || {})};
    return {kind: 'video', id: uid(), name: role === 'cam' ? 'Facecam' : 'Gameplay', role, crop: [...(t[role] || [0, 0, 1, 1])],
            x, y, w, h, shape, radius: 0.12, border: 0, bcolor: '#ffffff', ...extra};
  };
  function sceneFromLegacy(c) {
    const t = {...CFG.defaults, ...(c.template || {})}, ls = [];
    const split = t.layout === 'split', camH = even(1920 * clamp(+t.split || 0.35, 0.15, 0.7));
    if (split) {
      const camTop = t.cam_pos === 'top';
      ls.push({...videoLayer('game', 0, camTop ? camH : 0, 1080, 1920 - camH)});
      ls.push({...videoLayer('cam', 0, camTop ? 0 : 1920 - camH, 1080, camH)});
    } else ls.push(videoLayer('game', 0, 0, 1080, 1920));
    (c.overlays || []).forEach(o => ls.push({kind: 'text', id: uid(), name: 'Text', text: o.text, x: (+o.x || 0.5) * 1080, y: (+o.y || 0.3) * 1920,
      size: +o.size || 70, color: o.color || '#ffffff', box: !!o.box, bcolor: '#000000', opacity: 1, font: 'Arial Black', start: +o.start || 0, end: +o.end || 0}));
    const lg = c.logo || {};
    if (lg.enabled && lg.file) ls.push({kind: 'image', id: uid(), name: 'Logo', file: lg.file, x: (+lg.x || 0.85) * 1080, y: (+lg.y || 0.06) * 1920, w: (+lg.w || 0.2) * 1080, opacity: +lg.opacity || 1, start: 0, end: 0});
    if (t.watermark) ls.push({kind: 'text', id: uid(), name: 'Watermark', text: t.watermark, x: 540, y: t.watermark_pos === 'top' ? 70 : 1920 - (t.progress_bar ? 70 : 50),
      size: 40, color: '#ffffff', box: false, bcolor: '#000000', opacity: 0.55, font: 'Arial', start: 0, end: 0});
    const hy = split ? (t.cam_pos === 'top' ? camH + 60 : 80) : 230;
    ls.push({kind: 'captions', id: 'cap', name: 'Captions', x: 540, y: (+t.caption_y || 0.72) * 1920, scale: 1, style: t.caption_style || 'tiktok', color: '', hi: '', words: 0});
    ls.push({kind: 'hook', id: 'hook', name: 'Hook', x: 540, y: hy + 48, scale: 1, secs: +t.hook_secs || 3.5, hidden: t.hook === false});
    return {v: 1, bg: {type: 'blur', color: '#111318', color2: '#3a1c71', image: '', dim: 0.25, blur: 14}, progress: {on: !!t.progress_bar, color: '#ffd93d', pos: 'bottom', h: 12}, layers: ls};
  }
  const loadImg = f => { if (f && !ED.imgs[f]) { const i = new Image(); i.src = '/assets/' + encodeURIComponent(f); ED.imgs[f] = i; } return ED.imgs[f]; };
  const imgCrop = l => l.crop || [0, 0, 1, 1];
  const imgH = l => { const i = loadImg(l.file), c = imgCrop(l); return i && i.naturalWidth ? l.w * (c[3] * i.naturalHeight) / (c[2] * i.naturalWidth) : l.w; };
  function drawImageLayer(l, i) {
    const h = imgH(l), c = imgCrop(l), x0 = l.x - l.w / 2, y0 = l.y - h / 2;
    PX.save(); PX.globalAlpha = l.opacity;
    if (l.shape && l.shape !== 'rect') { pathOf(shapePoints(l.shape, l.w, h, l.radius ?? 0.12), x0, y0); PX.clip(); }
    PX.drawImage(i, c[0] * i.naturalWidth, c[1] * i.naturalHeight, c[2] * i.naturalWidth, c[3] * i.naturalHeight, x0 * K, y0 * K, l.w * K, h * K);
    PX.restore();
  }
  const tNow = () => V.currentTime - ED.start;
  const visible = (l, t) => {
    if (l.hidden) return false;
    if (l.kind === 'text' || l.kind === 'image') return t >= (l.start || 0) - 0.01 && (!l.end || t <= l.end);
    if (l.kind === 'third' && l.from_end) return t >= (ED.end - ED.start) - l.from_end - 0.01;
    if (l.kind === 'third') return t >= l.start - 0.01 && (!l.dur || t <= l.start + l.dur);
    if (l.kind === 'hook') return !!$('#edHook').value.trim() && t < l.secs;
    return true;
  };
  function wrapText(text, font, maxW) {     // -> lines (in screen pixels of the 1080-wide frame)
    PX.font = font; const out = [], m = t => PX.measureText(t).width / K;
    String(text || '').split('\n').forEach(para => {
      const ws = para.split(/\s+/).filter(Boolean); if (!ws.length) { out.push(''); return; }
      const total = m(ws.join(' '));
      if (total <= maxW) { out.push(ws.join(' ')); return; }
      const greedy = lim => { const ls = []; let cur = ''; ws.forEach(w => { const t = cur ? cur + ' ' + w : w; if (cur && m(t) > lim) { ls.push(cur); cur = w; } else cur = t; }); if (cur) ls.push(cur); return ls; };
      const n = greedy(maxW).length; let ls = greedy(Math.min(maxW, total / n * 1.12));   // even lines, like the video
      if (ls.length > n) ls = greedy(maxW);
      out.push(...ls);
    });
    return out;
  }
  const HOOK_MAXW = l => 1080 - 2 * (40 + 18 * l.scale);
  const TEXT_MAXW = l => 1080 - 2 * (20 + (l.box ? l.size * 0.25 : 0)) - 20;
  function textW(l) { PX.font = cssFont(l.size, l.font); return Math.max(...wrapText(l.text, cssFont(l.size, l.font), TEXT_MAXW(l)).map(t => PX.measureText(t).width / K), 10); }
  function bounds(l) {
    if (l.kind === 'video') return [l.x, l.y, l.w, l.h];
    if (l.kind === 'image') { const h = imgH(l); return [l.x - l.w / 2, l.y - h / 2, l.w, h]; }
    if (l.kind === 'text') { const n = wrapText(l.text, cssFont(l.size, l.font), TEXT_MAXW(l)).length, w = textW(l) + (l.box ? l.size * 0.5 : 0), h = l.size * 1.2 * n + l.size * 0.1; return [l.x - w / 2, l.y - h / 2, w, h]; }
    if (l.kind === 'captions') { const st = style(), sz = (st.size || 90) * l.scale; PX.font = cssFont(sz, st.font); const w = Math.min(1020, Math.max(PX.measureText(curCapText() || 'CAPTIONS HERE').width / K, 200)); return [l.x - w / 2, l.y - sz * 0.75, w, sz * 1.5]; }
    if (l.kind === 'hook') { const sz = 62 * l.scale, ls = wrapText($('#edHook').value.trim() || 'HOOK', cssFont(sz), HOOK_MAXW(l)); PX.font = cssFont(sz);
      const w = Math.max(...ls.map(t => PX.measureText(t).width / K)) + 36 * l.scale, h = sz * 1.2 * ls.length + 36 * l.scale; return [l.x - w / 2, l.y - h / 2, w, h]; }
    if (l.kind === 'third') { const b = primsBox(l.prims); return [l.x + b[0] * l.scale, l.y + b[1] * l.scale, b[2] * l.scale, b[3] * l.scale]; }
    return [0, 0, 0, 0];
  }

  /* ================================================================ open / close */
  window.openEditor = async name => {
    $('#editor .edbox').classList.remove('trimmode', 'capmode', 'textmode', 'imgmode', 'thmode', 'objmode'); if (typeof leaveLay === 'function') { leaveLay(); leaveFx(); }
    const j = await api(`/api/clips/${encodeURIComponent(name)}/full`); if (!j.ok) return;
    const c = j.clip;
    if (!c.has_source) return toast('The source of this clip was deleted, so it cannot be edited', true);
    ED.clip = c; ED.end = null;
    ED.scene = c.scene ? clone(c.scene) : sceneFromLegacy(c);
    ED.scene.layers.forEach(l => { l.id = l.id || uid(); if (l.kind === 'image') loadImg(l.file); });
    if (!capL()) ED.scene.layers.push({kind: 'captions', id: 'cap', name: 'Captions', x: 540, y: 1380, scale: 1, style: 'tiktok', color: '', hi: '', words: 0});
    if (!hookL()) ED.scene.layers.push({kind: 'hook', id: 'hook', name: 'Hook', x: 540, y: 300, scale: 1, secs: 3.5});
    ED.words = (c.words || []).map(w => [+w[0], +w[1], String(w[2])]);
    ED.sel = null; ED.hist = []; ED.hi = -1;
    $('#edName').textContent = c.hook || c.name;
    $('#edHook').value = c.hook || '';
    useFonts(CFG.fonts);
    $('#edStyle').innerHTML = Object.entries(CFG.styles).map(([k, s]) => `<option value="${k}">${esc(s.label)}</option>`).join('');
    $('#edStyleOn').checked = !!CFG.settings.style_on;
    V.src = srcUrl(c.name, c.rendered || '');
    V.onloadedmetadata = () => {
      ED.dur = V.duration || c.duration || 30; ED.fitDone = false;
      ED.start = Math.min(+(c.trim_start || 0), ED.dur - 1);
      ED.end = Math.min(+(c.trim_end || ED.dur), ED.dur);
      if (ED.end - ED.start < 3) { ED.start = 0; ED.end = ED.dur; }
      ED.cuts = (c.cuts || []).map(x => [+x[0], +x[1]]); ED.cutA = null;
      $('#edEnhQ').value = (CFG.settings && CFG.settings.enhance_quality) || 'HIGH';
      $('#edEnhS').value = (CFG.settings && CFG.settings.enhance_sharpen) || 'medium'; enhPoll(true);
      $('#edSpeech').value = c.speech || '';
      V.currentTime = Math.min(ED.start + 0.5, ED.end);
      L().filter(l => l.kind === 'video').forEach(fitCrop);
      syncTrim(); drawFxMarks(); drawBleep(); ED.hist = []; ED.hi = -1; commit();
    };
    ED.scene.fx = ED.scene.fx || []; ED.scene.sfx = ED.scene.sfx || [];
    ED.scene.bleep = ED.scene.bleep || {mode: 'beep', camblur: false, spans: []};
    ED.scene.music = ED.scene.music || {file: '', vol: 0.15, peak: 0.45, rise: 4, duck: true, offset: 0};
    ED.scene.loop = !!ED.scene.loop;
    drawBleep(); drawFixes(); loadMusicList();
    syncAll(); renderCaptionsList(); initTools(); drawPresets(); drawFrames(); drawMyLayouts(); loadServerImgs(); drawThirdTpls(); loadAccountNames(); drawFxUI();
    $('#editor').classList.add('on');
    requestAnimationFrame(() => { viewFit(); });
    cancelAnimationFrame(ED.raf); ED.raf = requestAnimationFrame(loop);
    commit();
  };
  $('#edFull').onclick = () => { try { document.fullscreenElement ? document.exitFullscreen() : $('#editor').requestFullscreen(); } catch (e) {} };
  const closeEditor = () => { try { if (document.fullscreenElement) document.exitFullscreen(); } catch (e) {}  V.pause(); V.removeAttribute('src'); V.load(); cancelAnimationFrame(ED.raf); $('#editor').classList.remove('on'); };
  $('#edClose').onclick = () => { if (ED.hi > 0 && !confirm('Close without saving your changes?')) return; closeEditor(); };

  /* ================================================================ undo / redo */
  const snapshot = () => JSON.stringify({scene: ED.scene, words: ED.words, start: ED.start, end: ED.end, cuts: ED.cuts || [], hook: $('#edHook').value});
  function commit() {
    if (!ED.scene) return;
    if (window.__drawTracks) window.__drawTracks();
    const s = snapshot(); if (ED.hist[ED.hi] === s) return;
    ED.hist = ED.hist.slice(0, ED.hi + 1); ED.hist.push(s); if (ED.hist.length > 80) ED.hist.shift(); ED.hi = ED.hist.length - 1; syncUndo();
  }
  function restore(i) {
    const o = JSON.parse(ED.hist[i]); ED.hi = i;
    ED.scene = o.scene; ED.words = o.words; ED.start = o.start; ED.end = o.end; ED.cuts = o.cuts || []; $('#edHook').value = o.hook;
    if (ED.sel && !L().some(l => l.id === ED.sel)) ED.sel = null;
    syncAll(); syncTrim(); renderCaptionsList(); syncUndo();
  }
  function syncUndo() { $('#edUndo').disabled = ED.hi <= 0; $('#edRedo').disabled = ED.hi >= ED.hist.length - 1; }
  $('#edUndo').onclick = () => { if (ED.hi > 0) restore(ED.hi - 1); };
  $('#edRedo').onclick = () => { if (ED.hi < ED.hist.length - 1) restore(ED.hi + 1); };
  $('#editor').addEventListener('change', () => setTimeout(commit, 0));
  const act = fn => (...a) => { fn(...a); syncAll(); commit(); };

  /* ================================================================ tabs */
  function edTab(k) {
    if (k === 'audio') k = 'fx';   // audio lives in the Audio & FX tool now
    if (ED.tool !== undefined && window.__openTool && !edTab.inner) {
      const PANEL = {edit: 'trim', captions: 'caps', text: 'text', image: 'img', layout: 'canvas', fx: 'fx', thirds: 'thirds'};
      const tool = Object.keys(PANEL).find(t => PANEL[t] === k);
      if (tool && ED.tool !== tool) { edTab.inner = true; try { window.__openTool(tool); } finally { edTab.inner = false; } return; }
    }
    $$('#edTabs button').forEach(b => b.classList.toggle('on', b.dataset.t === k));
    $$('#editor .edp').forEach(p => p.classList.toggle('on', p.dataset.t === k));
    if (k === 'layers') renderLayers();
    if (k === 'cover' && window.coverTab) window.coverTab(ED.clip.name, $('#edHook').value);
  }
  $$('#edTabs button').forEach(b => b.onclick = () => edTab(b.dataset.t));
  /* ---- NVIDIA AI enhance: button + progress bar in the editor */
  const REMOTE = !/^(127\.0\.0\.1|localhost|\[::1\])$/.test(location.hostname);
  function srcUrl(name, v) { return `/${REMOTE ? 'rawlite' : 'raw'}/${encodeURIComponent(name)}.mp4?${v || ''}`; }
  const QN = {MEDIUM: 'Medium', HIGH: 'High', ULTRA: 'Ultra'};
  let enhTimer = 0, enhLastState = '';
  async function enhPoll(first) {
    clearTimeout(enhTimer);
    if (!ED.clip || !$('#editor').classList.contains('on')) return;
    const name = ED.clip.name, r = await api(`/api/clips/${encodeURIComponent(name)}/enhance`);
    if (!r.ok || !ED.clip || ED.clip.name !== name) return;
    const j = r.job || {}, bar = $('#edEnhBar'), st = j.state || '', active = ['queued', 'running', 'finishing'].includes(st);
    bar.className = 'enhbar'; $('#edEnhBox').classList.toggle('done', !!r.enhanced && !active);
    $('#edEnhGo').disabled = active;
    $('#edEnhGo').textContent = active ? '⏳ Enhancing…' : r.enhanced ? (r.enhanced.quality === $('#edEnhQ').value && (r.enhanced.sharpen || 'off') === $('#edEnhS').value ? `Enhanced` : `Re-enhance`) : 'Enhance';
    $('#edEnhBox').style.opacity = r.ready || r.enhanced ? 1 : 0.5;
    const acts = [];
    if (active) {
      bar.classList.add('on');
      $('.enhfill', bar).style.width = (j.pct || 0) + '%';
      const left = j.eta ? (j.eta > 90 ? ` · about ${Math.round(j.eta / 60)} min left` : ` · about ${j.eta} s left`) : '';
      $('.enhtxt', bar).textContent = r.ahead ? `⏳ Waiting for the graphics card - ${r.ahead} clip ahead`
        : st === 'finishing' ? (j.step === 'sharpening' ? 'Sharpening the 4K video…' : 'Finishing - swapping in the enhanced video…') : `AI enhancing (${QN[j.quality] || j.quality})… ${j.pct || 0}%${left}`;
      if (st === 'finishing' && !V.paused) V.pause();
      if (st === 'finishing' && V.getAttribute('src')) { ED.keepT = V.currentTime; V.removeAttribute('src'); V.load(); }   // let the PC swap the file
    } else if (st === 'error') {
      bar.classList.add('on', 'err'); $('.enhtxt', bar).textContent = '⚠ AI enhance failed: ' + (j.error || '');
    } else if (r.enhanced) {
      bar.classList.add('on', 'ok');
      $('.enhtxt', bar).textContent = `AI enhanced (${QN[r.enhanced.quality] || r.enhanced.quality}${r.enhanced.sharpen && r.enhanced.sharpen !== 'off' ? ', sharpen ' + r.enhanced.sharpen : ''})` + (enhLastState && enhLastState !== 'done' ? ' - preview updated. Press Save & render to use it.' : '');
      acts.push(['undo', '↺ Undo']);
      if (r.has_original) acts.push(['delorig', 'Delete original']);
    }
    $('.enhact', bar).innerHTML = acts.map(([k, t]) => `<button class="btn sm" data-ea="${k}">${t}</button>`).join('');
    $$('.enhact [data-ea]', bar).forEach(b => b.onclick = () => enhAct(b.dataset.ea));
    if ((st === 'done' && enhLastState && enhLastState !== 'done') || (!active && !V.getAttribute('src'))) {   // load the enhanced video
      const t = ED.keepT ?? V.currentTime; V.src = srcUrl(name, r.mtime); V.addEventListener('loadedmetadata', () => { V.currentTime = t; }, {once: true}); ED.keepT = undefined;
    }
    enhLastState = st || (r.enhanced ? 'done' : '');
    if (first && !active) enhLastState = r.enhanced ? 'done' : '';
    if (active) enhTimer = setTimeout(() => enhPoll(), 1500);
  }
  async function enhAct(k) {
    const name = ED.clip.name;
    if (k === 'undo' && !confirm('Go back to the original (not enhanced) video for this clip?')) return;
    if (k === 'delorig' && !confirm('Delete the original video to save space? The clip stays enhanced, but you can no longer undo or switch quality.')) return;
    if (k === 'undo') { ED.keepT = V.currentTime; V.removeAttribute('src'); V.load(); }
    const r = await api(`/api/clips/${encodeURIComponent(name)}/enhance`, k === 'undo' ? {undo: true} : {delete_original: true});
    if (r.ok) { toast(k === 'undo' ? 'Back to the original - press Save & render to use it' : 'Original deleted - space freed');
      if (k === 'undo') { V.src = srcUrl(name, r.mtime); V.addEventListener('loadedmetadata', () => { V.currentTime = ED.keepT || 0; ED.keepT = undefined; }, {once: true}); } }
    enhLastState = ''; enhPoll(true);
  }
  $('#edEnhGo').onclick = async () => {
    if (!(window.ENH && window.ENH.ready)) return toast('Install AI Enhance first: Settings. Files, AI Enhance (NVIDIA RTX)', true);
    const r = await api(`/api/clips/${encodeURIComponent(ED.clip.name)}/enhance`, {quality: $('#edEnhQ').value, sharpen: $('#edEnhS').value});
    if (r.ok) { enhLastState = 'queued'; toast('AI enhance started, you can keep editing'); enhPoll(); }
  };
  $('#edEnhQ').onchange = () => enhPoll(true);
  $('#edEnhS').onchange = () => enhPoll(true);
  $('#edHook').addEventListener('input', () => {        // the Cover tab follows the hook as you type
    clearTimeout(ED.hookT); ED.hookT = setTimeout(() => { if ($('#editor .edp[data-t="cover"]').classList.contains('on') && window.coverTab) window.coverTab(ED.clip.name, $('#edHook').value); }, 700);
  });

  /* ================================================================ playback + timeline */
  $('#edPlay').onclick = () => {
    if (V.paused) { if (V.currentTime < ED.start || V.currentTime >= ED.end - 0.05) V.currentTime = ED.start; V.play(); }
    else V.pause();
  };
  const PLAY_I = '<svg class="i" aria-hidden="true"><use href="#i-play"/></svg>', PAUSE_I = '<svg class="i" aria-hidden="true" style="margin:0"><path d="M7 4h3v16H7zM14 4h3v16h-3z"/></svg>';
  V.onplay = () => { $('#edPlay').innerHTML = PAUSE_I; }; V.onpause = () => { $('#edPlay').innerHTML = PLAY_I; };
  const TL = $('#edTimeline');
  const tlTime = e => { const r = TL.getBoundingClientRect(); return clamp((e.clientX - r.left) / r.width * ED.dur, 0, ED.dur); };
  let tlDrag = null;
  TL.addEventListener('pointerdown', e => {
    const t = tlTime(e), r = TL.getBoundingClientRect(), px = ED.dur ? r.width / ED.dur : 1;
    tlDrag = Math.abs(t - ED.start) * px < 14 ? 'start' : Math.abs(t - ED.end) * px < 14 ? 'end' : 'head';
    TL.setPointerCapture(e.pointerId); tlMove(e);
  });
  TL.addEventListener('pointermove', e => { if (tlDrag) tlMove(e); });
  TL.addEventListener('pointerup', () => { if (tlDrag && tlDrag !== 'head') commit(); tlDrag = null; });
  function tlMove(e) {
    const t = tlTime(e);
    if (tlDrag === 'start') { ED.start = Math.min(t, ED.end - 3); V.currentTime = ED.start; }
    else if (tlDrag === 'end') { ED.end = Math.max(t, ED.start + 3); V.currentTime = Math.max(ED.start, ED.end - 1); }
    else V.currentTime = t;
    syncTrim();
  }
  function syncTrim() {
    if (window.__drawTracks) setTimeout(window.__drawTracks, 0);
    if (!ED.fitDone && ED.dur) { ED.fitDone = true; setTimeout(() => window.__tlFit && window.__tlFit(), 30); }   // open with the whole video fitted
    const p = v => (100 * v / (ED.dur || 1)) + '%';
    $('#edSelBox').style.left = p(ED.start); $('#edSelBox').style.width = p(ED.end - ED.start);
    $('#edTs').value = ED.start.toFixed(1); $('#edTe').value = ED.end.toFixed(1);
    const cutLen = (ED.cuts || []).reduce((a, c) => a + Math.max(0, Math.min(c[1], ED.end) - Math.max(c[0], ED.start)), 0);
    $('#edLen').textContent = `${(ED.end - ED.start - cutLen).toFixed(1)} s` + (cutLen ? ` (${cutLen.toFixed(1)} s cut out)` : '') + ` of ${ED.dur.toFixed(1)} s`;
    $$('#edTimeline .tlcut').forEach(e => e.remove());
    (ED.cuts || []).forEach(([a, b]) => { const d = document.createElement('div'); d.className = 'tlcut'; d.style.left = p(a); d.style.width = p(b - a); d.title = 'cut out'; TL.appendChild(d); });
    if (ED.cutA !== null && ED.cutA !== undefined) { const d = document.createElement('div'); d.className = 'tlcut pend'; d.style.left = p(ED.cutA); d.style.width = '2px'; TL.appendChild(d); }
    renderCuts();
    drawFxMarks();
  }
  /* ---- cut out parts in the middle */
  function renderCuts() {
    const box = $('#edCutList'); if (!box) return;
    box.innerHTML = (ED.cuts || []).map((c, i) => `<div class="cutrow"><span><svg class="i" aria-hidden="true"><use href="#i-scissors"/></svg>${(c[0] - ED.start).toFixed(1)} s → ${(c[1] - ED.start).toFixed(1)} s</span>
      <b class="mut">${(c[1] - c[0]).toFixed(1)} s removed</b><button class="btn sm" data-cp="${i}" title="Play across this cut"><svg class="i" aria-hidden="true"><use href="#i-play"/></svg></button><button class="btn sm" data-cd="${i}"><svg class="i" aria-hidden="true"><use href="#i-trash"/></svg></button></div>`).join('')
      || '<div class="mut">No cuts yet.</div>';
    $('#edCutA').textContent = ED.cutA !== null && ED.cutA !== undefined ? `Cut from ${(ED.cutA - ED.start).toFixed(1)} s … press "to here" at the end of the part` : '';
    $$('#edCutList [data-cd]').forEach(b => b.onclick = () => { ED.cuts.splice(+b.dataset.cd, 1); syncTrim(); commit(); });
    $$('#edCutList [data-cp]').forEach(b => b.onclick = () => { const c = ED.cuts[+b.dataset.cp]; V.currentTime = Math.max(ED.start, c[0] - 2); V.play(); });
  }
  function addCut(a, b) {
    a = clamp(Math.min(a, b), ED.start, ED.end); b = clamp(Math.max(a, b), ED.start, ED.end);
    if (b - a < 0.1) return toast('Move the playhead to the end of the part you want to remove', true);
    const cuts = [...(ED.cuts || []), [+a.toFixed(2), +b.toFixed(2)]].sort((x, y) => x[0] - y[0]), m = [];
    cuts.forEach(c => { if (m.length && c[0] <= m[m.length - 1][1] + 0.05) m[m.length - 1][1] = Math.max(m[m.length - 1][1], c[1]); else m.push([...c]); });
    const kept = ED.end - ED.start - m.reduce((x, c) => x + c[1] - c[0], 0);
    if (kept < 3) return toast('The clip must stay at least 3 seconds long', true);
    ED.cuts = m; ED.cutA = null; syncTrim(); commit(); toast(`Cut out ${(b - a).toFixed(1)} s`);
  }
  $('#edCutFrom').onclick = () => { ED.cutA = V.currentTime; V.pause(); syncTrim(); };
  $('#edCutTo').onclick = () => { if (ED.cutA === null || ED.cutA === undefined) return toast('First press "Cut from here" at the start of the part', true); addCut(ED.cutA, V.currentTime); };
  $('#edCutSel').onclick = () => { const s = +$('#edCutS').value + ED.start, e = +$('#edCutE').value + ED.start; addCut(s, e); };
  $('#edCutClear').onclick = () => { ED.cuts = []; ED.cutA = null; syncTrim(); commit(); };
  $('#edTs').onchange = e => { ED.start = clamp(+e.target.value, 0, ED.end - 3); V.currentTime = ED.start; syncTrim(); };
  $('#edTe').onchange = e => { ED.end = clamp(+e.target.value, ED.start + 3, ED.dur); syncTrim(); };
  $('#edSetIn').onclick = () => { ED.start = Math.min(V.currentTime, ED.end - 3); syncTrim(); commit(); };
  $('#edSetOut').onclick = () => { ED.end = Math.max(V.currentTime, ED.start + 3); syncTrim(); commit(); };
  $('#edBack').onclick = () => { V.currentTime = Math.max(0, V.currentTime - 1); };
  $('#edFwd').onclick = () => { V.currentTime = Math.min(ED.dur, V.currentTime + 1); };

  /* ================================================================ view zoom / pan */
  function applyView() { const v = ED.view; STAGE.style.transform = `translate(${v.tx}px,${v.ty}px) scale(${v.z})`; }
  function viewFit() { ED.view = {z: 1, tx: 0, ty: 0}; applyView(); }
  function zoomAt(z, px, py) {
    const v = ED.view, nz = clamp(z, 1, 6);
    v.tx = px - (px - v.tx) * nz / v.z; v.ty = py - (py - v.ty) * nz / v.z; v.z = nz;
    const r = WRAP.getBoundingClientRect();
    v.tx = clamp(v.tx, r.width - r.width * v.z, 0); v.ty = clamp(v.ty, r.height - r.height * v.z, 0);
    applyView();
  }
  const wrapCenter = () => { const r = WRAP.getBoundingClientRect(); return [r.width / 2, r.height / 2]; };
  $('#edSafe').onclick = () => { ED.safe = !ED.safe; $('#edSafe').classList.toggle('on', ED.safe); };

  /* ================================================================ stage interaction: select, drag, resize, pinch */
  const toScene = e => { const r = PH.getBoundingClientRect(); return [(e.clientX - r.left) / r.width * W, (e.clientY - r.top) / r.height * H]; };
  const selL = () => L().find(l => l.id === ED.sel) || null;
  const order = () => [...L().filter(l => l.kind === 'video' || l.kind === 'image'), ...L().filter(l => !(l.kind === 'video' || l.kind === 'image'))];
  function hitTest(x, y) {
    const t = tNow(), list = order().reverse();
    const sel = selL();
    if (sel) { const [bx, by, bw, bh] = bounds(sel); if (x >= bx && x <= bx + bw && y >= by && y <= by + bh) return sel; }
    for (const l of list) {
      if (!visible(l, t) && l.id !== ED.sel) continue;
      if (l.kind === 'captions' && !curCapText() && l.id !== ED.sel && !$('#editor .edp[data-t="caps"]').classList.contains('on')) continue;
      const [bx, by, bw, bh] = bounds(l);
      if (x >= bx && x <= bx + bw && y >= by && y <= by + bh) return l;
    }
    return null;
  }
  const handleSize = () => 46 / ED.view.z;
  function onHandle(l, x, y) { const [bx, by, bw, bh] = bounds(l), hs = handleSize(); return x >= bx + bw - hs && x <= bx + bw + hs * 0.6 && y >= by + bh - hs && y <= by + bh + hs * 0.6; }
  const startVals = l => clone(l);
  function scaleLayer(l, s0, f, anchor) {
    f = clamp(f, 0.15, 8);
    if (l.kind === 'video') {
      const w = clamp(s0.w * f, 80, 3000), h = clamp(s0.h * f, 80, 4000);
      if (anchor === 'center') { l.x = s0.x + s0.w / 2 - w / 2; l.y = s0.y + s0.h / 2 - h / 2; }
      l.w = w; l.h = h; fitCrop(l);
    } else if (l.kind === 'image') l.w = clamp(s0.w * f, 20, 3000);
    else if (l.kind === 'text') l.size = clamp(s0.size * f, 14, 300);
    else l.scale = clamp(s0.scale * f, l.kind === 'third' ? 0.2 : 0.3, l.kind === 'third' ? 4 : 3);
  }
  function moveLayer(l, s0, dx, dy) {
    let nx = s0.x + dx, ny = s0.y + dy;
    l.x = nx; l.y = ny;
    // snapping to centre lines and edges
    const [bx, by, bw, bh] = bounds(l), th = 14 / ED.view.z; ED.guides = [];
    const sx = [[540, bx + bw / 2], [0, bx], [1080, bx + bw]], sy = [[960, by + bh / 2], [0, by], [1920, by + bh]];
    for (const [g, v] of sx) if (Math.abs(g - v) < th) { l.x += g - v; ED.guides.push(['x', g]); break; }
    for (const [g, v] of sy) if (Math.abs(g - v) < th) { l.y += g - v; ED.guides.push(['y', g]); break; }
  }
  PH.addEventListener('pointerdown', e => {
    try { PH.setPointerCapture(e.pointerId); } catch (err) { /* synthetic or already released */ }
    ED.ptrs.set(e.pointerId, {x: e.clientX, y: e.clientY});
    const [x, y] = toScene(e);
    if (ED.ptrs.size === 2) {           // second finger: pinch the selected thing, or zoom the view
      const [a, b] = [...ED.ptrs.values()], sel = selL();
      const d0 = Math.hypot(a.x - b.x, a.y - b.y) || 1, wr = WRAP.getBoundingClientRect();
      if (ED.g && ED.g.mode === 'move' && ED.g.layer) Object.assign(ED.g.layer, {x: ED.g.s0.x, y: ED.g.s0.y});
      ED.g = sel && ED.g && ED.g.layer === sel && ED.g.wasSel ? {mode: 'pinchL', layer: sel, s0: startVals(sel), d0}
        : {mode: 'pinchV', d0, z0: ED.view.z, mx: (a.x + b.x) / 2 - wr.left, my: (a.y + b.y) / 2 - wr.top};
      return;
    }
    const sel = selL();
    if (sel && onHandle(sel, x, y)) { ED.g = {mode: 'resize', layer: sel, s0: startVals(sel), x, y, b0: bounds(sel)}; return; }
    const hit = hitTest(x, y);
    if (hit) {
      const wasSel = ED.sel === hit.id;
      if (!wasSel) { ED.sel = hit.id; ED.tapSel = true; syncProps(); renderLayers(); }
      ED.g = {mode: 'move', layer: hit, s0: startVals(hit), x, y, wasSel};
    } else {
      if (ED.sel) { ED.sel = null; syncProps(); renderLayers(); }
      ED.g = ED.view.z > 1 ? {mode: 'pan', cx: e.clientX, cy: e.clientY, tx: ED.view.tx, ty: ED.view.ty} : null;
    }
  });
  PH.addEventListener('pointermove', e => {
    if (!ED.ptrs.has(e.pointerId)) { const [x, y] = toScene(e), s = selL(); PH.style.cursor = s && onHandle(s, x, y) ? 'nwse-resize' : hitTest(x, y) ? 'move' : 'default'; return; }
    ED.ptrs.set(e.pointerId, {x: e.clientX, y: e.clientY});
    const g = ED.g; if (!g) return;
    if (g.mode === 'pinchL' || g.mode === 'pinchV') {
      const [a, b] = [...ED.ptrs.values()]; if (!b) return;
      const f = Math.hypot(a.x - b.x, a.y - b.y) / g.d0;
      if (g.mode === 'pinchL') { scaleLayer(g.layer, g.s0, f, 'center'); syncPropsLive(); }
      else zoomAt(g.z0 * f, g.mx, g.my);
      return;
    }
    if (g.mode === 'pan') { const v = ED.view, r = WRAP.getBoundingClientRect(); v.tx = clamp(g.tx + e.clientX - g.cx, r.width - r.width * v.z, 0); v.ty = clamp(g.ty + e.clientY - g.cy, r.height - r.height * v.z, 0); applyView(); return; }
    const [x, y] = toScene(e);
    if (g.mode === 'move') { moveLayer(g.layer, g.s0, x - g.x, y - g.y); syncPropsLive(); }
    else if (g.mode === 'resize') {
      const l = g.layer, [bx, by, bw, bh] = g.b0;
      if (l.kind === 'video' && !e.shiftKey) { l.w = clamp(g.s0.w + x - g.x, 80, 3000); l.h = clamp(g.s0.h + y - g.y, 80, 4000); fitCrop(l); }
      else {
        const f = Math.max((bw + x - g.x) / bw, (bh + y - g.y) / bh);
        l.x = g.s0.x; l.y = g.s0.y; scaleLayer(l, g.s0, f, null);
        if (l.kind !== 'third' && l.kind !== 'video') { const nb = bounds(l); l.x += bx - nb[0]; l.y += by - nb[1]; }   // keep the top-left corner still
      }
      syncPropsLive();
    }
  });
  const endPtr = e => {
    ED.ptrs.delete(e.pointerId);
    if (ED.ptrs.size === 0) { if (ED.g && ED.g.layer) commit(); ED.g = null; ED.guides = []; }
    else if (ED.g && ED.g.mode.startsWith('pinch')) ED.g = ED.g.layer ? {mode: 'none', layer: ED.g.layer} : null;
  };
  PH.addEventListener('pointerup', endPtr); PH.addEventListener('pointercancel', endPtr);
  PH.addEventListener('dblclick', e => { const [x, y] = toScene(e), h = hitTest(x, y); if (h && (h.kind === 'text' || h.kind === 'third' || h.kind === 'hook')) { ED.sel = h.id; syncProps(); const i = $('#edProps [data-focus]'); if (i) { i.focus(); i.select && i.select(); } } });
  WRAP.addEventListener('wheel', e => {
    e.preventDefault();
    const sel = selL(), [x, y] = toScene(e);
    if (!e.ctrlKey && sel && hitTest(x, y) === sel) { const s0 = startVals(sel); scaleLayer(sel, s0, e.deltaY < 0 ? 1.06 : 0.94, 'center'); syncPropsLive(); clearTimeout(ED.wt); ED.wt = setTimeout(commit, 400); return; }
    const r = WRAP.getBoundingClientRect(); zoomAt(ED.view.z * (e.deltaY < 0 ? 1.12 : 0.89), e.clientX - r.left, e.clientY - r.top);
  }, {passive: false});
  document.addEventListener('keydown', e => {
    if (!$('#editor').classList.contains('on') || /INPUT|TEXTAREA|SELECT/.test(document.activeElement?.tagName || '')) return;
    const l = selL(), k = e.key, ctrl = e.ctrlKey || e.metaKey;
    if (ctrl && k.toLowerCase() === 'z') { e.preventDefault(); e.shiftKey ? $('#edRedo').click() : $('#edUndo').click(); return; }
    if (ctrl && k.toLowerCase() === 'y') { e.preventDefault(); $('#edRedo').click(); return; }
    if (k === ' ') { e.preventDefault(); $('#edPlay').click(); return; }
    if (!l) return;
    if (k === 'Delete' || k === 'Backspace') { e.preventDefault(); removeLayer(l); return; }
    if (ctrl && k.toLowerCase() === 'd') { e.preventDefault(); dupLayer(l); return; }
    const step = e.shiftKey ? 20 : 2, mv = {ArrowLeft: [-step, 0], ArrowRight: [step, 0], ArrowUp: [0, -step], ArrowDown: [0, step]}[k];
    if (mv) { e.preventDefault(); l.x += mv[0]; l.y += mv[1]; syncPropsLive(); clearTimeout(ED.kt); ED.kt = setTimeout(commit, 400); }
    if (k === 'Escape') { ED.sel = null; syncProps(); if (window.__sheetClose) window.__sheetClose(); }
  });

  /* ================================================================ layer actions */
  function removeLayer(l) {
    if (l.kind === 'captions' || l.kind === 'hook') { l.hidden = true; toast(`${l.name} removed. Tap the eye on its box to bring it back`); }
    else ED.scene.layers = L().filter(x => x !== l);
    ED.sel = null; syncAll(); commit();
  }
  function dupLayer(l) {
    if (l.kind === 'captions' || l.kind === 'hook') return toast('Captions and hook can only exist once', true);
    const c = clone(l); c.id = uid(); c.x += 40; c.y += 40; L().splice(L().indexOf(l) + 1, 0, c); ED.sel = c.id; syncAll(); commit();
  }
  function moveZ(l, d) { const a = L(), i = a.indexOf(l), j = clamp(i + d, 0, a.length - 1); a.splice(i, 1); a.splice(j, 0, l); syncAll(); commit(); }
  const addLayer = l => { l.id = l.id || uid(); L().push(l); ED.sel = l.id; syncAll(); commit(); return l; };

  /* ================================================================ layout presets */
  const PRESETS = [
    ['Cam top', () => [videoLayer('game', 0, 640, 1080, 1280), videoLayer('cam', 0, 0, 1080, 640)]],
    ['Cam bottom', () => [videoLayer('game', 0, 0, 1080, 1280), videoLayer('cam', 0, 1280, 1080, 640)]],
    ['Gameplay only', () => [videoLayer('game', 0, 0, 1080, 1920)]],
    ['Bubble cam', () => [videoLayer('game', 0, 0, 1080, 1920), videoLayer('cam', 60, 150, 360, 360, 'circle', {border: 8})]],
    ['Heart cam', () => [videoLayer('game', 0, 0, 1080, 1920), videoLayer('cam', 330, 120, 420, 380, 'heart', {border: 8, bcolor: '#ff3d6e'})]],
    ['Floating 16:9', () => [videoLayer('game', 30, 760, 1020, 574, 'round', {radius: 0.08}), videoLayer('cam', 300, 230, 480, 480, 'circle', {border: 8})], {type: 'blur'}],
    ['Cards', () => [videoLayer('cam', 90, 140, 900, 560, 'round', {radius: 0.1, border: 6}), videoLayer('game', 30, 760, 1020, 860, 'round', {radius: 0.06})], {type: 'gradient'}],
    ['Arch cam', () => [videoLayer('game', 0, 700, 1080, 1220), videoLayer('cam', 140, 60, 800, 620, 'arch', {border: 6})], {type: 'blur'}],
  ];
  function drawPresets() {
    $('#edPresets').innerHTML = PRESETS.map((p, i) => `<button data-i="${i}"><canvas width="54" height="96"></canvas>${esc(p[0])}</button>`).join('');
    $$('#edPresets button').forEach(b => {
      const [, mk, bg] = PRESETS[+b.dataset.i], c = b.querySelector('canvas').getContext('2d'), k = 54 / 1080;
      c.fillStyle = bg?.type === 'gradient' ? '#4a2a7a' : '#2b2f3a'; c.fillRect(0, 0, 54, 96);
      ED.clip && mk().forEach(l => {
        const pts = shapePoints(l.shape, l.w * k, l.h * k, l.radius);
        c.beginPath(); pts.forEach(([x, y], i) => i ? c.lineTo(l.x * k + x, l.y * k + y) : c.moveTo(l.x * k + x, l.y * k + y)); c.closePath();
        c.fillStyle = l.role === 'cam' ? '#ff3d6e' : '#2ee6c8'; c.fill();
      });
      b.onclick = act(() => {
        const vids = mk(); vids.forEach(fitCrop);
        const others = L().filter(l => l.kind !== 'video');
        ED.scene.layers = [...vids, ...others];
        if (bg) ED.scene.bg.type = bg.type;
        ED.sel = null;
      });
    });
  }
  /* ---------------- my saved layouts (shared by laptop and phone) */
  function layoutThumb(c, sc) {
    const k = 54 / 1080; c.fillStyle = sc.bg?.type === 'gradient' ? '#4a2a7a' : '#2b2f3a'; c.fillRect(0, 0, 54, 96);
    (sc.layers || []).forEach(l => {
      if (l.kind === 'image') {
        const im = loadImg(l.file); const h = imgH(l);
        if (im && im.complete && im.naturalWidth) { try { c.drawImage(im, l.x * k, l.y * k, l.w * k, h * k); return; } catch (e) { /* */ } }
        c.fillStyle = '#ffc94a'; c.fillRect(l.x * k, l.y * k, l.w * k, h * k);
      } else if (l.kind === 'video') { c.fillStyle = l.role === 'cam' ? '#ff3d6e' : '#2ee6c8'; c.fillRect(l.x * k, l.y * k, l.w * k, l.h * k); }
    });
  }
  async function drawMyLayouts() {
    const j = await api('/api/layouts'); const list = (j.ok && j.layouts) || [];
    list.forEach(L0 => (L0.scene.layers || []).forEach(l => l.kind === 'image' && loadImg(l.file)));
    $('#edMyLayouts').innerHTML = list.map((l, i) => `<button data-i="${i}"><canvas width="54" height="96"></canvas>${esc(l.name)}<span class="laydel" title="Delete">✕</span></button>`).join('')
      || '<span class="mut">No saved layouts yet - arrange this clip, then Save this layout.</span>';
    const paint = () => $$('#edMyLayouts button').forEach(b => layoutThumb(b.querySelector('canvas').getContext('2d'), list[+b.dataset.i].scene));
    paint(); setTimeout(paint, 700);
    $$('#edMyLayouts button').forEach(b => {
      const lay = list[+b.dataset.i];
      b.querySelector('.laydel').onclick = async e => {
        e.stopPropagation(); if (!confirm(`Delete the layout "${lay.name}"?`)) return;
        const r = await api('/api/layouts', {name: lay.name, delete: true}); if (r.ok) { drawMyLayouts(); window.loadLayouts && loadLayouts(); }
      };
      b.onclick = act(() => {
        const sc = clone(lay.scene);
        sc.layers.forEach(l => { l.id = uid(); delete l.keys; if (l.kind === 'image') loadImg(l.file); if (l.kind === 'video') fitCrop(l); });
        if (sc.bg && sc.bg.image) loadImg(sc.bg.image);
        const keep = L().filter(l => l.kind === 'text' && (l.start > 0 || l.end > 0));
        const capOld = capL(), hookOld = hookL();
        ED.scene = {...sc, layers: [...sc.layers, ...keep], fx: ED.scene.fx || [], sfx: ED.scene.sfx || []};
        if (!capL() && capOld) ED.scene.layers.push(capOld);
        if (!hookL() && hookOld) ED.scene.layers.push(hookOld);
        ED.sel = null; toast(`Layout "${lay.name}" applied, press Save & render`);
      });
    });
  }
  $('#edLayoutSave').onclick = async () => {
    const name = (prompt('Name this layout (e.g. Carry Minati):', ED.clip && ED.clip.channel ? '' : '') || '').trim(); if (!name) return;
    const sc = clone(ED.scene);
    sc.layers = sc.layers.filter(l => !(l.kind === 'text' && (l.start > 0 || l.end > 0)));
    sc.layers.forEach(l => delete l.keys); sc.fx = []; sc.sfx = [];
    const j = await api('/api/layouts', {name, scene: sc});
    if (j.ok) { toast(`Saved layout "${name}"`); drawMyLayouts(); window.loadLayouts && loadLayouts(); }
  };
  /* ---------------- bleep words */
  const BL = () => ED.scene.bleep;
  const isBleeped = w => BL().spans.some(([a, b]) => w[0] < b - 0.02 && w[1] > a + 0.02);
  function drawBleep() {
    if (!ED.scene || !ED.scene.bleep) return;
    $$('#edBlMode button').forEach(b => b.classList.toggle('on', b.dataset.v === BL().mode));
    $('#edBlCam').checked = !!BL().camblur;
    const inClip = ED.words.map((w, i) => [w, i]).filter(([w]) => ED.end == null || (w[1] > ED.start && w[0] < ED.end));
    $('#edBlWords').innerHTML = inClip.map(([w, i]) => `<button data-i="${i}" class="${isBleeped(w) ? 'on' : ''}">${esc(w[2])}</button>`).join('')
      || '<span class="mut">No caption words in this clip.</span>';
    $('#edBlCount').textContent = BL().spans.length ? `${BL().spans.length} bleeped` : '';
    $$('#edBlWords button').forEach(b => b.onclick = act(() => {
      const w = ED.words[+b.dataset.i];
      if (isBleeped(w)) BL().spans = BL().spans.filter(([a, z]) => !(w[0] < z - 0.02 && w[1] > a + 0.02));
      else BL().spans.push([+w[0].toFixed(3), +w[1].toFixed(3)]);
      drawBleep();
    }));
  }
  $$('#edBlMode button').forEach(b => b.onclick = act(() => { BL().mode = b.dataset.v; drawBleep(); }));
  $('#edBlCam').onchange = act(e => { BL().camblur = e.target.checked; });
  $('#edBlClear').onclick = act(() => { BL().spans = []; drawBleep(); });
  $('#edBlAvoid').onclick = act(() => {
    const bad = String((CFG.settings && CFG.settings.avoid_words) || '').toLowerCase().split(/[,\n]+/).map(x => x.trim()).filter(Boolean);
    if (!bad.length) return toast('Add your avoid-words in Settings → Posting first', true);
    let n = 0;
    ED.words.forEach(w => { const k = String(w[2]).toLowerCase().replace(/[^\p{L}\p{N}']+/gu, '');
      if (bad.includes(k) && !isBleeped(w)) { BL().spans.push([+w[0].toFixed(3), +w[1].toFixed(3)]); n++; } });
    drawBleep(); toast(n ? `Bleeped ${n} word${n > 1 ? 's' : ''}` : 'None of your avoid-words are spoken in this clip');
  });
  /* ---------------- always-fix words */
  let FIXES = {};
  async function drawFixes(j) {
    j = j || await api('/api/wordfixes'); if (!j.ok) return; FIXES = j.fixes || {};
    $('#edFixList').innerHTML = Object.entries(FIXES).map(([a, b]) => `<button data-k="${esc(a)}" title="Remove">${esc(a)} → <b>${esc(b)}</b> ✕</button>`).join('')
      || '<span class="mut">Nothing yet.</span>';
    $$('#edFixList button').forEach(b => b.onclick = async () => drawFixes(await api('/api/wordfixes', {from: b.dataset.k, delete: true})));
  }
  $('#edFixAdd').onclick = async () => {
    const from = $('#edFixFrom').value.trim(), to = $('#edFixTo').value.trim();
    if (!from || !to) return toast('Type the wrong word and the right spelling', true);
    const j = await api('/api/wordfixes', {from, to}); if (!j.ok) return;
    $('#edFixFrom').value = ''; $('#edFixTo').value = ''; drawFixes(j); toast(`"${from}" will always be "${to}" from now on`);
  };
  $('#edFixApply').onclick = act(() => {
    const key = w => String(w).toLowerCase().replace(/[^\p{L}\p{N}']+/gu, '');
    const rules = Object.entries(FIXES).map(([a, b]) => [a.split(/\s+/), b]).sort((x, y) => y[0].length - x[0].length);
    const out = []; let i = 0, n = 0;
    while (i < ED.words.length) {
      const hit = rules.find(([ks]) => ks.every((k, j) => ED.words[i + j] && key(ED.words[i + j][2]) === k));
      if (hit) { const last = ED.words[i + hit[0].length - 1]; out.push([ED.words[i][0], last[1], hit[1] + (String(last[2]).match(/[^\p{L}\p{N}']*$/u) || [''])[0]]); i += hit[0].length; n++; }
      else out.push(ED.words[i++]);
    }
    ED.words = out; renderCaptionsList(); drawBleep();
    toast(n ? `Fixed ${n} word${n > 1 ? 's' : ''} - press Save & render` : 'Nothing to fix in this clip');
  });
  /* ---------------- background music + loop ending */
  const MU = () => ED.scene.music;
  const muAudio = new Audio(); muAudio.preload = 'auto';
  async function loadMusicList(sel) {
    const j = await api('/api/music'); const list = (j.ok && j.files) || [];
    $('#edMuFile').innerHTML = '<option value="">No music</option>' + list.map(f => `<option value="${esc(f)}">${esc(f.replace(/^music_/, '').replace(/\.[^.]+$/, ''))}</option>`).join('');
    if (sel) MU().file = sel;
    drawMusic();
  }
  function drawMusic() {
    const m = MU(); $('#edMuFile').value = m.file || '';
    $('#edMuVol').value = m.vol; $('#edMuPeak').value = m.peak; $('#edMuRise').value = String(m.rise);
    $('#edMuOff').value = m.offset || 0; $('#edMuDuck').checked = m.duck !== false; $('#edLoop').checked = !!ED.scene.loop;
    $$('.mubox').forEach(e => e.style.opacity = m.file ? 1 : 0.4);
    if (m.file && !muAudio.src.endsWith(encodeURIComponent(m.file))) muAudio.src = '/assets/' + encodeURIComponent(m.file);
    if (!m.file) muAudio.pause();
  }
  $('#edMuFile').onchange = act(e => { MU().file = e.target.value; drawMusic(); });
  $('#edMuVol').oninput = e => { MU().vol = +e.target.value; }; $('#edMuVol').onchange = act(() => {});
  $('#edMuPeak').oninput = e => { MU().peak = +e.target.value; }; $('#edMuPeak').onchange = act(() => {});
  $('#edMuRise').onchange = act(e => { MU().rise = +e.target.value; });
  $('#edMuOff').onchange = act(e => { MU().offset = Math.max(0, +e.target.value || 0); });
  $('#edMuDuck').onchange = act(e => { MU().duck = e.target.checked; });
  $('#edLoop').onchange = act(e => { ED.scene.loop = e.target.checked; });
  $('#edMuUp').onchange = async e => {
    const f = e.target.files[0]; e.target.value = ''; if (!f) return;
    const fd = new FormData(); fd.append('file', f); toast('Uploading music…');
    const j = await fetch('/api/music', {method: 'POST', body: fd}).then(r => r.json()).catch(() => ({}));
    if (!j.ok) return toast(j.error || 'Upload failed', true);
    await loadMusicList(j.file); commit(); toast('Music added under the clip');
  };
  function musicTick() {        // preview: play the music with the video at its volume curve
    const m = ED.scene && ED.scene.music;
    if (!m || !m.file || V.paused) { if (!muAudio.paused) muAudio.pause(); return; }
    const t = Math.max(0, V.currentTime - ED.start), dur = Math.max(0.1, ED.end - ED.start), r0 = dur - (m.rise || 0);
    let v = m.rise > 0 && t > r0 ? m.vol + (m.peak - m.vol) * (t - r0) / m.rise : m.vol;
    muAudio.volume = Math.max(0, Math.min(1, v));
    const want = (m.offset || 0) + t;
    if (muAudio.duration && Math.abs(muAudio.currentTime - (want % muAudio.duration)) > 0.35) muAudio.currentTime = want % muAudio.duration;
    if (muAudio.paused) muAudio.play().catch(() => {});
  }
  setInterval(musicTick, 250);
  $('#edAddGame').onclick = act(() => { const l = videoLayer('game', 140, 560, 800, 450, 'round'); fitCrop(l); addLayer(l); });
  $('#edAddCam').onclick = act(() => { const l = videoLayer('cam', 340, 200, 400, 400, 'circle', {border: 6}); fitCrop(l); addLayer(l); });

  /* ================================================================ background + progress bar */
  $$('#edBgType button').forEach(b => b.onclick = act(() => {
    ED.scene.bg.type = b.dataset.v;
    if (b.dataset.v === 'image' && !ED.scene.bg.image) toast('Pick an image or upload one');
  }));
  $('#edBgC1').oninput = e => { ED.scene.bg.color = e.target.value; };
  $('#edBgC2').oninput = e => { ED.scene.bg.color2 = e.target.value; };
  $('#edBgBlur').oninput = e => { ED.scene.bg.blur = +e.target.value; };
  $('#edBgDim').oninput = e => { ED.scene.bg.dim = +e.target.value; };
  async function uploadImage(file) {
    const fd = new FormData(); fd.append('file', file);
    const j = await fetch('/api/assets/image', {method: 'POST', body: fd}).then(r => r.json()).catch(() => ({}));
    if (!j.ok) { toast(j.error || 'Upload failed', true); return null; }
    loadImg(j.file); rememberImg(j.file); return j.file;
  }
  function drawBgPick(a) {   // background images: upload first, then the same images as the Image tool
    const P = $('#edBgPick'); if (!P) return; a = a || recentImgs();
    P.innerHTML = `<button class="up" data-up="1" aria-label="Upload a background"><svg class="i" aria-hidden="true"><use href="#i-upload"/></svg>Upload</button>`
      + a.map(f => `<button data-f="${esc(f)}" class="${ED.scene && ED.scene.bg.image === f ? 'on' : ''}" title="${esc(f)}"><img src="/assets/${encodeURIComponent(f)}" loading="lazy" alt=""></button>`).join('');
    P.querySelector('[data-up]').onclick = () => $('#edBgFile').click();
    $$('#edBgPick [data-f]').forEach(b => b.onclick = () => { loadImg(b.dataset.f); ED.scene.bg.image = b.dataset.f; ED.scene.bg.type = 'image'; syncAll(); commit(); drawBgPick(); });
  }
  $('#edBgFile').onchange = async e => {
    const f = e.target.files[0]; e.target.value = ''; if (!f) return;
    const name = await uploadImage(f); if (!name) return;
    ED.scene.bg.image = name; ED.scene.bg.type = 'image'; syncAll(); commit(); drawBgPick(); toast('Background added');
  };
  $('#edBar').onchange = e => { ED.scene.progress.on = e.target.checked; };
  $('#edBarC').oninput = e => { ED.scene.progress.color = e.target.value; };
  $('#edBarPos').onchange = e => { ED.scene.progress.pos = e.target.value; };

  /* ================================================================ my style */
  $('#edStyleSave').onclick = async () => {
    const sc = clone(ED.scene);
    sc.layers = sc.layers.filter(l => !(l.kind === 'text' && (l.start > 0 || l.end > 0)));   // clip-specific text stays with this clip
    sc.fx = []; sc.sfx = [];
    const j = await api('/api/style', {scene: sc, on: true});
    if (j.ok) { CFG.settings.style_scene = sc; CFG.settings.style_on = true; $('#edStyleOn').checked = true; toast('Saved. New clips will use this look'); }
  };
  $('#edStyleApply').onclick = act(() => {
    const st = CFG.settings.style_scene; if (!st) return toast('Save a style first', true);
    const sc = clone(st), t = {...CFG.defaults, ...(ED.clip.template || {})};
    sc.layers.forEach(l => { l.id = uid(); if (l.kind === 'video' && (l.role === 'cam' || l.role === 'game')) { l.crop = [...t[l.role]]; fitCrop(l); } if (l.kind === 'image') loadImg(l.file); });
    const keep = L().filter(l => l.kind === 'text' && (l.start > 0 || l.end > 0));
    ED.scene = {...sc, layers: [...sc.layers, ...keep], fx: ED.scene.fx || [], sfx: ED.scene.sfx || []}; ED.sel = null;
  });
  $('#edStyleOn').onchange = async e => { const j = await api('/api/style', {on: e.target.checked}); if (j.ok) CFG.settings.style_on = e.target.checked; };

  /* ================================================================ captions + hook */
  function groups() {
    const st = style(), out = []; let cur = [];
    ED.words.forEach((w, i) => {
      const prev = cur.length ? ED.words[cur[cur.length - 1]] : null;
      if (cur.length && (cur.length >= (st.words || 3) || w[0] - prev[1] > 0.6 || /[.!?]$/.test(prev[2]))) { out.push(cur); cur = []; }
      cur.push(i);
    });
    if (cur.length) out.push(cur);
    return out;
  }
  function curGroup() {
    const t = V.currentTime;
    for (const idx of groups()) { const a = ED.words[idx[0]], z = ED.words[idx[idx.length - 1]]; if (t >= a[0] && t <= z[1] + 0.15) return idx; }
    return null;
  }
  function curCapText() { const g = curGroup(), st = style(); return g ? g.map(i => st.upper ? ED.words[i][2].toUpperCase() : ED.words[i][2]).join(' ') : ''; }
  function renderCaptionsList() {
    const g = groups();
    $('#edCapList').innerHTML = g.length ? g.map((idx, gi) => {
      const a = ED.words[idx[0]], b = ED.words[idx[idx.length - 1]];
      return `<div class="caprow"><button class="btn sm" data-seek="${a[0]}">${fmt(Math.max(0, a[0] - ED.start))}</button>
        <input data-g="${gi}" value="${esc(idx.map(i => ED.words[i][2]).join(' '))}"><span class="mut">${(b[1] - a[0]).toFixed(1)}s</span></div>`;
    }).join('') : '<div class="mut">No speech was found in this clip. Use Text &amp; images to add your own text.</div>';
    $$('#edCapList [data-seek]').forEach(b => b.onclick = () => { V.currentTime = +b.dataset.seek; });
    drawBleep();
    $$('#edCapList input').forEach(inp => inp.onchange = () => {
      const idx = groups()[+inp.dataset.g], a = ED.words[idx[0]][0], z = ED.words[idx[idx.length - 1]][1];
      const toks = inp.value.trim().split(/\s+/).filter(Boolean), step = (z - a) / Math.max(1, toks.length);
      ED.words.splice(idx[0], idx.length, ...toks.map((t, k) => [a + k * step, a + (k + 1) * step, t]));
      renderCaptionsList();
    });
  }
  $('#edTrGo').onclick = async () => {
    if (!ED.words.length) return toast('This clip has no captions to translate', true);
    ED.origWords = ED.origWords || clone(ED.words);
    $('#edTrGo').disabled = true; $('#edTrGo').textContent = 'Translating…';
    const j = await api(`/api/clips/${encodeURIComponent(ED.clip.name)}/translate`, {lang: $('#edTrLang').value, meta: false, captions: true, render: false});
    $('#edTrGo').disabled = false; $('#edTrGo').textContent = 'Translate captions';
    if (j.ok) { ED.words = j.words.map(w => [+w[0], +w[1], String(w[2])]); renderCaptionsList(); commit(); toast('Translated. Press Save & render when you are happy'); }
  };
  $('#edRecap').onclick = async () => {
    if (ED.words.length && !confirm('Replace the captions with new ones made from the sound? Your edits to caption words will be lost.')) return;
    const b = $('#edRecap'); b.disabled = true; b.textContent = 'Listening…';
    const j = await api(`/api/clips/${encodeURIComponent(ED.clip.name)}/recaption`, {speech: $('#edSpeech').value});
    b.disabled = false; b.textContent = 'Re-make captions';
    if (j.ok) { ED.words = j.words.map(w => [+w[0], +w[1], String(w[2])]); ED.origWords = null; renderCaptionsList(); commit();
      toast(`New captions (${j.words.length} words, ${j.seconds} s), press Save & render`); }
  };
  $('#edTrUndo').onclick = async () => {
    const j = await api(`/api/clips/${encodeURIComponent(ED.clip.name)}/translate`, {meta: false, captions: true, restore: true, render: false});
    if (j.ok) { ED.words = j.words.map(w => [+w[0], +w[1], String(w[2])]); renderCaptionsList(); commit(); toast('Original captions back'); }
  };
  $('#edStyle').onchange = e => { capL().style = e.target.value; capL().color = ''; capL().hi = ''; capL().font = ''; syncAll(); renderCaptionsList(); };
  $('#edCapScale').oninput = e => { capL().scale = +e.target.value; };
  $('#edCapFont').onchange = e => { capL().font = e.target.value; };
  $('#edFontFile').onchange = async e => {
    const f = e.target.files[0]; e.target.value = ''; if (!f) return;
    const fd = new FormData(); fd.append('file', f);
    const j = await fetch('/api/fonts', {method: 'POST', body: fd}).then(r => r.json()).catch(() => ({}));
    if (!j.ok) return toast(j.error || 'Upload failed', true);
    CFG.fonts = j.fonts; useFonts(j.fonts); capL().font = j.family; syncAll(); commit(); toast(`Font added: ${j.family}`);
  };
  $('#edCapWords').onchange = e => { capL().words = +e.target.value; renderCaptionsList(); };
  $('#edCapAnim').onchange = act(e => { capL().anim = e.target.value; renderCaptionsList(); });
  $('#edHookAcc').onchange = act(e => { hookL().accent = e.target.value; });
  function drawHookLooks() {
    const hk = hookL(); if (!hk) return;
    $('#edHookLooks').innerHTML = Object.entries(CFG.hook_looks || {box: 'White box'}).map(([k, v]) => `<button data-v="${k}" class="${(hk.look || 'box') === k ? 'on' : ''}">${esc(v)}</button>`).join('');
    $$('#edHookLooks button').forEach(b => b.onclick = act(() => { hookL().look = b.dataset.v; drawHookLooks(); }));
    $('#edHookAcc').value = hk.accent || '#ff3d6e';
  }
  $('#edCapC').oninput = e => { capL().color = e.target.value; };
  $('#edCapHi').oninput = e => { capL().hi = e.target.value; };
  $('#edCapCReset').onclick = act(() => { capL().color = ''; capL().hi = ''; });
  $('#edHookOn').onchange = e => { hookL().hidden = !e.target.checked; };
  $('#edHookSecs').onchange = e => { hookL().secs = clamp(+e.target.value || 3.5, 0.5, 15); };

  /* ================================================================ text + images */
  const QUICK = ['WAIT FOR IT', 'NO WAY', 'WATCH TILL THE END', 'PART 2?', 'HE DID WHAT?!', 'CLUTCH', 'BRO...', 'FOLLOW FOR MORE'];
  $('#edQuick').innerHTML = QUICK.map(q => `<button>${esc(q)}</button>`).join('');
  const newText = (text, extra = {}) => {
    const t = Math.max(0, +tNow().toFixed(1));
    return addLayer({kind: 'text', name: 'Text', text, x: 540, y: 560, size: 76, color: '#ffffff', box: true, bcolor: '#000000', opacity: 1,
                     font: 'Arial Black', start: t, end: +(t + 3).toFixed(1), ...extra});
  };
  $$('#edQuick button').forEach(b => b.onclick = () => newText(b.textContent));
  $('#edCredit').onclick = act(() => {
    const ch = (ED.clip.ctx && ED.clip.ctx.channel) || ED.clip.channel || 'channel';
    addLayer({kind: 'text', name: 'Credit', text: 'Clip: ' + ch, x: 540, y: 1840, size: 34, color: '#ffffff', box: false, bcolor: '#000000', opacity: 0.85,
              font: 'Arial', start: 0, end: 0});
    toast('Credit line added at the bottom, drag it anywhere');
  });
  $('#edOutro').onclick = act(() => {
    const name = $('#edFrHandle').value.trim() || $('#edThName').value.trim() || '@yourchannel', acc = $('#edFrAcc').value || '#ff3d6e';
    const P = buildOutro(name, acc), w = P[1].pts[1][0];
    addLayer({kind: 'third', name: 'Outro ribbon', template: 'outro', platform: '', title: name, sub: '', accent: acc, prims: P,
              x: Math.round((1080 - w) / 2), y: 1560, scale: 1, start: 0, dur: 3, from_end: 3, anim: 'slide'});
    V.currentTime = Math.max(ED.start, ED.end - 1.5); toast('Outro ribbon added. It shows in the last 3 seconds');
  });
  $('#edAddText').onclick = () => { newText('YOUR TEXT'); setTimeout(() => { const i = $('#edProps [data-focus]'); if (i) { i.focus(); i.select(); } }, 30); };
  $('#edAddWm').onclick = () => {
    const name = prompt('Watermark text (your @handle):', localStorage.getItem('chipper_wm') || ''); if (!name) return;
    localStorage.setItem('chipper_wm', name);
    addLayer({kind: 'text', name: 'Watermark', text: name, x: 540, y: 1840, size: 40, color: '#ffffff', box: false, bcolor: '#000000', opacity: 0.55, font: 'Arial', start: 0, end: 0});
  };
  let SERVER_IMGS = [];
  async function loadServerImgs() { const j = await api('/api/assets/images'); if (j.ok) { SERVER_IMGS = j.files || []; drawRecent(); } }
  const recentImgs = () => { let a = []; try { a = JSON.parse(localStorage.getItem('chipper_imgs') || '[]'); } catch (e) { /* */ }
    return [...a, ...SERVER_IMGS.filter(f => !a.includes(f))].slice(0, 40); };
  function rememberImg(f) { const a = [f, ...recentImgs().filter(x => x !== f)].slice(0, 12); localStorage.setItem('chipper_imgs', JSON.stringify(a)); drawRecent(); }
  function drawRecent() {
    const a = recentImgs(); if (CFG.settings.last_logo && !a.includes(CFG.settings.last_logo)) a.push(CFG.settings.last_logo);
    $('#edRecentImgs').innerHTML = a.map(f => `<button data-f="${esc(f)}" class="imgchip" title="${esc(f)}"><img src="/assets/${encodeURIComponent(f)}" loading="lazy"></button>`).join('') || '<span class="mut">Images you upload show here.</span>';
    $$('#edRecentImgs button').forEach(b => b.onclick = () => addImage(b.dataset.f));
    drawBgPick(a);
    const R = $('#edImgRow'); if (!R) return;   // the phone tray: upload first, then your images to swipe through
    R.innerHTML = `<label class="imgtile up"><svg class="i" aria-hidden="true"><use href="#i-upload"/></svg><span>Upload</span><input type="file" id="edImgUp2" accept="image/png,image/jpeg,image/webp,image/gif" hidden></label>`
      + a.map(f => `<button class="imgtile" data-f="${esc(f)}" title="${esc(f)}"><img src="/assets/${encodeURIComponent(f)}" loading="lazy" alt=""></button>`).join('');
    $('#edImgUp2').onchange = $('#edImgFile').onchange;
    $$('#edImgRow button').forEach(b => b.onclick = () => { addImage(b.dataset.f); toast('Image added. Drag it where you want'); });
  }
  const addImage = f => { loadImg(f); addLayer({kind: 'image', name: 'Image', file: f, x: 860, y: 180, w: 280, opacity: 1, start: 0, end: 0}); };
  $('#edImgFile').onchange = async e => { const f = e.target.files[0]; e.target.value = ''; if (!f) return; const n = await uploadImage(f); if (n) { addImage(n); toast('Image added. Drag it where you want'); } };

  /* ================================================================ lower thirds */
  function drawThirdTpls() {
    ['#edThPlatRow', '#edThPlatRow0'].forEach(q => $(q).style.display = THIRDS[ED.third]?.gen ? '' : 'none');
    $('#edThirdTpls').innerHTML = Object.entries(THIRDS).map(([k, t]) => `<button data-k="${k}" class="${k === ED.third ? 'on' : ''}"><canvas width="300" height="128"></canvas>${esc(t.label)}</button>`).join('');
    $$('#edThirdTpls button').forEach(b => {
      const k = b.dataset.k, t = THIRDS[k], cv = b.querySelector('canvas'), c = cv.getContext('2d');
      const prims = buildThird(k, $('#edThName').value || 'Your Channel', t.sub, t.gen ? '' : t.acc, ED.plat), bb = primsBox(prims);
      const s = Math.min(280 / bb[2], 108 / bb[3]);
      drawPrims(prims, (300 / s - bb[2]) / 2 - bb[0], (128 / s - bb[3]) / 2 - bb[1], 1, 1, c, s);
      b.onclick = () => { ED.third = k; $$('#edThirdTpls button').forEach(x => x.classList.toggle('on', x === b)); $('#edThSub').value = t.sub; $('#edThAcc').value = t.gen ? PLATFORMS[ED.plat].color : t.acc;
        ['#edThPlatRow', '#edThPlatRow0'].forEach(q => $(q).style.display = t.gen ? '' : 'none');
        const saved = thirdNames()[k]; if (saved) $('#edThName').value = saved; };
    });
  }
  $('#edThPlat').innerHTML = Object.entries(PLATFORMS).map(([k, p]) => `<button data-p="${k}">${p.label}</button>`).join('');
  function syncPlat() { $$('#edThPlat button').forEach(b => b.classList.toggle('on', b.dataset.p === ED.plat)); }
  $$('#edThPlat button').forEach(b => b.onclick = () => { ED.plat = b.dataset.p; $('#edThAcc').value = PLATFORMS[ED.plat].color; syncPlat(); drawThirdTpls(); });
  syncPlat();
  const thirdNames = () => { try { return JSON.parse(localStorage.getItem('chipper_third_names') || '{}'); } catch (e) { return {}; } };
  async function loadAccountNames() {
    const names = new Set(Object.values(thirdNames()));
    try {
      const j = await api('/api/accounts'); const a = j.accounts || {};
      (a.youtube?.channels || []).forEach(c => c.title && names.add(c.title));
      (a.instagram?.accounts || []).forEach(c => c.username && names.add('@' + c.username));
      (a.facebook?.pages || []).forEach(c => c.name && names.add(c.name));
    } catch (e) { /* offline is fine */ }
    $('#edThChips').innerHTML = [...names].filter(Boolean).map(n => `<button>${esc(n)}</button>`).join('') || '<span class="mut">Type your channel name above.</span>';
    $$('#edThChips button').forEach(b => b.onclick = () => { $('#edThName').value = b.textContent; drawThirdTpls(); });
  }
  $('#edThName').oninput = () => { clearTimeout(ED.tt); ED.tt = setTimeout(drawThirdTpls, 250); };
  function thirdFromForm(old) {
    const name = $('#edThName').value.trim();
    if (!name) { toast('Type your channel name first', true); $('#edThName').focus(); return null; }
    const n = thirdNames(); n[ED.third] = name; localStorage.setItem('chipper_third_names', JSON.stringify(n));
    const sub = $('#edThSub').value.trim(), acc = $('#edThAcc').value;
    const prims = buildThird(ED.third, name, sub, acc, ED.plat), bb = primsBox(prims);
    if (!THIRDS[ED.third]) ED.third = 'darkpill';
    const gen = THIRDS[ED.third].gen;
    return {kind: 'third', name: THIRDS[ED.third].label + (gen ? ` (${PLATFORMS[ED.plat].label})` : ''), template: ED.third, platform: ED.plat, title: name, sub, accent: acc, prims,
            x: old ? old.x : Math.round((1080 - bb[2]) / 2), y: old ? old.y : 1500, scale: old ? old.scale : 1,
            start: Math.max(0, +$('#edThStart').value || 0), dur: Math.max(0, +$('#edThDur').value || 0), anim: $('#edThAnim').value, topts: {...ED.thOpts}};
  }
  $('#edThAdd').onclick = () => { const l = thirdFromForm(); if (!l) return; addLayer(l); V.currentTime = ED.start + l.start + 0.5; toast('Lower third added. Drag it to place it'); };
  $('#edThUpd').onclick = act(() => { const s = selL(); if (!s || s.kind !== 'third') return; const n = thirdFromForm(s); if (n) Object.assign(s, n); });


  /* ================================================================ meme effects + sounds */
  const FXN = {zoom: ['🔍', 'Zoom punch', 0.6], shake: ['📳', 'Shake', 0.8], flash: ['⚡', 'Flash', 0.35], bw: ['⚫', 'Black & white', 2],
               red: ['🩸', 'Red drama', 2], glitch: ['👾', 'Glitch', 0.5], fry: ['🍟', 'Deep fry', 1], blue: ['🌧', 'Sad blue', 2.5], dark: ['🌑', 'Dark corners', 2.5]};
  const bigText = (text, color, extra = {}) => ({kind: 'text', name: 'Effect text', text, x: 540, y: 820, size: 150,
    color, box: false, bcolor: '#000000', opacity: 1, font: FONTS.includes('Pricedown Black') ? 'Pricedown Black' : 'Impact', ...extra});
  /* ---------- stickers: little vector drawings (render the same in the preview and the final video) */
  const SK = {
    circ: (cx, cy, r, color, o = {}) => ({t: 'rr', x: cx - r, y: cy - r, w: 2 * r, h: 2 * r, r, color, ...o}),
    ell: (cx, cy, rx, ry, color, o = {}) => ({t: 'rr', x: cx - rx, y: cy - ry, w: 2 * rx, h: 2 * ry, r: Math.min(rx, ry), color, ...o}),
    poly: (pts, color, o = {}) => ({t: 'poly', pts: pts.map(q => [Math.round(q[0]), Math.round(q[1])]), color, ...o}),
    txt: (x, y, text, size, color, o = {}) => ({t: 'text', x, y, text, size, color, font: 'Arial Black', an: 5, bw: 6, bc: '#000000', ...o}),
    drop(cx, cy, r, color, o = {}) {           // tear / sweat drop, point up
      const pts = [[cx, cy - r * 2.1]];
      for (let i = 0; i <= 24; i++) { const a = -Math.PI / 5 + (i / 24) * (Math.PI + 2 * Math.PI / 5); pts.push([cx + r * Math.cos(a), cy + r * Math.sin(a)]); }
      return SK.poly(pts, color, o);
    },
    heart(cx, cy, r, color, o = {}) {
      const pts = []; for (let i = 0; i < 40; i++) { const t = (i / 40) * Math.PI * 2;
        pts.push([cx + r * 16 * Math.pow(Math.sin(t), 3) / 16, cy - r * (13 * Math.cos(t) - 5 * Math.cos(2 * t) - 2 * Math.cos(3 * t) - Math.cos(4 * t)) / 16]); }
      return SK.poly(pts, color, o);
    },
    star(cx, cy, r, color, o = {}) {           // 4-point sparkle
      const pts = []; for (let i = 0; i < 8; i++) { const a = i * Math.PI / 4 - Math.PI / 2, rr = i % 2 ? r * 0.28 : r; pts.push([cx + rr * Math.cos(a), cy + rr * Math.sin(a)]); }
      return SK.poly(pts, color, o);
    },
    ray(cx, cy, a, r1, r2, w, color) {         // thin wedge pointing outwards
      const p = (r, d) => [cx + r * Math.cos(a + d), cy + r * Math.sin(a + d)];
      return SK.poly([p(r1, -w / r1), p(r2, 0), p(r1, w / r1)], color);
    },
    bird(x, y, s, color) {                     // simple flying bird (two wings)
      return SK.poly([[x - 60 * s, y - 18 * s], [x - 30 * s, y - 30 * s], [x, y], [x + 30 * s, y - 30 * s], [x + 60 * s, y - 18 * s],
        [x + 32 * s, y - 20 * s], [x, y + 10 * s], [x - 32 * s, y - 20 * s]], color);
    },
  };
  const STICKERS = {
    question: ['❓', 'Question marks', 1.8, () => [SK.txt(80, 120, '?', 220, '#ffffff', {bw: 9}), SK.txt(230, 60, '?', 150, '#ffd400', {bw: 7}), SK.txt(330, 150, '?', 105, '#ffffff', {bw: 6})], 380, 330],
    shock: ['❗', 'Shock lines !?', 1.2, () => [...Array.from({length: 14}, (_, i) => SK.ray(220, 220, i * Math.PI * 2 / 14 + 0.1, 120, 215 + (i % 2) * 35, 14, '#ffffff')),
      SK.txt(220, 225, '!?', 170, '#ff2d55', {bw: 9, bc: '#ffffff'})], 320, 360],
    sweat: ['💧', 'Sweat drop', 2.5, () => [SK.drop(70, 140, 55, '#7fd3ff', {bw: 4, bc: '#0b3a5c'}), SK.ell(52, 140, 12, 22, '#ffffff', {alpha: 0.85})], 740, 520],
    thought: ['💭', 'Thought bubble', 2.4, () => [SK.ell(250, 140, 230, 130, '#ffffff', {bw: 5, bc: '#222222'}), SK.circ(70, 330, 38, '#ffffff', {bw: 5, bc: '#222222'}),
      SK.circ(25, 400, 20, '#ffffff', {bw: 4, bc: '#222222'}), SK.txt(250, 140, '. . .', 120, '#222222', {bw: 0})], 520, 230],
    birds: ['🐦', 'Birds', 2.4, () => [SK.bird(110, 110, 1.8, '#1b1f27'), SK.bird(320, 50, 1.3, '#1b1f27'), SK.bird(400, 175, 1.05, '#1b1f27')].map(p => ({...p, bw: 4, bc: '#ffffff'})), 40, 360],
    cloud: ['🌧', 'Rain cloud', 3, () => [...[[40, 260], [90, 310], [150, 320], [210, 300], [250, 260]].map(([x, y]) => SK.poly([[x, y], [x + 7, y], [x - 8, y + 55], [x - 15, y + 55]], '#6fb7ff')),
      SK.circ(90, 170, 75, '#7d8796'), SK.circ(180, 130, 95, '#8a95a5'), SK.circ(270, 180, 68, '#7d8796'), SK.ell(180, 215, 170, 45, '#7d8796')], 360, 120],
    anger: ['💢', 'Anger mark', 1.6, () => [0, 1, 2, 3].map(i => { const a = i * Math.PI / 2 + Math.PI / 4, c = Math.cos(a), sn = Math.sin(a), cx = 100 + 45 * c, cy = 100 + 45 * sn;
      return SK.poly([[cx - 30 * sn, cy + 30 * c], [cx + 30 * sn, cy - 30 * c], [cx + 30 * sn + 18 * c, cy - 30 * c + 18 * sn], [cx + 10 * c, cy + 10 * sn], [cx - 30 * sn + 18 * c, cy + 30 * c + 18 * sn]], '#ff2b2b', {bw: 3, bc: '#ffffff'}); }), 760, 380],
    hearts: ['💕', 'Hearts', 2.5, () => [SK.heart(80, 120, 70, '#ff4f8b', {bw: 4, bc: '#ffffff'}), SK.heart(230, 60, 45, '#ff7aa8', {bw: 3, bc: '#ffffff'}), SK.heart(300, 170, 35, '#ff4f8b', {bw: 3, bc: '#ffffff'})], 380, 300],
    sparkle: ['✨', 'Sparkles', 1.8, () => [SK.star(90, 90, 85, '#ffe14d'), SK.star(250, 50, 50, '#ffffff'), SK.star(230, 190, 38, '#ffe14d')], 380, 320],
    eyes: ['👀', 'Side eye', 2, () => [SK.ell(90, 90, 80, 95, '#ffffff', {bw: 5, bc: '#111111'}), SK.ell(270, 90, 80, 95, '#ffffff', {bw: 5, bc: '#111111'}),
      SK.circ(130, 105, 38, '#111111'), SK.circ(310, 105, 38, '#111111'), SK.circ(142, 92, 10, '#ffffff'), SK.circ(322, 92, 10, '#ffffff')], 360, 300],
    dots: ['💬', 'Awkward ...', 2.6, () => [SK.txt(160, 60, '...', 200, '#ffffff', {bw: 8})], 380, 760],
  };
  function stickerLayer(k, t0, dur, at) {
    const S = STICKERS[k], prims = S[3]();
    return {kind: 'third', id: uid(), name: 'Sticker', title: S[1], template: 'sticker', platform: '', sub: '', accent: '#ffffff', prims,
      x: at ? at[0] : S[4], y: at ? at[1] : S[5], scale: 1, start: +t0.toFixed(1), dur: dur || S[2], anim: 'fade'};
  }
  const COMBOS = [
    ['🤔', 'Thinking', {fx: [['zoom', 0, 2.4, 0.25]], sfx: [['birds', 0]], stk: [['thought'], ['birds']]}],
    ['😮', 'Surprised', {fx: [['zoom', 0, 0.8, 0.8], ['flash', 0, 0.15, 0.5, '#ffffff'], ['shake', 0, 0.35, 0.4]], sfx: [['gasp', 0]], stk: [['shock']]}],
    ['😞', 'Disappointed', {fx: [['blue', 0, 3.2, 0.85], ['zoom', 0, 3.2, 0.2]], sfx: [['sad', 0]], stk: [['cloud']]}],
    ['😩', 'Faaah', {fx: [['zoom', 0, 1.9, 0.45], ['shake', 0.12, 1.2, 0.55]], sfx: [['faah', 0]], text: ['FAAAH', '#ffffff', 1.9]}],
    ['😬', 'Close miss', {fx: [['zoom', 0, 0.6, 0.6], ['shake', 0, 0.5, 0.6]], sfx: [['whoosh', 0], ['ooh', 0.25]], text: ['SO CLOSE!', '#ffd400', 2], stk: [['sweat']]}],
    ['😟', 'Worried', {fx: [['dark', 0, 3, 0.75], ['zoom', 0, 3, 0.2]], sfx: [['heartbeat', 0]], stk: [['sweat']]}],
    ['😐', 'Awkward', {fx: [['zoom', 0, 2.7, 0.2]], sfx: [['crickets', 0]], stk: [['dots']]}],
    ['👀', 'Side eye', {fx: [['zoom', 0, 2, 0.5]], sfx: [['boing', 0]], stk: [['eyes']]}],
    ['😳', 'Plot twist', {fx: [['red', 0, 2.6, 0.6], ['zoom', 0.9, 1.7, 0.45]], sfx: [['dundun', 0]], text: ['PLOT TWIST', '#ff2d55', 2.6]}],
    ['🥁', 'Ba dum tss', {fx: [['zoom', 0.35, 0.8, 0.3]], sfx: [['rimshot', 0]]}],
    ['😤', 'Angry', {fx: [['red', 0, 1.6, 0.7], ['shake', 0, 0.8, 0.7]], sfx: [['boom', 0]], stk: [['anger']]}],
    ['🥰', 'Wholesome', {fx: [['flash', 0, 0.3, 0.3, '#ffc0d9']], sfx: [['ding', 0]], stk: [['hearts'], ['sparkle']]}],
    ['🎢', 'Nope / fall', {fx: [['zoom', 0, 1, 0.5]], sfx: [['slide', 0]], text: ['NOPE', '#ffffff', 1.2]}],
    ['❓', 'Confused', {fx: [['zoom', 0, 1.6, 0.35]], sfx: [['ding', 0]], stk: [['question']]}],
    ['💥', 'Boom zoom', {fx: [['zoom', 0, 0.7, 0.7]], sfx: [['boom', 0]]}],
    ['🧨', 'Explosion', {fx: [['flash', 0, 0.45, 1, '#ff9a00'], ['shake', 0, 1.1, 0.9]], sfx: [['explosion', 0]]}],
    ['😵', 'Fail', {fx: [['bw', 0, 2.4, 0.7], ['zoom', 0, 2.4, 0.3]], sfx: [['fail', 0]], text: ['FAIL', '#ff3d3d', 2.4]}],
    ['👾', 'Glitch', {fx: [['glitch', 0, 0.6, 0.9]], sfx: [['glitch', 0]]}],
    ['🍟', 'Deep fry', {fx: [['fry', 0, 1.3, 0.9], ['shake', 0, 1.3, 0.5]], sfx: [['airhorn', 0]]}],
    ['🔨', 'Bonk', {fx: [['shake', 0, 0.35, 0.7], ['flash', 0, 0.15, 0.6, '#ffffff']], sfx: [['bonk', 0]]}],
    ['😱', 'Suspense', {fx: [['red', 0, 2.5, 0.8], ['zoom', 1.6, 0.9, 0.3]], sfx: [['riser', 0]]}],
    ['🏆', 'Mission passed', {fx: [['flash', 0, 0.3, 0.5, '#ffffff']], sfx: [['tada', 0]], text: ['MISSION PASSED', '#ffc300', 2.6], text2: ['RESPECT +', '#ffffff', 2.6]}],
    ['💀', 'Wasted', {fx: [['bw', 0, 2.8, 0.8], ['red', 0, 2.8, 0.35], ['zoom', 0, 2.8, 0.25]], sfx: [['boom', 0]], text: ['WASTED', '#c8102e', 2.8]}],
    ['🌪', 'Whoosh', {fx: [['zoom', 0, 0.35, 0.45]], sfx: [['whoosh', 0]]}],
    ['📀', 'Record stop', {fx: [['bw', 0, 1.2, 0.6]], sfx: [['scratch', 0]], text: ['WAIT...', '#ffffff', 1.2]}],
  ];
  const fxArr = () => (ED.scene.fx = ED.scene.fx || []);
  const sfxArr = () => (ED.scene.sfx = ED.scene.sfx || []);
  const sndLabel = n => n.startsWith('file:voice_') ? '🗣 ' + n.slice(11).replace(/_[0-9a-f]{10}\.mp3$/, '').replace(/_/g, ' ') : n.startsWith('file:') ? '🎵 ' + n.slice(5).replace(/\.[^.]+$/, '') : (CFG.sfx || {})[n] || n;
  const sndUrl = n => n.startsWith('file:') ? '/sounds/' + encodeURIComponent(n.slice(5)) : `/sfx/${n}.wav`;
  function playSnd(n, vol = 0.9) { try { const a = new Audio(sndUrl(n)); a.volume = clamp(vol, 0, 1); a.play().catch(() => {}); } catch (e) { /* ignore */ } }
  const addFx = (type, start, dur, amt = 0.7, color = '#ffffff') => fxArr().push({id: uid(), type, start: +start.toFixed(2), dur, amt, color});
  const addSfx = (sound, start, vol = 0.9) => sfxArr().push({id: uid(), sound, start: +start.toFixed(2), vol});
  function addCombo(c) {
    const t0 = Math.max(0, tNow());
    (c.fx || []).forEach(([ty, o, d, a, col]) => addFx(ty, t0 + o, d, a, col));
    (c.sfx || []).forEach(([n, o]) => addSfx(n, t0 + o));
    if (c.text) L().push({...bigText(c.text[0], c.text[1]), id: uid(), start: +t0.toFixed(1), end: +(t0 + c.text[2]).toFixed(1)});
    if (c.text2) L().push({...bigText(c.text2[0], c.text2[1], {y: 980, size: 90}), id: uid(), start: +t0.toFixed(1), end: +(t0 + c.text2[2]).toFixed(1)});
    (c.stk || []).forEach(([k, d, at]) => L().push(stickerLayer(k, t0, d, at)));
    syncAll(); commit(); playSnd((c.sfx || [[]])[0][0] || 'ding');
  }
  function drawFxUI() {
    $('#edCombos').innerHTML = COMBOS.map((c, i) => `<button data-i="${i}"><span>${c[0]}</span>${esc(c[1])}</button>`).join('');
    $$('#edCombos button').forEach(b => b.onclick = () => { addCombo(COMBOS[+b.dataset.i][2]); toast(`${COMBOS[+b.dataset.i][1]} added at ${fmt(Math.max(0, tNow()))}`); });
    $('#edStickers').innerHTML = Object.entries(STICKERS).map(([k, v]) => `<button data-k="${k}">${v[0]} ${esc(v[1])}</button>`).join('');
    $$('#edStickers button').forEach(b => b.onclick = act(() => { const l = stickerLayer(b.dataset.k, Math.max(0, tNow())); L().push(l); ED.sel = l.id; }));
    $('#edFxBtns').innerHTML = Object.entries(FXN).map(([k, v]) => `<button data-k="${k}">${v[0]} ${v[1]}</button>`).join('');
    $$('#edFxBtns button').forEach(b => b.onclick = act(() => { const k = b.dataset.k; addFx(k, Math.max(0, tNow()), FXN[k][2], 0.7, k === 'flash' ? '#ffffff' : '#ffffff'); }));
    $('#edSfxBtns').innerHTML = Object.entries(CFG.sfx || {}).map(([k, v]) => `<button data-k="${k}" title="Tap to add at the playhead, long-press or right-click to listen"><svg class="i" aria-hidden="true"><use href="#i-music"/></svg>${esc(v)}</button>`).join('');
    $$('#edSfxBtns button').forEach(b => { b.onclick = act(() => { addSfx(b.dataset.k, Math.max(0, tNow())); playSnd(b.dataset.k); }); b.oncontextmenu = e => { e.preventDefault(); playSnd(b.dataset.k); }; });
    drawMySounds(); voiceSetup();
  }
  function drawMySounds() {
    const list = CFG.sounds || [];
    $('#edMySnds').innerHTML = list.map(f => `<button data-f="${esc(f)}"><svg class="i" aria-hidden="true"><use href="#i-music"/></svg>${esc(f.replace(/\.[^.]+$/, '').slice(0, 24))}</button>`).join('');
    $$('#edMySnds button').forEach(b => { b.onclick = act(() => { addSfx('file:' + b.dataset.f, Math.max(0, tNow())); playSnd('file:' + b.dataset.f); }); b.oncontextmenu = e => { e.preventDefault(); playSnd('file:' + b.dataset.f); }; });
  }
  /* ---- Voice: an English voice reads a line (made on the PC, added like a sound effect) */
  const VO = {voices: null, last: null};
  async function voiceSetup() {
    if (VO.voices) return;
    const j = await fetch('/api/tts').then(r => r.json()).catch(() => ({}));
    if (!j.ok) return;
    VO.voices = j.voices;
    const keep = localStorage.getItem('chipVoice') || '';
    $('#edVoVoice').innerHTML = j.voices.map(v => `<option value="${v.id}" ${v.id === keep ? 'selected' : ''}>${esc(v.name)} (${v.gender === 'f' ? 'female' : 'male'}, ${v.accent})</option>`).join('');
    if (!j.natural) $('#edVoNote').textContent = j.installing ? 'Getting the natural voices ready on the PC. Until then the Windows voice is used.' : 'Natural voices need the edge-tts package on the PC. Until then the Windows voice is used.';
  }
  async function voiceMake() {
    const text = $('#edVoText').value.trim();
    if (!text) { toast('Type what the voice should say', true); return null; }
    const body = {text, voice: $('#edVoVoice').value, rate: +$('#edVoRate').value};
    const key = JSON.stringify(body);
    if (VO.last && VO.last.key === key) return VO.last;
    try { localStorage.setItem('chipVoice', body.voice); } catch (e) { /* ignore */ }
    toast('Making the voice…');
    const j = await fetch('/api/tts', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: key}).then(r => r.json()).catch(() => ({}));
    if (!j.ok) { toast(j.error || 'Could not make the voice', true); return null; }
    if (j.sounds) { CFG.sounds = j.sounds; drawMySounds(); }
    return (VO.last = {key, file: j.file, secs: j.secs || 2, by: j.by});
  }
  $('#edVoHook').onclick = () => { const h = $('#edHook').value.trim(); if (!h) return toast('This clip has no hook yet', true); $('#edVoText').value = h; };
  $('#edVoListen').onclick = async () => { const v = await voiceMake(); if (v) playSnd('file:' + v.file, 1); };
  $('#edVoAdd').onclick = async () => {
    const v = await voiceMake(); if (!v) return;
    const f = {id: uid(), sound: 'file:' + v.file, start: +Math.max(0, tNow()).toFixed(2), vol: 1};
    if ($('#edVoDuck').checked) Object.assign(f, {duck: true, len: v.secs});
    sfxArr().push(f); syncAll(); commit();
    toast(`Voice added at ${fmt(f.start)} (${v.by === 'Windows voice' ? 'Windows voice' : v.by || 'voice'})`);
  };
  $('#edSndFile').onchange = async e => {
    const f = e.target.files[0]; e.target.value = ''; if (!f) return;
    const fd = new FormData(); fd.append('file', f);
    const j = await fetch('/api/sounds', {method: 'POST', body: fd}).then(r => r.json()).catch(() => ({}));
    if (!j.ok) return toast(j.error || 'Upload failed', true);
    CFG.sounds = j.sounds; drawMySounds(); toast('Sound added. Tap it to put it on the clip');
  };
  function renderFxList() {
    const items = [...fxArr().map(f => ({...f, k: 'fx'})), ...sfxArr().map(f => ({...f, k: 'sfx'}))].sort((a, b) => a.start - b.start);
    $('#edFxList').innerHTML = items.length ? items.map(f => `<div class="fxrow" data-id="${f.id}" data-k="${f.k}">
        <b>${f.k === 'fx' ? FXN[f.type][0] + ' ' + FXN[f.type][1] : '🔊 ' + esc(sndLabel(f.sound))}</b>
        <span class="mut">at</span><input type="number" step="0.1" min="0" data-f="start" value="${(+f.start).toFixed(1)}"><button class="btn sm" data-a="now">now</button>
        ${f.k === 'fx' ? `<span class="mut">for</span><input type="number" step="0.1" min="0.05" data-f="dur" value="${(+f.dur).toFixed(2)}"><span class="mut">strength</span><input type="range" min="0.1" max="1" step="0.05" data-f="amt" value="${f.amt}">${f.type === 'flash' ? `<input type="color" data-f="color" value="${f.color || '#ffffff'}">` : ''}`
          : `<span class="mut">volume</span><input type="range" min="0" max="2" step="0.05" data-f="vol" value="${f.vol}"><button class="btn sm" data-a="play"><svg class="i" aria-hidden="true"><use href="#i-play"/></svg></button>`}
        <button class="btn sm" data-a="go" title="Jump here">⤴</button><button class="btn sm" data-a="del"><svg class="i" aria-hidden="true"><use href="#i-trash"/></svg></button></div>`).join('')
      : '<div class="mut">Nothing yet. Move the playhead to the funny moment and tap a combo.</div>';
    $$('#edFxList .fxrow').forEach(row => {
      const arr = row.dataset.k === 'fx' ? fxArr() : sfxArr(), f = arr.find(x => x.id === row.dataset.id);
      row.querySelectorAll('[data-f]').forEach(inp => inp.addEventListener('input', () => { f[inp.dataset.f] = inp.type === 'color' ? inp.value : +inp.value; drawFxMarks(); }));
      row.querySelectorAll('[data-a]').forEach(b => b.onclick = () => {
        const a = b.dataset.a;
        if (a === 'del') { arr.splice(arr.indexOf(f), 1); renderFxList(); drawFxMarks(); commit(); }
        else if (a === 'now') { f.start = +Math.max(0, tNow()).toFixed(2); renderFxList(); drawFxMarks(); commit(); }
        else if (a === 'go') V.currentTime = ED.start + Math.max(0, f.start - 0.5);
        else if (a === 'play') playSnd(f.sound, f.vol);
      });
    });
    drawFxMarks();
  }
  function drawFxMarks() {
    $$('#edTimeline .tlfx').forEach(e => e.remove());
    if (!ED.dur) return;
    [...fxArr().map(f => [f.start, '#ff3d6e']), ...sfxArr().map(f => [f.start, '#ffd93d'])].forEach(([t, c]) => {
      const d = document.createElement('div'); d.className = 'tlfx'; d.style.background = c; d.style.left = (100 * (ED.start + t) / ED.dur) + '%'; $('#edTimeline').appendChild(d);
    });
  }
  // preview: sounds play in the browser while the clip plays
  const played = new Set(); let lastT = 0;
  function previewSounds(t) {
    if (t < lastT - 0.05) played.clear();
    lastT = t;
    if (V.paused) return;
    for (const f of sfxArr()) if (!played.has(f.id) && t >= f.start && t < f.start + 0.25) { played.add(f.id); playSnd(f.sound, f.vol); }
  }
  V.addEventListener('seeking', () => played.clear());
  // preview: visual effects drawn over the finished frame
  const FXC = document.createElement('canvas'); FXC.width = PH.width; FXC.height = PH.height; const FX2 = FXC.getContext('2d');
  function applyFx(t) {
    const act = fxArr().filter(f => t >= f.start && t <= f.start + f.dur); if (!act.length) return;
    const W2 = PH.width, H2 = PH.height;
    const redraw = (fn) => { FX2.clearRect(0, 0, W2, H2); FX2.drawImage(PH, 0, 0); PX.save(); fn(); PX.restore(); };
    for (const f of act) {
      const a = f.amt, p = (t - f.start) / f.dur;
      if (f.type === 'zoom') redraw(() => { const z = 1 + 0.35 * a; PX.drawImage(FXC, -(W2 * z - W2) / 2, -(H2 * z - H2) / 2, W2 * z, H2 * z); });
      else if (f.type === 'shake') redraw(() => { const m = 26 * a * K, z = 1 + 2 * m / W2; PX.drawImage(FXC, -(W2 * z - W2) / 2 + m * Math.sin(t * 61), -(H2 * z - H2) / 2 + m * Math.cos(t * 53), W2 * z, H2 * z); });
      else if (f.type === 'flash') { PX.fillStyle = f.color || '#fff'; PX.globalAlpha = Math.min(1, 0.4 + 0.6 * a) * (1 - p); PX.fillRect(0, 0, W2, H2); PX.globalAlpha = 1; }
      else if (f.type === 'bw') redraw(() => { PX.filter = `grayscale(1) contrast(${1 + 0.3 * a})`; PX.drawImage(FXC, 0, 0); });
      else if (f.type === 'blue') { PX.save(); PX.globalCompositeOperation = 'multiply'; PX.fillStyle = `rgb(${Math.round(255 * (1 - 0.35 * a))},${Math.round(255 * (1 - 0.15 * a))},255)`; PX.fillRect(0, 0, W2, H2); PX.restore(); }
      else if (f.type === 'dark') { PX.save(); const g = PX.createRadialGradient(W2 / 2, H2 / 2, H2 * 0.18, W2 / 2, H2 / 2, H2 * 0.62); g.addColorStop(0, `rgba(0,0,0,${0.12 * a})`); g.addColorStop(1, `rgba(0,0,0,${0.55 + 0.4 * a})`); PX.fillStyle = g; PX.fillRect(0, 0, W2, H2); PX.restore(); }
      else if (f.type === 'fry') redraw(() => { PX.filter = `saturate(${1 + 2 * a}) contrast(${1 + 0.6 * a})`; PX.drawImage(FXC, 0, 0); });
      else if (f.type === 'red') { PX.save(); PX.globalCompositeOperation = 'multiply'; PX.fillStyle = `rgb(255,${Math.round(255 * (1 - 0.5 * a))},${Math.round(255 * (1 - 0.5 * a))})`; PX.fillRect(0, 0, W2, H2);
        PX.globalCompositeOperation = 'source-over'; const g = PX.createRadialGradient(W2 / 2, H2 / 2, H2 * 0.25, W2 / 2, H2 / 2, H2 * 0.7); g.addColorStop(0, 'rgba(0,0,0,0)'); g.addColorStop(1, 'rgba(0,0,0,0.75)'); PX.fillStyle = g; PX.fillRect(0, 0, W2, H2); PX.restore(); }
      else if (f.type === 'glitch') redraw(() => { const d = 18 * a * K; PX.globalCompositeOperation = 'lighter'; PX.globalAlpha = 0.5;
        PX.filter = 'sepia(1) saturate(8) hue-rotate(-50deg)'; PX.drawImage(FXC, -d, 0); PX.filter = 'sepia(1) saturate(8) hue-rotate(160deg)'; PX.drawImage(FXC, d, 0); });
    }
  }

  /* ================================================================ layers panel */
  const ICON = {video: '🎥', image: '🖼', text: '🔤', captions: '💬', hook: '🪝', third: '🏷'};
  const lname = l => l.kind === 'text' ? (l.name === 'Watermark' ? 'Watermark: ' : '') + l.text : l.kind === 'third' ? `${l.name}: ${l.title}` : l.kind === 'video' ? `${l.name} (${l.shape})` : l.name || l.kind;
  const EYE = h => `<svg class="i" aria-hidden="true"><use href="#i-${h ? 'eye-off' : 'eye'}"/></svg>`;
  function syncDel() { const b = $('#edLyrDel'); if (b) b.disabled = !selL(); }
  $('#edLyrDel').onclick = () => { const l = selL(); if (!l) return; removeLayer(l); renderLayers(); syncDel(); if (BOX.classList.contains('objmode')) leaveObj(); };
  function renderLayers() {   // the strip beside the preview: one box per layer, front at the top, drag to reorder
    const S = $('#edLyrStrip'); if (!S || !ED.scene) return;
    const items = [...order()].reverse();
    S.innerHTML = items.map(l => `<div class="lyt ${l.id === ED.sel ? 'sel' : ''} ${l.hidden ? 'off' : ''}" data-id="${l.id}" title="${esc(lname(l))}">
      <canvas width="120" height="64"></canvas><span>${esc(lname(l))}</span><button class="eye" data-a="hide" aria-label="Show or hide">${EYE(l.hidden)}</button></div>`).join('');
    ED.thumbT = 0; syncDel();
  }
  function paintLayerThumbs() {
    const S = $('#edLyrStrip'); if (!S || !S.offsetParent) return;
    $$('#edLyrStrip .lyt').forEach(el => {
      const l = L().find(x => x.id === el.dataset.id), c = el.querySelector('canvas'); if (!l || !c) return;
      const x = c.getContext('2d'), CW = c.width, CH = c.height; x.clearRect(0, 0, CW, CH);
      let [bx, by, bw, bh] = bounds(l); bx = clamp(bx, 0, W); by = clamp(by, 0, H); bw = Math.max(8, Math.min(bw, W - bx)); bh = Math.max(8, Math.min(bh, H - by));
      const s = Math.min(CW / bw, CH / bh), dw = bw * s, dh = bh * s;
      try { x.drawImage(PH, bx * K, by * K, bw * K, bh * K, (CW - dw) / 2, (CH - dh) / 2, dw, dh); } catch (e) { /* frame not ready */ }
    });
  }
  (() => {   // drag a box up or down to change what sits in front
    const S = $('#edLyrStrip'); let D = null;
    S.addEventListener('pointerdown', e => {
      const el = e.target.closest('.lyt'); if (!el) return;
      if (e.target.closest('[data-a="hide"]')) { const l = L().find(x => x.id === el.dataset.id); l.hidden = !l.hidden; syncAll(); commit(); renderLayers(); return; }
      D = {el, id: el.dataset.id, y0: e.clientY, on: false, pinned: ['hook', 'captions'].includes(L().find(x => x.id === el.dataset.id)?.kind)}; try { el.setPointerCapture(e.pointerId); } catch (err) { /* ignore */ }
    });
    S.addEventListener('pointermove', e => {
      if (!D) return; const dy = e.clientY - D.y0;
      if (!D.on && Math.abs(dy) < 6) return;
      if (D.pinned) return;   // hook and captions always stay on top
      D.on = true; D.el.classList.add('drag'); D.el.style.transform = `translateY(${dy}px)`;
      const sib = [...S.querySelectorAll('.lyt')].filter(x => x !== D.el); sib.forEach(x => x.classList.remove('drop-b', 'drop-a'));
      D.to = null;
      for (const x of sib) { const r = x.getBoundingClientRect(); if (e.clientY < r.top + r.height / 2) { x.classList.add('drop-b'); D.to = [x.dataset.id, 'b']; break; } }
      if (!D.to && sib.length) { const x = sib[sib.length - 1]; x.classList.add('drop-a'); D.to = [x.dataset.id, 'a']; }
      const r = S.getBoundingClientRect(); if (e.clientY < r.top + 30) S.scrollTop -= 8; else if (e.clientY > r.bottom - 30) S.scrollTop += 8;
    });
    const end = () => {
      if (!D) return; const d = D; D = null;
      const l = L().find(x => x.id === d.id);
      if (!d.on) { ED.sel = l.id; ED.tapSel = true; syncProps(); renderLayers(); if (l.kind === 'text' || l.kind === 'image' || l.kind === 'third') V.currentTime = ED.start + (l.start || 0) + 0.2; return; }
      if (d.to) {
        const pinned = L().filter(x => x.kind === 'hook' || x.kind === 'captions');
        const front = [...order()].reverse().filter(x => x !== l && !pinned.includes(x)), i = front.findIndex(x => x.id === d.to[0]);
        front.splice(d.to[1] === 'b' ? i : i + 1, 0, l);
        ED.scene.layers = [...front.reverse(), ...pinned]; syncAll(); commit();
      }
      renderLayers();
    };
    S.addEventListener('pointerup', end); S.addEventListener('pointercancel', end);
  })();

  /* ================================================================ selected-item properties */
  const row = (label, html) => `<span class="mut">${label}</span><div class="row">${html}</div>`;
  const timeRow = (l, endKey = 'end') => row('Shows', `<input data-k="start" type="number" step="0.1" min="0" value="${(+l.start || 0).toFixed(1)}"><button class="btn sm" data-now="start">now</button>
     <span class="mut">to</span><input data-k="${endKey}" type="number" step="0.1" min="0" value="${(+l[endKey] || 0).toFixed(1)}"><button class="btn sm" data-now="${endKey}">now</button>
     <span class="mut">${endKey === 'dur' ? 's long (0 = whole clip)' : '(0 = to the end)'}</span>`);
  function syncProps() {
    syncDel();
    sheetForSelection();
    const l = selL(), P = $('#edProps');
    $('#edThUpd').style.display = l && l.kind === 'third' ? '' : 'none';
    if (!l) { P.innerHTML = ''; return; }
    let h = `<h4>${ICON[l.kind]} ${esc(lname(l)).slice(0, 40)}<span class="sp2"><button class="btn sm" data-a="center" title="Centre horizontally">⇔</button>
      <button class="btn sm" data-a="dup" title="Duplicate (Ctrl+D)">⧉</button><button class="btn sm" data-a="del" title="Delete"><svg class="i" aria-hidden="true"><use href="#i-trash"/></svg></button><button class="btn sm" data-a="x" title="Deselect">✕</button></span></h4><div class="pgrid">`;
    if (l.kind === 'video') {
      h += `</div><div class="shapes">${SHAPES.map(([k, t]) => `<button data-shape="${k}" title="${t}" class="${l.shape === k ? 'on' : ''}">${shapeSvg(k)}</button>`).join('')}</div><div class="pgrid">`;
      if (l.shape === 'round') h += row('Corners', `<input type="range" data-k="radius" min="0.02" max="0.5" step="0.01" value="${l.radius}">`);
      h += row('Border', `<input type="range" data-k="border" min="0" max="40" step="1" value="${l.border}" style="width:140px"><input type="color" data-k="bcolor" value="${l.bcolor}">`);
      h += row('Size', `<button class="btn sm" data-a="full">Fill screen</button><button class="btn sm" data-a="square">1:1</button><button class="btn sm" data-a="wide">16:9</button><button class="btn sm" data-a="tall">9:16</button>`);
      h += `</div><div class="sect">What this window shows - drag the box on the stream, drag its corner or scroll to zoom</div><canvas id="edSrc"></canvas>
        <div class="row" style="margin-top:6px"><button class="btn sm" data-a="srcfull">Whole stream</button><select id="edTplPick">${Object.keys(CFG.templates).map(n => `<option>${esc(n)}</option>`).join('')}</select>
        <button class="btn sm" data-a="tplcrop">Use template's ${l.role === 'cam' ? 'facecam' : 'gameplay'} crop</button></div>
        <div class="sect"><svg class="i" aria-hidden="true"><use href="#i-target"/></svg>Follow the action - move the crop during the clip</div>
        <div class="row"><button class="btn sm pri" data-a="kfadd">◆ Keyframe here</button><button class="btn sm" data-a="kfprev" title="Previous keyframe">◀ ◆</button>
          <button class="btn sm" data-a="kfnext" title="Next keyframe">◆ ▶</button>
          <select id="edKfMode" title="How the crop gets from one keyframe to the next"><option value="smooth" ${l.kmode !== 'cut' ? 'selected' : ''}>Smooth slide</option><option value="cut" ${l.kmode === 'cut' ? 'selected' : ''}>Jump (camera cut)</option></select>
          ${l.keys && l.keys.length ? '<button class="btn sm" data-a="kfclear">Clear all</button>' : ''}</div>
        <div class="kfl">${(l.keys || []).map((k, i) => `<span class="kfc${Math.abs(k[0] - V.currentTime) < 0.15 ? ' on' : ''}" data-i="${i}">◆ ${(k[0] - ED.start).toFixed(1)} s<b data-del="${i}" title="Delete">✕</b></span>`).join('')}</div>
        <div class="hint">${l.keys && l.keys.length ? 'Go to another moment and drag the box above - a keyframe is added there automatically. The crop slides between keyframes.'
          : 'Play to a moment, press ◆ Keyframe here, then go to another moment and drag the box above to where the action is. The crop moves smoothly between them.'}</div><div class="pgrid">`;
    } else if (l.kind === 'image') {
      h += row('Size', `<input type="range" data-k="w" min="40" max="1080" step="2" value="${l.w}">`);
      h += row('Opacity', `<input type="range" data-k="opacity" min="0.05" max="1" step="0.05" value="${l.opacity}">`);
      h += timeRow(l);
    } else if (l.kind === 'text') {
      h += row('Text', `<textarea data-k="text" data-focus rows="2" style="flex:1;min-height:52px" placeholder="Enter = new line">${esc(l.text)}</textarea>`);
      h += row('Font', `<select data-k="font">${FONTS.map(f => `<option ${f === l.font ? 'selected' : ''}>${f}</option>`).join('')}</select><input type="number" data-k="size" min="14" max="300" value="${Math.round(l.size)}" style="width:70px"><input type="color" data-k="color" value="${l.color}">`);
      h += row('Box', `<label><input type="checkbox" data-k="box" ${l.box ? 'checked' : ''}> background box</label><input type="color" data-k="bcolor" value="${l.bcolor}"><span class="mut">outline</span><input type="color" data-k="stroke" value="${l.stroke || '#000000'}">`);
      h += row('Animation', `<select data-k="anim">${Object.entries(TEXT_ANIMS).map(([k, v]) => `<option value="${k}" ${(l.anim || 'none') === k ? 'selected' : ''}>${v}</option>`).join('')}</select>`);
      h += row('Opacity', `<input type="range" data-k="opacity" min="0.1" max="1" step="0.05" value="${l.opacity}">`);
      h += timeRow(l);
    } else if (l.kind === 'captions') {
      h += row('Size', `<input type="range" data-k="scale" min="0.5" max="2.5" step="0.05" value="${l.scale}">`);
      h += row('', `<button class="btn sm" data-a="captab">Style, colours and words →</button>`);
    } else if (l.kind === 'hook') {
      h += row('Text', `<textarea data-hook data-focus rows="2" style="flex:1;min-height:52px" placeholder="Enter = new line">${esc($('#edHook').value)}</textarea>`);
      h += row('Size', `<input type="range" data-k="scale" min="0.4" max="2.5" step="0.05" value="${l.scale}">`);
      h += row('Shows for', `<input type="number" data-k="secs" min="0.5" max="15" step="0.5" value="${l.secs}"><span class="mut">s at the start</span>`);
    } else if (l.kind === 'third') {
      h += row('Name', `<input data-third="title" data-focus value="${esc(l.title)}" style="flex:1">`);
      h += row('Second line', `<input data-third="sub" value="${esc(l.sub)}" style="flex:1"><input type="color" data-third="accent" value="${l.accent}">`);
      h += row('Size', `<input type="range" data-k="scale" min="0.3" max="2.5" step="0.05" value="${l.scale}">`);
      h += timeRow(l, 'dur');
      h += row('Animation', `<select data-k="anim">${[['slide', 'Slide in'], ['rise', 'Rise up'], ['fade', 'Fade'], ['none', 'None']].map(([v, t]) => `<option value="${v}" ${l.anim === v ? 'selected' : ''}>${t}</option>`).join('')}</select>`);
    }
    h += '</div>';
    P.innerHTML = h;
    $$('#edProps [data-k]').forEach(inp => {
      const k = inp.dataset.k;
      inp.addEventListener(inp.type === 'checkbox' || inp.tagName === 'SELECT' ? 'change' : 'input', () => {
        const v = inp.type === 'checkbox' ? inp.checked : (inp.type === 'range' || inp.type === 'number') ? +inp.value : inp.value;
        if (k === 'text' && !String(v).trim()) return;
        l[k] = v; if (l.kind === 'video') fitCrop(l);
        if (k === 'start' || k === 'end' || k === 'dur') syncLayBar();
      });
    });
    $$('#edProps [data-now]').forEach(b => b.onclick = act(() => {
      const t = +tNow().toFixed(1), k = b.dataset.now;
      if (k === 'dur') l.dur = Math.max(0.5, +(t - l.start).toFixed(1)); else l[k] = Math.max(0, t);
      if (k === 'start' && l.end && l.end <= l.start) l.end = +(l.start + 3).toFixed(1);
    }));
    $$('#edProps [data-shape]').forEach(b => b.onclick = act(() => {
      const s = b.dataset.shape; l.shape = s;
      if ((s === 'circle' || s === 'heart' || s === 'star' || s === 'hexagon' || s === 'squircle') && Math.abs(l.w - l.h) > 40 && l.w >= 1000) {
        const d = Math.min(l.w, l.h, 600); l.x += (l.w - d) / 2; l.y += (l.h - d) / 2; l.w = d; l.h = d; fitCrop(l);
      }
    }));
    const hk = $('#edProps [data-hook]'); if (hk) hk.oninput = () => { $('#edHook').value = hk.value; };
    $$('#edProps [data-third]').forEach(inp => inp.addEventListener('input', () => {
      l[inp.dataset.third] = inp.value; if (!l.title.trim()) return;
      l.prims = buildThird(l.template || 'tiktok', l.title, l.sub, l.accent, l.platform || 'youtube');
    }));
    if ($('#edKfMode')) $('#edKfMode').onchange = act(() => { l.kmode = $('#edKfMode').value; });
    $$('#edProps .kfc').forEach(c => c.onclick = e => {
      const i = +c.dataset.i;
      if (e.target.dataset.del !== undefined) { act(() => { l.crop = [...cropAt(l, V.currentTime)]; l.keys.splice(i, 1); })(); return; }
      V.currentTime = l.keys[i][0];
    });
    $$('#edProps [data-a]').forEach(b => b.onclick = () => {
      const a = b.dataset.a;
      if (a === 'del') return removeLayer(l);
      if (a === 'dup') return dupLayer(l);
      if (a === 'x') { ED.sel = null; return syncProps(); }
      if (a === 'captab') return edTab('caps');
      act(() => {
        if (a === 'center') { const [bx, , bw] = bounds(l); l.x += 540 - (bx + bw / 2); }
        else if (a === 'full') { l.x = 0; l.y = 0; l.w = 1080; l.h = 1920; l.shape = 'rect'; l.border = 0; }
        else if (a === 'square' || a === 'wide' || a === 'tall') { const r = {square: 1, wide: 16 / 9, tall: 9 / 16}[a], cx = l.x + l.w / 2, cy = l.y + l.h / 2;
          l.w = Math.min(1080, a === 'tall' ? 600 : 1000); l.h = l.w / r; l.x = cx - l.w / 2; l.y = cy - l.h / 2; }
        else if (a === 'srcfull') { l.crop = [0, 0, 1, 1]; l.keys = []; }
        else if (a === 'kfadd') { if (l.keys && l.keys.length) l.crop = [...cropAt(l, V.currentTime)]; setKey(l, V.currentTime); }
        else if (a === 'kfclear') { l.crop = [...cropAt(l, V.currentTime)]; l.keys = []; }
        else if (a === 'kfprev' || a === 'kfnext') { const K = l.keys || [], t = V.currentTime;
          const k = a === 'kfprev' ? [...K].reverse().find(q => q[0] < t - 0.05) : K.find(q => q[0] > t + 0.05); if (k) V.currentTime = k[0]; }
        else if (a === 'tplcrop') { const t = CFG.templates[$('#edTplPick').value]; if (t) l.crop = [...(t[l.role === 'cam' ? 'cam' : 'game'] || [0, 0, 1, 1])]; }
        if (l.kind === 'video') fitCrop(l);
      })();
    });
    syncLayBar(); renderKf();
  }
  function syncPropsLive() {   // cheap refresh while dragging: just numbers that changed
    const l = selL(); if (!l) return;
    $$('#edProps [data-k]').forEach(inp => { const k = inp.dataset.k; if (['w', 'scale', 'size'].includes(k) && document.activeElement !== inp) inp.value = l[k]; });
  }
  function syncLayBar() {
    const l = selL(), bar = $('#edLayBar');
    if (!l || !('start' in l) || !ED.dur) { bar.style.display = 'none'; return; }
    const s = ED.start + (+l.start || 0), e = l.kind === 'third' ? (l.dur ? s + l.dur : ED.end) : (l.end ? ED.start + l.end : ED.end);
    bar.style.display = ''; bar.style.left = (100 * s / ED.dur) + '%'; bar.style.width = (100 * Math.max(0, Math.min(e, ED.end) - s) / ED.dur) + '%';
  }
  function syncAll() {
    const sc = ED.scene, c = capL(), hk = hookL(), st = style();
    if (sc.bleep && sc.music) { drawBleep(); drawMusic(); }
    $$('#edBgType button').forEach(b => b.classList.toggle('on', b.dataset.v === sc.bg.type));
    const t = sc.bg.type;
    $$('#editor .bgc').forEach(x => x.style.display = t === 'color' || t === 'gradient' ? '' : 'none');
    $$('#editor .bgg').forEach(x => x.style.display = t === 'gradient' ? '' : 'none');
    $$('#editor .bgi').forEach(x => x.style.display = t === 'image' ? '' : 'none');
    if (t === 'image') drawBgPick();
    $$('#editor .bgb').forEach(x => x.style.display = t === 'blur' ? '' : 'none');
    $$('#editor .bgd').forEach(x => x.style.display = t === 'blur' || t === 'image' ? '' : 'none');
    $('#edBgC1').value = sc.bg.color; $('#edBgC2').value = sc.bg.color2; $('#edBgBlur').value = sc.bg.blur; $('#edBgDim').value = sc.bg.dim;
    $('#edBgName').textContent = sc.bg.image || '';
    $('#edBar').checked = !!sc.progress.on; $('#edBarC').value = sc.progress.color; $('#edBarPos').value = sc.progress.pos;
    $('#edStyle').value = c.style; $('#edCapScale').value = c.scale; $('#edCapFont').value = c.font || ''; $('#edCapWords').value = c.words || 0;
    if (!$('#edCapAnim').options.length) $('#edCapAnim').innerHTML = Object.entries(CFG.anims || {auto: 'Style default'}).map(([k, v]) => `<option value="${k}">${esc(v)}</option>`).join('');
    $('#edCapAnim').value = c.anim || 'auto'; drawHookLooks();
    $('#edCapC').value = st.color?.length === 7 ? st.color : '#ffffff'; $('#edCapHi').value = st.hi?.length === 7 ? st.hi : '#ffff00';
    $('#edHookOn').checked = !hk.hidden; $('#edHookSecs').value = hk.secs;
    if (!THIRDS[ED.third]) ED.third = 'darkpill';
    if (!$('#edThAcc').value || $('#edThAcc').value === '#000000') { $('#edThAcc').value = PLATFORMS[ED.plat].color; $('#edThSub').value = ''; }
    if (!$('#edThStart').value) { $('#edThStart').value = 1; $('#edThDur').value = 4; }
    drawRecent(); syncProps(); renderLayers(); renderFxList();
  }

  /* ================================================================ source crop editor (for the selected video window) */
  let srcDrag = null;
  function srcRect() { const c = $('#edSrc'); return c ? c.getBoundingClientRect() : null; }
  document.addEventListener('pointerdown', e => {
    if (e.target.id !== 'edSrc') return; const l = selL(); if (!l || l.kind !== 'video') return;
    if (l.keys && l.keys.length) l.crop = [...cropAt(l, V.currentTime)];
    const r = srcRect(), px = (e.clientX - r.left) / r.width, py = (e.clientY - r.top) / r.height, [bx, by, bw, bh] = l.crop;
    const corner = px > bx + bw - 0.05 && py > by + bh - 0.08 && px < bx + bw + 0.03 && py < by + bh + 0.05;
    if (!corner && !(px >= bx && px <= bx + bw && py >= by && py <= by + bh)) return;
    srcDrag = {mode: corner ? 'size' : 'move', px, py, o: [...l.crop]}; e.target.setPointerCapture(e.pointerId);
  });
  document.addEventListener('pointermove', e => {
    if (!srcDrag) return; const l = selL(); if (!l) return;
    const r = srcRect(), px = (e.clientX - r.left) / r.width, py = (e.clientY - r.top) / r.height, o = srcDrag.o;
    if (srcDrag.mode === 'move') { l.crop = [clamp(o[0] + px - srcDrag.px, 0, 1 - o[2]), clamp(o[1] + py - srcDrag.py, 0, 1 - o[3]), o[2], o[3]];
      if (l.keys && l.keys.length) setKey(l, V.currentTime); }
    else { l.crop = [o[0], o[1], clamp(o[2] + px - srcDrag.px, 0.04, 1), o[3]]; l.crop[0] = o[0] + o[2] / 2 - l.crop[2] / 2; l.crop[1] = o[1] + o[3] / 2 - l.crop[3] / 2; fitCrop(l); l.crop[0] = clamp(o[0] + o[2] / 2 - l.crop[2] / 2, 0, 1 - l.crop[2]); l.crop[1] = clamp(o[1] + o[3] / 2 - l.crop[3] / 2, 0, 1 - l.crop[3]);
      if (l.keys && l.keys.length) setKey(l, V.currentTime); }
  });
  document.addEventListener('pointerup', () => { if (srcDrag) { srcDrag = null; commit(); const l = selL(); if (l && l.keys && l.keys.length) syncProps(); } });
  document.addEventListener('wheel', e => {
    if (e.target.id !== 'edSrc') return; const l = selL(); if (!l || l.kind !== 'video') return; e.preventDefault();
    if (l.keys && l.keys.length) l.crop = [...cropAt(l, V.currentTime)];
    const c = l.crop, cx = c[0] + c[2] / 2, cy = c[1] + c[3] / 2; c[2] = clamp(c[2] * (e.deltaY < 0 ? 0.94 : 1.06), 0.04, 1); fitCrop(l);
    l.crop[0] = clamp(cx - l.crop[2] / 2, 0, 1 - l.crop[2]); l.crop[1] = clamp(cy - l.crop[3] / 2, 0, 1 - l.crop[3]);
    if (l.keys && l.keys.length) setKey(l, V.currentTime);
    clearTimeout(ED.st); ED.st = setTimeout(commit, 400);
  }, {passive: false});
  function drawSrc() {
    const c = $('#edSrc'), l = selL(); if (!c || !l || l.kind !== 'video') return;
    const Wc = c.width = c.clientWidth * devicePixelRatio, Hc = c.height = c.clientHeight * devicePixelRatio, x = c.getContext('2d');
    if (V.videoWidth) x.drawImage(V, 0, 0, Wc, Hc); x.fillStyle = 'rgba(0,0,0,0.55)'; x.fillRect(0, 0, Wc, Hc);
    const cc = cropAt(l, V.currentTime), [bx, by, bw, bh] = [cc[0] * Wc, cc[1] * Hc, cc[2] * Wc, cc[3] * Hc];
    if (V.videoWidth) x.drawImage(V, cc[0] * srcW(), cc[1] * srcH(), cc[2] * srcW(), cc[3] * srcH(), bx, by, bw, bh);
    if (l.keys && l.keys.length) {                // keyframe path: where the crop centre travels
      x.strokeStyle = 'rgba(255,212,0,.8)'; x.lineWidth = 1.5 * devicePixelRatio; x.beginPath();
      l.keys.forEach((k, i) => { const px = (k[1] + cc[2] / 2) * Wc, py = (k[2] + cc[3] / 2) * Hc; i ? x.lineTo(px, py) : x.moveTo(px, py); }); x.stroke();
      x.fillStyle = '#ffd400'; l.keys.forEach(k => { const px = (k[1] + cc[2] / 2) * Wc, py = (k[2] + cc[3] / 2) * Hc; x.beginPath(); x.moveTo(px, py - 6); x.lineTo(px + 6, py); x.lineTo(px, py + 6); x.lineTo(px - 6, py); x.fill(); });
    }
    x.strokeStyle = l.role === 'cam' ? '#ff3d6e' : '#2ee6c8'; x.lineWidth = 2.5 * devicePixelRatio; x.strokeRect(bx, by, bw, bh);
    x.fillStyle = x.strokeStyle; const hs = 14 * devicePixelRatio; x.fillRect(bx + bw - hs, by + bh - hs, hs, hs);
  }

  /* ================================================================ draw loop */
  function drawBg() {
    const bg = ED.scene.bg;
    if (bg.type === 'color') { PX.fillStyle = bg.color; PX.fillRect(0, 0, PH.width, PH.height); return; }
    if (bg.type === 'gradient') { const g = PX.createLinearGradient(0, 0, 0, PH.height); g.addColorStop(0, bg.color); g.addColorStop(1, bg.color2); PX.fillStyle = g; PX.fillRect(0, 0, PH.width, PH.height); return; }
    let src = null, sw = 0, sh = 0;
    if (bg.type === 'image') { const i = loadImg(bg.image); if (i && i.naturalWidth) { src = i; sw = i.naturalWidth; sh = i.naturalHeight; } }
    else if (V.videoWidth) { src = V; sw = V.videoWidth; sh = V.videoHeight; }
    PX.fillStyle = '#000'; PX.fillRect(0, 0, PH.width, PH.height);
    if (!src) return;
    const s = Math.max(W / sw, H / sh), cw = W / s, ch = H / sh * sh / s;
    if (bg.type === 'blur') {
      const sz = clamp(Math.round(60 / Math.max(1, bg.blur) * 6), 6, 60);
      TINY.width = Math.round(sz * 9 / 16) || 1; TINY.height = sz;
      TX.drawImage(src, (sw - cw) / 2, (sh - ch) / 2, cw, ch, 0, 0, TINY.width, TINY.height);
      PX.imageSmoothingEnabled = true; PX.imageSmoothingQuality = 'high'; PX.drawImage(TINY, 0, 0, PH.width, PH.height);
    } else PX.drawImage(src, (sw - cw) / 2, (sh - ch) / 2, cw, ch, 0, 0, PH.width, PH.height);
    if (bg.dim > 0.01) { PX.fillStyle = `rgba(0,0,0,${bg.dim})`; PX.fillRect(0, 0, PH.width, PH.height); }
  }
  function drawVideo(l) {
    PX.save(); PX.globalAlpha = l.opacity ?? 1;
    drawVideo0(l); PX.restore();
  }
  function drawVideo0(l) {
    if (l.border > 0) {
      const b = l.border, pts = shapePoints(l.shape, l.w + 2 * b, l.h + 2 * b, l.shape === 'round' ? l.radius * Math.min(l.w, l.h) / Math.min(l.w + 2 * b, l.h + 2 * b) : l.radius);
      pathOf(pts, l.x - b, l.y - b); PX.fillStyle = l.bcolor; PX.fill();
    }
    PX.save(); pathOf(shapePoints(l.shape, l.w, l.h, l.radius), l.x, l.y); PX.clip();
    const c = cropAt(l, V.currentTime);
    if (V.videoWidth) PX.drawImage(V, c[0] * srcW(), c[1] * srcH(), c[2] * srcW(), c[3] * srcH(), l.x * K, l.y * K, l.w * K, l.h * K);
    else { PX.fillStyle = '#1b1e25'; PX.fillRect(l.x * K, l.y * K, l.w * K, l.h * K); }
    PX.restore();
  }
  // text animations: same timings as engine.text_events, so the preview matches the render
  const TEXT_ANIMS = {none: 'None', fade: 'Fade in & out', fadein: 'Fade in', fadeout: 'Fade out', typewriter: 'Typewriter', pop: 'Pop', drop: 'Drop in', rise: 'Rise up',
    slide: 'Slide in', zoom: 'Zoom in', spin: 'Spin in', glitch: 'Glitch', bounce: 'Bounce', shake: 'Shake', blink: 'Blink'};
  function textAnim(l) {
    const S = {a: 1, sx: 1, sy: 1, dx: 0, dy: 0, rot: 0, chars: Infinity, glitch: 0}, an = l.anim || 'none';
    if (an === 'none') return S;
    const ms = (tNow() - (l.start || 0)) * 1000, D = Math.max(200, ((l.end || (ED.end - ED.start)) - (l.start || 0)) * 1000), f = (a, b) => clamp((ms - a) / (b - a), 0, 1);
    if (ms < 0) return S;
    if (an === 'fade') S.a = Math.min(f(0, 250), clamp((D - ms) / 250, 0, 1));
    else if (an === 'fadein') S.a = f(0, 350);
    else if (an === 'fadeout') S.a = clamp((D - ms) / 350, 0, 1);
    else if (an === 'pop') S.sx = S.sy = ms < 160 ? 0.3 + 0.88 * f(0, 160) : 1.18 - 0.18 * f(160, 280);
    else if (an === 'drop') { S.dy = -320 * (1 - f(0, 320)); S.a = f(0, 120); const q = ms < 420 ? f(320, 420) : 1 - f(420, 520); S.sy = 1 - 0.12 * q; S.sx = 1 + 0.08 * q; }
    else if (an === 'rise') { S.dy = 220 * (1 - f(0, 350)); S.a = f(0, 250); }
    else if (an === 'slide') S.dx = -900 * (1 - f(0, 330));
    else if (an === 'zoom') { const q = f(0, 320); S.sx = S.sy = 2.6 - 1.6 * q; S.a = q; }
    else if (an === 'spin') { const q = f(0, 380); S.rot = -200 * (1 - q); S.sx = S.sy = 0.15 + 0.85 * q; }
    else if (an === 'bounce' && ms < 12000) { const ph = ms % 700, q = ph < 160 ? ph / 160 : ph < 320 ? 1 - (ph - 160) / 160 : 0; S.sx = 1 + 0.12 * q; S.sy = 1 - 0.1 * q; }
    else if (an === 'shake' && ms < 700) S.rot = Math.floor(ms / 50) % 2 ? 4 : -4;
    else if (an === 'blink' && ms >= 500 && ms < 12000 && (ms - 500) % 900 < 250) S.a = 0;
    else if (an === 'glitch' && ms < 360) { S.sx = ms < 60 ? 1.35 : ms < 120 ? 0.85 : ms < 180 ? 1.15 : 1; S.glitch = 1 - f(0, 360); }
    else if (an === 'typewriter') { const step = Math.max(25, Math.min(70, D * 0.6 / Math.max(1, l.text.length))); S.chars = Math.floor(ms / step) + 1; }
    return S;
  }
  function drawText(l) {
    const A = textAnim(l); if (A.a <= 0) return;
    PX.save(); PX.globalAlpha = (l.opacity ?? 1) * A.a; PX.font = cssFont(l.size, l.font); PX.textAlign = 'center'; PX.textBaseline = 'middle';
    let ls = wrapText(l.text, cssFont(l.size, l.font), TEXT_MAXW(l));
    const x = l.x * K, s = l.size * K, lh = s * 1.2, y0 = l.y * K - lh * (ls.length - 1) / 2;
    if (A.chars < Infinity) { let n = A.chars; ls = ls.map(t => { const c = t.slice(0, Math.max(0, n)); n -= t.length + 1; return c; }); }
    PX.translate(x + A.dx * K, l.y * K + A.dy * K); PX.rotate(A.rot * Math.PI / 180); PX.scale(A.sx, A.sy); PX.translate(-x, -l.y * K);
    PX.font = cssFont(l.size, l.font);
    const ow = l.outline ?? 1, base = PX.globalAlpha;
    if (A.glitch) [[-10, 'rgba(255,0,60,.6)'], [10, 'rgba(0,240,255,.6)']].forEach(([d, c]) => { PX.globalAlpha = base * A.glitch; PX.fillStyle = c; ls.forEach((t, i) => PX.fillText(t, x + d * K, y0 + i * lh)); PX.globalAlpha = base; });
    ls.forEach((t, i) => { if (!t) return; const w = PX.measureText(t).width, y = y0 + i * lh;
      if (l.box) { PX.fillStyle = l.bcolor || '#000'; PX.globalAlpha = base * 0.75; PX.fillRect(x - w / 2 - s * 0.25, y - lh / 2, w + s * 0.5, lh); PX.globalAlpha = base; }
      else {
        if (l.shadow !== false) { PX.save(); PX.fillStyle = 'rgba(0,0,0,.55)'; PX.fillText(t, x + 3 * K, y + 3 * K); PX.restore(); }
        if (ow > 0) { PX.lineJoin = 'round'; PX.strokeStyle = l.stroke || '#000'; PX.lineWidth = Math.max(2, s / 5.5 * ow); PX.strokeText(t, x, y); }
      } });
    PX.fillStyle = l.color; ls.forEach((t, i) => t && PX.fillText(t, x, y0 + i * lh)); PX.restore();
  }
  function drawCaptions(l, ghost) {
    const st = style(), idx = curGroup(), sz = (st.size || 90) * l.scale;
    let toks, cur = -1;
    if (idx) { toks = idx.map(i => st.upper ? ED.words[i][2].toUpperCase() : ED.words[i][2]); cur = idx.findIndex(i => V.currentTime >= ED.words[i][0] && V.currentTime < ED.words[i][1] + 0.05); }
    else if (ghost) toks = ['CAPTIONS', 'GO', 'HERE']; else return;
    PX.save(); if (!idx) PX.globalAlpha = 0.45;
    PX.font = cssFont(sz, st.font); PX.textBaseline = 'middle'; PX.textAlign = 'left';
    const sp = PX.measureText(' ').width, ws = toks.map(w => PX.measureText(w).width), maxW = 1020 * K;
    const lines = [[]]; let lw = 0;                       // wrap like the renderer does (screen width minus margins)
    toks.forEach((w, k) => { const add = (lines[lines.length - 1].length ? sp : 0) + ws[k]; if (lw + add > maxW && lines[lines.length - 1].length) { lines.push([]); lw = 0; } lines[lines.length - 1].push(k); lw += (lines[lines.length - 1].length > 1 ? sp : 0) + ws[k]; });
    const lh = sz * 1.12 * K, y0 = l.y * K - (lines.length - 1) * lh / 2;
    lines.forEach((ln, li) => {
      const tot = ln.reduce((a, k) => a + ws[k], 0) + sp * (ln.length - 1), cy = y0 + li * lh; let cx = l.x * K - tot / 2;
      if (st.box) { PX.fillStyle = typeof st.box === 'string' ? st.box : 'rgba(0,0,0,0.7)'; PX.fillRect(cx - sz * 0.2 * K, cy - sz * 0.62 * K, tot + sz * 0.4 * K, sz * 1.24 * K); }
      ln.forEach(k => {
        const later = idx && cur >= 0 && k > cur && (st.anim === 'build' || st.anim === 'pop');
        if (later) { cx += ws[k] + sp; return; }          // build-up: words appear as they are spoken
        const on = st.mode !== 'plain' && k === cur;
        if (st.glow) { PX.shadowColor = st.glow; PX.shadowBlur = 18 * K * l.scale; }
        if (!st.box) { PX.lineJoin = 'round'; PX.strokeStyle = st.glow || '#000'; PX.lineWidth = (st.outline || 7) * 2 * l.scale * K; PX.strokeText(toks[k], cx, cy); }
        PX.shadowBlur = 0;
        if (!st.hollow || on) { PX.fillStyle = on ? st.hi : st.color; PX.fillText(toks[k], cx, cy); }
        cx += ws[k] + sp;
      });
    });
    PX.restore();
  }
  function hookAccentIdx(words) {
    const stop = new Set(['the', 'and', 'you', 'this', 'that', 'with', 'for', 'was', 'his', 'her', 'are', 'what', 'when', 'just', 'they']);
    let best = -1, bi = 0;
    words.forEach((w, i) => { const k = w.toLowerCase().replace(/[^\p{L}\p{N}_]/gu, ''); const sc = k.length - (stop.has(k) ? 5 : 0) + (w === w.toUpperCase() && /\p{L}/u.test(w) ? 2 : 0); if (sc > best) { best = sc; bi = i; } });
    return bi;
  }
  function drawHookLook(l, txt, ghost) {
    const look = l.look, s = l.scale, acc = l.accent || '#ff3d6e', fs = (look === 'outline' ? 84 * 1.15 : look === 'sticker' ? 84 * 0.82 : 84) * s;
    const lines = txt.split('\n').map(x => x.trim()).filter(Boolean).map(x => x.toUpperCase().split(/\s+/));
    const ai = hookAccentIdx(lines.flat());
    PX.save(); if (ghost) PX.globalAlpha = 0.45;
    PX.font = cssFont(fs); PX.textBaseline = 'middle'; PX.textAlign = 'left'; PX.lineJoin = 'round';
    const lh = fs * 1.12 * K, y0 = l.y * K - lh * (lines.length - 1) / 2, sp = PX.measureText(' ').width;
    if (look === 'banner') { const bh = (lines.length * fs * 1.05 + 46 * s) * K; PX.fillStyle = acc; PX.fillRect(0, l.y * K - bh / 2, 1080 * K, bh); }
    if (look === 'sticker') {
      const wmax = Math.max(...lines.map(ws => PX.measureText(ws.join(' ')).width)), pad = 20 * s * K;
      PX.translate(l.x * K, l.y * K); PX.rotate(-4 * Math.PI / 180); PX.translate(-l.x * K, -l.y * K);
      PX.fillStyle = '#fff'; PX.shadowColor = 'rgba(0,0,0,.4)'; PX.shadowBlur = 10 * K; PX.shadowOffsetY = 6 * K;
      PX.fillRect(l.x * K - wmax / 2 - pad, y0 - lh / 2 - pad * 0.6, wmax + pad * 2, lh * lines.length + pad * 1.2); PX.shadowColor = 'transparent';
    }
    let k = 0;
    lines.forEach((ws, li) => {
      const widths = ws.map(w => PX.measureText(w).width), tot = widths.reduce((a, b) => a + b, 0) + sp * (ws.length - 1), cy = y0 + li * lh;
      let cx = l.x * K - tot / 2;
      ws.forEach((w, j) => {
        const isA = k === ai; k++;
        if (look === 'neon') { PX.shadowColor = isA ? acc : '#2ee5ff'; PX.shadowBlur = 22 * s * K; PX.strokeStyle = isA ? acc : '#2ee5ff'; PX.lineWidth = 4 * s * K; PX.strokeText(w, cx, cy); PX.shadowBlur = 0; }
        else if (look !== 'sticker') { PX.strokeStyle = look === 'outline' && isA ? acc : '#000'; PX.lineWidth = (look === 'outline' ? 24 : look === 'banner' ? 8 : 18) * s * K; PX.strokeText(w, cx, cy); }
        PX.fillStyle = look === 'sticker' ? (isA ? acc : '#000') : look === 'banner' ? (isA ? '#ffff00' : '#fff') : look === 'impact' && isA ? acc : '#fff';
        PX.fillText(w, cx, cy); cx += widths[j] + sp;
      });
    });
    if (look === 'impact') {
      const w = Math.min(860, 330 * s + 26 * s * Math.max(...txt.split('\n').map(x => x.trim().length))) * K, y = (l.y + lines.length * fs * 0.55 + 14 * s) * K;
      PX.fillStyle = acc; PX.fillRect(l.x * K - w / 2, y, w, Math.max(6, 12 * s) * K);
    }
    PX.restore();
  }
  function drawHook(l, ghost) {
    const txt = $('#edHook').value.trim(); if (!txt) return;
    if (l.look && l.look !== 'box') return drawHookLook(l, txt, ghost);
    const sz = 62 * l.scale * K, ls = wrapText(txt, cssFont(62 * l.scale), HOOK_MAXW(l)), lh = sz * 1.2;
    PX.save(); if (ghost) PX.globalAlpha = 0.45;
    PX.font = cssFont(62 * l.scale); PX.textAlign = 'center'; PX.textBaseline = 'middle';
    const pad = 18 * l.scale * K, y0 = l.y * K - lh * (ls.length - 1) / 2;
    PX.fillStyle = '#fff';
    ls.forEach((t, i) => { const w = PX.measureText(t).width; PX.fillRect(l.x * K - w / 2 - pad, y0 + i * lh - lh / 2 - pad * 0.5, w + pad * 2, lh + pad); });
    PX.fillStyle = '#000'; ls.forEach((t, i) => PX.fillText(t, l.x * K, y0 + i * lh)); PX.restore();
  }
  function drawThird(l, t, ghost) {
    let a = 1, dx = 0, dy = 0;
    if (!ghost && l.anim !== 'none') {
      const st0 = l.from_end ? (ED.end - ED.start) - l.from_end : l.start;
      const since = t - st0, left = l.from_end ? 99 : l.dur ? l.start + l.dur - t : 99;
      a = clamp(Math.min(since / 0.25, left / 0.25), 0, 1);
      const mv = THIRD_MOVES[l.anim]; if (mv) { const p = clamp(since / mv[2], 0, 1); dx = mv[0] * (1 - p); dy = mv[1] * (1 - p); }
      if (l.anim === 'wipe') {   // reveal left to right
        a = clamp(left / 0.25, 0, 1); const b = primsBox(l.prims), p = clamp(since / 0.45, 0, 1);
        PX.save(); PX.beginPath(); PX.rect((l.x + (b[0] - 20) * l.scale) * K, 0, (b[2] + 60) * l.scale * p * K, PH.height); PX.clip();
        drawPrims(l.prims, l.x, l.y, l.scale, a); PX.restore(); return;
      }
    }
    drawPrims(l.prims, l.x + dx, l.y + dy, l.scale, ghost ? 0.45 : a);
  }
  function drawSelection() {
    const l = selL(); if (!l) return;
    const [bx, by, bw, bh] = bounds(l), z = ED.view.z;
    PX.save(); PX.strokeStyle = '#2ee6c8'; PX.lineWidth = 2.5 / z; PX.setLineDash([8 / z, 5 / z]); PX.strokeRect(bx * K, by * K, bw * K, bh * K); PX.setLineDash([]);
    const hs = handleSize() * K * 0.62; PX.fillStyle = '#2ee6c8'; PX.strokeStyle = '#000'; PX.lineWidth = 2 / z;
    PX.beginPath(); PX.arc((bx + bw) * K, (by + bh) * K, hs / 2, 0, Math.PI * 2); PX.fill(); PX.stroke(); PX.restore();
  }
  function drawSafe() {
    PX.save(); PX.fillStyle = 'rgba(255,61,110,0.22)';
    PX.fillRect(0, 0, PH.width, 150 * K); PX.fillRect(920 * K, 760 * K, 160 * K, 760 * K); PX.fillRect(0, 1540 * K, PH.width, 380 * K);
    PX.fillStyle = '#fff'; PX.font = `700 ${22 * K * 1.4}px Segoe UI, sans-serif`; PX.textAlign = 'center';
    PX.fillText('app top bar', PH.width / 2, 90 * K); PX.fillText('caption + username area', PH.width / 2, 1740 * K);
    PX.save(); PX.translate(1000 * K, 1140 * K); PX.rotate(-Math.PI / 2); PX.fillText('like / comment buttons', 0, 0); PX.restore(); PX.restore();
  }
  function loop() {
    ED.raf = requestAnimationFrame(loop);
    if (!$('#editor').classList.contains('on') || !ED.scene) return;
    if (!V.paused && V.currentTime >= ED.end) V.currentTime = ED.start;
    if (!V.paused && ED.cuts) { const c = ED.cuts.find(c => V.currentTime >= c[0] && V.currentTime < c[1] - 0.02); if (c) V.currentTime = c[1]; }
    const t = tNow(), inSel = V.currentTime >= ED.start - 0.01 && V.currentTime <= ED.end + 0.01;
    drawBg();
    for (const l of L()) { if (l.hidden) continue; if (l.kind === 'video') drawVideo(l); else if (l.kind === 'image' && visible(l, t)) { const i = loadImg(l.file); if (i?.naturalWidth) drawImageLayer(l, i); } }
    if (inSel) applyFx(t);           // effects hit the video, not the text (same as the render)
    for (const l of L()) {
      if (l.hidden) continue; const vis = visible(l, t) && inSel, ghost = !vis && l.id === ED.sel;
      if (l.kind === 'text' && (vis || ghost)) { if (ghost) PX.globalAlpha = 0.45; drawText(l); PX.globalAlpha = 1; }
      else if (l.kind === 'captions' && inSel) drawCaptions(l, l.id === ED.sel || $('#editor .edp[data-t="caps"]').classList.contains('on'));
      else if (l.kind === 'hook' && (vis || ghost)) drawHook(l, ghost);
      else if (l.kind === 'third' && (vis || ghost)) drawThird(l, t, ghost);
    }
    const pb = ED.scene.progress;
    if (pb.on && inSel) { PX.fillStyle = pb.color; const h = pb.h * K; PX.fillRect(0, pb.pos === 'top' ? 0 : PH.height - h, PH.width * clamp(t / (ED.end - ED.start), 0, 1), h); }
    if (inSel) previewSounds(t);
    const kl = selL(); if (kl && kl.keys && kl.keys.length) $$('#edProps .kfc').forEach(c => { const k = kl.keys[+c.dataset.i]; c.classList.toggle('on', !!k && Math.abs(k[0] - V.currentTime) < 0.15); });
    if (ED.safe) drawSafe();
    for (const [a, v] of ED.guides) { PX.strokeStyle = '#ff3d6e'; PX.lineWidth = 1.5; PX.beginPath(); if (a === 'x') { PX.moveTo(v * K, 0); PX.lineTo(v * K, PH.height); } else { PX.moveTo(0, v * K); PX.lineTo(PH.width, v * K); } PX.stroke(); }
    if (performance.now() - (ED.thumbT || 0) > 450) { ED.thumbT = performance.now(); paintLayerThumbs(); }
    drawSelection();
    if (OBJ.tab === 'crop' && BOX.classList.contains('objmode') && (!V.paused || ED.objT !== V.currentTime)) { ED.objT = V.currentTime; drawObjSrc(); const ot = $('#obTime'); if (ot) ot.textContent = fmt(Math.max(0, tNow())); }
    if (!inSel) { PX.fillStyle = 'rgba(0,0,0,0.55)'; PX.fillRect(0, 0, PH.width, PH.height); PX.fillStyle = '#fff'; PX.font = '600 26px Segoe UI'; PX.textAlign = 'center'; PX.fillText('outside the trimmed clip', PH.width / 2, PH.height / 2); }
    $('#edHead').style.left = (100 * V.currentTime / (ED.dur || 1)) + '%';
    tlTick();
    $('#edTime').textContent = `${fmt(Math.max(0, t))} / ${fmt(ED.end - ED.start)}`;
    drawSrc();
  }


  /* ================================================================ v2 shell: tools, sheet, preview size, timeline */
  const TOOLS = {edit: ['trim', 'Edit'], captions: ['caps', 'Captions'], text: ['text', 'Text'], image: ['img', 'Image'], layout: ['canvas', 'Layout'], fx: ['fx', 'Audio & effects'], thirds: ['thirds', 'Lower thirds']};
  const SUBS = {fx: 'Effects & stickers', thirds: 'Lower thirds'};
  const BOX = $('#editor .edbox'), INSP = $('#edInsp');
  const phone = () => matchMedia('(max-width: 900px)').matches;
  ED.tool = null;
  function markTool(tool) { $$('#editor [data-tool]').forEach(b => { b.classList.toggle('on', b.dataset.tool === tool); b.setAttribute('aria-pressed', String(b.dataset.tool === tool)); }); }
  function openTool(tool, sub) {
    if (!TOOLS[tool]) return;
    ED.tool = tool; ED.sub = sub || null;
    const panel = sub || TOOLS[tool][0];
    edTab(panel);
    $('#edToolName').textContent = sub ? SUBS[sub] : TOOLS[tool][1];
    INSP.classList.toggle('sub', !!sub); INSP.classList.remove('propsonly');
    markTool(tool);
    $('#edSheetBody').scrollTop = 0;
    if (phone()) sheetOpen(BOX.classList.contains('sheet-full') ? 'full' : 'mid');
  }
  window.__openTool = openTool;
  window.__edState = () => ({sel: ED.sel, fx: (ED.scene.fx || []).length, sfx: (ED.scene.sfx || []).length, layers: L().length, hi: ED.hi});
  function initTools() {
    $('#edMore').classList.remove('on');
    if (phone()) { sheetClose(); ED.tool = null; markTool(null); edTab.inner = true; edTab('canvas'); edTab.inner = false; }
    else openTool('layout');
    sizePreview(); setTimeout(() => { tlFit(); drawTracks(); }, 60);
  }
  $$('#editor [data-tool]').forEach(b => b.onclick = () => {
    const t = b.dataset.tool;
    ED.capFromBar = false;
    leaveObj();
    if (phone() && t === 'thirds') { const on = !BOX.classList.contains('thmode'); sheetClose(); leaveLay(); leaveFx(); BOX.classList.remove('trimmode', 'capmode', 'textmode', 'imgmode'); BOX.classList.toggle('thmode', on); markTool(on ? 'thirds' : null); if (on) thTab(THM.tab || 'tpl'); return; }
    BOX.classList.remove('thmode');
    if (phone() && t === 'fx') { const on = !BOX.classList.contains('fxmode'); sheetClose(); leaveLay(); BOX.classList.remove('trimmode', 'capmode', 'textmode', 'imgmode'); if (on) { BOX.classList.add('fxmode'); markTool('fx'); fxTab(FXM.tab || 'snd'); } else leaveFx(); return; }
    leaveFx();
    if (phone() && t === 'layout') { const on = !BOX.classList.contains('laymode'); sheetClose(); BOX.classList.remove('trimmode', 'capmode', 'textmode', 'imgmode'); if (on) { BOX.classList.add('laymode'); markTool('layout'); layTab(LAYM.tab || 'mine'); } else leaveLay(); return; }
    leaveLay();
    if (phone() && t === 'edit') { sheetClose(); BOX.classList.remove('capmode', 'textmode'); BOX.classList.add('trimmode'); markTool('edit'); return; }
    if (phone() && t === 'image') { const on = !BOX.classList.contains('imgmode'); sheetClose(); BOX.classList.remove('trimmode', 'capmode', 'textmode'); BOX.classList.toggle('imgmode', on); markTool(on ? 'image' : null); if (on) drawRecent(); return; }
    BOX.classList.remove('imgmode');
    if (phone() && t === 'text') { sheetClose(); BOX.classList.remove('trimmode', 'capmode'); BOX.classList.add('textmode'); markTool('text'); const sl = selL(); if (!sl || sl.kind !== 'text') { const tl = [...L()].reverse().find(x => x.kind === 'text' && visible(x, tNow())); ED.sel = tl ? tl.id : null; renderLayers(); } txTab(TXM.tab || 'add'); return; }
    if (phone() && t === 'captions') { sheetClose(); BOX.classList.remove('trimmode', 'textmode'); BOX.classList.add('capmode'); markTool('captions'); capTab(CT.tab || 'style'); return; }
    if (phone() && ED.tool === t && BOX.classList.contains('sheet') && !INSP.classList.contains('propsonly') && !ED.sub) return sheetClose();
    openTool(t);
  });
  // trim bar on phones: the same actions as the Edit panel, one tap each, no window
  const secs = () => (V.currentTime - ED.start).toFixed(1) + ' s';
  const TRIM_MSG = {edSetIn: () => 'Clip starts here', edSetOut: () => 'Clip ends here', edCutFrom: () => `Cut from ${secs()}. Play to the end of the part, then Cut to`, edCutClear: () => 'All cuts removed'};
  $$('#editor [data-trim]').forEach(b => b.onclick = () => {
    const id = b.dataset.trim;
    $('#' + id).click();
    if (TRIM_MSG[id]) toast(TRIM_MSG[id]());
    $('#editor [data-trim="edCutFrom"]').classList.toggle('armed', ED.cutA !== null && ED.cutA !== undefined);
    drawTracks();
  });
  $('#edTrimBack').onclick = () => { BOX.classList.remove('trimmode'); markTool(null); };
  matchMedia('(max-width: 900px)').addEventListener('change', e => { if (!e.matches) BOX.classList.remove('trimmode'); });
  /* ---- captions on phones: a bottom bar (Hooks, Style, Fonts, Animation, More, Back) with a small tray above it */
  const CT = {tab: null};
  const setOrig = (id, v, ev = 'change') => { const o = $('#' + id); if (o.type === 'checkbox') o.checked = v; else o.value = v; o.dispatchEvent(new Event('input', {bubbles: true})); o.dispatchEvent(new Event('change', {bubbles: true})); commit(); };
  const ANIM_DEMO = ['NO', 'WAY', 'BRO'];
  function capTab(t) {
    CT.tab = t; const T = $('#edCapTray'), c = capL(), hk = hookL();
    $$('#editor [data-cap]').forEach(b => b.classList.toggle('on', b.dataset.cap === t));
    if (t === 'more') {   // the full captions panel: re-make, translate, fix words, bleeps
      BOX.classList.remove('capmode'); openTool('captions'); sheetOpen('full'); ED.capFromBar = true;
      setTimeout(() => { const r = $('#edRecap'); if (r) r.closest('.row').previousElementSibling.scrollIntoView({block: 'start'}); }, 260);
      return;
    }
    if (!c) { T.innerHTML = '<div class="mut">This clip has no captions layer.</div>'; return; }
    if (t === 'style') {
      const st = CFG.styles[c.style] || {};
      T.innerHTML = `<div class="ctrow"><span class="ctlab">Words</span><div class="ctseg" id="ctWords">${[['0', 'Auto'], ['1', '1'], ['2', '2'], ['3', '3'], ['4', '4'], ['5', '5']].map(([v, n]) => `<button data-v="${v}" class="${String(c.words || 0) === v ? 'on' : ''}">${n}</button>`).join('')}</div>
<span class="ctgap"></span>
          <label class="ctcol" title="Text colour"><input type="color" id="ctC" value="${$('#edCapC').value}"></label><label class="ctcol" title="Highlight colour"><input type="color" id="ctHi" value="${$('#edCapHi').value}"></label>
          <button class="ctbtn" id="ctReset" title="Back to the style's colours" aria-label="Reset colours">↺</button></div>
        <div class="ctscroll">${Object.entries(CFG.styles).map(([k, v]) => `<button class="ctstyle ${c.style === k ? 'on' : ''}" data-v="${k}"><b style="color:${v.color || '#fff'};${v.font ? `font-family:'${esc(v.font)}';` : ''}">Aa <i style="color:${v.hi || v.color || '#ff0'}">Bb</i></b><span>${esc(v.label)}</span></button>`).join('')}</div>`;
      $$('#ctWords button').forEach(b => b.onclick = () => { setOrig('edCapWords', b.dataset.v); capTab('style'); });
      $('#ctC').oninput = e => setOrig('edCapC', e.target.value, 'input'); $('#ctHi').oninput = e => setOrig('edCapHi', e.target.value, 'input');
      $('#ctReset').onclick = () => { $('#edCapCReset').click(); syncAll(); capTab('style'); };
      $$('#edCapTray .ctstyle').forEach(b => b.onclick = () => { setOrig('edStyle', b.dataset.v); syncAll(); capTab('style'); });
    } else if (t === 'fonts') {
      T.innerHTML = `<div class="ctrow"><select id="ctFont" style="flex:1;min-width:0">${$('#edCapFont').innerHTML}</select>
          <button class="ctbtn wide" id="ctAddFont"><svg class="i" aria-hidden="true"><use href="#i-upload"/></svg>Add</button></div>
        <div class="ctrow"><span class="ctlab">Size</span><input type="range" id="ctSize" min="0.5" max="2.5" step="0.05" value="${c.scale}" style="flex:1"><b class="ctval" id="ctSizeV">${Math.round(c.scale * 100)}%</b></div>`;
      $('#ctFont').value = c.font || '';
      $('#ctFont').onchange = e => setOrig('edCapFont', e.target.value);
      $('#ctAddFont').onclick = () => { const f = $('#edFontFile'); f.addEventListener('change', () => setTimeout(() => capTab('fonts'), 1200), {once: true}); f.click(); };
      $('#ctSize').oninput = e => { $('#edCapScale').value = e.target.value; $('#edCapScale').dispatchEvent(new Event('input')); $('#ctSizeV').textContent = Math.round(e.target.value * 100) + '%'; };
      $('#ctSize').onchange = () => commit();
    } else if (t === 'anim') {
      const cur = c.anim || 'auto';
      T.innerHTML = `<div class="ctscroll tall">${Object.entries(CFG.anims || {auto: 'Style default'}).map(([k, v]) => `<button class="ctanim a-${k} ${cur === k ? 'on' : ''}" data-v="${k}"><div class="demo">${ANIM_DEMO.map((w, i) => `<span style="--i:${i}">${w}</span>`).join(' ')}</div><span class="nm">${esc(v)}</span></button>`).join('')}</div>`;
      $$('#edCapTray .ctanim').forEach(b => b.onclick = () => { setOrig('edCapAnim', b.dataset.v); capTab('anim'); });
      const on = $('#edCapTray .ctanim.on'); if (on) on.scrollIntoView({inline: 'center', block: 'nearest'});
    } else if (t === 'hooks') {
      T.innerHTML = `<div class="ctrow"><input id="ctHook" placeholder="Your hook" value="${esc($('#edHook').value)}" style="flex:1;min-width:0">
          <label class="cttime" title="Seconds on screen"><input id="ctSecs" type="number" step="0.5" min="0.5" max="15" value="${hk ? hk.secs : 3.5}"><span>s</span></label>
          <button class="ctswitch ${hk && !hk.hidden ? 'on' : ''}" id="ctHookOn" role="switch" aria-checked="${hk && !hk.hidden}" aria-label="Show the hook"><i></i></button></div>
        <div class="ctrow"><div class="ctscroll inline">${Object.entries(CFG.hook_looks || {box: 'White box'}).map(([k, v]) => `<button class="ctchip ${((hk && hk.look) || 'box') === k ? 'on' : ''}" data-v="${k}">${esc(v)}</button>`).join('')}</div>
          <label class="ctcol" title="Accent colour"><input type="color" id="ctAcc" value="${(hk && hk.accent) || '#ff3d6e'}"></label></div>`;
      $('#ctHook').oninput = e => { $('#edHook').value = e.target.value; $('#edHook').dispatchEvent(new Event('input')); clearTimeout(CT.ht); CT.ht = setTimeout(commit, 600); };
      $('#ctSecs').onchange = e => setOrig('edHookSecs', e.target.value);
      $('#ctHookOn').onclick = () => { setOrig('edHookOn', !$('#edHookOn').checked); capTab('hooks'); };
      $$('#edCapTray .ctchip').forEach(b => b.onclick = () => { const o = $(`#edHookLooks button[data-v="${b.dataset.v}"]`); if (o) o.click(); capTab('hooks'); });
      $('#ctAcc').onchange = e => setOrig('edHookAcc', e.target.value);
    }
  }
  $$('#editor [data-cap]').forEach(b => b.onclick = () => capTab(b.dataset.cap));
  $('#edCapBack').onclick = () => { BOX.classList.remove('capmode'); markTool(null); };
  matchMedia('(max-width: 900px)').addEventListener('change', e => { if (!e.matches) BOX.classList.remove('capmode'); });
  /* ---- text on phones: Add, Fonts, Animation, Styles, Back, with a small tray above the bar */
  const TXM = {tab: null};
  const TEXT_STYLES = [
    ['Classic', {color: '#ffffff', box: false, stroke: '#000000', outline: 1, shadow: true}],
    ['Black box', {color: '#ffffff', box: true, bcolor: '#000000'}],
    ['White box', {color: '#111111', box: true, bcolor: '#ffffff'}],
    ['Yellow punch', {color: '#ffe11a', box: false, stroke: '#000000', outline: 1.6, shadow: true, font: 'Impact'}],
    ['Red label', {color: '#ffffff', box: true, bcolor: '#e8173c'}],
    ['Neon', {color: '#ffffff', box: false, stroke: '#ff3d6e', outline: 1.4, shadow: false}],
    ['Mint box', {color: '#0b0b0b', box: true, bcolor: '#2ee6c8'}],
    ['Heavy outline', {color: '#ffffff', box: false, stroke: '#000000', outline: 2.4, shadow: false}],
    ['Purple box', {color: '#ffffff', box: true, bcolor: '#7c5cff'}],
    ['Soft shadow', {color: '#ffffff', box: false, outline: 0, shadow: true}],
  ];
  const txL = () => { const l = selL(); return l && l.kind === 'text' ? l : null; };
  function txTab(t) {
    TXM.tab = t; const T = $('#edTxtTray'), l = txL();
    $$('#editor [data-txt]').forEach(b => b.classList.toggle('on', b.dataset.txt === t));
    const need = '<div class="ctrow"><span class="mut" style="flex:1">Add a text first, or tap one on the video.</span><button class="ctbtn wide" id="txGoAdd">＋ Add text</button></div>';
    if (t === 'add') {
      T.innerHTML = `<div class="ctrow"><input id="txText" placeholder="${l ? 'Text' : 'Type your text'}" value="${l ? esc(l.text) : ''}" style="flex:1;min-width:0">
          <button class="ctbtn wide pri" id="txNew" title="Add a new text at the playhead">＋ New</button></div>
        <div class="ctrow"><span class="ctlab">Opacity</span><input type="range" id="txOp" min="0.1" max="1" step="0.05" value="${l ? l.opacity ?? 1 : 1}" style="flex:1" ${l ? '' : 'disabled'}><b class="ctval" id="txOpV">${Math.round((l ? l.opacity ?? 1 : 1) * 100)}%</b></div>`;
      $('#txText').oninput = e => { const x = txL(); if (x && e.target.value.trim()) { x.text = e.target.value; renderLayers(); clearTimeout(TXM.t); TXM.t = setTimeout(commit, 600); } };
      $('#txText').onkeydown = e => { if (e.key === 'Enter' && !txL()) $('#txNew').click(); };
      $('#txNew').onclick = () => { const v = $('#txText').value.trim(); newText(txL() || !v ? 'Your text' : v); renderLayers(); txTab('add'); setTimeout(() => { const i = $('#txText'); if (i) { i.focus(); i.select(); } }, 30); };
      $('#txOp').oninput = e => { const x = txL(); if (!x) return; x.opacity = +e.target.value; $('#txOpV').textContent = Math.round(x.opacity * 100) + '%'; };
      $('#txOp').onchange = () => commit();
      return;
    }
    if (!l) { T.innerHTML = need; $('#txGoAdd').onclick = () => { newText('Your text'); renderLayers(); txTab(t); }; return; }
    if (t === 'fonts') {
      T.innerHTML = `<div class="ctrow"><select id="txFont" style="flex:1;min-width:0">${FONTS.map(f => `<option ${f === l.font ? 'selected' : ''} style="font-family:'${esc(f)}'">${esc(f)}</option>`).join('')}</select>
          <label class="ctbtn wide" style="cursor:pointer"><svg class="i" aria-hidden="true"><use href="#i-upload"/></svg>Upload<input type="file" id="txFontUp" accept=".ttf,.otf" hidden></label></div>
        <div class="ctrow"><span class="ctlab">Size</span><input type="range" id="txSize" min="14" max="300" step="1" value="${Math.round(l.size)}" style="flex:1"><b class="ctval" id="txSizeV">${Math.round(l.size)}</b></div>`;
      $('#txFont').onchange = e => { l.font = e.target.value; commit(); };
      $('#txFontUp').onchange = async e => {
        const f = e.target.files[0]; e.target.value = ''; if (!f) return;
        const fd = new FormData(); fd.append('file', f);
        const j = await fetch('/api/fonts', {method: 'POST', body: fd}).then(r => r.json()).catch(() => ({}));
        if (!j.ok) return toast(j.error || 'Upload failed', true);
        CFG.fonts = j.fonts; useFonts(j.fonts); l.font = j.family; commit(); toast(`Font added: ${j.family}`); txTab('fonts');
      };
      $('#txSize').oninput = e => { l.size = +e.target.value; $('#txSizeV').textContent = l.size; };
      $('#txSize').onchange = () => commit();
    } else if (t === 'anim') {
      const cur = l.anim || 'none';
      T.innerHTML = `<div class="ctscroll tall">${Object.entries(TEXT_ANIMS).map(([k, v]) => `<button class="ctanim ta-${k} ${cur === k ? 'on' : ''}" data-v="${k}"><div class="demo"><span>${k === 'typewriter' ? 'HEY<i>|</i>' : 'HEY'}</span></div><span class="nm">${esc(v)}</span></button>`).join('')}</div>`;
      $$('#edTxtTray .ctanim').forEach(b => b.onclick = () => { l.anim = b.dataset.v; commit(); txTab('anim'); V.currentTime = ED.start + (l.start || 0); if (V.paused) V.play().catch(() => {}); });
      const on = $('#edTxtTray .ctanim.on'); if (on) on.scrollIntoView({inline: 'center', block: 'nearest'});
    } else if (t === 'styles') {
      const oc = l.box ? (l.bcolor || '#000000') : (l.stroke || '#000000');
      T.innerHTML = `<div class="ctrow"><span class="ctlab">Text</span><label class="ctcol"><input type="color" id="txC" value="${l.color}"></label>
          <span class="ctlab">${l.box ? 'Box' : 'Outline'}</span><label class="ctcol"><input type="color" id="txO" value="${oc}"></label><span class="ctgap"></span>
          <button class="ctswitch ${l.box ? 'on' : ''}" id="txBox" role="switch" aria-checked="${!!l.box}" aria-label="Background box"><i></i></button><span class="ctlab">Box</span></div>
        <div class="ctscroll">${TEXT_STYLES.map(([n, v], i) => `<button class="ctstyle tx" data-i="${i}"><b style="color:${v.color};${v.box ? `background:${v.bcolor};` : `text-shadow:${[v.outline ? [[-1,-1],[1,-1],[-1,1],[1,1],[0,-1.5],[0,1.5],[-1.5,0],[1.5,0]].map(([x, y]) => `${x * Math.min(1.6, v.outline)}px ${y * Math.min(1.6, v.outline)}px 0 ${v.stroke || '#000'}`).join(',') : '', v.shadow ? '3px 3px 0 rgba(0,0,0,.55)' : ''].filter(Boolean).join(',') || 'none'};`}${v.font ? `font-family:'${v.font}';` : ''}">Text</b><span>${esc(n)}</span></button>`).join('')}</div>`;
      $('#txC').oninput = e => { l.color = e.target.value; }; $('#txC').onchange = () => commit();
      $('#txO').oninput = e => { if (l.box) l.bcolor = e.target.value; else { l.stroke = e.target.value; if (!(l.outline > 0)) l.outline = 1; } }; $('#txO').onchange = () => commit();
      $('#txBox').onclick = () => { l.box = !l.box; commit(); txTab('styles'); };
      $$('#edTxtTray .ctstyle.tx').forEach(b => b.onclick = () => { const v = TEXT_STYLES[+b.dataset.i][1]; Object.assign(l, {stroke: '#000000', outline: 1, shadow: true}, v); commit(); txTab('styles'); });
    }
  }
  $$('#editor [data-txt]').forEach(b => b.onclick = () => txTab(b.dataset.txt));
  $('#edTxtBack').onclick = () => { BOX.classList.remove('textmode'); markTool(null); };
  matchMedia('(max-width: 900px)').addEventListener('change', e => { if (!e.matches) BOX.classList.remove('textmode'); });
  /* ---- layout on phones: My layouts, Frames, Presets, Background, Progress bar, My style. The real controls move into a
     small tray above the bar while it is open, and go back into the Layout panel when it closes. */
  const LAYM = {tab: null, moved: []};
  const LAY_PARTS = {
    mine: [['#edMyLayouts', 'scroll'], ['#edLayoutSave', 'pin']],
    frames: [['#edFrames', 'scroll'], ['#edFrName', 'row']],
    presets: [['#edPresets', 'scroll'], ['#edAddGame', 'row']],
    bg: [['#edBgType', 'el'], ['#edBgBlur', 'grid']],
    extra: [['#edBar', 'row'], ['#edStyleSave', 'row'], ['#edStyleOn', 'label']],
  };
  function putBack() { LAYM.moved.forEach(([el, ph]) => ph.replaceWith(el)); LAYM.moved = []; }
  function leaveLay() { putBack(); if (BOX.classList.contains('laymode')) { BOX.classList.remove('laymode'); markTool(null); } }
  function layTab(t) {
    putBack(); LAYM.tab = t;
    $$('#editor [data-lay]').forEach(b => b.classList.toggle('on', b.dataset.lay === t));
    const T = $('#edLayTray'); T.innerHTML = ''; T.dataset.tab = t;
    const main = document.createElement('div'); main.className = 'ltmain'; T.appendChild(main);
    LAY_PARTS[t].forEach(([sel, how]) => {
      let el = $(sel); if (!el) return;
      if (how === 'row') el = el.closest('.row') || el; else if (how === 'grid') el = el.closest('.pgrid') || el; else if (how === 'label') el = el.closest('label') || el;
      const ph = document.createComment('lay'); el.before(ph); LAYM.moved.push([el, ph]);
      if (how === 'pin') { const w = document.createElement('div'); w.className = 'ltpin'; T.appendChild(w); w.appendChild(el); } else main.appendChild(el);
    });
    if (t === 'bg') $('#edBgType .on')?.scrollIntoView({inline: 'nearest', block: 'nearest'});
  }
  $$('#editor [data-lay]').forEach(b => b.onclick = () => layTab(b.dataset.lay));
  $('#edLayBack').onclick = () => leaveLay();
  matchMedia('(max-width: 900px)').addEventListener('change', e => { if (!e.matches) leaveLay(); });
  /* ---- Audio & FX on phones: Sounds, Playhead (meme combos), Stickers, Visual FX, Loop & rise. Everything lands at the playhead. */
  const FXM = {tab: null, moved: []};
  const fxMove = (el, into) => { if (!el) return; const ph = document.createComment('fx'); el.before(ph); FXM.moved.push([el, ph]); into.appendChild(el); };
  function fxBack() { FXM.moved.forEach(([el, ph]) => ph.replaceWith(el)); FXM.moved = []; }
  /* two taps: the first tap previews (hear it, or see it play at the playhead), a second tap on the same tile adds it */
  const PV = {btn: null, hi: -1, audio: null, timer: 0, t0: 0};
  function pvStop(revert) {
    clearTimeout(PV.timer); if (PV.audio) { PV.audio.pause(); PV.audio = null; }
    if (PV.playing) { V.pause(); V.currentTime = PV.t0; PV.playing = false; }
    if (revert && PV.hi >= 0 && ED.hi > PV.hi) { ED.hist.length = PV.hi + 1; restore(PV.hi); }
    if (PV.btn) PV.btn.classList.remove('armed'); PV.btn = null; PV.hi = -1;
  }
  function pvPlay(ms) { PV.t0 = V.currentTime; PV.playing = true; V.play().catch(() => {}); PV.timer = setTimeout(() => { V.pause(); V.currentTime = PV.t0; PV.playing = false; }, ms); }
  $('#edFxTray').addEventListener('click', e => {
    const b = e.target.closest('.hs button'); if (!b) return;
    e.preventDefault(); e.stopImmediatePropagation();
    const k = b.dataset;
    if (PV.btn === b) {   // second tap: keep it
      const kept = PV.hi >= 0; clearTimeout(PV.timer); if (PV.audio) { PV.audio.pause(); PV.audio = null; }
      if (PV.playing) { V.pause(); V.currentTime = PV.t0; PV.playing = false; }
      b.classList.remove('armed'); PV.btn = null; PV.hi = -1;
      if (k.mu) { const o = $('#edMuFile'); o.value = o.value === k.mu ? '' : k.mu; o.dispatchEvent(new Event('change')); toast(o.value ? 'Music added under the whole clip' : 'Music removed'); fxTab('snd'); }
      else if (k.my) { $(`#edMySnds [data-f="${CSS.escape(k.my)}"]`)?.onclick(); toast('Sound added at the playhead'); }
      else if (k.sk) { $(`#edSfxBtns [data-k="${CSS.escape(k.sk)}"]`)?.onclick(); toast('Sound added at the playhead'); }
      else if (kept) toast('Added at the playhead');
      return;
    }
    pvStop(true);   // a different tile: undo the last preview first
    PV.btn = b; b.classList.add('armed');
    if (k.mu) {
      if ($('#edMuFile').value === k.mu) { toast('Tap again to remove this music'); return; }
      PV.audio = new Audio('/assets/' + encodeURIComponent(k.mu)); PV.audio.volume = 0.8; PV.audio.currentTime = (MU().offset || 0); PV.audio.play().catch(() => {});
      PV.timer = setTimeout(() => pvStop(false), 5000); toast('Preview. Tap again to use it');
    } else if (k.my) { PV.audio = new Audio(sndUrl('file:' + k.my)); PV.audio.play().catch(() => {}); toast('Preview. Tap again to add'); }
    else if (k.sk) { PV.audio = new Audio(sndUrl(k.sk)); PV.audio.play().catch(() => {}); toast('Preview. Tap again to add'); }
    else if (b.onclick) {   // combos, stickers and visual effects: add for real, play it, undo unless tapped again
      PV.hi = ED.hi; b.onclick.call(b, e); pvPlay(1800); toast('Preview. Tap again to add');
    }
  }, true);
  function leaveFx() { pvStop(true); fxBack(); if (BOX.classList.contains('fxmode')) { BOX.classList.remove('fxmode'); markTool(null); } }
  function fxTab(t) {
    pvStop(true); fxBack(); FXM.tab = t;
    $$('#editor [data-fxt]').forEach(b => b.classList.toggle('on', b.dataset.fxt === t));
    const T = $('#edFxTray'); T.innerHTML = '<div class="ltmain"></div>'; T.dataset.tab = t;
    const M = T.firstChild, hs = () => { const d = document.createElement('div'); d.className = 'hs'; M.appendChild(d); return d; };
    const row = html => { const d = document.createElement('div'); d.className = 'ctrow'; d.innerHTML = html; M.appendChild(d); return d; };
    if (t === 'snd') {
      // one list: your uploads first (music and sounds), then the built-in sounds. Sounds land at the playhead; music plays under the whole clip.
      const h = hs(), cur = $('#edMuFile').value, nm = f => f.replace(/^music_/, '').replace(/\.[^.]+$/, '').slice(0, 22);
      const mus = [...$('#edMuFile').options].map(o => o.value).filter(Boolean);
      const mine = $$('#edMySnds [data-f]').map(b => b.dataset.f), built = $$('#edSfxBtns [data-k]').map(b => [b.dataset.k, b.textContent.trim()]);
      h.innerHTML = mus.map(f => `<button data-mu="${esc(f)}" class="mu ${cur === f ? 'on' : ''}"><svg class="i" aria-hidden="true"><use href="#i-${cur === f ? 'check' : 'music'}"/></svg>${esc(nm(f))}<small>${cur === f ? 'Playing' : 'Music'}</small></button>`).join('')
        + mine.map(f => `<button data-my="${esc(f)}" class="mine"><svg class="i" aria-hidden="true"><use href="#i-radio"/></svg>${esc(nm(f))}<small>Yours</small></button>`).join('')
        + built.map(([k, n]) => `<button data-sk="${esc(k)}"><svg class="i" aria-hidden="true"><use href="#i-music"/></svg>${esc(n)}</button>`).join('');
      const pin = document.createElement('div'); pin.className = 'ltpin';
      pin.innerHTML = `<button id="fxUp" class="tall" title="Upload a sound or music"><svg class="i" aria-hidden="true"><use href="#i-upload"/></svg>Upload<input type="file" id="fxUpFile" accept=".mp3,.wav,.ogg,.m4a,.aac,audio/*" hidden></button>`;
      T.appendChild(pin);
      $('#fxUp').onclick = e => { if (e.target.id !== 'fxUpFile') $('#fxUpFile').click(); };
      $('#fxUpFile').onchange = e => {   // long files become music, short ones sound effects
        const f = e.target.files[0]; e.target.value = ''; if (!f) return;
        const a = new Audio(), url = URL.createObjectURL(f);
        const send = dur => { URL.revokeObjectURL(url); const inp = $(dur >= 15 ? '#edMuUp' : '#edSndFile'), dt = new DataTransfer(); dt.items.add(f); inp.files = dt.files; inp.dispatchEvent(new Event('change')); toast(dur >= 15 ? 'Uploading music…' : 'Uploading sound…'); };
        a.onloadedmetadata = () => send(a.duration || 0); a.onerror = () => send(0); a.src = url;
      };
      const r2 = row('<span class="ctlab">Music volume</span>'); fxMove($('#edMuVol'), r2); $('#edMuVol').style.flex = '1';
      if (!cur) r2.style.opacity = '.45';
    } else if (t === 'combo') fxMove($('#edCombos'), hs());
    else if (t === 'stk') fxMove($('#edStickers'), hs());
    else if (t === 'vfx') fxMove($('#edFxBtns'), hs());
    else if (t === 'voice') { fxMove($('#edVoiceBox'), M); voiceSetup(); }
    else if (t === 'loop') {
      fxMove($('#edLoop').closest('label'), M);
      const r = row('<span class="ctlab">Rise at end</span>'); fxMove($('#edMuPeak').closest('.row'), r); r.lastChild.style.flex = '1';
      if (!$('#edMuFile').value) row('<span class="mut" style="font-size:12px">Rise at the end needs background music. Pick it under Sounds.</span>');
    }
  }
  $$('#editor [data-fxt]').forEach(b => b.onclick = () => fxTab(b.dataset.fxt));
  { let tm = 0; const redo = () => { clearTimeout(tm); tm = setTimeout(() => { if (BOX.classList.contains('fxmode') && FXM.tab === 'snd') fxTab('snd'); }, 150); };
    const mo = new MutationObserver(redo); mo.observe($('#edMySnds'), {childList: true}); mo.observe($('#edMuFile'), {childList: true}); }
  $('#edFxBack').onclick = () => leaveFx();
  matchMedia('(max-width: 900px)').addEventListener('change', e => { if (!e.matches) leaveFx(); });
  /* ---- lower thirds on phones: Template, Channel, Font, Animation, Add to clip */
  const THM = {tab: null, accts: null};
  async function thAccounts() {
    if (THM.accts) return THM.accts;
    const A = {youtube: [], instagram: [], facebook: [], tiktok: [], twitch: [], x: []};
    try {
      const j = await api('/api/accounts'); const a = j.accounts || {};
      (a.youtube?.channels || []).forEach(c => c.title && A.youtube.push(c.title));
      (a.instagram?.accounts || []).forEach(c => c.username && A.instagram.push('@' + c.username));
      (a.facebook?.pages || []).forEach(c => c.name && A.facebook.push(c.name));
      (a.tiktok?.accounts || []).forEach(c => (c.username || c.name) && A.tiktok.push(c.username ? '@' + c.username : c.name));
    } catch (e) { /* offline is fine */ }
    return (THM.accts = A);
  }
  function thAutoName() {   // one connected account on this platform fills the name by itself
    const list = (THM.accts || {})[ED.plat] || [];
    if (list.length === 1) { $('#edThName').value = list[0]; return true; }
    if (list.length > 1 && !list.includes($('#edThName').value)) $('#edThName').value = list[0];
    return false;
  }
  function thPreview(cv, k) {
    const c = cv.getContext('2d'); c.clearRect(0, 0, cv.width, cv.height);
    const prims = buildThird(k, $('#edThName').value || 'Your Channel', $('#edThSub').value, PLATFORMS[ED.plat].color, ED.plat), bb = primsBox(prims);
    const sc = Math.min((cv.width - 16) / bb[2], (cv.height - 16) / bb[3]);
    drawPrims(prims, (cv.width / sc - bb[2]) / 2 - bb[0], (cv.height / sc - bb[3]) / 2 - bb[1], 1, 1, c, sc);
  }
  async function thTab(t) {
    THM.tab = t; const T = $('#edThTray');
    $$('#editor [data-tht]').forEach(b => b.classList.toggle('on', b.dataset.tht === t));
    if (!THIRDS[ED.third]) ED.third = 'darkpill';
    await thAccounts();
    if (t === 'tpl') {
      T.innerHTML = `<div class="ctscroll inline" id="thPlat">${Object.entries(PLATFORMS).map(([k, p]) => `<button class="ctchip ${ED.plat === k ? 'on' : ''}" data-p="${k}">${p.label}</button>`).join('')}</div>
        <div class="ctscroll" id="thTpls">${Object.entries(THIRDS).map(([k, v]) => `<button class="thtile ${ED.third === k ? 'on' : ''}" data-k="${k}"><canvas width="240" height="96"></canvas><span>${esc(v.label)}</span></button>`).join('')}</div>`;
      $$('#thTpls .thtile').forEach(b => { thPreview(b.querySelector('canvas'), b.dataset.k); b.onclick = () => { ED.third = b.dataset.k; drawThirdTpls(); thTab('tpl'); }; });
      $$('#thPlat button').forEach(b => b.onclick = () => { ED.plat = b.dataset.p; $('#edThAcc').value = PLATFORMS[ED.plat].color; syncPlat(); thAutoName(); drawThirdTpls(); thTab('tpl'); });
    } else if (t === 'chan') {
      const list = THM.accts[ED.plat] || [];
      if (!$('#edThName').value) thAutoName();
      T.innerHTML = `<div class="ctrow"><input id="thName" placeholder="Channel name or @handle" value="${esc($('#edThName').value)}" style="flex:1;min-width:0">
          <select id="thPick" style="flex:0 0 42%;min-width:0" ${list.length ? '' : 'disabled'}>${list.length ? list.map(n => `<option ${n === $('#edThName').value ? 'selected' : ''}>${esc(n)}</option>`).join('') : `<option>No ${PLATFORMS[ED.plat].label} account</option>`}</select></div>
        <div class="ctrow"><input id="thSub" placeholder="Second line (e.g. FOLLOW ME ON ${PLATFORMS[ED.plat].word})" value="${esc($('#edThSub').value)}" style="flex:1;min-width:0"></div>`;
      $('#thName').oninput = e => { $('#edThName').value = e.target.value; clearTimeout(ED.tt); ED.tt = setTimeout(drawThirdTpls, 250); };
      $('#thSub').oninput = e => { $('#edThSub').value = e.target.value; };
      $('#thPick').onchange = e => { $('#edThName').value = e.target.value; $('#thName').value = e.target.value; drawThirdTpls(); };
    } else if (t === 'font') {
      const O = ED.thOpts;
      T.innerHTML = `<div class="ctrow"><select id="thFont" style="flex:1;min-width:0"><option value="">Template font</option>${FONTS.map(f => `<option ${f === O.font ? 'selected' : ''}>${esc(f)}</option>`).join('')}</select>
          <label class="ctcol" title="Name colour"><input type="color" id="thCol" value="${O.color || '#ffffff'}"></label><button class="ctbtn" id="thColReset" title="Template colour" aria-label="Template colour">↺</button></div>
        <div class="ctrow"><span class="ctlab">Size</span><input type="range" id="thSize" min="0.6" max="1.6" step="0.05" value="${O.size || 1}" style="flex:1"><b class="ctval" id="thSizeV">${Math.round((O.size || 1) * 100)}%</b></div>`;
      $('#thFont').onchange = e => { O.font = e.target.value; drawThirdTpls(); };
      $('#thCol').oninput = e => { O.color = e.target.value; }; $('#thCol').onchange = () => drawThirdTpls();
      $('#thColReset').onclick = () => { O.color = ''; thTab('font'); };
      $('#thSize').oninput = e => { O.size = +e.target.value; $('#thSizeV').textContent = Math.round(O.size * 100) + '%'; };
    } else if (t === 'anim') {
      const cur = $('#edThAnim').value || 'slide';
      T.innerHTML = `<div class="ctscroll tall">${Object.entries(THIRD_ANIMS).map(([k, v]) => `<button class="ctanim th-${k} ${cur === k ? 'on' : ''}" data-v="${k}"><div class="demo"><span class="bar"></span></div><span class="nm">${esc(v)}</span></button>`).join('')}</div>`;
      $$('#edThTray .ctanim').forEach(b => b.onclick = () => { $('#edThAnim').value = b.dataset.v; thTab('anim'); });
    } else if (t === 'add') {
      const max = Math.max(1, Math.round(ED.end - ED.start)), d = +$('#edThDur').value || Math.min(4, max);
      T.innerHTML = `<div class="ctrow"><span class="ctlab">Stays for</span><input type="range" id="thDur" min="1" max="${max}" step="0.5" value="${Math.min(d, max)}" style="flex:1"><b class="ctval" id="thDurV">${Math.min(d, max)} s</b>
          <button class="ctbtn wide pri" id="thAdd">＋ Add</button></div>
        <div class="ctrow"><span class="mut" style="font-size:12px">It starts where the playhead is (${fmt(Math.max(0, tNow()))}). Drag it on the video to move it.</span></div>`;
      $('#thDur').oninput = e => { $('#edThDur').value = e.target.value; $('#thDurV').textContent = e.target.value + ' s'; };
      $('#edThDur').value = $('#thDur').value;
      $('#thAdd').onclick = () => {
        $('#edThStart').value = Math.max(0, +tNow().toFixed(1));
        const l = thirdFromForm(); if (!l) { thTab('chan'); return; }
        addLayer(l); renderLayers(); toast('Lower third added at the playhead');
        V.currentTime = ED.start + l.start; if (V.paused) V.play().catch(() => {});
        $('#thAdd').closest('.ctrow').nextElementSibling.firstChild.textContent = 'Added. Move the playhead and tap Add for another.';
      };
    }
  }
  $$('#editor [data-tht]').forEach(b => b.onclick = () => thTab(b.dataset.tht));
  $('#edThBack').onclick = () => { BOX.classList.remove('thmode'); markTool(null); };
  matchMedia('(max-width: 900px)').addEventListener('change', e => { if (!e.matches) BOX.classList.remove('thmode'); });
  /* ---- phones: tapping something on the video opens its own tools instead of a pop-up */
  function clearModes() { leaveLay(); leaveFx(); BOX.classList.remove('trimmode', 'capmode', 'textmode', 'imgmode', 'thmode', 'objmode'); if (BOX.classList.contains('sheet')) sheetClose(); }
  function routeSelection() {
    const l = selL();
    if (!l) { if (BOX.classList.contains('objmode')) leaveObj(); return !!0; }
    if (l.kind === 'hook' || l.kind === 'captions') { const tab = l.kind === 'hook' ? 'hooks' : 'style'; if (!BOX.classList.contains('capmode')) { clearModes(); BOX.classList.add('capmode'); markTool('captions'); } capTab(tab); return true; }
    if (l.kind === 'text') { if (!BOX.classList.contains('textmode')) { clearModes(); BOX.classList.add('textmode'); markTool('text'); } txTab(TXM.tab || 'add'); return true; }
    if (l.kind === 'video' || l.kind === 'image') { if (!BOX.classList.contains('objmode')) { clearModes(); BOX.classList.add('objmode'); markTool(null); } objTab('adjust'); return true; }
    if (l.kind === 'third' && l.template !== 'sticker' && !String(l.template).startsWith('frame:')) { if (!BOX.classList.contains('thmode')) { clearModes(); BOX.classList.add('thmode'); markTool('thirds'); } thTab('chan'); return true; }
    return false;
  }
  /* ---- video windows and images: Frame, Ratio, Crop, Back */
  const OBJ = {tab: null, lock: true, drag: null};
  function leaveObj() { BOX.classList.remove('objmode'); OBJ.drag = null; }
  const objSrc = l => l.kind === 'video' ? [srcW(), srcH()] : (() => { const i = loadImg(l.file); return i && i.naturalWidth ? [i.naturalWidth, i.naturalHeight] : [1000, 1000]; })();
  function cropBox(c, ar, SW, SH) {   // a crop box of pixel aspect ar around c's centre, as large as c's width allows
    let w = c[2] * SW, h = w / ar; if (h > SH) { h = SH; w = h * ar; } if (w > SW) { w = SW; h = w / ar; }
    const nw = w / SW, nh = h / SH, mx = c[0] + c[2] / 2, my = c[1] + c[3] / 2;
    return [clamp(mx - nw / 2, 0, 1 - nw), clamp(my - nh / 2, 0, 1 - nh), nw, nh];
  }
  function refitKeys(l) {   // keyframes follow a new window shape
    if (!l.keys || !l.keys.length) return; const [SW, SH] = objSrc(l), ar = l.w / l.h;
    l.keys = l.keys.map(k => { const b = cropBox(k.length === 5 ? k.slice(1) : [k[1], k[2], l.crop[2], l.crop[3]], ar, SW, SH); return [k[0], ...b.map(v => +v.toFixed(4))]; });
  }
  function setRatio(l, r) {
    if (l.kind === 'video') {
      if (r === 'fill') Object.assign(l, {x: 0, y: 0, w: 1080, h: 1920});
      else { const cx = l.x + l.w / 2, cy = l.y + l.h / 2; let w = l.w, h = w / r; if (h > 1920) { h = 1920; w = h * r; } if (w > 1080) { w = 1080; h = w / r; } Object.assign(l, {w: Math.round(w), h: Math.round(h), x: Math.round(cx - w / 2), y: Math.round(cy - h / 2)}); }
      fitCrop(l); refitKeys(l);
    } else {
      const [SW, SH] = objSrc(l), ar = r === 'fill' ? 1080 / 1920 : r;
      l.crop = cropBox([0, 0, 1, 1], ar, SW, SH);
      if (r === 'fill') Object.assign(l, {w: 1080, x: 540, y: 960});
    }
  }
  function objTab(t) {
    const l = selL(); if (!l || (l.kind !== 'video' && l.kind !== 'image')) return leaveObj();
    OBJ.tab = t; const T = $('#edObjTray'); T.dataset.tab = t;
    $$('#editor [data-objt]').forEach(b => b.classList.toggle('on', b.dataset.objt === t));
    if (l.kind === 'image' && !l.crop) l.crop = [0, 0, 1, 1];
    if (t === 'frame') {
      const sh = l.shape || 'rect';
      T.innerHTML = `<div class="ctscroll">${SHAPES.map(([k, n]) => `<button class="shtile ${sh === k ? 'on' : ''}" data-s="${k}" title="${n}">${shapeSvg(k)}<span>${n.split(' ')[0]}</span></button>`).join('')}</div>
        <div class="ctrow">${sh === 'round' || sh === 'squircle' ? `<span class="ctlab">Corners</span><input type="range" id="obRad" min="0.02" max="0.5" step="0.01" value="${l.radius ?? 0.12}" style="flex:1">` : ''}
          ${l.kind === 'video' ? `<span class="ctlab">Border</span><input type="range" id="obBord" min="0" max="40" step="1" value="${l.border || 0}" style="flex:1"><label class="ctcol"><input type="color" id="obBc" value="${l.bcolor || '#ffffff'}"></label>` : ''}</div>`;
      $$('#edObjTray .shtile').forEach(b => b.onclick = () => { l.shape = b.dataset.s; if (l.radius == null) l.radius = 0.12; commit(); objTab('frame'); });
      if ($('#obRad')) { $('#obRad').oninput = e => { l.radius = +e.target.value; }; $('#obRad').onchange = () => commit(); }
      if ($('#obBord')) { $('#obBord').oninput = e => { l.border = +e.target.value; }; $('#obBord').onchange = () => commit(); $('#obBc').oninput = e => { l.bcolor = e.target.value; }; $('#obBc').onchange = () => commit(); }
    } else if (t === 'ratio') {
      const R = [['fill', 'Fill screen', 9 / 16], [9 / 16, '9:16', 9 / 16], [16 / 9, '16:9', 16 / 9], [1, '1:1', 1], [4 / 5, '4:5', 4 / 5], [3 / 4, '3:4', 3 / 4]];
      T.innerHTML = `<div class="ctscroll">${R.map(([v, n, a], i) => `<button class="rtile" data-i="${i}"><i style="aspect-ratio:${a};${a >= 1 ? 'width:44px' : 'height:44px'}"></i><span>${n}</span></button>`).join('')}</div>`;
      $$('#edObjTray .rtile').forEach(b => b.onclick = () => { setRatio(l, R[+b.dataset.i][0]); syncAll(); commit(); toast(R[+b.dataset.i][1]); });
    } else if (t === 'adjust') {
      const vid = l.kind === 'video', wmax = 1080, w0 = Math.round(l.w);
      T.innerHTML = `<div class="ctrow"><span class="ctlab" style="min-width:56px">Size</span><input type="range" id="obSize" min="${vid ? 120 : 40}" max="${wmax}" step="2" value="${w0}" style="flex:1"><b class="ctval" id="obSizeV">${Math.round(w0 / 1080 * 100)}%</b></div>
        <div class="ctrow"><span class="ctlab" style="min-width:56px">Opacity</span><input type="range" id="obOp" min="0.05" max="1" step="0.05" value="${l.opacity ?? 1}" style="flex:1"><b class="ctval" id="obOpV">${Math.round((l.opacity ?? 1) * 100)}%</b></div>`;
      $('#obSize').oninput = e => {
        const w = +e.target.value;
        if (vid) { const cx = l.x + l.w / 2, cy = l.y + l.h / 2, h = Math.round(w * l.h / l.w); Object.assign(l, {w, h, x: Math.round(cx - w / 2), y: Math.round(cy - h / 2)}); fitCrop(l); }
        else l.w = w;
        $('#obSizeV').textContent = Math.round(w / 1080 * 100) + '%';
      };
      $('#obOp').oninput = e => { l.opacity = +e.target.value; $('#obOpV').textContent = Math.round(l.opacity * 100) + '%'; };
      $('#obSize').onchange = $('#obOp').onchange = () => { commit(); syncAll(); };
    } else if (t === 'crop') {
      const vid = l.kind === 'video', K = l.keys || [];
      const lockB = `<button class="ctbtn ${OBJ.lock ? 'pri' : ''}" id="obLock" title="${OBJ.lock ? 'Shape locked' : 'Free shape'}" aria-pressed="${OBJ.lock}"><svg class="i" aria-hidden="true" viewBox="0 0 24 24"><rect x="5" y="11" width="14" height="10" rx="2"/><path d="${OBJ.lock ? 'M8 11V7a4 4 0 0 1 8 0v4' : 'M8 11V7a4 4 0 0 1 7.5-2'}"/></svg></button>
          <button class="ctbtn wide" id="obReset" title="Show the whole ${vid ? 'stream' : 'picture'}">Whole</button>`;
      T.innerHTML = `<div class="ctrow"><canvas id="obSrc"></canvas></div>
        ${vid ? `<div class="ctrow"><button class="ctbtn" data-px="edBack" aria-label="Back 1 second">«</button><button class="ctbtn pri" data-px="edPlay" aria-label="Play or pause">${V.paused ? '▶' : '❚❚'}</button><button class="ctbtn" data-px="edFwd" aria-label="Forward 1 second">»</button>
          <span class="ctval" id="obTime" style="text-align:left;min-width:44px">${fmt(Math.max(0, tNow()))}</span><span class="ctgap"></span>
          <button class="ctbtn" id="obPrev" title="Previous keyframe">◀◆</button><button class="ctbtn wide pri" id="obKey" title="Keyframe here">◆ Key</button><button class="ctbtn" id="obNext" title="Next keyframe">◆▶</button></div>
        <div class="ctrow">${lockB}<div class="ctseg" id="obMode"><button data-v="smooth" class="${l.kmode !== 'cut' ? 'on' : ''}">Smooth</button><button data-v="cut" class="${l.kmode === 'cut' ? 'on' : ''}">Jump</button></div>
          <span class="ctgap"></span><span class="ctlab">${K.length} key${K.length === 1 ? '' : 's'}</span>${K.length ? '<button class="ctbtn" id="obClear" title="Remove all keyframes" aria-label="Remove all keyframes">✕</button>' : ''}</div>`
        : `<div class="ctrow">${lockB}<span class="ctlab">Drag the box, or its corner to resize</span></div>`}`;
      $$('#edObjTray [data-px]').forEach(b => b.onclick = () => { $('#' + b.dataset.px).click(); setTimeout(() => { b.dataset.px === 'edPlay' && (b.textContent = V.paused ? '▶' : '❚❚'); }, 60); });
      drawObjSrc();
      $('#obLock').onclick = () => { OBJ.lock = !OBJ.lock; objTab('crop'); };
      $('#obReset').onclick = () => { if (vid) { l.crop = fitBox([0, 0, 1, 1], l.w / l.h, ...objSrc(l)); l.keys = []; } else l.crop = [0, 0, 1, 1]; syncAll(); commit(); objTab('crop'); };
      if (vid) {
        $('#obKey').onclick = () => { if (K.length) l.crop = [...cropAt(l, V.currentTime)]; setKey(l, V.currentTime); commit(); objTab('crop'); toast('Keyframe set'); };
        const jump = d => { const t = V.currentTime, ks = (l.keys || []).map(k => k[0]); const n = d > 0 ? ks.find(k => k > t + 0.05) : [...ks].reverse().find(k => k < t - 0.05); if (n != null) V.currentTime = n; else toast(d > 0 ? 'No later keyframe' : 'No earlier keyframe'); };
        $('#obPrev').onclick = () => jump(-1); $('#obNext').onclick = () => jump(1);
        $$('#obMode button').forEach(b => b.onclick = () => { l.kmode = b.dataset.v; commit(); objTab('crop'); });
        if ($('#obClear')) $('#obClear').onclick = () => { l.crop = [...cropAt(l, V.currentTime)]; l.keys = []; commit(); objTab('crop'); };
      }
      const cv = $('#obSrc'), pt = e => { const r = cv.getBoundingClientRect(); return [(e.clientX - r.left) / r.width, (e.clientY - r.top) / r.height]; };
      cv.onpointerdown = e => {
        if (vid && l.keys && l.keys.length) l.crop = [...cropAt(l, V.currentTime)];
        const [px, py] = pt(e), [bx, by, bw, bh] = l.crop, near = (a, b) => Math.abs(a - b) < 0.07;
        const corner = near(px, bx + bw) && near(py, by + bh);
        if (!corner && !(px >= bx && px <= bx + bw && py >= by && py <= by + bh)) return;
        OBJ.drag = {mode: corner ? 'size' : 'move', px, py, o: [...l.crop]}; try { cv.setPointerCapture(e.pointerId); } catch (er) { /* */ }
      };
      cv.onpointermove = e => {
        const D = OBJ.drag; if (!D) return; const [px, py] = pt(e), o = D.o, [SW, SH] = objSrc(l);
        if (D.mode === 'move') l.crop = [clamp(o[0] + px - D.px, 0, 1 - o[2]), clamp(o[1] + py - D.py, 0, 1 - o[3]), o[2], o[3]];
        else {
          let w = clamp(o[2] + px - D.px, 0.05, 1 - o[0]), h = clamp(o[3] + py - D.py, 0.05, 1 - o[1]);
          if (OBJ.lock) { const ar = (o[2] * SW) / (o[3] * SH); h = w * SW / ar / SH; if (o[1] + h > 1) { h = 1 - o[1]; w = h * SH * ar / SW; } }
          l.crop = [o[0], o[1], w, h];
          if (vid && !OBJ.lock) { l.h = Math.round(clamp(l.w * (h * SH) / (w * SW), 80, 1920)); refitKeys(l); }   // free crop reshapes the window
          if (vid) fitCrop(l);
        }
        if (vid && l.keys && l.keys.length) setKey(l, V.currentTime);
        drawObjSrc();
      };
      cv.onpointerup = cv.onpointercancel = () => { if (OBJ.drag) { OBJ.drag = null; commit(); syncAll(); } };
    }
  }
  function drawObjSrc() {   // the whole stream or picture, dimmed, with the crop box on top
    const c = $('#obSrc'), l = selL(); if (!c || !l || !BOX.classList.contains('objmode')) return;
    const T = $('#edObjTray'), rows = [...T.children].slice(1).reduce((a, x) => a + x.offsetHeight + 8, 0);
    const [SW, SH] = objSrc(l), r = c.parentElement.clientWidth, Hm = Math.max(150, T.clientHeight - rows - 40);
    let cw = r, ch = cw * SH / SW; if (ch > Hm) { ch = Hm; cw = ch * SW / SH; }
    if (c.style.width !== cw + 'px') { c.style.width = cw + 'px'; c.style.height = ch + 'px'; }
    const W = c.width = Math.round(cw * devicePixelRatio), H = c.height = Math.round(ch * devicePixelRatio), x = c.getContext('2d');
    const src = l.kind === 'video' ? V : loadImg(l.file);
    try { if (l.kind === 'video' ? V.videoWidth : src && src.naturalWidth) x.drawImage(src, 0, 0, W, H); } catch (e) { /* not ready */ }
    const cc = l.kind === 'video' ? cropAt(l, V.currentTime) : l.crop, [bx, by, bw, bh] = [cc[0] * W, cc[1] * H, cc[2] * W, cc[3] * H];
    x.fillStyle = 'rgba(0,0,0,0.6)'; x.fillRect(0, 0, W, by); x.fillRect(0, by + bh, W, H - by - bh); x.fillRect(0, by, bx, bh); x.fillRect(bx + bw, by, W - bx - bw, bh);
    x.strokeStyle = '#2ee6c8'; x.lineWidth = 2 * devicePixelRatio; x.strokeRect(bx, by, bw, bh);
    x.fillStyle = '#2ee6c8'; x.beginPath(); x.arc(bx + bw, by + bh, 7 * devicePixelRatio, 0, Math.PI * 2); x.fill();
    if (l.kind === 'video' && l.keys && l.keys.length) { x.fillStyle = '#ffd400'; l.keys.forEach(k => { const kx = (k[1] + (k.length === 5 ? k[3] : cc[2]) / 2) * W, ky = (k[2] + (k.length === 5 ? k[4] : cc[3]) / 2) * H; x.beginPath(); x.moveTo(kx, ky - 5); x.lineTo(kx + 5, ky); x.lineTo(kx, ky + 5); x.lineTo(kx - 5, ky); x.fill(); }); }
  }
  $$('#editor [data-objt]').forEach(b => b.onclick = () => objTab(b.dataset.objt));
  $('#edObjBack').onclick = () => { ED.sel = null; leaveObj(); syncProps(); renderLayers(); };
  matchMedia('(max-width: 900px)').addEventListener('change', e => { if (!e.matches) leaveObj(); });
  /* only one bottom-bar mode at a time: when a new one switches on, any other still on is closed properly */
  {
    const MODES = ['trimmode', 'capmode', 'textmode', 'imgmode', 'laymode', 'fxmode', 'thmode', 'objmode'];
    let prev = new Set();
    new MutationObserver(() => {
      const on = MODES.filter(m => BOX.classList.contains(m));
      if (on.length > 1) {
        const keep = on.find(m => !prev.has(m)) || on[on.length - 1];
        on.filter(m => m !== keep).forEach(m => {
          if (m === 'fxmode') leaveFx(); else if (m === 'laymode') leaveLay(); else if (m === 'objmode') leaveObj(); else BOX.classList.remove(m);
        });
      }
      prev = new Set(MODES.filter(m => BOX.classList.contains(m)));
    }).observe(BOX, {attributes: true, attributeFilter: ['class']});
  }
  // tap feedback on the toolbars, the side column, Delete and Render
  $('#editor').addEventListener('pointerdown', e => {
    const b = e.target.closest('#edMainBar .barbtn, .edrail .railbtn, #edSide .iconbtn, #edSide .sidesave, #edLyrDel, #edUndo, #edRedo, #edSave');
    if (!b || b.disabled) return;
    b.classList.remove('tapfx'); void b.offsetWidth; b.classList.add('tapfx');
    clearTimeout(b.__tapT); b.__tapT = setTimeout(() => b.classList.remove('tapfx'), 600);
  }, true);
  $$('#editor .moregrid [data-go]').forEach(b => b.onclick = () => {
    const g = b.dataset.go;
    if (TOOLS[g]) return openTool(g);
    if (g === 'safe') { $('#edSafe').click(); toast(ED.safe ? 'Safe zone on: app buttons are shaded' : 'Safe zone off'); return; }
    if (g === 'enhance') {
      if (phone()) { sheetClose(); $('#edMore').classList.add('on'); $('#edSideEnh').setAttribute('aria-expanded', 'true'); }
      else { const e = $('#edEnhBox'); e.scrollIntoView({block: 'nearest'}); e.animate([{outline: '2px solid var(--acc2)'}, {outline: '2px solid transparent'}], {duration: 1400}); }
    }
  });
  $('#edBackTool').onclick = () => sheetClose();
  $('#edSheetX').onclick = () => { if (phone()) sheetClose(); else { ED.sel = null; syncProps(); } };
  // phone action column: each button presses the matching top-bar button, and copies its disabled state
  $$('#edSide [data-proxy]').forEach(b => {
    const o = $('#' + b.dataset.proxy); b.onclick = e => { e.stopPropagation(); o.click(); };
    const sync = () => { b.disabled = o.disabled; if (b.hasAttribute('aria-pressed')) b.setAttribute('aria-pressed', String(o.classList.contains('on'))); }; sync(); new MutationObserver(sync).observe(o, {attributes: true, attributeFilter: ['disabled', 'class']});
  });
  {   // the top bar is hidden on phones, so the enhance menu moves out of it there
    const mq = matchMedia('(max-width: 900px)'), M = $('#edMore'), home = $('#edMoreBtn');
    const place = () => { if (mq.matches) { if (M.parentElement !== BOX) BOX.appendChild(M); } else if (M.nextElementSibling !== home) home.before(M); };
    place(); mq.addEventListener('change', place);
  }
  $('#edSideEnh').onclick = e => {   // opens the enhance settings in a row right next to the button
    e.stopPropagation(); const B = e.currentTarget, M = $('#edMore'), on = !M.classList.contains('on');
    B.classList.remove('pop'); void B.offsetWidth; B.classList.add('pop');
    if (on) { const r = B.getBoundingClientRect(), br = BOX.getBoundingClientRect(); M.style.left = (r.right - br.left + 8) + 'px'; M.style.top = (r.top - br.top + r.height / 2) + 'px'; M.style.translate = '0 -50%'; }
    M.classList.toggle('on', on); M.classList.toggle('enhpop', on); B.setAttribute('aria-expanded', String(on));
  };
  $('#edMoreBtn').onclick = e => { e.stopPropagation(); const on = !$('#edMore').classList.contains('on'); $('#edMore').classList.toggle('on', on); $('#edMoreBtn').setAttribute('aria-expanded', String(on)); };
  document.addEventListener('pointerdown', e => { if ($('#edMore').classList.contains('on') && !e.target.closest('#edMore,#edMoreBtn,#edSideEnh')) { $('#edMore').classList.remove('on', 'enhpop'); $('#edMoreBtn').setAttribute('aria-expanded', 'false'); $('#edSideEnh').setAttribute('aria-expanded', 'false'); } });
  $('#edFit2').onclick = () => viewFit();
  // ---- bottom sheet (phones and tablets)
  const SHEET = {compact: 0.32, mid: 0.5, full: 0.84};
  const barH = () => Math.max(...$$('#editor .edbar').map(b => b.offsetHeight), 0);
  function sheetOpen(size) {
    BOX.style.setProperty('--bar-px', barH() + 'px');
    const h = Math.round((BOX.clientHeight - barH()) * SHEET[size]);
    BOX.classList.add('sheet'); BOX.classList.remove('sheet-compact', 'sheet-mid', 'sheet-full'); BOX.classList.add('sheet-' + size);
    BOX.style.setProperty('--sheet-h', h + 'px');
    setTimeout(() => { sizePreview(); tlKeep(); }, 230);
  }
  function sheetClose() {
    BOX.classList.remove('sheet', 'sheet-compact', 'sheet-mid', 'sheet-full'); INSP.classList.remove('propsonly');
    if (ED.capFromBar && phone()) { ED.capFromBar = false; BOX.classList.add('capmode'); capTab('style'); markTool('captions'); return setTimeout(() => { sizePreview(); tlKeep(); }, 230); }
    if (phone()) { ED.tool = null; markTool(null); }
    setTimeout(() => { sizePreview(); tlKeep(); }, 230);
  }
  window.__sheetClose = () => { if (phone()) sheetClose(); };
  function sheetForSelection() {
    if (!phone() || !ED.scene) return;
    const tapped = ED.tapSel; ED.tapSel = false;
    if (tapped && routeSelection()) return;
    if (BOX.classList.contains('objmode')) { const l = selL(); if (!l) leaveObj(); else if (l.kind === 'video' || l.kind === 'image') objTab(OBJ.tab || 'adjust'); return; }
    if (BOX.classList.contains('textmode')) { if (TXM.tab) txTab(TXM.tab); return; }
    if (['capmode', 'trimmode', 'imgmode', 'laymode', 'fxmode', 'thmode'].some(c => BOX.classList.contains(c))) return;
    const l = L().find(x => x.id === ED.sel);
    if (l && !BOX.classList.contains('sheet')) { INSP.classList.add('propsonly'); $('#edToolName').textContent = 'Selected'; markTool(null); sheetOpen('compact'); }
    else if (!l && INSP.classList.contains('propsonly')) sheetClose();
  }
  (() => {   // drag the grip to resize or close the sheet
    const g = $('#edGrip'); let y0 = 0, h0 = 0, on = false;
    g.addEventListener('pointerdown', e => { on = true; y0 = e.clientY; h0 = INSP.offsetHeight; g.setPointerCapture(e.pointerId); INSP.style.transition = 'none'; });
    g.addEventListener('pointermove', e => { if (on) BOX.style.setProperty('--sheet-h', Math.max(80, h0 + y0 - e.clientY) + 'px'); });
    g.addEventListener('pointerup', e => {
      if (!on) return; on = false; INSP.style.transition = '';
      const avail = BOX.clientHeight - barH(), f = INSP.offsetHeight / avail;
      if (f < 0.2) sheetClose(); else sheetOpen(f < 0.41 ? 'compact' : f < 0.67 ? 'mid' : 'full');
    });
  })();
  INSP.addEventListener('focusin', e => { if (phone() && e.target.matches('input[type=text],input:not([type]),textarea') && BOX.classList.contains('sheet-compact')) sheetOpen('mid'); });
  // ---- preview keeps 9:16 inside whatever space is left
  function sizePreview() {
    const box = $('#edPreviewBox'); if (!box) return;
    const cs = getComputedStyle(box), w = box.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight),
      h = box.clientHeight - parseFloat(cs.paddingTop) - parseFloat(cs.paddingBottom);
    if (w <= 0 || h <= 0) return;
    let ww = Math.min(w, h * 9 / 16), hh = ww * 16 / 9;
    WRAP.style.width = Math.floor(ww) + 'px'; WRAP.style.height = Math.floor(hh) + 'px';
    viewFit();
  }
  new ResizeObserver(() => sizePreview()).observe($('#edPreviewBox'));
  // ---- multi-track timeline
  const TLI = $('#edTlInner'), TLS = $('#edTlScroll');
  ED.tlz = 1;
  const ICN = n => `<svg class="i" aria-hidden="true"><use href="#i-${n}"/></svg>`;
  const ROWS = [['video', 'film', 'Video'], ['cap', 'message', 'Captions'], ['hook', 'type', 'Hook'], ['text', 'edit', 'Text and images'], ['stk', 'star', 'Stickers and thirds'],
    ['fx', 'zap', 'Effects'], ['sfx', 'radio', 'Sounds'], ['music', 'music', 'Music'], ['bleep', 'mute', 'Bleeps'], ['kf', 'layers', 'Crop keyframes']];
  const pct = t => (100 * t / (ED.dur || 1)) + '%';
  function tlFit() {   // the whole video, start to end, fitted to the screen; + and - zoom in from there
    if (!ED.dur) return;
    ED.tlz = 1; TLI.style.width = '100%';
    requestAnimationFrame(() => { TLS.scrollLeft = 0; drawRuler(); drawTracks(); });
  }
  window.__tlFit = tlFit;
  new ResizeObserver(() => { if (ED.tlz <= 1.001) { clearTimeout(ED.fitT2); ED.fitT2 = setTimeout(tlFit, 60); } else drawRuler(); }).observe(TLS);
  function tlZoom(f, cx) {
    const r = TLS.getBoundingClientRect(), x = cx === undefined ? r.width / 2 : cx - r.left, t = (TLS.scrollLeft + x) / TLI.clientWidth * ED.dur;
    ED.tlz = clamp(ED.tlz * f, 1, 60); TLI.style.width = (100 * ED.tlz) + '%';
    TLS.scrollLeft = t / ED.dur * TLI.clientWidth - x; drawRuler();
  }
  function tlKeep() { drawRuler(); }
  $('#edTlIn').onclick = () => tlZoom(1.5); $('#edTlOut').onclick = () => tlZoom(1 / 1.5);
  $('#edTlExp').onclick = () => { const on = !$('#edTl').classList.contains('big'); $('#edTl').classList.toggle('big', on); $('#edTlExp').setAttribute('aria-expanded', String(on)); drawTracks(); setTimeout(sizePreview, 50); };
  TLS.addEventListener('wheel', e => { if (e.ctrlKey || e.metaKey) { e.preventDefault(); tlZoom(e.deltaY < 0 ? 1.2 : 1 / 1.2, e.clientX); } }, {passive: false});
  (() => {   // pinch the timeline to zoom it
    const pts = new Map(); let d0 = 0;
    TLS.addEventListener('pointerdown', e => { if (e.pointerType === 'touch') pts.set(e.pointerId, e.clientX); });
    TLS.addEventListener('pointermove', e => {
      if (!pts.has(e.pointerId)) return; pts.set(e.pointerId, e.clientX);
      if (pts.size === 2) { const [a, b] = [...pts.values()], d = Math.abs(a - b); if (d0) tlZoom(d / d0, (a + b) / 2); d0 = d; }
    });
    const up = e => { pts.delete(e.pointerId); if (pts.size < 2) d0 = 0; };
    TLS.addEventListener('pointerup', up); TLS.addEventListener('pointercancel', up);
  })();
  TLS.addEventListener('scroll', () => drawRuler(), {passive: true});
  function drawRuler() {
    if (!ED.dur) return;
    const px = TLI.clientWidth / ED.dur, step = [0.5, 1, 2, 5, 10, 15, 30, 60, 120].find(s => s * px >= 70) || 300;
    const a = Math.floor((TLS.scrollLeft / px) / step) * step, b = (TLS.scrollLeft + TLS.clientWidth) / px;
    let h = '';
    for (let t = Math.max(0, a); t <= Math.min(ED.dur, b + step); t += step) h += `<span style="left:${pct(t)}">${fmt(t)}</span>`;
    $('#edTlRuler').innerHTML = h;
  }
  $('#edTlRuler').addEventListener('pointerdown', e => { const r = TLI.getBoundingClientRect(); V.currentTime = clamp((e.clientX - r.left) / r.width * ED.dur, 0, ED.dur); });
  function capBlocks() {   // caption words grouped into short lines
    const out = []; let cur = null;
    (ED.words || []).forEach(w => {
      if (cur && (w[0] - cur[1] > 0.6 || cur[3] >= 4)) { out.push(cur); cur = null; }
      if (!cur) cur = [w[0], w[1], w[2], 1]; else { cur[1] = w[1]; cur[2] += ' ' + w[2]; cur[3]++; }
    });
    if (cur) out.push(cur);
    return out;
  }
  function drawTracks() {
    if (!ED.scene || !ED.dur) return;
    const S = ED.start, rel = t => S + t, rows = {cap: [], hook: [], text: [], stk: [], fx: [], sfx: [], music: [], bleep: [], kf: []};
    const B = (row, a, b, cls, label, data) => rows[row].push(`<div class="blk ${cls}${data.id && data.id === ED.sel ? ' sel' : ''}" style="left:${pct(a)};width:max(6px,${pct(Math.max(0.05, b - a))})" ${Object.entries(data).map(([k, v]) => `data-${k}="${esc(String(v))}"`).join(' ')} title="${esc(label)}">${data.drag ? '<i class="eh l"></i>' : ''}${esc(label)}${data.drag ? '<i class="eh r"></i>' : ''}</div>`);
    capBlocks().forEach(c => B('cap', c[0], c[1], 'k-cap', c[2], {go: 'captions'}));
    const hk = hookL(); if (hk && !hk.hidden && $('#edHook').value.trim()) B('hook', S, rel(hk.secs), 'k-hook', $('#edHook').value.replace(/\n/g, ' '), {id: hk.id, go: 'captions'});
    const clipLen = ED.end - ED.start;
    L().forEach(l => {
      if (l.kind === 'text') B('text', rel(l.start || 0), rel(l.end || clipLen), 'k-text', l.text || 'Text', {id: l.id, drag: 1});
      else if (l.kind === 'image') B('text', rel(l.start || 0), rel(l.end || clipLen), 'k-img', l.name || 'Image', {id: l.id, drag: 1});
      else if (l.kind === 'third') {
        const a = l.from_end ? clipLen - l.from_end : l.start, b = l.from_end ? clipLen : (l.dur ? l.start + l.dur : clipLen);
        B('stk', rel(a), rel(b), l.template === 'sticker' ? 'k-stk' : 'k-third', l.title || l.name || 'Third', {id: l.id, drag: l.from_end ? 0 : 1});
      } else if (l.kind === 'video' && (l.keys || []).length) l.keys.forEach((k, i) => rows.kf.push(`<div class="kf" style="left:${pct(k[0])}" data-t="${k[0]}" title="Crop keyframe ${i + 1} (${esc(l.name || l.role)})"></div>`));
    });
    (ED.scene.fx || []).forEach((f, i) => B('fx', rel(f.start || 0), rel((f.start || 0) + (f.dur || 0.6)), 'k-fx', f.type || f.kind || 'Effect', {go: 'more:fx'}));
    (ED.scene.sfx || []).forEach((f, i) => B('sfx', rel(f.start || 0), rel((f.start || 0) + 0.9), 'k-sfx', f.sound || 'Sound', {go: 'audio'}));
    const mu = ED.scene.music; if (mu && mu.file) B('music', S, ED.end, 'k-music', mu.file.replace(/^music_/, '').replace(/\.[^.]+$/, ''), {go: 'audio'});
    ((ED.scene.bleep || {}).spans || []).forEach(([a, b]) => B('bleep', a, b, 'k-bleep', 'Bleep', {go: 'captions'}));
    const big = $('#edTl').classList.contains('big');
    const show = ROWS.slice(1).filter(([k]) => big || rows[k].length).slice(0, big ? 99 : (phone() ? 2 : 4));
    $('#edTlRows').innerHTML = show.map(([k]) => `<div class="tlrow" data-row="${k}">${rows[k].join('')}</div>`).join('');
    $('#edTlLabels').innerHTML = `<div title="Video">${ICN('film')}</div>` + show.map(([k, ic, name]) => `<div title="${name}">${ICN(ic)}</div>`).join('');
    const hidden = ROWS.slice(1).filter(([k]) => rows[k].length && !show.find(s => s[0] === k)).length;
    $('#edTlInfo').textContent = hidden ? `${hidden} more track${hidden > 1 ? 's' : ''}` : '';
    drawRuler();
  }
  window.__drawTracks = drawTracks;
  function tlTick() {
    const h = $('#edTlHead'); if (!h || !ED.dur) return;
    h.style.left = pct(V.currentTime);
    if (!V.paused && !ED.tlDrag) {   // keep the playhead in view while playing
      const x = V.currentTime / ED.dur * TLI.clientWidth;
      if (x < TLS.scrollLeft + 20 || x > TLS.scrollLeft + TLS.clientWidth - 40) TLS.scrollLeft = x - TLS.clientWidth * 0.3;
    }
  }
  $('#edTlRows').addEventListener('pointerdown', e => {
    const blk = e.target.closest('.blk'), kf = e.target.closest('.kf'), r = TLI.getBoundingClientRect();
    const tAt = x => clamp((x - r.left) / r.width * ED.dur, 0, ED.dur);
    if (kf) { V.currentTime = +kf.dataset.t; return; }
    if (!blk) { V.currentTime = tAt(e.clientX); return; }
    const id = blk.dataset.id, l = id && L().find(x => x.id === id);
    if (!l || !blk.dataset.drag) {   // captions, effects, sounds: jump there and open their tool
      V.currentTime = tAt(e.clientX);
      if (l) { ED.sel = l.id; syncProps(); }
      const go = blk.dataset.go; if (go) { const [t, sub] = go.split(':'); openTool(t, sub); }
      return;
    }
    const el = blk;
    ED.sel = l.id; syncProps(); $$('#edTlRows .blk.sel').forEach(x => x.classList.remove('sel')); el.classList.add('sel');
    const edge = e.target.classList.contains('l') ? 'l' : e.target.classList.contains('r') ? 'r' : 'm';
    const clipLen = ED.end - ED.start, isThird = l.kind === 'third';
    const s0 = isThird ? l.start : (l.start || 0), e0 = isThird ? (l.dur ? l.start + l.dur : clipLen) : (l.end || clipLen), x0 = e.clientX, px = r.width / ED.dur;
    ED.tlDrag = true; try { el.setPointerCapture(e.pointerId); } catch (err) { /* element was redrawn */ }
    let moved = false;
    const mv = ev => {
      const d = (ev.clientX - x0) / px; if (Math.abs(ev.clientX - x0) > 3) moved = true;
      let a = s0, b = e0;
      if (edge === 'l') a = clamp(s0 + d, 0, e0 - 0.3); else if (edge === 'r') b = clamp(e0 + d, s0 + 0.3, clipLen); else { const len = e0 - s0; a = clamp(s0 + d, 0, clipLen - len); b = a + len; }
      a = +a.toFixed(2); b = +b.toFixed(2);
      if (isThird) { l.start = a; l.dur = +(b - a).toFixed(2); } else { l.start = a; l.end = b; }
      el.style.left = pct(S0 + a); el.style.width = pct(b - a);
      V.currentTime = S0 + (edge === 'r' ? b - 0.05 : a);
    };
    const S0 = ED.start;
    const up = () => { el.removeEventListener('pointermove', mv); el.removeEventListener('pointerup', up); ED.tlDrag = false; if (moved) { syncProps(); commit(); } else V.currentTime = tAt(e.clientX); drawTracks(); };
    el.addEventListener('pointermove', mv); el.addEventListener('pointerup', up);
  });
  window.addEventListener('resize', () => { if ($('#editor').classList.contains('on')) { sizePreview(); drawTracks(); } });

  /* ================================================================ save */
  $('#edSave').onclick = async () => {
    L().filter(l => l.kind === 'video').forEach(fitCrop);
    const body = {scene: ED.scene, trim_start: +ED.start.toFixed(2), trim_end: +ED.end.toFixed(2), words: ED.words, hook: $('#edHook').value.replace(/[ \t]+\n/g, '\n').replace(/\n{3,}/g, '\n\n').trim(), cuts: ED.cuts || []};
    $('#edSave').disabled = true;
    const j = await api(`/api/clips/${encodeURIComponent(ED.clip.name)}/edit`, body);
    $('#edSave').disabled = false;
    if (j.ok) { toast('Saved, rendering your edit. It updates in the Clips tab.'); closeEditor(); if (typeof closeModal === 'function') closeModal(); clipsSig = ''; loadClips(); }
  };
  window.edRefreshTemplates = () => {};
})();
