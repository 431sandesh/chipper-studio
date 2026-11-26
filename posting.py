"""Posting clips to YouTube Shorts and Instagram Reels.

YouTube: OAuth "Web application" client from Google Cloud, redirect URI http://127.0.0.1:5050/oauth/youtube.
Instagram: Instagram professional (Creator/Business) account linked to a Facebook Page,
           Meta app + a user token from Graph API Explorer. Video is uploaded straight from disk.
Tokens are stored only on this computer (accounts.json).
"""
import http.client as http_client
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request

# overridable for testing
G_AUTH = os.environ.get("CHIPPER_G_AUTH", "https://accounts.google.com/o/oauth2/v2/auth")
G_TOKEN = os.environ.get("CHIPPER_G_TOKEN", "https://oauth2.googleapis.com/token")
YT_API = os.environ.get("CHIPPER_YT_API", "https://www.googleapis.com/youtube/v3")
YT_UPLOAD = os.environ.get("CHIPPER_YT_UPLOAD", "https://www.googleapis.com/upload/youtube/v3/videos")
FB_GRAPH = os.environ.get("CHIPPER_FB_GRAPH", "https://graph.facebook.com/v23.0")
FB_RUPLOAD = os.environ.get("CHIPPER_FB_RUPLOAD", "https://rupload.facebook.com/ig-api-upload/v23.0")
YT_SCOPES = "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly"


class PostError(Exception):
    pass




def _http_once(method, url, body, headers, timeout):
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            text = r.read().decode("utf-8", "replace")
            return r.status, dict(r.headers), (json.loads(text) if text.strip().startswith(("{", "[")) else text)
    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8", "replace")
        try:
            j = json.loads(text)
            err = j.get("error")
            msg = (err.get("message") if isinstance(err, dict) else None) or j.get("error_description") or err or text
        except ValueError:
            msg = text[:300]
        raise PostError(f"{e.code}: {msg}") from None
    except urllib.error.URLError as e:
        raise NetError(f"network error: {e.reason}") from None
    except (ConnectionError, TimeoutError, OSError, http_client.HTTPException) as e:
        raise NetError(f"network error: {e}") from None


class NetError(PostError):
    """The internet dropped (not an answer from the site)."""


def http(method, url, data=None, headers=None, form=None, raw=None, timeout=120, retries=4):
    """One API call. Internet blips are retried (about a minute in total). Every call Chipper makes is safe to
    repeat: uploads go to the same upload session and publishing uses the same container / video id."""
    headers = dict(headers or {})
    body = None
    if form is not None:
        body = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif data is not None:
        body = json.dumps(data).encode()
        headers["Content-Type"] = "application/json; charset=UTF-8"
    elif raw is not None:
        body = raw
    waits = (3, 8, 15, 30)
    for attempt in range(retries + 1):
        try:
            return _http_once(method, url, body, headers, timeout)
        except NetError as e:
            if attempt >= retries:
                raise NetError(f"{e} - the PC's internet dropped; press Post again for this account") from None
            time.sleep(waits[min(attempt, len(waits) - 1)])


