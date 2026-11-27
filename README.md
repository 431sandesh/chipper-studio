# Chipper Studio

Chipper Studio turns live streams and long videos into vertical 1080×1920 clips for TikTok, Reels and Shorts. It runs on your own computer (Windows or Mac), watches chat for the big moments, cuts the clips, adds facecam layouts, captions, hooks, titles and hashtags and lets you edit and post them from your browser.

Nothing is posted until you press **Post**.

## What it does

- **Live clipping**: paste a YouTube, Twitch or TikTok LIVE link. Clips are made when chat spikes, or when you press **Clip now**. Up to 3 streams at once.
- **Clip finished videos**: paste a YouTube or Twitch video link, or upload a file. A campaign brief from a clipping campaign sets the hashtags, length and call to action.
- **Auto-clip**: add channels you clip. When one goes live, Chipper records and clips it. When one uploads a long video, Chipper clips that too.
- **Editor**: layouts with facecam and gameplay windows, crop keyframes, captions with styles, fonts and animations, hooks, text, images, lower thirds, meme effects, sounds and music.
- **Thumbnail editor**: a free canvas with frames from the clip, photos, text and stickers.
- **AI writing** (optional, free keys work): hooks, titles, captions, hashtags and a score for each clip, using Groq, Gemini, Claude or Ollama on your own computer.
- **Posting**: YouTube Shorts, Instagram Reels, Facebook Reels and TikTok (through the TikTok website).
- **Captions**: every new clip gets quick captions from a light, fast speech model. For the clips you keep, press **AI captions** in the editor to redo them with the best model (Whisper large-v3 on an NVIDIA graphics card, or Groq online).
- **Uploads**: pictures, music and sounds you upload are kept in the `uploads` folder. **Settings → Files** lists them, so you can delete files or remove duplicates. In the editor's Image section, **Grab YT thumbnail** saves a YouTube video's thumbnail from its link.
- **Posted clips**: deleting a posted clip removes the video but keeps its thumbnail and views in **Posted**. Delete it again there to remove it completely.
- **Phone access**: a private link to use Chipper from your phone or any other computer.

## What you need

|  | Windows | Mac |
|---|---|---|
| System | Windows 10 or 11 | macOS 12 or newer, Apple silicon or Intel |
| Disk space | about 2 GB, plus room for recordings (2–3 GB per hour of stream) | same |
| Installed for you | Python 3.12, FFmpeg, cloudflared | Python 3.12, FFmpeg, cloudflared (Apple silicon Macs also get Homebrew) |

AI Enhance (NVIDIA video upscaling) needs an NVIDIA graphics card, so it only works on Windows. Everything else works on both.

## Install on Windows

