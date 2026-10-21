#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
timeline_fallback.py — 无 ASR key 时的降级对齐器（均匀分布）

机制：以首/末能量起始点为活动区间，官方歌词逐行均匀铺满，全部行标
  confidence=0.30 + pending_review（用户是词作者时可肉眼微调后点头）。
  首尾剩余区间标 interlude（intro/outro）。

这不是权威对齐——产出的时间轴只能用于流程试跑与静帧预览成片；
口型驱动的正式成片必须换回 ASR 通路重跑关 1。

用法:
  python timeline_fallback.py <song_16k.wav> <lyrics.txt> <out_timeline.json> [--intro 2.0] [--outro 2.0]
"""
import argparse
import json
import math
import wave
from pathlib import Path
try:
    from .pipeline_contracts import atomic_batch
except ImportError:
    from pipeline_contracts import atomic_batch

SR = 16000
HOP = 400
ONSET_RMS = 0.35   # 相对峰值rms的起始判定阈值


def active_span(wav_path: str) -> tuple:
    with wave.open(wav_path) as w:
        if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (SR, 1, 2):
            raise ValueError("需要 16k/单声道/16bit PCM，请先用 ffmpeg 转换")
        data = w.readframes(w.getnframes())
    n = len(data) // 2                                # 帧数=字节数/2（16bit单声道）
    if n == 0:
        raise ValueError("音频为空")
    x = [int.from_bytes(data[2 * i:2 * i + 2], "little", signed=True) for i in range(n)]
    frames = [math.sqrt(sum(v * v for v in x[i:i + HOP]) / len(x[i:i + HOP])) / 32768
              for i in range(0, n, HOP) if x[i:i + HOP]]
    peak = max(frames)
    dur = n / SR
    if peak == 0:
        return 0.0, round(dur, 3), dur
    thr = peak * ONSET_RMS
    first = next((i * HOP / SR for i, e in enumerate(frames) if e > thr), 0.0)
    last_idx = max(i for i, e in enumerate(frames) if e > thr)
    last = last_idx * HOP / SR
    return round(first, 3), round(min(last + HOP / SR, dur), 3), dur


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("song_wav")
    ap.add_argument("lyrics_txt")
    ap.add_argument("out_json")
    ap.add_argument("--intro", type=float, default=2.0)
    ap.add_argument("--outro", type=float, default=2.0)
    ap.add_argument("--song", default="song")
    ap.add_argument("--master-audio", help="用户原始母带路径；分析 WAV 与交付母带必须分离")
    a = ap.parse_args()

    first, last, dur = active_span(a.song_wav)
    lines = [ln.strip() for ln in Path(a.lyrics_txt).read_text(encoding="utf-8").splitlines()
             if ln.strip()]
    if not lines:
        raise ValueError("歌词文件没有非空歌词行")
    if not all(math.isfinite(value) and value >= 0 for value in (a.intro, a.outro)):
        raise ValueError("--intro/--outro 必须是非负有限数")

    # 优先在检测到的活动区间内扣除前后留白；短音频或留白过大时逐级回退，
    # 但绝不为了“每行至少 1 秒”把时间轴写到音频时长之外。
    span0 = max(0.0, min(first + a.intro, dur))
    span1 = max(0.0, min(last - a.outro, dur))
    if span1 <= span0:
        span0, span1 = max(0.0, first), min(last, dur)
    if span1 <= span0:
        span0, span1 = 0.0, dur
    if span1 <= span0:
        raise ValueError("音频没有可用于分配歌词的有效时长")
    step = (span1 - span0) / len(lines)

    try:
        source_audio = Path(a.song_wav).resolve().relative_to(
            Path(a.out_json).resolve().parent.parent).as_posix()
    except ValueError:
        source_audio = str(Path(a.song_wav).resolve())

    tl = {"song": a.song, "source_audio": source_audio, "duration_sec": round(dur, 3),
          "fps_assumed": 24, "asr_engine": "uniform_fallback",
          "lyrics_source": "user_supplied", "authority": "provisional_fallback",
          "production_timing_verified": False,
          "lines": [], "segments": [], "pending_review": []}
    if a.master_audio:
        master = Path(a.master_audio).resolve()
        if not master.is_file():
            raise FileNotFoundError(f"原始母带不存在: {master}")
        try:
            tl["master_audio"] = master.relative_to(Path(a.out_json).resolve().parent.parent).as_posix()
        except ValueError:
            tl["master_audio"] = str(master)
    if span0 > 0.001:
        tl["lines"].append({"index": len(lines) + 1, "type": "interlude", "text": "",
                            "start": 0.0, "end": round(span0, 3),
                            "confidence": 1.0, "corrected": False})
    for k, text in enumerate(lines):
        s, e = span0 + k * step, span0 + (k + 1) * step
        if round(s, 3) >= round(e, 3):
            raise ValueError("歌词行数超过当前毫秒时间精度，拒绝产生零时长窗口")
        tl["lines"].append({"index": k + 1, "type": "vocal", "text": text,
                            "start": round(s, 3), "end": round(e, 3),
                            "confidence": 0.30, "corrected": False})
        tl["pending_review"].append(k + 1)
    if dur - span1 > 0.001:
        tl["lines"].append({"index": len(lines) + 2, "type": "interlude", "text": "",
                            "start": round(span1, 3), "end": round(dur, 3),
                            "confidence": 1.0, "corrected": False})
    if not any(ln["type"] == "interlude" for ln in tl["lines"]):
        tl["no_interlude"] = True

    lrc = "\n".join(f"[{int(ln['start']//60):02d}:{ln['start']%60:05.2f}]{ln['text']}"
                    for ln in tl["lines"] if ln["type"] != "interlude")
    if Path(a.out_json).resolve() == Path(a.out_json).with_suffix(".lrc").resolve():
        raise ValueError("时间轴与LRC输出路径必须不同")
    atomic_batch({Path(a.out_json): json.dumps(tl, ensure_ascii=False, indent=2),
                  Path(a.out_json).with_suffix(".lrc"): lrc + "\n"})
    print(json.dumps({"engine": "uniform_fallback", "lines": len(lines),
                      "active_span": [span0, span1], "duration_sec": round(dur, 3),
                      "pending_review": len(tl["pending_review"])}, ensure_ascii=False))


if __name__ == "__main__":
    main()
