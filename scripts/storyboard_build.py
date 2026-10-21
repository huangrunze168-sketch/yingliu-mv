#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从音乐引用构建导演设计的视觉镜头；无编辑方案时只交付时间骨架。"""
import argparse
import hashlib
import json
import math
from pathlib import Path

REQUIRED_STORY_FIELDS = {
    "story_beat", "narrative_before", "current_action", "implied_next",
    "space", "blocking", "props", "light", "camera_witness", "shot_delta",
    "visual_center", "information_hierarchy", "gaze_target", "action_physics",
    "material_response", "edit_trigger",
    # Scene Master V1.4.1：把静帧选择和整段运动分开，并要求可落地的拍摄依据。
    "story_intent", "obstacle", "keyframe_moment", "audience_information",
    "shooting_station", "frame_boundary", "visual_acceptance",
}
SHOT_TYPES = {"大特写", "特写", "近景", "中景", "中远景", "全景", "远景", "空镜", "背影", "群像", "细节"}
LIP_TYPES = {"大特写", "特写", "近景", "中景"}


def sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def snap(t: float, fps: float) -> float:
    return round(round(t * fps) / fps, 10)


def music_anchors(timeline: dict, music: dict, fps: float) -> dict:
    duration = float(timeline["duration_sec"])
    anchors = {"song:start": 0.0, "song:end": duration}
    indices = set()
    for line in timeline.get("lines", []):
        index = line["index"]
        if index in indices:
            raise ValueError(f"时间轴歌词索引重复: {index}")
        indices.add(index)
        if float(line["start"]) >= float(line["end"]):
            raise ValueError(f"歌词窗口为空或倒序: {index}")
        for key in ("start", "end"):
            value = float(line[key])
            if not math.isfinite(value) or not 0 <= value <= duration:
                raise ValueError(f"非法歌词时间: {index}.{key}")
            anchors[f"line:{index}:{key}"] = min(duration, snap(value, fps))
    for cue in music.get("cues", []):
        name = "cue:" + cue["id"]
        value = float(cue["time_sec"])
        if name in anchors or not math.isfinite(value) or not 0 <= value <= duration:
            raise ValueError(f"非法或重复音乐切点: {name}")
        if not cue.get("evidence") or not cue.get("kind"):
            raise ValueError(f"音乐切点缺少分析依据: {name}")
        anchors[name] = min(duration, snap(value, fps))
    return anchors


def build_visual_shots(timeline: dict, music: dict, edit_plan: dict, fps: float) -> list:
    if not math.isfinite(fps) or fps <= 0:
        raise ValueError("fps 必须为正数")
    anchors = music_anchors(timeline, music, fps)
    shots, ids = [], set()
    for item in edit_plan.get("shots", []):
        sid = item.get("id", "")
        if not sid.startswith("SH") or not sid[2:].isdigit() or sid in ids:
            raise ValueError(f"镜头 ID 无效或重复: {sid}")
        ids.add(sid)
        if item.get("start_ref") not in anchors or item.get("end_ref") not in anchors:
            raise ValueError(f"{sid} 必须引用已有歌词/音乐切点，不能填写自由秒数")
        start, end = anchors[item["start_ref"]], anchors[item["end_ref"]]
        if start >= end or (shots and abs(start - shots[-1]["window"]["end"]) > 0.001):
            raise ValueError(f"{sid} 窗口为空、倒序、重叠或有空洞")
        shot_type = item.get("shot_type")
        lip = item.get("lip_sync")
        if shot_type not in SHOT_TYPES or not isinstance(lip, bool):
            raise ValueError(f"{sid} 必须明确景别与 lip_sync，不自动轮换")
        if lip and shot_type not in LIP_TYPES:
            raise ValueError(f"{sid} 口型镜头景别无效")
        if not item.get("cut_reason") or not item.get("scene_id"):
            raise ValueError(f"{sid} 缺少场景引用或切镜依据")
        lines = [line for line in timeline.get("lines", [])
                 if line.get("type") != "interlude"
                 and float(line["start"]) < end - 1e-8
                 and float(line["end"]) > start + 1e-8]
        if lip and not lines:
            raise ValueError(f"{sid} 口型镜头没有人声歌词")
        shots.append({
            "id": sid, "scene_id": item["scene_id"],
            "segment": item.get("segment", "unclassified"),
            "window": {"start": start, "end": end},
            "timing_refs": {"start": item["start_ref"], "end": item["end_ref"]},
            "lyric_lines": [line["index"] for line in lines],
            "lyric_line": lines[0]["index"] if len(lines) == 1 else None,
            "shot_type": shot_type, "lip_sync": lip,
            "generation_role": "lip_sync" if lip else "narrative",
            "role": "perform" if lip else item.get("role", "narrative"),
            "cut_reason": item["cut_reason"], "camera": item.get("camera", ""),
            "prompt_file": f"prompts/{sid}.json", "prompt_fingerprint": None,
            "repair": {"mode": "none", "problems": []}, "status": "pending_story",
        })
    duration = float(timeline["duration_sec"])
    if (not shots or abs(shots[0]["window"]["start"]) > 0.001
            or abs(shots[-1]["window"]["end"] - duration) > 0.001):
        raise ValueError("视觉镜头必须从歌曲起点连续覆盖到全曲末尾")
    return shots


