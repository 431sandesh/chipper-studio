"""Discover pages: the YouTube gaming feed (who is live, newest and hottest uploads) and campaigns
(my saved campaign briefs + open campaigns from the Content Rewards, Clipster and Vyro boards)."""
import json
import os
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from html.parser import HTMLParser

import engine as E

# Creators shown in the YouTube feed before the owner adds their own (gaming, India + US).
DEFAULT_CHANNELS = ["@CarryIsLive", "@CarryMinati", "@IShowSpeed", "@KaiCenat", "@TechnoGamerzOfficial",
                    "@TotalGaming093", "@Mythpat", "@LiveInsaan", "@GamerFleet", "@BeastBoyShub",
                    "@MrBeastGaming", "@DynamoGaming"]
REFRESH = 300          # seconds between feed refreshes (only while someone looks at the page)
DAY = 86400


def _load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _save(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    for _ in range(10):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.2)


def _iso(ts):
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    except (ValueError, AttributeError):
        return 0


def rss_uploads(channel_id):
    """Latest uploads with views and date from the channel's public feed."""
    xml = E.http_text(f"{E.YT_BASE}/feeds/videos.xml?channel_id={channel_id}", timeout=15)
    out = []
    for e in re.findall(r"<entry>(.*?)</entry>", xml, re.S):
        vid = re.search(r"<yt:videoId>([\w-]{11})</yt:videoId>", e)
        if not vid:
            continue
        t = re.search(r"<title>(.*?)</title>", e, re.S)
        pub = re.search(r"<published>(.*?)</published>", e)
        views = re.search(r'<media:statistics views="(\d+)"', e)
        link = re.search(r'<link rel="alternate" href="([^"]+)"', e)
        out.append({"id": vid.group(1), "title": E.html_unescape(t.group(1)) if t else "",
                    "published": _iso(pub.group(1)) if pub else 0, "views": int(views.group(1)) if views else 0,
                    "short": bool(link and "/shorts/" in link.group(1))})
    return out


class YouTubeFeed:
    def __init__(self, path, log=print):
        self.path, self.log = path, log
        self.data = _load(path, {"extra": [], "removed": [], "ids": {}})
        self.cache = {}            # handle -> {"checked", "name", "live": (id, title) | None, "uploads": [...], "error"}
        self.lock = threading.Lock()
        self.busy = False
        self.last = 0

    def handles(self, watched=()):
        hs = [h for h in DEFAULT_CHANNELS if h.lower() not in {x.lower() for x in self.data["removed"]}]
        for h in list(self.data["extra"]) + [w for w in watched if w]:
            if h.lower() not in {x.lower() for x in hs}:
                hs.append(h)
        return hs

    def add(self, text):
        plat, key = E.parse_channel_input(text)
        if plat != "youtube":
            raise RuntimeError("paste a YouTube channel (@handle or link)")
        r = E.yt_resolve(key)
        with self.lock:
            self.data["ids"][key.lower()] = r
            self.data["removed"] = [x for x in self.data["removed"] if x.lower() != key.lower()]
            if key.lower() not in {x.lower() for x in self.data["extra"] + DEFAULT_CHANNELS}:
                self.data["extra"].append(key)
            _save(self.path, self.data)
            self.cache.pop(key, None)
        self.last = 0
        return r

    def remove(self, key):
        with self.lock:
            self.data["extra"] = [x for x in self.data["extra"] if x.lower() != key.lower()]
            if key.lower() in {x.lower() for x in DEFAULT_CHANNELS}:
                self.data["removed"].append(key)
            _save(self.path, self.data)
            self.cache.pop(key, None)

    def _one(self, h):
        try:
            ident = self.data["ids"].get(h.lower())
            if not ident:
                ident = E.yt_resolve(h)
                with self.lock:
                    self.data["ids"][h.lower()] = ident
            live = E.yt_live_now(ident["channel_id"])
            ups = rss_uploads(ident["channel_id"])
            return h, {"checked": time.time(), "name": ident["name"], "channel_id": ident["channel_id"],
                       "live": list(live) if live[0] else None, "uploads": ups[:8], "error": ""}
        except Exception as e:  # noqa: BLE001
            old = self.cache.get(h) or {}
            return h, {**old, "checked": time.time(), "error": str(e)[:120], "name": old.get("name") or h}

    def refresh(self, watched=()):
        if self.busy:
            return
        self.busy = True
        try:
            with ThreadPoolExecutor(max_workers=6) as pool:
                for h, r in pool.map(self._one, self.handles(watched)):
                    self.cache[h] = r
            with self.lock:
                _save(self.path, self.data)
            self.last = time.time()
        finally:
            self.busy = False

    def get(self, watched=(), force=False):
        if (force or time.time() - self.last > REFRESH) and not self.busy:
            threading.Thread(target=self.refresh, args=(tuple(watched),), daemon=True).start()
        now = time.time()
        live, vids, chans = [], [], []
        for h in self.handles(watched):
            c = self.cache.get(h)
            chans.append({"key": h, "name": (c or {}).get("name") or h, "custom": h not in DEFAULT_CHANNELS,
                          "watched": h in watched, "error": (c or {}).get("error", ""), "ready": bool(c)})
            if not c:
                continue
            if c.get("live"):
                live.append({"id": c["live"][0], "title": c["live"][1], "channel": c["name"], "key": h})
            for u in c.get("uploads") or []:
                if u["short"] or now - u["published"] > 30 * DAY:
                    continue
                age = max(0.25, (now - u["published"]) / DAY)
                vids.append({**u, "channel": c["name"], "key": h, "per_day": int(u["views"] / age),
                             "replay": False})
        return {"live": live, "videos": vids, "channels": chans, "refreshing": self.busy,
                "checked": self.last}


