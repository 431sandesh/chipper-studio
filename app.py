"""Chipper Studio - local web UI for auto-clipping live streams.

Run:  python app.py   (or double-click run.bat)  ->  http://127.0.0.1:5050
"""
import json
import os
import queue
import shutil
import statistics
import sys
import threading
import time
import webbrowser
from collections import deque

try:
    from flask import Flask, jsonify, request, send_from_directory, abort, redirect, make_response
except ImportError:
    sys.exit("Flask is missing. Run:  python -m pip install flask")

# The Windows installer puts ffmpeg, ffprobe and cloudflared in a "bin" folder next to Chipper
_BIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bin")
if os.path.isdir(_BIN):
    os.environ["PATH"] = _BIN + os.pathsep + os.environ.get("PATH", "")

import hashlib
import zipfile
import urllib.error
import urllib.request
import hmac
import re
import secrets
import subprocess

import engine as E
import thumbs as TH
import nvidia_enhance as NV
import posting as P
import tiktok_web as TW
import discover as DS

HERE = os.path.dirname(os.path.abspath(__file__))
WORK = os.path.join(HERE, "work")
DIRS = {k: os.path.join(WORK, k) for k in ("rec", "raw", "meta", "thumbs", "tmp", "vod", "assets", "enh")}
# Everything uploaded (from the phone app or the browser) is kept in one place you can open on the PC:
#   uploads\audio   background music      uploads\fx      your own sound effects
#   uploads\images  pictures and logos    uploads\other   any other file
UPLOADS = os.path.join(HERE, "uploads")
UP = {k: os.path.join(UPLOADS, k) for k in ("audio", "fx", "images", "other")}
for _d in UP.values():
    os.makedirs(_d, exist_ok=True)


ALIASES_F = os.path.join(UPLOADS, "aliases.json")


