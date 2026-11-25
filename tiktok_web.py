"""TikTok posting through the TikTok website, in a real browser you logged into yourself (no API).

How it works
- "Log in to TikTok" opens a normal browser window on this PC with its own profile folder (tiktok/<id>). You log in
  there yourself (QR code, password, whatever TikTok shows). Chipper only waits until you are logged in.
- When you press Post in Chipper, it opens the same profile on tiktok.com/tiktokstudio/upload, attaches the clip,
  writes the caption and presses TikTok's own Post button - the same steps you would do by hand.

Safety
- Uploads only happen when you press Post. At most MAX_PER_DAY posts per account per day and at least MIN_GAP_MIN
  minutes between two posts on the same account.
- Nothing tries to hide automation or get around TikTok's checks. If TikTok shows a captcha / security check or logs
  you out, Chipper stops and asks you to finish it yourself in the browser window.
"""
import json
import os
import re
import shutil
import threading
import random
import time

SITE = os.environ.get("CHIPPER_TIKTOK_SITE", "https://www.tiktok.com")
UPLOAD_URL = SITE + "/tiktokstudio/upload?from=upload"
MAX_PER_DAY = 10
MIN_GAP_MIN = 10
BROWSER_LOCK = threading.Lock()          # guards the per-account lock table
_LOCKS = {}


def profile_lock(profile):
    """One browser per TikTok account (Chrome can't share a profile), different accounts in parallel."""
    with BROWSER_LOCK:
        return _LOCKS.setdefault(os.path.normcase(os.path.abspath(profile)), threading.Lock())


class TikTokError(Exception):
    pass


# Several ways to find each part of the page, because TikTok renames things now and then.
SEL = {
    "file": ['input[type="file"][accept*="video"]', 'input[type="file"]'],
    "caption": ['div[contenteditable="true"][role="combobox"]', 'div.public-DraftEditor-content[contenteditable="true"]',
                'div[contenteditable="true"]'],
    "post": ['button[data-e2e="post_video_button"]', 'button:has-text("Post"):not(:has-text("Post now"))',
             'div[role="button"]:has-text("Post")'],
    "captcha": ['#captcha-verify-container', '#tiktok-verify-ele', 'div[class*="captcha_verify"]',
                'div[class*="secsdk-captcha"]', 'text=/Verify to continue|Drag the slider|Select 2 objects/i'],
    "done": ['text=/Your video (has been|is being) (uploaded|posted)|Video published|Manage your posts|Uploaded to TikTok/i'],
    "post_now": ['button:has-text("Post now")'],
    "dismiss": ['button:has-text("Got it")', 'button:has-text("Not now")', 'button:has-text("Cancel")'],
}


def _first(page, keys, timeout=0):
    """First visible element for any of the selectors (waits up to timeout seconds)."""
    end = time.time() + timeout
    while True:
        for s in SEL[keys] if isinstance(keys, str) else keys:
            try:
                loc = page.locator(s).first
                if loc.count() and (keys == "file" or loc.is_visible()):
                    return loc
            except Exception:  # noqa: BLE001
                pass
        if time.time() >= end:
            return None
        time.sleep(0.5)


# ---------------------------------------------------------------- human pace
SPEED = float(os.environ.get("CHIPPER_TIKTOK_SPEED", "1"))   # 1 = normal person, 2 = twice as slow, 0 = tests


def _pause(lo, hi):
    """Wait a random, person-like amount of time."""
    if SPEED > 0:
        time.sleep(random.uniform(lo, hi) * SPEED)


def _human_click(page, loc):
    """Move the mouse to a random spot on the element in small steps, hesitate, then click."""
    try:
        b = loc.bounding_box()
    except Exception:  # noqa: BLE001
        b = None
    if b and SPEED > 0:
        x = b["x"] + b["width"] * random.uniform(0.3, 0.7)
        y = b["y"] + b["height"] * random.uniform(0.3, 0.7)
        try:
            page.mouse.move(x + random.uniform(-180, 180), y + random.uniform(-120, 120))
            _pause(0.1, 0.3)
            page.mouse.move(x, y, steps=random.randint(12, 28))
            _pause(0.2, 0.6)
            page.mouse.click(x, y, delay=random.randint(60, 140))
            return
        except Exception:  # noqa: BLE001
            pass
    loc.click()


