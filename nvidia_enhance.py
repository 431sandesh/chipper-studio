"""Chipper Studio - NVIDIA RTX AI Enhance (Video Super Resolution) manager.

Installs NVIDIA's VFX Python package into its own environment next to Chipper (so the big PyTorch download never
touches Chipper's main Python), checks the graphics card, and runs vsr_worker.py on a clip's source video.
"""
import json
import os
import re
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("CHIPPER_NVVFX_DIR") or os.path.join(HERE, "nvvfx")
VENV = os.path.join(ROOT, "venv")
PY = os.path.join(VENV, "Scripts", "python.exe") if os.name == "nt" else os.path.join(VENV, "bin", "python")
WORKER = os.path.join(HERE, "vsr_worker.py")
STT_WORKER = os.path.join(HERE, "whisper_worker.py")
STT_MODELS = os.path.join(ROOT, "whisper-models")
STATE = os.path.join(ROOT, "state.json")
LOG = os.path.join(ROOT, "install.log")
GPU_LOCK = threading.Lock()
STT = None                                      # the GpuWhisper instance (set by Chipper)                     # one AI job on the graphics card at a time
NOWIN = 0x08000000 if os.name == "nt" else 0

# same pins as NVIDIA's sample (torch with CUDA 12.8 supports RTX 20-50 series, incl. Blackwell)
PIP_STEPS = [
    ["-m", "pip", "install", "--upgrade", "pip"],
    ["-m", "pip", "install", "--extra-index-url", "https://download.pytorch.org/whl/cu128", "torch==2.11.0+cu128"],
    ["-m", "pip", "install", "av", "numpy", "nvidia-vfx==0.2.0.0"],
]


