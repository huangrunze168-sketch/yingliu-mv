"""按视觉镜头选取生成素材；素材时长与最终剪辑时长分离。"""
import argparse
import json
import math
import subprocess
import tempfile
import uuid
import os
from pathlib import Path

try:
    from .visual_development import fingerprint, load
except ImportError:
    from visual_development import fingerprint, load


def compile_selection(storyboard: dict, clips: dict, selections: list) -> list:
    try:
        from .pipeline_contracts import unique_ids, timeline_errors, number
    except ImportError:
        from pipeline_contracts import unique_ids, timeline_errors, number
    if not unique_ids(storyboard.get('shots', [])):
        raise ValueError('视觉分镜ID为空或重复')
    duration = number(storyboard['shots'][-1]['window']['end'])
    failures = timeline_errors(storyboard['shots'], duration)
    if failures: raise ValueError('; '.join(failures))
    fps = number(storyboard["fps"])
    if not math.isfinite(fps) or fps <= 0:
        raise ValueError("fps 无效")
    if not storyboard.get("shots"):
        raise ValueError("视觉分镜不能为空")
    by_shot = {item["shot_id"]: item for item in selections}
    if len(by_shot) != len(selections) or set(by_shot) != {s["id"] for s in storyboard["shots"]}:
        raise ValueError("选段必须唯一且完整覆盖视觉分镜")
    result, assigned_frames = [], 0
    total_frames = int(math.floor(storyboard["shots"][-1]["window"]["end"] * fps))
    for index, shot in enumerate(storyboard["shots"]):
        selection = by_shot[shot["id"]]
        clip = clips.get(selection.get("clip_id"), {})
        start, end = selection["in_frame"], selection["out_frame"]
        if type(start) is not int or type(end) is not int:
            raise ValueError("素材选段必须使用整数帧，不得静默截断")
        expected_frames = round((shot["window"]["end"] - shot["window"]["start"]) * fps)
        if index == len(storyboard["shots"]) - 1:
            expected_frames = total_frames - assigned_frames
        if (not clip or clip.get("status") != "reviewed" or not clip.get("evidence")
                or not selection.get("edit_reason") or not selection.get("state_check")):
            raise ValueError(f"{shot['id']} 缺少已审素材、剪辑理由或状态衔接检查")
        if type(clip.get('frame_count')) is not int or clip['frame_count'] <= 0:
            raise ValueError('素材frame_count须为正整数，不得截断或接受布尔值')
        if expected_frames <= 0:
            raise ValueError('镜头音乐窗口不足一帧')
        if (start < 0 or end <= start or end > int(clip.get("frame_count", 0))
                or end - start != expected_frames or float(clip.get("fps", 0)) != fps):
            raise ValueError(f"{shot['id']} 选段帧数/帧率不符合音乐窗口，不自动变速")
        if clip.get("scene_id") != shot["scene_id"]:
            raise ValueError(f"{shot['id']} 素材场景与分镜不一致")
        editing = (shot.get('crew') or {}).get('editing') or {}
        transition = {'type': editing.get('transition_type', 'hard_cut')}
        postroll = 0
        if transition['type'] == 'dissolve':
            postroll = editing.get('transition_duration_frames')
            if type(postroll) is not int or postroll <= 0 or index == len(storyboard['shots'])-1:
                raise ValueError('叠化须指定正整数帧数且有下一镜')
            following = storyboard['shots'][index+1]['window']
            if postroll > round((following['end']-following['start'])*fps) or end+postroll > int(clip['frame_count']):
                raise ValueError('叠化超过下一镜窗口或缺少真实后延素材')
            transition['duration_frames'] = postroll
        result.append({**selection, "source_path": clip["path"], "source_sha256": clip["sha256"],
                       "target_path": f"clips/edit/{shot['id']}_fitted.mp4",
                       "window": shot["window"], "fps": fps, "frames": expected_frames,
                       'video_transition': transition, 'postroll_frames': postroll})
        assigned_frames += expected_frames
    return result