def _human_type(page, text):
    """Type like a person: uneven key speed, short breaks between words, longer ones after sentences."""
    fast = len(text) > 600                       # long captions: a bit quicker so it doesn't take forever
    for part in re.split(r"(\s+)", text):
        if not part:
            continue
        if part.isspace():
            page.keyboard.type(part, delay=0)
            _pause(0.05, 0.25 if fast else 0.45)
            continue
        for ch in part:
            page.keyboard.type(ch, delay=0)
            if SPEED > 0:
                time.sleep(random.uniform(0.03, 0.09) if fast else random.uniform(0.06, 0.19))
        if part.startswith("#") or part.startswith("@"):
            _pause(0.8, 1.6)                         # let the suggestion list show/close, like a person reading it
        elif part[-1] in ".!?\u0964":
            _pause(0.5, 1.4)
        if random.random() < 0.06:
            _pause(1.0, 2.5)                         # the odd "thinking" break


class TikTokWeb:
    def __init__(self, root, log=print):
        self.root = root
        self.log = log
        os.makedirs(root, exist_ok=True)
        self.file = os.path.join(root, "accounts.json")
        self.login_state = {"state": "idle", "msg": ""}
        self.windows = {}       # aid -> {"front": bool} for "Open browser" windows still open
        self.window_msg = {}    # aid -> last problem opening that window
        try:
            with open(self.file, encoding="utf-8") as f:
                self.accounts = json.load(f)
        except (OSError, ValueError):
            self.accounts = []

    def save(self):
        with open(self.file, "w", encoding="utf-8") as f:
            json.dump(self.accounts, f, indent=1)

    def public(self):
        return [{"id": a["id"], "username": a.get("username", ""), "name": a.get("name", ""),
                 "posts_today": len([t for t in a.get("posts", []) if t > time.time() - 86400]),
                 "window": a["id"] in self.windows, "window_msg": self.window_msg.get(a["id"], "")}
                for a in self.accounts]

    def account(self, aid):
        for a in self.accounts:
            if a["id"] == aid:
                return a
        raise TikTokError("TikTok account not found - log in again in Settings -> Social accounts")

    # ------------------------------------------------------------------ browser
    def _open(self, profile, headless=False):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise TikTokError("TikTok posting needs Playwright: run install.bat (or: pip install playwright)") from None
        pw = sync_playwright().start()
        last = None
        for sandbox in (True, False):                     # Chrome's security sandbox on whenever possible
            for channel in ("chrome", "msedge", None):    # your installed Chrome/Edge first, else Playwright's own
                try:
                    kw = {"channel": channel} if channel else {}
                    ctx = pw.chromium.launch_persistent_context(profile, headless=headless, viewport=None,
                                                                chromium_sandbox=sandbox,
                                                                args=["--window-size=1200,900"], **kw)
                    return pw, ctx
                except Exception as e:  # noqa: BLE001
                    last = e
        pw.stop()
        raise TikTokError(f"could not open a browser ({str(last)[:120]}) - run install.bat once")

    @staticmethod
    def _logged_in(ctx):
        try:
            return any(c["name"] == "sessionid" and c["value"] for c in ctx.cookies(SITE))
        except Exception:  # noqa: BLE001
            return False

    # ------------------------------------------------------------------ login
    def start_login(self):
        if self.login_state["state"] == "waiting":
            raise TikTokError("a TikTok login window is already open on the PC")
        aid = os.urandom(4).hex()
        self.login_state = {"state": "waiting", "msg": "A browser opened on the PC - log in to TikTok there"}
        threading.Thread(target=self._login, args=(aid,), daemon=True).start()
        return aid

    def _login(self, aid):
        profile = os.path.join(self.root, aid)
        pw = ctx = None
        try:
            with profile_lock(profile):
                pw, ctx = self._open(profile)
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                page.goto(SITE + "/login", wait_until="domcontentloaded")
                end = time.time() + 600
                while time.time() < end and not self._logged_in(ctx):
                    if not ctx.pages:
                        raise TikTokError("the login window was closed before logging in")
                    time.sleep(2)
                if not self._logged_in(ctx):
                    raise TikTokError("not logged in after 10 minutes - try again")
                self._wait_human(page, ctx, "Logged in - finish TikTok's security check in the browser window")
                user = ""
                try:
                    page.goto(SITE + "/profile", wait_until="domcontentloaded", timeout=30000)
                    time.sleep(3)
                    self._wait_human(page, ctx, "Finish TikTok's security check in the browser window")
                    m = re.search(r"/@([\w.]+)", page.url) or \
                        re.search(r'"uniqueId":"([\w.]+)"', page.content())
                    user = m.group(1) if m else ""
                except Exception:  # noqa: BLE001
                    pass
            self.accounts.append({"id": aid, "username": user, "name": ("@" + user) if user else "TikTok account",
                                  "profile": aid, "added": time.time(), "posts": []})
            self.save()
            self.login_state = {"state": "done", "msg": f"Connected {('@' + user) if user else 'TikTok'}"}
            self.log(f"TikTok connected: {('@' + user) if user else aid}")
        except Exception as e:  # noqa: BLE001
            self.login_state = {"state": "error", "msg": str(e)[:200]}
            shutil.rmtree(profile, ignore_errors=True)
        finally:
            for x in (ctx, pw):
                try:
                    x and (x.close() if x is ctx else x.stop())
                except Exception:  # noqa: BLE001
                    pass

    def _wait_human(self, page, ctx, msg, limit=600):
        """While TikTok shows a security check, keep the window open and wait for you to solve it."""
        end = time.time() + limit
        clear = 0
        while time.time() < end and ctx.pages:
            if _first(page, "captcha"):
                self.login_state = {"state": "waiting", "msg": msg}
                clear = 0
            else:
                clear += 1
                if clear >= 3:                  # no check visible for ~6 s: done
                    return
            time.sleep(2)

    def remove(self, aid):
        a = self.account(aid)
        self.accounts = [x for x in self.accounts if x["id"] != aid]
        self.save()
        shutil.rmtree(os.path.join(self.root, a.get("profile", aid)), ignore_errors=True)

    # ------------------------------------------------------------------ upload
    def check_limits(self, a):
        now = time.time()
        posts = [t for t in a.get("posts", []) if t > now - 86400]
        if len(posts) >= MAX_PER_DAY:
            raise TikTokError(f"{a['name']} already posted {MAX_PER_DAY} times in the last 24 h - "
                              "Chipper stops there to keep the account safe")
        if posts and now - max(posts) < MIN_GAP_MIN * 60:
            wait = int((MIN_GAP_MIN * 60 - (now - max(posts))) // 60) + 1
            raise TikTokError(f"wait {wait} more min - Chipper keeps {MIN_GAP_MIN} min between TikTok posts")

    def upload(self, aid, video, caption, progress=lambda s: None):
        a = self.account(aid)
        self.check_limits(a)
        profile = os.path.join(self.root, a.get("profile", aid))
        if not os.path.isdir(profile):
            raise TikTokError("this TikTok login is gone - log in again in Settings -> Social accounts")
        pw = ctx = page = None
        if aid in self.windows:
            raise TikTokError(f"the {a['name']} browser window is open on the PC - close it, then press Post again")
        lock = profile_lock(profile)
        if not lock.acquire(timeout=900):
            raise TikTokError(f"{a['name']} is busy in another TikTok window - try again in a minute")
        lock.release()
        with lock:
            try:
                progress("opening TikTok")
                pw, ctx = self._open(profile)
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                page.goto(UPLOAD_URL, wait_until="domcontentloaded", timeout=60000)
                _pause(4, 8)                                   # look at the page first
                self._guard(page, ctx)
                try:
                    page.mouse.move(random.uniform(300, 900), random.uniform(200, 600), steps=15)
                    page.mouse.wheel(0, random.randint(80, 260))
                    _pause(0.8, 2)
                    page.mouse.wheel(0, -random.randint(80, 260))
                except Exception:  # noqa: BLE001
                    pass
                inp = _first(page, "file", 60)
                if not inp:
                    raise TikTokError("could not find TikTok's upload button - the page may have changed")
                _pause(1.5, 3.5)
                progress("uploading the video to TikTok")
                inp.set_input_files(video)
                box = _first(page, "caption", 180)
                if not box:
                    self._guard(page, ctx)
                    raise TikTokError("the caption box did not appear - the upload may have failed")
                _pause(3, 7)                                   # watch the upload start before typing
                progress("writing the caption")
                _human_click(page, box)
                _pause(0.5, 1.2)
                page.keyboard.press("Control+A")
                _pause(0.2, 0.5)
                page.keyboard.press("Backspace")
                _pause(0.6, 1.5)
                _human_type(page, caption[:2200])
                _pause(0.4, 1)
                page.keyboard.press("Escape")
                _pause(4, 10)                                  # read the caption back
                progress("waiting for TikTok to finish processing")
                btn = None
                end = time.time() + 600
                while time.time() < end:
                    self._guard(page, ctx)
                    btn = _first(page, "post")
                    if btn and btn.is_enabled() and (btn.get_attribute("aria-disabled") or "false") != "true" \
                            and (btn.get_attribute("data-disabled") or "false") != "true":
                        break
                    time.sleep(2)
                else:
                    raise TikTokError("TikTok was still processing after 10 minutes")
                for d in SEL["dismiss"]:
                    try:
                        loc = page.locator(d).first
                        if loc.count() and loc.is_visible():
                            _pause(1, 2.5)
                            _human_click(page, loc)
                    except Exception:  # noqa: BLE001
                        pass
                try:
                    btn.scroll_into_view_if_needed()
                except Exception:  # noqa: BLE001
                    pass
                _pause(3, 7)                                   # a last look before posting
                progress("pressing Post")
                _human_click(page, btn)
                end = time.time() + 180
                while time.time() < end:
                    pn = _first(page, "post_now")
                    if pn:
                        _pause(1.5, 3.5)
                        _human_click(page, pn)              # "Continue to post?" confirmation
                    if _first(page, "done") or "/tiktokstudio/content" in page.url:
                        break
                    self._guard(page, ctx)
                    time.sleep(2)
                else:
                    raise TikTokError("pressed Post but TikTok did not confirm - check your TikTok profile")
                a.setdefault("posts", []).append(time.time())
                a["posts"] = [t for t in a["posts"] if t > time.time() - 7 * 86400]
                self.save()
                _pause(4, 9)                                   # stay on the page a moment, like a person
                return {"url": f"{SITE}/@{a['username']}" if a.get("username") else SITE}
            except TikTokError:
                self._shot(page)
                raise
            except Exception as e:  # noqa: BLE001
                self._shot(page)
                raise TikTokError(f"TikTok upload failed: {str(e)[:160]}") from None
            finally:
                for x in (ctx, pw):
                    try:
                        x and (x.close() if x is ctx else x.stop())
                    except Exception:  # noqa: BLE001
                        pass

    def _guard(self, page, ctx):
        """Stop (never work around) when TikTok wants a human: captcha / security check / logged out."""
        if "/login" in page.url or not self._logged_in(ctx):
            raise TikTokError("TikTok logged this account out - log in again in Settings -> Social accounts")
        if _first(page, "captcha"):
            raise TikTokError("TikTok asked for a security check - open Settings -> Social accounts -> TikTok -> "
                              "'Open browser' on the PC, solve it yourself, then post again")

    def _shot(self, page):
        try:
            if page:
                page.screenshot(path=os.path.join(self.root, "last_error.png"))
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------ manual browser (solve checks yourself)
    def open_browser(self, aid):
        a = self.account(aid)
        if aid in self.windows:                       # already open: just bring it to the front
            self.windows[aid]["front"] = True
            return f"The {a['name']} window is already open - brought it to the front"
        profile = os.path.join(self.root, a.get("profile", aid))
        lock = profile_lock(profile)
        if not lock.acquire(blocking=False):
            raise TikTokError(f"Chipper is using {a['name']} right now (posting or logging in) - try again in a minute")
        self.windows[aid] = {"front": False}
        self.window_msg.pop(aid, None)

        def run():
            pw = ctx = None
            try:
                pw, ctx = self._open(profile)
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                page.goto(SITE + "/tiktokstudio/upload", wait_until="domcontentloaded", timeout=60000)
                page.bring_to_front()
                end = time.time() + 900
                while ctx.pages and time.time() < end:
                    w = self.windows.get(aid) or {}
                    if w.get("front"):
                        w["front"] = False
                        try:
                            ctx.pages[0].bring_to_front()
                        except Exception:  # noqa: BLE001
                            pass
                    time.sleep(1)
            except Exception as e:  # noqa: BLE001
                self.window_msg[aid] = str(e)[:200]
                self.log(f"TikTok browser for {a['name']} failed: {e}")
            finally:
                try:
                    if ctx:
                        ctx.close()
                except Exception:  # noqa: BLE001
                    pass
                try:
                    if pw:
                        pw.stop()
                except Exception:  # noqa: BLE001
                    pass
                self.windows.pop(aid, None)
                lock.release()
        threading.Thread(target=run, daemon=True).start()
        return f"Opening {a['name']} on the PC..."