def _aliases():
    try:
        with open(ALIASES_F, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def asset_path(fname):
    """Where an editor file really is: the uploads folders first, then the older work/assets folder.
    A duplicate that was removed points to the copy that was kept, so old clips still render."""
    fname = os.path.basename(fname or "")
    fname = _aliases().get(fname, fname)
    for d in (UP["images"], UP["audio"], UP["other"], DIRS["assets"]):
        p = os.path.join(d, fname)
        if fname and os.path.isfile(p):
            return p
    return os.path.join(DIRS["assets"], fname)


def safe_name(fn, keep=80):
    base, ext = os.path.splitext(os.path.basename(fn or "file"))
    return re.sub(r"[^A-Za-z0-9_.-]", "_", base)[:keep] + re.sub(r"[^A-Za-z0-9.]", "", ext.lower())[:8]
for d in DIRS.values():
    os.makedirs(d, exist_ok=True)
SETTINGS_F = os.path.join(HERE, "settings.json")
TEMPLATES_F = os.path.join(HERE, "templates.json")
FRAME = os.path.join(DIRS["tmp"], "frame.jpg")
PORT = 5050


def default_out():
    if sys.platform == "darwin":
        return os.path.join(os.path.expanduser("~"), "Movies", "Chipper Clips")
    for var in ("OneDrive", "OneDriveConsumer"):
        if os.environ.get(var) and os.path.isdir(os.environ[var]):
            return os.path.join(os.environ[var], "Clips")
    return os.path.join(HERE, "clips")


DEFAULT_SETTINGS = {
    "out_dir": default_out(), "model": "base", "lang": "en", "min": 5, "ratio": 2.5,
    "pre": 20, "post": 10, "cooldown": 30, "captions": True, "delay": 0.0,
    "auto_extend": True, "max_len": 60, "style_scene": None, "style_on": False,
    "yt_cookies": "auto", "campaign_brief": "", "rec_quality": "best", "thumb_auto": "hook", "enhance_default": False, "enhance_quality": "HIGH", "word_fixes": {}, "avoid_words": "", "yt_captions": True, "use_ratings": True,
    "post_daily_cap": 0, "post_gap_min": 0, "enhance_sharpen": "medium", "render_quality": "high", "speech": "", "stt_fix": True, "cap_lang": "", "meta_lang": "en",
    "hashtags": "#gaming #clips #fyp #viral #streamer", "api_key": "", "ai_model": "",
    "ai_provider": "builtin", "ai_key": "",
    "remote_password": "", "remote_email": "", "remote_auto": True,
    # permanent link: a Cloudflare named tunnel (token from the Cloudflare dashboard) and its address
    "remote_tunnel_token": "", "remote_tunnel_host": "",
    "ntfy_topic": "", "ntfy_server": "https://ntfy.sh", "notify_link": True, "notify_clips": True,
    "last_logo": "",
    "stt": "auto", "stt_auto_model": "turbo", "groq_key": "", "smart_trim": True, "ollama_url": "http://127.0.0.1:11434",
    "template": "Default", "keep_recordings": True,
}


def load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    for attempt in range(12):            # Windows: a reader or antivirus can hold the file for a moment
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 11:
                raise
            time.sleep(0.05 * (attempt + 1))


settings = {**DEFAULT_SETTINGS, **load_json(SETTINGS_F, {})}
E.RENDER_QUALITY = settings.get("render_quality") or "high"
if not settings.get("ntfy_topic"):          # private, hard-to-guess topic name for phone notifications
    settings["ntfy_topic"] = "chipper-" + __import__("secrets").token_hex(8)
    save_json(SETTINGS_F, settings)
templates = load_json(TEMPLATES_F, {}) or {"Default": dict(E.DEFAULT_TEMPLATE)}
meta_lock = threading.Lock()
logs = deque(maxlen=300)


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    logs.append(line)
    print(line, flush=True)


def notify(title, message, click=None, tags=None):
    """Push a notification to your phone via ntfy (free app). Never blocks clipping."""
    topic = (settings.get("ntfy_topic") or "").strip()
    if not topic:
        return

    def send():
        body = {"topic": topic, "title": title[:120], "message": message[:400]}
        if click:
            body["click"] = click
        if tags:
            body["tags"] = tags
        try:
            req = __import__("urllib.request").request.Request(
                (settings.get("ntfy_server") or "https://ntfy.sh").rstrip("/"),
                data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
            __import__("urllib.request").request.urlopen(req, timeout=15).read()
        except Exception as e:  # noqa: BLE001
            log(f"phone notification failed: {e}")
    threading.Thread(target=send, daemon=True).start()


def out_dir():
    d = settings["out_dir"] or default_out()
    os.makedirs(d, exist_ok=True)
    return d


def active_template():
    return E.merged_template(templates.get(settings.get("template")) or templates.get("Default"))


# ------------------------------------------------------------------ clip metadata
def meta_path(name):
    return os.path.join(DIRS["meta"], name + ".json")


def read_meta(name):
    return load_json(meta_path(name), None)


def write_meta(m):
    with meta_lock:
        save_json(meta_path(m["name"]), m)


def update_meta(name, **kw):
    with meta_lock:
        m = load_json(meta_path(name), {"name": name})
        m.update(kw)
        save_json(meta_path(name), m)
    return m


def write_post_txt(m):
    with open(os.path.join(out_dir(), m["name"] + ".txt"), "w", encoding="utf-8") as f:
        f.write(f"{m.get('caption', '')}\n\n{' '.join(m.get('hashtags', []))}\n")


# ------------------------------------------------------------------ worker
jobs = queue.Queue()


def clamp_len(meta, plan, dur):
    """Respect a campaign's maximum clip length."""
    mx, mn = (plan or {}).get("max_len"), (plan or {}).get("min_len")
    if dur and (mx or mn):
        a = meta.get("trim_start") or 0.0
        b = meta.get("trim_end") or dur
        if mx and b - a > mx:
            b = a + mx
        if mn and b - a < mn:                 # too short for the campaign: grow it, after first, then before
            b = min(dur, a + mn)
            a = max(0.0, b - mn)
        meta["trim_start"], meta["trim_end"] = round(a, 2), round(b, 2)
    return meta


def make_ctx(url, kind="", sid="", brief="", genre="auto", info=None):
    """What the AI should know about the source: real title + channel (never internal ids) and the campaign rules."""
    info = info if info is not None else E.source_info(url)
    ch = info.get("channel") or (sid if kind == "twitch" else "@" + sid if kind == "tiktok" and sid else "")
    plan = E.plan_from_brief(settings, brief, info, genre, log) if (brief or genre not in ("", "auto")) else \
        {**E.offline_plan("", info), "genre": "stream" if kind in ("twitch", "youtube", "tiktok") and info.get("live", True) else
         E.guess_genre(info.get("title", ""), info.get("categories"), info.get("live"))}
    return {"title": info.get("title", ""), "channel": ch, "description": info.get("description", "")[:600],
            "genre": plan.get("genre", "stream"), "brief": (brief or "")[:2500], "plan": plan,
            "lang": "en"}                    # post text is always English; Nepali is only for on-video subtitles


TASTE = {"at": 0, "text": ""}


def taste_notes():
    """Short summary of the clips the owner rated - the AI uses it to score new moments the same way."""
    if time.time() - TASTE["at"] < 300:
        return TASTE["text"]
    rated = []
    for f in os.listdir(DIRS["meta"]):
        if f.endswith(".json"):
            m = load_json(os.path.join(DIRS["meta"], f), None) or {}
            if m.get("rating"):
                rated.append(m)
    rated.sort(key=lambda m: -(m.get("rated_at") or 0))
    line = lambda m: (f"- {m['rating']}/5: \"{(m.get('hook') or '')[:60]}\" ({m.get('channel', '')}, "
                      f"{round(m.get('length') or m.get('duration') or 0)} s) speech: {(m.get('transcript') or '')[:110]}")
    good = [line(m) for m in rated if m["rating"] >= 4][:8]
    bad = [line(m) for m in rated if m["rating"] <= 2][:6]
    text = ""
    if len(good) + len(bad) >= 3:
        text = ("Liked:\n" + "\n".join(good) if good else "") + ("\nDid NOT like:\n" + "\n".join(bad) if bad else "")
    TASTE.update(at=time.time(), text=text.strip())
    return TASTE["text"]


def with_lang(ctx):
    """Titles/captions language: the setting at the time the clip is made (plus the owner's star-rating taste)."""
    c = dict(ctx or {})
    c["lang"] = "en"
    if settings.get("use_ratings", True):
        try:
            c["taste"] = taste_notes()
        except Exception:  # noqa: BLE001
            pass
    return c


def cap_lang_words(words, name=""):
    """On-screen captions in another language (e.g. Nepali): translate the timed words. -> (shown, original or None)"""
    lang = settings.get("cap_lang") or ""
    if not words or not lang or lang == (settings.get("lang") or "en"):
        return words, None
    if lang in ("ne", "ne-mix", "hi") and sum(bool(E.DEVA.search(w[2])) for w in words) > len(words) / 2:
        return words, None                    # already spoken + transcribed in Devanagari
    try:
        return E.translate_words(settings, words, lang, log), words
    except Exception as e:  # noqa: BLE001
        log(f"could not translate captions of {name}: {str(e)[:100]}")
        return words, None


def process_clip(task):
    kind = task["kind"]
    name = task["name"]
    raw = os.path.join(DIRS["raw"], name + ".mp4")
    out = os.path.join(out_dir(), name + ".mp4")
    try:
        if kind == "new":
            while time.time() < task["ready_at"]:
                time.sleep(1)
            update_meta(name, status="waiting for the recording")
            start, length = E.wait_for_recording(task["rec"], task["start"], task["length"], log=log)
            if abs(start - task["start"]) > 1:
                log(f"{name}: the recording never caught up - clipped the last {length:.0f} s that were saved instead")
            update_meta(name, status="cutting")
            if not E.cut_raw(task["rec"], start, length, raw):
                raise RuntimeError("could not cut this moment from the recording - it may not have been saved "
                                   "(slow internet). Try Settings - Recording quality: 720p")
            words = None
            if settings["captions"]:
                update_meta(name, status="transcribing")
                tctx = task.get("ctx") or {}
                words = E.transcribe_auto(raw, DIRS["tmp"], settings, log, speech=tctx.get("speech") or None,
                                          context=" - ".join(x for x in (tctx.get("channel"), tctx.get("title")) if x))
                if words is None:
                    log(f"captions failed for {name}: {E.LAST_WHISPER_ERROR[0]}")
                    update_meta(name, caption_error=E.LAST_WHISPER_ERROR[0])
                elif not words:
                    log(f"no speech found in {name} (no captions)")
            transcript = " ".join(w[2] for w in words or [])
            update_meta(name, status="thinking")
            dur = E.probe_duration(raw)
            meta = clamp_len(E.make_meta(settings, words or [], task.get("chat", []), task["channel"], dur, log,
                                         ctx=with_lang(task.get("ctx"))), (task.get("ctx") or {}).get("plan"), dur)
            if meta.get("model_note"):
                log(f"AI note: {meta['model_note']}")
            shown, orig = cap_lang_words(words or [], name)
            extra = {"words_orig": orig, "cap_lang": settings.get("cap_lang")} if orig else {}
            m = update_meta(name, words=shown or [], transcript=transcript, duration=round(dur, 2), **meta, **extra)
            if meta.get("score") is not None:
                log(f"{name}: score {meta['score']}/10, trimmed to {meta['trim_start']:.1f}-{meta['trim_end']:.1f}s - {meta.get('why', '')}")
            if settings.get("enhance_default") and VSR.ready():      # Settings: enhance every new clip
                update_meta(name, status="AI enhancing")
                ENH[name] = {"state": "queued", "pct": 0, "quality": settings.get("enhance_quality") or "HIGH",
                             "step": "queued", "started": time.time()}
                enhance_job(name, ENH[name]["quality"])
                m = read_meta(name) or m
        else:   # re-render with new template / hook
            m = read_meta(name)
            if not m or not os.path.exists(raw):
                raise RuntimeError("source for this clip is gone")
            if task.get("regen"):
                update_meta(name, status="writing hook")
                dur = m.get("duration") or E.probe_duration(raw)
                meta = clamp_len(E.make_meta(settings, [tuple(w) for w in (m.get("words_orig") or m.get("words", []))],
                                             m.get("chat", []), m.get("channel", ""), dur, log, ctx=with_lang(m.get("ctx"))),
                                 (m.get("ctx") or {}).get("plan"), dur)
                m = update_meta(name, **meta)
        tpl = E.merged_template(task.get("template") or m.get("template") or active_template())
        scene = m.get("scene")
        lay = ((task.get("ctx") or {}).get("layout") or "auto") if kind == "new" else "auto"
        if kind == "new" and saved_layout(lay):
            scene = saved_layout(lay)
        elif kind == "new" and lay in ("fit", "fill"):
            scene = E.clean_scene(E.layout_scene(lay, tpl))
        elif kind == "new" and lay != "template" and settings.get("style_on") and isinstance(settings.get("style_scene"), dict):
            scene = E.clean_scene(E.style_to_scene(settings["style_scene"], tpl))
        if kind == "new" or not scene:
            try:
                sw, sh = E.probe_size(raw)
            except Exception:  # noqa: BLE001
                sw, sh = 16, 9
            if sh > sw:      # vertical source (TikTok live, phone stream): show the whole frame, no facecam split
                full = {"kind": "video", "id": "v1", "name": "Video", "role": "custom", "crop": [0, 0, 1, 1],
                        "x": 0, "y": 0, "w": 1080, "h": 1920, "shape": "rect", "radius": 0.12, "border": 0,
                        "bcolor": "#ffffff"}
                base = scene or E.layout_scene("fill", tpl)       # keep my-style captions, thirds and branding
                base["layers"] = [full] + [l for l in base["layers"] if l.get("kind") != "video"]
                scene = E.clean_scene(base)
        update_meta(name, status="rendering", template=tpl, scene=scene)
        words = [tuple(w) for w in m.get("words", [])] if settings["captions"] or kind != "new" else []
        if scene:
            src, st, en, sc2, w2, tmpcut = cut_source(name, m, raw, scene, words)
            update_meta(name, status="rendering", progress=0)
            # the app shows a progress bar for this clip: how far ffmpeg has got
            E.PROGRESS.cb = lambda f, n=name: update_meta(n, progress=round(f * 100))
            try:
                ok, err = E.render_scene(src, out, sc2, w2, m.get("hook"), DIRS["tmp"],
                                         start=st, end=en, assets_dir=DIRS["assets"])
            finally:
                E.PROGRESS.cb = None
                if tmpcut and os.path.isfile(tmpcut):
                    os.remove(tmpcut)
        else:
            logo = None
            if (m.get("logo") or {}).get("enabled") and m["logo"].get("file"):
                logo = {**m["logo"], "path": asset_path(m["logo"]["file"])}
            src, st, en, w2 = raw, m.get("trim_start") or 0, m.get("trim_end"), words
            ok, err = E.render(src, out, tpl, w2, m.get("hook") if tpl["hook"] else None, DIRS["tmp"],
                               start=st, end=en, overlays=m.get("overlays") or [], logo=logo)
        if not ok:
            raise RuntimeError("render failed: " + err)
        E.make_thumb(out, os.path.join(DIRS["thumbs"], name + ".jpg"))
        m = update_meta(name, status="done", error="", rendered=time.time())
        try:
            make_cover(name, m)
        except Exception as e:  # noqa: BLE001
            log(f"{name}: thumbnail failed: {e}")
        write_post_txt(m)
        if kind == "new" or task.get("notify"):
            if settings.get("notify_clips", True):
                sc = f"{m['score']}/10 · " if m.get("score") else ""
                notify(f"✂ New clip: {m.get('hook') or name}"[:120], f"{sc}{m.get('channel', '')} · tap to review and post",
                       click=(tunnel.url + "/#clip=" + name) if tunnel.url else None, tags=["scissors"])
        log(f"clip ready: {name}.mp4" + (f'  hook: "{m.get("hook")}"' if m.get("hook") else ""))
    except Exception as e:  # noqa: BLE001
        update_meta(name, status="error", error=str(e))
        log(f"clip {name} failed: {e}")


WORKING = {"render": 0, "post": 0}      # what is running right now (the updater waits for these)


def worker():
    while True:
        job = jobs.get()
        WORKING["render"] += 1
        try:
            process_clip(job)
        finally:
            WORKING["render"] -= 1


threading.Thread(target=worker, daemon=True).start()


# ------------------------------------------------------------------ live job
def delete_later(path, wait=180):
    """give pending clips time to be cut, then remove the big recording"""
    time.sleep(wait)
    for p in (path, path + ".log"):
        try:
            os.remove(p)
        except OSError:
            pass


class Job:
    def __init__(self, slot):
        self.slot = slot
        self.name = ""
        self.template_name = ""
        self.lock = threading.Lock()
        self.state = "idle"        # idle | starting | live | stopping
        self.url = ""
        self.title = ""
        self.rec = None
        self.chat = None
        self.chart = deque(maxlen=360)      # (time, msgs, spike) per 10 s
        self.stop_flag = threading.Event()
        self.clips = 0
        self.ctx = {}

    def display(self):
        """The streamer's name once it's known (YouTube channel, Twitch or TikTok name), else the stream's id."""
        ch = ((self.ctx or {}).get("channel") or "").strip()
        return ch or self.name

    def info(self):
        hist = [c[1] for c in self.chart]
        return {
            "slot": self.slot, "state": self.state, "url": self.url, "name": self.display(), "id_name": self.name,
            "title": ((self.ctx or {}).get("title") or "")[:120],
            "template": self.template_name,
            "chat_connected": bool(self.chat and self.chat.connected),
            "recording": self.rec.path if self.rec else "",
            "since": self.rec.start_time if self.rec and self.rec.start_time else None,
            "chart": list(self.chart)[-90:],
            "median": statistics.median(hist[-18:]) if hist else 0,
            "clips": self.clips,
            "brief": getattr(self, "brief", ""), "layout": getattr(self, "layout", "auto"),
            "length": getattr(self, "length", ""),
            "campaign": self.campaign_summary() if getattr(self, "brief", "") or getattr(self, "length", "") else "",
        }

    def log(self, msg):
        log(f"[{self.name or 'stream ' + str(self.slot)}] {msg}")

    def template(self):
        t = templates.get(self.template_name) if self.template_name else None
        return E.merged_template(t) if t else active_template()

    def start(self, src, template_name="", brief=None, layout="auto", length="", speech=""):
        with self.lock:
            if self.state != "idle":
                raise RuntimeError("already running - stop first")
            kind, sid, url, name = E.parse_source(src)
            for other in JOBS.values():
                if other is not self and other.state != "idle" and other.url == url:
                    raise RuntimeError(f"that stream is already running in stream {other.slot}")
            self.template_name = template_name if template_name in templates else ""
            self.kind, self.sid, self.url, self.name = kind, sid, url, name
            self.brief = (settings.get("campaign_brief", "") if brief is None else brief or "").strip()[:4000]
            self.layout = layout if layout in ("auto", "template", "fit", "fill") else "auto"
            self.length = length if re.fullmatch(r"\d{1,3}-\d{1,3}", length or "") else ""
            self.state = "starting"
            self.stop_flag.clear()
            self.chart.clear()
            self._gap_warned, self._gap_check = False, 0
            self.clips = 0
            self.speech = speech if speech in E.SPEECH else ""
            self.ctx = {"channel": sid if kind == "twitch" else "@" + sid if kind == "tiktok" else "", "genre": "stream",
                        "brief": self.brief, "plan": self.quick_plan(), "layout": self.layout, "speech": self.speech}
        threading.Thread(target=self.run, daemon=True).start()
        threading.Thread(target=self.load_ctx, args=(kind, sid, url), daemon=True).start()

    def quick_plan(self):
        """Rules readable straight away (no AI) so the first clips already follow the campaign."""
        plan = E.offline_plan(self.brief, {}) if self.brief else {}
        if self.length:
            plan["min_len"], plan["max_len"] = map(int, self.length.split("-"))
        return plan

    def load_ctx(self, kind, sid, url):
        try:
            ctx = make_ctx(url if kind != "tiktok" else "", kind, sid, self.brief, "stream")
            if self.length:
                ctx["plan"]["min_len"], ctx["plan"]["max_len"] = map(int, self.length.split("-"))
            ctx["genre"] = "stream"
            ctx["layout"] = self.layout
            ctx["speech"] = getattr(self, "speech", "")
            self.ctx = ctx
            if ctx.get("title"):
                self.log(f"stream: {ctx['title'][:60]}" + (f" by {ctx['channel']}" if ctx.get("channel") else ""))
            if self.brief or self.length:
                self.log("campaign: " + self.campaign_summary())
        except Exception as e:  # noqa: BLE001
            self.log(f"could not read stream info: {e}")

    def campaign_summary(self):
        p = (self.ctx or {}).get("plan") or {}
        bits = []
        if p.get("min_len") or p.get("max_len"):
            bits.append(f"{p.get('min_len') or '?'}-{p.get('max_len') or '?'} s clips")
        if p.get("hashtags"):
            bits.append(" ".join(p["hashtags"][:6]))
        if p.get("mentions"):
            bits.append("tag " + " ".join(p["mentions"][:3]))
        if p.get("cta"):
            bits.append(f'"{p["cta"][:40]}"')
        if p.get("focus"):
            bits.append("clip: " + p["focus"][:80])
        if self.layout != "auto":
            bits.append({"template": "my template", "fit": "whole video", "fill": "fill screen"}[self.layout])
        return ", ".join(bits) or "brief saved"

    def set_campaign(self, brief, layout="auto", length=""):
        """Change the campaign while the stream is running: clips made from now on follow the new rules."""
        if self.state == "idle":
            raise RuntimeError("that stream is not running")
        self.brief = (brief or "").strip()[:4000]
        self.layout = layout if layout in ("auto", "template", "fit", "fill") else "auto"
        self.length = length if re.fullmatch(r"\d{1,3}-\d{1,3}", length or "") else ""
        self.ctx = {**(self.ctx or {}), "brief": self.brief, "plan": self.quick_plan(), "layout": self.layout,
                    "speech": getattr(self, "speech", "")}
        self.log("campaign updated - new clips follow it")
        threading.Thread(target=self.load_ctx, args=(self.kind, self.sid, self.url), daemon=True).start()

    def stop(self):
        if self.state in ("starting", "live"):
            self.state = "stopping"
            self.stop_flag.set()

    def offset_now(self):
        return time.time() - self.rec.start_time - float(settings.get("delay") or 0)

    def enqueue(self, n, manual=False, start=None, end=None, t_first=None):
        """start/end = seconds into the recording. Manual clips use pre/post around now."""
        now = time.time()
        off = self.offset_now()
        pre, post = float(settings["pre"]), float(settings["post"])
        if start is None:
            start, end = max(0.0, off - pre), off + post
        t_first = t_first or now
        plan = (self.ctx or {}).get("plan") or {}
        lo, hi = plan.get("min_len"), plan.get("max_len")
        if lo and end - start < lo + 3:             # campaign needs longer clips: take more before and after the moment
            need = lo + 3 - (end - start)
            back = min(start, need * 0.6)
            start, end = start - back, end + (need - back)
        if hi and end - start > hi + 8:              # keep a little extra so the AI can pick the best hi seconds
            mid = start + (end - start) * 0.45
            start, end = max(0.0, mid - (hi + 8) / 2), max(0.0, mid - (hi + 8) / 2) + hi + 8
        length = max(5.0, end - start)
        name = f"{self.name}_{time.strftime('%Y%m%d_%H%M%S', time.localtime(t_first))}" + \
            ("_manual" if manual else f"_{n}msgs")
        clip_start_wall = now - max(0.0, off - start) - float(settings.get("delay") or 0)
        chat = self.chat.recent_timed(clip_start_wall, now) if self.chat else []
        write_meta({"name": name, "created": now, "status": "waiting", "channel": self.display(),
                    "source": self.url, "msgs": n, "manual": manual, "chat": chat[-150:],
                    "length": round(length), "template": self.template(), "ctx": self.ctx,
                    "campaign": (getattr(self, "brief", "") or "")[:300],
                    "template_name": self.template_name or settings.get("template")})
        jobs.put({"kind": "new", "name": name, "rec": self.rec.path, "start": start,
                  "length": length, "ready_at": now + max(0.0, end - off) + 4,
                  "chat": chat, "channel": self.display(), "template": self.template(), "ctx": self.ctx})
        self.clips += 1
        self.log(("MANUAL CLIP" if manual else f"CLIP {round(length)} s, peak {n} msgs") + f" -> {name}")

    def run(self):
        self.log(f"starting {self.url}")
        self.rec = E.Recorder(self.url, self.name, DIRS["rec"], settings.get("rec_quality") or "best")
        if not self.rec.start(lambda: not self.stop_flag.is_set()):
            self.log("could not record: " + (self.rec.error_tail() or "stream offline or wrong link?"))
            self.rec.stop()
            self.state = "idle"
            return
        self.log(f"recording -> {os.path.basename(self.rec.path)}")
        self.state = "live"
        self.chat = {"youtube": E.YouTubeChat, "tiktok": E.TikTokChat}.get(self.kind, E.TwitchChat)(self.sid, self.log)
        self.chat.start()
        last_clip = -1e9
        pending = None       # clip being extended while chat stays hot
        next_tick = time.time() + 10
        while self.rec.alive() and not self.stop_flag.is_set():
            if self.stop_flag.wait(max(0, next_tick - time.time())):
                break
            next_tick += 10
            n = self.chat.take()
            now = time.time()
            hist = [c[1] for c in self.chart][-18:]
            thr = max(int(settings["min"]), float(settings["ratio"]) * max(statistics.median(hist), 1)) \
                if hist else 1e9
            spike = len(hist) >= 6 and n >= thr
            off = self.offset_now()
            pre, post = float(settings["pre"]), float(settings["post"])
            max_len = max(15.0, float(settings.get("max_len") or 60))
            cmax = ((self.ctx or {}).get("plan") or {}).get("max_len")
            if cmax:
                max_len = max(10.0, float(cmax) + 8)
            hot = False
            if pending:
                # still hyped? keep the clip running (chat >= 60% of the spike line)
                if settings.get("auto_extend") and n >= 0.6 * thr:
                    pending["end"] = min(off + post, pending["start"] + max_len)
                    pending["peak"] = max(pending["peak"], n)
                    hot = True
                if off >= pending["end"] or pending["end"] - pending["start"] >= max_len and not hot:
                    self.enqueue(pending["peak"], start=pending["start"], end=pending["end"], t_first=pending["t"])
                    pending, last_clip = None, now
                elif off >= pending["end"]:
                    pass
            elif spike and now - last_clip >= float(settings["cooldown"]):
                pending = {"start": max(0.0, off - pre), "end": off + post, "peak": n, "t": now}
                self.log(f"SPIKE {n} msgs (line {thr:.0f}) - recording the moment"
                    + (", will extend while chat stays hot" if settings.get("auto_extend") else ""))
                hot = True
                if not settings.get("auto_extend"):
                    self.enqueue(n, start=pending["start"], end=pending["end"], t_first=now)
                    pending, last_clip = None, now
            self.chart.append((now, n, spike or hot))
            if not getattr(self, "_gap_warned", False) and now - getattr(self, "_gap_check", 0) > 60:
                self._gap_check = now
                try:
                    with open(self.rec.path + ".log", encoding="utf-8", errors="replace") as fh:
                        gaps = fh.read().count("Sequence gap")
                except OSError:
                    gaps = 0
                if gaps >= 3:
                    self._gap_warned = True
                    self.log(f"internet is too slow for this stream's quality ({gaps} skipped parts) - clips may wait or "
                             "miss moments. Tip: Settings - Recording quality: 720p")
        if pending:
            self.enqueue(pending["peak"], start=pending["start"],
                         end=min(pending["end"], self.offset_now()), t_first=pending["t"])
        self.log("stream ended" if not self.stop_flag.is_set() else "stopped")
        self.chat.running = False
        self.rec.stop()
        if not settings.get("keep_recordings") and self.rec and os.path.exists(self.rec.path):
            # wait until pending clips have been cut, then delete the big recording
            threading.Thread(target=delete_later, args=(self.rec.path,), daemon=True).start()
        self.state = "idle"


MAX_STREAMS = 3
JOBS = {i: Job(i) for i in range(1, MAX_STREAMS + 1)}


def get_job(data):
    try:
        return JOBS[int((data or {}).get("slot") or 1)]
    except (KeyError, ValueError):
        raise RuntimeError("unknown stream slot") from None

# ------------------------------------------------------------------ finished streams (VOD)
class VodJob:
    """Clip a finished stream / video: download (or upload) -> find moments -> AI picks the best -> render."""

    def __init__(self):
        self.state = "idle"       # idle | downloading | analyzing | picking | rendering | done | error
        self.pct, self.msg, self.title, self.source = 0.0, "", "", ""
        self.found, self.made = 0, []
        self.stop_flag = threading.Event()
        self.thread = None

    def info(self):
        return {"state": self.state, "pct": round(self.pct, 1), "msg": self.msg, "title": self.title,
                "source": self.source, "found": self.found, "made": self.made}

    def busy(self):
        return self.state in ("downloading", "analyzing", "picking", "rendering")

    def start(self, url=None, file_path=None, clips=5, template="", name_hint="", brief="", genre="auto",
              layout="auto", length="", speech=""):
        if self.busy():
            raise RuntimeError("already clipping a video - wait or press Stop")
        self.opts = {"brief": (brief or "").strip()[:4000], "genre": genre or "auto", "layout": layout or "auto",
                     "length": length or "", "speech": speech if speech in E.SPEECH else ""}
        self.stop_flag.clear()
        self.state, self.pct, self.msg, self.found, self.made = "downloading", 0.0, "starting", 0, []
        self.source = url or os.path.basename(file_path or "")
        self.title = ""
        self.thread = threading.Thread(target=self.run, args=(url, file_path, max(1, min(int(clips), 20)),
                                                               template, name_hint), daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_flag.set()

    def set(self, state=None, pct=None, msg=None):
        if state:
            self.state = state
        if pct is not None:
            self.pct = pct
        if msg is not None:
            self.msg = msg

    def run(self, url, file_path, count, template, name_hint):
        try:
            stamp = time.strftime("%Y%m%d_%H%M%S")
            kind = "upload"
            if url:
                kind, sid, _, short = E.parse_source(url) if re.search(r"youtu|twitch", url) else ("other", "", url, "vod")
                short = {"twitch": "twitch", "youtube": short}.get(kind, "video") + "_vod"
                if kind == "twitch" and "/videos/" not in url:
                    raise RuntimeError("for Twitch, paste the link of the past broadcast (twitch.tv/videos/...)")
                base = os.path.join(DIRS["vod"], f"{short}_{stamp}")
                sync_ytauth()
                video, self.title = E.download_vod(url, base, lambda p, m: self.set(pct=p, msg=m),
                                                   stop=self.stop_flag.is_set, log=log)
                short_name = short
            else:
                video = file_path
                base = os.path.splitext(file_path)[0]
                self.title = name_hint or os.path.basename(file_path)
                short_name = re.sub(r"[^\w]+", "_", os.path.splitext(name_hint or "upload")[0])[:24].strip("_") or "upload"
            if self.stop_flag.is_set():
                raise RuntimeError("stopped")
            o = getattr(self, "opts", {}) or {}
            self.set("analyzing", 0, "reading the video and the campaign brief")
            info = E.source_info(url) if url else {"title": os.path.splitext(self.title)[0]}
            if not info.get("title"):
                info["title"] = self.title
            ctx = make_ctx(url, kind, "", o.get("brief", ""), o.get("genre", "auto"), info)
            plan = ctx["plan"]
            if o.get("length") and re.fullmatch(r"\d+-\d+", o["length"]):
                plan["min_len"], plan["max_len"] = map(int, o["length"].split("-"))
            genre = plan.get("genre", "stream")
            log(f"[vod] {self.title[:50]}: looks like {E.GENRES.get(genre, E.GENRES['vlog'])[0]}"
                + (f", campaign: {len(plan.get('hashtags', []))} required tags" if o.get("brief") else ""))
            self.set(msg="reading chat replay")
            chat = E.chat_replay(url, base, kind) if url else []
            self.set(msg="measuring loudness" + (f" ({len(chat)} chat messages found)" if chat else " (no chat replay)"))
            dur = E.probe_duration(video)
            if dur < 20:
                raise RuntimeError("video is too short to clip")
            loud = E.loudness_curve(video)
            smart = bool(settings.get("ai_provider") not in ("", "builtin") and
                         (settings.get("ai_key") or settings.get("ai_provider") == "ollama"))
            n_cand = count * 2 if smart else count
            cands, has_chat = [], bool(chat)
            if genre in ("podcast", "business") and smart:
                cands = E.talk_candidates(video, dur, {**settings, **({"speech": o["speech"]} if o.get("speech") else {})},
                                          plan, count, DIRS["tmp"], log,
                                          lambda p, m: self.set(pct=p, msg=m), self.stop_flag.is_set, o.get("brief", ""))
            if not cands:
                span = float(plan.get("max_len") or (50 if genre == "stream" else 40)) + 10
                cands, has_chat = E.find_candidates(dur, chat, loud, n_cand, pre=span * 0.6, post=span * 0.4)
            self.found = len(cands)
            if not cands:
                raise RuntimeError("no stand-out moments found (no chat spikes or loud peaks)")
            log(f"[vod] {self.title[:50]}: {len(cands)} candidate moments from "
                f"{'chat + loudness' if has_chat else 'loudness'} in {dur / 60:.0f} min")
            ytcap = None
            if kind == "youtube" and settings["captions"] and settings.get("yt_captions", True):
                self.set(msg="getting YouTube's own captions")
                sp = E.SPEECH.get(E.speech_code(settings, o.get("speech") or None), (None, ""))[0]
                ytcap = E.youtube_captions(url, base, sp, log)
                log(f"[vod] {self.title[:50]}: " + (f"using YouTube's own captions ({ytcap['lang']}, {len(ytcap['words'])} words)"
                                                     if ytcap else "no YouTube captions - making captions from the sound"))
            # phase 1: cut, transcribe and let the AI judge every candidate
            self.set("picking", 0)
            tpl = E.merged_template(templates.get(template)) if template in templates else active_template()
            scored = []
            for k, c in enumerate(cands):
                if self.stop_flag.is_set():
                    raise RuntimeError("stopped")
                self.set(pct=100 * k / len(cands), msg=f"judging moment {k + 1} of {len(cands)} "
                                                        f"(at {int(c['peak'] // 60)}:{int(c['peak'] % 60):02d})")
                name = f"{short_name}_{stamp}_{int(c['peak'])}s"
                raw = os.path.join(DIRS["raw"], name + ".mp4")
                if not E.cut_raw(video, c["start"], c["end"] - c["start"], raw):
                    continue
                given = None
                if ytcap:
                    given = {"lang": ytcap["lang"], "words": E.slice_words(ytcap["words"], c["start"], c["end"])}
                words = E.transcribe_auto(raw, DIRS["tmp"], settings, log, speech=o.get("speech") or None,
                                          context=self.title, given=given) if settings["captions"] else []
                cchat = [(round(t - c["start"], 1), m) for t, m in chat if c["start"] <= t <= c["end"]]
                d = E.probe_duration(raw)
                meta = clamp_len(E.make_meta(settings, words or [], cchat, short_name, d, log, ctx=with_lang(ctx)), plan, d)
                lay = o.get("layout", "auto")
                if lay == "auto":
                    lay = "template" if genre in ("stream", "sports") else "fit"
                scene = saved_layout(lay)
                if scene:
                    pass
                elif lay in ("fit", "fill"):
                    scene = E.clean_scene(E.layout_scene(lay, tpl))
                elif settings.get("style_on") and isinstance(settings.get("style_scene"), dict):
                    scene = E.clean_scene(E.style_to_scene(settings["style_scene"], tpl))
                write_meta({"name": name, "created": time.time(), "status": "queued", "channel": short_name,
                            "ctx": ctx, "genre": genre, "campaign": o.get("brief", "")[:300], "scene": scene,
                            "source": url or self.title, "msgs": c["msgs"], "manual": False, "vod": True,
                            "vod_at": c["peak"], "chat": cchat[-150:], "words": words or [],
                            "transcript": " ".join(w[2] for w in words or []), "duration": round(d, 2),
                            "template": tpl, "template_name": template or settings.get("template"),
                            "length": round((meta.get("trim_end") or d) - (meta.get("trim_start") or 0)), **meta})
                ai = meta.get("score")
                scored.append(((ai if ai is not None else 0) * 10 + c["signal"], name))
            # phase 2: render only the best ones, drop the rest
            scored.sort(reverse=True)
            keep = [n for _, n in scored[:count]]
            for _, n in scored[count:]:
                for path in (os.path.join(DIRS["raw"], n + ".mp4"), meta_path(n)):
                    try:
                        os.remove(path)
                    except OSError:
                        pass
            self.set("rendering", 0, f"rendering the best {len(keep)} clips")
            for n in keep:
                mm = read_meta(n) or {}
                shown, orig = cap_lang_words([tuple(w) for w in mm.get("words", [])], n)
                if orig:
                    update_meta(n, words=shown, words_orig=orig, cap_lang=settings.get("cap_lang"))
                jobs.put({"kind": "restyle", "name": n, "notify": True})
            self.made = keep
            # wait for renders so the progress shows when they're done
            while not self.stop_flag.is_set():
                done = sum(1 for n in keep if (read_meta(n) or {}).get("status") in ("done", "error"))
                self.set(pct=100 * done / max(1, len(keep)), msg=f"rendered {done} of {len(keep)}")
                if done >= len(keep):
                    break
                time.sleep(2)
            self.set("done", 100, f"{len(keep)} clip{'s' if len(keep) != 1 else ''} ready - see the Clips tab")
            log(f"[vod] done: {len(keep)} clips from {self.title[:50]}")
            self.drop_source(base)
        except Exception as e:  # noqa: BLE001
            self.set("error", msg=str(e)[:300])
            log(f"[vod] failed: {e}")
            if url and "base" in locals():
                self.drop_source(base)   # a retry downloads it again; an uploaded file is kept so it can be retried

    @staticmethod
    def drop_source(base):
        """Delete the downloaded (or uploaded) video and its side files once clipping is over. The clips keep their own
        short copy of their part, so editing and re-rendering still work."""
        folder, stem = os.path.dirname(base), os.path.basename(base)
        if os.path.normcase(os.path.abspath(folder)) != os.path.normcase(os.path.abspath(DIRS["vod"])) or not stem:
            return
        freed = 0
        for f in os.listdir(folder):
            if f == stem or f.startswith(stem + "."):
                p = os.path.join(folder, f)
                for _ in range(5):      # Windows can hold the file a moment after ffmpeg closes it
                    try:
                        sz = os.path.getsize(p)
                        os.remove(p)
                        freed += sz
                        break
                    except FileNotFoundError:
                        break
                    except OSError:
                        time.sleep(2)
        if freed:
            log(f"[vod] deleted the source video ({freed / 1e6:.0f} MB freed)")


vod = VodJob()

# ------------------------------------------------------------------ posting
ACC = P.Accounts(os.path.join(HERE, "accounts.json"))
YT = P.YouTube(ACC, f"http://127.0.0.1:{PORT}/oauth/youtube")
IG = P.Instagram(ACC)
FB = P.Facebook(ACC)
posts = queue.Queue()


def set_post(name, platform, **kw):
    with meta_lock:
        m = load_json(meta_path(name), {"name": name})
        m.setdefault("posts", {}).setdefault(platform, {}).update(kw, at=time.time())
        save_json(meta_path(name), m)


def post_worker():
    while True:
        item = posts.get()
        WORKING["post"] += 1
        try:
            _post_one(*item)
        finally:
            WORKING["post"] -= 1


def _post_one(name, platform, opts):
    if True:
        m = read_meta(name) or {}
        video = os.path.join(out_dir(), name + ".mp4")
        tags = m.get("hashtags", [])
        caption = (m.get("caption") or " ".join((m.get("hook") or "").split())).strip()
        title = " ".join((m.get("yt_title") or m.get("hook") or caption).split())     # titles are one line
        prog = lambda s: set_post(name, platform, status="posting", step=s)
        try:
            if not os.path.exists(video):
                raise P.PostError("clip file not found")
            cover = cover_for(name)
            spec = TH.clean(m.get("thumb") or {})
            if platform == "youtube" or platform.startswith("yt:"):
                ch_id = platform[3:] if platform.startswith("yt:") else None
                r = YT.upload(video, title, f"{caption}\n\n{' '.join(tags)}", tags,
                              opts.get("privacy") or ACC.data["youtube"].get("privacy", "public"), prog, ch_id, cover=cover)
            elif platform == "instagram" or platform.startswith("ig:"):
                acc_id = platform[3:] if platform.startswith("ig:") else None
                r = IG.upload(video, f"{caption}\n\n{' '.join(tags)}", prog, acc_id, cover=cover,
                              thumb_ms=int(spec["t"] * 1000) if m.get("thumb") else None)
            elif platform.startswith("fb:"):
                r = FB.upload(video, f"{caption}\n\n{' '.join(tags)}", prog, platform[3:], cover=cover)
            elif platform.startswith("tt:"):
                try:
                    tv = video
                    if cover and spec["first_frame"]:
                        prog("putting the thumbnail at the start (TikTok cover)")
                        tv = cover_p(name, "tiktok.mp4")
                        if not (os.path.isfile(tv) and os.path.getmtime(tv) >= max(os.path.getmtime(video), os.path.getmtime(cover))):
                            if not E.cover_first_frame(video, cover, tv):
                                tv = video
                    r = TT.upload(platform[3:], tv, f"{caption} {' '.join(tags)}".strip(), prog)
                except TW.TikTokError as e:
                    raise P.PostError(str(e)) from None
            else:
                raise P.PostError("unknown platform")
            set_post(name, platform, status="posted", url=r["url"], id=r.get("id") or "", error="", step="")
            log(f"posted {name} to {platform}: {r['url']}")
        except Exception as e:  # noqa: BLE001
            set_post(name, platform, status="failed", error=str(e), step="")
            log(f"posting {name} to {platform} failed: {e}")


threading.Thread(target=post_worker, daemon=True).start()


# ---------------------------------------------------------------- views of posted clips
def _post_id(platform, v):
    if v.get("id"):
        return str(v["id"])
    u = v.get("url") or ""
    m = re.search(r"youtube\.com/shorts/([\w-]{11})|youtu\.be/([\w-]{11})|[?&]v=([\w-]{11})", u) if platform.startswith(("yt:", "youtube")) else None
    if m:
        return next(g for g in m.groups() if g)
    m = re.search(r"facebook\.com/reel/(\d+)", u) if platform.startswith("fb:") else None
    return m.group(1) if m else ""


def post_stats(name, refresh=False):
    """Views, likes and comments for each platform a clip was posted to. Kept for 30 minutes; TikTok can't be read."""
    m = read_meta(name) or {}
    out = {}
    for plat, v in (m.get("posts") or {}).items():
        if v.get("status") != "posted" or plat.startswith("tt:") or plat == "tiktok":
            continue                          # TikTok: no way to read views
        st = v.get("stats") or {}
        if not refresh and time.time() - (st.get("at") or 0) < 1800:
            out[plat] = st
            continue
        vid = _post_id(plat, v)
        try:
            if not vid:
                raise P.PostError("no post id saved")
            if plat == "youtube" or plat.startswith("yt:"):
                st = YT.stats(vid, plat[3:] if plat.startswith("yt:") else None)
            elif plat == "instagram" or plat.startswith("ig:"):
                st = IG.stats(vid, plat[3:] if plat.startswith("ig:") else None)
            elif plat.startswith("fb:"):
                st = FB.stats(vid, plat[3:])
            else:
                continue                      # TikTok: no way to read views
            st["at"] = time.time()
        except Exception as e:  # noqa: BLE001
            st = {**st, "error": str(e)[:100], "at": time.time()}
        with meta_lock:                       # not set_post: that would move the post's time (used for daily limits)
            mm = load_json(meta_path(name), {"name": name})
            if plat in mm.get("posts", {}):
                mm["posts"][plat]["stats"] = st
                save_json(meta_path(name), mm)
        out[plat] = st
    return out


def stats_worker():
    """Every 40 minutes, refresh the views of clips posted in the last 30 days."""
    time.sleep(120)
    while True:
        try:
            for f in os.listdir(DIRS["meta"]):
                if not f.endswith(".json"):
                    continue
                m = load_json(os.path.join(DIRS["meta"], f), None) or {}
                if any(v.get("status") == "posted" and time.time() - (v.get("at") or 0) < 30 * 86400 for v in (m.get("posts") or {}).values()):
                    post_stats(m.get("name") or f[:-5])
        except Exception as e:  # noqa: BLE001
            log(f"[views] {e}")
        time.sleep(2400)


threading.Thread(target=stats_worker, daemon=True).start()


# Scheduled posts + per-account daily limit and spacing. A post only ever starts from the owner pressing Post
# (now or at the time they picked); the gate just waits until that account is allowed to post again.
POST_PENDING = set()


def post_times(platform):
    """When this account posted (or started posting) in the last 24 h."""
    out = []
    for f in os.listdir(DIRS["meta"]):
        if f.endswith(".json"):
            v = ((load_json(os.path.join(DIRS["meta"], f), None) or {}).get("posts") or {}).get(platform) or {}
            if v.get("status") in ("posted", "posting", "queued") and time.time() - (v.get("at") or 0) < 86400:
                out.append(v.get("at") or 0)
    return sorted(out)


def post_gate(platform):
    """-> seconds to wait before this account may post (0 = now)."""
    cap, gap = int(settings.get("post_daily_cap") or 0), float(settings.get("post_gap_min") or 0) * 60
    if not cap and not gap:
        return 0
    ts = post_times(platform)
    wait = 0
    if cap and len(ts) >= cap:
        wait = ts[-cap] + 86400 - time.time()
    if gap and ts:
        wait = max(wait, ts[-1] + gap - time.time())
    return max(0, wait)


def post_scheduler():
    for f in os.listdir(DIRS["meta"]):          # pick up scheduled posts saved before a restart
        if f.endswith(".json"):
            m = load_json(os.path.join(DIRS["meta"], f), None) or {}
            if any(v.get("status") in ("scheduled", "waiting") for v in (m.get("posts") or {}).values()):
                POST_PENDING.add(m.get("name") or f[:-5])
    while True:
        time.sleep(15)
        for name in list(POST_PENDING):
            m = read_meta(name) or {}
            left = False
            for plat, v in (m.get("posts") or {}).items():
                if v.get("status") not in ("scheduled", "waiting"):
                    continue
                if (v.get("due") or 0) > time.time():
                    left = True
                    continue
                w = post_gate(plat)
                if w > 0:
                    left = True
                    msg = f"waiting for the daily limit / spacing - about {int(w // 60) + 1} min"
                    if v.get("step") != msg or v.get("status") != "waiting":
                        set_post(name, plat, status="waiting", step=msg, due=v.get("due"))
                    continue
                set_post(name, plat, status="queued", error="", url="", step="", due=None)
                posts.put((name, plat, {"privacy": v.get("privacy")}))
            if not left:
                POST_PENDING.discard(name)


threading.Thread(target=post_scheduler, daemon=True).start()

# ------------------------------------------------------------------ remote access
def find_cloudflared():
    exe = shutil.which("cloudflared")
    if exe:
        return exe
    for p in (r"C:\Program Files (x86)\cloudflared\cloudflared.exe", r"C:\Program Files\cloudflared\cloudflared.exe",
              os.path.join(HERE, "cloudflared.exe"), "/opt/homebrew/bin/cloudflared", "/usr/local/bin/cloudflared"):
        if os.path.exists(p):
            return p
    return None


class Tunnel:
    def __init__(self):
        self.proc = None
        self.manual_stop = False
        self.restarts = []
        self.state = "off"      # off | starting | on | error
        self.url = ""
        self.error = ""

    def info(self):
        return {"state": self.state, "url": self.url, "error": self.error,
                "protected_email": "", "has_password": bool(settings.get("remote_password"))}

    def start(self):
        if self.proc and self.proc.poll() is None:
            return
        if len(settings.get("remote_password", "")) < 6:
            raise RuntimeError("set a remote password (6+ characters) first")
        exe = find_cloudflared()
        if not exe:
            raise RuntimeError("cloudflared is not installed. Run: " + ("brew install cloudflared" if IS_MAC else "winget install --id Cloudflare.cloudflared") + "  (then restart Chipper)")
        # Cloudflare's email protection blocks form submissions (POST), so the app's own
        # password lock protects the link instead.
        token, host = self.fixed()
        env = None
        if token and host:
            # permanent link (your own domain): the token goes in through the environment, not the command line
            cmd = [exe, "tunnel", "--no-autoupdate", "run"]
            env = {**os.environ, "TUNNEL_TOKEN": token}
        else:
            cmd = [exe, "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{PORT}"]
        self.state, self.url, self.error, self.manual_stop = "starting", "", "", False
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env,
                                     encoding="utf-8", errors="replace", creationflags=E.NO_WINDOW)
        threading.Thread(target=self._read, args=(self.proc,), daemon=True).start()

    @staticmethod
    def fixed():
        """(token, host) of the permanent link, if set up."""
        token = (settings.get("remote_tunnel_token") or "").strip()
        host = re.sub(r"^https?://", "", (settings.get("remote_tunnel_host") or "").strip()).strip("/").lower()
        return (token, host) if token and re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", host or "") else ("", "")

    def _read(self, proc):
        tail = deque(maxlen=8)
        token, host = self.fixed()
        for line in proc.stdout:
            tail.append(line.strip()[:400].replace(token, "•••") if token else line.strip()[:400])
            if token and host:
                # permanent link: it's up as soon as cloudflared connects to Cloudflare
                if "Registered tunnel connection" in line and not self.url:
                    self.url, self.state = f"https://{host}", "on"
                    log(f"remote link (permanent): {self.url}")
                    if settings.get("notify_link", True):
                        notify("Chipper is online 📱", f"Tap to open your remote link:\n{self.url}", click=self.url, tags=["link"])
                    try:
                        with open(os.path.join(out_dir(), "Chipper remote link.txt"), "w", encoding="utf-8") as f:
                            f.write(f"{self.url}\n\nYour permanent link. It stays the same when Chipper restarts.\n")
                    except OSError:
                        pass
                elif "Unauthorized" in line or "Invalid tunnel secret" in line or "token is not valid" in line.lower():
                    self.error = "Cloudflare refused the tunnel token - copy it again from the Cloudflare dashboard"
                continue
            # the real link is a random name like https://brave-lion-x.trycloudflare.com; never Cloudflare's own
            # api.trycloudflare.com, which shows up in its error messages
            m = None if re.search(r"(?i)\b(err|failed|error)\b", line) else \
                re.search(r"https://(?!api\.|www\.)[a-z0-9]+(?:-[a-z0-9]+)+\.trycloudflare\.com", line)
            if m and not self.url:
                self.url, self.state = m.group(0), "on"
                log(f"remote link: {self.url}")
                print("\n" + "=" * 64 + f"\n  REMOTE LINK (open on your phone):  {self.url}\n" + "=" * 64 + "\n", flush=True)
                if settings.get("notify_link", True):
                    notify("Chipper is online 📱", f"Tap to open your remote link:\n{self.url}", click=self.url,
                           tags=["link"])
                try:
                    with open(os.path.join(out_dir(), "Chipper remote link.txt"), "w", encoding="utf-8") as f:
                        f.write(f"{self.url}\n\nOpen this on your phone or work computer. "
                                f"It changes every time Chipper restarts.\n")
                except OSError:
                    pass
        if proc is self.proc:
            text = " | ".join(tail)
            if "allowed-mail" in text and ("flag provided but not defined" in text or "unknown" in text.lower()):
                self.error = "your cloudflared is too old for email protection: run  winget upgrade Cloudflare.cloudflared"
            elif self.state != "off" and not self.error.startswith("Cloudflare refused"):
                self.error = "tunnel stopped: " + text[-300:]
            self.state = "error" if self.error else "off"
            self.url = ""
            if self.error and settings.get("remote_auto") and not self.manual_stop:
                # keep trying: quickly at first, then every few minutes (e.g. while the internet is down)
                self.restarts = [t for t in self.restarts if time.time() - t < 1800]
                self.restarts.append(time.time())
                n = len(self.restarts)
                wait = 15 if n <= 3 else 60 if n <= 6 else 300
                if "status 429" in text or "Too Many Requests" in text:
                    wait = 600                   # Cloudflare is rate-limiting this PC: retrying sooner only extends it
                    self.error = "Cloudflare is busy giving out links (too many requests) - trying again in 10 min"
                elif "trycloudflare.com/tunnel" in text or "deadline exceeded" in text:
                    self.error = "couldn't reach Cloudflare to get a link (internet problem on the PC?) - retrying"
                log(f"remote link dropped - reconnecting in {wait // 60} min" if wait >= 120 else f"remote link dropped - reconnecting in {wait} s")
                threading.Timer(wait, self._restart).start()

    def _restart(self):
        if not self.manual_stop:
            try:
                self.start()
            except RuntimeError as e:
                log(f"remote access not restarted: {e}")

    def watchdog(self):
        """Every 5 minutes check the public link really reaches this app; reconnect if not."""
        fails = 0
        while True:
            time.sleep(300)
            if self.state != "on" or not self.url or self.manual_stop:
                fails = 0
                continue
            try:
                req = urllib.request.Request(self.url + "/healthz", headers={"User-Agent": "Mozilla/5.0 (Chipper Studio link check)"})
                urllib.request.urlopen(req, timeout=20).read()
                fails = 0
            except urllib.error.HTTPError as e:
                # Cloudflare answering at all (e.g. 403 from bot protection on your own domain) means the link is up;
                # only its "can't reach the computer" errors (502/530…) mean the tunnel is really down
                if e.code in (502, 503, 504) or 520 <= e.code <= 530:
                    fails += 1
                else:
                    fails = 0
            except Exception:  # noqa: BLE001
                fails += 1
                if fails >= 2:
                    log("remote link stopped responding - getting a new one")
                    fails = 0
                    self.stop(manual=False)
                    self._restart()

    def stop(self, manual=True):
        self.manual_stop = manual
        self.state = "off"
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
        self.proc = None
        self.url = ""


tunnel = Tunnel()


# ------------------------------------------------------------------ web
app = Flask(__name__, static_folder=os.path.join(HERE, "static"), static_url_path="/static")

# ---- one-time public links so Instagram can download a clip itself (used when Meta's upload server refuses it)
SHARES = {}                      # token -> (file path, expires at)


def public_link(video):
    base = tunnel.url if tunnel.url.startswith("https://") else ""
    if not base or not os.path.isfile(video):
        return ""
    fn = "cover.jpg" if video.lower().endswith(".jpg") else "clip.mp4"
    now = time.time()
    for k in [k for k, v in SHARES.items() if v[1] < now]:
        SHARES.pop(k, None)
    for k, (path, exp) in SHARES.items():
        if path == video and exp - now > 1800:
            return f"{base.rstrip('/')}/pub/{k}/{fn}"
    k = __import__("secrets").token_urlsafe(24)
    SHARES[k] = (video, now + 3600)
    return f"{base.rstrip('/')}/pub/{k}/{fn}"


IG.public_link = public_link


@app.route("/pub/<tok>/clip.mp4")
@app.route("/pub/<tok>/cover.jpg")
def pub_clip(tok):
    v = SHARES.get(tok)
    if not v or v[1] < time.time() or not os.path.isfile(v[0]):
        abort(404)
    return send_from_directory(os.path.dirname(v[0]), os.path.basename(v[0]), conditional=True,
                               mimetype="image/jpeg" if v[0].lower().endswith(".jpg") else "video/mp4")
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 ** 3        # uploads up to 20 GB


@app.errorhandler(Exception)
def any_error(e):
    from werkzeug.exceptions import HTTPException
    if isinstance(e, HTTPException):
        return jsonify({"ok": False, "error": f"{e.code}: {e.description}"}), e.code
    import traceback
    log("ERROR: " + "".join(traceback.format_exception_only(type(e), e)).strip())
    return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


SECRET_F = os.path.join(HERE, ".secret")
try:
    SECRET = open(SECRET_F, "rb").read()
except OSError:
    SECRET = secrets.token_bytes(32)
    with open(SECRET_F, "wb") as f:
        f.write(SECRET)


def auth_token():
    return hmac.new(SECRET, settings.get("remote_password", "").encode(), hashlib.sha256).hexdigest()


def is_local():
    host = (request.host or "").split(":")[0]
    return (request.remote_addr in ("127.0.0.1", "::1") and host in ("127.0.0.1", "localhost")
            and not request.headers.get("Cf-Connecting-Ip") and not request.headers.get("Cf-Ray"))


LOGIN_HTML = """<!doctype html><meta name=viewport content="width=device-width,initial-scale=1">
<title>Chipper Studio</title><link rel=icon type=image/png href="/static/brand/icon-64.png"><body style="background:#0e0f13;color:#e9ebf0;font-family:Segoe UI,system-ui;display:grid;place-items:center;height:100vh;margin:0">
<form method=post action="/login" style="background:#17191f;border:1px solid #2b2f39;padding:28px;border-radius:14px;width:300px">
<img src="/static/brand/logo-white.png" alt="Chipper Studio" style="display:block;width:150px;margin:0 auto 16px">
<p style="color:#8a90a0;margin:0 0 14px">{msg}</p>
<div style="position:relative"><input id=pw name=password type=password autofocus placeholder="Remote password" style="box-sizing:border-box;width:100%;padding:11px 46px 11px 11px;border-radius:9px;border:1px solid #2b2f39;background:#1f222a;color:#fff;font-size:15px">
<button type=button aria-label="Show password" onclick="var p=document.getElementById('pw');var s=p.type==='password';p.type=s?'text':'password';this.textContent=s?'\U0001F648':'\U0001F441'" style="position:absolute;right:6px;top:50%;transform:translateY(-50%);background:none;border:0;font-size:18px;cursor:pointer;color:#8a90a0">\U0001F441</button></div>
<button style="margin-top:12px;width:100%;padding:11px;border:0;border-radius:9px;background:#ff3d6e;color:#fff;font-weight:700;font-size:15px">Log in</button></form></body>"""


@app.before_request
def remote_gate():
    if (is_local() or request.path in ("/login", "/healthz", "/favicon.ico") or request.path.startswith("/pub/")
            or request.path.startswith("/static/brand/")):
        return None
    if not settings.get("remote_password"):
        return "Remote access is off.", 403
    if hmac.compare_digest(request.cookies.get("chipper_auth", ""), auth_token()):
        if (request.path.startswith("/oauth/") or request.path in ("/api/open", "/api/diag/whisper")
                or (request.path.startswith("/api/accounts") and request.method != "GET")
                or request.path.startswith("/api/remote/")):
            return jsonify({"ok": False, "error": "only available on the computer running Chipper"}), 403
        return None
    has = "chipper_auth" in request.cookies
    if request.path not in ("/favicon.ico",):
        log(f"remote: {request.method} {request.path} blocked ({'old/wrong login cookie' if has else 'not logged in'})")
    if request.path.startswith("/api/") or request.method != "GET":
        return jsonify({"ok": False, "error": "log in again"}), 401
    return LOGIN_HTML.format(msg="Enter your remote password."), 401


FAILS = deque(maxlen=50)


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return LOGIN_HTML.format(msg="Enter your remote password.")
    now = time.time()
    if sum(1 for t in FAILS if now - t < 900) >= 5:
        log("remote: login locked for 15 minutes after 5 wrong passwords")
        return LOGIN_HTML.format(msg="Too many wrong tries. Locked for 15 minutes."), 429
    pw = request.form.get("password", "")
    if settings.get("remote_password") and hmac.compare_digest(pw.strip(), settings["remote_password"]):
        log("remote: login OK")
        resp = make_response(redirect("/"))
        resp.set_cookie("chipper_auth", auth_token(), max_age=30 * 86400, httponly=True,
                        secure=not is_local(), samesite="Lax")
        return resp
    FAILS.append(now)
    log(f"remote: wrong password ({len(pw)} characters typed, expected {len(settings.get('remote_password', ''))})")
    return LOGIN_HTML.format(msg="Wrong password, try again."), 401


@app.get("/api/diag/whisper")
def api_diag_whisper():
    """Run whisper on the newest clip source and report what happens."""
    if not is_local():
        return fail("laptop only")
    raws = sorted((os.path.join(DIRS["raw"], f) for f in os.listdir(DIRS["raw"]) if f.endswith(".mp4")),
                  key=os.path.getmtime)
    if not raws:
        return fail("no clips to test with yet")
    t = time.time()
    mode = request.args.get("engine")
    words = E.transcribe_auto(raws[-1], DIRS["tmp"], {**settings, **({"stt": mode} if mode else {})}, log)
    return ok(file=os.path.basename(raws[-1]), seconds=round(time.time() - t, 1), cmd=E.whisper_cmd(),
              words=len(words) if words is not None else None, sample=" ".join(w[2] for w in (words or [])[:20]),
              error=E.LAST_WHISPER_ERROR[0])


@app.get("/favicon.ico")
def favicon():
    return send_from_directory(os.path.join(app.static_folder, "brand"), "favicon.ico", max_age=86400)


@app.get("/api/app/info")
def api_app_info():
    """For the Android app after it signs in: where to look for the laptop's link next time it changes."""
    return ok(ntfy_topic=settings.get("ntfy_topic") or "", ntfy_server=settings.get("ntfy_server") or "https://ntfy.sh",
              online=tunnel.url)


@app.get("/healthz")
def healthz():
    return "ok"


@app.post("/api/notify/test")
def api_notify_test():
    if not is_local():
        return fail("laptop only")
    notify("Chipper test ✅", "Phone notifications work. You'll get your remote link here every time Chipper starts.",
           click=tunnel.url or None, tags=["white_check_mark"])
    return ok(topic=settings.get("ntfy_topic"))


@app.post("/api/remote/start")
def api_remote_start():
    if not is_local():
        return fail("start or stop remote access from the laptop")
    try:
        tunnel.start()
    except RuntimeError as e:
        return fail(e)
    return ok()


@app.post("/api/remote/stop")
def api_remote_stop():
    if not is_local():
        return fail("start or stop remote access from the laptop")
    tunnel.stop()
    return ok()


def ok(**kw):
    return jsonify({"ok": True, **kw})


def fail(msg, code=400):
    return jsonify({"ok": False, "error": str(msg)}), code


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


# ------------------------------------------------------------------ auto-watch channels
WATCH_F = os.path.join(HERE, "watch.json")
LAYOUTS_F = os.path.join(HERE, "layouts.json")


def saved_layout(lay):
    """'saved:<name>' -> that layout's scene for a new clip, else None."""
    if not str(lay or "").startswith("saved:"):
        return None
    sc = (load_json(LAYOUTS_F, {}).get(lay[6:]) or {}).get("scene")
    return E.clean_scene(E.saved_layout_scene(sc)) if isinstance(sc, dict) else None


class Watcher:
    """Checks your channels every few minutes. Live -> starts recording + clipping in a free stream slot.
    New long upload -> clips it like 'Clip a finished stream'. Lives have priority: uploads wait while a watched
    channel is live. Nothing is posted automatically."""

    FAST = int(os.environ.get("CHIPPER_WATCH_FAST", 180))      # seconds between rounds normally
    SLOW = int(os.environ.get("CHIPPER_WATCH_SLOW", 600))      # ... and while the PC is busy clipping

    def __init__(self):
        self.data = load_json(WATCH_F, {"enabled": False, "channels": [], "seen": {}, "queue": []})
        for k, v in (("enabled", False), ("channels", []), ("seen", {}), ("queue", []), ("skip", {})):
            self.data.setdefault(k, v)
        self.lock = threading.RLock()
        self.wake = threading.Event()
        self.next_check = 0.0
        self.checking = ""
        self.live_slots = {}       # channel id -> job slot started by the watcher
        self.vod_from = None       # channel id whose upload is being clipped right now
        self.data.setdefault("skip", {})   # channel id -> live URL you stopped: not restarted until that live ends

    def save(self):
        with self.lock:
            d = {**self.data, "channels": [{k: v for k, v in c.items() if not k.startswith("_")} for c in self.data["channels"]]}
            save_json(WATCH_F, d)

    def busy(self):
        return any(j.state != "idle" for j in JOBS.values()) or vod.busy() or jobs.qsize() > 0

    def info(self):
        with self.lock:
            chans = [{k: v for k, v in c.items() if not k.startswith("_")} for c in self.data["channels"]]
            return {"enabled": self.data["enabled"], "channels": chans, "queue": list(self.data["queue"]),
                    "next_check": self.next_check, "checking": self.checking,
                    "interval": self.SLOW if self.busy() else self.FAST}

    # -- channel list
    def add(self, text):
        plat, key = E.parse_channel_input(text)
        with self.lock:
            if any(c["platform"] == plat and c["key"].lower() == key.lower() for c in self.data["channels"]):
                raise RuntimeError("that channel is already in your list")
        ch = {"id": os.urandom(4).hex(), "platform": plat, "key": key, "name": key, "channel_id": "",
              "live": True, "uploads": plat == "youtube", "clips": 5, "min_len": 5, "template": "",
              "brief": "", "layout": "auto", "genre": "auto", "speech": "", "added": time.time(),
              "status": "new", "error": "", "last_check": 0, "live_now": "", "last_upload": ""}
        if plat == "youtube":
            r = E.yt_resolve(key)
            ch["channel_id"], ch["name"] = r["channel_id"], r["name"]
            try:       # remember what's already there: only videos uploaded from now on get clipped
                seen = [v for v, _, _ in E.yt_uploads(ch["channel_id"])]
            except Exception:  # noqa: BLE001
                seen = []
            with self.lock:
                self.data["seen"][ch["id"]] = seen
        elif plat == "tiktok":
            ch["name"] = "@" + key
            try:
                _, _, nick = E.tiktok_live_now(key)
                ch["name"] = f"{nick} (@{key})" if nick and nick.lower() != key else "@" + key
            except RuntimeError as e:
                if "not found" in str(e):
                    raise
            except Exception:  # noqa: BLE001
                pass
        else:
            try:
                _, _, disp = E.twitch_live_now(key)
                ch["name"] = disp
            except RuntimeError as e:
                raise RuntimeError(str(e)) from None
            except Exception:  # noqa: BLE001
                pass
        with self.lock:
            self.data["channels"].append(ch)
        self.save()
        log(f"[watch] added {ch['name']} ({plat})")
        self.wake.set()
        return ch

    def update(self, cid, fields):
        allowed = {"live": bool, "uploads": bool, "clips": int, "min_len": float, "template": str, "brief": str,
                   "layout": str, "genre": str, "speech": str}
        with self.lock:
            ch = self.find(cid)
            for k, t in allowed.items():
                if k in fields:
                    ch[k] = t(fields[k])
            ch["clips"] = max(1, min(20, ch["clips"]))
            ch["min_len"] = max(0, min(600, ch["min_len"]))
            ch["brief"] = ch["brief"][:4000]
        self.save()
        return ch

    def remove(self, cid):
        with self.lock:
            gone = next((c for c in self.data["channels"] if c["id"] == cid), None)
            self.data["channels"] = [c for c in self.data["channels"] if c["id"] != cid]
            self.data["seen"].pop(cid, None)
            self.data["skip"].pop(cid, None)
            self.data["queue"] = [q for q in self.data["queue"] if q.get("ch") != cid]
        self.save()
        # a removed channel stops being clipped now, not just from the next check
        self.stop_channel(cid, (gone or {}).get("name", "channel"), "removed")

    def stop_channel(self, cid, name, why):
        """Stop whatever auto-clip started for this channel: its live recording and its video being clipped.
        Clips already made are kept."""
        slot = self.live_slots.pop(cid, None)
        j = JOBS.get(slot) if slot is not None else None
        if j and j.state in ("starting", "live"):
            j.watch_stopped = True
            j.stop()
            log(f"[watch] {name} {why} - stopped its live recording (stream {slot})")
        if self.vod_from == cid and vod.busy():
            vod.stop()
            log(f"[watch] {name} {why} - stopped clipping its video")
        if self.vod_from == cid:
            self.vod_from = None

    def stopped_by_you(self, job):
        """You pressed Stop on a stream auto-clip started: don't start that same live again."""
        if getattr(job, "watch_stopped", False):
            job.watch_stopped = False
            return
        for cid, slot in list(self.live_slots.items()):
            if JOBS.get(slot) is job:
                self.live_slots.pop(cid, None)
                with self.lock:
                    self.data["skip"][cid] = job.url
                    name = next((c["name"] for c in self.data["channels"] if c["id"] == cid), "channel")
                self.save()
                log(f"[watch] you stopped {name}'s live - auto-clip leaves this live alone until it ends")

    def find(self, cid):
        for c in self.data["channels"]:
            if c["id"] == cid:
                return c
        raise RuntimeError("channel not found")

    # -- main loop
    def loop(self):
        time.sleep(15)
        while True:
            try:
                if self.data["enabled"]:
                    self.round()
                self.dispatch()
            except Exception as e:  # noqa: BLE001
                log(f"[watch] {e}")
            wait = self.SLOW if self.busy() else self.FAST
            if getattr(self, "net_down", False):
                wait = 60                  # internet hiccup: try again in a minute instead of waiting a full round
            self.next_check = time.time() + wait
            # wake up early to start queued uploads as soon as the PC is free
            end = time.time() + wait
            while time.time() < end:
                if self.wake.wait(timeout=min(20, max(1, end - time.time()))):
                    self.wake.clear()
                    break
                self.dispatch()

    NET_ERRORS = ("getaddrinfo", "timed out", "Temporary failure in name resolution", "Name or service not known",
                  "Network is unreachable", "No route to host", "Connection reset", "Connection aborted",
                  "Connection refused", "Remote end closed", "WinError 10060", "WinError 10065", "WinError 10051",
                  "11001", "11002", "IncompleteRead", "SSL: ", "EOF occurred")

    def is_net_error(self, e):
        m = str(e)
        return isinstance(e, (TimeoutError, ConnectionError)) or any(k in m for k in self.NET_ERRORS)

    def round(self):
        net_fail = 0
        for ch in list(self.data["channels"]):
            if not self.data["enabled"]:
                break
            if ch not in self.data["channels"]:
                continue                          # removed while this round was running
            self.checking = ch["name"]
            for attempt in range(3):           # internet blips: retry quietly before showing an error
                try:
                    self.check(ch)
                    ch["error"] = ""
                    break
                except Exception as e:  # noqa: BLE001
                    if self.is_net_error(e):
                        if attempt < 2:
                            time.sleep((4, 10)[attempt])
                            continue
                        net_fail += 1
                        ch["error"] = "Internet problem on the PC (couldn't reach the site) - retrying in 1 min"
                    else:
                        ch["error"] = str(e)[:160]
                    break
            ch["last_check"] = time.time()
            time.sleep(1.5)        # be gentle with YouTube / Twitch
        self.checking = ""
        self.net_down = net_fail > 0
        if net_fail and not getattr(self, "_net_logged", False):
            log("[watch] internet problem on the PC - channel checks will retry every minute until it's back")
        self._net_logged = bool(net_fail)
        self.save()

    def check(self, ch):
        if ch["platform"] == "tiktok":
            rid, title, nick = E.tiktok_live_now(ch["key"])
            ch["name"] = nick or ch["name"]
            ch["live_now"] = (title or "live") if rid else ""
            ch["status"] = "live" if rid else "offline"
            if ch["status"] == "offline" and self.data["skip"].pop(ch["id"], None):
                log(f"[watch] {ch['name']}'s live ended - auto-clip will clip its next live")
            if rid and ch["live"]:
                self.start_live(ch, f"https://www.tiktok.com/@{ch['key']}/live", title)
            return
        if ch["platform"] == "twitch":
            sid, title, disp = E.twitch_live_now(ch["key"])
            ch["name"] = disp or ch["name"]
            ch["live_now"] = (title or "live") if sid else ""
            if sid and ch["live"]:
                self.start_live(ch, f"https://twitch.tv/{ch['key']}", title)
            ch["status"] = "live" if sid else "offline"
            if ch["status"] == "offline" and self.data["skip"].pop(ch["id"], None):
                log(f"[watch] {ch['name']}'s live ended - auto-clip will clip its next live")
            return
        vid, title = E.yt_live_now(ch["channel_id"])
        ch["live_now"] = title or (vid or "")
        ch["status"] = "live" if vid else "offline"
        if ch["status"] == "offline" and self.data["skip"].pop(ch["id"], None):
            log(f"[watch] {ch['name']}'s live ended - auto-clip will clip its next live")
        seen = self.data["seen"].setdefault(ch["id"], [])
        if vid:
            if vid not in seen:
                seen.append(vid)              # its replay later is not clipped twice
            if ch["live"]:
                self.start_live(ch, f"https://www.youtube.com/watch?v={vid}", title)
        if not ch["uploads"]:
            return
        for v, t, pub in E.yt_uploads(ch["channel_id"])[:6]:
            if v in seen:
                continue
            facts = E.yt_video_facts(v)
            if facts["live_now"] or facts["upcoming"]:
                continue                       # a live/premiere: the live check handles it, look again later
            seen.append(v)
            del seen[:-300]
            if facts["short"] or facts["length"] < ch["min_len"] * 60:
                log(f"[watch] {ch['name']}: skipped '{t[:50]}' ({facts['length'] // 60} min - too short)")
                continue
            ch["last_upload"] = t
            self.queue_upload(ch, f"https://www.youtube.com/watch?v={v}", t)

    def start_live(self, ch, url, title):
        if not self.data["enabled"] or ch not in self.data["channels"]:
            return                              # switched off or removed a moment ago
        if self.data["skip"].get(ch["id"]) == url or (ch["platform"] != "youtube" and self.data["skip"].get(ch["id"])):
            return                              # you stopped this live: wait until it ends
        for j in JOBS.values():
            if j.state != "idle" and (j.url == url or (ch["platform"] == "twitch" and j.url.endswith("/" + ch["key"]))
                                      or (ch["platform"] == "tiktok" and f"/@{ch['key']}/" in j.url)):
                return                          # already recording it
        fail = ch.get("_fail") or {}
        if fail.get("url") == url and time.time() < fail.get("until", 0):
            return                              # recording this live just failed - try again a bit later
        free = next((j for j in JOBS.values() if j.state == "idle"), None)
        if not free:
            if ch.get("_nofree") != url:
                ch["_nofree"] = url
                log(f"[watch] {ch['name']} is live but all {len(JOBS)} stream slots are busy")
                notify(f"🔴 {ch['name']} is live", "All stream slots are busy - stop one to clip this stream.",
                       click=tunnel.url or None, tags=["red_circle"])
            return
        free.start(url, ch.get("template") or "", brief=ch.get("brief") or settings.get("campaign_brief", ""),
                   layout=ch.get("layout") or "auto", speech=ch.get("speech") or "")      # the stream's clips follow this channel's brief
        self.live_slots[ch["id"]] = free.slot
        started = time.time()
        ch["_fail"] = {"url": url, "until": started + 300}       # no re-try for 5 min unless it really records

        def watch_fail(job=free):
            for _ in range(60):
                time.sleep(2)
                if job.state == "live" and job.rec and job.rec.start_time and job.rec.start_time >= started:
                    ch.pop("_fail", None)                       # recording - normal checks from now on
                    return
                if job.state == "idle":
                    break
            log(f"[watch] could not record {ch['name']}'s live - will try again in 5 min")
        threading.Thread(target=watch_fail, daemon=True).start()
        log(f"[watch] {ch['name']} went live - clipping in stream {free.slot}: {title[:60]}")
        notify(f"🔴 {ch['name']} is live - clipping", title or url, click=tunnel.url or None, tags=["red_circle"])

    def queue_upload(self, ch, url, title):
        with self.lock:
            if any(q["url"] == url for q in self.data["queue"]):
                return
            self.data["queue"].append({"ch": ch["id"], "name": ch["name"], "url": url, "title": title,
                                       "added": time.time()})
        self.save()
        log(f"[watch] new upload from {ch['name']}: {title[:60]} - queued for clipping")
        notify(f"📼 New video from {ch['name']}", f"{title} - Chipper will clip it", click=tunnel.url or None,
               tags=["movie_camera"])

    def dispatch(self):
        """Start the next queued upload when the clipper is free. Live streams first: wait while one runs."""
        with self.lock:
            if not self.data["enabled"] or not self.data["queue"] or vod.busy():
                return                          # auto-clip is off: queued videos wait
            if any(j.state != "idle" for j in JOBS.values()):
                return
            q = self.data["queue"].pop(0)
        self.save()
        try:
            ch = self.find(q["ch"])
        except RuntimeError:
            ch = {"clips": 5, "template": "", "brief": "", "genre": "auto", "layout": "auto"}
        try:
            vod.start(url=q["url"], clips=ch.get("clips", 5), template=ch.get("template", ""),
                      brief=ch.get("brief", ""), genre=ch.get("genre", "auto"), layout=ch.get("layout", "auto"),
                      speech=ch.get("speech", ""))
            self.vod_from = q["ch"]
            log(f"[watch] clipping {q['name']}: {q['title'][:60]}")
        except RuntimeError as e:
            with self.lock:
                self.data["queue"].insert(0, q)
            log(f"[watch] could not start yet: {e}")

    def clip_latest(self, cid):
        ch = self.find(cid)
        if ch["platform"] != "youtube":
            raise RuntimeError("only YouTube channels have uploads to clip")
        for v, t, _ in E.yt_uploads(ch["channel_id"])[:6]:
            f = E.yt_video_facts(v)
            if not (f["live_now"] or f["upcoming"]) and f["length"] >= ch["min_len"] * 60:
                self.queue_upload(ch, f"https://www.youtube.com/watch?v={v}", t)
                self.dispatch()
                return t
        raise RuntimeError("no recent long video found")


watcher = Watcher()


@app.get("/api/watch")
def api_watch():
    return ok(**watcher.info())


@app.post("/api/watch")
def api_watch_set():
    d = request.get_json(force=True) or {}
    if "enabled" in d:
        watcher.data["enabled"] = bool(d["enabled"])
        watcher.save()
        log("[watch] auto-clip " + ("ON" if watcher.data["enabled"] else "OFF - queued videos wait until it's back on"))
        if not watcher.data["enabled"]:
            for c in list(watcher.data["channels"]):        # stop the lives and video auto-clip started
                watcher.stop_channel(c["id"], c["name"], "auto-clip switched off")
        watcher.wake.set()
    return ok(**watcher.info())


@app.post("/api/watch/add")
def api_watch_add():
    try:
        watcher.add((request.get_json(force=True) or {}).get("channel", ""))
    except Exception as e:  # noqa: BLE001
        return fail(str(e)[:200])
    return ok(**watcher.info())


@app.post("/api/watch/update")
def api_watch_update():
    d = request.get_json(force=True) or {}
    try:
        watcher.update(d.get("id"), d)
    except RuntimeError as e:
        return fail(e)
    return ok(**watcher.info())


@app.post("/api/watch/remove")
def api_watch_remove():
    watcher.remove((request.get_json(force=True) or {}).get("id"))
    return ok(**watcher.info())


@app.post("/api/watch/check")
def api_watch_check():
    watcher.wake.set()
    return ok(**watcher.info())


@app.post("/api/watch/latest")
def api_watch_latest():
    try:
        t = watcher.clip_latest((request.get_json(force=True) or {}).get("id"))
    except Exception as e:  # noqa: BLE001
        return fail(str(e)[:200])
    return ok(title=t, **watcher.info())


@app.post("/api/watch/unqueue")
def api_watch_unqueue():
    url = (request.get_json(force=True) or {}).get("url")
    with watcher.lock:
        watcher.data["queue"] = [q for q in watcher.data["queue"] if q["url"] != url]
    watcher.save()
    return ok(**watcher.info())



@app.get("/api/state")
def api_state():
    return ok(jobs=[j.info() for j in JOBS.values()], logs=list(logs)[-100:], queue=jobs.qsize(),
              remote=tunnel.info(), vod=vod.info(), watch=watcher.info())


@app.get("/api/config")
def api_config():
    s = dict(settings)
    for k in ("api_key", "ai_key", "groq_key", "remote_tunnel_token"):
        s[k] = "••••" + s[k][-4:] if s.get(k) else ""
    s["remote_password"] = "••••••••" if s.get("remote_password") else ""
    if not is_local():
        for k in ("api_key", "ai_key", "groq_key", "remote_password", "out_dir", "ntfy_topic", "ntfy_server",
                  "remote_tunnel_token"):
            s.pop(k, None)
    styles = {k: {"label": v["label"], "words": v["words"], "upper": v["upper"], "mode": v["mode"], "anim": v.get("anim", ""), **v["css"],
                  "size": v["size"], "outline": v["outline"], "font": v["font"]}
              for k, v in E.CAPTION_STYLES.items()}
    return ok(settings=s, templates=templates, styles=styles, defaults=E.DEFAULT_TEMPLATE, remote=not is_local(),
              yt_cookie_file=os.path.isfile(YT_COOKIES_F), fonts=E.custom_fonts(), system_fonts=E.SYSTEM_FONTS,
              sfx=E.SFX_LABELS, sounds=list_sounds(), anims=E.CAPTION_ANIMS, hook_looks=E.HOOK_LOOKS, lite=LITE)


REMOTE_LOCKED = {"api_key", "ai_key", "groq_key", "ai_provider", "ai_model", "out_dir", "stt", "ollama_url",
                 "ntfy_topic", "ntfy_server"}


@app.post("/api/settings")
def api_settings():
    data = request.get_json(force=True)
    for k, v in data.items():
        if k not in DEFAULT_SETTINGS:
            continue
        if not is_local() and (k.startswith("remote_") or k in REMOTE_LOCKED):
            continue
        if k in ("api_key", "ai_key", "groq_key", "remote_password", "remote_tunnel_token") and str(v).startswith("••••"):
            continue
        if LITE and ((k == "ai_provider" and v == "ollama") or (k == "enhance_default" and v) or (k == "stt" and v in ("local", "gpu"))):
            continue                                   # light copy: nothing that needs a big local AI install
        try:
            settings[k] = type(DEFAULT_SETTINGS[k])(v)
        except (TypeError, ValueError):
            return fail(f"bad value for {k}")
    save_json(SETTINGS_F, settings)
    sync_ytauth()
    E.RENDER_QUALITY = settings.get("render_quality") or "high"
    return ok()


# A file named LITE next to app.py marks a light copy (e.g. a work laptop): no multi-GB AI downloads there.
LITE = os.path.isfile(os.path.join(HERE, "LITE"))
IS_MAC = sys.platform == "darwin"
LITE_MSG = "This is a light copy of Chipper: big AI downloads (NVIDIA enhance, GPU captions, local AI) are switched off here"

YT_COOKIES_F = os.path.join(HERE, "yt_cookies.txt")
FONTS_DIR = os.path.join(HERE, "fonts")
os.makedirs(FONTS_DIR, exist_ok=True)
E.FONTS_DIR = FONTS_DIR


@app.get("/api/fonts")
def api_fonts():
    return ok(fonts=E.custom_fonts(), system=E.SYSTEM_FONTS)


_FONT_INDEX = {}


def _font_dirs():
    dirs = [FONTS_DIR]
    if os.name == "nt":
        dirs += [os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts"),
                 os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows", "Fonts")]
    elif sys.platform == "darwin":
        dirs += ["/Library/Fonts", "/System/Library/Fonts", "/System/Library/Fonts/Supplemental", os.path.expanduser("~/Library/Fonts")]
    else:
        dirs += ["/usr/share/fonts", os.path.expanduser("~/.local/share/fonts")]
    return [d for d in dirs if d and os.path.isdir(d)]


def _font_index():
    """family -> best file (bold if there is one, as the captions are bold), built once."""
    if _FONT_INDEX:
        return _FONT_INDEX
    best = {}
    for d in _font_dirs():
        for root, _, files in os.walk(d):
            for fn in files:
                if not fn.lower().endswith((".ttf", ".otf")):
                    continue
                p = os.path.join(root, fn)
                fam = E.font_family(p)
                if not fam:
                    continue
                low = fn.lower()
                score = (2 if d == FONTS_DIR else 0) + (1 if ("bd" in low or "bold" in low or "black" in low) and "italic" not in low and not low.endswith(("i.ttf", "bi.ttf", "z.ttf")) else 0)
                if fam not in best or score > best[fam][0]:
                    best[fam] = (score, p)
    _FONT_INDEX.update({k: v[1] for k, v in best.items()})
    return _FONT_INDEX


@app.get("/api/fonts/file")
def api_font_file():
    """A font file by family name, so the phone app can show text in the same font."""
    fam = (request.args.get("family") or "").strip()
    p = _font_index().get(fam) or next((v for k, v in _font_index().items() if k.lower() == fam.lower()), None)
    if not p or not os.path.isfile(p):
        abort(404)
    return send_from_directory(os.path.dirname(p), os.path.basename(p), max_age=86400 * 30)


@app.post("/api/fonts")
def api_font_upload():
    f = request.files.get("file")
    if not f or not f.filename:
        return fail("choose a .ttf or .otf font")
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in (".ttf", ".otf"):
        return fail("use a .ttf or .otf font file")
    data = f.read(20 * 1024 * 1024 + 1)
    if len(data) > 20 * 1024 * 1024:
        return fail("that font file is too big")
    fname = re.sub(r"[^A-Za-z0-9_.-]", "_", os.path.basename(f.filename))[:80]
    path = os.path.join(FONTS_DIR, fname)
    with open(path, "wb") as out:
        out.write(data)
    fam = E.font_family(path)
    if not fam:
        os.remove(path)
        return fail("could not read that font")
    log(f"font added: {fam}")
    _FONT_INDEX.clear()
    return ok(file=fname, family=fam, fonts=E.custom_fonts())


SOUNDS_DIR = UP["fx"]
OLD_SOUNDS_DIR = os.path.join(HERE, "sounds")
E.SOUNDS_DIR = SOUNDS_DIR
E.SOUND_DIRS = [SOUNDS_DIR, OLD_SOUNDS_DIR]
E.sound_file = lambda f: sound_file_path(f)
E.asset_path = asset_path
E.SFX_CACHE = os.path.join(HERE, "work", "sfx")
SOUND_EXT = (".mp3", ".wav", ".ogg", ".m4a", ".aac")


def list_sounds():
    fs = set()
    for d in (SOUNDS_DIR, OLD_SOUNDS_DIR):
        if os.path.isdir(d):
            fs.update(f for f in os.listdir(d) if f.lower().endswith(SOUND_EXT))
    return sorted(fs)


def sound_file_path(fname):
    fname = os.path.basename(fname or "")
    fname = _aliases().get(fname, fname)
    for d in (SOUNDS_DIR, OLD_SOUNDS_DIR):
        p = os.path.join(d, fname)
        if fname and os.path.isfile(p):
            return p
    return ""


@app.get("/sfx/<name>.wav")
def sfx_file(name):
    p = E.make_sfx(name, E.SFX_CACHE)
    if not p:
        abort(404)
    return send_from_directory(os.path.dirname(p), os.path.basename(p), max_age=86400)


@app.get("/sounds/<path:fname>")
def sound_file(fname):
    p = sound_file_path(fname)
    if not p:
        abort(404)
    return send_from_directory(os.path.dirname(p), os.path.basename(p), max_age=86400)


@app.post("/api/sounds")
def api_sound_upload():
    """Your own sound effects (e.g. downloaded from Freesound, Pixabay or ElevenLabs)."""
    f = request.files.get("file")
    if not f or not f.filename:
        return fail("choose a sound file")
    if os.path.splitext(f.filename)[1].lower() not in SOUND_EXT:
        return fail("use an MP3, WAV, OGG or M4A file")
    data = f.read(15 * 1024 * 1024 + 1)
    if len(data) > 15 * 1024 * 1024:
        return fail("that sound is over 15 MB - effects should be short")
    fname = re.sub(r"[^A-Za-z0-9_.-]", "_", os.path.basename(f.filename))[:80]
    with open(os.path.join(SOUNDS_DIR, fname), "wb") as out:
        out.write(data)
    return ok(file=fname, sounds=list_sounds())


@app.route("/api/music", methods=["GET", "POST"])
def api_music():
    """Background music tracks (kept with the editor's images, so the phone sees them too)."""
    if request.method == "POST":
        f = request.files.get("file")
        if not f or not f.filename:
            return fail("choose a music file")
        if os.path.splitext(f.filename)[1].lower() not in SOUND_EXT:
            return fail("use an MP3, WAV, OGG or M4A file")
        data = f.read(40 * 1024 * 1024 + 1)
        if len(data) > 40 * 1024 * 1024:
            return fail("that music file is over 40 MB")
        fname = "music_" + re.sub(r"[^A-Za-z0-9_.-]", "_", os.path.basename(f.filename))[:80]
        with open(os.path.join(UP["audio"], fname), "wb") as out:
            out.write(data)
        return ok(file=fname)
    fs = {}
    for d in (DIRS["assets"], UP["audio"]):
        for f in os.listdir(d):
            if f.startswith("music_") and f.lower().endswith(SOUND_EXT):
                fs[f] = os.path.getmtime(os.path.join(d, f))
    return ok(files=sorted(fs, key=lambda f: -fs[f]))


# ---------------------------------------------------------------- Voice: text to speech (English only)
# Microsoft's online voices through edge-tts (free, no key). Without internet or the package it falls back to
# the voices built into Windows. Each voice is saved as a sound effect, so it renders like any other sound.
TTS_VOICES = [
    ("en-US-AndrewNeural", "Andrew", "US", "m"), ("en-US-BrianNeural", "Brian", "US", "m"),
    ("en-US-GuyNeural", "Guy", "US", "m"), ("en-US-ChristopherNeural", "Christopher", "US", "m"),
    ("en-US-AvaNeural", "Ava", "US", "f"), ("en-US-EmmaNeural", "Emma", "US", "f"),
    ("en-US-JennyNeural", "Jenny", "US", "f"), ("en-US-AriaNeural", "Aria", "US", "f"),
    ("en-GB-RyanNeural", "Ryan", "UK", "m"), ("en-GB-SoniaNeural", "Sonia", "UK", "f"),
    ("en-AU-WilliamNeural", "William", "AU", "m"), ("en-AU-NatashaNeural", "Natasha", "AU", "f"),
]
TTS_IDS = {v[0]: v for v in TTS_VOICES}
TTS_STATE = {"installing": False}


def _edge_ready():
    import importlib.util
    return importlib.util.find_spec("edge_tts") is not None


def tts_setup():
    """Add the edge-tts package once (small, pure Python) so the Voice tool has natural voices."""
    if _edge_ready() or TTS_STATE["installing"]:
        return
    TTS_STATE["installing"] = True
    try:
        log("Voice: adding the edge-tts package for natural English voices…")
        r = E.run([sys.executable, "-m", "pip", "install", "--quiet", "edge-tts"], capture_output=True, text=True, timeout=600)
        log("Voice: edge-tts ready" if r.returncode == 0 else "Voice: could not add edge-tts (" + (r.stderr or "")[-200:].strip() + ")")
    except Exception as e:
        log(f"Voice: could not add edge-tts ({e})")
    finally:
        TTS_STATE["installing"] = False


def _tts_windows(text, gender, rate, wav):
    """Offline fallback: the speech voices that come with Windows (David, Zira)."""
    if os.name != "nt":
        return False
    txt = wav + ".txt"
    with open(txt, "w", encoding="utf-8") as f:
        f.write(text)
    ps = ("Add-Type -AssemblyName System.Speech;$s=New-Object System.Speech.Synthesis.SpeechSynthesizer;"
          f"try{{$s.SelectVoiceByHints([System.Speech.Synthesis.VoiceGender]::{'Female' if gender == 'f' else 'Male'})}}catch{{}};"
          f"$s.Rate={max(-10, min(10, round(rate / 10)))};$s.SetOutputToWaveFile($env:CHIP_WAV);"
          "$s.Speak([IO.File]::ReadAllText($env:CHIP_TXT));$s.Dispose()")
    env = dict(os.environ, CHIP_WAV=wav, CHIP_TXT=txt)
    try:
        r = E.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps], env=env, capture_output=True, text=True, timeout=90)
        return r.returncode == 0 and os.path.isfile(wav) and os.path.getsize(wav) > 1000
    finally:
        try:
            os.remove(txt)
        except OSError:
            pass


def _int(v, d=0):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return d


def make_voice(text, voice, rate=0, pitch=0):
    """Speak the text and save it as a sound effect. Returns (file name, seconds, which engine) or raises."""
    v = TTS_IDS.get(voice) or TTS_VOICES[0]
    rate, pitch = int(max(-50, min(50, rate))), int(max(-20, min(20, pitch)))
    key = hashlib.sha1(f"{v[0]}|{rate}|{pitch}|{text}".encode()).hexdigest()[:10]
    slug = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_")[:28] or "voice"
    fname = f"voice_{slug}_{key}.mp3"
    out = os.path.join(SOUNDS_DIR, fname)
    if os.path.isfile(out):
        return fname, E.probe_duration(out), "saved"
    tmp = os.path.join(HERE, "work", f"tts_{key}")
    os.makedirs(os.path.dirname(tmp), exist_ok=True)
    raw, by = "", ""
    if _edge_ready():
        with open(tmp + ".txt", "w", encoding="utf-8") as f:
            f.write(text)
        r = E.run([sys.executable, "-m", "edge_tts", "--voice", v[0], f"--rate={rate:+d}%", f"--pitch={pitch:+d}Hz",
                   "--file", tmp + ".txt", "--write-media", tmp + ".mp3"], capture_output=True, text=True, timeout=90)
        if r.returncode == 0 and os.path.isfile(tmp + ".mp3") and os.path.getsize(tmp + ".mp3") > 500:
            raw, by = tmp + ".mp3", v[1]
        else:
            log("Voice: online voice failed, using the Windows voice (" + (r.stderr or "")[-160:].strip() + ")")
    if not raw:
        if _tts_windows(text, v[3], rate, tmp + ".wav"):
            raw, by = tmp + ".wav", "Windows voice"
        else:
            raise RuntimeError("couldn't make the voice. Check the PC's internet connection and try again")
    # trim the silence at the start and level the loudness so it sits well over the clip
    r = E.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", raw,
               "-af", "silenceremove=start_periods=1:start_threshold=-50dB,areverse,silenceremove=start_periods=1:start_threshold=-50dB,areverse,"
                      "loudnorm=I=-14:TP=-1.5:LRA=11,aresample=44100",
               "-c:a", "libmp3lame", "-q:a", "3", out], capture_output=True, text=True, timeout=120)
    for p in (tmp + ".txt", tmp + ".mp3", tmp + ".wav"):
        try:
            os.remove(p)
        except OSError:
            pass
    if r.returncode != 0 or not os.path.isfile(out):
        raise RuntimeError("couldn't save the voice: " + (r.stderr or "")[-160:].strip())
    return fname, E.probe_duration(out), by