# ================================================================== accounts store
class Accounts:
    def __init__(self, path):
        self.path = path
        try:
            with open(path, encoding="utf-8") as f:
                self.data = json.load(f)
        except (OSError, ValueError):
            self.data = {}
        yt = self.data.setdefault("youtube", {})
        yt.setdefault("channels", [])
        if yt.get("refresh_token"):                         # old single-channel format -> list
            yt["channels"].append({"id": yt.get("channel_id") or "default", "title": yt.get("channel", "YouTube"),
                                   "refresh_token": yt["refresh_token"], "access_token": yt.get("access_token", ""),
                                   "access_exp": yt.get("access_exp", 0)})
            for k in ("refresh_token", "access_token", "access_exp", "channel", "channel_id"):
                yt.pop(k, None)
        self.data.setdefault("facebook", {}).setdefault("pages", [])
        ig = self.data.setdefault("instagram", {})
        ig.setdefault("accounts", [])
        if ig.get("token") and ig.get("ig_user_id"):      # old single-account format -> list
            if not any(a["id"] == ig["ig_user_id"] for a in ig["accounts"]):
                ig["accounts"].append({"id": ig["ig_user_id"], "username": ig.get("username", ""),
                                       "page": ig.get("page", ""), "token": ig["token"],
                                       "expires": ig.get("expires", 0)})
            for k in ("token", "ig_user_id", "username", "page", "expires"):
                ig.pop(k, None)

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2)
        os.replace(tmp, self.path)

    def public(self):
        yt, ig = self.data["youtube"], self.data["instagram"]
        mask = lambda s: ("••••" + s[-4:]) if s else ""
        return {
            "youtube": {"client_id": yt.get("client_id", ""), "client_secret": mask(yt.get("client_secret", "")),
                        "connected": bool(yt.get("channels")), "privacy": yt.get("privacy", "public"),
                        "channels": [{"id": c["id"], "title": c.get("title", "")} for c in yt.get("channels", [])]},
            "instagram": {"app_id": ig.get("app_id", ""), "app_secret": mask(ig.get("app_secret", "")),
                          "connected": bool(ig.get("accounts")),
                          "accounts": [{"id": a["id"], "username": a.get("username", ""), "page": a.get("page", ""),
                                        "expires": a.get("expires", 0)} for a in ig.get("accounts", [])]},
            "facebook": {"connected": bool(self.data["facebook"].get("pages")),
                         "pages": [{"id": p["id"], "name": p.get("name", "")} for p in self.data["facebook"].get("pages", [])]},
            "langs": dict(self.data.get("langs") or {}),
        }