def validate_assembly(storyboard, clips, selections, assembly):
    """合成不信任清单声明：重算选段事实并逐镜比对顺序、来源和转场。"""
    expected = compile_selection(storyboard, clips, selections)
    actual = assembly.get('entries')
    if not isinstance(actual, list) or len(actual) != len(expected):
        raise ValueError('剪辑清单镜头数量不完整')
    fields = ('shot_id', 'clip_id', 'in_frame', 'out_frame', 'source_path',
              'source_sha256', 'window', 'fps', 'frames')
    for item, required in zip(actual, expected):
        if not isinstance(item, dict) or any(item.get(key) != required[key] for key in fields):
            raise ValueError('剪辑清单镜头顺序、窗口或来源与正式选段不一致')
        if (item.get('video_transition', {'type': 'hard_cut'}) != required['video_transition']
                or item.get('postroll_frames', 0) != required['postroll_frames']):
            raise ValueError('剪辑清单转场与分镜设计不一致')
        if not isinstance(item.get('target_path'), str) or not item['target_path'].strip():
            raise ValueError('剪辑清单缺少实际渲染路径')
    return True


def build(project: Path, render: bool = False, technical_preview: bool = False) -> dict:
    project = Path(project).resolve()
    try:
        from .pipeline_contracts import atomic_batch, require_no_transaction
    except ImportError:
        from pipeline_contracts import atomic_batch, require_no_transaction
    require_no_transaction(project)
    tracked = [project / 'state' / f'{name}.json' for name in (
        'storyboard', 'generated_clips', 'edit_selection', 'edit_assembly', 'chapter_plan',
        'development', 'story_plan', 'visual_edit_plan', 'timeline', 'style',
        'visual_assets', 'music_structure', 'director_review')]
    tracked.append(project / 'state/reviews/full_sequence.json')
    tracked.extend((project / 'state/reviews').glob('*.json'))
    expected = {path: fingerprint(path) if path.exists() else None for path in tracked}
    full_review = project / 'state/reviews/full_sequence.json'
    if full_review.exists() and load(full_review).get('decision') == 'approve':
        raise ValueError('全片已批准，剪辑清单冻结；先 director_review_gate invalidate --chapter full_sequence，修改后重新审片')
    storyboard = load(project / "state" / "storyboard.json")
    try:
        from .pipeline_contracts import supported_transitions
        from .director_review_gate import approved
        from .visual_development import require
        from .chapter_plan_build import require_production_timing
    except ImportError:
        from pipeline_contracts import supported_transitions
        from director_review_gate import approved
        from visual_development import require
        from chapter_plan_build import require_production_timing
    failures = supported_transitions(storyboard["shots"])
    try:
        from .sound_mix import require_plan
    except ImportError:
        from sound_mix import require_plan
    _, sound_versions = require_plan(project, storyboard)
    expected.update(sound_versions)
    if failures:
        raise ValueError("; ".join(failures))
    if render and not technical_preview:
        try:
            from .production_preflight import require_current
        except ImportError:
            from production_preflight import require_current
        require_current(project)
        require(project, storyboard, load(project / "state/style.json"))
        require_production_timing(load(project / "state/timeline.json"), storyboard["shots"])
        ok, failures = approved(project)
        if not ok:
            raise ValueError("正式剪辑必须先通过样片审片: " + "; ".join(failures))
    clips = load(project / "state" / "generated_clips.json")["clips"]
    selections = load(project / "state" / "edit_selection.json")["selections"]
    entries = compile_selection(storyboard, clips, selections)
    chapter_approvals = {}
    origin_versions = {}
    if render and not technical_preview:
        try:
            from .chapter_admission import require as require_chapters
        except ImportError:
            from chapter_admission import require as require_chapters
        chapter_approvals = require_chapters(project, storyboard, clips, selections, origin_versions)
        expected.update(origin_versions)
        for cid, sha in chapter_approvals.items():
            expected.setdefault(project / 'state/reviews' / f'{cid}.json', sha)
    for entry in entries:
        source = project / entry["source_path"]
        if not source.is_file() or fingerprint(source) != entry["source_sha256"]:
            raise ValueError(f"素材不存在或已改变: {source}")
        expected[source.resolve()] = entry['source_sha256']
        if render:
            probe = subprocess.run([
                "ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames",
                "-show_entries", "stream=avg_frame_rate,nb_read_frames", "-of", "json", str(source),
            ], capture_output=True, text=True, check=True)
            stream = json.loads(probe.stdout)["streams"][0]
            n, d = map(int, stream["avg_frame_rate"].split("/"))
            if abs(n / d - entry["fps"]) > 1e-6 or int(stream["nb_read_frames"]) < entry["out_frame"]+entry['postroll_frames']:
                raise ValueError("素材帧率/长度与选段记录不一致")
            if not technical_preview:
                try:
                    from .media_verify import is_cfr
                except ImportError:
                    from media_verify import is_cfr
                if not is_cfr(source, entry['fps']):
                    raise ValueError('正式按帧选段须使用已核验CFR素材，不能以平均帧率替代时间戳检查')
    result = {"status": "rendered" if render else "planned", "entries": entries,
              "chapter_approvals": chapter_approvals,
              "origin_versions": {str(path): sha for path, sha in origin_versions.items()},
              "technical_preview": technical_preview,
              "storyboard_sha256": fingerprint(project / "state" / "storyboard.json"),
              "selection_sha256": fingerprint(project / "state" / "edit_selection.json")}
    manifest = project / 'state/edit_assembly.json'
    if render:
        root = project / 'clips/edit'
        root.mkdir(parents=True, exist_ok=True)
        generation = uuid.uuid4().hex
        destination = root / generation
        with tempfile.TemporaryDirectory(dir=root) as temporary:
            staged = Path(temporary)
            for index, entry in enumerate(entries):
                target = staged / f'shot-{index:04d}.mp4'
                subprocess.run([
                    'ffmpeg', '-y', '-v', 'error', '-i', str(project / entry['source_path']),
                    '-vf', f"trim=start_frame={entry['in_frame']}:end_frame={entry['out_frame']+entry['postroll_frames']},setpts=PTS-STARTPTS",
                    '-an', '-c:v', 'libx264', '-crf', '18', '-pix_fmt', 'yuv420p', str(target),
                ], check=True, capture_output=True)
                try:
                    from .media_verify import probe as media_probe, ratio
                except ImportError:
                    from media_verify import probe as media_probe, ratio
                video = next(s for s in media_probe(target)['streams'] if s['codec_type'] == 'video')
                if int(video['nb_read_frames']) != entry['frames']+entry['postroll_frames'] or abs(ratio(video['avg_frame_rate'])-entry['fps']) > 1e-6:
                    raise ValueError('实际选段输出帧数或帧率不符合窗口')
                entry['output_sha256'] = fingerprint(target)
                entry['target_path'] = (destination / target.name).relative_to(project).as_posix()
            result['generation_id'] = generation
            def publish():
                staged.resolve().relative_to(root.resolve())
                destination.resolve().relative_to(root.resolve())
                if destination.exists(): raise FileExistsError('剪辑版本目录已存在，禁止覆盖')
                os.replace(staged, destination)
            atomic_batch({manifest: json.dumps(result, ensure_ascii=False, indent=2) + '\n'},
                         expected=expected, before_commit=publish)
    else:
        atomic_batch({manifest: json.dumps(result, ensure_ascii=False, indent=2) + '\n'}, expected=expected)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("project_dir")
    parser.add_argument("--render", action="store_true", help="仅本地剪辑已存在素材，不调用生成后端")
    parser.add_argument("--technical-preview", action="store_true")
    args = parser.parse_args()
    print(json.dumps(build(Path(args.project_dir).resolve(), args.render, args.technical_preview), ensure_ascii=False))