@app.get("/api/tts")
def api_tts_voices():
    return ok(voices=[{"id": a, "name": b, "accent": c, "gender": d} for a, b, c, d in TTS_VOICES],
              natural=_edge_ready(), installing=TTS_STATE["installing"])


@app.post("/api/tts")
def api_tts():
    j = request.get_json(force=True) or {}
    text = re.sub(r"\s+", " ", str(j.get("text") or "")).strip()[:600]
    if not text:
        return fail("type what the voice should say")
    try:
        fname, secs, by = make_voice(text, str(j.get("voice") or ""), _int(j.get("rate"), 0), _int(j.get("pitch"), 0))
    except Exception as e:
        return fail(str(e))
    return ok(file=fname, secs=round(secs, 2), by=by, sounds=list_sounds())


@app.post("/api/sounds/delete")
def api_sound_delete():
    p = sound_file_path((request.get_json(force=True) or {}).get("file"))
    if p:
        os.remove(p)
    return ok(sounds=list_sounds())


@app.get("/fonts/<path:fname>")
def font_file(fname):
    return send_from_directory(FONTS_DIR, os.path.basename(fname), max_age=86400)


def sync_ytauth():
    E.YT_AUTH["mode"] = settings.get("yt_cookies") or "auto"
    E.YT_AUTH["file"] = YT_COOKIES_F


