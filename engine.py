"""Chipper Studio engine: recording, chat, layouts, captions, hooks."""
import json
import math
import os
import random
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import Counter, deque

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")
NO_WINDOW = 0x08000000 if os.name == "nt" else 0   # hide console windows for ffmpeg etc.


def run(cmd, **kw):
    kw.setdefault("creationflags", NO_WINDOW)
    return subprocess.run(cmd, **kw)


PROGRESS = threading.local()     # .cb(fraction) set by app.py while a clip renders on this thread


def run_progress(cmd, total, cwd=None):
    """Run ffmpeg and report how far it has got (0..1) to PROGRESS.cb, from its own progress output."""
    cb = getattr(PROGRESS, "cb", None)
    if not cb or total <= 0:
        return run(cmd, cwd=cwd, capture_output=True, text=True)
    cmd = cmd[:1] + ["-progress", "pipe:1", "-nostats"] + cmd[1:]
    p = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                         encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
    err = []
    t = threading.Thread(target=lambda: err.extend(p.stderr), daemon=True)
    t.start()
    last = 0.0
    for line in p.stdout:
        if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
            try:
                f = min(0.99, int(line.split("=", 1)[1]) / 1e6 / total)
            except ValueError:
                continue
            if f - last >= 0.02:
                last = f
                try:
                    cb(f)
                except Exception:  # noqa: BLE001
                    pass
    p.wait()
    t.join(timeout=2)
    return subprocess.CompletedProcess(cmd, p.returncode, "", "".join(err))


def even(x):
    return max(2, int(round(x)) // 2 * 2)


# ------------------------------------------------------------------ sources
def parse_source(src):
    """-> (kind, id, stream_url, short_name)"""
    src = src.strip()
    m = re.search(r"(?:youtube\.com/(?:live/|watch\?v=|shorts/)|youtu\.be/)([\w-]{11})", src)
    if m:
        vid = m.group(1)
        return "youtube", vid, f"https://www.youtube.com/watch?v={vid}", f"yt_{vid}"
    m = re.search(r"tiktok\.com/@([\w.]{2,30})", src) or re.fullmatch(r"(?i)(?:tiktok|tt):\s*@?([\w.]{2,30})", src)
    if m:
        user = m.group(1).lower()
        return "tiktok", user, f"https://www.tiktok.com/@{user}/live", "tt_" + re.sub(r"\W", "_", user)
    m = re.search(r"twitch\.tv/([\w]+)", src)
    ch = (m.group(1) if m else src).lower().lstrip("#")
    if not re.fullmatch(r"\w{2,30}", ch):
        raise ValueError("paste a YouTube live link, a Twitch link or channel name, or a TikTok link (tiktok.com/@name)")
    return "twitch", ch, f"https://twitch.tv/{ch}", ch


def streamlink_cmd():
    exe = shutil.which("streamlink")
    return [exe] if exe else [sys.executable, "-m", "streamlink"]


def probe_size(path):
    out = run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
               "stream=width,height", "-of", "csv=p=0", path],
              capture_output=True, text=True).stdout
    line = next((l for l in out.splitlines() if l.strip()), "")       # .ts files list the size more than once
    out = line.strip().strip(",").split(",")
    try:
        return int(out[0]), int(out[1])
    except (ValueError, IndexError):
        return 1920, 1080


def stream_url(url, timeout=60):
    """Direct media URL of a live stream (TikTok via yt-dlp, everything else via streamlink)."""
    if "tiktok.com" in url:
        r = run(ytdlp_cmd() + ["-g", "-f", "best", "--no-warnings", url], capture_output=True, text=True, timeout=timeout)
    else:
        r = run(streamlink_cmd() + ["--stream-url", url, "best"], capture_output=True, text=True, timeout=timeout)
    out = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else ""
    return (out, "") if out.startswith("http") else ("", (r.stdout + r.stderr).strip()[-300:] or "stream not live?")


def capture_frame(out_jpg, rec_path=None, url=None):
    """Grab one frame: from the running recording if there is one, else straight from the live stream."""
    if rec_path and os.path.exists(rec_path) and os.path.getsize(rec_path) > 2_000_000:
        cmd = ["ffmpeg", "-v", "error", "-y", "-sseof", "-3", "-i", rec_path,
               "-frames:v", "1", "-q:v", "2", out_jpg]
        if run(cmd, timeout=60).returncode == 0:
            return True, ""
    if not url:
        return False, "no stream link"
    surl, err = stream_url(url)
    if not surl:
        return False, err
    r = run(["ffmpeg", "-v", "error", "-y", "-i", surl, "-frames:v", "1", "-q:v", "2", out_jpg],
            capture_output=True, text=True, timeout=90)
    return r.returncode == 0, r.stderr[-300:]


# ------------------------------------------------------------------ recording
REC_QUALITY = {"best": "best", "1080": "1080p60,1080p,best", "720": "720p60,720p,best", "480": "480p,best"}


class Recorder:
    def __init__(self, url, name, rec_dir, quality="best"):
        self.path = os.path.join(rec_dir, f"{name}_{time.strftime('%Y%m%d_%H%M%S')}.ts")
        self.url = url
        self.quality = REC_QUALITY.get(quality or "best", "best")
        self.start_time = None
        self.proc = None

    def start(self, should_continue=lambda: True):
        self.logf = open(self.path + ".log", "w")
        if "tiktok.com" in self.url:
            # TikTok: yt-dlp finds the live stream's address, ffmpeg records it as a growing .ts like streamlink does
            surl, err = stream_url(self.url)
            if not surl:
                self.logf.write(err + "\n")
                self.logf.flush()
                return False
            cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-reconnect", "1", "-reconnect_streamed", "1",
                   "-reconnect_delay_max", "10", "-i", surl, "-map", "0:v:0?", "-map", "0:a:0?", "-c", "copy",
                   "-f", "mpegts", self.path]
        else:
            cmd = streamlink_cmd() + ["-o", self.path, self.url, self.quality]
            if "twitch.tv" in self.url:
                cmd.insert(-2, "--twitch-disable-ads")
        self.proc = subprocess.Popen(cmd, stdout=self.logf, stderr=subprocess.STDOUT,
                                     creationflags=NO_WINDOW)
        for _ in range(90):
            if os.path.exists(self.path) and os.path.getsize(self.path) > 0:
                self.start_time = time.time()
                return True
            if self.proc.poll() is not None or not should_continue():
                return False
            time.sleep(1)
        return False

    def error_tail(self):
        try:
            return "\n".join(open(self.path + ".log").read().strip().splitlines()[-3:])
        except OSError:
            return ""

    def alive(self):
        return self.proc is not None and self.proc.poll() is None

    def stop(self):
        if self.alive():
            self.proc.terminate()
            try:
                self.proc.wait(10)
            except subprocess.TimeoutExpired:
                self.proc.kill()


# ------------------------------------------------------------------ chat
class ChatBase(threading.Thread):
    def __init__(self, log):
        super().__init__(daemon=True)
        self.log = log
        self.count = 0
        self.messages = deque(maxlen=600)     # (time, text) for hooks/hashtags
        self.lock = threading.Lock()
        self.running = True
        self.connected = False

    def add(self, texts):
        now = time.time()
        with self.lock:
            self.count += len(texts)
            for t in texts:
                self.messages.append((now, t))

    def take(self):
        with self.lock:
            n, self.count = self.count, 0
        return n

    def recent(self, t0, t1):
        with self.lock:
            return [m for t, m in self.messages if t0 <= t <= t1]

    def recent_timed(self, t0, t1):
        """[(seconds after t0, text)] - lets the AI line chat up with the speech"""
        with self.lock:
            return [(round(t - t0, 1), m) for t, m in self.messages if t0 <= t <= t1]

    def run(self):
        while self.running:
            try:
                self.session()
            except Exception as e:  # noqa: BLE001
                self.connected = False
                if self.running:
                    self.log(f"chat dropped ({type(e).__name__}: {e}), retrying in 5 s")
                    time.sleep(5)


class TikTokChat(ChatBase):
    """TikTok LIVE comments + gifts + likes through the free TikTokLive library (pip install TikTokLive).
    Gifts count extra and likes add a little, because on TikTok hype shows as gifts and likes as much as comments."""

    def __init__(self, user, log):
        super().__init__(log)
        self.user = user
        self.likes = 0

    def bump(self, n):
        with self.lock:
            self.count += n

    def run(self):
        try:
            from TikTokLive import TikTokLiveClient
            from TikTokLive.events import CommentEvent, GiftEvent, LikeEvent, ConnectEvent
        except ImportError:
            self.log("TikTok chat needs the TikTokLive library: run  pip install TikTokLive  (clips still work "
                     "with the Clip now button)")
            return
        import asyncio
        while self.running:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            client = TikTokLiveClient(unique_id="@" + self.user)

            @client.on(ConnectEvent)
            async def _c(_e):
                self.connected = True
                self.log(f"chat connected: tiktok @{self.user}")

            @client.on(CommentEvent)
            async def _m(e):
                t = str(getattr(e, "comment", "") or "").strip()
                if t:
                    self.add([t])

            @client.on(GiftEvent)
            async def _g(e):
                if getattr(e, "streaking", False):
                    return                                   # count a combo once, when it ends
                n = int(getattr(e, "repeat_count", 1) or 1)
                gift = getattr(getattr(e, "gift", None), "name", "") or "gift"
                self.add([f"[sent {gift} x{n}]"])
                self.bump(min(10, 2 + n // 5))

            @client.on(LikeEvent)
            async def _l(e):
                self.likes += int(getattr(e, "count", 0) or 0)
                if self.likes >= 40:
                    self.bump(self.likes // 40)
                    self.likes %= 40

            try:
                loop.run_until_complete(client.connect(fetch_live_check=True))
            except Exception as ex:  # noqa: BLE001
                self.log(f"tiktok chat: {str(ex)[:120]}")
            finally:
                self.connected = False
                try:
                    loop.run_until_complete(client.disconnect())
                except Exception:  # noqa: BLE001
                    pass
                loop.close()
            if self.running:
                time.sleep(15)


class TwitchChat(ChatBase):
    def __init__(self, channel, log):
        super().__init__(log)
        self.channel = channel

    def session(self):
        s = socket.create_connection(("irc.chat.twitch.tv", 6667), timeout=330)
        s.sendall(b"NICK justinfan12345\r\n")
        s.sendall(f"JOIN #{self.channel}\r\n".encode())
        self.log(f"chat connected: twitch #{self.channel}")
        self.connected = True
        buf = b""
        while self.running:
            data = s.recv(4096)
            if not data:
                raise OSError("server closed connection")
            buf += data
            *lines, buf = buf.split(b"\r\n")
            texts = []
            for line in lines:
                if line.startswith(b"PING"):
                    s.sendall(b"PONG" + line[4:] + b"\r\n")
                elif b" PRIVMSG " in line:
                    texts.append(line.split(b" :", 1)[-1].decode("utf-8", "replace"))
            if texts:
                self.add(texts)
        s.close()


class YouTubeChat(ChatBase):
    def __init__(self, vid, log):
        super().__init__(log)
        self.vid = vid

    def _get(self, url, data=None):
        headers = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}
        if data is not None:
            data = json.dumps(data).encode()
            headers["Content-Type"] = "application/json"
        with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=headers),
                                    timeout=20) as r:
            return r.read().decode("utf-8", "replace")

    def session(self):
        html = self._get(f"https://www.youtube.com/live_chat?is_popout=1&v={self.vid}")
        key = re.search(r'"INNERTUBE_API_KEY":"([^"]+)"', html)
        ver = (re.search(r'"INNERTUBE_CONTEXT_CLIENT_VERSION":"([^"]+)"', html)
               or re.search(r'"clientVersion":"([^"]+)"', html))
        conts = re.findall(r'"continuation":"([^"]+)"', html)
        if not (key and ver and conts):
            raise RuntimeError("could not open live chat (chat off or stream ended?)")
        cont = conts[0]
        m = re.search(r'"title":"Live chat"[^{}]*?"continuation":\{"reloadContinuationData":'
                      r'\{"continuation":"([^"]+)"', html)
        if m:
            cont = m.group(1)
        api = ("https://www.youtube.com/youtubei/v1/live_chat/get_live_chat"
               f"?key={key.group(1)}&prettyPrint=false")
        ctx = {"client": {"clientName": "WEB", "clientVersion": ver.group(1), "hl": "en"}}
        first = True
        while self.running:
            j = json.loads(self._get(api, {"context": ctx, "continuation": cont}))
            lcc = j.get("continuationContents", {}).get("liveChatContinuation")
            if not lcc:
                raise RuntimeError("chat ended or unavailable")
            texts = []
            for a in lcc.get("actions", []):
                item = a.get("addChatItemAction", {}).get("item", {})
                if not item:
                    continue
                r = item.get("liveChatTextMessageRenderer") or item.get("liveChatPaidMessageRenderer") or {}
                runs = r.get("message", {}).get("runs", [])
                texts.append("".join(x.get("text") or x.get("emoji", {}).get("shortcuts", [""])[0]
                                     for x in runs))
            if first:
                self.log(f"chat connected: youtube {self.vid}")
                self.connected = True
                first = False       # first batch is backlog
            elif texts:
                self.add(texts)
            nxt, wait = None, 5.0
            for c in lcc.get("continuations", []):
                for k in ("invalidationContinuationData", "timedContinuationData",
                          "reloadContinuationData"):
                    if k in c:
                        nxt = c[k]["continuation"]
                        wait = c[k].get("timeoutMs", 5000) / 1000
            if not nxt:
                raise RuntimeError("no continuation (stream ended?)")
            cont = nxt
            time.sleep(min(max(wait, 1.0), 10.0))


# ------------------------------------------------------------------ layout
DEFAULT_TEMPLATE = {
    "layout": "full",           # "full" = gameplay only, "split" = cam + gameplay
    "cam_pos": "top",           # cam on "top" or "bottom" in split layout
    "split": 0.35,              # cam height as a fraction of 1920
    "cam": [0.10, 0.06, 0.22, 0.25],    # x, y, w, h as fractions of the source frame
    "game": [0.34, 0.0, 0.32, 1.0],
    "caption_style": "tiktok",
    "caption_y": 0.72,          # caption centre as a fraction of 1920
    "hook": True,               # hook title for the first seconds
    "hook_secs": 3.5,           # how long the hook stays on screen
    "progress_bar": False,      # thin bar along the bottom that fills as the clip plays
    "watermark": "",            # e.g. "@yourname", shown the whole clip
    "watermark_pos": "bottom",  # top | bottom
}


def merged_template(t):
    out = dict(DEFAULT_TEMPLATE)
    out.update({k: v for k, v in (t or {}).items() if k in DEFAULT_TEMPLATE})
    return out


def _crop(rect, w, h):
    x, y, cw, ch = rect
    cw, ch = even(max(16, min(cw, 1) * w)), even(max(16, min(ch, 1) * h))
    x = int(min(max(0, x * w), w - cw)) // 2 * 2
    y = int(min(max(0, y * h), h - ch)) // 2 * 2
    return f"crop={cw}:{ch}:{x}:{y}"


def _key_expr(pts, mode, var="t"):
    """Piecewise expression through (time, value) points: smooth ease or hard cut between keyframes."""
    e = f"{pts[-1][1]:.2f}"
    for (t0, v0), (t1, v1) in reversed(list(zip(pts, pts[1:]))):
        if mode == "cut" or abs(v1 - v0) < 0.01:
            seg = f"{v0:.2f}"
        else:
            u = f"(({var}-{t0})/{max(0.01, t1 - t0):.3f})"
            seg = f"{v0:.2f}+{v1 - v0:.2f}*{u}*{u}*(3-2*{u})"
        e = f"if(lt({var},{t1}),{seg},{e})"
    return f"if(lt({var},{pts[0][0]}),{pts[0][1]:.2f},{e})"


def crop_zooms(layer):
    keys = layer.get("keys") or []
    ws = [k[3] for k in keys if len(k) == 5]
    return len(ws) >= 2 and max(ws) - min(ws) > 0.005


def _crop_zoom(layer, w, h, start, ow, oh):
    """Keyframes that change the crop size too: crop a fixed box big enough for the widest keyframe, following the
    action, then zoompan inside it. Output is ow x oh at 30 fps."""
    keys = [k if len(k) == 5 else [*k, layer["crop"][2], layer["crop"][3]] for k in layer["keys"]]
    ar = (keys[0][3] * w) / max(1, keys[0][4] * h)                       # every keyframe keeps the window's shape
    mw = even(min(w, max(k[3] for k in keys) * w)); mh = even(min(h, mw / ar)); mw = even(min(mw, mh * ar))
    T = [round(k[0] - start, 3) for k in keys]
    cw = [min(k[3] * w, mw) for k in keys]
    cx = [k[1] * w + c / 2 for k, c in zip(keys, cw)]
    cy = [k[2] * h + (c / ar) / 2 for k, c in zip(keys, cw)]
    mode = layer.get("kmode")
    ex = lambda vals, var: _key_expr(list(zip(T, vals)), mode, var)
    bx = f"min(max(0,({ex(cx, 't')})-{mw / 2:.1f}),{w - mw})"
    by = f"min(max(0,({ex(cy, 't')})-{mh / 2:.1f}),{h - mh})"
    z = f"{mw:.1f}/({ex(cw, 'it')})"
    bxi = f"min(max(0,({ex(cx, 'it')})-{mw / 2:.1f}),{w - mw})"
    byi = f"min(max(0,({ex(cy, 'it')})-{mh / 2:.1f}),{h - mh})"
    zx = f"max(0,({ex(cx, 'it')})-({ex(cw, 'it')})/2-{bxi})"
    zy = f"max(0,({ex(cy, 'it')})-({ex(cw, 'it')})/{ar:.4f}/2-{byi})"
    return (f"crop={mw}:{mh}:x='{bx}':y='{by}',scale={even(ow * 2)}:{even(oh * 2)}:flags=bicubic,"
            f"zoompan=z='{z}':x='({zx})*{even(ow * 2) / mw:.5f}':y='({zy})*{even(oh * 2) / mh:.5f}':d=1:s={even(ow)}x{even(oh)}:fps=30")


def _crop_keys(layer, w, h, start):
    """crop filter whose position follows the layer's keyframes (raw-video times) - smooth slide or hard cut."""
    cw, ch = even(max(16, min(layer["crop"][2], 1) * w)), even(max(16, min(layer["crop"][3], 1) * h))
    keys = layer.get("keys") or []
    if not keys:
        return _crop(layer["crop"], w, h)
    pts = [(round(k[0] - start, 3), min(max(0, k[1] * w), w - cw), min(max(0, k[2] * h), h - ch)) for k in keys]

    def expr(i):
        val = 1 if i == 1 else 2
        last = pts[-1][val]
        e = f"{last:.1f}"
        for (t0, *a), (t1, *b) in reversed(list(zip(pts, pts[1:]))):
            v0, v1 = a[i - 1], b[i - 1]
            if layer.get("kmode") == "cut" or abs(v1 - v0) < 0.5:
                seg = f"{v0:.1f}"
            else:
                d = max(0.01, t1 - t0)
                u = f"((t-{t0})/{d:.3f})"
                seg = f"{v0:.1f}+{v1 - v0:.1f}*{u}*{u}*(3-2*{u})"
            e = f"if(lt(t,{t1}),{seg},{e})"
        return f"if(lt(t,{pts[0][0]}),{pts[0][val]:.1f},{e})"

    return f"crop={cw}:{ch}:x='{expr(1)}':y='{expr(2)}'"


def layout_filter(w, h, t, extra=""):
    """filter_complex producing [v] at 1080x1920. extra = filters appended (captions)."""
    t = merged_template(t)
    fit = lambda W, H: (f"scale={W}:{H}:force_original_aspect_ratio=increase:flags=lanczos,"
                        f"crop={W}:{H}")
    tail = (("," + extra) if extra else "") + ",setsar=1[v]"
    if t["layout"] != "split":
        return f"[0:v]{_crop(t['game'], w, h)},{fit(1080, 1920)}{tail}"
    cam_h = even(1920 * min(max(t["split"], 0.15), 0.7))
    game_h = 1920 - cam_h
    order = "[c][g]" if t["cam_pos"] == "top" else "[g][c]"
    return (f"[0:v]split=2[a][b];"
            f"[a]{_crop(t['cam'], w, h)},{fit(1080, cam_h)}[c];"
            f"[b]{_crop(t['game'], w, h)},{fit(1080, game_h)}[g];"
            f"{order}vstack{tail}")


# ------------------------------------------------------------------ captions
# ASS colours are &HAABBGGRR
CAPTION_STYLES = {
    "tiktok":  {"label": "TikTok Yellow", "font": "Arial Black", "size": 92, "color": "&H00FFFFFF",
                "hi": "&H0000FFFF", "outline": 7, "shadow": 4, "words": 3, "upper": True,
                "mode": "highlight", "box": False, "css": {"font": "Arial Black", "color": "#fff", "hi": "#ffff00"}},
    "hormozi": {"label": "Hormozi Green", "font": "Arial Black", "size": 104, "color": "&H00FFFFFF",
                "hi": "&H005AFF3C", "outline": 8, "shadow": 0, "words": 2, "upper": True,
                "mode": "highlight", "box": False, "css": {"font": "Arial Black", "color": "#fff", "hi": "#3cff5a"}},
    "beast":   {"label": "Beast Red", "font": "Impact", "size": 118, "color": "&H00FFFFFF",
                "hi": "&H002D2DFF", "outline": 9, "shadow": 5, "words": 2, "upper": True,
                "mode": "highlight", "box": False, "css": {"font": "Impact", "color": "#fff", "hi": "#ff2d2d"}},
    "karaoke": {"label": "Karaoke Fill", "font": "Arial Black", "size": 84, "color": "&H0000FFFF",
                "hi": "&H00FFFFFF", "outline": 6, "shadow": 3, "words": 4, "upper": True,
                "mode": "karaoke", "box": False, "css": {"font": "Arial Black", "color": "#fff", "hi": "#ffff00"}},
    "boxed":   {"label": "Black Box", "font": "Arial", "size": 72, "color": "&H00FFFFFF",
                "hi": "&H0000E5FF", "outline": 14, "shadow": 0, "words": 4, "upper": False,
                "mode": "highlight", "box": True, "css": {"font": "Arial", "color": "#fff", "hi": "#ffe500", "box": True}},
    "minimal": {"label": "Clean White", "font": "Segoe UI Semibold", "size": 66, "color": "&H00FFFFFF",
                "hi": "&H00FFFFFF", "outline": 3, "shadow": 2, "words": 6, "upper": False,
                "mode": "plain", "box": False, "css": {"font": "Segoe UI", "color": "#fff", "hi": "#fff"}},
    "gta":     {"label": "GTA (Pricedown)", "font": "Pricedown Black", "size": 112, "color": "&H00FFFFFF",
                "hi": "&H0040D76B", "outline": 8, "shadow": 4, "words": 3, "upper": False,
                "mode": "highlight", "box": False, "css": {"font": "Pricedown Black", "color": "#fff", "hi": "#6bd740"}},
    "mission": {"label": "Mission Passed", "font": "Pricedown Black", "size": 120, "color": "&H0000C3FF",
                "hi": "&H00FFFFFF", "outline": 9, "shadow": 0, "words": 2, "upper": True,
                "mode": "highlight", "box": False, "css": {"font": "Pricedown Black", "color": "#ffc300", "hi": "#fff"}},
    "comic":   {"label": "Comic Pop", "font": "Comic Sans MS", "size": 92, "color": "&H00FFFFFF",
                "hi": "&H00FF61FF", "outline": 8, "shadow": 5, "words": 3, "upper": True,
                "mode": "highlight", "box": False, "css": {"font": "Comic Sans MS", "color": "#fff", "hi": "#ff61ff"}},
    "oneword": {"label": "One Word Punch", "font": "Arial Black", "size": 138, "color": "&H00FFFFFF",
                "hi": "&H0000FFFF", "outline": 10, "shadow": 6, "words": 1, "upper": True,
                "mode": "highlight", "anim": "onebyone", "box": False, "css": {"font": "Arial Black", "color": "#fff", "hi": "#ffff00"}},
    "cinematic": {"label": "Cinematic", "font": "Georgia", "size": 64, "color": "&H00F0F0F0",
                "hi": "&H00F0F0F0", "outline": 2, "shadow": 3, "words": 6, "upper": False, "spacing": 3,
                "mode": "plain", "box": False, "css": {"font": "Georgia", "color": "#f0f0f0", "hi": "#f0f0f0"}},
    "outlined": {"label": "Hollow Outline", "font": "Arial Black", "size": 104, "color": "&H00FFFFFF",
                "hi": "&H00FFFFFF", "outline": 5, "shadow": 0, "words": 3, "upper": True, "hollow": True,
                "mode": "highlight", "box": False, "css": {"font": "Arial Black", "color": "#fff", "hi": "#fff", "hollow": True}},
    "neon":    {"label": "Neon Glow", "font": "Arial Black", "size": 96, "color": "&H00FFFFFF",
                "hi": "&H00F05AFF", "outline": 6, "shadow": 0, "words": 3, "upper": True, "glow": "&H00FFE52E",
                "mode": "highlight", "box": False, "css": {"font": "Arial Black", "color": "#fff", "hi": "#ff5af0", "glow": "#2ee5ff"}},
    "pill":    {"label": "Pill Box", "font": "Arial Black", "size": 76, "color": "&H00FFFFFF",
                "hi": "&H0000D5FF", "outline": 16, "shadow": 0, "words": 3, "upper": True,
                "mode": "highlight", "box": True, "boxcol": "&H00E0337A", "css": {"font": "Arial Black", "color": "#fff", "hi": "#ffd500", "box": "#7a33e0"}},
    "subtitle": {"label": "Netflix Subtitle", "font": "Arial", "size": 60, "color": "&H00FFFFFF",
                "hi": "&H00FFFFFF", "outline": 3, "shadow": 1, "words": 7, "upper": False,
                "mode": "plain", "anim": "full", "box": False, "css": {"font": "Arial", "color": "#fff", "hi": "#fff"}},
    "fire":    {"label": "Fire Pop", "font": "Impact", "size": 112, "color": "&H0000D7FF",
                "hi": "&H000045FF", "outline": 8, "shadow": 4, "words": 2, "upper": True,
                "mode": "highlight", "anim": "pop", "box": False, "css": {"font": "Impact", "color": "#ffd700", "hi": "#ff4500"}},
    "bold":    {"label": "Segoe Bold Blue", "font": "Segoe UI Black", "size": 96, "color": "&H00FFFFFF",
                "hi": "&H00FFB02E", "outline": 7, "shadow": 3, "words": 3, "upper": True,
                "mode": "highlight", "box": False, "css": {"font": "Segoe UI Black", "color": "#fff", "hi": "#2eb0ff"}},
}

