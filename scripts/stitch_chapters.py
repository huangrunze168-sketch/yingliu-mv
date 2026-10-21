#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""拼接任意用户所选后端产出的 CH*_fitted.mp4，规范到交付画布并重贴母带。"""
import argparse
import json
import subprocess
import sys
import tempfile
from contextlib import nullcontext
from pathlib import Path

try:
    from .director_review_gate import approved
    from .audio_master import resolve_master, master_encoding_args
    from .visual_development import fingerprint
except ImportError:  # 直接执行脚本时兼容
    from director_review_gate import approved
    from audio_master import resolve_master, master_encoding_args
    from visual_development import fingerprint


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def run(command: list[str]) -> None:
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8",
                            errors="replace")
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "ffmpeg 执行失败")


def probe_duration(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=True)
    return float(result.stdout.strip())


def resolve_canvas(project: Path, width: int | None, height: int | None) -> tuple[int, int]:
    if (width is None) != (height is None):
        raise ValueError("--width 与 --height 必须同时提供")
    if width is not None and height is not None:
        if width <= 0 or height <= 0:
            raise ValueError("交付画布尺寸必须大于 0")
        return width, height
    style = json.loads((project / "state" / "style.json").read_text(encoding="utf-8"))
    raw = str(style.get("resolution", "")).lower().replace("×", "x")
    try:
        parsed_width, parsed_height = (int(value.strip()) for value in raw.split("x", 1))
    except (ValueError, TypeError):
        raise ValueError("style.resolution 必须为 WIDTHxHEIGHT，或显式传入 --width/--height")
    if parsed_width <= 0 or parsed_height <= 0:
        raise ValueError("style.resolution 必须为正整数画布")
    return parsed_width, parsed_height