# ------------------------------------------------------------------ campaigns
BOARD = "https://contentrewards.com"
MONEY = re.compile(r"\$\s?([\d,]+(?:\.\d+)?)\s?([KkMm]?)")
PLATFORMS = [   # where clippers find paid campaigns (plain links, no referral codes)
    {"name": "Content Rewards", "url": "https://contentrewards.com", "pays": "Per 1K views or prize pools, paid through Whop"},
    {"name": "Clipster", "url": "https://clipster.com", "pays": "Per 1K views or per post, depending on the brief"},
    {"name": "Vyro", "url": "https://vyro.com/campaigns", "pays": "Per 1K views, brand and creator campaigns (MrBeast and others)"},
    {"name": "Whop", "url": "https://whop.com/discover/", "pays": "Search 'clipping' for creator and brand programs"},
]
GAMING = re.compile(r"\bgta\b|grand theft auto|gaming|\bgame|stream|twitch|kick|\bcs2\b|valorant|fortnite|minecraft|roblox", re.I)


def _money(s):
    out = []
    for n, u in MONEY.findall(s or ""):
        try:
            out.append(float(n.replace(",", "")) * {"": 1, "k": 1e3, "m": 1e6}[u.lower()])
        except ValueError:
            pass
    return out


class _Links(HTMLParser):
    """Campaign links on the board: <a href="/c/campaigns/<uuid>" aria-label="Preview ..."> with the rate inside."""

    def __init__(self):
        super().__init__()
        self.items, self.cur, self.depth = [], None, 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "a" and re.fullmatch(r"/c/campaigns/[0-9a-f-]{36}", (a.get("href") or "").split("?")[0]):
            self.cur = {"path": a["href"].split("?")[0], "label": a.get("aria-label") or "", "text": [], "img": ""}
            self.depth = 1
        elif self.cur is not None:
            if tag == "a":
                self.depth += 1
            if tag == "img" and not self.cur["img"] and (a.get("src") or "").startswith("https://"):
                self.cur["img"] = a["src"]

    def handle_endtag(self, tag):
        if self.cur is not None and tag == "a":
            self.depth -= 1
            if self.depth <= 0:
                self.items.append(self.cur)
                self.cur = None

    def handle_data(self, d):
        if self.cur is not None and d.strip():
            self.cur["text"].append(d.strip())