1. Download **ChipperStudioSetup.exe** from [chipperstudio.nepconlabs.com](https://chipperstudio.nepconlabs.com) or the [Releases page](https://github.com/431sandesh/chipper-studio/releases).
2. Run it. If Windows shows "Windows protected your PC", click **More info**, then **Run anyway**.
3. Pick what to install:
   - **Standard (any PC):** Chipper, plus the small browser it uses to post to TikTok.
   - **Everything (NVIDIA RTX cards):** also the NVIDIA RTX AI pack, which is AI Enhance (sharper video) and AI captions with Whisper large-v3. It's about a 6 GB download and takes 15 to 40 minutes. Only choose it if your PC has an NVIDIA RTX 20, 30, 40 or 50 series card.
4. Start Chipper from the **Chipper Studio** shortcut on your desktop. Your browser opens at http://127.0.0.1:5050.

Python, FFmpeg and cloudflared come inside the installer, so you don't need to install anything else. Keep the black window open while you use Chipper. Close it to quit.

You can add or remove the NVIDIA AI pack and the TikTok browser later in Chipper: **Settings, Files**.

<details><summary>Install by hand instead (from the ZIP)</summary>

1. Download this repo: the green **Code** button, then **Download ZIP**. Unzip it somewhere easy, for example `C:\Chipper`.
2. Double-click **install-windows.bat**. If Python wasn't installed, it installs it and asks you to run it again.
3. When it says **Done**, double-click **run-windows.bat**.
</details>

## Install on Mac

1. Download this repo: the green **Code** button, then **Download ZIP**. Unzip it, then move the folder somewhere easy, for example your home folder.
2. Open the folder, right-click **install-mac.command** and choose **Open**, then **Open** again. (Right-click is needed the first time because the file came from the internet.)
   - On an **Intel Mac**, it downloads FFmpeg and cloudflared into the Chipper folder. No password needed.
   - On an **Apple silicon Mac** (M1 or newer), it installs Homebrew if you don't have it. That step asks for your Mac password. Nothing appears on screen while you type it, which is normal.
   - Python 3.12 is set up inside the Chipper folder, so it doesn't touch the rest of your Mac.
   - It asks whether to install Whisper for captions on your Mac (about 2 GB).
3. When it says **Done**, double-click **run-mac.command**. Your browser opens at http://127.0.0.1:5050.

Keep the Terminal window open while you use Chipper. Close it to quit.

If double-clicking does nothing, open **Terminal**, type `cd ` (with a space), drag the Chipper folder into the window, press Return, then type these yourself (pasting can add hidden characters that break them):

```
bash install-mac.command
bash run-mac.command
```

## First steps

1. **Live tab**: paste a stream link, pick a template and press **Start**.
2. **Template**: press **Capture frame from live**, drag the pink box onto the facecam and the green box onto the gameplay, pick a caption style, name it and **Save**. Templates are reused for that streamer.
3. **Clips tab**: watch your clips, edit them, change the thumbnail and post.

## Use it from your phone or another computer

1. **Settings → Remote access**: set a remote password (6+ characters) and press **Turn on**.
2. A link like `https://some-random-words.trycloudflare.com` appears. Open it anywhere and enter your password.
3. The link changes each time Chipper restarts. To get it automatically on your phone, install the free **ntfy** app and subscribe to the topic shown in Settings.

**A link that never changes (optional).** If your domain is on Cloudflare, create a tunnel in the Cloudflare dashboard (Zero Trust → Networks → Tunnels → Create a tunnel → Cloudflared). Copy the token (the long `eyJ…` text in the install command; don't run the command), add a public hostname such as `studio.yourdomain.com` pointing to `HTTP` `127.0.0.1:5050`, then paste the address and token in **Settings → Remote access → Permanent link** and turn remote access off and on. The token stays on your computer.

Over the link you can clip, edit and post. Keys, social accounts and remote access settings can only be changed on the computer running Chipper.

## Connecting your accounts

Keys and sign-ins are saved only on your computer, in `accounts.json`. Don't share that file.

### YouTube Shorts (about 10 minutes, once)

1. Go to https://console.cloud.google.com and create a project.
2. **APIs & Services → Library**: search **YouTube Data API v3** and press **Enable**.
3. **Google Auth Platform → OAuth consent screen**: any app name, your email, audience **External**. Add your own Gmail as a test user, then press **Publish app**, or Google signs you out every 7 days.
4. **Clients → Create client**, type **Web application**. Under **Authorized redirect URIs** add exactly: `http://127.0.0.1:5050/oauth/youtube`
5. Copy the **Client ID** and **Client secret** into **Settings → Connected accounts → YouTube**, press **Connect YouTube** and sign in. When Google says it hasn't verified the app, click **Advanced → Go to** your app: it's your own.

### Instagram and Facebook Reels (about 15 minutes, once)

1. Make your Instagram a professional (Creator) account and link it to a Facebook Page.
2. At https://developers.facebook.com, create an app and copy its **App ID** and **App secret**.
3. In **Graph API Explorer**, choose your app and a **User Token** with: `pages_show_list, pages_read_engagement, pages_manage_posts, instagram_basic, instagram_content_publish, business_management`. Press **Generate Access Token**.
4. Paste the App ID, App secret and token into **Settings → Connected accounts → Instagram** and press **Save & connect**. The token lasts 60 days; Settings shows how many days are left.

### TikTok

TikTok doesn't let personal tools post through its API, so Chipper uses the TikTok website in a browser you log into yourself: **Settings → Social accounts → TikTok → Log in to TikTok**. Chipper posts only when you press Post, at most 10 posts per account per day, 10 minutes apart. If TikTok shows a captcha or security check, Chipper stops and you finish it yourself. Automated uploads are against TikTok's rules, so only use accounts you can afford to risk.

## AI for hooks and titles (optional)

**Settings → AI & captions**:

- **Built-in**: free, no key. Hooks come from the speech and the chat's mood.
- **Groq**: free key from console.groq.com. Recommended.
- **Google Gemini**: free key from aistudio.google.com.
- **Claude**: paid key.
- **Ollama**: runs on your own computer. Install it from ollama.com and run `ollama pull llama3.2:3b`.

If the AI fails or hits its free limit, Chipper falls back to the built-in hooks.

## Where your files go

Inside the Chipper folder:

- `work/rec`: full recordings (turn off "Keep full recordings" in Settings to delete them automatically)
- `work/raw`: the source of each clip, so clips can be re-edited later
- `uploads/audio`, `uploads/fx`, `uploads/images`, `uploads/other`: everything you upload from the browser
- `fonts`: extra fonts (.ttf or .otf) for captions and text
- `settings.json`, `accounts.json`, `templates.json`: your settings, sign-ins and templates

Finished clips go to `OneDrive/Clips` on Windows (or `clips` in the Chipper folder if you don't use OneDrive) and `Movies/Chipper Clips` on Mac. Change it in **Settings**.

None of your own files are ever part of this repo: `.gitignore` keeps them out.

## Updating

On Windows, run the newest installer over the old one. On Mac, or if you installed from the ZIP, download the new ZIP and copy its files over your Chipper folder. Your settings, accounts, clips and uploads stay, because they aren't in the ZIP. If something new needs installing, run the install script again.

## Troubleshooting

- **"MISSING: ffmpeg"**: run the install script again, then reopen Chipper. On Mac you can also run `brew install ffmpeg`.
- **Remote access says cloudflared is not installed**: Windows: `winget install --id Cloudflare.cloudflared`. Mac: `brew install cloudflared`. Then restart Chipper.
- **Port 5050 is busy**: another copy of Chipper is already running. Close it first.
- **Nepali or Hindi captions show boxes**: pick a font that has Devanagari letters. Windows uses Nirmala UI automatically.