def publish_preview(project, staged, output, verification, expected):
    """成功预览和报告同批提交；失败或旧版本任务不能破坏旧产物。"""
    try:
        from .pipeline_contracts import atomic_batch
    except ImportError:
        from pipeline_contracts import atomic_batch
    output = Path(output).resolve()
    output.relative_to((Path(project).resolve() / 'output').resolve())
    if verification.get('status') != 'pass' or verification.get('errors') != []:
        raise ValueError('技术预览验收失败，不发布或覆盖旧成功预览')
    atomic_batch({output: Path(staged).read_bytes(),
                  output.with_suffix('.verification.json'): json.dumps(verification, ensure_ascii=False, indent=2)},
                 expected=expected)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("project_dir")
    ap.add_argument("--width", type=int)
    ap.add_argument("--height", type=int)
    ap.add_argument("--output-name", default="final_mv.mp4")
    ap.add_argument("--allow-unreviewed-preview", action="store_true",
                    help="仅输出技术预览；不会被认定为正式成片")
    args = ap.parse_args()
    project = Path(args.project_dir).resolve()
    try:
        from .delivery_state import record_path
    except ImportError:
        from delivery_state import record_path
    # 技术预览同样禁止路径型文件名；须在创建目录或渲染前检查。
    record_path(project, args.output_name)
    preview_expected = {}
    if args.allow_unreviewed_preview:
        paths = [project / 'state' / f'{name}.json' for name in (
            'timeline', 'style', 'storyboard', 'chapter_plan', 'edit_assembly', 'edit_selection', 'generated_clips')]
        preview_output = project / 'output' / args.output_name
        paths.extend([preview_output, preview_output.with_suffix('.verification.json')])
        preview_expected = {path: fingerprint(path) if path.is_file() else None for path in paths}
    try:
        from .pipeline_contracts import require_no_transaction
    except ImportError:
        from pipeline_contracts import require_no_transaction
    require_no_transaction(project)
    if not args.allow_unreviewed_preview:
        try:
            from .production_preflight import require_current
        except ImportError:
            from production_preflight import require_current
        require_current(project)
    width, height = resolve_canvas(project, args.width, args.height)
    try:
        from .director_review_gate import delivery_approved
        from .pipeline_contracts import supported_transitions, number
        from .media_verify import verify_delivery
    except ImportError:
        from director_review_gate import delivery_approved
        from pipeline_contracts import supported_transitions, number
        from media_verify import verify_delivery
    style = json.loads((project / "state/style.json").read_text(encoding="utf-8"))
    left, right = map(float, style["aspect_ratio"].split(":"))
    if abs(width/height-left/right)>1e-6:
        raise ValueError("交付画布比例与style.aspect_ratio不一致")
    storyboard = json.loads((project / "state/storyboard.json").read_text(encoding="utf-8"))
    if not args.allow_unreviewed_preview:
        try:
            from .visual_development import require
            from .chapter_plan_build import require_production_timing
        except ImportError:
            from visual_development import require
            from chapter_plan_build import require_production_timing
        require(project, storyboard, style)
        require_production_timing(json.loads((project / "state/timeline.json").read_text(encoding="utf-8")), storyboard['shots'])
    fps = number(storyboard.get("fps"))
    if fps <= 0:
        raise ValueError("项目fps必须为正向有限数")
    failures = supported_transitions(storyboard.get("shots", []))
    if failures:
        raise ValueError("; ".join(failures))
    review_ok, review_errors = approved(project)
    if not review_ok and not args.allow_unreviewed_preview:
        raise ValueError("导演样片审片未通过，禁止合成为正式成片: " + "; ".join(review_errors))
    if args.allow_unreviewed_preview and "preview" not in args.output_name.lower():
        raise ValueError("未审片产物的 output-name 必须包含 preview")
    if not args.allow_unreviewed_preview:
        ok, failures = delivery_approved(project)
        if not ok:
            raise ValueError("正式交付须完成逐章与全片审片: " + "; ".join(failures))
    plan = json.loads((project / "state" / "chapter_plan.json").read_text(encoding="utf-8"))
    assembly_path = project / "state" / "edit_assembly.json"
    if assembly_path.is_file():
        assembly = json.loads(assembly_path.read_text(encoding="utf-8"))
        try:
            from .edit_assemble import validate_assembly
        except ImportError:
            from edit_assemble import validate_assembly
        validate_assembly(storyboard,
            json.loads((project / 'state/generated_clips.json').read_text(encoding='utf-8'))['clips'],
            json.loads((project / 'state/edit_selection.json').read_text(encoding='utf-8'))['selections'], assembly)
        if assembly.get("technical_preview") and not args.allow_unreviewed_preview:
            raise ValueError("技术预览剪辑不能标记为正式交付")
        if not args.allow_unreviewed_preview:
            try:
                from .chapter_admission import require as require_chapters
            except ImportError:
                from chapter_admission import require as require_chapters
            origin_versions = {}
            admitted = require_chapters(project, storyboard,
                json.loads((project / 'state/generated_clips.json').read_text(encoding='utf-8'))['clips'],
                json.loads((project / 'state/edit_selection.json').read_text(encoding='utf-8'))['selections'], origin_versions)
            if {str(path): sha for path, sha in origin_versions.items()} != assembly.get('origin_versions'):
                raise ValueError('选段素材来源版本已改变，须重做准入和全片审片')
            if admitted != assembly.get('chapter_approvals'):
                raise ValueError('正式选段使用的章节批准版本已改变，须重做准入和全片审片')
        if (assembly.get("status") != "rendered" or assembly.get("storyboard_sha256") !=
                fingerprint(project / "state" / "storyboard.json") or assembly.get("selection_sha256") !=
                fingerprint(project / "state" / "edit_selection.json")):
            raise ValueError("剪辑素材未完成或分镜/选段已改变，重新运行 edit_assemble --render")
        clips = [project / entry["target_path"] for entry in assembly["entries"]]
        if any(not path.is_file() or fingerprint(path) != entry.get("output_sha256")
               for path, entry in zip(clips, assembly["entries"])):
            raise ValueError("剪辑产物已改变，需重新验收")
    else:
        storyboard_path = project / "state" / "storyboard.json"
        if (storyboard_path.is_file() and json.loads(storyboard_path.read_text(encoding="utf-8")).get(
                "schema_version") == 2 and not args.allow_unreviewed_preview):
            raise ValueError("V1.4 正式成片必须完成素材选段与 edit_assemble --render")
        clips = [project / "clips" / f"{chapter['id']}_fitted.mp4"
                 for chapter in plan.get("chapters", [])]
    missing = [str(path) for path in clips if not path.is_file()]
    if missing:
        raise FileNotFoundError("缺少章节视频: " + ", ".join(missing))

    timeline = json.loads((project / "state" / "timeline.json").read_text(encoding="utf-8"))
    duration = float(timeline["duration_sec"])
    audio = resolve_master(project, timeline)
    audio_args = master_encoding_args(audio)
    try:
        from .sound_mix import require_plan, mux as mux_sound
    except ImportError:
        from sound_mix import require_plan, mux as mux_sound
    sound_plan, sound_versions = require_plan(project, storyboard)
    preview_expected.update(sound_versions)
    if args.allow_unreviewed_preview:
        if assembly_path.is_file():
            for clip, entry in zip(clips, assembly['entries']):
                preview_expected[clip.resolve()] = entry['output_sha256']
        else:
            for clip in clips: preview_expected[clip.resolve()] = fingerprint(clip)
        preview_expected[audio.resolve()] = fingerprint(audio)
    output = project / "output" / args.output_name
    output.parent.mkdir(parents=True, exist_ok=True)
    delivery_record = None
    if not args.allow_unreviewed_preview:
        try:
            from .delivery_state import begin, register_output, finish, rendering_guard
        except ImportError:
            from delivery_state import begin, register_output, finish, rendering_guard
        delivery_record = begin(project, args.output_name, {'master_audio': str(audio.resolve()),
                                                           'duration_sec': duration, 'fps': fps,
                                                           'width': width, 'height': height})

    with (rendering_guard(project, delivery_record) if delivery_record is not None else nullcontext()), tempfile.TemporaryDirectory(dir=project / "output") as tmp:
        tmp_path = Path(tmp)
        concat_file = tmp_path / "chapters.txt"
        concat_file.write_text("\n".join(
            "file '" + str(path).replace("'", "'\\''") + "'" for path in clips
        ) + "\n", encoding="utf-8")
        joined = tmp_path / "joined.mp4"
        if assembly_path.is_file() and any(e.get('video_transition', {}).get('type') == 'dissolve' for e in assembly['entries']):
            try:
                from .transition_render import join_picture
            except ImportError:
                from transition_render import join_picture
            join_picture(project, assembly['entries'], joined, fps, width, height)
        else:
            if any((s.get('crew') or {}).get('editing', {}).get('transition_type') == 'dissolve' for s in storyboard['shots']):
                raise ValueError('叠化必须先重新运行edit_assemble --render，不能沿用旧清单')
            run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0",
                 "-i", str(concat_file), "-an", "-c:v", "copy", str(joined)])
        # 先锁定画面帧数，再单独封装母带；否则 -frames:v 会提前结束 MP3 等音轨的包复制。
        picture = tmp_path / "picture.mp4"
        run(["ffmpeg", "-y", "-v", "error", "-i", str(joined), "-an",
             "-filter:v", (f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                           f"crop={width}:{height}"),
             "-map", "0:v:0", "-frames:v", str(int(duration*fps+1e-6)),
             "-r", str(fps), "-c:v", "libx264", "-preset", "medium", "-crf", "18",
             "-pix_fmt", "yuv420p", str(picture)])
        staged_output = tmp_path / 'export.mp4'
        if sound_plan is not None:
            mux_sound(project, picture, audio, staged_output, tmp_path)
        else:
            run(["ffmpeg", "-y", "-v", "error", "-i", str(picture), "-i", str(audio),
                 "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", *audio_args, str(staged_output)])
        if delivery_record is not None:
            register_output(project, delivery_record, staged_output)
        else:
            verification = verify_delivery(staged_output, audio, duration, fps, width, height, project=project)
            publish_preview(project, staged_output, output, verification, preview_expected)

    if delivery_record is not None:
        verification = finish(project, delivery_record)
    actual = probe_duration(output)
    if verification["errors"]:
        raise ValueError("成片实际媒体验收失败: " + "; ".join(verification["errors"]))
    print(json.dumps({"output": str(output), "chapters": len(clips),
                      "target_duration": duration, "actual_duration": round(actual, 6),
                      "master_audio": str(audio), "audio_encoding": audio_args,
                      "delivery_status": "ready" if delivery_record is not None else "technical_preview",
                      "user_received": False,
                      "canvas": f"{width}x{height}"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