class NvidiaVSR:
    def __init__(self, log=print):
        self.log = log
        self.busy = ""                               # "installing" / "checking" while running
        self.progress = ""
        self.state = self._load()

    def _load(self):
        try:
            with open(STATE, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save(self):
        os.makedirs(ROOT, exist_ok=True)
        with open(STATE, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=1)

    def ready(self):
        return bool(self.state.get("ok")) and os.path.isfile(PY)

    def public(self):
        tail = ""
        try:
            with open(LOG, encoding="utf-8", errors="replace") as f:
                tail = "".join(f.readlines()[-6:])
        except OSError:
            pass
        return {"installed": os.path.isfile(PY), "ready": self.ready(), "busy": self.busy, "progress": self.progress,
                "gpu": self.state.get("gpu", ""), "vram_gb": self.state.get("vram_gb"), "error": self.state.get("error", ""),
                "ms_per_frame_360p": self.state.get("ms_per_frame_360p"), "checked": self.state.get("checked"),
                "log": tail[-1200:]}

    # ------------------------------------------------------------------ install / check
    def install(self):
        if self.busy:
            raise RuntimeError("already " + self.busy)
        self.busy, self.progress = "installing", "starting"
        threading.Thread(target=self._install, daemon=True).start()

    def _install(self):
        os.makedirs(ROOT, exist_ok=True)
        try:
            with open(LOG, "w", encoding="utf-8") as lf:
                def step(cmd, label):
                    self.progress = label
                    lf.write(f"\n=== {label}\n")
                    lf.flush()
                    r = subprocess.run(cmd, stdout=lf, stderr=subprocess.STDOUT, creationflags=NOWIN)
                    if r.returncode != 0:
                        raise RuntimeError(f"{label} failed (see {LOG})")
                if not os.path.isfile(PY):
                    step([sys.executable, "-m", "venv", VENV], "creating the NVIDIA environment")
                labels = ["updating pip", "downloading PyTorch for NVIDIA (about 3 GB - this takes a while)",
                          "installing NVIDIA Video Effects"]
                for args, label in zip(PIP_STEPS, labels):
                    step([PY] + args, label)
            self.busy = ""
            self.check()
        except Exception as e:  # noqa: BLE001
            self.state.update(ok=False, error=str(e)[:300])
            self._save()
            self.log(f"[AI enhance] install failed: {e}")
        finally:
            self.busy, self.progress = "", ""

    def check(self):
        """Load the model on the graphics card and time it."""
        self.busy = "checking"
        try:
            if not os.path.isfile(PY):
                raise RuntimeError("not installed yet")
            r = subprocess.run([PY, "-I", WORKER, "--check"], capture_output=True, text=True, timeout=600, creationflags=NOWIN)
            line = [l for l in (r.stdout or "").splitlines() if l.startswith("{")]
            res = json.loads(line[-1]) if line else {"ok": False, "error": (r.stderr or r.stdout or "no answer")[-300:]}
            self.state = {**res, "checked": time.time()}
            self._save()
            self.log("[AI enhance] " + (f"ready on {res.get('gpu')}" if res.get("ok") else f"not working: {res.get('error')}"))
            return res
        finally:
            self.busy = ""

    # ------------------------------------------------------------------ run
    def enhance(self, src, out, scale=2, quality="HIGH", progress=lambda p: None, timeout=3600):
        """AI-upscale src (video only) to out. Waits for the graphics card if another clip is using it."""
        if not self.ready():
            raise RuntimeError("AI Enhance is not installed - Settings - AI Enhance (NVIDIA)")
        with GPU_LOCK:
            if STT is not None:
                STT.stop()                    # give Whisper's graphics memory back before the big upscale
            p = subprocess.Popen([PY, "-I", "-u", WORKER, "-i", src, "-o", out, "--scale", str(scale), "--quality", quality],
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, creationflags=NOWIN)
            t0, last = time.time(), []
            for line in p.stdout:
                last = (last + [line.rstrip()])[-8:]
                m = re.match(r"PROGRESS (\d+) (\d+)", line)
                if m and int(m.group(2)):
                    progress(min(99, int(int(m.group(1)) * 100 / int(m.group(2)))))
                if time.time() - t0 > timeout:
                    p.kill()
                    raise RuntimeError("AI enhance took too long")
            p.wait()
            if p.returncode != 0 or not os.path.isfile(out):
                raise RuntimeError("AI enhance failed: " + " | ".join(last)[-300:])
            return out


# ====================================================================== Whisper on the graphics card
class GpuWhisper:
    """Whisper large-v3 on the NVIDIA card in Chipper's NVIDIA environment. One long-running helper process keeps
    the model loaded between clips and quits after a few idle minutes to give the graphics memory back."""
    IDLE = 240

    def __init__(self, vsr, log=print):
        self.vsr, self.log = vsr, log
        self.proc = None
        self.lock = threading.Lock()
        self.last = 0
        self.busy = ""
        self.state = vsr.state.get("stt") or {}
        threading.Thread(target=self._idle_loop, daemon=True).start()

    def installed(self):
        return bool(self.state.get("ok")) and os.path.isfile(PY)

    def public(self):
        return {"installed": self.installed(), "busy": self.busy, "error": self.state.get("error", ""),
                "running": bool(self.proc and self.proc.poll() is None)}

    def _save(self):
        self.vsr.state["stt"] = self.state
        self.vsr._save()

    def install(self):
        if self.busy or self.vsr.busy:
            raise RuntimeError("busy - try again in a moment")
        if not os.path.isfile(PY):
            raise RuntimeError("install AI Enhance first (it sets up the NVIDIA environment)")
        self.busy = "installing"
        threading.Thread(target=self._install, daemon=True).start()

    def _install(self):
        try:
            with open(LOG, "a", encoding="utf-8") as lf:
                lf.write("\n=== installing Whisper for the graphics card\n")
                lf.flush()
                r = subprocess.run([PY, "-m", "pip", "install", "openai-whisper"], stdout=lf, stderr=subprocess.STDOUT,
                                   creationflags=NOWIN)
                if r.returncode != 0:
                    raise RuntimeError("pip install openai-whisper failed (see " + LOG + ")")
                self.busy = "downloading the large-v3 speech model (about 3 GB)"
                lf.write("\n=== downloading Whisper large-v3\n")
                lf.flush()
            res = self._call({"cmd": "load", "model": "large-v3", "root": STT_MODELS}, timeout=3600)
            if not res.get("ok"):
                raise RuntimeError(res.get("error") or "could not load the model")
            self.state = {"ok": True, "at": time.time()}
            self.log("[GPU captions] Whisper large-v3 ready on the graphics card")
        except Exception as e:  # noqa: BLE001
            self.state = {"ok": False, "error": str(e)[:300]}
            self.log(f"[GPU captions] install failed: {e}")
        finally:
            self._save()
            self.busy = ""

    def _start(self):
        if self.proc and self.proc.poll() is None:
            return
        env = dict(os.environ, PYTHONIOENCODING="utf-8", PYTHONUTF8="1")
        self.proc = subprocess.Popen([PY, "-I", "-u", STT_WORKER], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.DEVNULL, text=True, encoding="utf-8", env=env, creationflags=NOWIN)

    def _call(self, req, timeout=900):
        with self.lock:
            self._start()
            self.last = time.time()
            box = {}

            def read():
                try:
                    box["line"] = self.proc.stdout.readline()
                except Exception as e:  # noqa: BLE001
                    box["err"] = str(e)
            self.proc.stdin.write(json.dumps(req, ensure_ascii=False) + "\n")
            self.proc.stdin.flush()
            t = threading.Thread(target=read, daemon=True)
            t.start()
            t.join(timeout)
            self.last = time.time()
            if t.is_alive() or not box.get("line"):
                self.stop()
                return {"ok": False, "error": box.get("err") or "the speech helper stopped or took too long"}
            return json.loads(box["line"])

    def stop(self):
        p, self.proc = self.proc, None
        if p and p.poll() is None:
            try:
                p.kill()
            except OSError:
                pass

    def _idle_loop(self):
        while True:
            time.sleep(20)
            if self.proc and self.proc.poll() is None and not self.lock.locked() and time.time() - self.last > self.IDLE:
                self.stop()

    def transcribe(self, audio, lang=None, prompt=None, model=None):
        """-> (words, detected language) or (None, error). Skips when AI Enhance is using the graphics card."""
        if not self.installed():
            return None, "GPU captions are not installed"
        if not GPU_LOCK.acquire(blocking=False):
            return None, "the graphics card is busy with AI Enhance"
        try:
            res = self._call({"cmd": "transcribe", "audio": audio, "lang": lang, "prompt": prompt,
                              "model": model or "large-v3", "root": STT_MODELS})
        finally:
            GPU_LOCK.release()
        if not res.get("ok"):
            return None, res.get("error") or "failed"
        return [tuple(w) for w in res.get("words") or []], res.get("language")
