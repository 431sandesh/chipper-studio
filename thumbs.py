"""Chipper Studio - thumbnail designer.

A thumbnail is described by a small "spec" (which frame, zoom, colour pop, headline text, circle, arrow, badge...).
render_thumb() turns a spec into a 1080x1920 JPG with ONE ffmpeg call (frame grab + zoom + colour + an ASS layer
for the text and shapes), so the editor preview and the final cover always look the same.
"""
import math
import os
import re

import engine as E

W, H = 1080, 1920
POSITIONS = ("top", "upper", "middle", "lower", "bottom")
POS_Y = {"top": 330, "upper": 560, "middle": 960, "lower": 1360, "bottom": 1640}

DEFAULT = {
    "t": 0.8, "zoom": 1.0, "fx": 0.5, "fy": 0.5, "pop": 0.35, "bright": 0.0, "vignette": 0.3, "blur": 0.0,
    "text": "", "hl": "", "font": "Arial Black", "size": 150, "color": "#ffffff", "hcolor": "#ffd400",
    "stroke": "#000000", "pos": "top", "box": False, "bcolor": "#ff2d55", "tilt": 0, "upper": True,
    "circle": False, "cx": 540, "cy": 1050, "cr": 230, "ccolor": "#ff2b2b",
    "arrow": False, "ax": 540, "ay": 1050, "aangle": 135, "acolor": "#ff2b2b",
    "badge": "", "badgecolor": "#ff2d55", "frame": 0, "framecolor": "#ffffff",
    "first_frame": False, "clean": False, "src": "clip", "design": "", "text_auto": "", "photos": [],
}


def _num(v, d, lo=None, hi=None):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return d
    if math.isnan(v):
        return d
    if lo is not None:
        v = max(lo, v)
    if hi is not None:
        v = min(hi, v)
    return v


def _col(v, d):
    v = str(v or "")
    return v.lower() if re.fullmatch(r"#[0-9a-fA-F]{6}", v) else d


def clean(spec):
    s = dict(DEFAULT)
    spec = spec if isinstance(spec, dict) else {}
    for k in ("t", "zoom", "fx", "fy", "pop", "bright", "vignette", "blur", "size", "tilt", "cx", "cy", "cr",
              "ax", "ay", "aangle", "frame"):
        if k in spec:
            s[k] = spec[k]
    s["t"] = _num(s["t"], 0.8, 0, 36000)
    s["zoom"] = _num(s["zoom"], 1, 1, 3)
    s["fx"], s["fy"] = _num(s["fx"], 0.5, 0, 1), _num(s["fy"], 0.5, 0, 1)
    s["pop"], s["bright"] = _num(s["pop"], 0.35, 0, 1), _num(s["bright"], 0, -0.5, 0.5)
    s["vignette"], s["blur"] = _num(s["vignette"], 0.3, 0, 1), _num(s["blur"], 0, 0, 1)
    s["size"], s["tilt"] = _num(s["size"], 150, 40, 320), _num(s["tilt"], 0, -20, 20)
    s["cx"], s["cy"], s["cr"] = _num(s["cx"], 540, 0, W), _num(s["cy"], 1050, 0, H), _num(s["cr"], 230, 40, 700)
    s["ax"], s["ay"], s["aangle"] = _num(s["ax"], 540, 0, W), _num(s["ay"], 1050, 0, H), _num(s["aangle"], 135, -360, 360)
    s["frame"] = _num(s["frame"], 0, 0, 60)
    for k in ("color", "hcolor", "stroke", "bcolor", "ccolor", "acolor", "badgecolor", "framecolor"):
        s[k] = _col(spec.get(k), DEFAULT[k])
    s["text"] = re.sub(r"[\r\n{}\\]+", " ", str(spec.get("text") or ""))[:80].strip()
    s["hl"] = re.sub(r"[{}\\]+", "", str(spec.get("hl") or ""))[:60].strip()
    s["badge"] = re.sub(r"[\r\n{}\\]+", " ", str(spec.get("badge") or ""))[:24].strip()
    s["font"] = re.sub(r"[,{}\\]", "", str(spec.get("font") or DEFAULT["font"]))[:60] or DEFAULT["font"]
    s["pos"] = spec.get("pos") if spec.get("pos") in POSITIONS else DEFAULT["pos"]
    for k in ("box", "upper", "circle", "arrow", "first_frame", "clean"):
        s[k] = bool(spec.get(k, DEFAULT[k]))
    s["design"] = str(spec.get("design") or "")[:20]
    s["src"] = "upload" if spec.get("src") == "upload" else "clip"
    s["text_auto"] = spec.get("text_auto") if spec.get("text_auto") in ("full", "short") else ""
    phs = []
    for ph in (spec.get("photos") or [])[:4]:
        if not isinstance(ph, dict) or not re.fullmatch(r"[\w.-]{1,120}\.jpg", str(ph.get("file") or "")):
            continue
        cr = ph.get("crop") if isinstance(ph.get("crop"), list) and len(ph.get("crop")) == 4 else [0, 0, 1, 1]
        cw, ch = _num(cr[2], 1, 0.03, 1), _num(cr[3], 1, 0.03, 1)
        phs.append({"file": ph["file"], "crop": [_num(cr[0], 0, 0, 1 - cw), _num(cr[1], 0, 0, 1 - ch), cw, ch],
                    "x": _num(ph.get("x"), 540, -500, W + 500), "y": _num(ph.get("y"), 960, -500, H + 500),
                    "w": _num(ph.get("w"), 420, 60, 1600), "shape": ph.get("shape") if ph.get("shape") in E.SHAPES else "circle",
                    "border": _num(ph.get("border"), 10, 0, 60), "bcolor": _col(ph.get("bcolor"), "#ffffff"),
                    "flip": bool(ph.get("flip")), "ar": _num(ph.get("ar"), 1, 0.1, 10)})
    s["photos"] = phs
    return s