def apply_story_plan(project: Path, shots: list) -> tuple[str, dict, str | None]:
    path = project / "state" / "story_plan.json"
    if not path.is_file():
        return "timing_only", {}, None
    plan = json.loads(path.read_text(encoding="utf-8"))
    if plan.get("method") != "zy_story_visual_development":
        raise ValueError("story_plan.method 必须为 zy_story_visual_development")
    items = plan.get("shots", [])
    mapping = {item.get("id"): item for item in items}
    if len(mapping) != len(items) or set(mapping) != {s["id"] for s in shots}:
        raise ValueError("story_plan.shots ID 必须唯一并完整匹配视觉编辑方案")
    errors = []
    for shot in shots:
        item = mapping[shot["id"]]
        missing = sorted(key for key in REQUIRED_STORY_FIELDS if not item.get(key))
        if missing:
            errors.append(f"{shot['id']} 缺少故事字段: {','.join(missing)}")
        if item.get("scene_id") != shot["scene_id"]:
            errors.append(f"{shot['id']} scene_id 与编辑方案不一致")
        if not item.get("source_beat") or not item.get("state_before") or not item.get("state_after"):
            errors.append(f"{shot['id']} 缺少来源节拍或前后连续性状态")
        if "lyric_lines" in item and item["lyric_lines"] != shot["lyric_lines"]:
            errors.append(f"{shot['id']} 不得改写歌词引用")
        exclusions = item.get('reference_exclusions', [])
        if (not isinstance(exclusions, list) or any(
                not isinstance(value, str) or not value.strip() for value in exclusions)):
            errors.append(f"{shot['id']} reference_exclusions须为非空字符串列表，不能传字符串冒充列表")
        coverage = item.get("coverage_plan", [])
        if not isinstance(coverage, list):
            errors.append(f"{shot['id']} coverage_plan 必须是列表")
        else:
            coverage_ids = [part.get("id") for part in coverage if isinstance(part, dict)]
            if (len(coverage_ids) != len(coverage) or any(not cid for cid in coverage_ids)
                    or len(set(coverage_ids)) != len(coverage_ids)):
                errors.append(f"{shot['id']} coverage_plan ID 必须非空且唯一")
            for part in coverage:
                if not isinstance(part, dict):
                    continue
                if part.get("selection_status") not in {"candidate", "selected", "rejected"}:
                    errors.append(f"{shot['id']}/{part.get('id')} coverage selection_status 无效")
                if not part.get("purpose") or not part.get("keyframe_moment"):
                    errors.append(f"{shot['id']}/{part.get('id')} coverage 缺少用途或单一瞬间")
                if (part.get("selection_status") == "selected"
                        and not part.get("materialized_shot_id")):
                    errors.append(
                        f"{shot['id']}/{part.get('id')} 已选 coverage 必须先落为独立定时镜头"
                    )
                elif part.get("selection_status") == "selected":
                    target = mapping.get(part["materialized_shot_id"])
                    timed = next((s for s in shots if s['id'] == part['materialized_shot_id']), None)
                    if (not target or target.get("scene_id") != item.get("scene_id")
                            or target.get("coverage_source_id") != part.get("id")
                            or not timed or part.get('window') != timed.get('window')
                            or part.get('keyframe_moment') != target.get('keyframe_moment')):
                        errors.append(f"{shot['id']} coverage未引用同场景正式镜头及coverage_source_id")
    if errors:
        raise ValueError("\n".join(errors))
    for shot in shots:
        item = mapping[shot["id"]]
        for key in REQUIRED_STORY_FIELDS | {
            "purpose", "knowledge_state", "prop_state", "source_beat",
            "state_before", "state_after", "active_rule_ids", "director_decisions", "motion_design", "risk_tags",
            "decision_authority", "design_revision", "coverage_plan", "crew", "coverage_source_id",
            "reference_exclusions", "sequence_direction", "film_grammar",
        }:
            if key in item:
                shot[key] = item[key]
        shot.update(visual_concept=item["story_beat"], story_status="ready", status="pending")
        shot['camera'] = json.dumps((shot.get('crew') or {}).get('cinematography', {}), ensure_ascii=False)
    return "zy_story_visual_development", plan.get("continuity_bible", {}), sha256_file(path)


