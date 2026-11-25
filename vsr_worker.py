"""Chipper Studio - NVIDIA RTX Video Super Resolution worker.

Runs inside Chipper's own NVIDIA environment (D:\\Clipping\\nvvfx\\venv), not the main Python, because it needs
PyTorch (CUDA) and NVIDIA's nvidia-vfx package. Based on NVIDIA's MIT-licensed nvidia-vfx-python-samples.

  python vsr_worker.py --check
  python vsr_worker.py -i in.mp4 -o out.mp4 --scale 2 --quality HIGH

Prints "PROGRESS done total" lines so Chipper can show a percentage. Video only - Chipper adds the audio back.
"""
import argparse
import json
import os
import sys
import time
from fractions import Fraction

# never import from Chipper's own folder: NVIDIA's package is also called "nvvfx"
_here = os.path.dirname(os.path.abspath(__file__))
sys.path[:] = [p for p in sys.path if p and os.path.abspath(p) != _here]


def check():
    out = {"ok": False}
    try:
        import torch
        out["torch"] = torch.__version__
        out["cuda"] = bool(torch.cuda.is_available())
        if out["cuda"]:
            out["gpu"] = torch.cuda.get_device_name(0)
            out["vram_gb"] = round(torch.cuda.get_device_properties(0).total_memory / 2 ** 30, 1)
        from nvvfx import VideoSuperRes
        sr = VideoSuperRes(device=0, quality=VideoSuperRes.QualityLevel["HIGH"], strength=1.0,
                           image_encoding=VideoSuperRes.ImageEncoding.RGB8)
        sr.input_width, sr.input_height, sr.output_width, sr.output_height = 640, 360, 1280, 720
        sr.load()
        out["model"] = bool(sr.is_loaded)
        x = torch.rand((3, 360, 640), device="cuda:0", dtype=torch.float32)
        t0 = time.time()
        for _ in range(5):
            sr.run(x, stream_ptr=torch.cuda.current_stream().cuda_stream)
        torch.cuda.synchronize()
        out["ms_per_frame_360p"] = round((time.time() - t0) / 5 * 1000, 1)
        sr.close()
        out["ok"] = out["model"]
    except Exception as e:  # noqa: BLE001
        out["error"] = f"{type(e).__name__}: {e}"[:400]
    print(json.dumps(out), flush=True)
    return 0 if out["ok"] else 1


def run(args):
    import av
    import numpy as np
    import torch
    from nvvfx import VideoSuperRes

    gpu = 0
    torch.cuda.set_device(gpu)
    stream_ptr = torch.cuda.current_stream().cuda_stream
    src = av.open(args.input)
    vs = src.streams.video[0]
    vs.thread_type = "AUTO"
    w, h = vs.codec_context.width, vs.codec_context.height
    total = vs.frames or 0
    if not total and vs.duration and vs.time_base:
        total = int(float(vs.duration * vs.time_base) * float(vs.average_rate or 30))
    ow, oh = w * args.scale, h * args.scale
    sr = VideoSuperRes(device=gpu, quality=VideoSuperRes.QualityLevel[args.quality], strength=args.strength,
                       image_encoding=VideoSuperRes.ImageEncoding.RGB8)
    sr.input_width, sr.input_height, sr.output_width, sr.output_height = w, h, ow, oh
    sr.load()

    rate = Fraction(vs.average_rate or 30).limit_denominator(10000)
    dst = None
    for codec, opts in (("h264_nvenc", {"preset": "p5", "rc": "vbr", "cq": "23", "maxrate": "26M", "bufsize": "52M"}), ("hevc_nvenc", {"preset": "p5"}),
                        ("libx264", {"preset": "veryfast", "crf": "18"})):
        c = av.open(args.output, mode="w")
        try:
            st = c.add_stream(codec, rate=rate)
            st.width, st.height, st.pix_fmt = ow, oh, "yuv420p"
            st.bit_rate = args.bitrate
            st.options = opts
            st.codec_context.open()
        except Exception:  # noqa: BLE001
            c.close()
            continue
        dst, out_stream = c, st
        break
    if dst is None:
        print("ERROR no video encoder available", flush=True)
        return 2
    print(f"START {w}x{h} -> {ow}x{oh} frames={total} encoder={out_stream.codec_context.name}", flush=True)
    t0, n = time.time(), 0
    try:
        for frame in src.decode(vs):
            arr = frame.to_ndarray(format="rgb24")
            x = torch.from_numpy(np.ascontiguousarray(arr)).to(f"cuda:{gpu}").permute(2, 0, 1).float().div_(255.0).contiguous()
            res = sr.run(x, stream_ptr=stream_ptr)
            y = torch.from_dlpack(res.image)
            img = (y.clamp(0.0, 1.0) * 255.0).round().to(torch.uint8).permute(1, 2, 0).contiguous().cpu().numpy()
            of = av.VideoFrame.from_ndarray(img, format="rgb24")
            for p in out_stream.encode(of):
                dst.mux(p)
            n += 1
            if n % 15 == 0:
                print(f"PROGRESS {n} {total} {n / max(0.001, time.time() - t0):.1f}fps", flush=True)
        for p in out_stream.encode(None):
            dst.mux(p)
    finally:
        dst.close()
        src.close()
        sr.close()
    print(f"DONE {n} frames in {time.time() - t0:.1f}s", flush=True)
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("-i", "--input")
    ap.add_argument("-o", "--output")
    ap.add_argument("--scale", type=int, default=2, choices=[1, 2, 3, 4])
    ap.add_argument("--quality", default="HIGH")
    ap.add_argument("--strength", type=float, default=1.0)
    ap.add_argument("--bitrate", type=int, default=18_000_000)
    a = ap.parse_args()
    if a.check:
        return check()
    if not a.input or not a.output:
        ap.error("-i and -o are needed")
    return run(a)


if __name__ == "__main__":
    sys.exit(main())