def _ass_c(hexc, alpha=0):
    h = hexc.lstrip("#")
    return f"&H{alpha:02X}{h[4:6]}{h[2:4]}{h[0:2]}&".upper()


def _circle_path(cx, cy, r):
    k = 0.5523 * r
    return (f"m {cx:.0f} {cy - r:.0f} b {cx + k:.0f} {cy - r:.0f} {cx + r:.0f} {cy - k:.0f} {cx + r:.0f} {cy:.0f} "
            f"b {cx + r:.0f} {cy + k:.0f} {cx + k:.0f} {cy + r:.0f} {cx:.0f} {cy + r:.0f} "
            f"b {cx - k:.0f} {cy + r:.0f} {cx - r:.0f} {cy + k:.0f} {cx - r:.0f} {cy:.0f} "
            f"b {cx - r:.0f} {cy - k:.0f} {cx - k:.0f} {cy - r:.0f} {cx:.0f} {cy - r:.0f}")


def _arrow_path(tx, ty, angle, length=330, shaft=34, head=95):
    """Arrow whose TIP is at (tx, ty), coming from the direction 'angle' (degrees, 0 = from the right)."""
    a = math.radians(angle)
    ux, uy = math.cos(a), math.sin(a)            # from tip towards the tail
    px, py = -uy, ux
    bx, by = tx + ux * head * 1.1, ty + uy * head * 1.1      # base of the head
    ex, ey = tx + ux * length, ty + uy * length               # tail
    pts = [(tx, ty), (bx + px * head * 0.75, by + py * head * 0.75), (bx + px * shaft / 2, by + py * shaft / 2),
           (ex + px * shaft / 2, ey + py * shaft / 2), (ex - px * shaft / 2, ey - py * shaft / 2),
           (bx - px * shaft / 2, by - py * shaft / 2), (bx - px * head * 0.75, by - py * head * 0.75)]
    return "m " + " l ".join(f"{x:.0f} {y:.0f}" for x, y in pts)


def headline_tokens(s):
    text = s["text"].upper() if s["upper"] else s["text"]
    hl = {w.strip(".,!?").upper() for w in re.split(r"\s+", s["hl"]) if w}
    out = []
    for w in text.split():
        out.append((w, w.strip(".,!?").upper() in hl))
    return out


