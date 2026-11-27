"""Chipper Studio - install the optional add-ons from the Windows installer (or by hand).

    python addons.py nvidia tiktok

nvidia  NVIDIA RTX AI pack: AI Enhance (PyTorch + NVIDIA Video Effects) and Whisper large-v3 for AI captions
tiktok  the small browser Chipper uses to post to TikTok

Everything here can also be added or removed later in Chipper: Settings, Files.
"""
import os
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
_BIN = os.path.join(HERE, "bin")
if os.path.isdir(_BIN):
    os.environ["PATH"] = _BIN + os.pathsep + os.environ.get("PATH", "")
sys.path.insert(0, HERE)


def say(msg=""):
    print(msg, flush=True)


def nvidia_card():
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"], capture_output=True, text=True, timeout=30)
        return r.stdout.strip().splitlines()[0] if r.returncode == 0 and r.stdout.strip() else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def watch(obj, done):
    """Print what the installer is doing every few seconds."""
    last = ""
    while not done.is_set():
        now = " - ".join(x for x in (getattr(obj, "busy", ""), getattr(obj, "progress", "")) if x and x != "installing")
        if now and now != last:
            say("  " + now)
            last = now
        time.sleep(2)


def install_nvidia():
    say("== NVIDIA RTX AI pack (AI Enhance + AI captions with Whisper large-v3)")
    card = nvidia_card()
    if not card:
        say("  No NVIDIA graphics card found, so this part is skipped.")
        say("  Captions will come from YouTube or a free Groq key (Settings, AI).")
        return True
    say(f"  Graphics card: {card}")
    say("  This downloads about 6 GB and can take 15-40 minutes. Please keep this window open.")
    import nvidia_enhance as NV
    vsr = NV.NvidiaVSR(lambda m: say("  " + str(m)))
    done = threading.Event()
    threading.Thread(target=watch, args=(vsr, done), daemon=True).start()
    vsr.busy = "installing"
    vsr._install()
    done.set()
    if not vsr.ready():
        say("  AI Enhance did not install: " + (vsr.state.get("error") or "see " + NV.LOG))
        say("  You can try again later in Chipper: Settings, Files, Install AI Enhance.")
        return False
    stt = NV.GpuWhisper(vsr, lambda m: say("  " + str(m)))
    done = threading.Event()
    threading.Thread(target=watch, args=(stt, done), daemon=True).start()
    stt.busy = "installing Whisper"
    stt._install()
    done.set()
    try:
        stt.stop()
    except Exception:  # noqa: BLE001
        pass
    if not stt.installed():
        say("  AI captions did not install: " + (stt.state.get("error") or "see " + NV.LOG))
        say("  You can try again later in Chipper: Settings, Files, Install GPU captions.")
        return False
    say("  NVIDIA AI pack ready.")
    return True


def install_tiktok():
    say("== TikTok posting browser (about 150 MB)")
    r = subprocess.run([sys.executable, "-m", "playwright", "install", "chromium"])
    say("  Ready." if r.returncode == 0 else "  Did not install. Try again later in Chipper: Settings, Files.")
    return r.returncode == 0


def main(args):
    jobs = {"nvidia": install_nvidia, "tiktok": install_tiktok}
    ok = True
    for a in args:
        if a in jobs:
            ok = jobs[a]() and ok
            say()
    say("All done." if ok else "Finished, with a problem above. Chipper still works; you can retry from Settings.")
    if "--pause" in args or ("--pause-on-error" in args and not ok):
        input("Press Enter to close this window...")


if __name__ == "__main__":
    main(sys.argv[1:])