def build(project: Path, fps: float = 24) -> dict:
    try:
        from .pipeline_contracts import digest, atomic_batch, require_no_transaction
        from .sequence_direction import align_to_storyboard
    except ImportError:
        from pipeline_contracts import digest, atomic_batch, require_no_transaction
        from sequence_direction import align_to_storyboard
    project = Path(project).resolve()
    require_no_transaction(project)
    names = ('timeline', 'music_structure', 'visual_edit_plan', 'story_plan', 'development', 'sequence_direction', 'storyboard')
    expected = {project / 'state' / (name + '.json'): digest(project / 'state' / (name + '.json'))
                if (project / 'state' / (name + '.json')).is_file() else None for name in names}
    timeline = json.loads((project / "state" / "timeline.json").read_text(encoding="utf-8"))
    duration = float(timeline["duration_sec"])
    if not math.isfinite(duration) or duration <= 0 or not math.isfinite(fps) or fps <= 0:
        raise ValueError("歌曲时长与 fps 必须为正数")
    edit_path = project / "state" / "visual_edit_plan.json"
    music_path = project / "state" / "music_structure.json"
    music = json.loads(music_path.read_text(encoding="utf-8")) if music_path.is_file() else {}
    if not edit_path.is_file():
        result = {"schema_version": 2, "story_mode": "timing_only", "shots": [],
                  "music_windows": timeline.get("lines", []), "music_analysis_status":
                  "provided" if music else "pending", "fps": fps,
                  "next_required": ["development.json", "visual_edit_plan.json", "story_plan.json"]}
    else:
        edit = json.loads(edit_path.read_text(encoding="utf-8"))
        shots = build_visual_shots(timeline, music, edit, fps)
        mode, continuity, story_fp = apply_story_plan(project, shots)
        development_path = project / 'state/development.json'
        if development_path.is_file():
            development = json.loads(development_path.read_text(encoding='utf-8'))
            kinds = {s['id']: s.get('kind', 'unknown') for s in development.get('scenes', [])}
            for shot in shots:
                shot['scene_kind'] = kinds.get(shot['scene_id'], 'unknown')
        result = {"schema_version": 2, "story_mode": mode, "shots": shots, "fps": fps,
                  "frame_snapped": True, "timing_mode": "music_referenced_visual_edit",
                  "music_anchors": music_anchors(timeline, music, fps),
                  "music_structure_fingerprint": sha256_file(music_path) if music_path.is_file() else None,
                  "visual_edit_fingerprint": sha256_file(edit_path),
                  "timeline_fingerprint": sha256_file(project / "state" / "timeline.json"),
                  "story_plan_fingerprint": story_fp, "continuity_bible": continuity,
                  "performance_policy": edit.get("performance_policy", {})}
    updates = {}
    sequence_path = project / 'state/sequence_direction.json'
    if sequence_path.is_file() and result['story_mode'] != 'timing_only':
        sequence = align_to_storyboard(json.loads(sequence_path.read_text(encoding='utf8')), result)
        expected.update({Path(v['path']): v['sha256'] for v in sequence['sources'].values()})
        updates[sequence_path] = json.dumps(sequence, ensure_ascii=False, indent=2) + '\n'
    updates[project / 'state/storyboard.json'] = json.dumps(result, ensure_ascii=False, indent=2) + '\n'
    atomic_batch(updates, expected=expected)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("project_dir")
    parser.add_argument("--fps", type=float, default=24)
    args = parser.parse_args()
    result = build(Path(args.project_dir), args.fps)
    print(json.dumps({"shots": len(result["shots"]), "story_mode": result["story_mode"]}, ensure_ascii=False))