@app.post("/api/ytcookies")
def api_ytcookies():
    """cookies.txt of a browser signed in to YouTube - only from the PC itself, never through the remote link."""
    if not is_local():
        return fail("upload the cookies file on the PC itself")
    if request.form.get("delete"):
        try:
            os.remove(YT_COOKIES_F)
        except OSError:
            pass
        E.YT_AUTH["working"] = None
        return ok(has_file=False)
    f = request.files.get("file")
    if not f:
        return fail("choose the cookies.txt file")
    data = f.read(5 * 1024 * 1024)
    text = data.decode("utf-8", "replace")
    if "youtube.com" not in text or "\t" not in text:
        return fail("that does not look like a cookies.txt with YouTube cookies (Netscape format)")
    with open(YT_COOKIES_F, "w", encoding="utf-8", newline="\n") as out:
        out.write(text if text.startswith("# ") else "# Netscape HTTP Cookie File\n" + text)
    E.YT_AUTH["working"] = None
    log("YouTube cookies file saved")
    return ok(has_file=True)


@app.post("/api/start")
def api_start():
    data = request.get_json(force=True) or {}
    try:
        get_job(data).start(data.get("url", ""), data.get("template", ""), brief=data.get("brief"),
                            layout=data.get("layout") or "auto", length=data.get("length") or "",
                            speech=data.get("speech") or "")
    except Exception as e:  # noqa: BLE001
        return fail(e)
    return ok()


