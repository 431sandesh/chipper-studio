"""Chipper Studio - Whisper speech-to-text on the NVIDIA graphics card.

Runs inside Chipper's NVIDIA environment (the same one AI Enhance uses, which already has PyTorch + CUDA).
Stays running and keeps the model loaded, reading one JSON request per line on stdin and answering one JSON line:

  {"cmd": "transcribe", "audio": "x.wav", "model": "large-v3", "lang": "hi", "prompt": "...", "root": "models dir"}
  {"cmd": "load", "model": "large-v3", "root": "..."}     (download/load only)
  {"cmd": "ping"}
"""
import json
import os
import sys

_here = os.path.dirname(os.path.abspath(__file__))
sys.path[:] = [p for p in sys.path if p and os.path.abspath(p) != _here]

MODELS = {}


def out(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def model(name, root):
    import torch
    import whisper
    if name not in MODELS:
        MODELS.clear()
        torch.cuda.empty_cache()
        MODELS[name] = whisper.load_model(name, device="cuda", download_root=root)
    return MODELS[name]


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            cmd = req.get("cmd")
            if cmd == "ping":
                import torch
                out({"ok": True, "cuda": torch.cuda.is_available()})
            elif cmd == "load":
                model(req["model"], req.get("root"))
                out({"ok": True})
            elif cmd == "transcribe":
                m = model(req.get("model") or "large-v3", req.get("root"))
                r = m.transcribe(req["audio"], language=req.get("lang") or None, task="transcribe",
                                 word_timestamps=True, fp16=True, condition_on_previous_text=False,
                                 initial_prompt=req.get("prompt") or None, temperature=(0.0, 0.2, 0.4),
                                 no_speech_threshold=0.6)
                words = [[round(float(w["start"]), 3), round(float(w["end"]), 3), w["word"].strip()]
                         for s in r.get("segments", []) for w in s.get("words", []) if w.get("word", "").strip()]
                out({"ok": True, "words": words, "language": r.get("language")})
            elif cmd == "quit":
                out({"ok": True})
                return 0
            else:
                out({"ok": False, "error": "unknown command"})
        except Exception as e:  # noqa: BLE001
            out({"ok": False, "error": f"{type(e).__name__}: {e}"[:400]})
    return 0


if __name__ == "__main__":
    sys.exit(main())
