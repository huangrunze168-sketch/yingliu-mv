#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
align_verify.py — 对齐三验（纯标准库，无 numpy/scipy）

验1  各段时长合计 == 原曲长（差 <0.05s）
验2  逐段波形：在预期位置 ±SEARCH 局部搜索最优滞后，要求 |lag|<0.15s 且相关度>0.6
验3  口型段在窗口内能量非空（全混能量代理；接人声分离 API 后可换 stems）

用法:
  python align_verify.py <song_16k_mono.wav> <storyboard.json> <timeline.json>
      [--candidate-wav final_16k_mono.wav] [--out report.json]
输入 wav 需 16k 单声道 16bit PCM（调用前先: ffmpeg -i song.mp3 -ac 1 -ar 16000 song.wav）。
正式验收应传 --candidate-wav；省略时仅对源音频自身做链路自检。
退出码: 0 = 三验全过；1 = 任一不过
"""
import argparse
import json
import math
import sys
import wave
import hashlib
import subprocess
from pathlib import Path

SR = 16000
HOP = 400                      # 包络帧移 25ms
SEARCH = 5.0                   # 局部搜索 ±5s
LAG_TOL = 0.15
CORR_MIN = 0.6
ENERGY_MIN = 0.05
DUR_TOL = 0.05


def load_wav(path: str):
    with wave.open(path, "rb") as w:
        if (w.getframerate(), w.getnchannels(), w.getsampwidth()) != (SR, 1, 2):
            raise ValueError("需要 16k/单声道/16bit PCM，请先用 ffmpeg 转换")
        data = w.readframes(w.getnframes())
    n = len(data) // 2
    ints = [int.from_bytes(data[2 * i:2 * i + 2], "little", signed=True) for i in range(n)]
    return ints


def envelope(x):
    env = []
    for i in range(0, len(x), HOP):
        chunk = x[i:i + HOP]
        if not chunk:
            continue
        s = 0
        for v in chunk:
            s += v * v
        env.append(math.sqrt(s / len(chunk)) / 32768.0)
    return env


def seg_env(env, start, dur):
    i0 = max(0, int(start / (HOP / SR)))
    i1 = min(len(env), int((start + dur) / (HOP / SR)))
    return env[i0:i1] if i1 > i0 else [0.0]


def corr(a, b):
    if len(a) != len(b) or len(a) < 4:
        return 0.0
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((x - mb) ** 2 for x in b)
    if va < 1e-12 or vb < 1e-12:
        return 0.0
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    return num / math.sqrt(va * vb)


def best_lag(full_env, seg_ref, start):
    """在预期位置附近局部搜索最优滞后（帧级）。seg_ref=整曲包络中期望窗口的片段。"""
    best = (-2.0, 0.0)
    max_shift = int(SEARCH / (HOP / SR))
    i0 = max(0, int(start / (HOP / SR)))
    for sh in range(-max_shift, max_shift + 1, 2):        # 步长2帧=50ms，够判"≈0"
        j0 = i0 + sh
        if j0 < 0 or j0 + len(seg_ref) > len(full_env):
            continue
        c = corr(seg_ref, full_env[j0:j0 + len(seg_ref)])
        if c > best[0]:
            best = (c, sh * (HOP / SR))
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("song_wav")
    ap.add_argument("storyboard_json")
    ap.add_argument("timeline_json")
    ap.add_argument("--out", default=None)
    ap.add_argument("--candidate-wav", default=None,
                    help="待验收成片的 16k 单声道 WAV；省略时仅做源音频自检")
    ap.add_argument("--candidate-video", default=None)
    a = ap.parse_args()

    sb = json.loads(Path(a.storyboard_json).read_text(encoding="utf-8"))
    tl = json.loads(Path(a.timeline_json).read_text(encoding="utf-8"))
    x = load_wav(a.song_wav)
    ref_env = envelope(x)
    candidate_x = load_wav(a.candidate_wav) if a.candidate_wav else x
    candidate_env = envelope(candidate_x)
    source_dur = len(x) / SR
    candidate_dur = len(candidate_x) / SR

    shots = sorted(sb.get("shots", []), key=lambda s: s["window"]["start"])

    # 验1 时长合计
    total = sum(float(s["window"]["end"]) - float(s["window"]["start"]) for s in shots)
    timeline_dur = float(tl["duration_sec"])
    c1 = {"planned_shots_sum": round(total, 3), "song": round(source_dur, 3),
          "timeline": round(timeline_dur, 3), "candidate_audio": round(candidate_dur, 3),
          "diff": round(total - source_dur, 3),
          "timeline_diff": round(timeline_dur - source_dur, 3),
          "candidate_diff": round(candidate_dur - source_dur, 3),
          "pass": (abs(total - source_dur) < DUR_TOL and
                   abs(timeline_dur - source_dur) < DUR_TOL and
                   abs(candidate_dur - source_dur) < DUR_TOL)}

    # 验2 波形滞后 + 验3 能量
    c2, c3 = [], []
    for s in shots:
        w = s["window"]
        dur = float(w["end"]) - float(w["start"])
        ref = seg_env(ref_env, float(w["start"]), dur)
        c, lag = best_lag(candidate_env, ref, float(w["start"]))
        sample_start, sample_end = round(float(w['start'])*SR), round(float(w['end'])*SR)
        exact_samples = (sample_end <= len(x) and sample_end <= len(candidate_x)
                         and sample_end > sample_start
                         and x[sample_start:sample_end] == candidate_x[sample_start:sample_end])
        if exact_samples:
            c, lag = 1.0, 0.0
        ok2 = abs(lag) < LAG_TOL and c > CORR_MIN
        c2.append({"shot_id": s["id"], "expected_start": w["start"],
                   "best_lag": round(lag, 3), "correlation": round(c, 3), "pass": ok2,
                   "method": "exact_pcm_samples" if exact_samples else "envelope_correlation"})
        candidate_segment = seg_env(candidate_env, float(w["start"]), dur)
        e = sum(candidate_segment) / max(1, len(candidate_segment))
        ok3 = (not s.get("lip_sync")) or e > ENERGY_MIN
        c3.append({"shot_id": s["id"], "lip_sync": bool(s.get("lip_sync")),
                   "energy": round(e, 4), "pass": ok3})

    report = {
        "mode": "candidate" if a.candidate_wav else "self_reference",
        "check1_total_duration": c1,
        "check2_waveform_lag": c2,
        "check3_vocal_energy": c3,
        "overall": "PASS" if (c1["pass"] and all(r["pass"] for r in c2)
                              and all(r["pass"] for r in c3)) else "FAIL",
    }
    report["planned_duration"] = total
    report["lip_sync_verified"] = False
    report["energy_scope"] = "full_mix_proxy_not_vocal_or_lips"
    if a.candidate_video:
        try:
            from .media_verify import probe
            from .pipeline_contracts import digest
        except ImportError:
            from media_verify import probe
            from pipeline_contracts import digest
        media = probe(a.candidate_video)
        video = next(s for s in media["streams"] if s["codec_type"] == "video")
        actual_video = float(video.get("duration", media["format"]["duration"]))
        report["actual_video_duration"] = actual_video
        report["candidate_video_sha256"] = digest(a.candidate_video)
        if a.candidate_wav:
            actual_audio = subprocess.run(['ffmpeg', '-v', 'error', '-i', a.candidate_video,
                                          '-map', '0:a:0', '-ac', '1', '-ar', '16000',
                                          '-c:a', 'pcm_s16le', '-f', 's16le', '-'],
                                         capture_output=True, check=True).stdout
            with wave.open(a.candidate_wav, 'rb') as candidate_file:
                declared_audio = candidate_file.readframes(candidate_file.getnframes())
            report['candidate_audio_bound_to_video'] = actual_audio == declared_audio
            report['candidate_audio_pcm_sha256'] = hashlib.sha256(actual_audio).hexdigest()
            if not report['candidate_audio_bound_to_video']:
                report['overall'] = 'FAIL'
        if abs(actual_video-source_dur)>DUR_TOL:
            report["overall"] = "FAIL"
        if not a.candidate_wav:
            report["overall"] = "DIAGNOSTIC_PASS" if report["overall"] == "PASS" else "FAIL"
        report["delivery_verified"] = report["overall"] == "PASS"
    else:
        report["delivery_verified"] = False
        if report["overall"] == "PASS":
            report["overall"] = "AUDIO_ONLY_PASS" if a.candidate_wav else "DIAGNOSTIC_PASS"
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(text, encoding="utf-8")
    print(text)
    sys.exit(0 if report["overall"] in {"PASS", "AUDIO_ONLY_PASS", "DIAGNOSTIC_PASS"} else 1)


if __name__ == "__main__":
    main()