@app.post("/api/live/campaign")
def api_live_campaign():
    data = request.get_json(force=True) or {}
    try:
        j = get_job(data)
        j.set_campaign(data.get("brief") or "", data.get("layout") or "auto", data.get("length") or "")
    except Exception as e:  # noqa: BLE001
        return fail(e)
    return ok(campaign=j.campaign_summary())


@app.post("/api/stop")
def api_stop():
    j = get_job(request.get_json(force=True))
    watcher.stopped_by_you(j)
    j.stop()
    return ok()


@app.post("/api/clip_now")
def api_clip_now():
    j = get_job(request.get_json(force=True))
    if j.state != "live":
        return fail("that stream is not live")
    j.enqueue(0, manual=True)
    return ok()


@app.post("/api/capture")
def api_capture():
    data = request.get_json(force=True) or {}
    url = data.get("url") or ""
    rec = None
    try:
        if url:
            url = E.parse_source(url)[2]
        for j in JOBS.values():          # reuse a running recording of the same stream
            if j.state == "live" and j.rec and (j.url == url or (not url and int(data.get("slot") or 0) == j.slot)):
                rec, url = j.rec.path, j.url
        if not url:
            return fail("paste a stream link first")
        good, err = E.capture_frame(FRAME, rec, url)
    except Exception as e:  # noqa: BLE001
        return fail(e)
    if not good:
        return fail("could not grab a frame: " + err)
    w, h = E.probe_size(FRAME)
    return ok(w=w, h=h, t=time.time())


@app.get("/frame.jpg")
def frame():
    if not os.path.exists(FRAME):
        abort(404)
    resp = send_from_directory(DIRS["tmp"], "frame.jpg")
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.post("/api/templates")
def api_save_template():
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()[:40]
    if not name:
        return fail("template needs a name")
    templates[name] = E.merged_template(data.get("template"))
    save_json(TEMPLATES_F, templates)
    if data.get("activate", True):
        settings["template"] = name
        save_json(SETTINGS_F, settings)
    return ok()


@app.delete("/api/templates/<name>")
def api_del_template(name):
    if name in templates and len(templates) > 1:
        del templates[name]
        if settings.get("template") == name:
            settings["template"] = next(iter(templates))
        save_json(TEMPLATES_F, templates)
        save_json(SETTINGS_F, settings)
    return ok()


@app.get("/api/clips")
def api_clips():
    out = []
    for f in os.listdir(DIRS["meta"]):
        if f.endswith(".json"):
            m = load_json(os.path.join(DIRS["meta"], f), None)
            if m:
                m.pop("chat", None)
                m["avoid"] = avoid_hits(m)
                m.pop("words", None)
                out.append(m)
    out.sort(key=lambda m: m.get("created", 0), reverse=True)
    return ok(clips=out[:200])


def avoid_hits(m):
    """Words-to-avoid found in the clip's text or speech (brand-unsafe words, banned names...)."""
    bad = [w.strip().lower() for w in re.split(r"[,\n]+", settings.get("avoid_words") or "") if w.strip()]
    if not bad:
        return []
    text = " ".join([m.get("hook") or "", m.get("caption") or "", m.get("yt_title") or "", " ".join(m.get("hashtags") or []),
                     m.get("transcript") or ""]).lower()
    bleeped = [(a, b) for a, b in ((m.get("scene") or {}).get("bleep") or {}).get("spans") or []]
    spoken_ok = lambda w: any(a - 0.05 <= ws and we <= b + 0.05 for a, b in bleeped
                              for ws, we, t in (m.get("words") or []) if w in str(t).lower())
    return [w for w in bad if re.search(r"(?<!\w)" + re.escape(w) + r"(?!\w)", text) and not spoken_ok(w)][:6]


@app.post("/api/clips/<name>/rate")
def api_rate(name):
    m = load_json(meta_path(name), None)
    if not m:
        return fail("clip not found")
    try:
        stars = max(0, min(5, int((request.get_json(force=True) or {}).get("stars") or 0)))
    except (TypeError, ValueError):
        return fail("bad rating")
    update_meta(name, rating=stars or None, rated_at=time.time())
    TASTE["at"] = 0
    return ok(rating=stars)


@app.route("/api/wordfixes", methods=["GET", "POST"])
def api_wordfixes():
    """'Always fix' list: a word the captions keep getting wrong -> the right spelling, for every new clip."""
    fx = dict(settings.get("word_fixes") or {})
    if request.method == "POST":
        d = request.get_json(force=True) or {}
        frm = re.sub(r"\s+", " ", str(d.get("from") or "")).strip().lower()[:60]
        if not frm:
            return fail("type the wrong word")
        if d.get("delete"):
            fx.pop(frm, None)
        else:
            to = re.sub(r"\s+", " ", str(d.get("to") or "")).strip()[:60]
            if not to:
                return fail("type the right spelling")
            fx[frm] = to
        settings["word_fixes"] = dict(sorted(fx.items())[:400])
        save_json(SETTINGS_F, settings)
    return ok(fixes=settings.get("word_fixes") or {})


def clean_text_fields(data):
    upd = {k: data[k] for k in ("hook", "caption", "hashtags", "yt_title") if k in data}
    if isinstance(upd.get("hashtags"), str):
        upd["hashtags"] = [t if t.startswith("#") else "#" + t for t in upd["hashtags"].split() if t]
    for k in ("trim_start", "trim_end"):
        if data.get(k) not in (None, ""):
            try:
                upd[k] = max(0.0, float(data[k]))
            except (TypeError, ValueError):
                pass
    return upd


@app.post("/api/clips/<name>/render")
def api_rerender(name):
    m = read_meta(name)
    if not m:
        return fail("no such clip", 404)
    data = request.get_json(force=True) or {}
    upd = clean_text_fields(data)
    update_meta(name, status="queued", **upd)
    tpl = data.get("template")
    if data.get("use_active"):
        tpl = active_template()
    sc = m.get("scene")
    if sc and isinstance(tpl, dict):
        for l in sc.get("layers", []):
            if l.get("kind") == "captions" and tpl.get("caption_style") in E.CAPTION_STYLES:
                l["style"] = tpl["caption_style"]
        if data.get("apply_crops"):
            sc = E.style_to_scene(sc, tpl)
        update_meta(name, scene=E.clean_scene(sc))
    jobs.put({"kind": "restyle", "name": name, "template": tpl, "regen": bool(data.get("regen"))})
    return ok()


@app.post("/api/clips/<name>/edit")
def api_edit(name):
    """Save everything from the clip editor and re-render."""
    m = read_meta(name)
    if not m:
        return fail("no such clip", 404)
    if not os.path.exists(os.path.join(DIRS["raw"], name + ".mp4")):
        return fail("the source of this clip was deleted, it can't be edited")
    d = request.get_json(force=True) or {}
    upd = clean_text_fields(d)
    if isinstance(d.get("template"), dict):
        upd["template"] = E.merged_template(d["template"])
    if isinstance(d.get("words"), list):
        words = []
        for w in d["words"]:
            try:
                words.append([round(float(w[0]), 3), round(float(w[1]), 3), str(w[2])[:60]])
            except (TypeError, ValueError, IndexError):
                continue
        upd["words"] = words
        upd["transcript"] = " ".join(w[2] for w in words)
    if isinstance(d.get("overlays"), list):
        upd["overlays"] = [{"text": str(o.get("text", ""))[:120], "start": float(o.get("start") or 0),
                            "end": float(o.get("end") or 0), "x": float(o.get("x", 0.5)), "y": float(o.get("y", 0.3)),
                            "size": int(o.get("size") or 70), "color": str(o.get("color") or "#ffffff")[:9],
                            "box": bool(o.get("box"))} for o in d["overlays"][:20] if str(o.get("text", "")).strip()]
    if isinstance(d.get("logo"), dict):
        lg = d["logo"]
        upd["logo"] = {"enabled": bool(lg.get("enabled")), "file": os.path.basename(str(lg.get("file") or "")),
                       "x": float(lg.get("x", 0.85)), "y": float(lg.get("y", 0.06)),
                       "w": float(lg.get("w", 0.2)), "opacity": float(lg.get("opacity", 1))}
    if isinstance(d.get("scene"), dict):
        if len(json.dumps(d["scene"])) > 400_000:
            return fail("this edit is too big to save")
        upd["scene"] = E.clean_scene(d["scene"])
        cap = next((l for l in upd["scene"]["layers"] if l["kind"] == "captions"), None)
        if cap:
            upd.setdefault("template", E.merged_template(m.get("template")))["caption_style"] = cap["style"]
    if isinstance(d.get("cuts"), list):
        ts_ = upd.get("trim_start", m.get("trim_start") or 0)
        te_ = upd.get("trim_end", m.get("trim_end")) or 1e9
        upd["cuts"] = E.clean_cuts(d["cuts"], ts_, te_)
    if upd.get("trim_end") is not None and upd.get("trim_start") is not None and upd["trim_end"] - upd["trim_start"] < 3:
        return fail("the clip must be at least 3 seconds long")
    if upd.get("cuts") and (upd.get("trim_end") or 0) - (upd.get("trim_start") or 0) - sum(b - a for a, b in upd["cuts"]) < 3:
        return fail("after the cuts the clip must still be at least 3 seconds long")
    update_meta(name, status="queued", **upd)
    jobs.put({"kind": "restyle", "name": name})
    return ok()


@app.get("/api/clips/<name>/full")
def api_clip_full(name):
    """Everything the editor needs (words included)."""
    m = read_meta(name)
    if not m:
        return fail("no such clip", 404)
    m["has_source"] = os.path.exists(os.path.join(DIRS["raw"], name + ".mp4"))
    m.pop("chat", None)
    if not m.get("scene") and request.args.get("scene"):      # the phone app's editor needs a scene to start from
        try:
            m["scene"] = E.clean_scene(E.layout_scene("auto", m.get("template")))
        except Exception as e:  # noqa: BLE001
            log(f"default scene failed: {e}")
    return ok(clip=m, styles={k: {"label": v["label"], "words": v["words"], "upper": v["upper"], "mode": v["mode"], **v["css"],
                                  "size": v["size"], "outline": v["outline"], "font": v["font"]} for k, v in E.CAPTION_STYLES.items()})


@app.get("/raw/<path:fname>")
def raw_media(fname):
    return send_from_directory(DIRS["raw"], fname, conditional=True)


@app.get("/assets/<path:fname>")
def asset(fname):
    p = asset_path(fname)
    if not os.path.isfile(p):
        abort(404)
    return send_from_directory(os.path.dirname(p), os.path.basename(p))


@app.post("/api/assets/logo")
def api_logo_upload():
    f = request.files.get("file")
    if not f or not f.filename:
        return fail("choose an image")
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in (".png", ".jpg", ".jpeg", ".webp"):
        return fail("use a PNG (transparent works best), JPG or WEBP")
    fname = f"logo_{time.strftime('%Y%m%d_%H%M%S')}{ext}"
    f.save(os.path.join(UP["images"], fname))
    settings["last_logo"] = fname
    save_json(SETTINGS_F, settings)
    return ok(file=fname)


@app.post("/api/clips/<name>/translate")
def api_translate(name):
    """Nepali (or other) version of a clip: post text and/or the on-screen captions."""
    d = request.get_json(force=True) or {}
    lang = d.get("lang") or "ne"
    m = read_meta(name)
    if not m:
        return fail("clip not found")
    out = {}
    try:
        if d.get("meta", True):
            tr = E.translate_meta(settings, m, lang, m.get("ctx"))
            i18n = m.get("i18n") or {}
            i18n[lang] = tr
            update_meta(name, i18n=i18n)
            out["meta"] = tr
        if d.get("captions"):
            src = [tuple(w) for w in (m.get("words_orig") or m.get("words") or [])]
            if not src:
                return fail("this clip has no captions to translate")
            if d.get("restore"):
                update_meta(name, words=src, words_orig=None, cap_lang="")
                out["words"] = src
            else:
                new = E.translate_words(settings, src, lang, log)
                update_meta(name, words=new, words_orig=src, cap_lang=lang)
                out["words"] = new
            if d.get("render", True):
                jobs.put({"kind": "restyle", "name": name})
    except Exception as e:  # noqa: BLE001
        return fail(str(e)[:200])
    return ok(**out)


TT = TW.TikTokWeb(os.path.join(HERE, "tiktok"), log)
VSR = NV.NvidiaVSR(log)
STT = NV.GpuWhisper(VSR, log)
NV.STT = STT
E.GPU_STT = STT.transcribe


# ---------------------------------------------------------------- add-ons you can add or remove later
def _tiktok_browser():
    base = os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "ms-playwright")
    try:
        return any(d.startswith("chromium") for d in os.listdir(base))
    except OSError:
        return False


ADDON = {"busy": "", "error": ""}


def _addon_job(label, fn):
    if ADDON["busy"]:
        raise RuntimeError("already " + ADDON["busy"])
    ADDON.update(busy=label, error="")

    def go():
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            ADDON["error"] = str(e)[:300]
            log(f"[add-ons] {label} failed: {e}")
        finally:
            ADDON["busy"] = ""
    threading.Thread(target=go, daemon=True).start()


def _install_tiktok_browser():
    r = E.run([sys.executable, "-m", "playwright", "install", "chromium"], capture_output=True, text=True, timeout=1800)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "playwright failed")[-200:])
    log("[add-ons] TikTok posting browser ready")


def _remove_nvidia():
    if VSR.busy or STT.busy:
        raise RuntimeError("the NVIDIA pack is busy, try again in a moment")
    STT.stop()
    shutil.rmtree(NV.ROOT, ignore_errors=True)
    VSR.state, STT.state = {}, {}
    log("[add-ons] NVIDIA AI pack removed")


@app.get("/api/addons")
def api_addons():
    return ok(tiktok_browser=_tiktok_browser(), nvidia=os.path.isdir(NV.ROOT), busy=ADDON["busy"], error=ADDON["error"])


@app.post("/api/addons")
def api_addons_do():
    if not is_local():
        return fail("add or remove add-ons on the PC itself")
    what = (request.get_json(force=True) or {}).get("do")
    try:
        if what == "tiktok_browser":
            _addon_job("installing the TikTok posting browser", _install_tiktok_browser)
        elif what == "remove_nvidia":
            _remove_nvidia()
        else:
            return fail("unknown add-on")
    except Exception as e:  # noqa: BLE001
        return fail(e)
    return api_addons()


@app.post("/api/stt/install")
def api_stt_install():
    if LITE:
        return fail(LITE_MSG)
    if not is_local():
        return fail("install on the PC itself")
    try:
        STT.install()
    except Exception as e:  # noqa: BLE001
        return fail(e)
    return ok(**STT.public())