# Extra fonts (e.g. Pricedown for a GTA look) live in the "fonts" folder next to app.py.
FONTS_DIR = ""
SYSTEM_FONTS = ["Arial Black", "Impact", "Arial", "Segoe UI Black", "Segoe UI Semibold", "Verdana", "Tahoma",
                "Trebuchet MS", "Georgia", "Comic Sans MS", "Courier New"]


def font_family(path):
    """Read a .ttf/.otf font's family name (name table) without extra libraries."""
    import struct
    try:
        with open(path, "rb") as f:
            data = f.read()
        num = struct.unpack(">H", data[4:6])[0]
        for i in range(num):
            tag, _, off, _ = struct.unpack(">4sIII", data[12 + 16 * i:28 + 16 * i])
            if tag != b"name":
                continue
            _, count, so = struct.unpack(">HHH", data[off:off + 6])
            found = {}
            for j in range(count):
                pid, eid, lid, nid, ln, o = struct.unpack(">6H", data[off + 6 + 12 * j:off + 18 + 12 * j])
                if nid not in (1, 4):
                    continue
                raw = data[off + so + o:off + so + o + ln]
                txt = raw.decode("utf-16-be", "ignore") if pid in (0, 3) else raw.decode("latin-1", "ignore")
                if txt.strip():
                    found.setdefault(nid, txt.strip())
            return found.get(1) or found.get(4) or ""
    except (OSError, struct.error):
        pass
    return ""


def custom_fonts():
    out = []
    if FONTS_DIR and os.path.isdir(FONTS_DIR):
        for fn in sorted(os.listdir(FONTS_DIR)):
            if fn.lower().endswith((".ttf", ".otf")):
                fam = font_family(os.path.join(FONTS_DIR, fn))
                if fam:
                    out.append({"file": fn, "family": fam})
    return out


def ass_filter(ass_name, work_dir):
    """ass=... plus fontsdir so libass finds the extra fonts (relative path avoids Windows 'C:' escaping)."""
    f = f"ass={ass_name}"
    if FONTS_DIR and os.path.isdir(FONTS_DIR):
        try:
            rel = os.path.relpath(FONTS_DIR, work_dir).replace("\\", "/")
            f += f":fontsdir='{rel}'"
        except ValueError:          # different drive
            pass
    return f


def group_words(words, size=3, gap=0.6):
    groups, cur = [], []
    for w in words:
        if cur and (len(cur) >= size or w[0] - cur[-1][1] > gap or cur[-1][2][-1] in ".!?"):
            groups.append(cur)
            cur = []
        cur.append(w)
    if cur:
        groups.append(cur)
    return groups


def ts_ass(t):
    cs = int(round(max(t, 0) * 100))
    return f"{cs // 360000}:{cs // 6000 % 60:02}:{cs // 100 % 60:02}.{cs % 100:02}"


def ts_srt(t):
    ms = int(round(max(t, 0) * 1000))
    return f"{ms // 3600000:02}:{ms // 60000 % 60:02}:{ms // 1000 % 60:02},{ms % 1000:03}"


def ass_lines(t):
    """Text for ASS with the user's line breaks (Enter) kept as \\N."""
    parts = [clean_ass(p) for p in str(t or "").replace("\r", "").split("\n")]
    while parts and not parts[-1]:
        parts.pop()
    return "\\N".join(parts).strip()


def clean_ass(t):
    t = re.sub(r"[\U00010000-\U0010FFFF☀-➿️]", "", t)     # emoji don't render in ASS
    return t.replace("\\", "").replace("{", "(").replace("}", ")").strip()


def write_srt(words, path, size=3):
    with open(path, "w", encoding="utf-8") as f:
        for i, g in enumerate(group_words(words, size), 1):
            f.write(f"{i}\n{ts_srt(g[0][0])} --> {ts_srt(g[-1][1])}\n{' '.join(w[2] for w in g)}\n\n")


def hex_to_ass(c, alpha="00"):
    c = (c or "#ffffff").lstrip("#")
    if len(c) == 3:
        c = "".join(x * 2 for x in c)
    try:
        r, g, b = c[0:2], c[2:4], c[4:6]
        int(r + g + b, 16)
    except ValueError:
        r, g, b = "ff", "ff", "ff"
    return f"&H{alpha}{b}{g}{r}".upper()


def write_ass(path, words, template, hook_text=None, hook_secs=None, overlays=None):
    t = merged_template(template)
    if hook_secs is None:
        hook_secs = float(t.get("hook_secs") or 3.5)
    st = dict(CAPTION_STYLES.get(t["caption_style"], CAPTION_STYLES["tiktok"]))
    st["font"] = deva_font(st["font"], *[w[2] for w in (words or [])])
    y = int(1920 * min(max(t["caption_y"], 0.1), 0.95))
    border = 3 if st["box"] else 1
    outline_col = "&H40000000" if st["box"] else "&H00000000"
    if t["layout"] == "split":
        cam_h = even(1920 * min(max(t["split"], 0.15), 0.7))
        hook_y = cam_h + 60 if t["cam_pos"] == "top" else 80
    else:
        hook_y = 230
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{st['font']},{st['size']},{st['color']},{st['hi']},{outline_col},&H96000000,1,0,0,0,100,100,0,0,{border},{st['outline']},{st['shadow']},5,70,70,0,1
Style: Hook,Arial Black,62,&H00000000,&H00000000,&H00FFFFFF,&H00000000,1,0,0,0,100,100,0,0,3,18,0,8,90,90,0,1
Style: Ovl,Arial Black,70,&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,1,0,0,0,100,100,0,0,1,6,3,5,40,40,0,1
Style: OvlBox,Arial Black,70,&H00FFFFFF,&H00FFFFFF,&H50000000,&H00000000,1,0,0,0,100,100,0,0,3,16,0,5,40,40,0,1
Style: Wm,Arial,40,&H70FFFFFF,&H70FFFFFF,&H90000000,&H00000000,1,0,0,0,100,100,0,0,1,2,0,5,40,40,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    end_all = ts_ass(35999)
    wm = clean_ass(t.get("watermark") or "")
    if wm:
        wy = 70 if t.get("watermark_pos") == "top" else 1920 - (70 if t.get("progress_bar") else 50)
        lines.append(f"Dialogue: 2,{ts_ass(0)},{end_all},Wm,,0,0,0,,{{\\an5\\pos(540,{wy})}}{wm}")
    for o in overlays or []:
        txt = clean_ass(str(o.get("text", "")))
        if not txt:
            continue
        s0 = max(0.0, float(o.get("start") or 0))
        e0 = float(o.get("end") or 0) or 35999
        x, yy = int(1080 * float(o.get("x", 0.5))), int(1920 * float(o.get("y", 0.3)))
        size = int(o.get("size") or 70)
        col = hex_to_ass(o.get("color") or "#ffffff")
        style = "OvlBox" if o.get("box") else "Ovl"
        lines.append(f"Dialogue: 3,{ts_ass(s0)},{ts_ass(e0)},{style},,0,0,0,,"
                     f"{{\\an5\\pos({x},{yy})\\fs{size}\\1c{col}\\fad(120,120)}}{txt}")
    if hook_text and t["hook"]:
        h = ass_lines(hook_text)
        if h:
            lines.append(f"Dialogue: 1,{ts_ass(0)},{ts_ass(hook_secs)},Hook,,0,0,0,,"
                         f"{{\\an8\\pos(540,{hook_y})\\fn{deva_font('Arial Black', h)}\\fad(150,250)}}{h}")
    pos = f"\\an5\\pos(540,{y})"
    groups = group_words(words or [], st["words"])
    for gi, g in enumerate(groups):
        nxt = groups[gi + 1][0][0] if gi + 1 < len(groups) else 1e9
        toks = [clean_ass(w[2].upper() if st["upper"] else w[2]) for w in g]
        if st["mode"] == "plain":
            lines.append(f"Dialogue: 0,{ts_ass(g[0][0])},{ts_ass(min(g[-1][1] + 0.15, nxt))},Cap,,0,0,0,,"
                         f"{{{pos}\\fad(80,80)}}" + " ".join(toks))
        elif st["mode"] == "karaoke":
            # \kf fills from SecondaryColour to PrimaryColour; here Primary=yellow (sung), Secondary=white
            parts, prev = [], g[0][0]
            for (ws, we, _), tok in zip(g, toks):
                parts.append(f"{{\\kf{max(1, int(round((we - prev) * 100)))}}}{tok} ")
                prev = we
            lines.append(f"Dialogue: 0,{ts_ass(g[0][0])},{ts_ass(min(g[-1][1] + 0.3, nxt))},Cap,,0,0,0,,"
                         f"{{{pos}\\1c{st['color']}\\2c{st['hi']}}}" + "".join(parts).strip())
        else:
            for i, (ws, we, _) in enumerate(g):
                start = ws if i else g[0][0]
                end = g[i + 1][0] if i + 1 < len(g) else max(we, ws + 0.25)
                if end <= start:
                    continue
                parts = []
                for j, tok in enumerate(toks):
                    if j == i:
                        parts.append(f"{{\\c{st['hi']}{st.get('_fil', '')}\\fscx122\\fscy122\\t(0,120,\\fscx108\\fscy108)}}"
                                     f"{tok}{{\\c{st['color']}{st.get('_hol', '')}\\fscx100\\fscy100}}")
                    else:
                        parts.append(tok)
                pop = "\\fscx80\\fscy80\\t(0,90,\\fscx100\\fscy100)" if i == 0 else ""
                lines.append(f"Dialogue: 0,{ts_ass(start)},{ts_ass(end)},Cap,,0,0,0,,"
                             f"{{{pos}{pop}}}" + " ".join(parts))
    with open(path, "w", encoding="utf-8") as f:
        f.write(head + "\n".join(lines) + "\n")


# ------------------------------------------------------------------ whisper
LAST_WHISPER_ERROR = [""]


def whisper_cmd():
    exe = shutil.which("whisper")
    return [exe] if exe else [sys.executable, "-m", "whisper"]


def transcribe(clip, work_dir, model="base", lang=None):
    """-> list of (start, end, word), or None (error text in LAST_WHISPER_ERROR)"""
    cmd = whisper_cmd() + [
        clip, "--model", model, "--output_format", "json", "--output_dir", work_dir,
        "--word_timestamps", "True", "--verbose", "False", "--fp16", "False"]
    if lang:
        cmd += ["--language", lang]
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
    try:
        r = run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env, timeout=1800)
    except (OSError, subprocess.TimeoutExpired) as e:
        LAST_WHISPER_ERROR[0] = f"could not run whisper: {e}"
        return None
    jpath = os.path.join(work_dir, os.path.splitext(os.path.basename(clip))[0] + ".json")
    if r.returncode != 0 or not os.path.exists(jpath):
        lines = [l for l in (r.stderr or r.stdout or "").strip().splitlines() if l.strip()]
        LAST_WHISPER_ERROR[0] = f"whisper exit {r.returncode}: " + " | ".join(lines[-4:])[-600:]
        return None
    with open(jpath, encoding="utf-8") as f:
        result = json.load(f)
    os.remove(jpath)
    LAST_WHISPER_ERROR[0] = ""
    return [(float(w["start"]), float(w["end"]), w["word"].strip())
            for s in result.get("segments", []) for w in s.get("words", []) if w.get("word", "").strip()]


