#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
render_preview.py — 静帧预览成片（无视频生成 API 时的全链产出）

每镜视觉资产解析优先级：
  1) shot.visual_asset
  2) input/keyframe-{SHOT_ID}*
  3) 按 scene_id：master-{scene}* / {scene}-keyframe* / scene_{scene}*
  4) 无匹配时再按时间把 scene_* 均匀分配
  5) 最后才回退 protagonist（日志会写明 via=，避免静默全用主角）
  → Ken Burns → duration_fit → concat → 母带重贴。
窗口已帧吸附（storyboard_build），故贴合步为恒等操作，合计严格=全曲长。

这不是正式成片：口型镜在预览里同样无口型，仅用于验证时间轴/分镜/对齐链路。
正式成片仍需步8接视频生成 API。

用法:
  python render_preview.py <project_dir> [--fps 24] [--width W --height H]
      [--output-name mv_preview.mp4]
产出: output/mv_preview.mp4 + output/verification_report.json
"""
import argparse
import json
import math
import subprocess
import sys
from pathlib import Path

try:
    from .audio_master import resolve_master, master_encoding_args
except ImportError:
    from audio_master import resolve_master, master_encoding_args

PRESETS = [  # Ken Burns 预设轮转: (zoom表达式模板, x, y)
    ("1+0.10*on/{D}", "(iw-iw/zoom)/2", "(ih-ih/zoom)/2"),          # 推近
    ("1.10-0.10*on/{D}", "(iw-iw/zoom)/2", "(ih-ih/zoom)/2"),       # 拉远
    ("1.08", "(iw-iw/zoom)*on/{D}", "(ih-ih/zoom)/2"),              # 左→右
    ("1.08", "(iw-iw/zoom)/2", "(ih-ih/zoom)*on/{D}"),              # 上→下
]
AUDIO_EXTENSIONS = {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg"}
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"cmd failed ({r.returncode}):\n  CMD: {subprocess.list2cmdline(cmd)}\n"
                           f"  ERR: {r.stderr.strip()[-400:]}")


def resolve_audio(proj: Path, timeline: dict) -> Path:
    return resolve_master(proj, timeline)



def list_input_images(proj: Path) -> list[Path]:
    input_dir = proj / "input"
    if not input_dir.is_dir():
        return []
    return sorted(
        p for p in input_dir.iterdir()
        if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS
    )


def _first_stem_prefix(images: list[Path], *prefixes: str) -> Path | None:
    lowered = [(p, p.stem.lower()) for p in images]
    for prefix in prefixes:
        prefix = prefix.lower()
        hits = [p for p, stem in lowered if stem.startswith(prefix)]
        if hits:
            return sorted(hits, key=lambda p: p.name.lower())[0]
    return None


def resolve_visual_asset(proj: Path, shot: dict, index: int, total: int,
                         scene_assets: list, fallback: Path | None,
                         images: list[Path] | None = None):
    """按优先级解析预览静帧；返回 (path_or_None, via_reason)。

    兼容旧调用：仅关心 path 时可取返回值[0]。scene_assets 时间分配保留为
    无逐镜/场景匹配时的回退。
    """
    images = list(images) if images is not None else list_input_images(proj)
    shot_id = str(shot.get("id") or "").strip()
    scene_id = str(shot.get("scene_id") or "").strip()

    declared = shot.get("visual_asset")
    if declared:
        candidate = Path(declared)
        candidate = candidate if candidate.is_absolute() else proj / candidate
        if not candidate.is_file():
            raise FileNotFoundError(f"{shot_id or 'shot'} visual_asset 不存在: {candidate}")
        return candidate, "visual_asset"

    if shot_id:
        keyframe = _first_stem_prefix(images, f"keyframe-{shot_id}")
        if keyframe is not None:
            return keyframe, f"shot_keyframe:{keyframe.name}"

    if scene_id:
        scene_match = _first_stem_prefix(
            images,
            f"master-{scene_id}",
            f"{scene_id}-keyframe",
            f"scene_{scene_id}",
        )
        if scene_match is not None:
            return scene_match, f"scene_match:{scene_match.name}"

    if scene_assets:
        phase = min(len(scene_assets) - 1, index * len(scene_assets) // max(1, total))
        chosen = scene_assets[phase]
        return chosen, f"scene_time_bucket:{chosen.name}"

    if fallback is not None:
        return fallback, f"fallback:{fallback.name}"
    return None, "color_placeholder"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project_dir")
    ap.add_argument("--fps", type=float, default=24)
    ap.add_argument("--width", type=int)
    ap.add_argument("--height", type=int)
    ap.add_argument("--output-name", default="mv_preview.mp4")
    a = ap.parse_args()
    proj = Path(a.project_dir)
    sb = json.loads((proj / "state" / "storyboard.json").read_text(encoding="utf-8"))
    timeline = json.loads((proj / "state" / "timeline.json").read_text(encoding="utf-8"))
    if (a.width is None) != (a.height is None):
        raise ValueError("--width 与 --height 必须同时提供")
    if a.width is None:
        style = json.loads((proj / "state" / "style.json").read_text(encoding="utf-8"))
        raw_resolution = str(style.get("resolution", "")).lower().replace("×", "x")
        try:
            a.width, a.height = (int(value.strip())
                                 for value in raw_resolution.split("x", 1))
        except (ValueError, TypeError):
            raise ValueError("style.resolution 必须为 WIDTHxHEIGHT，或显式传入 --width/--height")
    shots = sb.get("shots", [])
    if not shots:
        raise ValueError("storyboard.shots 不能为空")
    if a.fps <= 0 or a.width <= 0 or a.height <= 0:
        raise ValueError("--fps/--width/--height 必须大于 0")
    if Path(a.output_name).name != a.output_name or Path(a.output_name).suffix.lower() != ".mp4":
        raise ValueError("--output-name 必须是当前 output/ 下的 .mp4 文件名")
    # BUG-15 修法：全曲通常不是整数帧（48.848958s×24=1172.375），末镜按 ceil 会多出
    # 0.375 帧。改为末镜吸收残差：D_last = ⌊全曲×fps⌋ − Σ(前面各镜 D)，总帧数=⌊全曲×fps⌋
    song_dur = shots[-1]["window"]["end"]
    total_frames = int(math.floor(song_dur * a.fps))
    Ds = [int(math.ceil((s["window"]["end"] - s["window"]["start"]) * a.fps - 1e-4))
          for s in shots[:-1]]
    last_frames = total_frames - sum(Ds)
    if last_frames < 1:
        raise ValueError("分镜窗口换算后的总帧数无效，请先重新运行 storyboard_build.py")
    Ds.append(last_frames)
    images = list_input_images(proj)
    protagonist = sorted(p for p in images if p.stem.lower() == "protagonist")
    img = protagonist[0] if protagonist else None
    scene_assets = sorted(p for p in images if p.stem.lower().startswith("scene_"))
    if img is None and not scene_assets and images:
        # 无主角、无 scene_* 时仍保留一张底图，避免整链中断；via= 会标明 fallback
        img = images[0]
    audio = resolve_audio(proj, timeline)
    clips = proj / "clips"
    out_dir = proj / "output"
    clips.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    report = []
    for i, s in enumerate(shots):
        w = s["window"]
        dur = w["end"] - w["start"]
        D = Ds[i]
        zt, xt, yt = PRESETS[i % len(PRESETS)]
        shot_img, via = resolve_visual_asset(
            proj, s, i, len(shots), scene_assets, img, images=images,
        )
        print(f"[render] {s['id']} D={D} preset={i % len(PRESETS)} "
              f"asset={shot_img.name if shot_img else 'color'} via={via}", flush=True)
        # BUG-10 修法：三个表达式都要做 {D} 替换（此前只替换了 z，x/y 的 {D} 原样进 ffmpeg）
        zt, xt, yt = (e.format(D=D) for e in (zt, xt, yt))
        # 参考图只锁人物，不得为适配画布而改变五官与服装比例。先做等比前景，
        # 再用同图的模糊放大副本填满画布，最后才施加 Ken Burns 运镜。
        canvas_w, canvas_h = a.width * 2, a.height * 2
        reference_filter = (
            f"[0:v]split=2[bg0][fg0];"
            f"[bg0]scale={canvas_w}:{canvas_h}:force_original_aspect_ratio=increase,"
            f"crop={canvas_w}:{canvas_h},boxblur=30:10[bg];"
            f"[fg0]scale={canvas_w}:{canvas_h}:force_original_aspect_ratio=decrease[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2,"
            f"zoompan=z='{zt}':x='{xt}':y='{yt}':"
            f"d={D}:s={a.width}x{a.height}:fps={a.fps}[v]"
        )
        raw = clips / f"{s['id']}_raw.mp4"
        fit = clips / f"{s['id']}.mp4"
        if shot_img:
            render_cmd = ["ffmpeg", "-y", "-v", "error", "-loop", "1", "-i", str(shot_img),
                          "-filter_complex", reference_filter, "-map", "[v]"]
        else:
            # 无主角模式仍要能跑通预览链路，用中性底图代替不存在的 protagonist.*。
            render_cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                          f"color=c=0x101018:s={a.width}x{a.height}:r={a.fps}"]
        run(render_cmd + ["-frames:v", str(D), "-c:v", "libx264", "-preset", "fast",
                          "-crf", "20", "-pix_fmt", "yuv420p", str(raw)])
        # 时长贴合：以帧数换算的目标秒传入（D 帧精确），窗口已帧吸附时走 copy 快路径
        r = subprocess.run([sys.executable, str(Path(__file__).parent / "duration_fit.py"),
                            str(raw), str(fit), f"{D / a.fps:.9f}", "--fps", str(a.fps)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"duration_fit failed for {s['id']}: {r.stderr}")
        info = json.loads(r.stdout)
        report.append({"shot_id": s["id"], "target": round(dur, 3), **info})

    # concat
    # BUG-16 修法：ffmpeg 的 concat demuxer 按「列表文件所在目录」解析相对条目，
    # 而条目此前按 cwd 相对书写，视 cwd 偶合而偶发通过；条目与列表一律绝对路径。
    lst = clips / "concat.txt"
    lst.write_text("".join(f"file '{(clips / (s['id'] + '.mp4')).resolve()}'\n"
                           for s in shots), encoding="utf-8")
    output_stem = Path(a.output_name).stem
    silent = out_dir / f".{output_stem}_silent.mp4"
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst.resolve()),
         "-c", "copy", str(silent)])
    # 母带重贴
    final = out_dir / a.output_name
    run(["ffmpeg", "-y", "-v", "error", "-i", str(silent), "-i", str(audio),
         "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", *master_encoding_args(audio),
         str(final)])

    # 生成真实候选音轨与母带的对齐报告，而不是把母带和自己比较。
    reference_wav = out_dir / ".verify_reference.wav"
    candidate_wav = out_dir / ".verify_candidate.wav"
    report_path = (out_dir / "verification_report.json" if a.output_name == "mv_preview.mp4"
                   else out_dir / f"verification_report_{output_stem}.json")
    try:
        run(["ffmpeg", "-y", "-v", "error", "-i", str(audio), "-ac", "1", "-ar", "16000",
             "-c:a", "pcm_s16le", str(reference_wav)])
        run(["ffmpeg", "-y", "-v", "error", "-i", str(final), "-map", "0:a:0", "-ac", "1",
             "-ar", "16000", "-c:a", "pcm_s16le", str(candidate_wav)])
        verify = subprocess.run(
            [sys.executable, str(Path(__file__).parent / "align_verify.py"),
             str(reference_wav), str(proj / "state" / "storyboard.json"),
             str(proj / "state" / "timeline.json"), "--candidate-wav", str(candidate_wav),
             "--candidate-video", str(final),
             "--out", str(report_path)], capture_output=True, text=True)
        verification = "PASS" if verify.returncode == 0 else "FAIL"
    finally:
        reference_wav.unlink(missing_ok=True)
        candidate_wav.unlink(missing_ok=True)

    total = sum(r["actual"] for r in report)
    print(json.dumps({"final": str(final), "verification_report": str(report_path),
                      "master_audio": str(audio), "audio_encoding": master_encoding_args(audio),
                      "verification": verification, "clips": len(report),
                      "sum_actual": round(total, 3),
                      "max_delta": round(max(abs(r["delta"]) for r in report), 4)},
                     ensure_ascii=False))
    if verification != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