@app.post("/api/clips/<name>/recaption")
def api_recaption(name):
    """Make the captions again from the clip's sound, in the chosen language (editor: Captions tab)."""
    m = read_meta(name)
    raw = raw_p(name)
    if not m or not os.path.isfile(raw):
        return fail("this clip has no source video")
    body = request.get_json(force=True, silent=True) or {}
    code = body.get("speech") or ""
    if code and code not in E.SPEECH:
        return fail("unknown language")
    # "AI captions" in the editor: the best model, on this PC's graphics card (large-v3) or on Groq
    src = body.get("source") or ""
    opts = dict(settings)
    if src == "groq":
        opts["stt"] = "groq"
    elif src == "pc":
        opts["stt"] = "auto"
    opts["stt_quality"] = "best" if src in ("pc", "groq") or body.get("best") else "auto"
    t0 = time.time()
    log(f"[captions] {name}: AI captions ({'Groq' if src == 'groq' else 'graphics card, large model' if src == 'pc' else 'auto'})")
    words = E.transcribe_auto(raw, DIRS["tmp"], opts, log, speech=code or None,
                              context=" - ".join(x for x in ((m.get("ctx") or {}).get("channel"), m.get("yt_title")) if x))
    if words is None:
        return fail("captions failed: " + (E.LAST_WHISPER_ERROR[0] or "unknown error"))
    update_meta(name, words=[list(w) for w in words], transcript=" ".join(w[2] for w in words), speech=code,
                captions_by=E.LAST_STT[0])
    return ok(words=[list(w) for w in words], seconds=round(time.time() - t0, 1), language=E.LAST_LANG[0], by=E.LAST_STT[0])


@app.get("/api/enhance")
def api_enhance():
    return ok(**VSR.public(), stt=STT.public())


@app.post("/api/enhance/install")
def api_enhance_install():
    if LITE:
        return fail(LITE_MSG)
    if IS_MAC:
        return fail("AI Enhance needs an NVIDIA graphics card, so it only works on Windows PCs")
    if not is_local():
        return fail("install AI Enhance on the PC itself")
    try:
        VSR.install()
    except Exception as e:  # noqa: BLE001
        return fail(e)
    return ok(**VSR.public())


@app.get("/api/clips/<name>/enhance")
def api_clip_enhance_status(name):
    return ok(**enh_status(name))


@app.post("/api/clips/<name>/enhance")
def api_clip_enhance(name):
    if LITE:
        return fail(LITE_MSG)
    if IS_MAC and not (request.get_json(force=True, silent=True) or {}).get("undo"):
        return fail("AI Enhance needs an NVIDIA graphics card, so it only works on Windows PCs")
    if not read_meta(name):
        return fail("clip not found")
    d = request.get_json(force=True, silent=True) or {}
    try:
        if d.get("undo"):
            undo_enhance(name)
        elif d.get("delete_original"):
            if not (read_meta(name) or {}).get("enhanced"):
                return fail("enhance the clip first")
            os.remove(raw_p(name, "orig"))
            update_meta(name, has_original=False)
        else:
            start_enhance(name, (d.get("quality") or settings.get("enhance_quality") or "HIGH").upper(), d.get("sharpen"))
    except Exception as e:  # noqa: BLE001
        return fail(e)
    return ok(**enh_status(name))


@app.get("/rawlite/<name>.mp4")
def raw_lite(name):
    """Phone / remote preview: the light 1080p copy of an enhanced source (the 4K one is too heavy remotely)."""
    name = os.path.basename(name)
    raw, lite = raw_p(name), raw_p(name, "lite")
    m = read_meta(name) or {}
    if m.get("enhanced") and os.path.isfile(raw) and not os.path.isfile(lite):
        with LITE_LOCK:                               # made once, the first time the clip is opened remotely
            if not os.path.isfile(lite):
                E.run(["ffmpeg", "-v", "error", "-y", "-i", raw, "-vf", "scale=-2:1080:flags=lanczos", "-c:v", "libx264",
                       "-preset", "veryfast", "-crf", "25", "-c:a", "copy", "-movflags", "+faststart", lite + ".tmp.mp4"])
                if os.path.isfile(lite + ".tmp.mp4"):
                    os.replace(lite + ".tmp.mp4", lite)
    fn = os.path.basename(lite) if (m.get("enhanced") and os.path.isfile(lite)) else os.path.basename(raw)
    return send_from_directory(DIRS["raw"], fn, conditional=True)


PROXY_LOCK = threading.Lock()


def make_proxy(name):
    """A light copy of the clip's source for phone editing: 720p, 30 fps, about 2.5 Mbit/s.
    Made on the graphics card's video encoder when there is one (about a second), else on the processor."""
    src, out = raw_p(name), raw_p(name, "proxy")
    if not os.path.isfile(src):
        raise RuntimeError("this clip has no source video")
    with PROXY_LOCK:
        if os.path.isfile(out) and os.path.getmtime(out) >= os.path.getmtime(src):
            return out
        tmp = out + ".tmp.mp4"
        vf = "scale=-2:'min(720,ih)':flags=bicubic,fps=30"
        tries = [["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", "28", "-b:v", "2500k", "-maxrate", "4M"],
                 ["-c:v", "libx264", "-preset", "veryfast", "-crf", "28", "-maxrate", "4M", "-bufsize", "8M"]]
        for enc in tries:
            r = E.run(["ffmpeg", "-v", "error", "-y", "-i", src, "-vf", vf, *enc, "-g", "30", "-pix_fmt", "yuv420p",
                       "-c:a", "aac", "-b:a", "96k", "-movflags", "+faststart", tmp])
            if r.returncode == 0 and os.path.isfile(tmp) and os.path.getsize(tmp) > 1000:
                os.replace(tmp, out)
                log(f"[proxy] {name}: phone editing copy made ({os.path.getsize(out) / 1e6:.1f} MB)")
                return out
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise RuntimeError("could not make the phone copy")


@app.post("/api/clips/<name>/proxy")
def api_clip_proxy(name):
    """Editor: 'Proxy from PC' - make (or reuse) the light editing copy, then the phone downloads it."""
    name = os.path.basename(name)
    try:
        p = make_proxy(name)
    except Exception as e:  # noqa: BLE001
        return fail(str(e)[:200])
    return ok(url=f"/rawproxy/{name}.mp4", size=os.path.getsize(p))


@app.get("/rawproxy/<name>.mp4")
def raw_proxy(name):
    name = os.path.basename(name)
    if not os.path.isfile(raw_p(name, "proxy")):
        return fail("no phone copy yet", 404)
    return send_from_directory(DIRS["raw"], os.path.basename(raw_p(name, "proxy")), conditional=True)


@app.post("/api/enhance/check")
def api_enhance_check():
    if not is_local():
        return fail("run the check on the PC itself")
    if VSR.busy:
        return fail("busy: " + VSR.busy)
    threading.Thread(target=VSR.check, daemon=True).start()
    return ok()


# ------------------------------------------------------------------ posting groups (one tap -> all of a brand's accounts)
def all_targets():
    """[(target key, display name)] of every connected account."""
    pub = ACC.public()
    out = [("yt:" + c["id"], c.get("title", "")) for c in pub["youtube"]["channels"]]
    out += [("ig:" + a["id"], a.get("username", "")) for a in pub["instagram"]["accounts"]]
    out += [("fb:" + p["id"], p.get("name", "")) for p in pub["facebook"]["pages"]]
    out += [("tt:" + a["id"], a.get("username") or a.get("name", "")) for a in TT.public()]
    return out


def auto_groups():
    """Group accounts by brand name: 'Sano Clip', '@sano_clip', 'SkillIssue', '@prowid_skillissue'..."""
    norm = lambda x: re.sub(r"[^a-z0-9]", "", str(x).lower())
    tg = all_targets()
    keys = []
    for k, name in tg:                          # brand names come from YouTube channels and Facebook Pages
        if k[:3] in ("yt:", "fb:") and len(norm(name)) >= 4 and norm(name) not in [n for n, _ in keys]:
            keys.append((norm(name), name))
    groups = []
    for key, label in keys:
        members = [k for k, name in tg if norm(name) and (key in norm(name) or norm(name) in key)]
        if len(members) >= 2:
            groups.append({"id": key[:20], "name": label, "targets": members})
    return groups


@app.get("/api/groups")
def api_groups():
    return ok(groups=ACC.data.get("groups") or [], targets=[{"key": k, "name": n} for k, n in all_targets()])


@app.post("/api/groups")
def api_groups_save():
    if not is_local():
        return fail("edit posting groups on the PC")
    d = request.get_json(force=True) or {}
    if d.get("auto"):
        found = auto_groups()
        cur = {g["name"].lower(): g for g in ACC.data.get("groups") or []}
        for g in found:
            if g["name"].lower() in cur:
                cur[g["name"].lower()]["targets"] = list(dict.fromkeys(cur[g["name"].lower()]["targets"] + g["targets"]))
            else:
                cur[g["name"].lower()] = g
        groups = list(cur.values())
    else:
        valid = {k for k, _ in all_targets()}
        groups = []
        for g in (d.get("groups") or [])[:20]:
            name = str(g.get("name") or "").strip()[:40]
            if name:
                groups.append({"id": str(g.get("id") or os.urandom(3).hex())[:20], "name": name,
                               "targets": [t for t in g.get("targets") or [] if t in valid]})
    ACC.data["groups"] = groups
    ACC.save()
    return ok(groups=groups)


@app.post("/api/tiktok/login")
def api_tiktok_login():
    if not is_local():
        return fail("log in to TikTok on the PC itself - the login window opens there")
    try:
        TT.start_login()
    except TW.TikTokError as e:
        return fail(e)
    return ok(login=TT.login_state)


@app.get("/api/tiktok")
def api_tiktok():
    return ok(accounts=TT.public(), login=TT.login_state)


@app.post("/api/tiktok/remove")
def api_tiktok_remove():
    if not is_local():
        return fail("remove TikTok accounts on the PC itself")
    try:
        TT.remove((request.get_json(force=True) or {}).get("id"))
    except TW.TikTokError as e:
        return fail(e)
    return ok(accounts=TT.public())


@app.post("/api/tiktok/browser")
def api_tiktok_browser():
    if not is_local():
        return fail("this opens a browser window on the PC - do it there")
    try:
        msg = TT.open_browser((request.get_json(force=True) or {}).get("id"))
    except TW.TikTokError as e:
        return fail(e)
    return ok(msg=msg)


@app.post("/api/accounts/lang")
def api_account_lang():
    """Language each account posts in (e.g. Sano Clip -> Nepali)."""
    d = request.get_json(force=True) or {}
    lang = d.get("lang") or ""
    if lang and lang not in E.LANGS:
        return fail("unknown language")
    langs = ACC.data.setdefault("langs", {})
    if lang:
        langs[d.get("target", "")] = lang
    else:
        langs.pop(d.get("target", ""), None)
    ACC.save()
    return ok(langs=ACC.data["langs"])


def _same_file(folder, data, ext=None):
    """An existing file in [folder] with exactly these bytes (so the same picture is never stored twice)."""
    n = len(data)
    h = None
    try:
        names = os.listdir(folder)
    except OSError:
        return None
    for f in names:
        p = os.path.join(folder, f)
        if not os.path.isfile(p) or os.path.getsize(p) != n or (ext and os.path.splitext(f)[1].lower() != ext):
            continue
        h = h or hashlib.sha1(data).hexdigest()
        with open(p, "rb") as fh:
            if hashlib.sha1(fh.read()).hexdigest() == h:
                return f
    return None


@app.post("/api/assets/image")
def api_image_upload():
    """Backgrounds, stickers and logos for the clip editor."""
    f = request.files.get("file")
    if not f or not f.filename:
        return fail("choose an image")
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
        return fail("use a PNG, JPG, WEBP or GIF image")
    data = f.read(30 * 1024 * 1024 + 1)
    if len(data) > 30 * 1024 * 1024:
        return fail("that image is over 30 MB")
    same = _same_file(UP["images"], data) or _same_file(DIRS["assets"], data)
    if same:
        w, h = E.probe_size(asset_path(same))
        return ok(file=same, w=w, h=h, existing=True)
    fname = f"img_{time.strftime('%Y%m%d_%H%M%S')}_{os.urandom(2).hex()}{ext}"
    with open(os.path.join(UP["images"], fname), "wb") as out:
        out.write(data)
    w, h = E.probe_size(os.path.join(UP["images"], fname))
    return ok(file=fname, w=w, h=h)


UP_KINDS = {"audio": SOUND_EXT, "fx": SOUND_EXT, "images": (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"), "other": None}


@app.post("/api/uploads")
def api_uploads_add():
    """Any file from the phone or browser, kept on the PC in uploads\\audio, fx, images or other."""
    f = request.files.get("file")
    if not f or not f.filename:
        return fail("choose a file")
    kind = request.form.get("kind") or ""
    ext = os.path.splitext(f.filename)[1].lower()
    if kind not in UP_KINDS:
        kind = "images" if ext in UP_KINDS["images"] else "fx" if ext in SOUND_EXT else "other"
    if UP_KINDS[kind] and ext not in UP_KINDS[kind]:
        return fail({"audio": "use an MP3, WAV, OGG or M4A file", "fx": "use an MP3, WAV, OGG or M4A file",
                     "images": "use a PNG, JPG, WEBP or GIF image"}[kind])
    if ext in (".exe", ".bat", ".cmd", ".ps1", ".vbs", ".js", ".msi", ".dll", ".scr", ".com", ".lnk"):
        return fail("that kind of file can't be uploaded")
    cap = {"audio": 40, "fx": 15, "images": 30, "other": 200}[kind] * 1024 * 1024
    data = f.read(cap + 1)
    if len(data) > cap:
        return fail(f"that file is over {cap // 1048576} MB")
    same = _same_file(UP[kind], data) or (_same_file(DIRS["assets"], data) if kind in ("images", "audio") else None)
    if same:
        extra = {}
        if kind == "images":
            extra["w"], extra["h"] = E.probe_size(asset_path(same))
        return ok(kind=kind, file=same, existing=True, **extra)
    if kind == "audio":
        fname = "music_" + safe_name(f.filename)
    elif kind == "images":
        fname = f"img_{time.strftime('%Y%m%d_%H%M%S')}_{os.urandom(2).hex()}{ext}"
    else:
        fname = safe_name(f.filename)
        if os.path.exists(os.path.join(UP[kind], fname)):
            b, e = os.path.splitext(fname)
            fname = f"{b}_{os.urandom(2).hex()}{e}"
    p = os.path.join(UP[kind], fname)
    with open(p, "wb") as out:
        out.write(data)
    extra = {}
    if kind == "images":
        extra["w"], extra["h"] = E.probe_size(p)
    log(f"[upload] {kind}/{fname} ({len(data) // 1024} KB)")
    return ok(kind=kind, file=fname, **extra)


FILE_DIRS = {**UP, "older": DIRS["assets"]}
OLDER_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".mp3", ".wav", ".ogg", ".m4a")


def _dupes(d):
    """Groups of byte-identical files in folder [d]: {kept_name: [copies...]}, keeping the oldest."""
    by = {}
    for f in os.listdir(d):
        p = os.path.join(d, f)
        if os.path.isfile(p) and f != "aliases.json":
            by.setdefault(os.path.getsize(p), []).append(f)
    out = {}
    for fs in by.values():
        if len(fs) < 2:
            continue
        hs = {}
        for f in fs:
            with open(os.path.join(d, f), "rb") as fh:
                hs.setdefault(hashlib.sha1(fh.read()).hexdigest(), []).append(f)
        for g in hs.values():
            if len(g) > 1:
                g.sort(key=lambda f: os.path.getmtime(os.path.join(d, f)))
                out[g[0]] = g[1:]
    return out


@app.get("/api/uploads")
def api_uploads_list():
    out, dup = {}, 0
    for k, d in FILE_DIRS.items():
        fs = [(os.path.getmtime(os.path.join(d, f)), f, os.path.getsize(os.path.join(d, f))) for f in os.listdir(d)
              if os.path.isfile(os.path.join(d, f)) and f != "aliases.json" and (k != "older" or os.path.splitext(f)[1].lower() in OLDER_EXT)]
        copies = {c for cs in _dupes(d).values() for c in cs}
        dup += len(copies & {f for _, f, _ in fs})
        out[k] = [{"file": f, "size": sz, "time": int(t), "dupe": f in copies} for t, f, sz in sorted(fs, reverse=True)]
    return ok(folders=out, path=UPLOADS, dupes=dup)


@app.get("/uploads/<kind>/<path:fname>")
def upload_file(kind, fname):
    if kind not in UP:
        abort(404)
    return send_from_directory(UP[kind], os.path.basename(fname))


@app.post("/api/uploads/delete")
def api_uploads_delete():
    """Delete one or more uploaded files: {kind, file} or {files: [{kind, file}, ...]}."""
    d = request.get_json(force=True) or {}
    items = d.get("files") or [{"kind": d.get("kind"), "file": d.get("file")}]
    gone = 0
    for it in items[:500]:
        k, fname = (it or {}).get("kind"), os.path.basename((it or {}).get("file") or "")
        if k in FILE_DIRS and fname and fname != "aliases.json" and os.path.isfile(os.path.join(FILE_DIRS[k], fname)):
            if k == "older" and os.path.splitext(fname)[1].lower() not in OLDER_EXT:
                continue
            os.remove(os.path.join(FILE_DIRS[k], fname))
            gone += 1
    if gone:
        log(f"[upload] deleted {gone} file(s)")
    return api_uploads_list()


@app.post("/api/uploads/dedupe")
def api_uploads_dedupe():
    """Remove byte-identical copies, keeping the oldest. Clips that used a removed copy still find the kept one."""
    al, n = _aliases(), 0
    for k, d in FILE_DIRS.items():
        for keep, copies in _dupes(d).items():
            for c in copies:
                if k == "older" and os.path.splitext(c)[1].lower() not in OLDER_EXT:
                    continue
                os.remove(os.path.join(d, c))
                al[c] = keep
                n += 1
    for k, v in list(al.items()):          # follow chains so every name points at a file that exists
        while v in al and al[v] != v:
            v = al[v]
        al[k] = v
    with open(ALIASES_F, "w", encoding="utf-8") as fh:
        json.dump(al, fh)
    log(f"[upload] removed {n} duplicate file(s)")
    r = api_uploads_list().get_json()
    r["removed"] = n
    return jsonify(r)


_YT_ID = re.compile(r"(?:v=|youtu\.be/|/shorts/|/live/|/embed/|/v/)([A-Za-z0-9_-]{11})")


@app.post("/api/uploads/ytthumb")
def api_yt_thumb():
    """Save a YouTube video's thumbnail at the best size YouTube has (usually 1280x720) into uploads\\images."""
    url = str((request.get_json(force=True) or {}).get("url") or "").strip()
    m = _YT_ID.search(url) or (re.fullmatch(r"[A-Za-z0-9_-]{11}", url) and re.match(r"(.*)", url))
    if not m:
        return fail("paste a YouTube video link")
    vid = m.group(1)
    fname = f"img_yt_{vid}.jpg"
    p = os.path.join(UP["images"], fname)
    if not os.path.isfile(p):
        cands = []
        try:  # yt-dlp knows every size YouTube keeps; take the biggest
            import yt_dlp
            with yt_dlp.YoutubeDL({"quiet": True, "skip_download": True, "no_warnings": True}) as y:
                info = y.extract_info(f"https://www.youtube.com/watch?v={vid}", download=False, process=False)
            ts = [t for t in (info.get("thumbnails") or []) if t.get("url") and ".jpg" in t["url"].split("?")[0]]
            ts.sort(key=lambda t: -((t.get("width") or 0) * (t.get("height") or 0) or t.get("preference") or 0))
            cands = [t["url"] for t in ts[:3]]
        except Exception:
            pass
        cands += [f"https://i.ytimg.com/vi/{vid}/{n}.jpg" for n in ("maxresdefault", "sddefault", "hqdefault")]
        data = None
        for u in cands:
            try:
                with urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "Mozilla/5.0"}), timeout=15) as r:
                    b = r.read(15 * 1024 * 1024)
                if len(b) > 3000:  # YouTube's grey placeholder is ~1 KB
                    data = b
                    break
            except Exception:
                continue
        if not data:
            return fail("couldn't get that video's thumbnail")
        with open(p, "wb") as out:
            out.write(data)
        log(f"[upload] YouTube thumbnail {vid}")
    w, h = E.probe_size(p)
    return ok(kind="images", file=fname, w=w, h=h)


@app.get("/api/assets/images")
def api_image_list():
    """Every image uploaded to the editor, newest first - so the phone sees the same images as the laptop."""
    out = []
    for d in (DIRS["assets"], UP["images"]):
        for f in os.listdir(d):
            if f.startswith(("img_", "logo_")) and os.path.splitext(f)[1].lower() in (".png", ".jpg", ".jpeg", ".webp", ".gif"):
                out.append((os.path.getmtime(os.path.join(d, f)), f))
    return ok(files=[f for _, f in sorted(out, reverse=True)[:80]])


@app.route("/api/layouts", methods=["GET", "POST"])
def api_layouts():
    """Saved editor layouts ('Carry Minati': GTA art on top, clip in the middle, Carry art below...)."""
    d = load_json(LAYOUTS_F, {})
    if request.method == "GET":
        return ok(layouts=[{"name": k, "scene": v.get("scene"), "at": v.get("at")} for k, v in
                           sorted(d.items(), key=lambda kv: -(kv[1].get("at") or 0))])
    b = request.get_json(force=True) or {}
    name = re.sub(r"\s+", " ", str(b.get("name") or "")).strip()[:40]
    if not name:
        return fail("give the layout a name")
    if b.get("delete"):
        d.pop(name, None)
    else:
        if not isinstance(b.get("scene"), dict) or len(json.dumps(b["scene"])) > 400_000:
            return fail("nothing to save")
        d[name] = {"scene": E.clean_scene(b["scene"]), "at": time.time()}
    save_json(LAYOUTS_F, d)
    return ok(layouts=[{"name": k, "scene": v.get("scene"), "at": v.get("at")} for k, v in
                       sorted(d.items(), key=lambda kv: -(kv[1].get("at") or 0))])


@app.post("/api/style")
def api_style():
    """'My style': a saved editor look that new clips can use automatically."""
    d = request.get_json(force=True) or {}
    if isinstance(d.get("scene"), dict):
        if len(json.dumps(d["scene"])) > 400_000:
            return fail("this style is too big to save")
        settings["style_scene"] = E.clean_scene(d["scene"])
    if "on" in d:
        if d["on"] and not settings.get("style_scene"):
            return fail("save a style first")
        settings["style_on"] = bool(d["on"])
    save_json(SETTINGS_F, settings)
    return ok(style_on=settings["style_on"])


@app.post("/api/clips/<name>/meta")
def api_save_meta(name):
    data = request.get_json(force=True) or {}
    upd = clean_text_fields(data)
    if isinstance(data.get("i18n"), dict):          # edited Nepali (or other) versions of the post text
        cur = (read_meta(name) or {}).get("i18n") or {}
        for lang, v in data["i18n"].items():
            if lang in E.LANGS and isinstance(v, dict):
                t = clean_text_fields(v)
                cur[lang] = {"hook": t.get("hook", ""), "yt_title": t.get("yt_title", ""), "caption": t.get("caption", ""),
                             "hashtags": t.get("hashtags", [])}
        upd["i18n"] = cur
    m = update_meta(name, **upd)
    write_post_txt(m)
    return ok()


def _is_posted(m):
    return any((v or {}).get("status") == "posted" for v in (m.get("posts") or {}).values())