def transcribe_groq(clip, work_dir, key, lang=None, model="whisper-large-v3"):
    """Speech-to-text on Groq's free Whisper API. -> words list or None"""
    mp3 = os.path.join(work_dir, os.path.splitext(os.path.basename(clip))[0] + ".mp3")
    r = run(["ffmpeg", "-v", "error", "-y", "-i", clip, "-vn", "-ac", "1", "-ar", "16000", "-b:a", "48k", mp3])
    if r.returncode != 0 or not os.path.exists(mp3):
        LAST_WHISPER_ERROR[0] = "could not extract audio for Groq"
        return None
    boundary = "----chipper" + str(random.randint(10**8, 10**9))
    fields = [("model", model), ("response_format", "verbose_json"), ("timestamp_granularities[]", "word"),
              ("temperature", "0")]
    if lang:
        fields.append(("language", lang))
    body = b""
    for k, v in fields:
        body += f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
    with open(mp3, "rb") as f:
        audio = f.read()
    os.remove(mp3)
    body += (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="clip.mp3"\r\n'
             f"Content-Type: audio/mpeg\r\n\r\n").encode() + audio + f"\r\n--{boundary}--\r\n".encode()
    url = os.environ.get("CHIPPER_GROQ_STT", "https://api.groq.com/openai/v1/audio/transcriptions")
    req = urllib.request.Request(url, data=body, headers={
        "Authorization": "Bearer " + key, "Content-Type": f"multipart/form-data; boundary={boundary}",
        "User-Agent": "ChipperStudio/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            j = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        LAST_WHISPER_ERROR[0] = f"Groq speech-to-text {e.code}: {e.read().decode('utf-8', 'replace')[:300]}"
        return None
    except (urllib.error.URLError, OSError, ValueError) as e:
        LAST_WHISPER_ERROR[0] = f"Groq speech-to-text failed: {e}"
        return None
    LAST_LANG[0] = str(j.get("language") or lang or "")
    words = j.get("words") or []
    if not words:      # some models only give segments: spread segment words evenly
        for seg in j.get("segments", []):
            toks = seg.get("text", "").split()
            if not toks:
                continue
            step = (float(seg["end"]) - float(seg["start"])) / len(toks)
            for i, t in enumerate(toks):
                words.append({"word": t, "start": float(seg["start"]) + i * step,
                              "end": float(seg["start"]) + (i + 1) * step})
    LAST_WHISPER_ERROR[0] = ""
    return [(float(w["start"]), float(w["end"]), str(w["word"]).strip()) for w in words if str(w.get("word", "")).strip()]


SPEECH = {   # what the streamer speaks -> (Whisper language, caption script)
    "auto": (None, ""), "en": ("en", ""), "hi": ("hi", ""), "hi-rom": ("hi", "hi-rom"),
    "ne": ("ne", ""), "ne-rom": ("ne", "ne-rom"),
}
GPU_STT = None          # set by Chipper: f(audio_path, lang, prompt) -> (words, detected) or (None, error)
LAST_LANG = [""]
LAST_STT = [""]          # which engine/model made the last captions, e.g. "graphics card (turbo)"


def speech_code(settings, override=None):
    code = (override or settings.get("speech") or "").strip()
    if code not in SPEECH:          # old "Speech language" box: only Hindi/Nepali are kept - forced English was the
        old = (settings.get("lang") or "").strip().lower()      # main reason Hindi/Nepali captions came out wrong
        code = old if old in ("hi", "ne") else "auto"
    return code


def _word_key(w):
    return re.sub(r"[^\w']+", "", str(w).lower())


def apply_word_fixes(words, fixes):
    """'Always fix' list: kai sent -> Kai Cenat, pulis -> police. Keeps timing and the word's punctuation.
    A fix may be several words ("kai sent"), matched across neighbouring caption words."""
    if not words or not fixes:
        return words
    rules = sorted(((k.split(), v) for k, v in fixes.items() if k.strip() and str(v).strip()), key=lambda r: -len(r[0]))
    out, i = [], 0
    while i < len(words):
        hit = None
        for keys, val in rules:
            n = len(keys)
            if i + n <= len(words) and [_word_key(w[2]) for w in words[i:i + n]] == keys:
                hit = (n, val)
                break
        if hit:
            n, val = hit
            tail = re.search(r"[^\w']*$", str(words[i + n - 1][2])).group(0)
            out.append((words[i][0], words[i + n - 1][1], str(val).strip() + tail))
            i += n
        else:
            out.append(words[i])
            i += 1
    return out


def word_fix_rules(settings):
    fx = settings.get("word_fixes") if isinstance(settings.get("word_fixes"), dict) else {}
    return {" ".join(_word_key(x) for x in k.split()): v for k, v in fx.items()}


def youtube_captions(url, out_base, lang=None, log=print):
    """YouTube's own word-timed captions for a video (free, fast, good with Hindi) -> [(start, end, word)] in
    seconds from the video start, or None. Original-language auto captions are preferred over translations."""
    want = [lang] if lang else ["en", "hi", "ne"]
    subs = ",".join(f"{x}-orig,{x}" for x in want)
    try:
        run(ytdlp_cmd() + _auth_args(YT_AUTH["working"]) + ["--skip-download", "--write-subs", "--write-auto-subs",
            "--sub-langs", subs, "--sub-format", "json3", "--no-playlist", "-o", out_base + ".%(ext)s", url],
            capture_output=True, timeout=300)
    except Exception as e:  # noqa: BLE001
        log(f"YouTube captions not available ({e})")
        return None
    best = None
    for x in want:
        for tag in (f"{x}-orig", x):
            p = f"{out_base}.{tag}.json3"
            if os.path.isfile(p):
                best = best or (p, x)
    if not best:
        return None
    try:
        with open(best[0], encoding="utf-8") as f:
            ev = json.load(f).get("events") or []
    except (OSError, ValueError):
        return None
    words = []
    for e in ev:
        t0 = (e.get("tStartMs") or 0) / 1000
        end = t0 + (e.get("dDurationMs") or 0) / 1000
        segs = [(t0 + (g.get("tOffsetMs") or 0) / 1000, (g.get("utf8") or "").strip()) for g in e.get("segs") or []]
        segs = [g for g in segs if g[1] and g[1] != "\n"]
        for k, (ts, txt) in enumerate(segs):
            te = segs[k + 1][0] if k + 1 < len(segs) else min(end, ts + 0.8)
            for part in txt.split():
                if part.startswith("[") and part.endswith("]"):      # [Music] [Applause]
                    continue
                words.append((round(ts, 3), round(max(ts + 0.08, te), 3), part))
    words.sort()
    for k in range(len(words) - 1):          # no overlaps: a word ends when the next one starts
        if words[k][1] > words[k + 1][0]:
            words[k] = (words[k][0], max(words[k][0] + 0.05, words[k + 1][0]), words[k][2])
    if len(words) < 10:
        return None
    LAST_LANG[0] = best[1]
    return {"lang": best[1], "words": words}


def slice_words(words, a, b):
    """Caption words between source seconds a..b, re-timed to start at 0."""
    return [(round(max(0.0, s - a), 3), round(e - a, 3), w) for s, e, w in words if e > a + 0.05 and s < b - 0.05]


def transcribe_auto(clip, work_dir, settings, log=print, speech=None, context="", given=None):
    """Captions: Whisper large-v3 on the NVIDIA card if installed, else Groq (large-v3 for Hindi/Nepali), else
    Whisper on the processor. Hindi/Nepali get an AI clean-up pass, and Roman letters if chosen."""
    code = speech_code(settings, speech)
    lang, script = SPEECH[code]
    engine = settings.get("stt", "auto")
    groq_key = settings.get("ai_key") if settings.get("ai_provider") == "groq" else settings.get("groq_key", "")
    groq_key = groq_key or settings.get("groq_key", "")
    hint = {"hi": "नमस्ते दोस्तों, आज हम गेम खेल रहे हैं। भाई ये क्या हुआ!", "ne": "नमस्ते साथीहरू, आज हामी गेम खेल्दैछौं। ए साथी यो के भयो!"}.get(lang)
    words, LAST_LANG[0], LAST_STT[0] = None, "", ""
    # every new clip uses a light, fast model; "AI captions" in the editor asks for the best (stt_quality=best)
    best = settings.get("stt_quality") == "best"
    gpu_model = "large-v3" if best else (settings.get("stt_auto_model") or "turbo")
    if given and len(given.get("words") or []) >= 3 and not best:       # YouTube's own captions for this part of the video
        words, LAST_LANG[0], LAST_STT[0] = list(given["words"]), given.get("lang") or "", "YouTube"
    if words is None and engine in ("auto", "gpu") and GPU_STT:
        wav = os.path.join(work_dir, os.path.splitext(os.path.basename(clip))[0] + "_stt.wav")
        if run(["ffmpeg", "-v", "error", "-y", "-i", clip, "-vn", "-ac", "1", "-ar", "16000", wav]).returncode == 0:
            words, det = GPU_STT(os.path.abspath(wav), lang, hint, gpu_model)
            if words is None:
                log(f"GPU captions not used ({det})")
            else:
                LAST_LANG[0], LAST_STT[0] = det or lang or "", f"graphics card ({gpu_model})"
            try:
                os.remove(wav)
            except OSError:
                pass
    if words is None and engine in ("auto", "gpu", "groq") and groq_key:
        gm = "whisper-large-v3" if (lang != "en" or best) else "whisper-large-v3-turbo"
        words = transcribe_groq(clip, work_dir, groq_key, lang, model=gm)
        if words is not None:
            LAST_STT[0] = f"Groq ({gm})"
        if words is None and engine == "groq":
            return None
    if words is None and engine in ("auto", "local"):
        words = transcribe(clip, work_dir, settings.get("model", "base"), lang)
        if words is not None:
            LAST_STT[0] = f"processor ({settings.get('model', 'base')})"
    if words is None and engine == "groq" and not groq_key:
        LAST_WHISPER_ERROR[0] = "Groq captions need a Groq API key in Settings"
    if not words:
        return words
    spoken = lang or {"hindi": "hi", "nepali": "ne", "hi": "hi", "ne": "ne"}.get((LAST_LANG[0] or "").lower(), "")
    if spoken in ("hi", "ne") and settings.get("stt_fix", True):
        try:
            words = fix_words(settings, words, spoken, script, context, log)
        except Exception as e:  # noqa: BLE001
            log(f"caption clean-up skipped: {e}")
    return apply_word_fixes(words, word_fix_rules(settings))


def _groq_any(clip, work_dir, key, lang):
    words = transcribe_groq(clip, work_dir, key, lang)
    if words is None and ("model" in LAST_WHISPER_ERROR[0].lower() and " 40" in LAST_WHISPER_ERROR[0]):
        words = transcribe_groq(clip, work_dir, key, lang, model="whisper-large-v3")
    return words


# ------------------------------------------------------------------ hooks & hashtags
STOP = set("""a an the and or but if so to of in on at for with is are was were be been it its this that
these those i you he she we they me him her us them my your his our their just like lol lmao omg bro
what when where who why how yes no not do does did dont don't im i'm its it's u ur ya yeah oh ok okay
can will would could should get got go going have has had all out up one more some very really too
from about there here then than chat stream""".split())

HOOKS = {   # backup hooks when no AI is set up; {name} = the streamer, when known
    "funny": ["{name} can't stop laughing", "{name} really just did that", "{name}'s funniest moment today",
              "Nobody expected {name} to say this", "{name} lost it here"],
    "hype":  ["{name} pulls off the impossible", "{name}'s clutch of the year", "{name} wins it at the last second",
              "{name}'s best moment this stream", "{name} goes all in"],
    "shock": ["{name} did NOT expect this", "{name} can't believe it", "Nobody saw this coming for {name}",
              "{name} gets the shock of his life", "This changed everything for {name}"],
}
EMOJI = {"funny": "😂💀", "hype": "🔥😤", "shock": "😳👀"}


def detect_mood(chat):
    text = " ".join(chat).lower()
    score = {
        "funny": len(re.findall(r"lmao|lol|haha|kek|omegalul|💀|😂|🤣|xd", text)),
        "hype": len(re.findall(r"\bw\b|pog|gg|clutch|insane|goat|lets go|🔥|hype|sheesh", text)),
        "shock": len(re.findall(r"\?\?|what|wtf|no way|bro|😳|💀|huh|omg", text)),
    }
    return max(score, key=score.get) if any(score.values()) else "hype"


def keywords(texts, n=3):
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9]{2,20}", " ".join(texts))
    c = Counter(w.lower() for w in words if w.lower() not in STOP)
    return [w for w, k in c.most_common(n) if k >= 2]


def _chat_texts(chat):
    """chat may be [text, ...] or [[seconds, text], ...]"""
    return [c[1] if isinstance(c, (list, tuple)) else c for c in chat or []]


def dead_air_trim(words, duration, min_len=10.0):
    """Built-in retention trim: cut silence before the first word and long silence after the last."""
    if not words or not duration:
        return 0.0, duration
    start, end = 0.0, duration
    if words[0][0] > 2.0:
        start = max(0.0, words[0][0] - 0.6)
    if duration - words[-1][1] > 4.0:
        end = min(duration, words[-1][1] + 2.5)
    if end - start < min_len:
        return 0.0, duration
    return round(start, 2), round(end, 2)


def offline_meta(words, chat, channel, base_tags, duration=0):
    transcript = " ".join(w[2] for w in words or [])
    texts = _chat_texts(chat)
    mood = detect_mood(texts)
    who = clean_channel(channel) or "He"
    hook = random.choice(HOOKS[mood]).replace("{name}'s", who + "'s" if who != "He" else "His").replace("{name}", who)
    hook = hook[0].upper() + hook[1:]
    # a short punchy line from the clip makes a better hook than a template
    for sent in re.split(r"(?<=[.!?])\s+", transcript):
        wc = len(sent.split())
        if 3 <= wc <= 9 and sent.rstrip().endswith(("!", "?")):
            hook = sent.strip().strip('"')
            break
    tags = [t if t.startswith("#") else "#" + t for t in base_tags]
    channel = clean_channel(channel)
    if channel:
        tags.insert(0, "#" + re.sub(r"\W+", "", channel))
    for k in keywords(texts + [transcript]):
        tags.append("#" + k)
    tags = list(dict.fromkeys(tags))[:10]
    t0, t1 = dead_air_trim(words, duration)
    return {"hook": hook, "yt_title": hook, "caption": f"{hook} {EMOJI[mood]}", "hashtags": tags,
            "mood": mood, "ai": False, "trim_start": t0, "trim_end": t1, "score": None, "why": ""}


AI_PROVIDERS = {
    # name: (OpenAI-compatible chat URL or None for Claude, default model)
    "groq": ("https://api.groq.com/openai/v1/chat/completions", "openai/gpt-oss-120b"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai/chat/completions", "gemini-flash-latest"),
    "claude": (None, "claude-haiku-5-5"),
    "ollama": ("http://127.0.0.1:11434/v1/chat/completions", "llama3.2:3b"),
}
FALLBACK_MODEL = {"groq": "openai/gpt-oss-20b"}


def pick_model(settings, provider):
    """The model set in Settings, unless it belongs to another AI provider (then this provider's default)."""
    model = settings.get("ai_model") or ""
    if model and provider != "ollama" and (model.startswith("claude") != (provider == "claude")):
        model = ""
    return model or AI_PROVIDERS[provider][1]     # separate free quota; Llama 3.3 70B is being retired

SYSTEM_PROMPT = """You are the editor and growth strategist of a fast-growing short-form clips channel on YouTube Shorts,
TikTok and Instagram Reels (gaming streams, music, trailers, podcasts, business and more). Your only goal is
retention: viewers must not swipe in the first 2 seconds, must watch to the end, and should want to rewatch, comment
and share. When a campaign brief is given, following it is mandatory. Think step by step. Reply with JSON only."""


def _fmt_words(words, every=2.5):
    """[(s,e,w)] -> '[0.0s] text text' lines grouped ~every seconds"""
    lines, cur, t0 = [], [], None
    for s, e, w in words or []:
        if t0 is None:
            t0 = s
        cur.append(w)
        if s - t0 >= every or w[-1:] in ".!?":
            lines.append(f"[{t0:.1f}s] {' '.join(cur)}")
            cur, t0 = [], None
    if cur:
        lines.append(f"[{t0:.1f}s] {' '.join(cur)}")
    return "\n".join(lines)[:6000]


def _fmt_chat(chat):
    out = []
    for c in (chat or [])[-120:]:
        if isinstance(c, (list, tuple)):
            out.append(f"[{float(c[0]):.0f}s] {str(c[1])[:80]}")
        else:
            out.append(str(c)[:80])
    return "\n".join(out)[:4000]


GENRES = {
    "stream": ("Gaming / live stream", "a live stream around a moment where chat exploded",
               "the funny / shocking / skillful moment chat reacted to"),
    "music": ("Music video / song", "a music video or song",
              "the catchiest part: the hook or chorus, the drop, or the most striking visual"),
    "trailer": ("Movie / show trailer", "a movie, series or game trailer",
                "the most exciting, emotional or quotable beat that makes people want to watch it"),
    "podcast": ("Podcast / interview", "a podcast or interview",
                "one complete strong point: a surprising opinion, a story with a punchline, or a hot take"),
    "business": ("Business / education", "a business, marketing or educational video",
                 "one clear, valuable, self-contained insight, tip or claim with a strong opening line"),
    "sports": ("Sports", "a sports broadcast or highlights",
               "the goal, the play, the save or the big reaction"),
    "vlog": ("Vlog / other", "a vlog or general video", "the most interesting, funny or surprising moment"),
}
_ID_RE = re.compile(r"^(yt|twitch|video|upload)_|_vod$|^[\w-]{11}$")


def clean_channel(channel):
    """Chipper's internal names (yt_<video id>_vod ...) are not channel names - never show them to the AI."""
    c = str(channel or "")
    if not c or c.startswith(("yt_", "tt_", "video_", "upload")) or c.endswith("_vod") or re.fullmatch(r"[\w-]{11}", c):
        return ""
    return c


def smart_prompt(words, chat, channel, base_tags, duration, ctx=None):
    ctx = ctx or {}
    genre = ctx.get("genre") if ctx.get("genre") in GENRES else "stream"
    glabel, gsrc, gpay = GENRES[genre]
    name = ctx.get("channel") if ctx else clean_channel(channel)
    src = []
    if ctx.get("title"):
        src.append(f"Source video title: {ctx['title'][:150]}")
    src.append(f"Creator / channel: {name or 'unknown - do NOT invent one and never use ids or codes'}")
    if ctx.get("description"):
        src.append(f"Source description (may help with names): {ctx['description'][:400]}")
    plan = ctx.get("plan") or {}
    brief = ""
    if ctx.get("brief"):
        brief = f"""
CAMPAIGN BRIEF (from the campaign organiser - follow it exactly, it overrides the defaults below):
{ctx['brief'][:1800]}
"""
    must = ""
    if plan.get("hashtags") or plan.get("mentions"):
        must = f"\nMUST include these hashtags: {' '.join(plan.get('hashtags', []))}" + \
               (f"\nMUST mention: {' '.join(plan.get('mentions', []))} in the caption" if plan.get("mentions") else "")
    lo, hi = plan.get("min_len") or 12, plan.get("max_len") or 35
    chat_part = ""
    if chat:
        chat_part = f"""
CHAT DURING THE CLIP (seconds from clip start, chat lags the video by ~2-5 s):
{_fmt_chat(chat)}
"""
    taste = ""
    if ctx.get("taste"):
        taste = f"""
THE OWNER'S TASTE (star ratings they gave past clips - score this clip like they would):
{ctx['taste'][:1500]}
"""
    return f"""CLIP: {duration:.1f} seconds, cut from {gsrc}. Content type: {glabel}.
{chr(10).join(src)}
{brief}{taste}
SPEECH / LYRICS (timestamps in seconds from the start of the clip):
{_fmt_words(words) or "(no speech detected)"}
{chat_part}
Do this:
1. Identify the PAYOFF ({gpay}) and the minimum SETUP needed.
2. Choose "start" and "end" (seconds inside 0..{duration:.1f}) for the best short:
   - open on action or a strong first line, never on silence, greetings or filler;
   - end just after the payoff so the video loops cleanly - cut any rambling after it;
   - aim for {lo:.0f}-{hi:.0f} s, minimum {min(lo, 8):.0f} s, maximum {max(hi, 15):.0f} s. If the whole clip is needed, use 0 and {duration:.1f}.
3. "hooks": THREE different on-screen hook options for the first 3 seconds, best first. Each 3-7 words.
   A stranger scrolling past must instantly see WHAT is happening and WHY it matters. So every hook MUST name
   something concrete from the clip: the person (by real name if given above), a number or amount, an object,
   a place, or the stakes. Good patterns:
     - an action with stakes: "He sold a $3M car for $1", "Speed bets $1M on one kick"
     - a specific question: "Can a PS5 survive this fall?", "Who gets the last PS5?"
     - a bold claim from the speech: "He says GTA 6 looks real"
   NEVER write about the chat or the audience ("chat goes wild", "chat explodes", "the chat can't stop…").
   NEVER use vague filler: "wait for it", "insane moment", "you won't believe", "what just happened",
   "what did he just say/claim", "this is crazy", "watch till the end", "epic fail". No emojis, no hashtags.
   True to the clip, and it must make you want the payoff without giving it away.
   "hook" = the best of the three.
4. "yt_title": YouTube Shorts title, max 70 characters. Same rule: say what happens, with the real name / number /
   stakes, so it reads like news, e.g. "Carry is giving away a PS5 this December 😱". At most one emoji, no hashtags.
   Use the real creator / artist / movie / brand name only if it is given above. NEVER put ids, codes or random
   letters in it, and never mention "chat" unless the chat itself is the story.
5. "caption": Instagram/TikTok caption, 1-2 short sentences in a natural voice that fits the content type, ends with
   a question or call to action, 1-2 emojis.
6. "hashtags": 6-8 real, readable tags with #: 1-2 broad, the creator / topic if known, 3-4 niche ones. Prefer some of: {" ".join(base_tags)}{must}
7. "score": 1-10, how likely this short is to perform well{' AND how well it matches the campaign brief' if brief else ''}. "why": one short sentence.

{lang_rule(ctx.get("lang"))}
Return ONLY a JSON object with keys: start, end, hooks, hook, yt_title, caption, hashtags, score, why."""


# hooks that say nothing about the clip: the AI is told not to write them, and this catches any that slip through
VAGUE_HOOK = re.compile(r"(?i)\bchat\b|wait for it|insane moment|won'?t believe|what (just )?happened|what did (he|she|they) just|"
                        r"this is (so )?crazy|watch (till|until) the end|epic fail|gone wrong|you need to see|craziest moment")


def pick_hook(options):
    """The first hook that names something real (not vague filler); else the first one."""
    opts = [o for o in options if o]
    return next((o for o in opts if not VAGUE_HOOK.search(o)), opts[0] if opts else "")


def _parse_ai(text, duration=0):
    j = json.loads(text[text.index("{"): text.rindex("}") + 1])
    tags = j.get("hashtags", [])
    if isinstance(tags, str):
        tags = tags.split()
    tags = [t if t.startswith("#") else "#" + t for t in (str(x).strip() for x in tags) if t][:10]
    opts = [str(h).strip().strip('"') for h in (j.get("hooks") or []) if str(h).strip()]
    hook = pick_hook([str(j.get("hook", "")).strip().strip('"')] + opts)
    out = {"hook": hook, "hook_options": [h for h in opts if h != hook][:3], "caption": str(j.get("caption", "")).strip(),
           "yt_title": str(j.get("yt_title", "") or j.get("hook", "")).strip()[:100],
           "hashtags": tags, "ai": True, "why": str(j.get("why", "")).strip()[:200]}
    try:
        out["score"] = max(1, min(10, int(round(float(j.get("score"))))))
    except (TypeError, ValueError):
        out["score"] = None
    try:
        st, en = float(j.get("start", 0)), float(j.get("end", duration))
        if duration:
            st, en = max(0.0, min(st, duration)), max(0.0, min(en, duration))
        if duration and en - st >= 6:
            out["trim_start"], out["trim_end"] = round(st, 2), round(en, 2)
    except (TypeError, ValueError):
        pass
    return out


def _chat_call(provider, key, model, system, prompt, url=None, json_mode=True):
    url = url or AI_PROVIDERS[provider][0]
    body = {"model": model, "max_tokens": 1800 if provider == "groq" else 3000,   # Groq free: 8K tokens/min
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    if provider == "groq" and "gpt-oss" in model:
        body["reasoning_effort"] = "medium"
    else:
        body["temperature"] = 0.8
    req = urllib.request.Request(os.environ.get("CHIPPER_AI_URL", url), data=json.dumps(body).encode(),
                                 headers={"Authorization": "Bearer " + (key or "ollama"), "Content-Type": "application/json",
                                          "User-Agent": "ChipperStudio/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=180 if provider == "ollama" else 90) as r:
            return json.loads(r.read())["choices"][0]["message"]["content"]
    except urllib.error.HTTPError as e:
        body_txt = e.read().decode("utf-8", "replace")[:250]
        if e.code == 429 and "per minute" in body_txt and not getattr(_chat_call, "_retrying", False):
            # per-minute limit: wait as told (max 60 s) and try once more before using the backup model
            wait = min(60.0, float(e.headers.get("retry-after") or 20))
            time.sleep(wait)
            _chat_call._retrying = True
            try:
                return _chat_call(provider, key, model, system, prompt, url, json_mode)
            finally:
                _chat_call._retrying = False
        if json_mode and e.code == 400 and "response_format" in body_txt:
            return _chat_call(provider, key, model, system, prompt, url, json_mode=False)
        raise RuntimeError(f"{e.code} {body_txt}") from None
    except urllib.error.URLError as e:
        if provider == "ollama":
            raise RuntimeError(f"Ollama is not running at {url} ({e.reason}) - start the Ollama app") from None
        raise


def ai_meta(provider, key, model, prompt, duration=0, url=None):
    _, default_model = AI_PROVIDERS[provider]
    model = model or default_model
    if provider == "claude":
        body = {"model": model, "max_tokens": 1200, "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": prompt}]}
        req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=json.dumps(body).encode(),
                                     headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                                              "content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=60) as r:
            return _parse_ai(json.loads(r.read())["content"][0]["text"], duration)
    try:
        text = _chat_call(provider, key, model, SYSTEM_PROMPT, prompt, url)
    except RuntimeError as e:
        fb = FALLBACK_MODEL.get(provider)
        if not fb or fb == model:
            raise
        text = _chat_call(provider, key, fb, SYSTEM_PROMPT, prompt)   # model unavailable / rate limited
        out = _parse_ai(text, duration)
        out["model_note"] = f"used {fb} ({str(e)[:60]})"
        return out
    return _parse_ai(text, duration)


ID_TOKEN = re.compile(r"(?<![\w])#?(?=[\w-]*\d)(?=[\w-]*[A-Za-z])[\w-]{9,}(?![\w])")


def tidy_meta(meta, channel="", plan=None):
    """Remove leaked ids/codes and apply the campaign's required hashtags and mentions."""
    plan = plan or {}
    bad = set()
    if channel and not clean_channel(channel):          # internal name like yt_<video id>_vod -> never show it
        bad.add(str(channel).lower())
        raw_id = re.sub(r"^(yt|tt|twitch|video)_|_vod$", "", str(channel))
        raw_id = re.sub(r"_\d{8}_\d{6}.*$", "", raw_id)
        if raw_id:
            bad.add(raw_id.lower())

    norm = lambda x: re.sub(r"[^a-z0-9]", "", str(x).lower())
    badn = {norm(b) for b in bad if len(norm(b)) >= 6}

    def looks_id(tok):
        t = tok.lstrip("#@").lower()
        tn = norm(t)
        if not tn:
            return False
        if t.startswith(("yt_", "tt_", "twitch_", "video_", "upload_")) or t.endswith("_vod"):
            return True
        if tn in badn or any(len(tn) >= 6 and (tn in b or b in tn) for b in badn):
            return True
        # random code-like words: digits inside letters AND mixed upper/lower case (e.g. QQV0OUGxGs, dQw4w9WgXcQ)
        raw = tok.lstrip("#@")
        return bool(len(raw) >= 8 and re.search(r"[A-Za-z]\d+[A-Za-z]", raw) and re.search(r"[A-Z]", raw[1:])
                    and re.search(r"[a-z]", raw))

    for k in ("yt_title", "hook", "caption"):
        v = str(meta.get(k) or "")
        v = " ".join(w for w in v.split() if not looks_id(w.strip(".,!?:;\"'()")))
        meta[k] = v.strip()
    tags = [t for t in meta.get("hashtags", []) if t and not looks_id(t) and re.fullmatch(r"#[\w\u0900-\u097F]{2,40}", t)]
    req = [t if t.startswith("#") else "#" + t for t in plan.get("hashtags", [])]
    tags = list(dict.fromkeys(req + tags))
    if plan.get("max_tags"):
        tags = tags[:max(len(req), int(plan["max_tags"]))]
    meta["hashtags"] = tags[:15]
    cap = meta.get("caption", "")
    for m in plan.get("mentions", []):
        if m.lower() not in cap.lower():
            cap = (cap + " " + m).strip()
    if plan.get("cta") and plan["cta"].lower() not in cap.lower():
        cap = (cap + " " + plan["cta"]).strip()
    meta["caption"] = cap
    if not meta.get("yt_title"):
        meta["yt_title"] = meta.get("hook", "")
    return meta


def make_meta(settings, words, chat, channel, duration=0, log=print, ctx=None):
    """Hook, titles, caption, hashtags, score and (if smart trim is on) the best start/end."""
    base = [t for t in re.split(r"[\s,]+", settings.get("hashtags", "")) if t]
    provider = settings.get("ai_provider") or ("claude" if settings.get("api_key") else "builtin")
    key = settings.get("ai_key") or (settings.get("api_key") if provider == "claude" else "")
    meta = None
    url = None
    if provider == "ollama":
        url = (settings.get("ollama_url") or "http://127.0.0.1:11434").rstrip("/") + "/v1/chat/completions"
    if provider in AI_PROVIDERS and (key or provider == "ollama"):
        try:
            model = settings.get("ai_model") or ""
            if model and provider != "ollama" and (model.startswith("claude") != (provider == "claude")):
                model = ""          # model name from another provider -> use this provider's default
            meta = ai_meta(provider, key, model, smart_prompt(words, chat, channel, base, duration, ctx), duration, url)
            if not meta.get("hook"):
                meta = None
        except Exception as e:  # noqa: BLE001
            log(f"AI ({provider}) failed: {e} - using built-in hooks")
    if meta is None:
        meta = offline_meta(words, chat, clean_channel((ctx or {}).get("channel") or channel), base, duration)
    if not settings.get("smart_trim", True) or "trim_start" not in meta:
        meta["trim_start"], meta["trim_end"] = 0.0, duration
    return tidy_meta(meta, channel, (ctx or {}).get("plan"))


# ------------------------------------------------------------------ render
def media_span(path, tail_mb=6):
    """(first, last) media timestamp in a growing recording, in seconds. Reads only the end of the file."""
    first = run(["ffprobe", "-v", "error", "-show_entries", "format=start_time", "-of", "csv=p=0", path],
                capture_output=True, text=True).stdout.strip().splitlines()
    try:
        first = float(first[0])
    except (ValueError, IndexError):
        first = 0.0
    size = os.path.getsize(path)
    tmp = path + ".tail.ts"
    try:
        with open(path, "rb") as f, open(tmp, "wb") as o:
            f.seek(max(0, size - tail_mb * 1024 * 1024))
            o.write(f.read())
        out = run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "packet=pts_time", "-of", "csv=p=0", tmp],
                  capture_output=True, text=True).stdout.split()
        pts = [float(x.strip(",")) for x in out if x.strip(",").replace(".", "", 1).isdigit()]
        last = max(pts) if pts else first + probe_duration(path)
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass
    return first, last


def wait_for_recording(rec_path, start, length, should_wait=lambda: True, log=print, limit=240):
    """A slow connection makes the recording fall behind the live stream. Wait until the moment is really saved.
    -> (start, length) to cut, moved back if it never arrives."""
    t0, said = time.time(), False
    while True:
        try:
            first, last = media_span(rec_path)
        except Exception:  # noqa: BLE001
            return start, length
        have = last - first
        if have >= start + length - 0.5:
            return start, length
        if time.time() - t0 > limit or not should_wait():
            if have <= 5:
                return start, length
            length = min(length, have)
            return max(0.0, have - length), length
        if not said:
            log(f"recording is {start + length - have:.0f} s behind the live stream (slow internet) - waiting for the moment")
            said = True
        time.sleep(4)


def cut_raw(rec_path, start, length, raw_path):
    """16:9 source segment (kept so clips can be re-styled / re-cropped later)."""
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{start:.2f}",
           "-i", rec_path, "-t", f"{length:.2f}", "-map", "0:v:0", "-map", "0:a:0?",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "16",
           "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", raw_path]
    return run(cmd).returncode == 0 and os.path.exists(raw_path) and probe_duration(raw_path) > 1


def probe_duration(path):
    out = run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
              capture_output=True, text=True).stdout.strip()
    try:
        return float(out)
    except ValueError:
        return 0.0


RENDER_Q = {"standard": ("19", "10M"), "high": ("16", "16M"), "max": ("14", "24M")}
RENDER_QUALITY = "high"          # set from Settings - final clip quality before Instagram / TikTok / YouTube re-compress it


def enc_args():
    crf, cap = RENDER_Q.get(RENDER_QUALITY, RENDER_Q["high"])
    return ["-c:v", "libx264", "-preset", "faster", "-crf", crf, "-maxrate", cap, "-bufsize", str(int(cap[:-1]) * 2) + "M",
            "-profile:v", "high", "-pix_fmt", "yuv420p", "-r", "30"]