def write_ass(path, s):
    font = E.deva_font(s["font"], s["text"])
    lines = ["[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}", "WrapStyle: 0",
             "ScaledBorderAndShadow: yes", "", "[V4+ Styles]",
             "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
             "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
             "MarginR, MarginV, Encoding",
             f"Style: T,{font},{s['size']:.0f},{_ass_c(s['color'])},&H000000FF&,{_ass_c(s['stroke'])},&H80000000&,"
             f"1,0,0,0,100,100,0,0,1,{max(4, s['size'] / 11):.1f},{max(2, s['size'] / 30):.1f},5,50,50,0,1",
             f"Style: B,{font},{s['size']:.0f},{_ass_c(s['color'])},&H000000FF&,{_ass_c(s['bcolor'])},{_ass_c(s['bcolor'])},"
             f"1,0,0,0,100,100,0,0,3,{s['size'] * 0.16:.0f},0,5,50,50,0,1",
             "Style: D,Arial,20,&H00FFFFFF&,&H000000FF&,&H00000000&,&H00000000&,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1",
             "", "[Events]", "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"]
    ev = lambda layer, style, txt: lines.append(f"Dialogue: {layer},0:00:00.00,0:00:05.00,{style},,0,0,0,,{txt}")
    if s["frame"] > 0:
        f = s["frame"]
        ev(0, "D", f"{{\\an7\\pos(0,0)\\bord0\\shad0\\1c{_ass_c(s['framecolor'])}\\p1}}"
                   f"m 0 0 l {W} 0 l {W} {H} l 0 {H} l 0 0 m {f:.0f} {f:.0f} l {f:.0f} {H - f:.0f} l {W - f:.0f} {H - f:.0f} "
                   f"l {W - f:.0f} {f:.0f} l {f:.0f} {f:.0f}{{\\p0}}")
    if s["circle"]:
        ev(1, "D", f"{{\\an7\\pos(0,0)\\1a&HFF&\\bord{max(8, s['cr'] / 14):.0f}\\3c{_ass_c(s['ccolor'])}\\shad3\\4a&H60&\\p1}}"
                   f"{_circle_path(s['cx'], s['cy'], s['cr'])}{{\\p0}}")
    if s["arrow"]:
        ev(2, "D", f"{{\\an7\\pos(0,0)\\bord7\\3c&HFFFFFF&\\shad4\\4a&H60&\\1c{_ass_c(s['acolor'])}\\p1}}"
                   f"{_arrow_path(s['ax'], s['ay'], s['aangle'])}{{\\p0}}")
    if s["badge"]:
        bw = 40 + len(s["badge"]) * 40
        ev(3, "D", f"{{\\an7\\pos(0,0)\\bord0\\shad6\\4a&H80&\\1c{_ass_c(s['badgecolor'])}\\p1}}"
                   f"m 60 60 l {60 + bw} 60 l {60 + bw - 20} 150 l 40 150{{\\p0}}")
        ev(4, "T", f"{{\\an5\\pos({50 + bw / 2:.0f},105)\\fs70\\bord0\\shad0\\1c&HFFFFFF&}}{s['badge'].upper()}")
    toks = headline_tokens(s)
    if toks:
        y = POS_Y[s["pos"]]
        parts = []
        for w, hi in toks:
            parts.append(f"{{\\1c{_ass_c(s['hcolor'])}}}{w}{{\\1c{_ass_c(s['color'])}}}" if hi else w)
        body = " ".join(parts)
        tags = f"\\an5\\pos(540,{y})\\frz{-s['tilt']:.1f}\\q0"
        ev(5, "B" if s["box"] else "T", f"{{{tags}}}{body}")
    with open(path, "w", encoding="utf-8-sig") as f:
        f.write("\n".join(lines) + "\n")


def photo_size(ph):
    """Output size of a photo window: its width, and a height that keeps the cropped picture's shape."""
    w = E.even(ph["w"])
    if ph["shape"] in ("circle", "heart", "star", "hexagon", "diamond", "squircle"):
        return w, w
    return w, E.even(w * (ph["crop"][3] * 1.0) / max(0.01, ph["crop"][2]) / max(0.01, ph["ar"]))