@app.delete("/api/clips/<name>")
def api_delete(name):
    """Delete a clip from the PC. A clip that was posted keeps its thumbnail and post links (so its views can still be
    tracked in Posted); deleting it again from Posted, or with ?full=1, removes everything."""
    name = os.path.basename(name)
    m = read_meta(name) or {}
    keep = _is_posted(m) and not m.get("archived") and request.args.get("full") != "1"
    files = [os.path.join(out_dir(), name + ".mp4"), os.path.join(out_dir(), name + ".txt"),
             os.path.join(DIRS["raw"], name + ".mp4"), raw_p(name, "orig"), raw_p(name, "lite"), raw_p(name, "proxy")]
    files += [os.path.join(d, f) for d in (DIRS["enh"], DIRS["thumbs"], DIRS["raw"]) for f in os.listdir(d) if f.startswith(name + "__")]
    if not keep:
        files += [os.path.join(DIRS["thumbs"], name + ".jpg"), meta_path(name)]
    freed = 0
    for p in files:
        try:
            sz = os.path.getsize(p)
            os.remove(p)
            freed += sz
        except OSError:
            pass
    if keep:
        update_meta(name, archived=True, archived_at=time.time(), status="done")
        log(f"[clips] {name}: video deleted ({freed / 1e6:.0f} MB), kept in Posted for views")
    else:
        log(f"[clips] {name}: deleted completely")
    return ok(archived=keep, freed=freed)


@app.get("/media/<path:fname>")
def media(fname):
    return send_from_directory(out_dir(), fname)


# ------------------------------------------------------------------ thumbnails (covers)
# ------------------------------------------------------------------ NVIDIA AI enhance (whole source, in place)
LITE_LOCK = threading.Lock()
ENH = {}                       # name -> {"state": queued|running|finishing|done|error, "pct", "eta", "quality", "error"}


def raw_p(name, kind=""):
    return os.path.join(DIRS["raw"], name + (f".{kind}" if kind else "") + ".mp4")


SHARPEN = {   # after the AI upscale (on the 4K picture): crisper edges + a little punch so it shows on a phone
    "off": "",
    "light": "unsharp=7:7:0.6:7:7:0,eq=contrast=1.03:saturation=1.05",
    "medium": "unsharp=7:7:1.0:7:7:0,eq=contrast=1.05:saturation=1.08",
    "strong": "unsharp=9:9:1.4:7:7:0,eq=contrast=1.07:saturation=1.12",
}