def render(raw_path, out_path, template, words, hook_text, work_dir, start=0.0, end=None, overlays=None, logo=None):
    """crop/layout + captions + hook in ONE encode -> 1080x1920 mp4. start/end trim the source."""
    w, h = probe_size(raw_path)
    name = os.path.splitext(os.path.basename(out_path))[0]
    ass = os.path.join(work_dir, name + ".ass")
    start = max(0.0, float(start or 0))
    if end is not None and float(end) - start < 3:
        end = None
    # captions are timed from the start of the trimmed clip
    shifted = [(ws - start, we - start, t) for ws, we, t in (words or [])
               if we > start and (end is None or ws < float(end))]
    shifted = [(max(0.0, a), b, t) for a, b, t in shifted]
    write_ass(ass, shifted, template, hook_text, overlays=overlays)
    # run in work_dir so the ass path is relative (Windows drive letters break ffmpeg filters)
    vf = layout_filter(w, h, template, extra=ass_filter(f"{name}.ass", work_dir))
    t = merged_template(template)
    dur = (float(end) - start) if end is not None else max(0.5, probe_duration(raw_path) - start)
    if t.get("progress_bar"):
        vf = vf[:-3] + f"[pb0];color=c=0xFFD93D:s=1080x12:r=30:d={dur:.2f}[bar];" \
                       f"[pb0][bar]overlay=x='-W+W*t/{dur:.2f}':y=H-h:shortest=1[v]"
    if logo and logo.get("path") and os.path.exists(logo["path"]):
        lw = even(1080 * min(max(float(logo.get("w", 0.22)), 0.05), 1.0))
        lx = int(1080 * float(logo.get("x", 0.85))) - lw // 2
        ly = int(1920 * float(logo.get("y", 0.06)))
        vf = vf[:-3] + f"[lg0];[1:v]scale={lw}:-1,format=rgba,colorchannelmixer=aa={float(logo.get('opacity', 1)):.2f}[lg];" \
                       f"[lg0][lg]overlay=x={lx}:y={ly}-h/2:shortest=1[v]"
    tmp = out_path + ".part.mp4"
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    if start > 0:
        cmd += ["-ss", f"{start:.2f}"]
    cmd += ["-i", os.path.abspath(raw_path)]
    if logo and logo.get("path") and os.path.exists(logo["path"]):
        cmd += ["-loop", "1", "-i", os.path.abspath(logo["path"])]
    if end is not None:
        cmd += ["-t", f"{float(end) - start:.2f}"]
    cmd += ["-filter_complex", vf, "-map", "[v]", "-map", "0:a:0?"] + enc_args() + [
            "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", os.path.abspath(tmp)]
    r = run(cmd, cwd=work_dir, capture_output=True, text=True)
    try:
        os.remove(ass)
    except OSError:
        pass
    if r.returncode != 0:
        return False, r.stderr[-400:]
    os.replace(tmp, out_path)
    return True, ""


def cover_first_frame(video, jpg, out):
    """Copy of the clip whose first 0.1 s is the thumbnail (TikTok uses the first frame as the cover)."""
    r = run(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-t", "0.1", "-i", jpg, "-i", video, "-filter_complex",
             "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,fps=30,format=yuv420p,setsar=1[a];"
             "[1:v]scale=1080:1920,fps=30,format=yuv420p,setsar=1[b];[a][b]concat=n=2:v=1:a=0[v];"
             "[1:a]adelay=100|100[aa]", "-map", "[v]", "-map", "[aa]",
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-c:a", "aac", "-b:a", "192k",
             "-movflags", "+faststart", out], capture_output=True, text=True)
    return r.returncode == 0 and os.path.isfile(out)


def make_thumb(video, jpg):
    run(["ffmpeg", "-v", "error", "-y", "-ss", "1.5", "-i", video, "-frames:v", "1",
         "-vf", "scale=270:-2", "-q:v", "4", jpg])


# ------------------------------------------------------------------ finished streams / videos (VOD)
def ytdlp_cmd():
    exe = shutil.which("yt-dlp")
    return [exe] if exe else [sys.executable, "-m", "yt_dlp"]


# YouTube sometimes answers "Sign in to confirm you're not a bot". yt-dlp then needs the cookies of a signed-in
# YouTube session: either a cookies.txt file or read straight from a browser on this PC.
YT_AUTH = {"mode": "auto", "file": "", "working": None, "updated": False}
BOT_MARKERS = ("confirm you", "not a bot", "Sign in to confirm", "cookies-from-browser")


def _auth_args(a):
    if not a:
        return []
    if a.startswith("file:"):
        return ["--cookies", a[5:]]
    return ["--cookies-from-browser", a]


def auth_candidates():
    """Ways to try, best first. 'auto' tries no cookies, then the cookies file, then each browser."""
    m, f = YT_AUTH["mode"], YT_AUTH["file"]
    has_file = bool(f and os.path.isfile(f))
    if m == "off":
        return [None]
    if m == "file":
        return ["file:" + f] if has_file else [None]
    if m in ("firefox", "chrome", "edge", "brave", "opera", "vivaldi"):
        return [m]
    out = [YT_AUTH["working"]] if YT_AUTH["working"] else []
    out += [None] + (["file:" + f] if has_file else []) + ["firefox", "edge", "chrome", "brave"]
    seen, res = set(), []
    for a in out:
        if a not in seen:
            seen.add(a)
            res.append(a)
    return res


def update_ytdlp():
    """YouTube changes often; a fresh yt-dlp fixes many download errors."""
    if YT_AUTH["updated"]:
        return False
    YT_AUTH["updated"] = True
    r = run([sys.executable, "-m", "pip", "install", "--upgrade", "--quiet", "yt-dlp"], capture_output=True,
            text=True, timeout=300)
    return r.returncode == 0


def download_vod(url, out_base, progress=lambda pct, msg: None, max_height=1080, stop=lambda: False, log=print):
    """Download with yt-dlp; if YouTube asks for a sign-in, update yt-dlp and retry with cookies."""
    tried, last = [], None
    cands = auth_candidates()
    i = 0
    while i < len(cands):
        a = cands[i]
        try:
            r = _download_once(url, out_base, progress, max_height, stop, _auth_args(a))
            if a and YT_AUTH["mode"] == "auto" and YT_AUTH["working"] != a:
                YT_AUTH["working"] = a
                log(f"YouTube download works with cookies from {'your cookies file' if a.startswith('file:') else a}")
            return r
        except RuntimeError as e:
            last = e
            msg = str(e)
            if msg == "stopped" or not any(k in msg for k in BOT_MARKERS):
                raise
            tried.append(a or "no cookies")
            if a is None and not YT_AUTH["updated"]:
                log("YouTube asked for a sign-in - updating yt-dlp and trying again")
                progress(1, "updating the downloader (yt-dlp)...")
                if update_ytdlp():
                    continue                     # same attempt again with the new yt-dlp
            if a is not None:
                log(f"cookies from {'your cookies file' if a.startswith('file:') else a} did not work")
            i += 1
    raise RuntimeError("YouTube wants a signed-in session for this video (bot check). Fix: in Chipper on the PC open "
                       "Settings -> Social accounts -> YouTube downloads and upload a cookies.txt from a browser "
                       "signed in to YouTube (or sign in to YouTube in Firefox on the PC). Tried: " + ", ".join(tried))


def _download_once(url, out_base, progress, max_height, stop, auth):
    out_tpl = out_base + ".%(ext)s"
    cmd = ytdlp_cmd() + auth + [
        "-f", f"bv*[height<={max_height}][vcodec^=avc1]+ba[ext=m4a]/bv*[height<={max_height}]+ba/b[height<={max_height}]/b",
        "--merge-output-format", "mp4", "--no-playlist", "--newline", "--no-part",
        "--print", "before_dl:TITLE:%(title)s", "-o", out_tpl, url]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            encoding="utf-8", errors="replace", creationflags=NO_WINDOW)
    title, tail = "", deque(maxlen=6)
    for line in proc.stdout:
        line = line.strip()
        tail.append(line)
        if stop():
            proc.kill()
            raise RuntimeError("stopped")
        if line.startswith("TITLE:"):
            title = line[6:]
            progress(1, f"downloading: {title[:60]}")
        m = re.search(r"\[download\]\s+([\d.]+)%.*?(?:of\s+~?\s*([\d.]+\w+))?", line)
        if m:
            progress(min(99.0, float(m.group(1))), f"downloading {m.group(1)}%" + (f" of {m.group(2)}" if m.group(2) else ""))
    proc.wait()
    path = out_base + ".mp4"
    if proc.returncode != 0 or not os.path.exists(path):
        cands = [out_base + e for e in (".mkv", ".webm") if os.path.exists(out_base + e)]
        if proc.returncode == 0 and cands:
            run(["ffmpeg", "-v", "error", "-y", "-i", cands[0], "-c", "copy", path])
        if not os.path.exists(path):
            raise RuntimeError("download failed: " + " | ".join(tail)[-400:])
    return path, title


def chat_replay(url, out_base, kind):
    """-> [(seconds_into_video, text)] from the stream's chat replay, or [] if there is none."""
    msgs = []
    if kind == "youtube":
        run(ytdlp_cmd() + _auth_args(YT_AUTH["working"]) + ["--skip-download", "--write-subs", "--sub-langs", "live_chat", "--no-playlist",
                           "-o", out_base + ".%(ext)s", url], capture_output=True, timeout=1800)
        path = out_base + ".live_chat.json"
        if os.path.exists(path):
            with open(path, encoding="utf-8", errors="replace") as f:
                for line in f:
                    try:
                        j = json.loads(line)
                        rep = j.get("replayChatItemAction") or {}
                        t = float(rep.get("videoOffsetTimeMsec", 0)) / 1000
                        for a in rep.get("actions", []):
                            r = (a.get("addChatItemAction", {}).get("item", {}).get("liveChatTextMessageRenderer")
                                 or a.get("addChatItemAction", {}).get("item", {}).get("liveChatPaidMessageRenderer"))
                            if r:
                                text = "".join(x.get("text") or x.get("emoji", {}).get("shortcuts", [""])[0]
                                               for x in r.get("message", {}).get("runs", []))
                                msgs.append((t, text))
                    except (ValueError, KeyError, TypeError, IndexError):
                        continue
    elif kind == "twitch":
        try:
            from chat_downloader import ChatDownloader     # optional: pip install chat-downloader
            for m in ChatDownloader().get_chat(url):
                t = m.get("time_in_seconds")
                if t is not None:
                    msgs.append((float(t), m.get("message", "")))
        except Exception:   # noqa: BLE001 - chat is a bonus signal, clipping still works without it
            msgs = []
    return msgs