def render_thumb(video, spec, out_jpg, work_dir, width=W, photos_dir=""):
    """-> (ok, error). Grabs the frame at spec.t from the finished clip (or uses your own picture) and designs
    the thumbnail on it: zoom, colour, photo windows (picture-in-picture) and the text/shape layer."""
    s = clean(spec)
    is_img = video.lower().endswith((".jpg", ".jpeg", ".png", ".webp"))
    dur = 0 if is_img else E.probe_duration(video)
    t = min(s["t"], max(0.0, dur - 0.1)) if dur else s["t"]
    name = os.path.splitext(os.path.basename(out_jpg))[0]
    ass = os.path.join(work_dir, name + ".thumb.ass")
    write_ass(ass, s)
    tmpf = [ass]
    z = s["zoom"]
    zw, zh = E.even(W * z), E.even(H * z)
    x = int(min(max(0, s["fx"] * zw - W / 2), zw - W))
    y = int(min(max(0, s["fy"] * zh - H / 2), zh - H))
    inputs = (([] if is_img else ["-ss", f"{t:.2f}"]) + ["-i", os.path.abspath(video)])
    g = []
    base = f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H}"
    if is_img:
        try:
            iw, ih = E.probe_size(video)
        except Exception:  # noqa: BLE001
            iw, ih = W, H
        if abs((iw / max(1, ih)) / (W / H) - 1) > 0.08:      # not a 9:16 picture: show all of it on a blurred copy
            base = (f"[0:v]split[tb][tf];[tb]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},gblur=sigma=30,"
                    f"eq=brightness=-0.08[tb2];[tf]scale={W}:{H}:force_original_aspect_ratio=decrease[tf2];"
                    f"[tb2][tf2]overlay=(W-w)/2:(H-h)/2")
    chain = [base, f"scale={zw}:{zh}:flags=lanczos", f"crop={W}:{H}:{x}:{y}"]
    if s["blur"] > 0:
        chain.append(f"gblur=sigma={s['blur'] * 12:.1f}")
    p = s["pop"]
    chain.append(f"eq=contrast={1 + 0.35 * p:.2f}:saturation={1 + 0.9 * p:.2f}:brightness={s['bright'] + 0.03 * p:.2f}")
    if p > 0.05:
        chain.append(f"unsharp=5:5:{0.8 * p:.2f}")
    if s["vignette"] > 0.02:
        chain.append(f"vignette=angle=PI/{max(2.2, 5.5 - 3 * s['vignette']):.2f}")
    g.append(",".join(chain) + ",format=rgba[b0]")
    cur, n = "b0", 0
    for ph in s["photos"]:
        path = os.path.join(photos_dir or os.path.dirname(out_jpg), ph["file"])
        if not os.path.isfile(path):
            continue
        n += 1
        pw, ph_h = photo_size(ph)
        inputs += ["-i", os.path.abspath(path)]
        pi = len([a for a in inputs if a == "-i"]) - 1
        cx, cy, cw, ch = ph["crop"]
        flip = ",hflip" if ph["flip"] else ""
        g.append(f"[{pi}:v]crop=iw*{cw:.4f}:ih*{ch:.4f}:iw*{cx:.4f}:ih*{cy:.4f},scale={pw}:{ph_h}:force_original_aspect_ratio=increase,"
                 f"crop={pw}:{ph_h}{flip},format=rgba[p{n}]")
        lab = f"p{n}"
        if ph["shape"] != "rect":
            mp = os.path.join(work_dir, f"{name}_m{n}.pgm")
            E.write_mask(mp, E.shape_points(ph["shape"], pw, ph_h, 0.18), pw, ph_h)
            tmpf.append(mp)
            inputs += ["-i", os.path.abspath(mp)]
            mi = len([a for a in inputs if a == "-i"]) - 1
            g.append(f"[{mi}:v]scale={pw}:{ph_h},format=gray[pm{n}];[p{n}][pm{n}]alphamerge[pa{n}]")
            lab = f"pa{n}"
        px, py = int(ph["x"] - pw / 2), int(ph["y"] - ph_h / 2)
        b = int(ph["border"])
        if b > 0:
            bw_, bh_ = E.even(pw + 2 * b), E.even(ph_h + 2 * b)
            mp = os.path.join(work_dir, f"{name}_b{n}.pgm")
            E.write_mask(mp, E.shape_points(ph["shape"], bw_, bh_, 0.18), bw_, bh_)
            tmpf.append(mp)
            inputs += ["-i", os.path.abspath(mp)]
            mi = len([a for a in inputs if a == "-i"]) - 1
            g.append(f"color=c={ph['bcolor']}:s={bw_}x{bh_},format=rgba[bc{n}];[{mi}:v]scale={bw_}:{bh_},format=gray[bm{n}];"
                     f"[bc{n}][bm{n}]alphamerge[ba{n}];[{cur}][ba{n}]overlay={px - b}:{py - b}:shortest=1[c{n}a]")
            cur = f"c{n}a"
        g.append(f"[{cur}][{lab}]overlay={px}:{py}[c{n}]")
        cur = f"c{n}"
    tail = f"[{cur}]format=yuv420p,{E.ass_filter(os.path.basename(ass), work_dir)}"
    if width != W:
        tail += f",scale={E.even(width)}:-2"
    g.append(tail + "[out]")
    cmd = ["ffmpeg", "-v", "error", "-y"] + inputs + ["-filter_complex", ";".join(g), "-map", "[out]", "-frames:v", "1",
                                                     "-q:v", "2", os.path.abspath(out_jpg)]
    r = E.run(cmd, cwd=work_dir, capture_output=True, text=True)
    for f in tmpf:
        try:
            os.remove(f)
        except OSError:
            pass
    ok = r.returncode == 0 and os.path.isfile(out_jpg)
    return ok, "" if ok else (r.stderr or "ffmpeg failed")[-400:]