# ================================================================== YouTube
class YouTube:
    def __init__(self, acc, redirect_uri):
        self.acc = acc
        self.redirect_uri = redirect_uri
        self.states = {}          # state -> created time (several sign-in tabs may be open)

    @property
    def d(self):
        return self.acc.data["youtube"]

    def auth_url(self):
        if not (self.d.get("client_id") and self.d.get("client_secret")):
            raise PostError("add your YouTube client ID and secret first")
        state = secrets.token_urlsafe(16)
        now = time.time()
        self.states = {k: t for k, t in self.states.items() if now - t < 1800}
        self.states[state] = now
        q = {"client_id": self.d["client_id"], "redirect_uri": self.redirect_uri, "response_type": "code",
             "scope": YT_SCOPES, "access_type": "offline", "prompt": "consent select_account",
             "state": state}
        return G_AUTH + "?" + urllib.parse.urlencode(q)

    def finish(self, code, state):
        """Sign-in finished: add (or refresh) the channel that was picked on Google's screen."""
        if state not in self.states:
            raise PostError("this sign-in link is old (Chipper was restarted?). Close this tab and press Connect YouTube again")
        _, _, j = http("POST", G_TOKEN, form={
            "code": code, "client_id": self.d["client_id"], "client_secret": self.d["client_secret"],
            "redirect_uri": self.redirect_uri, "grant_type": "authorization_code"})
        if not j.get("refresh_token"):
            raise PostError("Google did not return a refresh token; remove the app's access in your Google account and connect again")
        self.states.pop(state, None)
        ch = {"refresh_token": j["refresh_token"], "access_token": j["access_token"],
              "access_exp": time.time() + int(j.get("expires_in", 3600)) - 60, "id": "", "title": ""}
        try:
            _, _, info = http("GET", f"{YT_API}/channels?part=snippet&mine=true",
                              headers={"Authorization": "Bearer " + ch["access_token"]})
            items = info.get("items") or []
            if items:
                ch["id"], ch["title"] = items[0]["id"], items[0]["snippet"]["title"]
        except PostError:
            pass
        if not ch["id"]:
            raise PostError("this Google account has no YouTube channel - pick an account (or brand channel) that has one")
        # a channel carried over from the old single-channel setup has no id yet: replace it
        self.d["channels"] = [c for c in self.d.get("channels", [])
                              if not (c["id"] == "default" and c.get("title") in (ch["title"], "YouTube", "connected"))]
        chans = self.d["channels"]
        for i, c in enumerate(chans):
            if c["id"] == ch["id"]:
                chans[i] = ch
                break
        else:
            chans.append(ch)
        self.acc.save()
        return ch["title"]

    def channel(self, channel_id=None):
        chans = self.d.get("channels", [])
        if not chans:
            raise PostError("YouTube is not connected")
        if channel_id is None:
            if len(chans) > 1:
                raise PostError("pick which YouTube channel to post to")
            return chans[0]
        for c in chans:
            if c["id"] == channel_id:
                return c
        raise PostError("that YouTube channel is no longer connected")

    def remove(self, channel_id=None):
        self.d["channels"] = [c for c in self.d.get("channels", []) if channel_id and c["id"] != channel_id]
        self.acc.save()

    def _token(self, ch):
        if ch.get("access_token") and time.time() < ch.get("access_exp", 0):
            return ch["access_token"]
        try:
            _, _, j = http("POST", G_TOKEN, form={
                "client_id": self.d["client_id"], "client_secret": self.d["client_secret"],
                "refresh_token": ch["refresh_token"], "grant_type": "refresh_token"})
        except PostError as e:
            if str(e).startswith("401") or "unauthorized" in str(e).lower():
                raise PostError(f"{ch.get('title')} was connected with an older Google login (client). Remove it in "
                                "Settings -> Social accounts and press Add channel to connect it again.") from None
            if "invalid_grant" in str(e) or "expired" in str(e).lower():
                raise PostError(f"YouTube sign-in for {ch.get('title')} expired - press Connect / add channel again "
                                "(set the Google app to 'In production' so it stops expiring weekly)") from None
            raise
        ch.update(access_token=j["access_token"], access_exp=time.time() + int(j.get("expires_in", 3600)) - 60)
        self.acc.save()
        return ch["access_token"]

    def stats(self, vid, channel_id=None):
        """Views, likes and comments of a posted video (any connected channel can read them)."""
        chans = self.d.get("channels", [])
        if not chans:
            raise PostError("YouTube is not connected")
        ch = next((c for c in chans if c["id"] == channel_id), chans[0])
        _, _, j = http("GET", f"{YT_API}/videos?part=statistics&id={urllib.parse.quote(vid)}",
                       headers={"Authorization": f"Bearer {self._token(ch)}"}, timeout=20, retries=1)
        st = ((j.get("items") or [{}])[0]).get("statistics") or {}
        return {"views": int(st.get("viewCount", 0)), "likes": int(st.get("likeCount", 0)), "comments": int(st.get("commentCount", 0))}

    def upload(self, video, title, description, tags, privacy="public", progress=lambda s: None, channel_id=None,
               cover=None):
        ch = self.channel(channel_id)
        auth = {"Authorization": "Bearer " + self._token(ch)}
        size = os.path.getsize(video)
        title = (title or "Clip").strip()
        if "#shorts" not in title.lower() and len(title) <= 90:
            title += " #Shorts"
        meta = {"snippet": {"title": title[:100], "description": description[:4900],
                            "tags": [t.lstrip("#") for t in tags][:15], "categoryId": "20"},
                "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False}}
        progress(f"starting upload to {ch.get('title')}")
        start = lambda: http("POST", YT_UPLOAD + "?uploadType=resumable&part=snippet,status", data=meta, headers={
            **auth, "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(size)})
        try:
            _, h, _ = start()
        except PostError as e:
            if not str(e).startswith("401"):
                raise
            ch["access_token"], ch["access_exp"] = "", 0          # stale cached sign-in: get a fresh one
            auth = {"Authorization": "Bearer " + self._token(ch)}
            try:
                _, h, _ = start()
            except PostError as e2:
                if str(e2).startswith("401"):
                    raise PostError(f"YouTube refused the sign-in for {ch.get('title')}. In Settings -> Social "
                                    "accounts remove that channel and press Add channel to connect it again.") from None
                raise
        loc = h.get("Location") or h.get("location")
        if not loc:
            raise PostError("YouTube did not return an upload URL")
        progress(f"uploading {size // 1_000_000} MB")
        with open(video, "rb") as f:
            _, _, j = http("PUT", loc, raw=f.read(), headers={**auth, "Content-Type": "video/mp4"}, timeout=900)
        vid = j.get("id") if isinstance(j, dict) else None
        if not vid:
            raise PostError("upload finished but no video id came back")
        if cover and os.path.isfile(cover):
            progress("setting the thumbnail")
            try:
                with open(cover, "rb") as f:
                    http("POST", f"{YT_UPLOAD.replace('/videos', '/thumbnails/set')}?videoId={vid}&uploadType=media",
                         raw=f.read(), headers={**auth, "Content-Type": "image/jpeg"}, timeout=120)
            except PostError as e:      # unverified channels can't set custom thumbnails - the video is still posted
                progress(f"thumbnail not set ({str(e)[:80]})")
        return {"id": vid, "url": f"https://youtube.com/shorts/{vid}"}


# ================================================================== Instagram (Facebook Login)
class Instagram:
    def __init__(self, acc):
        self.acc = acc

    @property
    def d(self):
        return self.acc.data["instagram"]

    def connect(self, token):
        """token: user access token from Graph API Explorer (short or long lived)."""
        token = (token or "").strip()
        if not token:
            raise PostError("paste the access token from Graph API Explorer")
        expires = time.time() + 3600
        if self.d.get("app_id") and self.d.get("app_secret"):
            # exchange for a 60-day token
            q = urllib.parse.urlencode({"grant_type": "fb_exchange_token", "client_id": self.d["app_id"],
                                        "client_secret": self.d["app_secret"], "fb_exchange_token": token})
            _, _, j = http("GET", f"{FB_GRAPH}/oauth/access_token?{q}")
            token = j["access_token"]
            expires = time.time() + int(j.get("expires_in", 60 * 86400))
        q = urllib.parse.urlencode({"fields": "name,access_token,tasks,instagram_business_account{id,username}",
                                    "access_token": token, "limit": 100})
        _, _, j = http("GET", f"{FB_GRAPH}/me/accounts?{q}")
        found = []
        accounts = self.d.setdefault("accounts", [])
        pages = self.acc.data["facebook"].setdefault("pages", [])
        for page in j.get("data", []):
            if page.get("access_token"):          # Facebook Page itself (for Facebook Reels)
                pentry = {"id": page["id"], "name": page.get("name", ""), "token": page["access_token"]}
                for i, p in enumerate(pages):
                    if p["id"] == pentry["id"]:
                        pages[i] = pentry
                        break
                else:
                    pages.append(pentry)
                found.append("FB: " + pentry["name"])
            iga = page.get("instagram_business_account")
            if not iga:
                continue
            entry = {"id": iga["id"], "username": iga.get("username", ""), "page": page.get("name", ""),
                     "token": token, "expires": expires}
            for i, a in enumerate(accounts):
                if a["id"] == entry["id"]:
                    accounts[i] = entry       # refresh token for an account we already have
                    break
            else:
                accounts.append(entry)
            found.append("@" + entry["username"])
        if found:
            self.acc.save()
            return found
        raise PostError("no Facebook Pages or Instagram professional accounts found in this token. "
                        "Link your Instagram (Creator/Business) to a Facebook Page and include the "
                        "pages_show_list, instagram_basic and instagram_content_publish permissions")

    def account(self, account_id=None):
        accs = self.d.get("accounts", [])
        if not accs:
            raise PostError("Instagram is not connected")
        if account_id is None:
            if len(accs) > 1:
                raise PostError("pick which Instagram account to post to")
            return accs[0]
        for a in accs:
            if a["id"] == account_id:
                return a
        raise PostError("that Instagram account is no longer connected")

    def remove(self, account_id=None):
        if account_id:
            self.d["accounts"] = [a for a in self.d.get("accounts", []) if a["id"] != account_id]
        else:
            self.d["accounts"] = []
        self.acc.save()

    # app.py sets this to a function(video_path) -> public https link (or "" when no remote link is running)
    public_link = None

    def stats(self, mid, account_id=None):
        """Views (plays), likes and comments of a posted reel."""
        accs = self.d.get("accounts", [])
        if not accs:
            raise PostError("Instagram is not connected")
        tok = next((a for a in accs if a["id"] == account_id), accs[0])["token"]
        q = urllib.parse.urlencode({"fields": "like_count,comments_count", "access_token": tok})
        base = http("GET", f"{FB_GRAPH}/{mid}?{q}", timeout=20, retries=1)[2]
        views = 0
        for metric in ("views", "plays", "ig_reels_aggregated_all_plays_count"):
            try:
                q = urllib.parse.urlencode({"metric": metric, "access_token": tok})
                d = http("GET", f"{FB_GRAPH}/{mid}/insights?{q}", timeout=20, retries=1)[2].get("data") or []
                if d:
                    views = int(((d[0].get("values") or [{}])[0]).get("value") or d[0].get("total_value", {}).get("value") or 0)
                    break
            except PostError:
                continue
        return {"views": views, "likes": int(base.get("like_count", 0)), "comments": int(base.get("comments_count", 0))}

    def upload(self, video, caption, progress=lambda s: None, account_id=None, cover=None, thumb_ms=None):
        """Try the direct upload first. If Meta's upload server rejects it (ProcessingFailedError), switch to
        letting Instagram download the clip from Chipper's remote link, which avoids that server entirely."""
        hiccup = ("ProcessingFailed", "processing failed", "could not process", "500", "502", "503",
                  "timed out", "network error")
        last = None
        use_link = False
        for attempt in range(3):
            link = None
            if use_link:
                link = self.public_link(video) if self.public_link else ""
                if not link:
                    use_link = False
            try:
                return self._upload_once(video, caption, progress, account_id, link=link, cover=cover, thumb_ms=thumb_ms)
            except PostError as e:
                last = e
                msg = str(e)
                if not any(k in msg for k in hiccup) or attempt == 2:
                    break
                if not link and self.public_link and self.public_link(video):
                    use_link = True
                    progress(f"direct upload refused, letting Instagram fetch the clip instead ({attempt + 2}/3)")
                    time.sleep(3)
                else:
                    progress(f"Instagram hiccup, retrying ({attempt + 2}/3) in 20 s")
                    time.sleep(20)
        msg = str(last)
        if "ProcessingFailed" in msg and not (self.public_link and self.public_link(video)):
            raise PostError("Meta's upload server refused the file. Turn on the Remote link (Settings -> Remote "
                            "access) and press Post again: Chipper then lets Instagram download the clip itself.")
        raise last

    def _upload_once(self, video, caption, progress=lambda s: None, account_id=None, link=None, cover=None, thumb_ms=None):
        """link=None: send the file bytes to Meta (resumable upload).
        link=<public https url>: let Instagram download the clip itself from that link."""
        a = self.account(account_id)
        if a.get("expires") and time.time() > a["expires"]:
            raise PostError(f"token for @{a.get('username')} expired, paste a new one in Settings")
        tok, ig = a["token"], a["id"]
        size = os.path.getsize(video)
        progress("creating reel")
        form = {"media_type": "REELS", "caption": caption[:2200], "share_to_feed": "true", "access_token": tok}
        if link:
            form["video_url"] = link
        else:
            form["upload_type"] = "resumable"
        cover_link = self.public_link(cover) if (cover and self.public_link) else ""
        if cover_link:
            form["cover_url"] = cover_link              # the designed thumbnail (needs the remote link on)
        elif thumb_ms is not None:
            form["thumb_offset"] = str(int(thumb_ms))  # otherwise at least use the chosen frame
        _, _, j = http("POST", f"{FB_GRAPH}/{ig}/media", form=form)
        cid = j.get("id")
        if not cid:
            raise PostError("Instagram did not create the reel container")
        if not link:
            progress(f"uploading {size // 1_000_000} MB")
            with open(video, "rb") as f:
                _, _, r = http("POST", j.get("uri") or f"{FB_RUPLOAD}/{cid}", raw=f.read(), headers={
                    "Authorization": "OAuth " + tok, "offset": "0", "file_size": str(size)}, timeout=900)
            if isinstance(r, dict) and r.get("success") is False:
                raise PostError("upload rejected: " + str(r))
        progress("instagram is fetching and processing the video" if link else "instagram is processing the video")
        for _ in range(90):              # up to ~6 minutes
            q = urllib.parse.urlencode({"fields": "status_code,status", "access_token": tok})
            _, _, s = http("GET", f"{FB_GRAPH}/{cid}?{q}")
            code = s.get("status_code")
            if code == "FINISHED":
                break
            if code in ("ERROR", "EXPIRED"):
                raise PostError("Instagram could not process the video: " + str(s.get("status", code)))
            time.sleep(4)
        else:
            raise PostError("Instagram is taking too long to process; try Post again later")
        progress("publishing")
        try:
            _, _, p = http("POST", f"{FB_GRAPH}/{ig}/media_publish", form={"creation_id": cid, "access_token": tok})
            mid = p.get("id")
        except PostError:
            # the answer may have been lost after Instagram already published it: check before reporting a failure
            q = urllib.parse.urlencode({"fields": "status_code", "access_token": tok})
            if http("GET", f"{FB_GRAPH}/{cid}?{q}")[2].get("status_code") != "PUBLISHED":
                raise
            q = urllib.parse.urlencode({"fields": "id", "limit": "1", "access_token": tok})
            mid = ((http("GET", f"{FB_GRAPH}/{ig}/media?{q}")[2].get("data") or [{}])[0]).get("id")
        url = ""
        try:
            q = urllib.parse.urlencode({"fields": "permalink", "access_token": tok})
            url = http("GET", f"{FB_GRAPH}/{mid}?{q}")[2].get("permalink", "")
        except PostError:
            pass
        return {"id": mid, "url": url or f"https://instagram.com/{a.get('username', '')}"}


# ================================================================== Facebook Page Reels
class Facebook:
    """Publish Reels to Facebook Pages (Page tokens come from the same Meta connection as Instagram)."""

    def __init__(self, acc):
        self.acc = acc

    @property
    def d(self):
        return self.acc.data["facebook"]

    def page(self, page_id=None):
        pages = self.d.get("pages", [])
        if not pages:
            raise PostError("no Facebook Page connected")
        if page_id is None:
            if len(pages) > 1:
                raise PostError("pick which Facebook Page to post to")
            return pages[0]
        for p in pages:
            if p["id"] == page_id:
                return p
        raise PostError("that Facebook Page is no longer connected")

    def remove(self, page_id=None):
        self.d["pages"] = [p for p in self.d.get("pages", []) if page_id and p["id"] != page_id]
        self.acc.save()

    def stats(self, vid, page_id=None):
        """Plays and likes of a posted reel."""
        p = self.page(page_id)
        tok = p["token"]
        views = 0
        for metric in ("blue_reels_play_count", "fb_reels_total_plays", "total_video_views"):
            try:
                q = urllib.parse.urlencode({"metric": metric, "access_token": tok})
                d = http("GET", f"{FB_GRAPH}/{vid}/video_insights?{q}", timeout=20, retries=1)[2].get("data") or []
                if d:
                    views = int(((d[0].get("values") or [{}])[0]).get("value") or 0)
                    break
            except PostError:
                continue
        likes = 0
        try:
            q = urllib.parse.urlencode({"fields": "likes.summary(true)", "access_token": tok})
            likes = int(((http("GET", f"{FB_GRAPH}/{vid}?{q}", timeout=20, retries=1)[2].get("likes") or {}).get("summary") or {}).get("total_count", 0))
        except PostError:
            pass
        return {"views": views, "likes": likes}

    def upload(self, video, description, progress=lambda s: None, page_id=None, cover=None):
        p = self.page(page_id)
        tok, pid = p["token"], p["id"]
        size = os.path.getsize(video)
        progress(f"starting Facebook reel on {p.get('name')}")
        try:
            _, _, j = http("POST", f"{FB_GRAPH}/{pid}/video_reels", form={"upload_phase": "start", "access_token": tok})
        except PostError as e:
            if "permission" in str(e).lower() or "(#200)" in str(e) or "(#10)" in str(e):
                raise PostError("Facebook needs the pages_manage_posts permission: add it in Graph API Explorer, "
                                "generate a new token and press Connect / add accounts") from None
            raise
        vid, up = j.get("video_id"), j.get("upload_url")
        if not vid:
            raise PostError("Facebook did not start the upload")
        progress(f"uploading {size // 1_000_000} MB")
        with open(video, "rb") as f:
            _, _, r = http("POST", up or f"{FB_RUPLOAD.replace('ig-api-upload', 'video-upload')}/{vid}", raw=f.read(),
                           headers={"Authorization": "OAuth " + tok, "offset": "0", "file_size": str(size),
                                    "Content-Type": "application/octet-stream"}, timeout=900)
        if isinstance(r, dict) and r.get("success") is False:
            raise PostError("Facebook upload rejected: " + str(r))
        progress("publishing")
        _, _, fin = http("POST", f"{FB_GRAPH}/{pid}/video_reels", form={
            "upload_phase": "finish", "video_id": vid, "video_state": "PUBLISHED",
            "description": description[:2200], "access_token": tok})
        if isinstance(fin, dict) and fin.get("success") is False:
            raise PostError("Facebook did not publish the reel: " + str(fin))
        if cover and os.path.isfile(cover):
            progress("setting the thumbnail")
            try:
                bnd = "----chipper" + secrets.token_hex(8)
                with open(cover, "rb") as f:
                    img = f.read()
                body = (f"--{bnd}\r\nContent-Disposition: form-data; name=\"is_preferred\"\r\n\r\ntrue\r\n"
                        f"--{bnd}\r\nContent-Disposition: form-data; name=\"access_token\"\r\n\r\n{tok}\r\n"
                        f"--{bnd}\r\nContent-Disposition: form-data; name=\"source\"; filename=\"cover.jpg\"\r\n"
                        f"Content-Type: image/jpeg\r\n\r\n").encode() + img + f"\r\n--{bnd}--\r\n".encode()
                http("POST", f"{FB_GRAPH}/{vid}/thumbnails", raw=body,
                     headers={"Content-Type": f"multipart/form-data; boundary={bnd}"}, timeout=120)
            except PostError as e:
                progress(f"thumbnail not set ({str(e)[:80]})")
        # wait a little for processing so obvious failures are reported
        for _ in range(20):
            try:
                q = urllib.parse.urlencode({"fields": "status", "access_token": tok})
                st = http("GET", f"{FB_GRAPH}/{vid}?{q}")[2].get("status", {})
            except PostError:
                break
            if st.get("video_status") == "error" or (st.get("processing_phase") or {}).get("status") == "error":
                raise PostError("Facebook could not process the video: " + str((st.get("processing_phase") or {}).get("error", st))[:300])
            if (st.get("publishing_phase") or {}).get("status") == "complete" or st.get("video_status") == "ready":
                break
            time.sleep(4)
        return {"id": vid, "url": f"https://www.facebook.com/reel/{vid}"}