def loudness_curve(video, rate=4000):
    """-> list of loudness in dB, one value per second of the video (for finding loud moments)."""
    try:
        import numpy as np
    except ImportError:
        np = None
    proc = subprocess.Popen(["ffmpeg", "-v", "error", "-i", video, "-vn", "-ac", "1", "-ar", str(rate),
                             "-f", "s16le", "-"], stdout=subprocess.PIPE, creationflags=NO_WINDOW)
    out, chunk = [], rate * 2
    while True:
        buf = proc.stdout.read(chunk)
        if not buf:
            break
        if np is not None:
            a = np.frombuffer(buf[: len(buf) // 2 * 2], dtype=np.int16).astype(np.float32)
            rms = float(np.sqrt(np.mean(a * a))) if a.size else 0.0
        else:
            import array
            a = array.array("h", buf[: len(buf) // 2 * 2])[::8]
            rms = (sum(x * x for x in a) / max(1, len(a))) ** 0.5
        out.append(20 * __import__("math").log10(max(rms, 1.0) / 32768))
    proc.wait()
    return out


def _rolling_median(vals, half):
    import statistics
    return [statistics.median(vals[max(0, i - half): i + half + 1]) for i in range(len(vals))]


def find_candidates(duration, chat, loud, count, pre=25.0, post=15.0, bucket=10):
    """Rank moments by chat spikes (if there is chat replay) and loudness peaks.
    -> [{"start", "end", "peak", "signal", "msgs"}] best first, non-overlapping."""
    nb = max(1, int(duration // bucket))
    chat_b = [0] * nb
    for t, _ in chat:
        i = int(t // bucket)
        if 0 <= i < nb:
            chat_b[i] += 1
    loud_b = []
    for i in range(nb):
        seg = loud[i * bucket:(i + 1) * bucket]
        # top of the bucket matters (a scream), not the average
        loud_b.append(max(seg) * 0.6 + (sum(seg) / len(seg)) * 0.4 if seg else -90.0)
    has_chat = sum(chat_b) >= max(20, nb // 3)
    c_med = _rolling_median(chat_b, 18) if has_chat else [0] * nb     # ~3 min either side
    l_med = _rolling_median(loud_b, 30)                               # ~5 min either side
    scores = []
    for i in range(nb):
        loud_x = max(0.0, loud_b[i] - l_med[i])                       # dB above what's normal around it
        s = loud_x / 6.0
        if has_chat:
            # chat lags the video by a few seconds: look at this bucket and the next one
            n = max(chat_b[i], chat_b[i + 1] if i + 1 < nb else 0)
            ratio = n / max(c_med[i], 1.0)
            s = 0.7 * min(ratio, 6.0) + 0.3 * min(s, 3.0)
        scores.append((s, i))
    scores.sort(reverse=True)
    picked, taken = [], []
    gap = pre + post
    for s, i in scores:
        if len(picked) >= count or s <= 0:
            break
        peak = (i + 1) * bucket
        if any(abs(peak - p) < gap for p in taken):
            continue
        start = max(0.0, peak - pre)
        end = min(duration, peak + post)
        if end - start < 8:
            continue
        taken.append(peak)
        n = max(chat_b[i], chat_b[i + 1] if i + 1 < nb else 0)
        picked.append({"start": round(start, 2), "end": round(end, 2), "peak": peak,
                       "signal": round(s, 2), "msgs": n})
    return picked, has_chat


def ollama_models(base="http://127.0.0.1:11434"):
    """-> list of installed Ollama model names, or raises RuntimeError if Ollama isn't running."""
    try:
        with urllib.request.urlopen(base.rstrip("/") + "/api/tags", timeout=5) as r:
            return [m.get("name") for m in json.loads(r.read()).get("models", [])]
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise RuntimeError(f"Ollama is not running at {base} ({getattr(e, 'reason', e)})") from None


# ================================================================== free-form scene (portrait editor)
# A scene describes the whole 1080x1920 frame: a background, any number of video windows (each shows a crop of
# the source inside a shape), images, text, captions, hook, lower thirds and a progress bar. The browser editor
# draws exactly the same thing (static/editor.js mirrors shape_points), so what you see is what renders.
SHAPES = ("rect", "round", "pill", "circle", "heart", "star", "hexagon", "diamond", "arch", "squircle")


def _rr_points(w, h, tl, tr, br, bl, seg=10):
    """Rounded rectangle outline (clockwise from the top-right corner)."""
    import math
    pts = []
    corners = ((w - tr, tr, tr, -90, (w, 0)), (w - br, h - br, br, 0, (w, h)),
               (bl, h - bl, bl, 90, (0, h)), (tl, tl, tl, 180, (0, 0)))
    for cx, cy, r, a0, sharp in corners:
        if r <= 0.5:
            pts.append(sharp)
            continue
        for i in range(seg + 1):
            a = math.radians(a0 + 90 * i / seg)
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def _fit_box(pts, w, h):
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    return [((x - x0) / ((x1 - x0) or 1) * w, (y - y0) / ((y1 - y0) or 1) * h) for x, y in pts]


def shape_points(shape, w, h, radius=0.12):
    """Outline of a shape filling a w x h box, as a list of (x, y) points. Mirrored in editor.js."""
    import math
    m = min(w, h)
    if shape == "round":
        r = min(max(radius, 0.0), 0.5) * m
        return _rr_points(w, h, r, r, r, r)
    if shape == "pill":
        r = m / 2
        return _rr_points(w, h, r, r, r, r)
    if shape == "arch":
        r = min(w / 2, h)
        return _rr_points(w, h, r, r, 0, 0)
    if shape == "circle":
        return [(w / 2 + w / 2 * math.cos(2 * math.pi * i / 96), h / 2 + h / 2 * math.sin(2 * math.pi * i / 96))
                for i in range(96)]
    if shape == "squircle":
        pts = []
        for i in range(96):
            a = 2 * math.pi * i / 96
            c, s = math.cos(a), math.sin(a)
            pts.append((math.copysign(abs(c) ** 0.5, c), math.copysign(abs(s) ** 0.5, s)))
        return _fit_box(pts, w, h)
    if shape == "heart":
        pts = []
        for i in range(120):
            t = 2 * math.pi * i / 120
            pts.append((16 * math.sin(t) ** 3,
                        -(13 * math.cos(t) - 5 * math.cos(2 * t) - 2 * math.cos(3 * t) - math.cos(4 * t))))
        return _fit_box(pts, w, h)
    if shape == "star":
        pts = []
        for i in range(10):
            a = math.radians(-90 + 36 * i)
            r = 1.0 if i % 2 == 0 else 0.48
            pts.append((r * math.cos(a), r * math.sin(a)))
        return _fit_box(pts, w, h)
    if shape == "hexagon":
        return _fit_box([(math.cos(math.radians(60 * i)), math.sin(math.radians(60 * i))) for i in range(6)], w, h)
    if shape == "diamond":
        return [(w / 2, 0), (w, h / 2), (w / 2, h), (0, h / 2)]
    return [(0, 0), (w, 0), (w, h), (0, h)]


def write_mask(path, pts, w, h, maxdim=420):
    """Fill a polygon into a small greyscale PGM (ffmpeg scales it up, which also smooths the edge)."""
    k = min(1.0, maxdim / max(w, h, 1))
    mw, mh = max(2, int(round(w * k))), max(2, int(round(h * k)))
    sp = [(x * mw / w, y * mh / h) for x, y in pts]
    edges = list(zip(sp, sp[1:] + sp[:1]))
    data = bytearray(mw * mh)
    for y in range(mh):
        yc = y + 0.5
        xs = []
        for (x1, y1), (x2, y2) in edges:
            if (y1 <= yc < y2) or (y2 <= yc < y1):
                xs.append(x1 + (yc - y1) * (x2 - x1) / (y2 - y1))
        xs.sort()
        row = y * mw
        for i in range(0, len(xs) - 1, 2):
            a, b = max(0, int(round(xs[i]))), min(mw, int(round(xs[i + 1])))
            if b > a:
                data[row + a:row + b] = b"\xff" * (b - a)
    with open(path, "wb") as f:
        f.write(f"P5\n{mw} {mh}\n255\n".encode() + bytes(data))


def write_gradient(path, c1, c2, angle="v"):
    def rgb(c):
        c = (c or "#000000").lstrip("#")
        c = "".join(x * 2 for x in c) if len(c) == 3 else c
        try:
            return [int(c[i:i + 2], 16) for i in (0, 2, 4)]
        except ValueError:
            return [0, 0, 0]
    a, b = rgb(c1), rgb(c2)
    w, h = 54, 96
    data = bytearray()
    for y in range(h):
        for x in range(w):
            t = y / (h - 1) if angle == "v" else (x / (w - 1) + y / (h - 1)) / 2
            data += bytes(int(a[i] + (b[i] - a[i]) * t) for i in range(3))
    with open(path, "wb") as f:
        f.write(f"P6\n{w} {h}\n255\n".encode() + bytes(data))


def _num(v, d, lo=None, hi=None):
    try:
        v = float(v)
        if v != v:
            raise ValueError
    except (TypeError, ValueError):
        v = d
    if lo is not None:
        v = max(lo, v)
    if hi is not None:
        v = min(hi, v)
    return v


def _col(c, d="#ffffff"):
    c = str(c or d).strip()
    return c if re.fullmatch(r"#[0-9a-fA-F]{6}", c) else d


def clean_scene(sc):
    """Validate a scene coming from the browser (numbers clamped, files reduced to plain names)."""
    sc = sc if isinstance(sc, dict) else {}
    bg = sc.get("bg") if isinstance(sc.get("bg"), dict) else {}
    out = {"v": 1, "bg": {"type": bg.get("type") if bg.get("type") in ("blur", "color", "gradient", "image") else "color",
                          "color": _col(bg.get("color"), "#000000"), "color2": _col(bg.get("color2"), "#3a1c71"),
                          "image": os.path.basename(str(bg.get("image") or ""))[:120],
                          "dim": _num(bg.get("dim"), 0.25, 0, 0.9), "blur": _num(bg.get("blur"), 14, 0, 40)}}
    pb = sc.get("progress") if isinstance(sc.get("progress"), dict) else {}
    out["progress"] = {"on": bool(pb.get("on")), "color": _col(pb.get("color"), "#ffd93d"),
                       "pos": "top" if pb.get("pos") == "top" else "bottom", "h": _num(pb.get("h"), 12, 4, 40)}
    out["fx"] = []
    for f in (sc.get("fx") or [])[:60]:
        if isinstance(f, dict) and f.get("type") in FX_TYPES:
            out["fx"].append({"id": str(f.get("id") or "")[:20], "type": f["type"], "start": _num(f.get("start"), 0, 0, 3600),
                              "dur": _num(f.get("dur"), 0.5, 0.05, 30), "amt": _num(f.get("amt"), 0.7, 0.1, 1.0),
                              "color": _col(f.get("color"), "#ffffff")})
    out["sfx"] = []
    for f in (sc.get("sfx") or [])[:60]:
        snd = str((f or {}).get("sound") or "")[:120] if isinstance(f, dict) else ""
        if snd in SFX or snd.startswith("file:"):
            out["sfx"].append({"id": str(f.get("id") or "")[:20], "sound": snd if snd in SFX else "file:" + os.path.basename(snd[5:]),
                               "start": _num(f.get("start"), 0, 0, 3600), "vol": _num(f.get("vol"), 0.8, 0, 2)})
            if f.get("duck"):      # a voice-over: the clip's own sound gets quieter while it speaks
                out["sfx"][-1].update(duck=True, len=_num(f.get("len"), 2, 0.2, 120), name=str(f.get("name") or "")[:60])
    bl = sc.get("bleep") if isinstance(sc.get("bleep"), dict) else {}
    out["bleep"] = {"mode": "mute" if bl.get("mode") == "mute" else "beep", "camblur": bool(bl.get("camblur")),
                    "spans": sorted([[_num(q[0], 0, 0, 86400), _num(q[1], 0, 0, 86400)] for q in (bl.get("spans") or [])[:80]
                                     if isinstance(q, (list, tuple)) and len(q) == 2 and float(q[1]) > float(q[0])])}
    mu = sc.get("music") if isinstance(sc.get("music"), dict) else {}
    out["music"] = {"file": os.path.basename(str(mu.get("file") or ""))[:120], "name": str(mu.get("name") or "")[:60],
                    "vol": _num(mu.get("vol"), 0.15, 0, 1), "peak": _num(mu.get("peak"), 0.45, 0, 1.5),
                    "rise": _num(mu.get("rise"), 4, 0, 20), "duck": mu.get("duck") is not False,
                    "offset": _num(mu.get("offset"), 0, 0, 3600)}
    out["loop"] = bool(sc.get("loop"))
    layers = []
    for l in (sc.get("layers") or [])[:40]:
        if not isinstance(l, dict):
            continue
        k = l.get("kind")
        base = {"kind": k, "id": str(l.get("id") or "")[:20], "name": str(l.get("name") or "")[:40],
                "hidden": bool(l.get("hidden")), "locked": bool(l.get("locked"))}   # locked: can't be moved by touch
        if k == "video":
            cr = l.get("crop") if isinstance(l.get("crop"), list) and len(l.get("crop")) == 4 else [0, 0, 1, 1]
            base.update(role=l.get("role") if l.get("role") in ("cam", "game", "custom") else "custom",
                        crop=[_num(cr[0], 0, 0, 1), _num(cr[1], 0, 0, 1), _num(cr[2], 1, 0.02, 1), _num(cr[3], 1, 0.02, 1)],
                        x=_num(l.get("x"), 0, -2000, 3000), y=_num(l.get("y"), 0, -3000, 4000),
                        w=_num(l.get("w"), 1080, 40, 4000), h=_num(l.get("h"), 1920, 40, 6000),
                        shape=l.get("shape") if l.get("shape") in SHAPES else "rect",
                        radius=_num(l.get("radius"), 0.12, 0, 0.5), border=_num(l.get("border"), 0, 0, 60),
                        bcolor=_col(l.get("bcolor"), "#ffffff"),
                        keys=sorted([[_num(q[0], 0, 0, 86400), _num(q[1], 0, 0, 1), _num(q[2], 0, 0, 1)]
                                     + ([_num(q[3], 0.1, 0.02, 1), _num(q[4], 0.1, 0.02, 1)] if len(q) == 5 else [])
                                     for q in (l.get("keys") or [])[:80] if isinstance(q, list) and len(q) in (3, 5)]),
                        kmode="cut" if l.get("kmode") == "cut" else "smooth", opacity=_num(l.get("opacity"), 1, 0.05, 1))
        elif k == "image":
            base.update(file=os.path.basename(str(l.get("file") or ""))[:120], x=_num(l.get("x"), 540),
                        y=_num(l.get("y"), 300), w=_num(l.get("w"), 300, 20, 4000),
                        opacity=_num(l.get("opacity"), 1, 0.05, 1), start=_num(l.get("start"), 0, 0),
                        end=_num(l.get("end"), 0, 0), shape=l.get("shape") if l.get("shape") in SHAPES else "rect",
                        radius=_num(l.get("radius"), 0.12, 0.02, 0.5), border=_num(l.get("border"), 0, 0, 40),
                        bcolor=_col(l.get("bcolor"), "#ffffff"))
            c = l.get("crop") if isinstance(l.get("crop"), list) and len(l.get("crop")) == 4 else [0, 0, 1, 1]
            cw, ch = _num(c[2], 1, 0.02, 1), _num(c[3], 1, 0.02, 1)
            base["crop"] = [_num(c[0], 0, 0, 1 - cw), _num(c[1], 0, 0, 1 - ch), cw, ch]
            if not base["file"]:
                continue
        elif k == "text":
            base.update(text=str(l.get("text") or "")[:160], x=_num(l.get("x"), 540), y=_num(l.get("y"), 500),
                        size=_num(l.get("size"), 70, 12, 300), color=_col(l.get("color")), box=bool(l.get("box")),
                        bcolor=_col(l.get("bcolor"), "#000000"), opacity=_num(l.get("opacity"), 1, 0.05, 1),
                        font=str(l.get("font") or "Arial Black")[:40], start=_num(l.get("start"), 0, 0),
                        end=_num(l.get("end"), 0, 0), anim=l.get("anim") if l.get("anim") in TEXT_ANIMS else "none",
                        stroke=_col(l.get("stroke"), "#000000"), outline=_num(l.get("outline"), 1, 0, 3),
                        shadow=l.get("shadow") is not False)
            if not base["text"].strip():
                continue
        elif k == "captions":
            base.update(x=_num(l.get("x"), 540), y=_num(l.get("y"), 1380), scale=_num(l.get("scale"), 1, 0.3, 3),
                        style=l.get("style") if l.get("style") in CAPTION_STYLES else "tiktok",
                        color=_col(l.get("color"), "") if l.get("color") else "", hi=_col(l.get("hi"), "") if l.get("hi") else "",
                        words=int(_num(l.get("words"), 0, 0, 8)), font=str(l.get("font") or "")[:60],
                        anim=l.get("anim") if l.get("anim") in CAPTION_ANIMS else "auto")
        elif k == "hook":
            base.update(x=_num(l.get("x"), 540), y=_num(l.get("y"), 300), scale=_num(l.get("scale"), 1, 0.3, 3),
                        secs=_num(l.get("secs"), 3.5, 0.5, 15),
                        look=l.get("look") if l.get("look") in HOOK_LOOKS else "box", accent=_col(l.get("accent"), "#ff3d6e"))
        elif k == "third":
            prims = []
            for p in (l.get("prims") or [])[:80]:
                if not isinstance(p, dict) or p.get("t") not in ("rr", "poly", "text"):
                    continue
                q = {"t": p["t"], "color": _col(p.get("color")), "alpha": _num(p.get("alpha"), 1, 0, 1)}
                if p["t"] == "rr":
                    q.update({k2: _num(p.get(k2), 0, -2000, 3000) for k2 in ("x", "y", "w", "h", "r")})
                    q.update(bw=_num(p.get("bw"), 0, 0, 30), bc=_col(p.get("bc"), "#ffffff"))
                elif p["t"] == "poly":
                    q["pts"] = [[_num(a[0], 0), _num(a[1], 0)] for a in (p.get("pts") or [])[:200]
                                if isinstance(a, list) and len(a) == 2]
                    q.update(bw=_num(p.get("bw"), 0, 0, 30), bc=_col(p.get("bc"), "#ffffff"))
                else:
                    q.update(x=_num(p.get("x"), 0), y=_num(p.get("y"), 0), text=str(p.get("text") or "")[:80],
                             size=_num(p.get("size"), 40, 6, 300), font=str(p.get("font") or "Arial Black")[:40],
                             an=int(_num(p.get("an"), 4, 1, 9)), bw=_num(p.get("bw"), 0, 0, 20),
                             bc=_col(p.get("bc"), "#000000"), italic=bool(p.get("italic")),
                             weight=int(_num(p.get("weight"), 700, 100, 900)))
                q["blur"] = _num(p.get("blur"), 0, 0, 30)
                prims.append(q)
            base.update(prims=prims, x=_num(l.get("x"), 60), y=_num(l.get("y"), 1500),
                        scale=_num(l.get("scale"), 1, 0.2, 4), start=_num(l.get("start"), 1, 0),
                        dur=_num(l.get("dur"), 4, 0, 3600), from_end=_num(l.get("from_end"), 0, 0, 60),
                        anim=l.get("anim") if l.get("anim") in THIRD_ANIMS else "slide",
                        template=str(l.get("template") or "")[:20], platform=str(l.get("platform") or "")[:20], title=str(l.get("title") or "")[:60],
                        sub=str(l.get("sub") or "")[:60], accent=_col(l.get("accent"), "#ff3d6e"))
        else:
            continue
        layers.append(base)
    if not any(l["kind"] == "captions" for l in layers):
        layers.append({"kind": "captions", "id": "cap", "name": "Captions", "hidden": False, "x": 540, "y": 1380,
                       "scale": 1.0, "style": "tiktok", "color": "", "hi": "", "words": 0})
    if not any(l["kind"] == "hook" for l in layers):
        layers.append({"kind": "hook", "id": "hook", "name": "Hook", "hidden": False, "x": 540, "y": 300,
                       "scale": 1.0, "secs": 3.5})
    out["layers"] = layers
    return out


def fit_crop(layer, sw, sh):
    """Make a video window's source crop match the window's shape, so nothing gets cut off unexpectedly."""
    cx, cy, cw, ch = layer["crop"]
    a = layer["w"] / layer["h"]
    wpx = cw * sw
    hpx = wpx / a
    if hpx > sh:
        hpx = sh
        wpx = hpx * a
    if wpx > sw:
        wpx = sw
        hpx = wpx / a
    ncw, nch = wpx / sw, hpx / sh
    mx, my = cx + cw / 2, cy + ch / 2
    layer["crop"] = [min(max(0, mx - ncw / 2), 1 - ncw), min(max(0, my - nch / 2), 1 - nch), ncw, nch]
    return layer


def _ass_alpha(opacity):
    return f"&H{int(round(255 * (1 - min(max(opacity, 0), 1)))):02X}&"


def _ass_path(pts):
    if not pts:
        return ""
    p = [f"m {pts[0][0]:.1f} {pts[0][1]:.1f}", "l"]
    p += [f"{x:.1f} {y:.1f}" for x, y in pts[1:]]
    return " ".join(p)


# lower-third entrances: (dx, dy, ms) moves, plus fade and wipe. Every prim moves together, so the card stays in one piece.
THIRD_ANIMS = {"slide": "Slide in", "slider": "From the right", "rise": "Rise up", "drop": "Drop in", "glide": "Slow glide",
               "wipe": "Wipe", "fade": "Fade", "none": "None"}
THIRD_MOVES = {"slide": (-160, 0, 300), "slider": (220, 0, 300), "rise": (0, 70, 300), "drop": (0, -140, 350), "glide": (-420, 0, 900)}

TEXT_ANIMS = {"none": "None", "fade": "Fade in & out", "fadein": "Fade in", "fadeout": "Fade out", "typewriter": "Typewriter",
              "pop": "Pop", "drop": "Drop in", "rise": "Rise up", "slide": "Slide in", "zoom": "Zoom in", "spin": "Spin in",
              "glitch": "Glitch", "bounce": "Bounce", "shake": "Shake", "blink": "Blink"}


def text_events(l, layer, x, y, s0, e0, head, body_raw):
    """ASS lines for one text layer with its animation. head = override tags without the closing brace; body_raw = text."""
    an, D = l.get("anim") or "none", max(0.2, e0 - s0) * 1000
    a0 = _ass_alpha(l["opacity"])
    pos = f"\\pos({x},{y})"
    tags, out = "", []
    if an == "fade":
        tags = "\\fad(250,250)"
    elif an == "fadein":
        tags = "\\fad(350,0)"
    elif an == "fadeout":
        tags = "\\fad(0,350)"
    elif an == "pop":
        tags = "\\fscx30\\fscy30\\t(0,160,\\fscx118\\fscy118)\\t(160,280,\\fscx100\\fscy100)"
    elif an == "drop":
        pos = f"\\move({x},{y - 320},{x},{y},0,320)"; tags = "\\fad(120,0)\\t(320,420,\\fscy88\\fscx108)\\t(420,520,\\fscy100\\fscx100)"
    elif an == "rise":
        pos = f"\\move({x},{y + 220},{x},{y},0,350)"; tags = "\\fad(250,0)"
    elif an == "slide":
        pos = f"\\move({x - 900},{y},{x},{y},0,330)"
    elif an == "zoom":
        tags = f"\\fscx260\\fscy260\\alpha&HFF&\\t(0,320,\\fscx100\\fscy100\\alpha{a0})"
    elif an == "spin":
        tags = "\\frz-200\\fscx15\\fscy15\\t(0,380,\\frz0\\fscx100\\fscy100)"
    elif an == "bounce":
        tags = "".join(f"\\t({t},{t + 160},\\fscx112\\fscy90)\\t({t + 160},{t + 320},\\fscx100\\fscy100)" for t in range(0, int(min(D, 12000)), 700))
    elif an == "shake":
        tags = "".join(f"\\t({t},{t + 50},\\frz{4 if i % 2 else -4})" for i, t in enumerate(range(0, 700, 50))) + "\\t(700,760,\\frz0)"
    elif an == "blink":
        tags = "".join(f"\\t({t},{t + 1},\\alpha&HFF&)\\t({t + 250},{t + 251},\\alpha{a0})" for t in range(500, int(min(D, 12000)), 900))
    elif an == "glitch":
        tags = "\\t(0,60,\\fscx135)\\t(60,120,\\fscx85)\\t(120,180,\\fscx115)\\t(180,240,\\fscx100)"
        g_end = min(e0, s0 + 0.36)
        for dx, col in ((-10, "&H0000FF&"), (10, "&HFFFF00&")):     # red and cyan copies split off for a moment
            out.append(f"Dialogue: {layer - 1},{ts_ass(s0)},{ts_ass(g_end)},Ovl,,0,0,0,,"
                       f"{{\\an5\\pos({x + dx},{y})\\fn{l['_fn']}\\fs{int(l['size'])}\\bord0\\shad0\\1c{col}\\alpha&H60&"
                       f"\\t(0,120,\\fscx140)\\t(120,360,\\fscx100\\alpha&HFF&)}}{body_raw}")
    elif an == "typewriter":
        step = int(max(25, min(70, (D * 0.6) / max(1, len(l["text"])))))
        parts, i, k = [], 0, 0
        body = body_raw
        while i < len(body):
            if body.startswith("\\N", i):
                parts.append("\\N"); i += 2; continue
            t = k * step
            parts.append(f"{{\\alpha&HFF&\\t({t},{t + 1},\\alpha{a0})}}{body[i]}"); i += 1; k += 1
        return out + [f"Dialogue: {layer},{ts_ass(s0)},{ts_ass(e0)},{head}{pos}}}{''.join(parts)}"]
    return out + [f"Dialogue: {layer},{ts_ass(s0)},{ts_ass(e0)},{head}{pos}{tags}}}{body_raw}"]


CAPTION_ANIMS = {"auto": "Style default", "highlight": "Highlight word", "karaoke": "Karaoke fill", "pop": "Pop each word",
                 "build": "Build up", "onebyone": "One word at a time", "popline": "Pop-in line", "full": "Full line", "bounce": "Bounce"}


def _caption_events(words, st, x, y):
    lines = []
    pos = f"\\an5\\pos({int(x)},{int(y)})"
    anim = st.get("anim_override") or st.get("anim") or ""
    mode = {"karaoke": "karaoke", "full": "plain", "popline": "plain"}.get(anim, st["mode"])
    if anim in ("highlight", "pop", "build", "onebyone", "bounce"):
        mode = "highlight"
    size = 1 if anim == "onebyone" else st["words"]
    hol = "\\1a&HFF&" if st.get("hollow") else ""        # hollow letters: only the outline shows, the spoken word fills in
    fil = "\\1a&H00&" if st.get("hollow") else ""
    look = hol
    if st.get("glow"):
        look += f"\\3c{st['glow']}\\blur{max(3, st['outline'])}"
    if st.get("spacing"):
        look += f"\\fsp{st['spacing']}"
    if st.get("boxcol"):
        look += f"\\3c{st['boxcol']}"
    pos += look
    st = {**st, "_fil": fil, "_hol": hol}
    groups = group_words(words or [], size)
    for gi, g in enumerate(groups):
        nxt = groups[gi + 1][0][0] if gi + 1 < len(groups) else 1e9
        toks = [clean_ass(w[2].upper() if st["upper"] else w[2]) for w in g]
        if anim == "popline":
            lines.append(f"Dialogue: 0,{ts_ass(g[0][0])},{ts_ass(min(g[-1][1] + 0.15, nxt))},Cap,,0,0,0,,"
                         f"{{{pos}\\fscx60\\fscy60\\t(0,110,\\fscx112\\fscy112)\\t(110,190,\\fscx100\\fscy100)}}" + " ".join(toks))
            continue
        if anim == "full":
            lines.append(f"Dialogue: 0,{ts_ass(g[0][0])},{ts_ass(min(g[-1][1] + 0.15, nxt))},Cap,,0,0,0,,{{{pos}}}" + " ".join(toks))
            continue
        if anim in ("pop", "build", "onebyone", "bounce"):
            if anim == "bounce":
                fx = "\\frz-6\\t(0,100,\\frz4\\fscx118\\fscy118)\\t(100,200,\\frz0\\fscx106\\fscy106)"
            elif anim == "onebyone":
                fx = "\\fscx55\\fscy55\\t(0,90,\\fscx115\\fscy115)\\t(90,160,\\fscx100\\fscy100)"
            else:
                fx = "\\fscx70\\fscy70\\t(0,90,\\fscx125\\fscy125)\\t(90,170,\\fscx110\\fscy110)"
            for i, (ws, we, _) in enumerate(g):
                start = ws if i else g[0][0]
                end = g[i + 1][0] if i + 1 < len(g) else min(max(we, ws + 0.25) + 0.15, nxt)
                if end <= start:
                    continue
                parts = []
                for j, tok in enumerate(toks):
                    if j == i:
                        parts.append(f"{{\\c{st['hi']}{fil}{fx}}}{tok}{{\\c{st['color']}{hol}\\fscx100\\fscy100\\frz0}}")
                    elif j > i and anim in ("build", "pop"):
                        parts.append(f"{{\\alpha&HFF&}}{tok}{{\\alpha&H00&{hol}}}")      # hidden, so the line never jumps
                    else:
                        parts.append(tok)
                lines.append(f"Dialogue: 0,{ts_ass(start)},{ts_ass(end)},Cap,,0,0,0,,{{{pos}}}" + " ".join(parts))
            continue
        if mode == "plain":
            lines.append(f"Dialogue: 0,{ts_ass(g[0][0])},{ts_ass(min(g[-1][1] + 0.15, nxt))},Cap,,0,0,0,,"
                         f"{{{pos}\\fad(80,80)}}" + " ".join(toks))
        elif mode == "karaoke":
            parts, prev = [], g[0][0]
            for (ws, we, _), tok in zip(g, toks):
                parts.append(f"{{\\kf{max(1, int(round((we - prev) * 100)))}}}{tok} ")
                prev = we
            lines.append(f"Dialogue: 0,{ts_ass(g[0][0])},{ts_ass(min(g[-1][1] + 0.3, nxt))},Cap,,0,0,0,,"
                         f"{{{pos}\\1c{st['color']}\\2c{st['hi']}}}" + "".join(parts).strip())
        else:
            for i, (ws, we, _) in enumerate(g):
                start = ws if i else g[0][0]
                end = g[i + 1][0] if i + 1 < len(g) else max(we, ws + 0.25)
                if end <= start:
                    continue
                parts = []
                for j, tok in enumerate(toks):
                    if j == i:
                        parts.append(f"{{\\c{st['hi']}{st.get('_fil', '')}\\fscx122\\fscy122\\t(0,120,\\fscx108\\fscy108)}}"
                                     f"{tok}{{\\c{st['color']}{st.get('_hol', '')}\\fscx100\\fscy100}}")
                    else:
                        parts.append(tok)
                pop = "\\fscx80\\fscy80\\t(0,90,\\fscx100\\fscy100)" if i == 0 else ""
                lines.append(f"Dialogue: 0,{ts_ass(start)},{ts_ass(end)},Cap,,0,0,0,,{{{pos}{pop}}}" + " ".join(parts))
    return lines


def blurf(p, s):
    return f"\\blur{p['blur'] * s:.1f}" if p.get("blur") else ""


HOOK_LOOKS = {"box": "White box", "impact": "Impact + accent word", "neon": "Neon glow", "banner": "Banner bar",
              "outline": "Big outline", "sticker": "Tilted sticker"}


def _accent_index(words):
    """The word to colour in a hook: the longest 'real' word (not the/and/you...)."""
    stop = {"the", "and", "you", "this", "that", "with", "for", "was", "his", "her", "are", "what", "when", "just", "they"}
    best, bi = -1, 0
    for i, w in enumerate(words):
        k = re.sub(r"[^\w]", "", w.lower())
        sc = len(k) - (5 if k in stop else 0) + (2 if w.isupper() else 0)
        if sc > best:
            best, bi = sc, i
    return bi


def hook_events(l, hook_text):
    """The hook (first seconds) in one of several looks."""
    h = ass_lines(hook_text)
    if not h:
        return []
    s, X, Y, secs = l["scale"], int(l["x"]), int(l["y"]), l["secs"]
    look = l.get("look") or "box"
    acc = hex_to_ass(l.get("accent") or "#ff3d6e")
    fn = deva_font("Arial Black", h)
    t0, t1 = ts_ass(0), ts_ass(secs)
    mg = int(40 + 18 * s)
    if look == "box":
        return [f"Dialogue: 40,{t0},{t1},Hook,,{mg},{mg},0,,{{\\an5\\pos({X},{Y})\\fs{int(62 * s)}\\bord{max(4, int(18 * s))}"
                f"\\fn{fn}\\fad(150,250)}}{h}"]
    raw_lines = hook_text.replace("\r", "").split("\n")
    words_all = [w for ln in raw_lines for w in ln.split()]
    ai = _accent_index(words_all) if words_all else -1

    def colored(col_tag, plain_tag):
        out, k = [], 0
        for ln in raw_lines:
            ws = []
            for w in ln.split():
                ws.append(f"{{{col_tag}}}{clean_ass(w.upper())}{{{plain_tag}}}" if k == ai else clean_ass(w.upper()))
                k += 1
            out.append(" ".join(ws))
        return "\\N".join(x for x in out if x)
    fs = int(84 * s)
    pop = "\\fscx40\\fscy40\\t(0,140,\\fscx112\\fscy112)\\t(140,240,\\fscx100\\fscy100)\\fad(0,250)"
    ev = []
    if look == "impact":
        txt = colored(f"\\1c{acc}", "\\1c&H00FFFFFF&")
        ev.append(f"Dialogue: 40,{t0},{t1},Ovl,,{mg},{mg},0,,{{\\an5\\pos({X},{Y})\\fn{fn}\\fs{fs}\\bord{max(5, int(9 * s))}"
                  f"\\shad{int(5 * s)}\\1c&H00FFFFFF&\\3c&H00000000&{pop}}}{txt}")
        n = max(1, len(raw_lines))           # underline bar under the text, slides out from the middle
        w = int(min(860, 330 * s + 26 * s * max(len(x) for x in raw_lines)))
        y = int(Y + n * fs * 0.55 + 14 * s)
        hh = max(6, int(12 * s))
        ev.append(f"Dialogue: 39,{t0},{t1},Draw,,0,0,0,,{{\\an7\\pos({X - w // 2},{y})\\p1\\bord0\\shad0\\1c{acc}"
                  f"\\fscx0\\t(120,360,\\fscx100)\\fad(0,250)}}m 0 0 l {w} 0 {w} {hh} 0 {hh}{{\\p0}}")
    elif look == "neon":
        txt = colored("\\1c&H00FFFFFF&\\3c" + acc, "\\1c&H00FFFFFF&\\3c&H00FFE52E&")
        for lay, extra in ((39, f"\\bord{int(10 * s)}\\blur{int(12 * s)}"), (40, f"\\bord{max(2, int(3 * s))}\\blur1")):
            ev.append(f"Dialogue: {lay},{t0},{t1},Ovl,,{mg},{mg},0,,{{\\an5\\pos({X},{Y})\\fn{fn}\\fs{fs}{extra}\\shad0"
                      f"\\3c&H00FFE52E&\\fad(200,250)\\t(0,600,\\alpha&H00&)}}{txt}")
    elif look == "banner":
        n = max(1, len(raw_lines))
        bh = int(n * fs * 1.05 + 46 * s)
        ev.append(f"Dialogue: 39,{t0},{t1},Draw,,0,0,0,,{{\\an7\\move(-1080,{Y - bh // 2},0,{Y - bh // 2},0,220)\\p1\\bord0\\shad0"
                  f"\\1c{acc}\\fad(0,250)}}m 0 0 l 1080 0 1080 {bh} 0 {bh}{{\\p0}}")
        txt = colored("\\1c&H0000FFFF&", "\\1c&H00FFFFFF&")
        ev.append(f"Dialogue: 40,{t0},{t1},Ovl,,{mg},{mg},0,,{{\\an5\\move({X + 1080},{Y},{X},{Y},60,280)\\fn{fn}\\fs{fs}"
                  f"\\bord{max(2, int(4 * s))}\\shad0\\3c&H00000000&\\fad(0,250)}}{txt}")
    elif look == "outline":
        txt = colored(f"\\3c{acc}", "\\3c&H00000000&")
        ev.append(f"Dialogue: 40,{t0},{t1},Ovl,,{mg},{mg},0,,{{\\an5\\pos({X},{Y})\\fn{fn}\\fs{int(fs * 1.15)}"
                  f"\\bord{max(6, int(12 * s))}\\shad0\\1c&H00FFFFFF&\\3c&H00000000&{pop}}}{txt}")
    else:   # sticker: tilted white box with black text, slaps on
        txt = colored(f"\\1c{acc}", "\\1c&H00000000&")
        ev.append(f"Dialogue: 40,{t0},{t1},OvlBox,,{mg},{mg},0,,{{\\an5\\pos({X},{Y})\\frz4\\fn{fn}\\fs{int(fs * 0.82)}"
                  f"\\bord{max(8, int(20 * s))}\\shad{int(8 * s)}\\4c&H64000000&\\1c&H00000000&\\3c&H00FFFFFF&"
                  f"\\fscx160\\fscy160\\t(0,120,\\fscx96\\fscy96)\\t(120,200,\\fscx100\\fscy100)\\fad(0,250)}}{txt}")
    return ev


def write_scene_ass(path, words, scene, hook_text, dur=None):
    layers = [l for l in scene["layers"] if not l.get("hidden")]
    cap = next((l for l in layers if l["kind"] == "captions"), None)
    st = dict(CAPTION_STYLES.get((cap or {}).get("style"), CAPTION_STYLES["tiktok"]))
    if cap:
        s = cap["scale"]
        st["size"] = int(st["size"] * s)
        st["outline"] = max(1, round(st["outline"] * s))
        st["shadow"] = round(st["shadow"] * s)
        if cap.get("color"):
            st["color"] = hex_to_ass(cap["color"])
        if cap.get("hi"):
            st["hi"] = hex_to_ass(cap["hi"])
        if cap.get("words"):
            st["words"] = int(cap["words"])
        if cap.get("font"):
            st["font"] = clean_ass(cap["font"])
    st["font"] = deva_font(st["font"], *[w[2] for w in (words or [])])
    border = 3 if st["box"] else 1
    outline_col = "&H40000000" if st["box"] else "&H00000000"
    head = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{st['font']},{st['size']},{st['color']},{st['hi']},{outline_col},&H96000000,1,0,0,0,100,100,0,0,{border},{st['outline']},{st['shadow']},5,30,30,0,1
Style: Hook,Arial Black,62,&H00000000,&H00000000,&H00FFFFFF,&H00000000,1,0,0,0,100,100,0,0,3,18,0,5,40,40,0,1
Style: Ovl,Arial Black,70,&H00FFFFFF,&H00FFFFFF,&H00000000,&H96000000,1,0,0,0,100,100,0,0,1,6,3,5,20,20,0,1
Style: OvlBox,Arial Black,70,&H00FFFFFF,&H00FFFFFF,&H50000000,&H00000000,1,0,0,0,100,100,0,0,3,16,0,5,20,20,0,1
Style: Draw,Arial,20,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1
Style: T3,Arial Black,40,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,1,0,0,0,100,100,0,0,1,0,0,4,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    end_all = 35999
    zl = 10
    for l in layers:
        k = l["kind"]
        if k == "text":
            zl += 1
            s0, e0 = l["start"], (l["end"] or end_all)
            style = "OvlBox" if l["box"] else "Ovl"
            ov = f"\\3c{hex_to_ass(l['bcolor'])}" if l["box"] else f"\\3c{hex_to_ass(l.get('stroke') or '#000000')}"
            fn = clean_ass(deva_font(l.get("font") or "Arial Black", l["text"]))
            bord = max(2, int(l['size'] / 4.5)) if l["box"] else (int(l['size'] / 11 * l.get('outline', 1)) if l.get('outline', 1) > 0 else 0)
            shad = "" if l["box"] else ("\\shad3\\4c&H000000&" if l.get("shadow", True) else "\\shad0")
            an = l.get("anim") or "none"
            if an == "none" and not (l['start'] == 0 and not l['end']):
                l = {**l, "anim": "fade"}; l["_fade"] = 120
            hd = (f"{style},,0,0,0,,{{\\an5\\fn{fn}\\fs{int(l['size'])}\\bord{bord}{shad}"
                    f"\\1c{hex_to_ass(l['color'])}{ov}\\alpha{_ass_alpha(l['opacity'])}")
            if l.get("_fade"):
                lines.append(f"Dialogue: {zl},{ts_ass(s0)},{ts_ass(e0)},{hd}\\pos({int(l['x'])},{int(l['y'])})\\fad(120,120)}}{ass_lines(l['text'])}")
            else:
                zl += 1
                lines += text_events({**l, "_fn": fn}, zl, int(l['x']), int(l['y']), s0, e0, hd, ass_lines(l['text']))
        elif k == "hook" and hook_text:
            lines += hook_events(l, hook_text)
        elif k == "captions":
            lines += _caption_events(words, {**st, "anim_override": "" if l.get("anim") in (None, "", "auto") else l["anim"]},
                                     l["x"], l["y"])
        elif k == "third":
            s, X, Y = l["scale"], l["x"], l["y"]
            s0 = l["start"]
            if l.get("from_end") and dur:          # outro ribbon: the last few seconds, whatever the clip length
                s0 = max(0.0, dur - l["from_end"])
                e0 = dur + 1
            else:
                e0 = (s0 + l["dur"]) if l["dur"] > 0 else end_all
            dx, dy, mms = THIRD_MOVES.get(l["anim"], (0, 0, 300))
            fad = "" if l["anim"] == "none" else ("\\fad(0,250)" if l["anim"] == "wipe" else "\\fad(250,250)")
            if l["anim"] == "wipe":     # reveal left to right across the whole card
                bx, by = [], []
                for p in l["prims"]:
                    if p["t"] == "poly":
                        bx += [a for a, _ in p["pts"]]; by += [b for _, b in p["pts"]]
                    else:
                        bx += [p["x"], p["x"] + p.get("w", p.get("size", 40) * len(p.get("text", "")) * 0.6)]
                        by += [p["y"] - (0 if p["t"] == "rr" else p.get("size", 40)), p["y"] + p.get("h", p.get("size", 40))]
                x1, x2 = int(X + min(bx or [0]) * s) - 20, int(X + max(bx or [0]) * s) + 40
                y1, y2 = int(Y + min(by or [0]) * s) - 40, int(Y + max(by or [0]) * s) + 40
                fad += f"\\clip({x1},{y1},{x1},{y2})\\t(0,450,\\clip({x1},{y1},{x2},{y2}))"
            for p in l["prims"]:
                zl += 1
                if p["t"] in ("rr", "poly"):
                    if p["t"] == "rr":
                        w, h = p["w"] * s, p["h"] * s
                        r = min(p["r"] * s, w / 2, h / 2)
                        pts = shape_points("round", w, h, r / max(min(w, h), 1)) if r > 0.5 else shape_points("rect", w, h)
                        ox, oy = p["x"] * s, p["y"] * s
                        bw = f"\\bord{p['bw'] * s:.1f}\\3c{hex_to_ass(p['bc'])}" if p.get("bw") else "\\bord0"
                    else:
                        pts = [(a * s, b * s) for a, b in p["pts"]]
                        ox = oy = 0
                        bw = f"\\bord{p['bw'] * s:.1f}\\3c{hex_to_ass(p['bc'])}" if p.get("bw") else "\\bord0"
                    px, py = X + ox, Y + oy
                    mv = (f"\\move({px + dx:.0f},{py + dy:.0f},{px:.0f},{py:.0f},0,{mms})" if (dx or dy)
                          else f"\\pos({px:.0f},{py:.0f})")
                    lines.append(f"Dialogue: {zl},{ts_ass(s0)},{ts_ass(e0)},Draw,,0,0,0,,"
                                 f"{{\\an7{mv}\\p1\\shad0{bw}{blurf(p, s)}\\1c{hex_to_ass(p['color'])}\\1a{_ass_alpha(p['alpha'])}{fad}}}"
                                 f"{_ass_path(pts)}{{\\p0}}")
                else:
                    px, py = X + p["x"] * s, Y + p["y"] * s
                    mv = (f"\\move({px + dx:.0f},{py + dy:.0f},{px:.0f},{py:.0f},0,{mms})" if (dx or dy)
                          else f"\\pos({px:.0f},{py:.0f})")
                    bw = f"\\bord{p['bw'] * s:.1f}\\3c{hex_to_ass(p['bc'])}" if p.get("bw") else "\\bord0"
                    lines.append(f"Dialogue: {zl},{ts_ass(s0)},{ts_ass(e0)},T3,,0,0,0,,"
                                 f"{{\\q2\\an{p['an']}{mv}\\fn{clean_ass(deva_font(p['font'], p['text']))}\\fs{p['size'] * s:.0f}\\shad0{bw}"
                                 f"{'' if p.get('weight', 700) >= 600 else chr(92) + 'b0'}{chr(92) + 'i1' if p.get('italic') else ''}{blurf(p, s)}"
                                 f"\\1c{hex_to_ass(p['color'])}\\1a{_ass_alpha(p['alpha'])}{fad}}}{clean_ass(p['text'])}")
    with open(path, "w", encoding="utf-8") as f:
        f.write(head + "\n".join(lines) + "\n")


# ------------------------------------------------------------------ cutting parts out of a clip
def clean_cuts(cuts, start, end):
    """Cut-out ranges (absolute source seconds), sorted, merged and kept inside the trim."""
    out = []
    for c in cuts or []:
        try:
            a, b = float(c[0]), float(c[1])
        except (TypeError, ValueError, IndexError):
            continue
        a, b = max(a, start), min(b, end)
        if b - a >= 0.1:
            out.append([round(a, 2), round(b, 2)])
    out.sort()
    merged = []
    for a, b in out:
        if merged and a <= merged[-1][1] + 0.05:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return merged[:30]


def cut_mapper(start, cuts):
    """f(absolute source time) -> time in the edited clip (parts inside a cut jump to where the cut was)."""
    def f(t):
        out = t - start
        for a, b in cuts:
            if t >= b:
                out -= b - a
            elif t > a:
                out -= t - a
        return max(0.0, out)
    return f


def apply_cuts(raw_path, start, end, cuts, work_dir, name):
    """Source with the cut-out parts removed (one fast encode). -> path of the edited source, starting at 0."""
    keep, t = [], start
    for a, b in cuts:
        if a > t:
            keep.append((t, a))
        t = max(t, b)
    if end > t:
        keep.append((t, end))
    if not keep:
        raise RuntimeError("everything was cut out")
    sel = "+".join(f"between(t,{a:.3f},{b - 0.001:.3f})" for a, b in keep)
    out = os.path.join(work_dir, f"{name}_cut.mp4")
    has_a = has_audio(raw_path)
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", raw_path, "-filter_complex",
           f"[0:v]select='{sel}',setpts=N/FRAME_RATE/TB[v]" + (f";[0:a]aselect='{sel}',asetpts=N/SR/TB[a]" if has_a else ""),
           "-map", "[v]"] + (["-map", "[a]"] if has_a else []) + [
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "14", "-c:a", "aac", "-b:a", "192k", out]
    r = run(cmd, capture_output=True, text=True)
    if r.returncode != 0 or not os.path.isfile(out):
        raise RuntimeError("could not cut the clip: " + (r.stderr or "")[-300:])
    return out


def remap_cuts(scene, words, start, cuts):
    """Move every timed thing (words, text, stickers, effects, sounds, crop keyframes) to the edited timeline.
    Scene times are relative to the clip start; words and keyframes are absolute source times."""
    f = cut_mapper(start, cuts)
    rel = lambda x: f(start + x)
    inside = lambda a, b: any(a >= c0 - 0.01 and b <= c1 + 0.01 for c0, c1 in cuts)
    nw = [(round(f(a), 3), round(f(b), 3), w) for a, b, w in (words or []) if not inside(a, b) and f(b) - f(a) > 0.01]
    sc = json.loads(json.dumps(scene))
    for l in sc.get("layers", []):
        if l.get("kind") in ("text", "image") and "start" in l:
            s0, e0 = l.get("start") or 0, l.get("end") or 0
            l["start"] = round(rel(s0), 3)
            if e0:
                l["end"] = round(max(l["start"] + 0.1, rel(e0)), 3)
        elif l.get("kind") == "third":
            s0, d0 = l.get("start") or 0, l.get("dur") or 0
            l["start"] = round(rel(s0), 3)
            if d0:
                l["dur"] = round(max(0.3, rel(s0 + d0) - l["start"]), 3)
        elif l.get("kind") == "video" and l.get("keys"):
            l["keys"] = [[round(f(k[0]), 3), *k[1:]] for k in l["keys"]]
    for fx in sc.get("fx", []):
        s0 = fx["start"]
        fx["start"] = round(rel(s0), 3)
        fx["dur"] = round(max(0.05, rel(s0 + fx["dur"]) - fx["start"]), 3)
    sc["sfx"] = [{**x, "start": round(rel(x["start"]), 3)} for x in sc.get("sfx", []) if not inside(start + x["start"], start + x["start"])]
    if isinstance(sc.get("bleep"), dict):
        sc["bleep"]["spans"] = [[round(f(a), 3), round(f(b), 3)] for a, b in sc["bleep"].get("spans") or []
                                if not inside(a, b) and f(b) - f(a) > 0.01]
    return sc, nw


def render_scene(raw_path, out_path, scene, words, hook_text, work_dir, start=0.0, end=None, assets_dir=""):
    """Render a free-form scene in ONE encode -> 1080x1920 mp4."""
    sw, sh = probe_size(raw_path)
    sc = clean_scene(scene)
    name = os.path.splitext(os.path.basename(out_path))[0]
    start = max(0.0, float(start or 0))
    if end is not None and float(end) - start < 3:
        end = None
    dur = (float(end) - start) if end is not None else max(0.5, probe_duration(raw_path) - start)
    shifted = [(max(0.0, ws - start), we - start, t) for ws, we, t in (words or [])
               if we > start and (end is None or ws < float(end))]
    tmpfiles = []

    def tmp(ext):
        p = os.path.join(work_dir, f"{name}_{len(tmpfiles)}{ext}")
        tmpfiles.append(p)
        return p

    inputs = []

    def add_input(path):
        inputs.append(["-loop", "1", "-t", f"{dur + 0.5:.2f}", "-i", os.path.abspath(path)])
        return len(inputs)

    bspans = [(max(0.0, a - start), min(dur, b - start)) for a, b in sc["bleep"]["spans"] if b - start > 0 and a - start < dur]
    bexpr = "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in bspans) or "0"
    layers = [l for l in sc["layers"] if not l.get("hidden")]
    vids = [l for l in layers if l["kind"] == "video"]
    bg = sc["bg"]
    if bg["type"] == "image" and not (assets_dir and os.path.isfile(_ap(assets_dir, bg["image"]))):
        bg["type"] = "blur"
    n_split = len(vids) + (1 if bg["type"] == "blur" else 0)
    g = []
    if n_split:
        g.append("[0:v]fps=30,split=%d%s" % (n_split, "".join(f"[s{i}]" for i in range(n_split))))
    si = 0
    k = 1 - bg["dim"]
    dimf = f",colorchannelmixer=rr={k:.2f}:gg={k:.2f}:bb={k:.2f}" if bg["dim"] > 0.01 else ""
    if bg["type"] == "blur":
        b = max(1, int(bg["blur"]))
        g.append(f"[s{si}]scale=270:480:force_original_aspect_ratio=increase,crop=270:480,boxblur={b}:2,"
                 f"scale=1080:1920,setsar=1{dimf},format=yuv420p[base0]")
        si += 1
    elif bg["type"] == "color":
        g.append(f"color=c=0x{bg['color'][1:]}:s=1080x1920:r=30:d={dur:.2f},format=yuv420p[base0]")
    elif bg["type"] == "gradient":
        p = tmp(".ppm")
        write_gradient(p, bg["color"], bg["color2"])
        i = add_input(p)
        g.append(f"[{i}:v]scale=1080:1920:flags=bicubic,fps=30,setsar=1,format=yuv420p[base0]")
    else:
        i = add_input(_ap(assets_dir, bg["image"]))
        g.append(f"[{i}:v]scale=1080:1920:force_original_aspect_ratio=increase:flags=lanczos,crop=1080:1920,"
                 f"fps=30,setsar=1{dimf},format=yuv420p[base0]")
    cur, n = "base0", 0
    for l in layers:
        if l["kind"] == "video":
            n += 1
            fit_crop(l, sw, sh)
            bw_, bh_ = even(l["w"]), even(l["h"])
            x, y = int(round(l["x"])), int(round(l["y"]))
            cblur = ""
            if l["role"] == "cam" and bspans and sc["bleep"]["camblur"]:      # hide the mouth while the word is bleeped
                cblur = f",boxblur=24:3:enable='{bexpr}'"
            cropf = _crop_zoom(l, sw, sh, start, bw_, bh_) if crop_zooms(l) else _crop_keys(l, sw, sh, start)
            g.append(f"[s{si}]{cropf},scale={bw_}:{bh_}:force_original_aspect_ratio=increase:"
                     f"flags=lanczos,crop={bw_}:{bh_}{cblur},setsar=1,format=rgba[v{n}]")
            si += 1
            vlab = f"v{n}"
            if l["shape"] != "rect":
                mp = tmp(".pgm")
                write_mask(mp, shape_points(l["shape"], bw_, bh_, l["radius"]), bw_, bh_)
                mi = add_input(mp)
                g.append(f"[{mi}:v]scale={bw_}:{bh_}:flags=bicubic,format=gray[m{n}];[v{n}][m{n}]alphamerge[va{n}]")
                vlab = f"va{n}"
            bd = int(round(l["border"]))
            if bd > 0:
                ow, oh = even(bw_ + 2 * bd), even(bh_ + 2 * bd)
                mp = tmp(".pgm")
                write_mask(mp, shape_points(l["shape"], ow, oh, l["radius"] * min(bw_, bh_) / max(min(ow, oh), 1)
                                            if l["shape"] == "round" else l["radius"]), ow, oh)
                mi = add_input(mp)
                g.append(f"color=c=0x{l['bcolor'][1:]}:s={ow}x{oh}:r=30:d={dur:.2f},format=rgba[bc{n}];"
                         f"[{mi}:v]scale={ow}:{oh}:flags=bicubic,format=gray[bm{n}];[bc{n}][bm{n}]alphamerge[bb{n}];"
                         f"[{cur}][bb{n}]overlay=x={x - bd}:y={y - bd}[o{n}b]")
                cur = f"o{n}b"
            if l.get("opacity", 1) < 0.999:
                g.append(f"[{vlab}]colorchannelmixer=aa={l['opacity']:.2f}[vo{n}]")
                vlab = f"vo{n}"
            g.append(f"[{cur}][{vlab}]overlay=x={x}:y={y}[o{n}]")
            cur = f"o{n}"
        elif l["kind"] == "image":
            fp = _ap(assets_dir, l["file"])
            if not os.path.isfile(fp):
                continue
            n += 1
            ii = add_input(fp)
            iw = even(l["w"])
            en = ""
            if l["start"] > 0 or l["end"] > 0:
                en = f":enable='between(t,{l['start']:.2f},{(l['end'] or 99999):.2f})'"
            c = l.get("crop") or [0, 0, 1, 1]
            try:
                nw, nh = probe_size(fp)
            except Exception:
                nw, nh = 1000, 1000
            ih = even(max(2, iw * (c[3] * nh) / max(1, c[2] * nw)))
            cf = "" if c == [0, 0, 1, 1] else f"crop=iw*{c[2]:.4f}:ih*{c[3]:.4f}:iw*{c[0]:.4f}:ih*{c[1]:.4f},"
            g.append(f"[{ii}:v]{cf}scale={iw}:{ih}:flags=lanczos,format=rgba[ip{n}]")
            src = f"ip{n}"
            if l.get("shape", "rect") != "rect":       # cut the picture to the shape, keeping its own see-through parts
                mp = tmp(".pgm")
                write_mask(mp, shape_points(l["shape"], iw, ih, l["radius"]), iw, ih)
                mi = add_input(mp)
                g.append(f"[{src}]split[ia{n}][ib{n}];[ib{n}]alphaextract[al{n}];[{mi}:v]format=gray,scale={iw}:{ih}[mk{n}];"
                         f"[al{n}][mk{n}]blend=all_mode=multiply[am{n}];[ia{n}][am{n}]alphamerge[is{n}]")
                src = f"is{n}"
            g.append(f"[{src}]colorchannelmixer=aa={l['opacity']:.2f}[im{n}];"
                     f"[{cur}][im{n}]overlay=x={int(l['x'])}-w/2:y={int(l['y'])}-h/2{en}[o{n}]")
            cur = f"o{n}"
    if bspans:      # bleeped words are starred in the captions too: d**n
        star = lambda w: w if len(w) < 3 else w[0] + "*" * (len(w) - 2) + w[-1]
        shifted = [(a, b, star(t) if any(a < y - 0.02 and b > x + 0.02 for x, y in bspans) else t) for a, b, t in shifted]
    ass = name + "_scene.ass"
    tmpfiles.append(os.path.join(work_dir, ass))
    write_scene_ass(os.path.join(work_dir, ass), shifted, sc, hook_text, dur=dur)
    fxg, cur = fx_filters([f for f in sc["fx"] if f["start"] < dur], cur)      # effects hit the video, not the text
    g += fxg
    g.append(f"[{cur}]{ass_filter(ass, work_dir)}[txt]")
    cur = "txt"
    pb = sc["progress"]
    if pb["on"]:
        ph = even(pb["h"])
        yy = "0" if pb["pos"] == "top" else "H-h"
        g.append(f"color=c=0x{pb['color'][1:]}:s=1080x{ph}:r=30:d={dur:.2f}[bar];"
                 f"[{cur}][bar]overlay=x='-W+W*t/{dur:.2f}':y={yy}:shortest=1[pbo]")
        cur = "pbo"
    if sc["loop"] and dur > 2:      # loop ending: the last half second melts into the first frame, so replays feel seamless
        ls = dur - 0.5
        g.append(f"[{cur}]split[lm][lf];[lf]trim=end_frame=1,loop=loop=-1:size=1,trim=duration={dur:.2f},setpts=N/30/TB[lfirst];"
                 f"[lm][lfirst]blend=all_expr='A*(1-clip((T-{ls:.2f})/0.5,0,1))+B*clip((T-{ls:.2f})/0.5,0,1)'[lp]")
        cur = "lp"
    g.append(f"[{cur}]format=yuv420p,setsar=1[v]")
    # the clip's own sound -> bleeps -> music bed (ducks under the voice, rises at the end) -> sound effects
    amap = "0:a:0?"
    snds = []
    for f in sc["sfx"]:
        if f["start"] < dur and f["vol"] > 0:
            p = sound_path(f["sound"], work_dir)
            if p:
                snds.append((p, f))
    mu = sc["music"]
    mpath = _ap(assets_dir, mu["file"]) if mu["file"] and assets_dir else ""
    if not (mpath and os.path.isfile(mpath)) or (mu["vol"] <= 0 and mu["peak"] <= 0):
        mpath = ""
    audio_inputs = []
    if snds or bspans or mpath or sc["loop"]:
        base_in = len(inputs) + 1
        if has_audio(raw_path):
            g.append("[0:a]aresample=44100,aformat=channel_layouts=stereo[a00]")
        else:
            g.append(f"anullsrc=r=44100:cl=stereo,atrim=0:{dur:.2f}[a00]")
        a0 = "a00"
        if bspans:
            g.append(f"[{a0}]volume=volume='if({bexpr},0,1)':eval=frame[a01]")
            a0 = "a01"
            if sc["bleep"]["mode"] == "beep":
                g.append(f"sine=f=1000:r=44100,atrim=0:{dur:.2f},aformat=channel_layouts=stereo,"
                         f"volume=volume='if({bexpr},0.32,0)':eval=frame[bp];[{a0}][bp]amix=inputs=2:duration=first:normalize=0[a02]")
                a0 = "a02"
        ducks = [(f["start"] - 0.08, f["start"] + f["len"] + 0.15) for _, f in snds if f.get("duck")]
        if ducks:
            dex = "+".join(f"between(t,{x:.2f},{y:.2f})" for x, y in ducks)
            g.append(f"[{a0}]volume=volume='if({dex},0.28,1)':eval=frame[a0d]")
            a0 = "a0d"
        if sc["loop"] and dur > 2:
            g.append(f"[{a0}]afade=t=in:d=0.25,afade=t=out:st={dur - 0.35:.2f}:d=0.35[a03]")
            a0 = "a03"
        labs = []
        k = 0
        if mpath:
            audio_inputs += ["-stream_loop", "-1", "-ss", f"{mu['offset']:.2f}", "-i", os.path.abspath(mpath)]
            r0 = max(0.0, dur - mu["rise"]) if mu["rise"] > 0 else dur
            vexpr = (f"if(lt(t,{r0:.2f}),{mu['vol']:.3f},{mu['vol']:.3f}+({mu['peak']:.3f}-{mu['vol']:.3f})*(t-{r0:.2f})/{max(mu['rise'], 0.01):.2f})"
                     if mu["rise"] > 0 else f"{mu['vol']:.3f}")
            g.append(f"[{base_in}:a]aresample=44100,aformat=channel_layouts=stereo,atrim=0:{dur:.2f},asetpts=N/SR/TB,"
                     f"volume=volume='{vexpr}':eval=frame,afade=t=in:d=0.6,afade=t=out:st={max(0, dur - 0.5):.2f}:d=0.5[mz]")
            if mu["duck"] and has_audio(raw_path):
                g.append(f"[{a0}]asplit[av][sc];[mz][sc]sidechaincompress=threshold=0.04:ratio=5:attack=30:release=400[md]")
                a0, ml = "av", "md"
            else:
                ml = "mz"
            labs.append(f"[{ml}]")
            k = 1
        for j, (p, f) in enumerate(snds):
            audio_inputs += ["-i", os.path.abspath(p)]
            ms = int(f["start"] * 1000)
            g.append(f"[{base_in + k + j}:a]aresample=44100,aformat=channel_layouts=stereo,adelay={ms}|{ms},volume={f['vol']:.2f}[sx{j}]")
            labs.append(f"[sx{j}]")
        g.append(f"[{a0}]{''.join(labs)}amix=inputs={1 + len(labs)}:duration=first:normalize=0,alimiter=limit=0.97[aout]")
        amap = "[aout]"
    tmpout = out_path + ".part.mp4"
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
    if start > 0:
        cmd += ["-ss", f"{start:.2f}"]
    cmd += ["-t", f"{dur:.2f}", "-i", os.path.abspath(raw_path)]
    for a in inputs:
        cmd += a
    cmd += audio_inputs
    cmd += ["-filter_complex", ";".join(g), "-map", "[v]", "-map", amap, "-t", f"{dur:.2f}"] + enc_args() + [
            "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", os.path.abspath(tmpout)]
    r = run_progress(cmd, dur, cwd=work_dir)
    for p in tmpfiles:
        try:
            os.remove(p)
        except OSError:
            pass
    if r.returncode != 0:
        return False, r.stderr[-600:]
    os.replace(tmpout, out_path)
    return True, ""


# ================================================================== meme sounds + effects (no downloads needed)
# Every sound below is synthesised by ffmpeg, so it is ours and safe to post. Users can add their own files too.
def tone(f, a, b, harm=(1, .5, .3), decay=0.0, amp=0.5):
    parts = "+".join(f"{h}*sin({k+1}*2*PI*{f}*(t-{a}))" for k, h in enumerate(harm))
    env = f"exp(-{decay}*(t-{a}))" if decay else "1"
    return f"between(t,{a},{b})*{amp}*({parts})*{env}"
SFX = {
 "boom": ("aevalsrc=exprs='0.95*sin(2*PI*(70*t-14*t*t))*exp(-3*t)+0.45*sin(2*PI*(140*t-28*t*t))*exp(-6*t)+0.25*sin(2*PI*35*t)*exp(-2*t)':d=1.6:s=44100,lowpass=f=1000,volume=1.3,alimiter=limit=0.95", 1.6),
 "explosion": ("anoisesrc=d=2.4:c=brown:a=1:r=44100,lowpass=f=600,volume=5,afade=t=out:st=0.05:d=2.3:curve=exp[n];aevalsrc=exprs='0.9*sin(2*PI*(60*t-12*t*t))*exp(-2.5*t)':d=2.4:s=44100[b];[n][b]amix=inputs=2:normalize=0,alimiter=limit=0.95", 2.4),
 "airhorn": ("aevalsrc=exprs='0.22*(sgn(sin(2*PI*415*t))+sgn(sin(2*PI*523*t))+0.6*sgn(sin(2*PI*622*t)))*(between(t,0,0.18)+between(t,0.24,0.42)+between(t,0.5,1.3))':d=1.35:s=44100,highpass=f=200,lowpass=f=3500,alimiter=limit=0.9", 1.35),
 "ding": ("aevalsrc=exprs='0.55*sin(2*PI*1318*t)*exp(-3*t)+0.3*sin(2*PI*2636*t)*exp(-5*t)+0.2*sin(2*PI*1975*t)*exp(-4*t)':d=1.6:s=44100", 1.6),
 "fail": ("aevalsrc=exprs='" + "+".join([tone(392,0,0.42,decay=1.5), tone(370,0.45,0.87,decay=1.5), tone(349,0.9,1.32,decay=1.5)]) +
          "+between(t,1.35,2.6)*0.5*(sin(2*PI*330*(t-1.35)+4*sin(2*PI*6*t))+0.5*sin(2*2*PI*330*(t-1.35)+8*sin(2*PI*6*t)))*exp(-1.2*(t-1.35))':d=2.6:s=44100,lowpass=f=2500,alimiter=limit=0.9", 2.6),
 "whoosh": ("anoisesrc=d=0.8:c=pink:a=1:r=44100,bandpass=f=900:width_type=h:w=1400,volume=3,afade=t=in:d=0.4:curve=exp,afade=t=out:st=0.4:d=0.4,alimiter=limit=0.9", 0.8),
 "bonk": ("aevalsrc=exprs='0.9*sin(2*PI*(520*t-900*t*t))*exp(-18*t)+0.55*sin(2*PI*180*t)*exp(-12*t)':d=0.5:s=44100", 0.5),
 "riser": ("aevalsrc=exprs='0.45*sin(2*PI*(150*t+220*t*t))*(t/2.5)+0.2*sin(2*PI*(300*t+440*t*t))*(t/2.5)':d=2.5:s=44100,alimiter=limit=0.9", 2.5),
 "glitch": ("aevalsrc=exprs='0.8*sgn(sin(2*PI*(80+60*floor(mod(t*23,4)))*t))*between(mod(t,0.12),0,0.07)':d=0.6:s=44100,lowpass=f=4000", 0.6),
 "tada": ("aevalsrc=exprs='" + "+".join(tone(f, a, 9, decay=3, amp=0.3) for f, a in ((523,0),(659,0.1),(784,0.2),(1046,0.3))) + "':d=1.8:s=44100,alimiter=limit=0.9", 1.8),
 "scratch": ("anoisesrc=d=0.5:c=white:a=1:r=44100,bandpass=f=2200:width_type=h:w=1800,tremolo=f=14:d=0.95,volume=2,afade=t=out:st=0.25:d=0.25,alimiter=limit=0.9", 0.5),
}

def _chirp(a, f0=3200, sweep=14000, length=0.07, amp=0.5):
    """One short bird chirp starting at a."""
    return (f"between(t,{a},{a + length:.3f})*{amp}*sin(2*PI*({f0}*(t-{a})+{sweep}*(t-{a})*(t-{a})))"
            f"*sin(PI*(t-{a})/{length})")


def _voice(f0, fall, a, b, formants, amp=0.5, vib=5.5, harms=14):
    """A rough 'ah'/'oo' voice: harmonics weighted by vowel formants, sliding pitch, vibrato."""
    parts = []
    for k in range(1, harms + 1):
        w = sum(g * math.exp(-((k * f0 - fq) / bw) ** 2) for fq, bw, g in formants) + 0.04 / k
        if w < 0.02:
            continue
        ph = f"2*PI*{k}*({f0}*(t-{a})-{fall / 2}*(t-{a})*(t-{a}))+{k * 0.25:.2f}*sin(2*PI*{vib}*t)"
        parts.append(f"{w:.3f}*sin({ph})")
    env = f"min(1,(t-{a})/0.07)*min(1,({b}-t)/0.35)"
    return f"between(t,{a},{b})*{amp}*({'+'.join(parts)})*{env}"


AH = ((750, 220, 1.0), (1150, 260, 0.6), (2600, 400, 0.15))
OO = ((320, 120, 1.0), (800, 200, 0.45))
SFX.update({
 "birds": ("aevalsrc=exprs='" + "+".join(_chirp(a, f0, sw) for a, f0, sw in
           ((0.05, 3300, 15000), (0.16, 3600, 14000), (0.27, 3300, 15000), (0.75, 4200, -9000), (0.86, 4000, -9000),
            (1.3, 3000, 18000), (1.38, 3200, 18000), (1.46, 3400, 18000), (1.54, 3600, 18000), (2.0, 4300, -12000),
            (2.12, 4100, -12000))) + "':d=2.4:s=44100,aecho=0.8:0.5:60:0.25,volume=2.4,alimiter=limit=0.9", 2.4),
 "crickets": ("aevalsrc=exprs='0.35*sin(2*PI*4600*t)*(0.5+0.5*sgn(sin(2*PI*28*t)))*between(mod(t,0.9),0,0.32)"
              "+0.2*sin(2*PI*4300*t)*(0.5+0.5*sgn(sin(2*PI*31*t)))*between(mod(t+0.45,1.1),0,0.25)':d=2.7:s=44100,"
              "afade=t=out:st=2.2:d=0.5", 2.7),
 "faah": ("anoisesrc=d=0.22:c=white:a=0.6:r=44100,highpass=f=2500,afade=t=in:d=0.05,afade=t=out:st=0.12:d=0.1[f];"
          "aevalsrc=exprs='" + _voice(230, 70, 0.14, 1.9, AH, amp=0.55) + "':d=2.0:s=44100[v];"
          "[f][v]amix=inputs=2:normalize=0,aecho=0.8:0.6:45|90:0.35|0.2,volume=1.6,alimiter=limit=0.95", 2.0),
 "gasp": ("anoisesrc=d=0.5:c=pink:a=1:r=44100,bandpass=f=1600:width_type=h:w=2200,volume=3,"
          "afade=t=in:d=0.3:curve=exp,afade=t=out:st=0.33:d=0.12[g];"
          "aevalsrc=exprs='" + _voice(260, -140, 0.4, 0.95, AH, amp=0.4) + "':d=1.0:s=44100[v];"
          "[g][v]amix=inputs=2:normalize=0,alimiter=limit=0.9", 1.0),
 "boing": ("aevalsrc=exprs='0.7*sin(2*PI*260*t+9*(1-cos(2*PI*11*t))*exp(-2.5*t))*exp(-3*t)':d=1.1:s=44100", 1.1),
 "sad": ("aevalsrc=exprs='" + "+".join(_voice(f, 0, a, b, ((900, 500, 1.0), (2000, 700, 0.4)), amp=0.38, vib=6.5, harms=10)
          for f, a, b in ((440, 0, 0.6), (415, 0.6, 1.2), (392, 1.2, 1.8), (330, 1.8, 3.2))) +
         "':d=3.2:s=44100,lowpass=f=3500,aecho=0.8:0.5:70:0.3,alimiter=limit=0.9", 3.2),
 "heartbeat": ("aevalsrc=exprs='" + "+".join(f"between(t,{a},{a + 0.25})*{g}*sin(2*PI*52*(t-{a}))*exp(-14*(t-{a}))"
               for b0 in (0, 0.8, 1.6, 2.4) for a, g in ((b0, 1.0), (b0 + 0.22, 0.7))) +
               "':d=3.0:s=44100,lowpass=f=300,volume=2.2,alimiter=limit=0.95", 3.0),
 "dundun": ("aevalsrc=exprs='" + "+".join(tone(f, a, b, harm=(1, .7, .5, .35, .25), decay=d, amp=0.32)
            for f, a, b, d in ((147, 0, 0.32, 3), (147, 0.42, 0.74, 3), (139, 0.9, 2.6, 0.9))) +
            "':d=2.6:s=44100,lowpass=f=1400,aecho=0.8:0.5:80:0.3,volume=2.6,alimiter=limit=0.9", 2.6),
 "rimshot": ("aevalsrc=exprs='0.8*sin(2*PI*(210*t-60*t*t))*exp(-16*t)*between(t,0,0.25)"
             "+0.8*sin(2*PI*(160*(t-0.17)-40*(t-0.17)*(t-0.17)))*exp(-16*(t-0.17))*between(t,0.17,0.45)':d=1.6:s=44100[d];"
             "anoisesrc=d=1.25:c=white:a=0.9:r=44100,highpass=f=5500,afade=t=out:st=0:d=1.2:curve=exp,adelay=380|380[c];"
             "[d][c]amix=inputs=2:normalize=0,alimiter=limit=0.9", 1.6),
 "slide": ("aevalsrc=exprs='0.45*sin(2*PI*if(lt(t,0.45),600*t+1333*t*t,540+1800*(t-0.45)-1444*(t-0.45)*(t-0.45)))"
           "*min(1,t/0.03)*min(1,(1.0-t)/0.1)':d=1.0:s=44100", 1.0),
 "ooh": ("aevalsrc=exprs='" + "+".join(_voice(f, -30, 0.0, 1.8, OO, amp=0.2, vib=4 + i * 0.7, harms=8)
         for i, f in enumerate((150, 178, 205, 232, 262, 300))) +
         "':d=1.8:s=44100[v];anoisesrc=d=1.8:c=pink:a=0.25:r=44100,lowpass=f=900,afade=t=in:d=0.4,afade=t=out:st=1.1:d=0.7[n];"
         "[v][n]amix=inputs=2:normalize=0,aecho=0.8:0.6:50|110:0.3|0.2,alimiter=limit=0.9", 1.8),
})


SFX_LABELS = {"boom": "Boom", "explosion": "Explosion", "airhorn": "Air horn", "ding": "Ding", "fail": "Fail trombone",
              "whoosh": "Whoosh", "bonk": "Bonk", "riser": "Suspense riser", "glitch": "Glitch", "tada": "Ta-da",
              "scratch": "Scratch", "birds": "Birds chirping", "crickets": "Crickets (awkward)", "faah": "Faaah",
              "gasp": "Gasp", "boing": "Boing", "sad": "Sad violin", "heartbeat": "Heartbeat", "dundun": "Dun dun duuun",
              "rimshot": "Ba dum tss", "slide": "Slide whistle", "ooh": "Crowd ooh"}
SOUNDS_DIR = ""          # user's own sound files (set by app.py)
SOUND_DIRS = []          # every folder sound effects may be in (set by app.py)
sound_file = None        # app.py's lookup (follows removed duplicates to the kept copy)
asset_path = None        # finds an editor file in the uploads folders (set by app.py)


def _ap(assets_dir, fname):
    if asset_path:
        return asset_path(fname)
    return os.path.join(assets_dir, fname)
SFX_CACHE = ""           # where generated sounds are kept (set by app.py)
FX_TYPES = ("zoom", "shake", "flash", "bw", "red", "glitch", "fry", "blue", "dark")


def make_sfx(name, out_dir):
    """Create (once) and return the wav for a built-in sound."""
    if name not in SFX:
        return ""
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"sfx_{name}.wav")
    if not os.path.isfile(path):
        g, d = SFX[name]
        r = run(["ffmpeg", "-v", "error", "-y", "-filter_complex", g + "[out]", "-map", "[out]", "-t", str(d),
                 "-ac", "2", "-ar", "44100", path], capture_output=True, text=True)
        if r.returncode != 0:
            return ""
    return path


def sound_path(sound, sfx_dir):
    sfx_dir = SFX_CACHE or sfx_dir
    if sound.startswith("file:"):
        if sound_file:
            return sound_file(sound[5:])
        for d in (SOUND_DIRS or [SOUNDS_DIR]):
            p = os.path.join(d, os.path.basename(sound[5:])) if d else ""
            if p and os.path.isfile(p):
                return p
        return ""
    return make_sfx(sound, sfx_dir)


def has_audio(path):
    r = run(["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index", "-of", "csv=p=0", path],
            capture_output=True, text=True)
    return bool(r.stdout.strip())


def fx_filters(fx, cur, n0=0):
    """Visual effects on the finished 1080x1920 frame -> (filter parts, new label)."""
    g = []
    for i, f in enumerate(fx):
        n = n0 + i
        s, e = f["start"], f["start"] + f["dur"]
        en = f"enable='between(t,{s:.2f},{e:.2f})'"
        a = f["amt"]
        t = f["type"]
        if t == "zoom":
            z = 1 + 0.35 * a
            g.append(f"[{cur}]split[fa{n}][fb{n}];[fb{n}]scale={even(1080 * z)}:{even(1920 * z)},crop=1080:1920[fc{n}];"
                     f"[fa{n}][fc{n}]overlay=0:0:{en}[fx{n}]")
        elif t == "shake":
            m = max(4, int(26 * a))
            g.append(f"[{cur}]split[fa{n}][fb{n}];[fb{n}]scale={1080 + 2 * m}:{even(1920 * (1080 + 2 * m) / 1080)},"
                     f"crop=1080:1920:x='(iw-1080)/2+{m}*sin(t*61)':y='(ih-1920)/2+{m}*cos(t*53)'[fc{n}];"
                     f"[fa{n}][fc{n}]overlay=0:0:{en}[fx{n}]")
        elif t == "flash":
            col = f.get("color") or "#ffffff"
            g.append(f"color=c=0x{col[1:]}:s=1080x1920:r=30:d={f['dur']:.2f},format=rgba,"
                     f"colorchannelmixer=aa={min(1.0, 0.4 + 0.6 * a):.2f},fade=t=out:st=0:d={f['dur']:.2f}:alpha=1,"
                     f"setpts=PTS+{s:.2f}/TB[fl{n}];[{cur}][fl{n}]overlay=0:0:eof_action=pass:{en}[fx{n}]")
        elif t == "bw":
            g.append(f"[{cur}]hue=s=0:{en},eq=contrast={1 + 0.3 * a:.2f}:{en}[fx{n}]")
        elif t == "red":
            g.append(f"[{cur}]colorchannelmixer=rr=1.1:gg={1 - 0.5 * a:.2f}:bb={1 - 0.5 * a:.2f}:{en},"
                     f"vignette=angle=PI/3.5:{en}[fx{n}]")
        elif t == "glitch":
            d = max(4, int(18 * a))
            g.append(f"[{cur}]rgbashift=rh=-{d}:bh={d}:gv={d // 3}:{en},noise=alls={int(30 * a)}:allf=t:{en}[fx{n}]")
        elif t == "blue":
            g.append(f"[{cur}]colorchannelmixer=rr={1 - 0.35 * a:.2f}:gg={1 - 0.15 * a:.2f}:bb={1 + 0.15 * a:.2f}:{en},"
                     f"eq=saturation={1 - 0.5 * a:.2f}:brightness={-0.05 * a:.2f}:{en}[fx{n}]")
        elif t == "dark":
            g.append(f"[{cur}]eq=brightness={-0.12 * a:.2f}:contrast={1 + 0.15 * a:.2f}:{en},"
                     f"vignette=angle=PI/{max(2.2, 4.5 - 2 * a):.2f}:{en}[fx{n}]")
        elif t == "fry":
            g.append(f"[{cur}]eq=saturation={1 + 2 * a:.2f}:contrast={1 + 0.6 * a:.2f}:{en},unsharp=7:7:{2.5 * a:.2f}:{en},"
                     f"noise=alls={int(20 * a)}:allf=t:{en}[fx{n}]")
        else:
            continue
        cur = f"fx{n}"
    return g, cur


# ================================================================== campaign brief + genre
def source_info(url, timeout=60):
    """Title, channel, description, tags and category of a YouTube/Twitch video (yt-dlp, no download)."""
    if not url or not re.match(r"https?://", url):
        return {}
    for a in auth_candidates()[:3]:
        try:
            r = run(ytdlp_cmd() + _auth_args(a) + ["-J", "--skip-download", "--no-playlist", "--no-warnings", url],
                    capture_output=True, text=True, timeout=timeout, encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            return {}
        if r.returncode == 0 and r.stdout.strip().startswith("{"):
            j = json.loads(r.stdout)
            return {"title": j.get("title") or "", "channel": j.get("channel") or j.get("uploader") or "",
                    "description": (j.get("description") or "")[:1500], "tags": (j.get("tags") or [])[:20],
                    "categories": j.get("categories") or [], "duration": j.get("duration") or 0,
                    "live": bool(j.get("was_live") or j.get("is_live"))}
        if not any(k in (r.stderr or "") for k in BOT_MARKERS):
            break
    return {}


GENRE_WORDS = {
    "music": r"\b(official (music )?video|lyric(s)? video|music video|song|single|album|feat\.|ft\.|remix|mv\b|audio)\b",
    "trailer": r"\b(trailer|teaser|official clip|first look|sneak peek|coming soon|in theaters|season \d)\b",
    "podcast": r"\b(podcast|episode|ep\.? ?\d+|interview|conversation|talk show|q&a|panel)\b",
    "business": r"\b(business|startup|marketing|entrepreneur|sales|brand|product|saas|finance|investing|course|tutorial|"
                r"how to|tips|webinar|keynote|ted)\b",
    "sports": r"\b(highlights|match|goal|nba|nfl|fifa|premier league|cricket|ufc|boxing|race)\b",
    "stream": r"\b(stream|livestream|twitch|kick|gameplay|gaming|let'?s play|speedrun|vod)\b",
}


def guess_genre(text, categories=(), live=False):
    t = (text or "").lower()
    cats = " ".join(categories or []).lower()
    if "music" in cats:
        return "music"
    if "gaming" in cats or live:
        return "stream"
    if "sports" in cats:
        return "sports"
    for g in ("trailer", "music", "podcast", "business", "sports", "stream"):
        if re.search(GENRE_WORDS[g], t):
            return g
    if any(c in cats for c in ("education", "science", "howto")):
        return "business"
    if "film" in cats or "entertainment" in cats:
        return "vlog"
    return "vlog"


def offline_plan(brief, info=None):
    """Rules we can read from a brief without AI: #hashtags, @mentions, clip length, genre."""
    b = brief or ""
    tags = list(dict.fromkeys(re.findall(r"(?<![\w#])#[A-Za-z0-9_\u0900-\u097F]{2,40}", b)))
    mentions = list(dict.fromkeys(re.findall(r"(?<![\w@])@[A-Za-z0-9_.]{1,29}[A-Za-z0-9_]", b)))
    lo = hi = None
    m = re.search(r"(\d{1,3})\s*(?:-|–|to)\s*(\d{1,3})\s*(?:s\b|sec|second)", b, re.I)
    if m:
        lo, hi = sorted((int(m.group(1)), int(m.group(2))))
    else:
        m = re.search(r"(?:under|max(?:imum)?|less than|up to|no longer than)\s*(\d{1,3})\s*(?:s\b|sec|second)", b, re.I)
        if m:
            hi = int(m.group(1))
        m = re.search(r"(?:at least|min(?:imum)?|longer than|over)\s*(\d{1,3})\s*(?:s\b|sec|second)", b, re.I)
        if m:
            lo = int(m.group(1))
        m = re.search(r"(?:under|max(?:imum)?|up to)\s*(\d)\s*min", b, re.I)
        if m and not hi:
            hi = int(m.group(1)) * 60
    mt = re.search(r"(?:max(?:imum)?|up to|no more than|at most)\s*(\d{1,2})\s*(?:hash)?tags", b, re.I)
    info = info or {}
    genre = guess_genre(b + " " + info.get("title", "") + " " + " ".join(info.get("tags", [])),
                        info.get("categories"), info.get("live"))
    cta = ""
    m = re.search(r"(link in (?:my )?bio|use code \w+|download \w+ now|follow @\w+)", b, re.I)
    if m:
        cta = m.group(1)
    return {"genre": genre, "hashtags": tags[:12], "mentions": mentions[:6], "min_len": lo, "max_len": hi,
            "cta": cta, "focus": "", "ai": False, "max_tags": int(mt.group(1)) if mt else None}


PLAN_PROMPT = """A clipper got this job. Read the campaign brief (may be empty) and the video info, then return the
rules for making short vertical clips (TikTok / Reels / Shorts) from this video.

CAMPAIGN BRIEF:
{brief}

VIDEO: title: {title}
channel: {channel}
category: {cats}
tags: {tags}
description: {desc}

Return ONLY JSON with keys:
"genre": one of stream, music, trailer, podcast, business, sports, vlog (what the VIDEO is),
"focus": one sentence - what kind of moments to clip for this campaign,
"hashtags": hashtags the brief REQUIRES (with #, exactly as written), [] if none,
"mentions": @accounts the brief requires to tag, [] if none,
"cta": a call to action the brief requires in the caption ("" if none),
"min_len": minimum clip seconds the brief requires or null, "max_len": maximum clip seconds or null,
"max_tags": maximum number of hashtags allowed by the brief or null,
"avoid": what the brief forbids ("" if nothing)."""


def plan_from_brief(settings, brief, info=None, genre="auto", log=print):
    """Turn a pasted campaign brief + the video's info into clipping rules (AI if available, else regex)."""
    info = info or {}
    plan = offline_plan(brief, info)
    provider = settings.get("ai_provider") or "builtin"
    key = settings.get("ai_key") or ""
    url = None
    if provider == "ollama":
        url = (settings.get("ollama_url") or "http://127.0.0.1:11434").rstrip("/") + "/v1/chat/completions"
    if provider in AI_PROVIDERS and provider != "claude" and (key or provider == "ollama"):
        try:
            model = pick_model(settings, provider)
            text = _chat_call(provider, key, model, "You turn clipping campaign briefs into strict JSON rules.",
                              PLAN_PROMPT.format(brief=(brief or "(none)")[:2500], title=info.get("title", ""),
                                                 channel=info.get("channel", ""), cats=", ".join(info.get("categories", [])),
                                                 tags=", ".join(info.get("tags", [])[:12]),
                                                 desc=info.get("description", "")[:600]), url)
            j = json.loads(text[text.index("{"): text.rindex("}") + 1])
            if j.get("genre") in GENRES:
                plan["genre"] = j["genre"]
            for k in ("hashtags", "mentions"):
                vals = [str(v).strip() for v in (j.get(k) or []) if str(v).strip()]
                pre = "#" if k == "hashtags" else "@"
                vals = [v if v.startswith(pre) else pre + v for v in vals]
                plan[k] = list(dict.fromkeys(plan[k] + vals))[:12]
            for k in ("min_len", "max_len", "max_tags"):
                try:
                    if j.get(k) not in (None, "", 0):
                        plan[k] = int(float(j[k]))
                except (TypeError, ValueError):
                    pass
            plan["focus"] = str(j.get("focus") or "")[:300]
            plan["cta"] = str(j.get("cta") or plan["cta"])[:120]
            plan["avoid"] = str(j.get("avoid") or "")[:300]
            plan["ai"] = True
        except Exception as e:  # noqa: BLE001
            log(f"could not read the brief with AI ({str(e)[:80]}) - using the simple rules")
    if genre and genre != "auto" and genre in GENRES:
        plan["genre"] = genre
    if plan.get("min_len") and plan.get("max_len") and plan["min_len"] > plan["max_len"]:
        plan["min_len"], plan["max_len"] = plan["max_len"], plan["min_len"]
    return plan


def talk_candidates(video, dur, settings, plan, count, work_dir, log=print, progress=lambda p, m: None, stop=lambda: False,
                    brief=""):
    """Podcasts / business talks: read the whole transcript and let the AI pick complete, self-contained points."""
    provider = settings.get("ai_provider") or "builtin"
    key = settings.get("ai_key") or ""
    url = None
    if provider == "ollama":
        url = (settings.get("ollama_url") or "http://127.0.0.1:11434").rstrip("/") + "/v1/chat/completions"
    if not (provider in AI_PROVIDERS and provider != "claude" and (key or provider == "ollama")):
        return []
    lo, hi = plan.get("min_len") or 20, plan.get("max_len") or 60
    chunk = 600.0
    out = []
    n_chunks = max(1, int(math.ceil(dur / chunk)))
    per = max(1, int(math.ceil(count * 2 / n_chunks)))
    for i in range(n_chunks):
        if stop():
            raise RuntimeError("stopped")
        t0 = i * chunk
        length = min(chunk + 30, dur - t0)
        if length < 15:
            break
        progress(100 * i / n_chunks, f"reading the talk {int(t0 // 60)}-{int((t0 + length) // 60)} min")
        part = os.path.join(work_dir, f"talk_{i}.m4a")
        run(["ffmpeg", "-v", "error", "-y", "-ss", f"{t0:.1f}", "-t", f"{length:.1f}", "-i", video, "-vn", "-ac", "1",
             "-ar", "16000", "-c:a", "aac", "-b:a", "48k", part])
        words = transcribe_auto(part, work_dir, settings, log) or []
        try:
            os.remove(part)
        except OSError:
            pass
        if len(words) < 20:
            continue
        prompt = f"""This is part of {GENRES.get(plan.get('genre'), GENRES['vlog'])[1]} (transcript, seconds from the start of this part).
{('CAMPAIGN BRIEF: ' + brief[:1200]) if brief else ''}
{('FOCUS: ' + plan['focus']) if plan.get('focus') else ''}
{('AVOID: ' + plan['avoid']) if plan.get('avoid') else ''}

TRANSCRIPT:
{_fmt_words(words, every=4)[:9000]}

Pick the {per} best moments for short vertical clips. Each must be a complete, self-contained point with a strong first
line (no "so", "and", "um" at the start) and end at the end of a sentence. Length {lo}-{hi} seconds.
Return ONLY JSON: {{"clips": [{{"start": seconds, "end": seconds, "score": 1-10, "why": "short reason"}}]}}"""
        try:
            text = _chat_call(provider, key, pick_model(settings, provider),
                              SYSTEM_PROMPT, prompt, url)
            j = json.loads(text[text.index("{"): text.rindex("}") + 1])
        except Exception as e:  # noqa: BLE001
            log(f"AI could not pick moments in part {i + 1}: {str(e)[:80]}")
            continue
        for c in (j.get("clips") or [])[:per + 1]:
            try:
                a, b = float(c["start"]), float(c["end"])
            except (KeyError, TypeError, ValueError):
                continue
            if b - a < 6:
                continue
            if a < 0 or a >= length:
                continue
            a, b = max(0.0, t0 + a - 1.0), min(dur, t0 + min(b, length) + 1.0)
            if b - a < 6:
                continue
            sc = c.get("score") or 5
            out.append({"start": a, "end": b, "peak": (a + b) / 2, "signal": float(sc), "msgs": 0})
    out.sort(key=lambda c: -c["signal"])
    keep = []
    for c in out:
        if all(c["end"] <= k["start"] or c["start"] >= k["end"] for k in keep):
            keep.append(c)
        if len(keep) >= count * 2:
            break
    return sorted(keep, key=lambda c: c["start"])


def layout_scene(mode, template=None):
    """Ready-made scenes for non-stream videos: 'fit' = whole video on a solid background, 'fill' = centre crop."""
    if mode == "fill":
        vid = {"kind": "video", "id": "v1", "name": "Video", "role": "custom", "crop": [0.34, 0, 0.32, 1],
               "x": 0, "y": 0, "w": 1080, "h": 1920, "shape": "rect", "radius": 0.12, "border": 0, "bcolor": "#ffffff"}
        cap_y, hook_y = 1420, 330
    else:
        vid = {"kind": "video", "id": "v1", "name": "Video", "role": "custom", "crop": [0, 0, 1, 1],
               "x": 0, "y": 656, "w": 1080, "h": 608, "shape": "rect", "radius": 0.12, "border": 0, "bcolor": "#ffffff"}
        cap_y, hook_y = 1420, 470
    style = (merged_template(template) or {}).get("caption_style", "tiktok") if template else "tiktok"
    # solid black behind the video by default (lighter to render and to preview); blur is one tap away in the editor
    return {"v": 1, "bg": {"type": "color", "color": "#000000", "color2": "#3a1c71", "image": "", "dim": 0.35, "blur": 16},
            "progress": {"on": False, "color": "#ffd93d", "pos": "bottom", "h": 12},
            "layers": [vid, {"kind": "captions", "id": "cap", "name": "Captions", "x": 540, "y": cap_y, "scale": 1.0,
                             "style": style, "color": "", "hi": "", "words": 0, "font": ""},
                       {"kind": "hook", "id": "hook", "name": "Hook", "x": 540, "y": hook_y, "scale": 1.0, "secs": 3.5}]}


# ================================================================== channel watcher (auto-clip lives + new uploads)
# Checks are plain web requests (a page or an RSS feed per channel) - very light, no video is touched until needed.
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36"
YT_BASE = os.environ.get("CHIPPER_YT_WEB", "https://www.youtube.com")
TWITCH_GQL = os.environ.get("CHIPPER_TWITCH_GQL", "https://gql.twitch.tv/gql")


def http_text(url, data=None, headers=None, timeout=20):
    h = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9", "Cookie": "CONSENT=YES+1; SOCS=CAI"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def parse_channel_input(text):
    """-> (platform, key). Accepts YouTube @handle / channel link / UC id, Twitch link or name."""
    t = (text or "").strip()
    m = re.search(r"tiktok\.com/@([\w.]{2,30})", t) or re.fullmatch(r"(?i)(?:tiktok|tt):\s*@?([\w.]{2,30})", t)
    if m:
        return "tiktok", m.group(1).lower()
    m = re.search(r"twitch\.tv/([A-Za-z0-9_]{2,30})", t)
    if m:
        return "twitch", m.group(1).lower()
    m = re.search(r"youtube\.com/channel/(UC[\w-]{22})", t) or re.fullmatch(r"(UC[\w-]{22})", t)
    if m:
        return "youtube", m.group(1)
    m = re.search(r"youtube\.com/(@[\w.\-]{2,60})", t) or re.fullmatch(r"(@[\w.\-]{2,60})", t)
    if m:
        return "youtube", m.group(1)
    m = re.search(r"youtube\.com/(?:c|user)/([\w.\-]{2,60})", t)
    if m:
        return "youtube", "c/" + m.group(1)
    if re.fullmatch(r"twitch:?\s*[A-Za-z0-9_]{2,30}", t, re.I):
        return "twitch", re.sub(r"(?i)twitch:?\s*", "", t).lower()
    if re.fullmatch(r"[A-Za-z0-9_]{3,25}", t):
        return "twitch", t.lower()
    raise ValueError("paste a YouTube channel (@handle or link), a Twitch channel (name or link) or a TikTok link "
                     "(tiktok.com/@name or tiktok:@name)")


def html_unescape(s):
    import html
    return html.unescape(s)


def yt_resolve(key):
    """@handle / c/name / UC id -> {"channel_id", "name"}"""
    if key.startswith("UC") and len(key) == 24:
        url = f"{YT_BASE}/channel/{key}"
    else:
        url = f"{YT_BASE}/{key}"
    page = http_text(url)
    cid = None
    for pat in (r'<link rel="canonical" href="https://www\.youtube\.com/channel/(UC[\w-]{22})"',
                r'"externalId":"(UC[\w-]{22})"', r'<meta itemprop="identifier" content="(UC[\w-]{22})"',
                r'"browseId":"(UC[\w-]{22})"', r'"channelId":"(UC[\w-]{22})"'):
        m = re.search(pat, page)
        if m:
            cid = m.group(1)
            break
    if not cid:
        raise RuntimeError("could not find that YouTube channel")
    m = re.search(r'<meta property="og:title" content="([^"]*)"', page) or re.search(r'"title":"([^"]{1,100})"', page)
    name = html_unescape(m.group(1)) if m else key
    return {"channel_id": cid, "name": name}


def yt_live_now(channel_id):
    """-> (video_id, title) if the channel is live right now (not just scheduled), else (None, "")."""
    page = http_text(f"{YT_BASE}/channel/{channel_id}/live")
    m = re.search(r'<link rel="canonical" href="https://www\.youtube\.com/watch\?v=([\w-]{11})"', page)
    if not m:
        return None, ""
    if re.search(r'"isLiveNow":\s*true', page) or (re.search(r'"isLive":\s*true', page)
                                                    and not re.search(r'"isUpcoming":\s*true', page)):
        t = re.search(r'<meta name="title" content="([^"]*)"', page) or re.search(r'<title>([^<]*)</title>', page)
        return m.group(1), html_unescape(t.group(1)).replace(" - YouTube", "") if t else ""
    return None, ""


def yt_uploads(channel_id):
    """Latest uploads from the channel's public RSS feed -> [(video_id, title, published_iso)] newest first."""
    xml = http_text(f"{YT_BASE}/feeds/videos.xml?channel_id={channel_id}")
    out = []
    for e in re.findall(r"<entry>(.*?)</entry>", xml, re.S):
        vid = re.search(r"<yt:videoId>([\w-]{11})</yt:videoId>", e)
        if not vid:
            continue
        t = re.search(r"<title>(.*?)</title>", e, re.S)
        pub = re.search(r"<published>(.*?)</published>", e)
        out.append((vid.group(1), html_unescape(t.group(1)) if t else "", pub.group(1) if pub else ""))
    return out


def yt_video_facts(video_id):
    """Length and live state of one video from its watch page."""
    page = http_text(f"{YT_BASE}/watch?v={video_id}")
    m = re.search(r'"lengthSeconds":"(\d+)"', page)
    return {"length": int(m.group(1)) if m else 0,
            "live_now": bool(re.search(r'"isLiveNow":\s*true', page)),
            "upcoming": bool(re.search(r'"isUpcoming":\s*true', page)),
            "was_live": bool(re.search(r'"isLiveContent":\s*true', page)),
            "short": bool(re.search(r'"/shorts/' + re.escape(video_id), page))}


TIKTOK_WEB = os.environ.get("CHIPPER_TIKTOK_WEB", "https://www.tiktok.com")


def tiktok_live_now(user):
    """-> (room_id or None, title, nickname). Reads the public live page; TikTokLive's check as a second opinion."""
    page = ""
    try:
        page = http_text(f"{TIKTOK_WEB}/@{user}/live")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise RuntimeError("TikTok account not found") from None
    except Exception:  # noqa: BLE001
        pass
    nick = re.search(r'"nickname":"([^"]{1,60})"', page)
    nick = nick.group(1) if nick else user
    title = re.search(r'"title":"([^"]{0,120})"', page)
    title = title.group(1) if title else ""
    m = re.search(r'"liveRoomUserInfo".{0,4000}?"roomId":"(\d+)".{0,800}?"status":(\d)', page, re.S) or \
        re.search(r'"roomId":"(\d{6,})".{0,1500}?"status":(\d)', page, re.S)
    if m:
        return (m.group(1) if m.group(2) == "2" else None), title, nick
    try:                                       # page didn't say - ask TikTokLive if it is installed
        import asyncio
        from TikTokLive import TikTokLiveClient
        loop = asyncio.new_event_loop()
        try:
            live = loop.run_until_complete(asyncio.wait_for(TikTokLiveClient(unique_id="@" + user).is_live(), 25))
        finally:
            loop.close()
        return ("live" if live else None), title, nick
    except ImportError:
        pass
    except Exception:  # noqa: BLE001
        pass
    if page and '"status":4' in page:
        return None, title, nick
    raise RuntimeError("could not read the TikTok live page (install TikTokLive for a reliable check)")


TWITCH_CLIENT_ID = "kimne78kx3ncx6brgo4mv6wkivb5bp"     # Twitch's public web client id (same as streamlink)


def twitch_live_now(login):
    """-> (stream_id, title, display_name) if live, else (None, "", name)."""
    q = [{"query": 'query{user(login:"%s"){displayName stream{id title type}}}' % login}]
    txt = http_text(TWITCH_GQL, data=json.dumps(q).encode(),
                    headers={"Client-ID": TWITCH_CLIENT_ID, "Content-Type": "application/json"})
    j = json.loads(txt)
    j = j[0] if isinstance(j, list) else j
    u = ((j.get("data") or {}).get("user")) or None
    if u is None:
        raise RuntimeError("Twitch channel not found")
    st = u.get("stream")
    if st and st.get("type") == "live":
        return st.get("id"), st.get("title") or "", u.get("displayName") or login
    return None, "", u.get("displayName") or login


# ================================================================== languages (Nepali and more)
LANGS = {
    "en": ("English", "English"),
    "ne": ("Nepali", "Nepali written in Devanagari script (e.g. यो त कमाल भयो!), natural spoken Nepali, not formal"),
    "ne-rom": ("Nepali (Roman)", "Romanized Nepali - Nepali words written in English letters the way Nepali people "
                                 "text (e.g. 'k cha hajur', 'yo ta kamaal bhayo')"),
    "ne-mix": ("Nepali + English", "Nepali in Devanagari mixed naturally with common English words, the way young "
                                   "Nepali creators write"),
    "hi": ("Hindi", "Hindi in Devanagari script"),
    "hi-rom": ("Hindi (Roman / Hinglish)", "Romanized Hindi (Hinglish) - Hindi words in English letters the way Indians "
                                           "text (e.g. 'bhai ye kya hua', 'kya baat hai')"),
}
DEVA = re.compile(r"[\u0900-\u097F]")
DEVA_FONTS = ("Nirmala UI", "Mangal", "Kokila", "Aparajita", "Utsaah", "Noto Sans Devanagari", "Mukta", "Poppins",
              "Hind", "Kalimati")


def deva_font(font, *texts):
    """Most caption fonts have no Devanagari letters - switch to Windows' Nirmala UI when the text needs it."""
    if any(DEVA.search(str(t or "")) for t in texts) and not any(f.lower() in str(font).lower() for f in DEVA_FONTS):
        return "Nirmala UI"
    return font


def lang_rule(lang):
    if not lang or lang == "en" or lang not in LANGS:
        return ""
    return (f"\nLANGUAGE: write hook, yt_title and caption in {LANGS[lang][1]}. Hashtags: about half in that language "
            f"(Devanagari hashtags like #नेपाली are fine) and half popular English ones (e.g. #nepal #nepali).")


def _ai_json(settings, system, prompt):
    """One JSON answer from the configured AI provider, or None when no AI is set up."""
    provider = settings.get("ai_provider") or "builtin"
    key = settings.get("ai_key") or ""
    url = None
    if provider == "ollama":
        url = (settings.get("ollama_url") or "http://127.0.0.1:11434").rstrip("/") + "/v1/chat/completions"
    if provider not in AI_PROVIDERS or not (key or provider == "ollama"):
        return None
    model = pick_model(settings, provider)
    if provider == "claude":
        body = {"model": model, "max_tokens": 3000, "system": system, "messages": [{"role": "user", "content": prompt}]}
        req = urllib.request.Request("https://api.anthropic.com/v1/messages", data=json.dumps(body).encode(),
                                     headers={"x-api-key": key, "anthropic-version": "2023-06-01",
                                              "content-type": "application/json"})
        with urllib.request.urlopen(req, timeout=90) as r:
            text = json.loads(r.read())["content"][0]["text"]
    else:
        try:
            text = _chat_call(provider, key, model, system, prompt, url)
        except RuntimeError:
            fb = FALLBACK_MODEL.get(provider)
            if not fb or fb == model:
                raise
            text = _chat_call(provider, key, fb, system, prompt, url)
    return json.loads(text[text.index("{"): text.rindex("}") + 1])


def translate_meta(settings, meta, lang, ctx=None):
    """Hook, title, caption and hashtags rewritten for a Nepali (or other) audience - not a word-for-word translation."""
    if lang not in LANGS:
        raise ValueError("unknown language")
    prompt = f"""Rewrite this short-video text for an audience that speaks {LANGS[lang][1]}.
Keep it catchy and natural for social media (not a stiff word-for-word translation). Keep names of people, games,
brands and channels as they are. Same meaning, same hook energy. YouTube title max 70 characters.
Hashtags: 6-8, about half in {LANGS[lang][0]} and half popular English ones{(' and KEEP these exactly: ' + ' '.join(ctx['plan']['hashtags'])) if ctx and (ctx.get('plan') or {}).get('hashtags') else ''}.

hook: {meta.get('hook', '')}
yt_title: {meta.get('yt_title', '')}
caption: {meta.get('caption', '')}
hashtags: {' '.join(meta.get('hashtags', []))}

Return ONLY JSON with keys hook, yt_title, caption, hashtags (list)."""
    j = _ai_json(settings, "You are a native-speaker social media editor. Reply with JSON only.", prompt)
    if not j:
        raise RuntimeError("translating needs an AI provider - add a free Groq key in Settings -> AI & captions")
    tags = j.get("hashtags") or []
    if isinstance(tags, str):
        tags = tags.split()
    out = {"hook": str(j.get("hook") or meta.get("hook", ""))[:120], "yt_title": str(j.get("yt_title") or "")[:100],
           "caption": str(j.get("caption") or "")[:2000],
           "hashtags": ["#" + str(t).lstrip("#").replace(" ", "") for t in tags if str(t).strip()][:15]}
    return tidy_meta(out, "", (ctx or {}).get("plan"))


def fix_words(settings, words, lang, script="", context="", log=print):
    """AI clean-up of Hindi / Nepali captions, word by word so every word keeps its timing.
    Fixes misheard words and spelling (Nepali that came out as Hindi, wrong matras...), keeps English game words in
    English, and writes Roman (Hinglish / Roman Nepali) when script is 'hi-rom' / 'ne-rom'."""
    name = {"hi": "Hindi", "ne": "Nepali"}[lang]
    if script in ("hi-rom", "ne-rom"):
        target = (f"Romanized {name} - {name} words in English letters the way people type on Instagram/TikTok "
                  + ("(e.g. 'bhai ye kya hua', 'kya baat hai')" if lang == "hi" else "(e.g. 'k cha hajur', 'yo ta kamaal bhayo')"))
    else:
        target = (f"correct {name} in Devanagari script" + (" (proper Nepali, not Hindi - e.g. 'के भयो', 'हो र?', 'साथीहरू')"
                                                               if lang == "ne" else " (natural spoken Hindi)"))
    out = []
    W = [list(w) for w in words]
    for i in range(0, len(W), 120):
        chunk = W[i:i + 120]
        src = "\n".join(f"{k + 1}. {w[2]}" for k, w in enumerate(chunk))
        prompt = f"""These {len(chunk)} words are automatic speech recognition of a {name} live stream{(' (' + context[:150] + ')') if context else ''}.
Speech recognition makes mistakes. Rewrite each word as {target}.
- Fix words that were clearly misheard, using the meaning of the whole sentence.
- English words the speaker really says (game names, 'bro', 'chat', 'GG', numbers) stay in English letters.
- Do NOT translate and do NOT add words. One output item per input item, same order, exactly {len(chunk)} items.
- An item may be an empty string to remove a junk word, or two short words if one word was split wrongly.
Return ONLY JSON: {{"words": ["...", "..."]}}

{src}"""
        j = _ai_json(settings, "You correct speech-to-text captions for Hindi and Nepali videos. Reply with JSON only.", prompt)
        got = [str(x).strip() for x in ((j or {}).get("words") or [])]
        if len(got) != len(chunk):
            log(f"caption clean-up: AI returned {len(got)} words for {len(chunk)} - kept the original part")
            out += [tuple(w) for w in chunk]
            continue
        for w, g in zip(chunk, got):
            toks = g.split()
            if not toks:
                continue
            a, b = w[0], w[1]
            step = (b - a) / len(toks)
            out += [(round(a + k * step, 3), round(a + (k + 1) * step, 3), t) for k, t in enumerate(toks)]
    return out


def translate_words(settings, words, lang, log=print):
    """Translate timed captions line by line; each line keeps its timing and its words share it evenly."""
    if not words:
        return words
    if lang not in LANGS:
        raise ValueError("unknown language")
    lines = group_words([tuple(w) for w in words], 6, gap=0.8)
    src = [" ".join(w[2] for w in g) for g in lines]
    out = []
    for i in range(0, len(src), 40):           # 40 lines per AI call keeps prompts small
        chunk = src[i:i + 40]
        prompt = f"""Translate these spoken subtitle lines into {LANGS[lang][1]}. Short, natural, the way people talk.
Keep names as they are. Exactly one output line for each input line, same order, same count ({len(chunk)}).
Return ONLY JSON: {{"lines": ["...", "..."]}}

""" + "\n".join(f"{k + 1}. {t}" for k, t in enumerate(chunk))
        j = _ai_json(settings, "You translate video subtitles. Reply with JSON only.", prompt)
        if not j:
            raise RuntimeError("translating needs an AI provider - add a free Groq key in Settings -> AI & captions")
        got = [str(x) for x in (j.get("lines") or [])]
        if len(got) != len(chunk):
            log(f"translation returned {len(got)} lines for {len(chunk)} - keeping the rest untranslated")
            got = (got + chunk[len(got):])[:len(chunk)]
        out += got
    new = []
    for g, text in zip(lines, out):
        toks = text.split() or [g[0][2]]
        a, b = g[0][0], g[-1][1]
        step = (b - a) / len(toks)
        new += [(round(a + k * step, 3), round(a + (k + 1) * step, 3), t) for k, t in enumerate(toks)]
    return new


def saved_layout_scene(layout):
    """A saved layout ("Carry Minati" etc.): images, windows and crops exactly as saved - the streamer's camera
    and game sit in the same place every stream, so the crops are kept too."""
    sc = json.loads(json.dumps(layout))
    for l in sc.get("layers", []):
        l.pop("keys", None)
    sc["fx"], sc["sfx"] = [], []
    return sc


def style_to_scene(style, template):
    """Apply a saved style (scene) to a new clip: video windows take their crop from the stream's template."""
    sc = json.loads(json.dumps(style))
    t = merged_template(template)
    for l in sc.get("layers", []):
        if l.get("kind") == "video" and l.get("role") in ("cam", "game"):
            l["crop"] = list(t[l["role"]])
    return sc