def finish_enhanced(up, prep, new, sharpen):
    """Put the sound back; with sharpening the 4K video is re-encoded on the graphics card (processor if that fails)."""
    vf = SHARPEN.get(sharpen or "off", "")
    base = ["ffmpeg", "-v", "error", "-y", "-i", up, "-i", prep, "-map", "0:v", "-map", "1:a?"]
    tries = [["-c", "copy"]] if not vf else [
        ["-vf", vf, "-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "21", "-b:v", "20M", "-maxrate", "30M",
         "-bufsize", "60M", "-pix_fmt", "yuv420p", "-c:a", "copy"],
        ["-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "17", "-pix_fmt", "yuv420p", "-c:a", "copy"]]
    err = ""
    for t in tries:
        r = E.run(base + t + ["-movflags", "+faststart", new], capture_output=True, text=True)
        if r.returncode == 0 and os.path.isfile(new):
            return
        err = (r.stderr or "")[-200:]
    raise RuntimeError("could not finish the enhanced video: " + err)


def enhance_job(name, quality, sharpen=None):
    """AI-upscale the clip's whole source (1080p -> 4K, 30 fps) and put it in place of the source, so preview,
    trimming, cuts, renders and thumbnails all use it. The original stays as <name>.orig.mp4 for undo / other quality."""
    job = ENH[name]
    raw, orig = raw_p(name), raw_p(name, "orig")
    prep = os.path.join(DIRS["tmp"], f"{name}_enh_in.mp4")
    up = os.path.join(DIRS["tmp"], f"{name}_enh_up.mp4")
    new = os.path.join(DIRS["tmp"], f"{name}_enh_new.mp4")
    try:
        if not os.path.isfile(orig):
            if (read_meta(name) or {}).get("enhanced"):
                raise RuntimeError("the original was deleted - this clip can't be enhanced again")
            if not os.path.isfile(raw):
                raise RuntimeError("this clip has no source video any more")
            shutil.copyfile(raw, orig)                 # keep the original for undo / another quality
        has_a = E.has_audio(orig)
        job.update(state="running", pct=0, step="preparing")
        r = E.run(["ffmpeg", "-v", "error", "-y", "-i", orig, "-vf", "fps=30,scale=-2:'min(1080,ih)':flags=lanczos",
                   "-c:v", "libx264", "-preset", "veryfast", "-crf", "12"] + (["-c:a", "copy"] if has_a else ["-an"]) + [prep],
                  capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError("could not prepare the video: " + (r.stderr or "")[-200:])
        dur = max(1.0, E.probe_duration(prep))
        t0 = time.time()

        def prog(pc):
            el = time.time() - t0
            job.update(pct=pc, step="enhancing", eta=int(el * (100 - pc) / pc) if pc >= 3 else None)
        job["step"] = "waiting for the graphics card" if NV.GPU_LOCK.locked() else "enhancing"
        VSR.enhance(prep, up, 2, quality, progress=prog)
        sharpen = sharpen if sharpen in SHARPEN else (settings.get("enhance_sharpen") or "medium")
        job.update(pct=99, step="sharpening" if SHARPEN.get(sharpen) else "finishing", state="finishing")
        finish_enhanced(up, prep, new, sharpen)
        for kind in ("lite", "proxy"):              # phone copies are remade from the new video when needed
            try:
                os.remove(raw_p(name, kind))
            except OSError:
                pass
        for attempt in range(120):                     # the editor preview may hold the old file for a moment
            try:
                os.replace(new, raw)
                break
            except PermissionError:
                if attempt == 119:
                    raise RuntimeError("the source is in use - close the clip's preview and press Enhance again")
                time.sleep(0.5)
        update_meta(name, enhanced={"quality": quality, "sharpen": sharpen, "at": time.time(), "secs": round(time.time() - t0)},
                    has_original=True)
        log(f"{name}: AI enhanced ({quality}, sharpen {sharpen}) in {time.time() - t0:.0f} s for {dur:.0f} s of video")
        job.update(state="done", pct=100, step="done")
    except Exception as e:  # noqa: BLE001
        log(f"{name}: AI enhance failed - {e}")
        job.update(state="error", error=str(e)[:300])
        if not os.path.isfile(raw) and os.path.isfile(orig):       # never leave a clip without a source
            shutil.copyfile(orig, raw)
    finally:
        for f in (prep, up, new):
            try:
                os.remove(f)
            except OSError:
                pass


def start_enhance(name, quality, sharpen=None):
    if not VSR.ready():
        raise RuntimeError("AI Enhance is not installed - Settings - Files - AI Enhance (NVIDIA RTX)")
    if quality not in ("MEDIUM", "HIGH", "ULTRA"):
        quality = "HIGH"
    j = ENH.get(name)
    if j and j.get("state") in ("queued", "running", "finishing"):
        raise RuntimeError("this clip is already being enhanced")
    ENH[name] = {"state": "queued", "pct": 0, "quality": quality, "step": "queued", "started": time.time()}
    threading.Thread(target=enhance_job, args=(name, quality, sharpen), daemon=True).start()


def undo_enhance(name):
    orig = raw_p(name, "orig")
    if not os.path.isfile(orig):
        raise RuntimeError("the original was deleted - this clip stays enhanced")
    for attempt in range(60):
        try:
            os.replace(orig, raw_p(name))
            break
        except PermissionError:
            if attempt == 59:
                raise RuntimeError("the source is in use - close the preview and try again")
            time.sleep(0.5)
    try:
        os.remove(raw_p(name, "lite"))
    except OSError:
        pass
    m = read_meta(name) or {"name": name}
    m.pop("enhanced", None)
    m["has_original"] = False
    write_meta(m)


def enh_status(name):
    m = read_meta(name) or {}
    j = ENH.get(name) or {}
    queued_ahead = sum(1 for k, v in ENH.items() if k != name and v.get("state") in ("running", "finishing"))
    return {"job": j, "enhanced": m.get("enhanced"), "has_original": os.path.isfile(raw_p(name, "orig")),
            "ahead": queued_ahead if j.get("state") == "queued" or j.get("step") == "waiting for the graphics card" else 0,
            "ready": VSR.ready(), "mtime": int(os.path.getmtime(raw_p(name))) if os.path.isfile(raw_p(name)) else 0}


def cut_source(name, m, raw, scene, words):
    """Apply the clip's cut-out parts. -> (source, start, end, scene, words, temp file or None)."""
    ts = m.get("trim_start") or 0
    te = m.get("trim_end") or E.probe_duration(raw)
    cuts = E.clean_cuts(m.get("cuts"), ts, te)
    if not cuts or not scene:
        return raw, ts, m.get("trim_end"), scene, words, None
    path = E.apply_cuts(raw, ts, te, cuts, DIRS["tmp"], name + "_" + os.urandom(2).hex())
    sc, w = E.remap_cuts(scene, words, ts, cuts)
    return path, 0.0, None, sc, w, path


def cover_p(name, kind="cover.jpg"):
    return os.path.join(DIRS["thumbs"], f"{name}__{kind}")


def clean_video(name):
    """The clip without subtitles/hook (for thumbnails with their own headline). Made once, on demand."""
    path = cover_p(name, "clean.mp4")
    out = os.path.join(out_dir(), name + ".mp4")
    if os.path.isfile(path) and os.path.getmtime(path) >= os.path.getmtime(out):
        return path
    m = read_meta(name) or {}
    raw = os.path.join(DIRS["raw"], name + ".mp4")
    sc = m.get("scene")
    if not sc or not os.path.isfile(raw):
        return out
    sc = json.loads(json.dumps(sc))
    sc["layers"] = [l for l in sc.get("layers", []) if l.get("kind") not in ("captions", "hook", "text", "third")]
    src, st, en, sc, _, tmpcut = cut_source(name, m, raw, sc, [])
    try:
        ok, err = E.render_scene(src, path, sc, [], "", DIRS["tmp"], start=st, end=en, assets_dir=DIRS["assets"])
    finally:
        if tmpcut and os.path.isfile(tmpcut):
            os.remove(tmpcut)
    return path if ok else out


def clean_segment(name, t):
    """A clean 3 s piece starting at t (quick - used for automatic covers so no full re-render is needed)."""
    out = os.path.join(out_dir(), name + ".mp4")
    full = cover_p(name, "clean.mp4")
    if os.path.isfile(full) and os.path.getmtime(full) >= os.path.getmtime(out):
        return full, t
    m = read_meta(name) or {}
    raw = os.path.join(DIRS["raw"], name + ".mp4")
    sc = m.get("scene")
    if not sc or not os.path.isfile(raw):
        return out, t
    if m.get("cuts"):
        return clean_video(name), t           # cut clips: the quick piece can't skip the cuts, use the full clean copy
    sc = json.loads(json.dumps(sc))
    sc["layers"] = [l for l in sc.get("layers", []) if l.get("kind") not in ("captions", "hook", "text", "third")]
    sc["fx"], sc["sfx"] = [], []
    seg = cover_p(name, "cleanseg.mp4")
    st = (m.get("trim_start") or 0) + t
    ok_, _ = E.render_scene(raw, seg, sc, [], "", DIRS["tmp"], start=st, end=st + 3.2, assets_dir=DIRS["assets"])
    return (seg, 0.0) if ok_ else (out, t)


def thumb_source(name, spec, quick=False):
    """-> (picture/video to take the frame from, spec with the right time)."""
    out = os.path.join(out_dir(), name + ".mp4")
    sp = TH.clean(spec)
    if sp["src"] == "upload" and os.path.isfile(cover_p(name, "upload.jpg")):
        return cover_p(name, "upload.jpg"), spec
    if not sp["clean"]:
        return out, spec
    if quick:
        src, t = clean_segment(name, sp["t"])
        return src, {**spec, "t": t}
    return clean_video(name), spec


def render_cover(name, spec, path, width=1080, quick=False, hook=None):
    m = read_meta(name) or {}
    if hook is not None:
        m = {**m, "hook": hook}
    spec = TH.apply_hook(spec, m)
    src, sp = thumb_source(name, spec, quick)
    return TH.render_thumb(src, sp, path, DIRS["tmp"], width=width, photos_dir=DIRS["thumbs"])


def _new_photo(name):
    return f"{name}__ph_{os.urandom(3).hex()}.jpg"


@app.post("/api/clips/<name>/thumb/photo")
def api_thumb_photo(name):
    """A photo for a picture-in-picture window on the thumbnail: upload one, or grab a frame from the stream."""
    m = read_meta(name)
    if not m:
        return fail("clip not found")
    fn = _new_photo(name)
    dest = os.path.join(DIRS["thumbs"], fn)
    if request.files.get("file"):
        f = request.files["file"]
        ext = os.path.splitext(f.filename or "")[1].lower()
        if ext not in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"):
            return fail("use a JPG, PNG or WEBP picture")
        data = f.read(30 * 1024 * 1024 + 1)
        if len(data) > 30 * 1024 * 1024:
            return fail("that picture is over 30 MB")
        tmp = os.path.join(DIRS["tmp"], f"{name}_ph{ext}")
        with open(tmp, "wb") as fh:
            fh.write(data)
        src, args = tmp, []
        dest = os.path.join(UP["images"], fn)
    else:
        d = request.get_json(force=True, silent=True) or {}
        raw = os.path.join(DIRS["raw"], name + ".mp4")
        src = raw if os.path.isfile(raw) else os.path.join(out_dir(), name + ".mp4")
        t = (m.get("trim_start") or 0) + max(0.0, float(d.get("t") or 0)) if src == raw else max(0.0, float(d.get("t") or 0))
        args, tmp = ["-ss", f"{t:.2f}"], None
    r = E.run(["ffmpeg", "-v", "error", "-y"] + args + ["-i", src, "-frames:v", "1", "-vf",
              "scale='min(1920,iw)':-2:flags=lanczos,format=yuvj420p", "-q:v", "2", dest], capture_output=True, text=True)
    if tmp:
        try:
            os.remove(tmp)
        except OSError:
            pass
    if r.returncode != 0 or not os.path.isfile(dest):
        return fail("could not read that picture")
    w, h = E.probe_size(dest)
    return ok(file=fn, w=w, h=h)


@app.post("/api/clips/<name>/thumb/upload")
def api_thumb_upload(name):
    """Your own picture: used as the thumbnail as it is, or as the background to design on."""
    if not read_meta(name):
        return fail("clip not found")
    f = request.files.get("file")
    if not f or not f.filename:
        return fail("choose a picture")
    ext = os.path.splitext(f.filename)[1].lower()
    if ext not in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"):
        return fail("use a JPG, PNG or WEBP picture")
    data = f.read(30 * 1024 * 1024 + 1)
    if len(data) > 30 * 1024 * 1024:
        return fail("that picture is over 30 MB")
    tmp = os.path.join(DIRS["tmp"], f"{name}_thumbup{ext}")
    with open(tmp, "wb") as fh:
        fh.write(data)
    r = E.run(["ffmpeg", "-v", "error", "-y", "-i", tmp, "-frames:v", "1", "-vf",
               "scale='min(2160,iw)':-2:flags=lanczos,format=yuvj420p", "-q:v", "2", cover_p(name, "upload.jpg")],
              capture_output=True, text=True)
    try:
        os.remove(tmp)
    except OSError:
        pass
    if r.returncode != 0:
        return fail("could not read that picture")
    if request.form.get("as_is") == "1":
        update_meta(name, thumb=TH.clean({"src": "upload", "pop": 0, "vignette": 0, "zoom": 1, "text": "", "design": "upload"}))
        try:
            make_cover(name)
        except Exception as e:  # noqa: BLE001
            return fail(f"could not make the thumbnail: {e}")
    return ok(v=time.time())


def make_cover(name, m=None, hook=None):
    """Design the clip's thumbnail: the one the user saved, or the automatic 'hook frame' look."""
    m = m or read_meta(name) or {}
    out = os.path.join(out_dir(), name + ".mp4")
    spec = m.get("thumb")
    if not isinstance(spec, dict):
        spec = TH.designs(m, m.get("duration") or E.probe_duration(out), m.get("scene"))[
            {"hook": 0, "headline": 1, "circle": 2, "boxed": 3, "react": 4, "clean": 5}.get(settings.get("thumb_auto") or "hook", 0)]
    ok, err = render_cover(name, spec, cover_p(name), quick=True, hook=hook)
    if not ok:
        raise RuntimeError(err)
    E.run(["ffmpeg", "-v", "error", "-y", "-i", cover_p(name), "-vf", "scale=270:-2", "-q:v", "4",
           os.path.join(DIRS["thumbs"], name + ".jpg")])
    return cover_p(name)


def cover_for(name):
    p = cover_p(name)
    if not os.path.isfile(p):
        try:
            make_cover(name)
        except Exception:  # noqa: BLE001
            return None
    return p if os.path.isfile(p) else None


@app.get("/api/clips/<name>/thumb")
def api_thumb_get(name):
    m = read_meta(name)
    if not m:
        return fail("clip not found")
    out = os.path.join(out_dir(), name + ".mp4")
    dur = m.get("duration") or E.probe_duration(out)
    if m.get("trim_end"):
        dur = (m.get("trim_end") or dur) - (m.get("trim_start") or 0)
    dur -= sum(b - a for a, b in E.clean_cuts(m.get("cuts"), m.get("trim_start") or 0, m.get("trim_end") or 1e9))
    if request.args.get("hook") is not None:
        m = {**m, "hook": request.args.get("hook")}
    return ok(design=m.get("cover_design") if isinstance(m.get("cover_design"), dict) else None,
              spec=TH.apply_hook(TH.clean(m["thumb"]), m) if isinstance(m.get("thumb"), dict) else None,
              designs=TH.designs(m, dur, m.get("scene")), duration=round(dur, 2), hook=m.get("hook") or "",
              has=os.path.isfile(cover_p(name)), v=int(os.path.getmtime(cover_p(name))) if os.path.isfile(cover_p(name)) else 0)


@app.post("/api/clips/<name>/cover/design")
def api_cover_design(name):
    """The thumbnail editor draws the cover in the browser: keep the picture as the clip's thumbnail and the
    design (layers, photos, text) so it can be opened and changed again."""
    m = read_meta(name)
    if not m:
        return fail("clip not found")
    d = request.get_json(force=True) or {}
    img = str(d.get("image") or "")
    if not img.startswith("data:image/jpeg;base64,") or len(img) > 16 * 1024 * 1024:
        return fail("no picture to save")
    design = d.get("design") if isinstance(d.get("design"), dict) else None
    if design is not None and len(json.dumps(design)) > 300_000:
        return fail("the design is too big to save")
    import base64
    try:
        raw = base64.b64decode(img.split(",", 1)[1], validate=True)
    except (ValueError, TypeError):
        return fail("could not read the picture")
    tmp = os.path.join(DIRS["tmp"], f"{name}_coverdesign.jpg")
    with open(tmp, "wb") as fh:
        fh.write(raw)
    r = E.run(["ffmpeg", "-v", "error", "-y", "-i", tmp, "-frames:v", "1", "-vf", "format=yuvj420p", "-q:v", "2",
               cover_p(name, "upload.jpg")], capture_output=True, text=True)
    try:
        os.remove(tmp)
    except OSError:
        pass
    if r.returncode != 0:
        return fail("could not read the picture")
    update_meta(name, cover_design=design,
                thumb=TH.clean({"src": "upload", "pop": 0, "vignette": 0, "zoom": 1, "text": "", "design": "upload"}))
    try:
        make_cover(name)
    except Exception as e:  # noqa: BLE001
        return fail(f"could not make the thumbnail: {e}")
    return ok(v=time.time())


@app.post("/api/clips/<name>/thumb/preview")
def api_thumb_preview(name):
    if not read_meta(name):
        return fail("clip not found")
    data = request.get_json(force=True) or {}
    spec = data.get("spec") or {}
    slot = re.sub(r"\W", "", str(data.get("slot") or "p"))[:12] or "p"
    path = cover_p(name, f"prev_{slot}.jpg")
    ok_, err = render_cover(name, spec, path, width=int(data.get("width") or 540),
                            hook=data.get("hook") if isinstance(data.get("hook"), str) else None)
    if not ok_:
        return fail("could not draw the thumbnail: " + err[-200:])
    return ok(url=f"/cover/{name}__prev_{slot}.jpg?{time.time():.3f}")


@app.post("/api/clips/<name>/thumb")
def api_thumb_save(name):
    m = read_meta(name)
    if not m:
        return fail("clip not found")
    data = request.get_json(force=True) or {}
    if data.get("auto"):
        m.pop("thumb", None)
        write_meta(m)
    else:
        update_meta(name, thumb=TH.clean(data.get("spec") or {}))
    try:
        make_cover(name, hook=data.get("hook") if isinstance(data.get("hook"), str) else None)
    except Exception as e:  # noqa: BLE001
        return fail(f"could not make the thumbnail: {e}")
    return ok(v=time.time())


@app.get("/cover/<path:fname>")
def cover_file(fname):
    return send_from_directory(DIRS["thumbs"], os.path.basename(fname), max_age=0)


@app.get("/thumb/<path:fname>")
def thumb(fname):
    fname = os.path.basename(fname)
    if not os.path.isfile(os.path.join(DIRS["thumbs"], fname)) and os.path.isfile(os.path.join(UP["images"], fname)):
        return send_from_directory(UP["images"], fname)
    return send_from_directory(DIRS["thumbs"], fname)


# ---------------------------------------------------------------- updates for the Windows installer version
# Only a copy installed with ChipperStudioSetup has a VERSION file. It asks the website's update service for the
# newest published version; the user presses Update, Chipper waits until nothing is running, then the installer runs
# quietly (keeping their choices, clips and settings) and Chipper starts again by itself.
UPDATE_SITE = "https://chipperstudio.nepconlabs.com"


def _read_version():
    try:
        with open(os.path.join(HERE, "VERSION"), encoding="utf-8-sig") as f:
            return f.read().strip()
    except OSError:
        return ""


def _vnum(label):
    m = re.match(r"^v?(\d+)\.(\d+)(?:\.(\d+))?$", str(label or "").replace("pc-", "").strip())
    return int(m[1]) * 1000000 + int(m[2]) * 10000 + int(m[3] or 0) if m else 0


class Updater:
    def __init__(self):
        self.current = _read_version()
        self.latest = None            # {label, version, notes, required, size, url}
        self.phase = ""               # "" | waiting | downloading | installing | error
        self.pct = 0
        self.error = ""
        self.checked = 0
        self.lock = threading.Lock()

    @property
    def installed(self):
        return bool(self.current) and os.name == "nt"

    def available(self):
        return bool(self.latest and _vnum(self.latest["label"]) > _vnum(self.current))

    def info(self):
        return {"installed": self.installed, "current": self.current, "latest": self.latest, "available": self.available(),
                "phase": self.phase, "pct": self.pct, "error": self.error, "checked": self.checked}

    def check(self):
        if not self.installed:
            return
        try:
            req = urllib.request.Request(UPDATE_SITE + "/api/latest?app=windows", headers={"User-Agent": f"ChipperStudio/{self.current}"})
            with urllib.request.urlopen(req, timeout=20) as r:
                j = json.loads(r.read().decode("utf-8"))
            if j.get("ok") is False or not j.get("label"):
                return
            self.latest = {"label": j["label"], "notes": str(j.get("notes") or "")[:3000], "required": bool(j.get("required")),
                           "size": int(j.get("size") or 0), "url": j.get("url") or UPDATE_SITE + "/api/download?app=windows"}
            self.checked = time.time()
            if self.available():
                log(f"update: Chipper Studio {self.latest['label']} is available (you have {self.current})")
        except Exception as e:  # noqa: BLE001
            log(f"update check failed: {e}")

    def loop(self):
        time.sleep(5)
        while True:
            self.check()
            time.sleep(6 * 3600)

    @staticmethod
    def busy():
        live = [j for j in JOBS.values() if j.state != "idle"]
        return bool(live) or vod.busy() or jobs.qsize() > 0 or posts.qsize() > 0 or WORKING["render"] > 0 or WORKING["post"] > 0

    def start(self):
        with self.lock:
            if self.phase in ("waiting", "downloading", "installing"):
                return
            if not self.available():
                raise RuntimeError("you already have the newest version")
            self.phase, self.pct, self.error = "waiting", 0, ""
        threading.Thread(target=self._run, daemon=True).start()

    def cancel(self):
        if self.phase == "waiting":
            self.phase = ""

    def _run(self):
        try:
            if self.busy():
                log("update: waiting until nothing is recording, rendering or posting")
            while self.busy():
                time.sleep(15)
                if self.phase != "waiting":
                    return
            self.phase = "downloading"
            work = os.path.join(HERE, "work", "update")
            shutil.rmtree(work, ignore_errors=True)
            os.makedirs(work, exist_ok=True)
            pkg = os.path.join(work, "setup.zip")
            req = urllib.request.Request(self.latest["url"], headers={"User-Agent": f"ChipperStudio/{self.current}"})
            with urllib.request.urlopen(req, timeout=60) as r, open(pkg, "wb") as f:
                total = int(r.headers.get("Content-Length") or self.latest.get("size") or 0)
                got = 0
                while True:
                    b = r.read(1 << 20)
                    if not b:
                        break
                    f.write(b)
                    got += len(b)
                    if total:
                        self.pct = min(99, round(got * 100 / total))
            exe = pkg
            if zipfile.is_zipfile(pkg):
                with zipfile.ZipFile(pkg) as z:
                    names = [n for n in z.namelist() if n.lower().endswith(".exe")]
                    if not names:
                        raise RuntimeError("the download has no installer in it")
                    z.extract(names[0], work)
                    exe = os.path.join(work, names[0])
            elif not open(pkg, "rb").read(2) == b"MZ":
                raise RuntimeError("the download is not an installer")
            self.pct, self.phase = 100, "installing"
            log(f"update: installing Chipper Studio {self.latest['label']}, Chipper restarts by itself in a minute")
            py = os.path.join(HERE, "python", "python.exe")
            if not os.path.isfile(py):
                py = sys.executable
            cmd = os.path.join(work, "update.cmd")
            with open(cmd, "w", encoding="utf-8") as f:
                f.write("@echo off\r\ntitle Updating Chipper Studio\r\necho Updating Chipper Studio, please wait...\r\n"
                        "timeout /t 3 /nobreak >nul\r\n"
                        f'"{exe}" /VERYSILENT /SUPPRESSMSGBOXES /NORESTART /SP- /LOG="{os.path.join(work, "setup.log")}"\r\n'
                        f'start "" /min "{py}" "{os.path.join(HERE, "app.py")}" --no-browser --updated\r\n')
            subprocess.Popen(["cmd", "/c", cmd], cwd=work, creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0), close_fds=True)
            time.sleep(1.5)
            os._exit(0)
        except Exception as e:  # noqa: BLE001
            self.phase, self.error = "error", str(e)[:300]
            log(f"update failed: {e}")


UPD = Updater()


@app.get("/api/update")
def api_update():
    return ok(**UPD.info(), local=is_local())


@app.post("/api/update")
def api_update_do():
    if not is_local():
        return fail("update Chipper on the PC itself")
    d = request.get_json(force=True) or {}
    try:
        if d.get("do") == "check":
            UPD.check()
        elif d.get("do") == "cancel":
            UPD.cancel()
        else:
            UPD.start()
    except Exception as e:  # noqa: BLE001
        return fail(e)
    return ok(**UPD.info(), local=True)


@app.post("/api/restart")
def api_restart():
    """Start a fresh copy of Chipper in a new window, then close this one (works from the remote link too)."""
    busy = [j.slot for j in JOBS.values() if j.state != "idle"] if isinstance(JOBS, dict) else \
        [j.slot for j in JOBS if j.state != "idle"]
    if busy and not (request.get_json(silent=True) or {}).get("force"):
        log(f"restart requested while stream slot(s) {busy} running - stopping them")

    def go():
        time.sleep(1.0)
        for j in (JOBS.values() if isinstance(JOBS, dict) else JOBS):
            try:
                if j.state != "idle":
                    j.stop()
            except Exception:
                pass
        try:
            tunnel.stop(manual=False)
        except Exception:
            pass
        script = os.path.abspath(__file__)
        if os.name == "nt":
            subprocess.Popen(f'cmd /c "timeout /t 4 /nobreak >nul & "{sys.executable}" "{script}""',
                             cwd=HERE, creationflags=subprocess.CREATE_NEW_CONSOLE)
        else:
            subprocess.Popen(["sh", "-c", f'sleep 4; exec "{sys.executable}" "{script}"'], cwd=HERE,
                             start_new_session=True)
        os._exit(0)

    log("restarting Chipper...")
    threading.Thread(target=go, daemon=True).start()
    return ok()


@app.post("/api/open")
def api_open():
    what = (request.get_json(force=True) or {}).get("what", "out")
    path = out_dir() if what == "out" else UPLOADS if what == "uploads" else DIRS["rec"]
    if os.name == "nt":
        os.startfile(path)  # noqa: S606
    elif IS_MAC:
        subprocess.Popen(["open", path])
    return ok(path=path)


@app.get("/api/accounts")
def api_accounts():
    pub = ACC.public()
    pub["tiktok"] = {"accounts": TT.public(), "login": TT.login_state}
    pub["groups"] = ACC.data.get("groups") or []
    if not is_local():      # remote: only what's needed to choose where to post, no keys or IDs of apps
        pub = {"youtube": {k: pub["youtube"][k] for k in ("connected", "privacy", "channels")},
               "instagram": {"connected": pub["instagram"]["connected"],
                             "accounts": [{"id": a["id"], "username": a["username"]} for a in pub["instagram"]["accounts"]]},
               "facebook": pub["facebook"], "langs": pub.get("langs", {}),
               "tiktok": {"accounts": pub["tiktok"]["accounts"]}, "groups": pub["groups"]}
        return ok(accounts=pub, remote=True)
    return ok(accounts=pub, redirect_uri=YT.redirect_uri)


@app.post("/api/accounts")
def api_accounts_save():
    data = request.get_json(force=True) or {}
    for plat, keys in (("youtube", ("client_id", "client_secret", "privacy")),
                       ("instagram", ("app_id", "app_secret"))):
        for k in keys:
            v = (data.get(plat) or {}).get(k)
            if v is None or str(v).startswith("••••"):
                continue
            ACC.data[plat][k] = str(v).strip()
    ACC.save()
    return ok(accounts=ACC.public())


@app.get("/oauth/youtube/start")
def yt_start():
    try:
        return redirect(YT.auth_url())
    except P.PostError as e:
        return f"<h3>{e}</h3>", 400


@app.get("/oauth/youtube")
def yt_callback():
    if request.args.get("error"):
        return page_msg("YouTube connection cancelled", request.args["error"])
    try:
        title = YT.finish(request.args.get("code", ""), request.args.get("state", ""))
    except P.PostError as e:
        return page_msg("Could not connect YouTube", str(e))
    log(f"YouTube channel connected: {title}")
    return page_msg("YouTube connected ✔", f"Channel: {title}. You can close this tab.")


def page_msg(title, text):
    return (f"<body style='background:#0e0f13;color:#e9ebf0;font-family:Segoe UI;padding:60px;text-align:center'>"
            f"<h2>{title}</h2><p>{text}</p><script>setTimeout(()=>window.close(),2500)</script></body>")


@app.post("/api/accounts/instagram/connect")
def ig_connect():
    try:
        found = IG.connect((request.get_json(force=True) or {}).get("token", ""))
    except (P.PostError, KeyError) as e:
        return fail(e)
    log("Meta connected: " + ", ".join(found))
    return ok(accounts=ACC.public(), found=found)


@app.post("/api/accounts/<plat>/disconnect")
def acc_disconnect(plat):
    if plat == "youtube":
        YT.remove((request.get_json(silent=True) or {}).get("id"))
    elif plat == "instagram":
        IG.remove((request.get_json(silent=True) or {}).get("id"))
    elif plat == "facebook":
        FB.remove((request.get_json(silent=True) or {}).get("id"))
    elif plat == "meta":                  # disconnect everything from the Meta connection
        IG.remove(None)
        FB.remove(None)
    ACC.save()
    return ok(accounts=ACC.public())


@app.post("/api/clips/<name>/post")
def api_post(name):
    m = read_meta(name)
    if not m or m.get("status") != "done":
        return fail("clip is not ready yet")
    data = request.get_json(force=True) or {}
    pub = ACC.public()
    ig_ids = {a["id"] for a in pub["instagram"]["accounts"]}
    yt_ids = {c["id"] for c in pub["youtube"]["channels"]}
    fb_ids = {p["id"] for p in pub["facebook"]["pages"]}
    plats = []
    for p in data.get("platforms", []):
        if p.startswith("yt:") and p[3:] in yt_ids:
            plats.append(p)
        elif p == "youtube" and len(yt_ids) == 1:
            plats.append("yt:" + next(iter(yt_ids)))
        elif p.startswith("fb:") and p[3:] in fb_ids:
            plats.append(p)
        elif p.startswith("ig:") and p[3:] in ig_ids:
            plats.append(p)
        elif p.startswith("tt:") and p[3:] in {a["id"] for a in TT.accounts}:
            plats.append(p)
        elif p == "instagram" and len(ig_ids) == 1:
            plats.append("ig:" + next(iter(ig_ids)))
    if not plats:
        return fail("pick at least one account to post to")
    due = 0
    if data.get("at"):
        try:
            due = float(data["at"])
        except (TypeError, ValueError):
            return fail("bad schedule time")
        if due < time.time() - 60:
            return fail("that time is in the past")
        if due > time.time() + 60 * 86400:
            return fail("schedule at most 60 days ahead")
    for p in plats:
        if due or post_gate(p) > 0:      # later, or this account must wait (daily limit / spacing)
            set_post(name, p, status="scheduled", error="", url="", due=due or time.time(), privacy=data.get("privacy"),
                     step="scheduled for " + time.strftime("%a %d %b %H:%M", time.localtime(due)) if due else "")
            POST_PENDING.add(name)
        else:
            set_post(name, p, status="queued", error="", url="")
            posts.put((name, p, {"privacy": data.get("privacy")}))
    return ok(scheduled=bool(due))


def _files(d, test=lambda f: True):
    out = []
    for root, _, fs in os.walk(d):
        for f in fs:
            p = os.path.join(root, f)
            try:
                if test(f):
                    out.append((p, os.path.getsize(p), os.path.getmtime(p)))
            except OSError:
                pass
    return out


def storage_groups():
    """What is filling the disk, in groups that can be cleaned. Clip sources of the last 7 days are never offered."""
    now = time.time()
    posted_old = set()
    for f in os.listdir(DIRS["meta"]):
        if f.endswith(".json"):
            m = load_json(os.path.join(DIRS["meta"], f), None) or {}
            if any(v.get("status") == "posted" for v in (m.get("posts") or {}).values()) and now - (m.get("created") or now) > 7 * 86400:
                posted_old.add(f[:-5])
    busy_rec = any(j.state not in ("idle", "error", "done", "stopped") for j in JOBS.values())
    return {
        "tmp": ("Temporary files", "safe - leftovers from making clips", _files(DIRS["tmp"]) + _files(DIRS["enh"])),
        "lite": ("Phone preview copies", "safe - made again when you open the clip on your phone",
                 _files(DIRS["raw"], lambda f: f.endswith((".lite.mp4", ".proxy.mp4")))),
        "orig": ("Originals of AI-enhanced clips", "you can no longer undo the enhancement",
                 [] if any(j.get("state") not in ("done", "error") for j in ENH.values()) else
                 _files(DIRS["raw"], lambda f: f.endswith(".orig.mp4"))),
        "rec": ("Old live recordings", "recordings older than 1 day" + (" (skipped while a stream records)" if busy_rec else ""),
                [] if busy_rec else [x for x in _files(DIRS["rec"]) if now - x[2] > 86400]),
        "vod": ("Downloaded videos", "full videos you clipped - older than 1 day" + ("" if vod.state in ("idle", "done", "error") else " (skipped: a video is being clipped)"),
                [x for x in _files(DIRS["vod"]) if now - x[2] > 86400] if vod.state in ("idle", "done", "error") else []),
        "posted": ("Sources of posted clips (older than 7 days)", "these clips can no longer be edited or re-rendered",
                   [x for x in _files(DIRS["raw"], lambda f: f.split(".")[0] in posted_old)]),
    }


@app.route("/api/storage", methods=["GET", "POST"])
def api_storage():
    if request.method == "POST":
        what = [w for w in (request.get_json(force=True) or {}).get("what") or [] if isinstance(w, str)]
        groups, freed, n = storage_groups(), 0, 0
        for w in what:
            for p, size, _ in (groups.get(w) or ("", "", []))[2]:
                try:
                    os.remove(p)
                    freed += size
                    n += 1
                except OSError:
                    pass
            if w == "orig":
                for f in os.listdir(DIRS["meta"]):
                    if f.endswith(".json"):
                        m = load_json(os.path.join(DIRS["meta"], f), None) or {}
                        if m.get("has_original"):
                            update_meta(f[:-5], has_original=False)
        log(f"storage: removed {n} files, freed {freed / 1e9:.1f} GB")
        return ok(freed=freed, files=n)
    du = shutil.disk_usage(WORK)
    groups = storage_groups()
    return ok(free=du.free, total=du.total, groups=[{"id": k, "label": v[0], "note": v[1], "files": len(v[2]),
                                                     "bytes": sum(x[1] for x in v[2])} for k, v in groups.items()],
              used={k: sum(x[1] for x in _files(d)) for k, d in DIRS.items() if k in ("rec", "raw", "vod", "tmp", "enh")})


YTFEED = DS.YouTubeFeed(os.path.join(HERE, "youtube_feed.json"), log)
CAMPS = DS.Campaigns(os.path.join(HERE, "campaigns.json"))


def watched_youtube():
    return [c["key"] for c in watcher.data.get("channels", []) if c.get("platform") == "youtube"]


@app.get("/api/youtube")
def api_youtube():
    return ok(**YTFEED.get(watched_youtube(), force=request.args.get("refresh") == "1"))


@app.post("/api/youtube/channels")
def api_youtube_channels():
    d = request.get_json(force=True) or {}
    try:
        if d.get("remove"):
            YTFEED.remove(str(d["remove"]))
        else:
            r = YTFEED.add(str(d.get("add") or ""))
            return ok(name=r["name"])
    except Exception as e:  # noqa: BLE001
        return fail(e)
    return ok()


@app.route("/api/campaigns", methods=["GET", "POST"])
def api_campaigns():
    if request.method == "POST":
        d = request.get_json(force=True) or {}
        try:
            if d.get("delete"):
                CAMPS.delete(str(d["delete"]))
                return ok(mine=CAMPS.mine())
            return ok(row=CAMPS.save(d), mine=CAMPS.mine())
        except RuntimeError as e:
            return fail(e)
    return ok(mine=CAMPS.mine(), board=CAMPS.board_rows(force=request.args.get("refresh") == "1"))


@app.get("/api/disk")
def api_disk():
    du = shutil.disk_usage(WORK)
    return ok(free=du.free, total=du.total)


@app.get("/api/clips/<name>/posts")
def api_clip_posts(name):
    """Where this clip is being posted and how far each one has got (the app's posting progress)."""
    m = read_meta(os.path.basename(name))
    if not m:
        return fail("clip not found")
    return ok(posts=m.get("posts") or {}, status=m.get("status", ""), progress=m.get("progress", 0),
              rendered=m.get("rendered", 0), error=m.get("error", ""))


@app.get("/api/clips/<name>/stats")
def api_clip_stats(name):
    """Views, likes and comments of a posted clip on each platform."""
    return ok(stats=post_stats(name, refresh=request.args.get("refresh") == "1"))


@app.post("/api/clips/<name>/unschedule")
def api_unschedule(name):
    plat = (request.get_json(force=True) or {}).get("platform") or ""
    m = read_meta(name) or {}
    v = (m.get("posts") or {}).get(plat)
    if not v or v.get("status") not in ("scheduled", "waiting"):
        return fail("nothing scheduled there")
    with meta_lock:
        m = load_json(meta_path(name), {"name": name})
        m.get("posts", {}).pop(plat, None)
        save_json(meta_path(name), m)
    return ok()


@app.post("/api/vod/start")
def api_vod_start():
    data = request.get_json(force=True) or {}
    url = (data.get("url") or "").strip()
    if not re.match(r"https?://", url):
        return fail("paste the full link of the finished stream or video (https://...)")
    try:
        vod.start(url=url, clips=int(data.get("clips") or 5), template=data.get("template") or "",
                  brief=data.get("brief") or "", genre=data.get("genre") or "auto", layout=data.get("layout") or "auto",
                  length=data.get("length") or "", speech=data.get("speech") or "")
    except (RuntimeError, ValueError) as e:
        return fail(e)
    return ok()


@app.post("/api/vod/upload")
def api_vod_upload():
    f = request.files.get("file")
    if not f or not f.filename:
        return fail("choose a video file")
    if vod.busy():
        return fail("already clipping a video - wait or press Stop")
    ext = os.path.splitext(f.filename)[1].lower() or ".mp4"
    if ext not in (".mp4", ".mov", ".mkv", ".webm", ".ts", ".flv", ".m4v"):
        return fail("unsupported file type")
    path = os.path.join(DIRS["vod"], f"upload_{time.strftime('%Y%m%d_%H%M%S')}{ext}")
    f.save(path)
    try:
        vod.start(file_path=path, clips=int(request.form.get("clips") or 5),
                  template=request.form.get("template") or "", name_hint=f.filename,
                  brief=request.form.get("brief") or "", genre=request.form.get("genre") or "auto",
                  layout=request.form.get("layout") or "auto", length=request.form.get("length") or "",
                  speech=request.form.get("speech") or "")
    except (RuntimeError, ValueError) as e:
        return fail(e)
    return ok()


@app.post("/api/vod/stop")
def api_vod_stop():
    vod.stop()
    return ok()


@app.post("/api/ai/test")
def api_ai_test():
    """Run the smart editor on a tiny sample so you can see the configured AI actually answers."""
    if not is_local():
        return fail("laptop only")
    sample = [(0.5, 0.9, "Bro"), (0.9, 1.3, "watch"), (1.3, 1.6, "this"), (4.0, 4.4, "NO"), (4.4, 4.9, "WAY!")]
    t = time.time()
    notes = []
    meta = E.make_meta(settings, sample, [(4.5, "LMAO"), (5.0, "no way")], "test", 8.0, notes.append)
    info = {}
    if settings.get("ai_provider") == "ollama":
        try:
            info["installed_models"] = E.ollama_models(settings.get("ollama_url") or "http://127.0.0.1:11434")
        except RuntimeError as e:
            info["ollama"] = str(e)
    return ok(seconds=round(time.time() - t, 1), ai=bool(meta.get("ai")), hook=meta.get("hook"),
              score=meta.get("score"), note=" ".join(notes) or meta.get("model_note", ""), **info)


def check_tools():
    missing = [t for t in ("ffmpeg", "ffprobe") if not shutil.which(t)]
    if missing:
        log("MISSING: " + ", ".join(missing) + (" -> brew install ffmpeg (then reopen)" if IS_MAC else " -> winget install -e --id Gyan.FFmpeg (then reopen)"))
    if not shutil.which("streamlink"):
        try:
            import streamlink  # noqa: F401
        except ImportError:
            log("MISSING: streamlink -> python -m pip install streamlink")
    try:
        import yt_dlp  # noqa: F401
    except ImportError:
        if not shutil.which("yt-dlp"):
            log("MISSING: yt-dlp (clipping finished streams) -> python -m pip install yt-dlp")
    if not shutil.which("whisper"):
        try:
            import whisper  # noqa: F401
        except ImportError:
            if not STT.installed():
                log("captions: no Whisper on this PC yet. Captions come from YouTube or Groq (Settings, AI); "
                    "with an NVIDIA RTX card you can install AI captions in Settings, Files")


def already_running():
    """Another Chipper already on this port? (Windows lets two programs share a port, which made two copies
    record the same stream.) Wait a little first: after a restart the old copy needs a moment to close."""
    import urllib.request
    for _ in range(8):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/healthz", timeout=2) as r:
                if r.status != 200:
                    return False
        except Exception:  # noqa: BLE001
            return False
        time.sleep(1.5)
    return True


if __name__ == "__main__":
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleTitleW("Chipper Studio - close this window to stop Chipper")
        except Exception:  # noqa: BLE001
            pass
    if already_running():
        print(f"Chipper is already running on http://127.0.0.1:{PORT} - opening it instead of starting a second copy.")
        webbrowser.open(f"http://127.0.0.1:{PORT}")
        sys.exit(0)
    for _f in os.listdir(DIRS["enh"]):           # caches from the first AI-enhance version - no longer used
        try:
            os.remove(os.path.join(DIRS["enh"], _f))
        except OSError:
            pass
    check_tools()
    sync_ytauth()
    threading.Thread(target=watcher.loop, daemon=True).start()
    threading.Thread(target=tts_setup, daemon=True).start()
    threading.Thread(target=UPD.loop, daemon=True).start()
    log(f"Chipper Studio on http://127.0.0.1:{PORT}  (clips -> {out_dir()})")
    threading.Thread(target=tunnel.watchdog, daemon=True).start()
    if settings.get("remote_auto") and settings.get("remote_password"):
        try:
            tunnel.start()
        except RuntimeError as e:
            log(f"remote access not started: {e}")
    elif settings.get("remote_auto"):
        log("remote access is off until you set a remote password (Settings -> Remote access)")
    import atexit
    atexit.register(tunnel.stop)
    if "--no-browser" not in sys.argv:
        threading.Timer(1.2, lambda: webbrowser.open(f"http://127.0.0.1:{PORT}")).start()
    app.run(host="127.0.0.1", port=PORT, threaded=True, debug=False)