class _Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.texts, self.imgs, self.skip = [], [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.skip += 1
        if tag == "img":
            src = dict(attrs).get("src") or ""
            if src.startswith("https://") and "/campaigns/" in src:
                self.imgs.append(src)

    def handle_endtag(self, tag):
        if tag in ("script", "style") and self.skip:
            self.skip -= 1

    def handle_data(self, d):
        if not self.skip and d.strip():
            self.texts.append(d.strip())


def contentrewards_campaigns(limit=12):
    """Open campaigns from the public Content Rewards board (best effort - the site can change)."""
    p = _Links()
    p.feed(E.http_text(BOARD + "/c/discover", timeout=15))
    rows, seen = [], set()
    for it in p.items:
        if it["path"] in seen:
            continue
        seen.add(it["path"])
        title = it["label"][8:] if it["label"].startswith("Preview ") else (it["text"][0] if it["text"] else "")
        tail = it["text"][-2:] if len(it["text"]) >= 2 else it["text"]
        kind, amount = "", 0
        if tail and tail[-1].lower().endswith("pool"):
            kind, amount = "pool", (_money(tail[-1]) or [0])[0]
        elif tail and tail[-1] == "/1K":
            kind, amount = "rate", max(_money(" ".join(tail)) or [0])
        if not title or not kind:
            continue
        rows.append({"title": title[:160], "url": BOARD + it["path"], "kind": kind, "amount": amount,
                     "poster": it["img"], "gaming": bool(GAMING.search(title)), "organizer": "", "brief": "",
                     "source": "Content Rewards"})
    rows.sort(key=lambda r: (r["gaming"], r["amount"] if r["kind"] == "rate" else r["amount"] / 2000), reverse=True)
    rows = rows[:limit]

    def details(r):
        try:
            pg = _Page()
            pg.feed(E.http_text(r["url"], timeout=12))
            t = pg.texts
            if r["title"] in t:
                i = t.index(r["title"])
                r["organizer"] = t[i - 1][:80] if i else ""
                end = t.index("Reference materials", i) if "Reference materials" in t[i:] else i + 80
                r["brief"] = "\n".join(t[i + 1:end])[:2500]
                r["open"] = "Accepting clips" in t[i:end]
            if not r["poster"] and pg.imgs:
                r["poster"] = pg.imgs[0]
        except Exception:  # noqa: BLE001
            pass
        return r
    with ThreadPoolExecutor(max_workers=6) as pool:
        rows = list(pool.map(details, rows))
    return [r for r in rows if r.get("open", True)]


def _num(v, d=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return d


def _plat(p):
    return str(p or "").replace("SOCIAL_MEDIA_PLATFORM_", "").replace("_", " ").title().replace("Tiktok", "TikTok").replace("Youtube", "YouTube")


def clipster_campaigns(limit=20):
    """Active public campaigns from Clipster's own campaign list (the same one clipster.com/discover shows)."""
    j = json.loads(E.http_text("https://api.clipster.com/campaigns/public?limit=60&include_discord_exclusive=false"
                               "&status=CAMPAIGN_STATUS_ACTIVE&page=1&sort=-is_popular%2C-rate_per_one_thousand_views",
                               headers={"Accept": "application/json", "Origin": "https://www.clipster.com"}, timeout=15))
    rows = []
    for c in (j.get("data") or {}).get("campaigns") or []:
        if c.get("status") != "CAMPAIGN_STATUS_ACTIVE" or c.get("is_discord_exclusive"):
            continue
        name = str(c.get("name") or "").strip()
        pay = c.get("pay_summary") or {}
        rate = _num(pay.get("max_rate_per_one_thousand_views") or c.get("rate_per_one_thousand_views"))
        per_post = _num(pay.get("max_base_pay_per_post"))
        if not name or not rate:       # only campaigns that pay for views (that's what clips earn)
            continue
        niches = [str(x) for x in ((c.get("niche_data") or {}).get("niches") or [])]
        cat = str(c.get("category") or "")
        music = "MUSIC" in cat or c.get("require_sound_usage")
        lines = [("Do: " if g.get("kind") == "DO" else "Don't: ") + str(g.get("content") or "").strip()
                 for g in c.get("guidelines") or [] if g.get("content")]
        plats = ", ".join(_plat(p) for p in c.get("allowed_platforms") or [])
        if plats:
            lines.append("Platforms: " + plats)
        tags = (c.get("caption_requirements") or {}).get("required_tags") or []
        if tags:
            lines.append("Required tags: " + " ".join(str(t) for t in tags))
        if c.get("minimum_views_for_earnings"):
            lines.append(f"Paid after {int(c['minimum_views_for_earnings']):,} views")
        for snd in (c.get("sounds") or [])[:1]:
            if snd.get("sound_name"):
                lines.append("Use the sound: " + str(snd["sound_name"]))
        if per_post:
            lines.append(f"Pays ${per_post:g} per post" + (f" + ${rate:g} per 1K views" if rate else ""))
        if c.get("max_earnings"):
            lines.append(f"Budget: ${_num(c['max_earnings']):,.0f}")
        rows.append({"title": name[:160], "url": "https://www.clipster.com/discover?openedCampaignId=" + str(c.get("id") or ""),
                     "kind": "rate", "amount": rate, "poster": c.get("thumbnail_url") or "",
                     "gaming": "GAMING" in niches or "STREAMER" in niches or "GAMING" in cat or bool(GAMING.search(name)),
                     "music": bool(music), "organizer": "", "brief": "\n".join(lines)[:2500], "source": "Clipster"})
    rows.sort(key=lambda r: (r["gaming"], not r["music"], r["amount"]), reverse=True)
    return rows[:limit]


def _next_data(html, key):
    """Find a JSON value inside a Next.js page's streamed data (self.__next_f.push)."""
    flight = "".join(json.loads(m) for m in re.findall(r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)', html))
    i = flight.find(key)
    if i < 0:
        return None
    val, _ = json.JSONDecoder().raw_decode(flight, i + len(key))
    return val


def vyro_campaigns(limit=15):
    """Public campaigns on vyro.com/campaigns (read from the data the page is built with)."""
    data = _next_data(E.http_text("https://vyro.com/campaigns", timeout=15), '"initialPage":{"data":') or []
    rows = []
    for c in data:
        if not isinstance(c, dict) or c.get("isLocked") or (c.get("status") or {}).get("id") not in (None, "active"):
            continue
        name = str(c.get("name") or "").strip()
        pm, bud, req = c.get("payoutModel") or {}, c.get("budget") or {}, c.get("requirements") or {}
        rate = _num(c.get("headlineRate") or pm.get("payRate"))
        left = _num(bud.get("remaining"), -1)
        if not name or left == 0:
            continue
        lines = []
        tg = c.get("targeting") or {}
        if tg.get("platforms"):
            lines.append("Platforms: " + ", ".join(_plat(p) for p in tg["platforms"]))
        if tg.get("languages"):
            lines.append("Language: " + ", ".join(str(x).upper() for x in tg["languages"]))
        txt = req.get("text") or {}
        tags = [str(t) for t in (txt.get("hashtags") or []) + (txt.get("ftcDisclosureHashtags") or [])]
        if tags:
            lines.append("Required hashtags: " + " ".join(tags))
        vid = req.get("video") or {}
        if vid.get("minLength") or vid.get("maxLength"):
            lines.append("Clip length: " + (f"at least {vid['minLength']}s" if vid.get("minLength") else "") +
                         (f", at most {vid['maxLength']}s" if vid.get("maxLength") else ""))
        if (req.get("subtitles") or {}).get("required"):
            lines.append("Subtitles required")
        if req.get("minViewsPerClip"):
            lines.append(f"Paid after {int(req['minViewsPerClip']):,} views per clip")
        links = [str(x.get("url")) for x in (c.get("media") or {}).get("links") or [] if x.get("url")]
        if links:
            lines.append("Footage: " + " ".join(links[:3]))
        if bud.get("value"):
            lines.append(f"Budget: ${_num(bud['value']):,.0f}" + (f" (${left:,.0f} left)" if left > 0 else ""))
        if c.get("endDate"):
            lines.append("Ends: " + str(c["endDate"])[:10])
        org = str((c.get("workspace") or {}).get("name") or "")
        rows.append({"title": name[:160], "url": "https://vyro.com/campaigns/" + str(c.get("slug") or ""),
                     "kind": "rate", "amount": rate, "poster": (c.get("workspace") or {}).get("image") or "",
                     "gaming": bool(GAMING.search(name + " " + org)), "music": False,
                     "organizer": org[:80], "brief": "\n".join(lines)[:2500], "source": "Vyro",
                     "left": round(left) if left > 0 else None})
    return rows[:limit]


SOURCES = [("Content Rewards", contentrewards_campaigns), ("Clipster", clipster_campaigns), ("Vyro", vyro_campaigns)]


def board_campaigns():
    """Open campaigns from every board at once. A board that fails is skipped; the error says which."""
    rows, bad = [], []
    with ThreadPoolExecutor(max_workers=3) as pool:
        futs = [(name, pool.submit(fn)) for name, fn in SOURCES]
        for name, f in futs:
            try:
                rows += f.result(timeout=60)
            except Exception as e:  # noqa: BLE001
                bad.append(f"{name} ({str(e)[:60]})")
    if bad and not rows:
        raise RuntimeError("; ".join(bad))
    for r in rows:      # the phone shows "organizer": say where it's from and what's left
        bits = [r.get("organizer") or "", r["source"]]
        if r.get("left"):
            bits.append(f"${r['left']:,} left")
        r["organizer"] = " · ".join(b for b in bits if b)
    # gaming and stream clipping first, music-edit campaigns last, then the best pay
    rows.sort(key=lambda r: (r["gaming"], not r.get("music"), r["amount"] if r["kind"] == "rate" else r["amount"] / 2000), reverse=True)
    return rows, bad


class Campaigns:
    def __init__(self, path):
        self.path = path
        self.lock = threading.Lock()
        self.board = {"at": 0, "rows": [], "error": "", "busy": False}

    def mine(self):
        return _load(self.path, [])

    def save(self, c):
        name = re.sub(r"\s+", " ", str(c.get("name") or "")).strip()[:80]
        if not name:
            raise RuntimeError("give the campaign a name")
        row = {"id": str(c.get("id") or "")[:40] or uuid.uuid4().hex[:10], "name": name,
               "url": str(c.get("url") or "").strip()[:400] if str(c.get("url") or "").startswith("https://") else "",
               "rate": str(c.get("rate") or "").strip()[:60], "brief": str(c.get("brief") or "").strip()[:4000],
               "length": str(c.get("length") or "")[:10] if re.fullmatch(r"(\d+-\d+)?", str(c.get("length") or "")) else "",
               "ends": str(c.get("ends") or "")[:10], "updated": time.time()}
        with self.lock:
            rows = [r for r in self.mine() if r["id"] != row["id"]]
            rows.insert(0, row)
            _save(self.path, rows[:200])
        return row

    def delete(self, cid):
        with self.lock:
            _save(self.path, [r for r in self.mine() if r["id"] != cid])

    def board_rows(self, force=False):
        b = self.board
        if (force or time.time() - b["at"] > 1800) and not b["busy"]:
            def run():
                b["busy"] = True
                try:
                    b["rows"], bad = board_campaigns()
                    b["error"] = ("Couldn't read " + ", ".join(bad)) if bad else ""
                except Exception as e:  # noqa: BLE001
                    b["error"] = f"could not read the campaign board ({str(e)[:80]})"
                finally:
                    b["at"], b["busy"] = time.time(), False
            threading.Thread(target=run, daemon=True).start()
        return {"rows": b["rows"], "error": b["error"], "busy": b["busy"], "at": b["at"], "platforms": PLATFORMS}
