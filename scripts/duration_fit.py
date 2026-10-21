#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
duration_fit.py — 时长贴合：把生成片段精确裁/补到窗口长度（帧网格）

机制：目标时长向上对齐帧网格 ceil(window*fps)/fps，先实测原片时长，
  过长 → -t 精裁；过短 → tpad 克隆末帧补齐。输出无音轨画面轨（音画对齐靠母带重贴）。

用法:
  python duration_fit.py <in.mp4> <out.mp4> <window_sec> [--fps 24]
退出码: 0 成功；非 0 = ffmpeg/ffprobe 失败
stdout: {"in": ..., "target": ..., "actual": ..., "delta": ...}
"""
import argparse
import json
import math
import subprocess
import sys
from pathlib import Path


def ffprobe_duration(path: str, fps: float = None) -> float:
    """优先用帧数×帧距计量（BUG-12 修法：mp4 容器时长会把末帧时基多报 1 帧，
    造成 +1帧/镜的测量假象；concat 后帧数严丝合缝，假数会污染台账）。"""
    if fps:
        try:
            from .media_verify import probe, ratio, is_cfr
        except ImportError:
            from media_verify import probe, ratio, is_cfr
        stream = next(s for s in probe(path)["streams"] if s["codec_type"] == "video")
        source_fps = ratio(stream["avg_frame_rate"])
        if ("nb_read_frames" in stream and abs(source_fps-ratio(stream["r_frame_rate"]))<1e-6
                and is_cfr(path, source_fps)):
            return int(stream["nb_read_frames"])/source_fps
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True)
    if out.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {out.stderr.strip()}")
    return float(out.stdout.strip())


def fit(inp: str, outp: str, window: float, fps: float) -> dict:
    if not math.isfinite(fps) or not math.isfinite(window) or fps <= 0 or window <= 0:
        raise ValueError("window_sec 和 --fps 必须大于 0")
    if Path(inp).resolve() == Path(outp).resolve():
        raise ValueError("input 与 output 不能是同一路径")
    Path(outp).parent.mkdir(parents=True, exist_ok=True)
    # BUG-11 修法(修订)：护栏 1e-4——须大于 10 位小数存储误差(≈2.4e-9*fps)，
    # 又须远小于半帧(24fps 半帧=0.021s)，才能同时挡住噪声与保住真小数窗口
    target = math.ceil(window * fps - 1e-4) / fps
    actual_in = ffprobe_duration(inp, fps)
    try:
        from .media_verify import probe, ratio, is_cfr
    except ImportError:
        from media_verify import probe, ratio, is_cfr
    stream = next(s for s in probe(inp)["streams"] if s["codec_type"] == "video")
    same_fps = (abs(ratio(stream["avg_frame_rate"])-fps)<1e-6
                and abs(ratio(stream["r_frame_rate"])-fps)<1e-6 and is_cfr(inp, fps))
    # BUG-13 修法：帧数已精确等于目标时直接流复制——重编码路径的 fps+tpad+-t
    # 组合会在边界帧多塞 1 帧（实测每镜 +0.042s），破坏"合计=全曲长"
    if same_fps and round(actual_in * fps) == round(target * fps):
        # 即使帧数已吻合，也必须去掉输入片段可能携带的音轨；直接 copyfile 会违背
        # “输出无音轨画面轨”的契约，并在最终母带重贴时制造双音轨。
        r = subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", inp,
                            "-map", "0:v:0", "-c:v", "copy", "-an", outp],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"ffmpeg stream copy failed: {r.stderr.strip()[-400:]}")
        return {"in": actual_in, "target": round(target, 3),
                "actual": round(ffprobe_duration(outp, fps), 3),
                "delta": round(actual_in - window, 3),
                "mode": "copy"}
    vf = f"fps={fps},tpad=stop_mode=clone:stop_duration={target + 1:.3f}"
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", inp,
           "-vf", vf, "-t", f"{target:.3f}", "-an",
           "-c:v", "libx264", "-preset", "fast", "-crf", "18", outp]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {r.stderr.strip()[-400:]}")
    actual_out = ffprobe_duration(outp, fps)
    if abs(actual_out - target) > 1.0 / fps + 0.01:
        raise RuntimeError(f"fit drift: out={actual_out:.3f} target={target:.3f}")
    return {"in": actual_in, "target": round(target, 3),
            "actual": round(actual_out, 3), "delta": round(actual_out - window, 3)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("output")
    ap.add_argument("window_sec", type=float)
    ap.add_argument("--fps", type=float, default=24)
    a = ap.parse_args()
    try:
        print(json.dumps(fit(a.input, a.output, a.window_sec, a.fps), ensure_ascii=False))
    except Exception as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
        sys.exit(1)


if __name__ == "__main__":
    main()