# ------------------------------------------------------------------ automatic designs
def headline_from(meta, full=False):
    """Words for the thumbnail, taken from the hook (no emoji, no hashtags): 2-5 punchy words, or the whole hook."""
    src = meta.get("hook") or meta.get("thumb_text") or meta.get("yt_title") or ""
    src = re.sub(r"#\w+|@\w+|https?://\S+", "", src)
    src = re.sub(r"[^\w\s'!?,.\u0900-\u097F-]", " ", src)
    words = [w for w in src.split() if w]
    if not words:
        return "", ""
    if full:
        return " ".join(words[:10]), ""
    words = [w.strip(",.") for w in words]
    words = words if len(words) <= 5 else words[:4]
    hl = max(words, key=len)
    return " ".join(words), hl


def apply_hook(spec, meta):
    """Text that follows the hook ('text_auto') is refreshed from the clip's current hook."""
    s = dict(spec)
    if s.get("text_auto") == "full":
        s["text"], _ = headline_from(meta, full=True)
    elif s.get("text_auto") == "short":
        old_hl = s.get("hl")
        s["text"], hl = headline_from(meta)
        if old_hl is not None and old_hl != "":
            s["hl"] = hl
    return s


def focus_of(scene):
    """Where the streamer's face is in the finished clip (facecam window), as 0-1 coordinates."""
    for l in (scene or {}).get("layers") or []:
        if l.get("kind") == "video" and l.get("role") == "cam" and not l.get("hidden"):
            return (min(1, max(0, (l["x"] + l["w"] / 2) / W)), min(1, max(0, (l["y"] + l["h"] / 2) / H)))
    return None


def designs(meta, duration, scene=None):
    """Ready-made looks for a clip. Each is a full spec the user can then tweak."""
    dur = max(1.0, float(duration or 10))
    text, hl = headline_from(meta)
    full, _ = headline_from(meta, full=True)
    mid = round(min(dur - 0.5, max(1.0, dur * 0.42)), 2)
    face = focus_of(scene)
    hook_t = round(min(dur - 0.2, 0.8), 2)
    out = [
        {"design": "hook", "name": "Hook frame", "t": hook_t, "pop": 0.4, "vignette": 0.35, "clean": True,
         "text": full, "text_auto": "full", "hl": "", "upper": False, "box": True, "bcolor": "#ffffff",
         "color": "#111111", "stroke": "#ffffff", "pos": "upper", "size": 96, "font": "Arial Black"},
        {"design": "headline", "name": "Big headline", "t": mid, "zoom": 1.15, "pop": 0.55, "vignette": 0.45,
         "text": text, "hl": hl, "pos": "top", "size": 160, "tilt": -3, "hcolor": "#ffd400"},
        {"design": "circle", "name": "Circle + arrow", "t": mid, "zoom": 1.1, "pop": 0.5, "vignette": 0.4,
         "text": text, "hl": hl, "pos": "top", "size": 140, "hcolor": "#ff2b2b",
         "circle": True, "cx": 540, "cy": 1080, "cr": 240, "arrow": True, "ax": 380, "ay": 880, "aangle": 225},
        {"design": "boxed", "name": "Red box", "t": round(dur * 0.3, 2), "pop": 0.45, "vignette": 0.3,
         "text": text, "hl": "", "pos": "lower", "size": 120, "box": True, "bcolor": "#ff2d55", "tilt": 2},
        {"design": "react", "name": "Reaction zoom", "t": mid, "zoom": 1.7 if face else 1.35,
         "fx": face[0] if face else 0.5, "fy": face[1] if face else 0.3, "pop": 0.6, "vignette": 0.5,
         "text": text, "hl": hl, "pos": "bottom", "size": 150, "hcolor": "#2ee6c8", "frame": 18, "framecolor": "#ffd400"},
        {"design": "clean", "name": "Clean & bright", "t": mid, "pop": 0.3, "bright": 0.04, "vignette": 0.0,
         "text": text, "hl": "", "pos": "middle", "size": 130, "badge": "WATCH", "badgecolor": "#ff2d55"},
    ]
    for d in out:
        d["clean"] = True               # frame without the burned-in subtitles/hook: the thumbnail draws its own
        if d.get("text") and not d.get("text_auto"):
            d["text_auto"] = "short"
    return [{**clean(d), "name": d["name"]} for d in out]
