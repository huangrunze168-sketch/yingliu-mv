#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""剧组部门契约与视听语言审计。

先保证编剧、导演、摄影、灯光、场景美术、人物美术、演员调度和剪辑能够
互相交接，再允许叠加导演风格。该模块只检查设计数据，不生成媒体。
"""
from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from typing import Any

try:
    from .film_grammar_corpus import audit_binding
    from .pipeline_contracts import SHOT_SIZES, number
except ImportError:
    from film_grammar_corpus import audit_binding
    from pipeline_contracts import SHOT_SIZES, number


DEPARTMENT_FIELDS = {
    "screenwriting": ("dramatic_question", "conflict_progression", "reveal_plan"),
    "directing": ("scene_objective", "performance_arc", "staging_principle",
                  "style_application_order"),
    "cinematography": ("coverage_strategy", "coverage_requirements", "lens_strategy", "axis_policy"),
    "lighting": ("motivated_source_strategy", "contrast_arc", "exposure_priority",
                 "continuity_strategy"),
    "production_design": ("set_story", "depth_layers", "material_palette",
                          "hero_prop_strategy"),
    "character_design": ("silhouette_rule", "wardrobe_arc", "wear_continuity"),
    "blocking": ("geography_rule", "movement_axis", "entrance_exit_logic"),
    "editing": ("rhythm_map", "hook_strategy", "cut_pattern", "pacing_policy",
                "sound_image_strategy"),
}

REQUIRED_HANDOFFS = {
    ("screenwriting", "directing"), ("directing", "blocking"),
    ("production_design", "blocking"), ("character_design", "blocking"),
    ("blocking", "cinematography"), ("lighting", "cinematography"),
    ("production_design", "cinematography"), ("cinematography", "editing"),
    ("directing", "editing"),
}

SHOT_DEPARTMENT_FIELDS = {
    "screenwriting": ("objective", "obstacle", "turn", "audience_gain"),
    "directing": ("performance_beat", "subtext", "staging_intent"),
    "cinematography": (
        "viewpoint_id", "shot_size", "camera_position", "camera_height",
        "camera_side", "angle_deg", "lens_behavior", "camera_movement",
        "movement_motivation", "axis_id", "focal_length_mm", "focus_target",
        "subject_placement", "headroom", "look_room", "depth_strategy", "frame_purpose",
    ),
    "lighting": (
        "source_used", "exposure_target", "face_priority", "shadow_behavior",
        "key_direction", "key_quality", "fill_strategy", "practicals",
        "color_temp_k", "continuity_anchor",
    ),
    "production_design": (
        "foreground_story", "midground_story", "background_story",
        "hero_prop_state", "visual_hook", "set_dressing", "material_response",
        "color_accent", "atmospheric_layer", "continuity_anchor",
    ),
    "character_design": ("silhouette_state", "wardrobe_state", "grooming_state"),
    "blocking": (
        "start_mark", "end_mark", "entry_direction", "exit_direction",
        "gaze_target", "screen_direction", "body_orientation", "action_path",
        "eyeline_vector", "crossing_rule",
    ),
    "editing": (
        "editorial_role", "incoming_relation", "outgoing_relation", "rhythm_class",
        "music_cue", "novelty_axis", "visual_change", "visual_change_code",
        "geography_status", "hook",
        "coverage_role", "transition_type", "cut_motivation", "audio_transition",
        "duration_intent",
    ),
}

SCENE_ART_FIELDS = (
    "foreground", "midground", "background", "scenic_action", "hero_prop",
    "negative_space", "material_contrast", "set_history", "color_script",
    "scale_cue", "background_activity",
)
SCENE_LIGHT_FIELDS = (
    "primary_source", "secondary_source", "shadow_zones", "exposure_priority",
    "color_temperature_relation", "cue_changes", "key_direction", "key_quality",
    "fill_strategy", "practicals", "continuity_anchor",
)
SIDES = {"left", "right", "center"}
DIRECTIONS = {"left_to_right", "right_to_left", "toward_camera", "away_camera", "static", "none"}
TRANSITIONS = {"hard_cut", "match_cut", "dissolve", "fade", "wipe", "whip", "black_frame"}
AUDIO_TRANSITIONS = {"straight", "J_cut", "L_cut", "music_hit", "silence_bridge", "sound_bridge"}

ANGLE_BUCKET_DEG = 15.0
POSITION_GRID = 0.25
NEAR_ANGLE_DEG = 30.0
FRAME_TOLERANCE_SEC = 1.0 / 24.0

COVERAGE_ESTABLISH = frozenset({"establish", "reestablish"})
COVERAGE_ACTION = frozenset({"action", "develop"})
COVERAGE_REACTION = frozenset({"reaction", "detail"})
COVERAGE_TURN = frozenset({"turn", "payoff"})
NARRATIVE_COVERAGE_KINDS = frozenset({"narrative", "performance"})

TEMPO_PACING_DEFAULTS = {
    "fast": {
        "max_average_shot_sec": 2.5,
        "slow_shot_threshold_sec": 3.0,
        "max_consecutive_slow_shots": 2,
        "max_consecutive_same_rhythm": 2,
        "hook_deadline_sec": 3.0,
        "max_hook_gap_sec": 8.0,
        "max_visual_stasis_sec": 4.0,
        "min_shot_size_variety_per_scene": 2,
        "max_consecutive_near_same_angle": 2,
    },
    "medium": {
        "max_average_shot_sec": 3.5,
        "slow_shot_threshold_sec": 4.0,
        "max_consecutive_slow_shots": 2,
        "max_consecutive_same_rhythm": 2,
        "hook_deadline_sec": 5.0,
        "max_hook_gap_sec": 12.0,
        "max_visual_stasis_sec": 6.0,
        "min_shot_size_variety_per_scene": 2,
        "max_consecutive_near_same_angle": 2,
    },
    "slow": {
        "max_average_shot_sec": 5.0,
        "slow_shot_threshold_sec": 6.0,
        "max_consecutive_slow_shots": 3,
        "max_consecutive_same_rhythm": 3,
        "hook_deadline_sec": 6.0,
        "max_hook_gap_sec": 16.0,
        "max_visual_stasis_sec": 8.0,
        "min_shot_size_variety_per_scene": 2,
        "max_consecutive_near_same_angle": 2,
    },
}
PACING_LOOSER_IF_GREATER = frozenset({
    "max_average_shot_sec", "slow_shot_threshold_sec", "max_consecutive_slow_shots",
    "max_consecutive_same_rhythm", "hook_deadline_sec", "max_hook_gap_sec",
    "max_visual_stasis_sec", "max_consecutive_near_same_angle",
})
PACING_LOOSER_IF_LESSER = frozenset({"min_shot_size_variety_per_scene"})

# P1: 灯光/舞美禁空泛词与可搭建拓扑；ZY 可机检 crew 增量
VAGUE_AESTHETIC_SUBSTRINGS = (
    # 中文空泛审美（不替代人审「好看」；仅拦不可执行措辞）
    "电影感", "唯美", "大片感", "大片", "高级感", "氛围感", "质感拉满",
    "高级光", "氛围光", "电影光", "网感", "高级电影感",
    "绝美", "超美", "梦幻感", "高级氛围", "氛围拉满", "质感满分",
    "高级质感", "氛围拉满", "光影大片", "情绪拉满", "好看", "漂亮",
    "惊艳", "炸裂", "封神", "氛围到位", "质感到位",
    # English empty aesthetic
    "cinematic feel", "cinematic vibe", "beautiful", "gorgeous", "stunning",
    "epic vibe", "moody vibe", "high-end look", "premium look", "aesthetic vibe",
    "atmosphere only", "vibes only", "film look", "movie look",
)
KEY_DIRECTIONS = frozenset({
    "left", "right", "front", "back", "above", "below",
    "left_front", "right_front", "left_rear", "right_rear",
    "camera_left", "camera_right", "camera_front", "camera_back",
    "左", "右", "前", "后", "上方", "下方",
    "左前", "右前", "左后", "右后",
    "左前侧", "右前侧", "左后侧", "右后侧",
    "画面左侧", "画面右侧", "画面前方", "画面后方",
    "画面左前", "画面右前", "画面左后", "画面右后",
    "画面左前侧", "画面右前侧", "画面左后侧", "画面右后侧",
})
TOPOLOGY_NODE_KINDS = frozenset({
    "entrance", "exit", "barrier", "landmark", "mark",
})
EXPOSURE_BIASES = frozenset({
    "protect_highlights", "protect_faces", "broad_midtones",
    "practical_adaptation", "dense_blacks", "neutral",
})
CAMERA_HEIGHT_POLICIES = frozenset({
    "eye_level", "low", "high", "grounded_threshold", "participant", "sit_eye_level",
})

NOVELTY_AXES = frozenset({
    "prop", "viewpoint", "blocking", "light", "information", "geography",
    "performance", "rhythm", "scale", "reveal", "costume", "sound",
})
VISUAL_CHANGE_CODES = frozenset({
    "enter", "exit", "prop_state", "gaze_shift", "light_shift", "distance_shift",
    "reveal", "geography", "performance_beat", "cutaway", "pose_shift", "rhythm_hit",
})
SCREEN_DIRECTION_REVERSE = {
    "left_to_right": "right_to_left",
    "right_to_left": "left_to_right",
    "toward_camera": "away_camera",
    "away_camera": "toward_camera",
}
BODY_ORIENTATION_NORMALIZE = {
    "left_to_right": "left_to_right", "right_to_left": "right_to_left",
    "toward_camera": "toward_camera", "away_camera": "away_camera",
    "朝右": "left_to_right", "向右": "left_to_right", "面向右": "left_to_right",
    "朝左": "right_to_left", "向左": "right_to_left", "面向左": "right_to_left",
    "面向镜头": "toward_camera", "朝向镜头": "toward_camera", "正对镜头": "toward_camera",
    "背对镜头": "away_camera", "背向镜头": "away_camera",
}
CROSS_SCENE_ANGLE_WINDOW_SEC = 8.0
EYELINE_QUADRANTS = frozenset({
    "left", "right", "up", "down", "center",
    "left_up", "left_down", "right_up", "right_down",
    "toward_camera", "away_camera",
})


def _missing(data: dict[str, Any], fields: tuple[str, ...]) -> list[str]:
    return [field for field in fields if data.get(field) in (None, "", [], {})]


def _positive_number(value: Any) -> float | None:
    try:
        if isinstance(value, bool):
            return None
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _max_run(values: list[Any], predicate) -> int:
    best = current = 0
    for value in values:
        current = current + 1 if predicate(value) else 0
        best = max(best, current)
    return best


def _shot_duration(shot: dict[str, Any]) -> float:
    window = shot.get("window") or {}
    return float(window.get("end", 0)) - float(window.get("start", 0))



def _angle_bucket(angle: Any) -> int | None:
    try:
        value = float(angle)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value):
        return None
    return int(round(value / ANGLE_BUCKET_DEG))


def _position_bucket(xy: Any) -> tuple[float, float] | None:
    try:
        x, y = float(xy[0]), float(xy[1])
    except (TypeError, ValueError, IndexError, KeyError):
        return None
    if not math.isfinite(x) or not math.isfinite(y):
        return None
    return (round(x / POSITION_GRID) * POSITION_GRID, round(y / POSITION_GRID) * POSITION_GRID)


def _circular_angle_delta(a: Any, b: Any) -> float | None:
    try:
        left, right = float(a), float(b)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(left) or not math.isfinite(right):
        return None
    return abs((left - right + 180.0) % 360.0 - 180.0)


def _resolve_tempo_class(music_structure: dict[str, Any] | None) -> str:
    if not music_structure:
        return "medium"
    raw = music_structure.get("tempo_class") or music_structure.get("tempo")
    if isinstance(raw, str) and raw.strip().casefold() in TEMPO_PACING_DEFAULTS:
        return raw.strip().casefold()
    bpm = _positive_number(music_structure.get("bpm"))
    if bpm is not None:
        if bpm >= 120:
            return "fast"
        if bpm <= 80:
            return "slow"
    return "medium"


def _pacing_exception_ok(editing_bible: dict[str, Any], policy: dict[str, Any]) -> bool:
    exception = policy.get("pacing_exception") or editing_bible.get("pacing_exception") or {}
    if not isinstance(exception, dict):
        return False
    reason = exception.get("reason")
    evidence = exception.get("evidence")
    if not (isinstance(reason, str) and isinstance(evidence, str)):
        return False
    reason_s, evidence_s = reason.strip(), evidence.strip()
    # 加严：两端非空、证据足够长、证据不得简单复读理由
    if len(reason_s) < 4 or len(evidence_s) < 8:
        return False
    if evidence_s == reason_s or evidence_s in reason_s:
        return False
    return True


def _effective_pacing_policy(
    editing_bible: dict[str, Any], music_structure: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[str]]:
    """合并曲速默认 soft-max 与项目策略；放宽超过默认须 pacing_exception。"""
    errors: list[str] = []
    policy = editing_bible.get("pacing_policy") or {}
    required_policy = (
        "max_average_shot_sec", "slow_shot_threshold_sec", "max_consecutive_slow_shots",
        "max_consecutive_same_rhythm", "hook_deadline_sec", "max_hook_gap_sec",
        "max_visual_stasis_sec", "min_shot_size_variety_per_scene",
    )
    missing_policy = _missing(policy, required_policy)
    if missing_policy:
        errors.append("剪辑节奏策略缺少: " + ",".join(missing_policy))
    tempo = _resolve_tempo_class(music_structure)
    defaults = dict(TEMPO_PACING_DEFAULTS[tempo])
    # 可选策略：近同角度连续上限；缺省用曲速默认
    if policy.get("max_consecutive_near_same_angle") in (None, "", [], {}):
        policy = {**policy, "max_consecutive_near_same_angle": defaults["max_consecutive_near_same_angle"]}
    numeric_keys = required_policy + ("max_consecutive_near_same_angle",)
    numeric = {key: _positive_number(policy.get(key)) for key in numeric_keys}
    for key in ("max_consecutive_slow_shots", "max_consecutive_same_rhythm",
                "min_shot_size_variety_per_scene", "max_consecutive_near_same_angle"):
        if type(policy.get(key)) is not int:
            numeric[key] = None
            errors.append(f"{key}必须为正整数，拒绝布尔值与小数")
    invalid = [key for key, value in numeric.items() if value is None]
    if invalid and not missing_policy:
        errors.append("剪辑节奏策略必须是正数: " + ",".join(invalid))
    allow_looser = _pacing_exception_ok(editing_bible, policy)
    effective: dict[str, Any] = {}
    if not invalid:
        for key in numeric_keys:
            declared = numeric[key]
            default = defaults[key]
            if key in PACING_LOOSER_IF_GREATER and declared > default + 1e-6 and not allow_looser:
                errors.append(
                    f"pacing_policy.{key}={declared} 超过曲速默认 {tempo}/{default}，"
                    "须提供 pacing_exception.reason 与 evidence"
                )
                effective[key] = default
            elif key in PACING_LOOSER_IF_LESSER and declared < default - 1e-6 and not allow_looser:
                errors.append(
                    f"pacing_policy.{key}={declared} 低于曲速默认 {tempo}/{default}，"
                    "须提供 pacing_exception.reason 与 evidence"
                )
                effective[key] = default
            else:
                effective[key] = declared
        effective["tempo_class"] = tempo
        effective["_allow_looser"] = allow_looser
    return effective, errors


def _music_cue_anchors(
    music_structure: dict[str, Any], timeline: dict[str, Any] | None, fps: float,
) -> dict[str, float]:
    """与 storyboard_build.music_anchors 对齐：cue id、cue:ID、line:N:start/end、song:start/end。"""
    duration = None
    if timeline is not None:
        try:
            duration = float(timeline["duration_sec"])
        except (KeyError, TypeError, ValueError):
            duration = None
    if duration is None:
        times = []
        for cue in music_structure.get("cues") or []:
            try:
                times.append(float(cue["time_sec"]))
            except (KeyError, TypeError, ValueError):
                pass
        duration = max(times) if times else 0.0
    anchors: dict[str, float] = {"song:start": 0.0, "song:end": float(duration)}
    if timeline is not None:
        for line in timeline.get("lines") or []:
            try:
                index = line["index"]
                start, end = float(line["start"]), float(line["end"])
            except (KeyError, TypeError, ValueError):
                continue
            if not math.isfinite(start) or not math.isfinite(end):
                continue
            anchors[f"line:{index}:start"] = start
            anchors[f"line:{index}:end"] = end
    for cue in music_structure.get("cues") or []:
        cue_id = cue.get("id")
        try:
            time_sec = float(cue["time_sec"])
        except (KeyError, TypeError, ValueError):
            continue
        if not isinstance(cue_id, str) or not cue_id.strip() or not math.isfinite(time_sec):
            continue
        anchors[cue_id] = time_sec
        anchors[f"cue:{cue_id}"] = time_sec
    return anchors


def _validate_music_cues(
    shots: list[dict[str, Any]], music_structure: dict[str, Any] | None,
    timeline: dict[str, Any] | None, fps: float,
) -> list[str]:
    errors: list[str] = []
    if music_structure is None:
        return ["缺少 music_structure，无法核验 music_cue 对齐"]
    if not isinstance(music_structure.get("cues"), list):
        return ["music_structure.cues 必须为列表"]
    try:
        fps_value = float(fps)
    except (TypeError, ValueError):
        fps_value = 24.0
    if not math.isfinite(fps_value) or fps_value <= 0:
        fps_value = 24.0
    tol = max(FRAME_TOLERANCE_SEC, 1.0 / fps_value)
    anchors = _music_cue_anchors(music_structure, timeline, fps_value)
    for shot in shots:
        sid = shot.get("id", "unknown")
        cue = ((shot.get("crew") or {}).get("editing") or {}).get("music_cue")
        if not isinstance(cue, str) or not cue.strip():
            continue  # 字段缺失由 department 检查负责
        key = cue.strip()
        if key not in anchors:
            errors.append(
                f"{sid} music_cue={key!r} 未引用 music_structure.cues[].id 或 "
                "song:/line:/cue: 锚点"
            )
            continue
        time_sec = anchors[key]
        window = shot.get("window") or {}
        try:
            start, end = float(window["start"]), float(window["end"])
        except (KeyError, TypeError, ValueError):
            errors.append(f"{sid} 窗口无效，无法核验 music_cue 时间")
            continue
        if not (start - tol <= time_sec <= end + tol):
            errors.append(
                f"{sid} music_cue 时间 {time_sec:.3f}s 未落入镜头窗口 "
                f"[{start:.3f},{end:.3f}]±{tol:.4f}s"
            )
    return errors


def _validate_scene_coverage_roles(
    shots: list[dict[str, Any]], scenes: list[dict[str, Any]] | None,
) -> list[str]:
    """叙事/表演场 ≥3 镜时强制 establish、action/develop、reaction/detail、turn/payoff。"""
    errors: list[str] = []
    kind_by_id = {
        str(scene.get("id")): scene.get("kind")
        for scene in (scenes or [])
        if scene.get("id") not in (None, "")
    }
    by_scene: dict[str, list[dict[str, Any]]] = {}
    for shot in shots:
        by_scene.setdefault(str(shot.get("scene_id")), []).append(shot)
    for scene_id, scene_shots in by_scene.items():
        kind = kind_by_id.get(scene_id, "narrative")
        if kind not in NARRATIVE_COVERAGE_KINDS or len(scene_shots) < 3:
            continue
        roles = {
            ((shot.get("crew") or {}).get("editing") or {}).get("coverage_role")
            for shot in scene_shots
        }
        roles.discard(None)
        roles.discard("")
        missing = []
        if not roles & COVERAGE_ESTABLISH:
            missing.append("establish/reestablish")
        if not roles & COVERAGE_ACTION:
            missing.append("action/develop")
        if not roles & COVERAGE_REACTION:
            missing.append("reaction/detail")
        if not roles & COVERAGE_TURN:
            missing.append("turn/payoff")
        if missing:
            errors.append(
                f"场景 {scene_id}（{kind}，{len(scene_shots)} 镜）缺少覆盖角色: "
                + ",".join(missing)
            )
    return errors



def _iter_string_values(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _iter_string_values(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_string_values(item)


def _vague_aesthetic_hits(text: str) -> list[str]:
    hits = []
    for term in VAGUE_AESTHETIC_SUBSTRINGS:
        if term and term in text:
            hits.append(term)
    return hits


def _reject_vague_aesthetic(label: str, payload: Any, errors: list[str]) -> None:
    found: list[str] = []
    for text in _iter_string_values(payload):
        for term in _vague_aesthetic_hits(text):
            if term not in found:
                found.append(term)
    if found:
        errors.append(f"{label} 禁止空泛审美词: {','.join(found)}")


def _normalize_key_direction(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    return text or None


def _validate_key_direction(label: str, value: Any, errors: list[str]) -> None:
    direction = _normalize_key_direction(value)
    if direction is None:
        errors.append(f"{label} key_direction 必须使用受控方位枚举")
    elif direction not in KEY_DIRECTIONS:
        errors.append(
            f"{label} key_direction={direction!r} 不在受控方位枚举 "
            f"(如 right_rear/右后侧/画面右后侧)"
        )


def _validate_scene_topology(
    scene: dict[str, Any],
    scene_shots: list[dict[str, Any]],
    errors: list[str],
) -> None:
    scene_id = scene.get("id", "unknown")
    topology = scene.get("topology")
    axes = scene.get("axes") or {}
    if isinstance(topology, str):
        _reject_vague_aesthetic(f"场景 {scene_id} topology", topology, errors)
        errors.append(
            f"场景 {scene_id} topology 必须为含 nodes[] 的可搭建结构，"
            "并与 axes / blocking marks 交叉引用"
        )
        return
    if not isinstance(topology, dict):
        errors.append(f"场景 {scene_id} topology 必须为对象，含可引用入口/阻挡节点")
        return
    _reject_vague_aesthetic(f"场景 {scene_id} topology", topology, errors)
    nodes = topology.get("nodes")
    if not isinstance(nodes, list) or len(nodes) < 2:
        errors.append(f"场景 {scene_id} topology.nodes 至少需要 2 个可引用节点")
        return
    node_ids: dict[str, dict[str, Any]] = {}
    kinds: set[str] = set()
    for index, node in enumerate(nodes):
        if not isinstance(node, dict):
            errors.append(f"场景 {scene_id} topology.nodes[{index}] 必须为对象")
            continue
        node_id = str(node.get("id") or "").strip()
        kind = str(node.get("kind") or "").strip()
        if not node_id:
            errors.append(f"场景 {scene_id} topology.nodes[{index}] 缺少 id")
            continue
        if node_id in node_ids:
            errors.append(f"场景 {scene_id} topology 节点 id 重复: {node_id}")
            continue
        if kind not in TOPOLOGY_NODE_KINDS:
            errors.append(
                f"场景 {scene_id} 节点 {node_id} kind 必须是 "
                + "/".join(sorted(TOPOLOGY_NODE_KINDS))
            )
            continue
        axis_id = node.get("axis_id")
        if axis_id is not None and str(axis_id) and str(axis_id) not in axes:
            errors.append(f"场景 {scene_id} 节点 {node_id} 引用未知轴线 {axis_id}")
        node_ids[node_id] = node
        kinds.add(kind)
    if not kinds & {"entrance", "exit"}:
        errors.append(f"场景 {scene_id} topology 至少需要 entrance 或 exit 节点")
    if "barrier" not in kinds and "landmark" not in kinds and "mark" not in kinds:
        errors.append(
            f"场景 {scene_id} topology 至少需要 barrier/landmark/mark 类可站位或阻挡节点"
        )
    for shot in scene_shots:
        sid = shot.get("id", "unknown")
        blocking = (shot.get("crew") or {}).get("blocking") or {}
        for field in ("start_mark", "end_mark"):
            mark = blocking.get(field)
            if not mark:
                continue
            if str(mark) not in node_ids:
                errors.append(
                    f"{sid} blocking.{field}={mark!r} 未在场景 {scene_id} "
                    "topology.nodes 注册"
                )


def _validate_director_crew_constraints(
    development: dict[str, Any],
    shots: list[dict[str, Any]],
    errors: list[str],
) -> None:
    """有导演时要求可机检的曝光/机位高度策略；无导演可出片。"""
    director = development.get("director") or {}
    if not director.get("name"):
        return
    constraints = director.get("crew_constraints")
    if not isinstance(constraints, dict):
        errors.append(
            "指定导演必须提供 crew_constraints"
            "（exposure_bias / camera_height_policy），不能只加长 prompt"
        )
        return
    missing = _missing(constraints, ("exposure_bias", "camera_height_policy"))
    if missing:
        errors.append("导演 crew_constraints 缺少: " + ",".join(missing))
        return
    exposure = constraints.get("exposure_bias")
    height = constraints.get("camera_height_policy")
    if exposure not in EXPOSURE_BIASES:
        errors.append(f"导演 exposure_bias={exposure!r} 不在受控词表")
    if height not in CAMERA_HEIGHT_POLICIES:
        errors.append(f"导演 camera_height_policy={height!r} 不在受控词表")
    if exposure not in EXPOSURE_BIASES or height not in CAMERA_HEIGHT_POLICIES:
        return
    for shot in shots:
        sid = shot.get("id", "unknown")
        crew = shot.get("crew") or {}
        lighting = crew.get("lighting") or {}
        camera = crew.get("cinematography") or {}
        if lighting.get("exposure_bias") != exposure:
            errors.append(
                f"{sid} lighting.exposure_bias 必须对齐导演约束 {exposure}"
            )
        if camera.get("height_strategy") != height:
            errors.append(
                f"{sid} cinematography.height_strategy 必须对齐导演约束 {height}"
            )


def _load_json_if_exists(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def validate_crew_pipeline(
    development: dict[str, Any],
    storyboard: dict[str, Any],
    *,
    music_structure: dict[str, Any] | None = None,
    timeline: dict[str, Any] | None = None,
    fps: float | None = None,
    project: Path | None = None,
) -> list[str]:
    """返回结构与视听语言错误；空列表表示可以进入导演风格编译。"""

    errors: list[str] = []
    if project is not None:
        root = Path(project)
        if music_structure is None:
            music_structure = _load_json_if_exists(root / "state" / "music_structure.json")
        if timeline is None:
            timeline = _load_json_if_exists(root / "state" / "timeline.json")
        if fps is None and storyboard.get("fps") is not None:
            try:
                fps = float(storyboard["fps"])
            except (TypeError, ValueError):
                fps = None
        if fps is None and timeline is not None:
            try:
                fps = float(timeline.get("fps_assumed") or 24)
            except (TypeError, ValueError):
                fps = 24.0
    if fps is None:
        fps = 24.0

    bible = development.get("crew_bible") or {}
    if bible.get("schema_version") != 2:
        errors.append("crew_bible.schema_version 必须为 2")
    for department, fields in DEPARTMENT_FIELDS.items():
        data = bible.get(department) or {}
        missing = _missing(data, fields)
        if missing:
            errors.append(f"剧组部门 {department} 缺少交接物: {','.join(missing)}")
    if (bible.get("directing") or {}).get("style_application_order") != "after_film_grammar":
        errors.append("导演风格只能在基础视听语言通过后应用: style_application_order=after_film_grammar")
    _reject_vague_aesthetic("crew_bible.character_design", bible.get("character_design") or {}, errors)
    _reject_vague_aesthetic("crew_bible.production_design", bible.get("production_design") or {}, errors)
    handoffs = bible.get("handoffs") or []
    actual_handoffs: set[tuple[str, str]] = set()
    for handoff in handoffs:
        source, target = handoff.get("from"), handoff.get("to")
        if source and target:
            actual_handoffs.add((str(source), str(target)))
        if not handoff.get("deliverable") or not handoff.get("acceptance"):
            errors.append(f"部门交接 {source}->{target} 缺少 deliverable/acceptance")
    missing_handoffs = REQUIRED_HANDOFFS - actual_handoffs
    if missing_handoffs:
        errors.append("缺少部门交接: " + ",".join(
            f"{source}->{target}" for source, target in sorted(missing_handoffs)
        ))

    shots = storyboard.get("shots") or []
    for scene in development.get("scenes", []):
        scene_id = scene.get("id", "unknown")
        art = scene.get("art_direction") or {}
        light = scene.get("lighting_plan") or {}
        missing_art = _missing(art, SCENE_ART_FIELDS)
        missing_light = _missing(light, SCENE_LIGHT_FIELDS)
        if missing_art:
            errors.append(f"场景 {scene_id} 美术设计缺少: {','.join(missing_art)}")
        if missing_light:
            errors.append(f"场景 {scene_id} 灯光设计缺少: {','.join(missing_light)}")
        _reject_vague_aesthetic(f"场景 {scene_id} art_direction", art, errors)
        _reject_vague_aesthetic(f"场景 {scene_id} lighting_plan", light, errors)
        if light:
            _validate_key_direction(f"场景 {scene_id} lighting_plan", light.get("key_direction"), errors)
        if scene.get("kind") not in {"narrative", "performance", "montage", "oner"}:
            errors.append(f"场景 {scene_id} 必须声明kind")
        requirements = scene.get("coverage_requirements")
        scene_shots = [s for s in shots if s.get("scene_id") == scene_id]
        roles = {(s.get("crew", {}).get("editing") or {}).get("coverage_role")
                 for s in scene_shots}
        if not isinstance(requirements, list) or not requirements or not set(requirements) <= roles:
            errors.append(f"场景 {scene_id} coverage_requirements未被正式镜头兑现")
        axes = scene.get("axes") or {}
        if not isinstance(axes, dict) or not axes:
            errors.append(f"场景 {scene_id} 缺少轴线注册表axes")
        else:
            seen_geometry = set()
            for aid, axis in axes.items():
                try:
                    a, b = axis["start"], axis["end"]
                    geometry = tuple(sorted((tuple(map(number, a)), tuple(map(number, b)))))
                    if len(a) != 2 or len(b) != 2 or a == b or geometry in seen_geometry:
                        raise ValueError
                    seen_geometry.add(geometry)
                except (ValueError, KeyError, TypeError):
                    errors.append(f"场景 {scene_id} 轴线{aid}无效或同几何重复命名")
        _validate_scene_topology(scene, scene_shots, errors)

    shots = storyboard.get("shots") or []
    for shot in shots:
        sid = shot.get("id", "unknown")
        crew = shot.get("crew") or {}
        for department, fields in SHOT_DEPARTMENT_FIELDS.items():
            missing = _missing(crew.get(department) or {}, fields)
            if missing:
                errors.append(f"{sid} / {department} 缺少: {','.join(missing)}")
        camera = crew.get("cinematography") or {}
        try:
            from .pipeline_contracts import CAMERA_MOVEMENTS
        except ImportError:
            from pipeline_contracts import CAMERA_MOVEMENTS
        motion_camera = (shot.get('motion_design') or {}).get('camera_motion')
        if motion_camera and CAMERA_MOVEMENTS.get(motion_camera, motion_camera) != CAMERA_MOVEMENTS.get(
                camera.get('camera_movement'), camera.get('camera_movement')):
            errors.append(f"{sid} 运镜事实源冲突: motion_design/crew.camera_movement")
        if SHOT_SIZES.get(shot.get("shot_type"), shot.get("shot_type")) != SHOT_SIZES.get(
                camera.get("shot_size"), camera.get("shot_size")):
            errors.append(f"{sid} 景别事实源冲突: shot_type/crew.shot_size")
        scene = next((s for s in development.get("scenes", []) if s.get("id") == shot.get("scene_id")), {})
        try:
            axis = scene["axes"][camera["axis_id"]]
            x, y = map(number, camera["position_xy"])
            ax, ay = map(number, axis["start"])
            bx, by = map(number, axis["end"])
            cross = (bx-ax)*(y-ay)-(by-ay)*(x-ax)
            side = "center" if abs(cross) < 1e-6 else "left" if cross > 0 else "right"
            if side != camera.get("camera_side"):
                errors.append(f"{sid} 摄影机几何轴侧与camera_side不一致")
        except (KeyError, TypeError, ValueError):
            errors.append(f"{sid} 轴线ID未注册或position_xy无效")
        if camera.get("camera_side") not in SIDES:
            errors.append(f"{sid} camera_side 必须是 left/right/center")
        try:
            angle = float(camera.get("angle_deg"))
            if not math.isfinite(angle) or not -180 <= angle <= 180:
                raise ValueError
        except (TypeError, ValueError):
            errors.append(f"{sid} angle_deg 必须是 -180..180 的实数")
        for field in ("headroom", "look_room"):
            try:
                value = float(camera.get(field))
                if not math.isfinite(value) or not 0 <= value <= 1:
                    raise ValueError
            except (TypeError, ValueError):
                errors.append(f"{sid} {field} 必须是 0..1 的画幅比例")
        focal_length = _positive_number(camera.get("focal_length_mm"))
        if focal_length is None:
            errors.append(f"{sid} focal_length_mm 必须为正数")
        lighting = crew.get("lighting") or {}
        try:
            color_temp = float(lighting.get("color_temp_k"))
            if not math.isfinite(color_temp) or not 1000 <= color_temp <= 20000:
                raise ValueError
        except (TypeError, ValueError):
            errors.append(f"{sid} color_temp_k 必须是 1000..20000K")
        _reject_vague_aesthetic(f"{sid} lighting", lighting, errors)
        _validate_key_direction(f"{sid} lighting", lighting.get("key_direction"), errors)
        production_design = crew.get("production_design") or {}
        _reject_vague_aesthetic(f"{sid} production_design", production_design, errors)
        blocking = crew.get("blocking") or {}
        for field in ("entry_direction", "exit_direction", "screen_direction"):
            if blocking.get(field) not in DIRECTIONS:
                errors.append(f"{sid} {field} 方向值无效")
        editing = crew.get("editing") or {}
        if type(editing.get("hook")) is not bool:
            errors.append(f"{sid} hook必须为布尔值")
        for field in ("entry_edge", "exit_edge"):
            if blocking.get(field) not in {"left", "right", "top", "bottom", "none"}:
                errors.append(f"{sid} {field}须独立声明画框边缘")
        if editing.get("transition_type") not in TRANSITIONS:
            errors.append(f"{sid} transition_type 不在受控词表")
        if editing.get("audio_transition") not in AUDIO_TRANSITIONS:
            errors.append(f"{sid} audio_transition 不在受控词表")
        novelty = editing.get("novelty_axis")
        if novelty not in NOVELTY_AXES:
            errors.append(
                f"{sid} novelty_axis 必须使用受控枚举: "
                + "/".join(sorted(NOVELTY_AXES))
            )
        change_code = editing.get("visual_change_code")
        if change_code not in VISUAL_CHANGE_CODES:
            errors.append(
                f"{sid} visual_change_code 必须使用受控枚举"
                "（禁止只改 visual_change 措辞刷新注意力）"
            )
        _reject_vague_aesthetic(f"{sid} editing.visual_change", editing.get("visual_change"), errors)
        character = crew.get("character_design") or {}
        _reject_vague_aesthetic(f"{sid} character_design", character, errors)

    _validate_director_crew_constraints(development, shots, errors)
    errors.extend(validate_sequence_language(
        shots, bible.get("editing") or {},
        music_structure=music_structure, timeline=timeline, fps=fps,
        scenes=development.get("scenes"),
    ))
    # P3.1：生产路径 revision 必须为 2（禁止旧 revision 跳过 sequence_direction）
    if project is not None:
        rev = development.get("creative_workflow_revision")
        if rev != 2:
            errors.append(
                "creative_workflow_revision 必须为 2；"
                "revision≠2 禁止跳过 sequence_direction（eligible=false）"
            )
        try:
            from .film_grammar_corpus import audit_binding
        except ImportError:
            from film_grammar_corpus import audit_binding
        grammar_report = audit_binding(development, storyboard, Path(project))
        if grammar_report:
            errors.extend(f"电影语法画像: {item}" for item in grammar_report.get("errors") or [])
    return errors



def _normalize_visual_fingerprint(text: Any) -> str:
    raw = str(text or "").casefold()
    return re.sub(r"[^\w]+", "", raw, flags=re.UNICODE)


def _eyeline_quadrant(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip().casefold()
    if not text:
        return None
    # Accept controlled tokens directly
    compact = text.replace(" ", "_").replace("-", "_")
    if compact in EYELINE_QUADRANTS:
        return compact
    # Heuristic map from Chinese/English free text used in fixtures
    has_left = any(token in text for token in ("左", "left"))
    has_right = any(token in text for token in ("右", "right"))
    has_up = any(token in text for token in ("上", "up", "上方"))
    has_down = any(token in text for token in ("下", "down", "下方"))
    if "toward" in text or "朝镜头" in text or "看镜头" in text:
        return "toward_camera"
    if "away" in text or "背对" in text or "离开镜头" in text:
        return "away_camera"
    if has_left and has_up:
        return "left_up"
    if has_left and has_down:
        return "left_down"
    if has_right and has_up:
        return "right_up"
    if has_right and has_down:
        return "right_down"
    if has_left:
        return "left"
    if has_right:
        return "right"
    if has_up:
        return "up"
    if has_down:
        return "down"
    if "中" in text or "center" in text:
        return "center"
    return None


def _directions_reversed(prev: Any, curr: Any) -> bool:
    if not isinstance(prev, str) or not isinstance(curr, str):
        return False
    a, b = prev.strip(), curr.strip()
    return SCREEN_DIRECTION_REVERSE.get(a) == b


def _normalize_body_orientation(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or text in {"none", "static"}:
        return None
    if text in BODY_ORIENTATION_NORMALIZE:
        return BODY_ORIENTATION_NORMALIZE[text]
    folded = text.casefold()
    for key, norm in BODY_ORIENTATION_NORMALIZE.items():
        if key.casefold() == folded:
            return norm
    # 宽松：含「朝左/朝右」等
    if "朝左" in text or "向左" in text:
        return "right_to_left"
    if "朝右" in text or "向右" in text:
        return "left_to_right"
    if "背对" in text or "背向" in text:
        return "away_camera"
    if "面向镜头" in text or "朝向镜头" in text or "正对镜头" in text:
        return "toward_camera"
    return None


def _is_static_path(blocking: dict[str, Any]) -> bool:
    entry = blocking.get("entry_direction")
    exit_ = blocking.get("exit_direction")
    return entry in {None, "", "none", "static"} and exit_ in {None, "", "none", "static"}


def validate_sequence_language(
    shots: list[dict[str, Any]],
    editing_bible: dict[str, Any],
    *,
    music_structure: dict[str, Any] | None = None,
    timeline: dict[str, Any] | None = None,
    fps: float = 24.0,
    scenes: list[dict[str, Any]] | None = None,
) -> list[str]:
    """检查轴线、动线、重复机位、30 度规则、空间建立、覆盖角色、music_cue 与节奏。"""

    errors: list[str] = []
    if not shots:
        return ["剧组审计没有镜头"]

    errors.extend(_validate_music_cues(shots, music_structure, timeline, fps))
    effective, pacing_errors = _effective_pacing_policy(editing_bible, music_structure)
    errors.extend(pacing_errors)
    numeric = {key: effective.get(key) for key in (
        "max_average_shot_sec", "slow_shot_threshold_sec", "max_consecutive_slow_shots",
        "max_consecutive_same_rhythm", "hook_deadline_sec", "max_hook_gap_sec",
        "max_visual_stasis_sec", "min_shot_size_variety_per_scene",
        "max_consecutive_near_same_angle",
    )}
    invalid = [key for key, value in numeric.items() if value is None]

    durations = [_shot_duration(shot) for shot in shots]
    if any(not math.isfinite(value) or value <= 0 for value in durations):
        errors.append("所有剪辑镜头必须有正向有限时长")
        return errors
    if not invalid:
        average = sum(durations) / len(durations)
        if average > numeric["max_average_shot_sec"] + 1e-6:
            errors.append(
                f"平均镜头时长 {average:.2f}s 超过节奏策略 {numeric['max_average_shot_sec']:.2f}s"
            )
        slow_run = _max_run(
            durations, lambda value: value > numeric["slow_shot_threshold_sec"] + 1e-6
        )
        if slow_run > int(numeric["max_consecutive_slow_shots"]):
            errors.append(f"连续慢镜头达到 {slow_run} 个，节奏拖沓")

        rhythm = [(shot.get("crew") or {}).get("editing", {}).get("rhythm_class") for shot in shots]
        for start in range(len(rhythm)):
            run = 1
            while start + run < len(rhythm) and rhythm[start + run] == rhythm[start]:
                run += 1
            if run > int(numeric["max_consecutive_same_rhythm"]):
                errors.append(f"从 {shots[start].get('id')} 起连续 {run} 镜节奏类型相同")
                break

        hook_deadline = numeric["hook_deadline_sec"]
        if not any(
            ((shot.get("crew") or {}).get("editing", {}).get("hook")) is True
            and float((shot.get("window") or {}).get("start", 0)) <= hook_deadline
            for shot in shots
        ):
            errors.append(f"前 {hook_deadline:.2f}s 内缺少明确视觉/叙事钩子")

        hook_times = [
            float((shot.get("window") or {}).get("start", 0)) for shot in shots
            if ((shot.get("crew") or {}).get("editing", {}).get("hook")) is True
        ]
        timeline_end = max(float((shot.get("window") or {}).get("end", 0)) for shot in shots)
        checkpoints = [0.0] + hook_times + [timeline_end]
        if any(right - left > numeric["max_hook_gap_sec"] + 1e-6
               for left, right in zip(checkpoints, checkpoints[1:])):
            errors.append(f"视觉/叙事钩子间隔超过 {numeric['max_hook_gap_sec']:.2f}s")

    scene_established: set[str] = set()
    signatures: list[tuple[Any, ...]] = []
    near_angle_run = 0
    for index, shot in enumerate(shots):
        sid = shot.get("id", "unknown")
        crew = shot.get("crew") or {}
        camera = crew.get("cinematography") or {}
        blocking = crew.get("blocking") or {}
        editing = crew.get("editing") or {}
        if type(editing.get("hook")) is not bool:
            errors.append(f"{sid} hook必须为布尔值")
        scene_id = shot.get("scene_id")
        geography = editing.get("geography_status")
        if scene_id not in scene_established:
            if geography not in {"establish", "established_from_previous"}:
                errors.append(f"{sid} 在碎切前没有建立场景地理")
            else:
                scene_established.add(scene_id)

        signature = (
            scene_id,
            camera.get("viewpoint_id"),
            camera.get("shot_size"),
            camera.get("camera_height"),
            camera.get("camera_side"),
            camera.get("lens_behavior"),
            camera.get("axis_id"),
            _angle_bucket(camera.get("angle_deg")),
            _position_bucket(camera.get("position_xy")),
        )
        signatures.append(signature)
        if index >= 2 and signatures[index - 2] == signatures[index - 1] == signature:
            errors.append(f"{sid} 与前两镜重复同一机位/景别/高度，禁止连续三个同角度")
        # 切碎 scene_id 稀释：同 viewpoint/同轴/同侧/近角跨场短窗仍计三连
        axis_sig = (
            camera.get("viewpoint_id"),
            camera.get("axis_id"),
            camera.get("camera_side"),
            _angle_bucket(camera.get("angle_deg")),
        )
        if index >= 2:
            prev2 = shots[index - 2]
            prev1 = shots[index - 1]
            p2c = ((prev2.get("crew") or {}).get("cinematography") or {})
            p1c = ((prev1.get("crew") or {}).get("cinematography") or {})
            sig2 = (
                p2c.get("viewpoint_id"), p2c.get("axis_id"),
                p2c.get("camera_side"), _angle_bucket(p2c.get("angle_deg")),
            )
            sig1 = (
                p1c.get("viewpoint_id"), p1c.get("axis_id"),
                p1c.get("camera_side"), _angle_bucket(p1c.get("angle_deg")),
            )
            if axis_sig == sig1 == sig2 and None not in axis_sig[:3]:
                try:
                    t0 = float((prev2.get("window") or {}).get("start", 0))
                    t1 = float((shot.get("window") or {}).get("end", 0))
                    span = t1 - t0
                except (TypeError, ValueError):
                    span = 0.0
                if span <= CROSS_SCENE_ANGLE_WINDOW_SEC + 1e-6:
                    # 仅当至少跨了一个 scene_id 时单独报（同场已由 signature 覆盖）
                    scenes_triple = {
                        prev2.get("scene_id"), prev1.get("scene_id"), scene_id,
                    }
                    if len(scenes_triple) > 1:
                        errors.append(
                            f"{sid} 跨 scene 短窗({span:.2f}s≤{CROSS_SCENE_ANGLE_WINDOW_SEC:.0f}s)"
                            f"同 viewpoint/轴/侧/角三连，禁止切碎 scene_id 稀释同角"
                        )

        if index == 0:
            near_angle_run = 1
            continue
        previous = shots[index - 1]
        pcrew = previous.get("crew") or {}
        pcamera = pcrew.get("cinematography") or {}
        pblocking = pcrew.get("blocking") or {}
        relation = editing.get("time_relation", "continuous")
        if relation not in {"continuous", "ellipsis", "flashback", "parallel"}:
            errors.append(f"{sid} time_relation无效")
        if relation != "continuous" and not str(editing.get("time_relation_evidence") or "").strip():
            errors.append(
                f"{sid} time_relation={relation} 须提供 time_relation_evidence，"
                "禁止用省略/闪回标签静默跳过同场动线核验"
            )
        same_scene = previous.get("scene_id") == scene_id and relation == "continuous"
        bridge = editing.get("axis_bridge") or {}
        reset = False
        if editing.get("axis_reset") is True:
            bridge_shot = next((s for s in shots[:index+1] if s.get("id") == bridge.get("shot_id")), None)
            try:
                duration = _shot_duration(bridge_shot or {})
                start, end = number(bridge["start_sec"]), number(bridge["end_sec"])
                bridge_crew = (bridge_shot or {}).get("crew", {})
                reset = (bridge_shot is not None and bridge_shot["id"] in {sid, previous["id"]}
                         and 0 <= start < end <= duration and bool(bridge.get("evidence"))
                         and ((bridge.get("type") == "neutral" and bridge_crew.get("cinematography", {}).get("camera_side") == "center")
                              or (bridge.get("type") == "reestablish" and bridge_crew.get("editing", {}).get("geography_status") == "establish")))
            except (ValueError, KeyError, TypeError):
                pass
            if not reset:
                errors.append(f"{sid} axis_reset缺少可核验的相邻桥接镜与时间码")
        if same_scene and pcamera.get("axis_id") != camera.get("axis_id") and not reset:
            errors.append(f"{sid} 更换axis_id须有明确空间重建证据")
        deliberate = editing.get("deliberate_jump_cut") is True and bool(
            editing.get("jump_cut_justification")
        )
        same_scene_axis = (
            same_scene
            and pcamera.get("axis_id") == camera.get("axis_id")
        )
        angle_delta = _circular_angle_delta(camera.get("angle_deg"), pcamera.get("angle_deg"))
        if angle_delta is None:
            angle_delta = 999.0
        try:
            cross_span = (
                float((shot.get("window") or {}).get("end", 0))
                - float((previous.get("window") or {}).get("start", 0))
            )
        except (TypeError, ValueError):
            cross_span = 999.0
        cross_scene_near_axis = (
            (not same_scene)
            and pcamera.get("viewpoint_id") not in {None, ""}
            and pcamera.get("viewpoint_id") == camera.get("viewpoint_id")
            and pcamera.get("axis_id") not in {None, ""}
            and pcamera.get("axis_id") == camera.get("axis_id")
            and cross_span <= CROSS_SCENE_ANGLE_WINDOW_SEC + 1e-6
        )
        near_same_angle = (
            (same_scene_axis or cross_scene_near_axis)
            and pcamera.get("camera_side") in {"left", "right", "center"}
            and camera.get("camera_side") == pcamera.get("camera_side")
            and angle_delta < NEAR_ANGLE_DEG
            and not reset
        )
        if near_same_angle:
            near_angle_run += 1
        else:
            near_angle_run = 1
        if (
            not invalid
            and near_same_angle
            and near_angle_run > int(numeric["max_consecutive_near_same_angle"])
        ):
            errors.append(
                f"{sid} 与前序镜头同轴同侧且角度变化不足 {NEAR_ANGLE_DEG:.0f}°，"
                f"近同角度连续 {near_angle_run} 镜超过上限 "
                f"{int(numeric['max_consecutive_near_same_angle'])}"
            )
        if same_scene_axis:
            if (pcamera.get("camera_side") in {"left", "right"}
                    and camera.get("camera_side") in {"left", "right"}
                    and pcamera.get("camera_side") != camera.get("camera_side")
                    and not reset):
                errors.append(f"{sid} 未用中性/建立镜重置就跨越 180 度轴线")
            # P0：同轴同侧时景别变化不能豁免 30°；有意跳切须 deliberate_jump_cut
            if (pcamera.get("camera_side") == camera.get("camera_side")
                    and angle_delta < NEAR_ANGLE_DEG and not deliberate):
                errors.append(
                    f"{sid} 与上一镜同轴同侧且机位变化仅 {angle_delta:.1f}°，"
                    f"不满足 {NEAR_ANGLE_DEG:.0f} 度变化（景别变化不能豁免）"
                )

        exit_direction = pblocking.get("exit_direction")
        entry_direction = blocking.get("entry_direction")
        continuous = relation == "continuous" and (same_scene or (bool(blocking.get("continuity_group")) and
                                   blocking.get("continuity_group") == pblocking.get("continuity_group")))
        if (continuous and exit_direction not in {None, "", "none", "static"}
                and entry_direction not in {None, "", "none", "static"}
                and exit_direction != entry_direction
                and not reset):
            errors.append(f"{sid} 入画方向与上一镜出画方向冲突，人物动线跳变")
        if continuous and pblocking.get("exit_edge") in {"left", "right"}:
            expected_edge = "left" if pblocking["exit_edge"] == "right" else "right"
            if blocking.get("entry_edge") not in {"none", expected_edge} and not reset:
                errors.append(f"{sid} 入画边缘未接住上一镜出画边缘")
        # 正反打切换观察对象时，不能把甲的站位/视线当成乙的连续动作。
        # 只有完整状态承接且两个对象的节点都能核对，才按不同主体处理。
        prev_subject, curr_subject = pblocking.get('subject_id'), blocking.get('subject_id')
        before_state, after_state = shot.get('state_before'), shot.get('state_after')
        grounded_subject_switch = (
            isinstance(prev_subject, str) and bool(prev_subject.strip())
            and isinstance(curr_subject, str) and bool(curr_subject.strip()) and prev_subject != curr_subject
            and isinstance(before_state, dict) and isinstance(after_state, dict)
            and prev_subject in before_state and curr_subject in before_state and curr_subject in after_state
            and bool(pblocking.get('end_mark')) and bool(blocking.get('start_mark')) and bool(blocking.get('end_mark'))
            and before_state == previous.get('state_after')
            and before_state.get(prev_subject) == pblocking.get('end_mark')
            and before_state.get(curr_subject) == blocking.get('start_mark')
            and after_state.get(curr_subject) == blocking.get('end_mark'))
        if (continuous and same_scene and not reset and grounded_subject_switch
                and not str(blocking.get('crossing_rule') or '').strip()):
            errors.append(f'{sid} 切换观察对象仍须声明crossing_rule')
        # P2 G4：同一对象的continuous镜头比对屏幕方向与视线象限。
        if continuous and same_scene and not reset and not grounded_subject_switch:
            prev_screen = pblocking.get("screen_direction")
            curr_screen = blocking.get("screen_direction")
            if (prev_screen not in {None, "", "none", "static"}
                    and curr_screen not in {None, "", "none", "static"}
                    and _directions_reversed(prev_screen, curr_screen)
                    and not str(blocking.get("direction_reverse_evidence") or "").strip()):
                errors.append(
                    f"{sid} screen_direction 相对上一镜无理由反向"
                    f"({prev_screen}→{curr_screen})；若有意反向须填 direction_reverse_evidence"
                )
            prev_eye = _eyeline_quadrant(pblocking.get("eyeline_vector"))
            curr_eye = _eyeline_quadrant(blocking.get("eyeline_vector"))
            if prev_eye and curr_eye:
                reverse_pairs = {
                    ("left", "right"), ("right", "left"),
                    ("up", "down"), ("down", "up"),
                    ("left_up", "right_down"), ("right_down", "left_up"),
                    ("left_down", "right_up"), ("right_up", "left_down"),
                    ("toward_camera", "away_camera"), ("away_camera", "toward_camera"),
                }
                if (prev_eye, curr_eye) in reverse_pairs and not str(
                        blocking.get("eyeline_reverse_evidence") or "").strip():
                    errors.append(
                        f"{sid} eyeline_vector 象限相对上一镜无理由反向"
                        f"({prev_eye}→{curr_eye})；若有意反向须填 eyeline_reverse_evidence"
                    )
            # P3.3：身体朝向冲突须证据
            prev_body = _normalize_body_orientation(pblocking.get("body_orientation"))
            curr_body = _normalize_body_orientation(blocking.get("body_orientation"))
            if (prev_body and curr_body and _directions_reversed(prev_body, curr_body)
                    and not str(blocking.get("body_orientation_evidence") or "").strip()):
                errors.append(
                    f"{sid} body_orientation 相对上一镜无理由反向"
                    f"({prev_body}→{curr_body})；若有意反向须填 body_orientation_evidence"
                )
            # static/口型站桩：须 crossing_rule + 站位承接
            if _is_static_path(blocking) or _is_static_path(pblocking):
                if not str(blocking.get("crossing_rule") or "").strip():
                    errors.append(
                        f"{sid} 同场 continuous 且动线为 static/none 时必须声明 crossing_rule"
                    )
                prev_end = str(pblocking.get("end_mark") or "").strip()
                curr_start = str(blocking.get("start_mark") or "").strip()
                if prev_end and curr_start and prev_end != curr_start:
                    errors.append(
                        f"{sid} static/站桩段站位未承接：上一镜 end_mark≠本镜 start_mark"
                    )
        if not editing.get("incoming_relation") or not pcrew.get("editing", {}).get("outgoing_relation"):
            errors.append(f"{sid} 与上一镜缺少双向剪辑关系说明")

    attention_keys: list[tuple[Any, ...]] = []
    for shot in shots:
        editing = (shot.get("crew") or {}).get("editing") or {}
        novelty = editing.get("novelty_axis")
        code = editing.get("visual_change_code")
        # 指纹：优先受控 code；否则 novelty + 去标点视觉变化，防止只改措辞刷新
        fingerprint = code or (
            str(novelty or ""),
            _normalize_visual_fingerprint(editing.get("visual_change")),
        )
        attention_keys.append((novelty, fingerprint))
    for index in range(2, len(attention_keys)):
        if attention_keys[index - 2] == attention_keys[index - 1] == attention_keys[index]:
            errors.append(
                f"{shots[index].get('id')} 连续三个镜头 novelty/visual_change 指纹相同，"
                "禁止只改措辞假装刷新注意力"
            )
            break
    if not invalid:
        run_value: Any = object()
        run_duration = 0.0
        max_stasis = 0.0
        for key, duration in zip(attention_keys, durations):
            if key == run_value:
                run_duration += duration
            else:
                run_value, run_duration = key, duration
            max_stasis = max(max_stasis, run_duration)
        if max_stasis > numeric["max_visual_stasis_sec"] + 1e-6:
            errors.append(
                f"相同注意力指纹持续 {max_stasis:.2f}s，超过 "
                f"{numeric['max_visual_stasis_sec']:.2f}s"
            )
        minimum_variety = int(numeric["min_shot_size_variety_per_scene"])
        scene_sizes: dict[str, set[Any]] = {}
        for shot in shots:
            scene_id = str(shot.get("scene_id"))
            crew = shot.get("crew") or {}
            scene_sizes.setdefault(scene_id, set()).add(
                (crew.get("cinematography") or {}).get("shot_size")
            )
        for scene_id, sizes in scene_sizes.items():
            if len(sizes - {None, ""}) < minimum_variety:
                errors.append(f"场景 {scene_id} 景别覆盖不足 {minimum_variety} 种")
    errors.extend(_validate_scene_coverage_roles(shots, scenes))
    return errors



def build_report(development: dict[str, Any], storyboard: dict[str, Any],
                 project: Path | None = None) -> dict[str, Any]:
    errors = validate_crew_pipeline(development, storyboard, project=project)
    warnings: list[str] = []
    grammar_report = None
    if project:
        try:
            from .film_grammar_corpus import audit_binding
        except ImportError:
            from film_grammar_corpus import audit_binding
        grammar_report = audit_binding(development, storyboard, project)
        # errors 已由 validate_crew_pipeline 并入；此处只收 warnings 与报告字段
        if grammar_report:
            warnings.extend(grammar_report.get("warnings") or [])
    shots = storyboard.get("shots") or []
    durations = [_shot_duration(shot) for shot in shots] if shots else []
    camera_rows = [((shot.get("crew") or {}).get("cinematography") or {}) for shot in shots]
    hook_times = sorted(
        float((shot.get("window") or {}).get("start", 0)) for shot in shots
        if ((shot.get("crew") or {}).get("editing") or {}).get("hook") is True
    )
    timeline_end = max((float((shot.get("window") or {}).get("end", 0)) for shot in shots),
                       default=0.0)
    checkpoints = [0.0] + hook_times + [timeline_end]
    scene_coverage: dict[str, dict[str, list[str]]] = {}
    for shot in shots:
        scene_id = str(shot.get("scene_id"))
        crew = shot.get("crew") or {}
        camera = crew.get("cinematography") or {}
        editing = crew.get("editing") or {}
        row = scene_coverage.setdefault(scene_id, {"shot_sizes": [], "viewpoints": [], "roles": []})
        for key, value in (
            ("shot_sizes", camera.get("shot_size")),
            ("viewpoints", camera.get("viewpoint_id")),
            ("roles", editing.get("coverage_role")),
        ):
            if value not in (None, "") and str(value) not in row[key]:
                row[key].append(str(value))
    source_fingerprints = None
    if project is not None:
        try:
            from .pipeline_contracts import digest as _digest
        except ImportError:
            from pipeline_contracts import digest as _digest
        root = Path(project)
        source_fingerprints = {
            "development": _digest(root / "state/development.json") if (root / "state/development.json").is_file() else None,
            "storyboard": _digest(root / "state/storyboard.json") if (root / "state/storyboard.json").is_file() else None,
        }
    return {
        "status": "blocked" if errors else "structural_pass",
        "errors": errors,
        "warnings": warnings,
        "source_fingerprints": source_fingerprints,
        "metrics": {
            "shot_count": len(shots),
            "average_shot_sec": round(sum(durations) / len(durations), 3) if durations else None,
            "department_count": len(DEPARTMENT_FIELDS),
            "handoff_count": len((development.get("crew_bible") or {}).get("handoffs") or []),
            "distinct_shot_sizes": len({row.get("shot_size") for row in camera_rows} - {None, ""}),
            "distinct_viewpoints": len({row.get("viewpoint_id") for row in camera_rows} - {None, ""}),
            "max_hook_gap_sec": round(max((right - left for left, right in
                                            zip(checkpoints, checkpoints[1:])), default=0.0), 3),
            "scene_coverage": scene_coverage,
        },
        "limits": "结构通过不等于成片好看；仍需动态观看和用户审片",
        "film_grammar_reference": grammar_report,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="检查八部门交接、连续性与剪辑节奏")
    parser.add_argument("project_dir", type=Path)
    args = parser.parse_args()
    project = args.project_dir.resolve()
    development = json.loads((project / "state/development.json").read_text(encoding="utf-8"))
    storyboard = json.loads((project / "state/storyboard.json").read_text(encoding="utf-8"))
    report = build_report(development, storyboard, project)
    gate = project / "gates/crew_pipeline.json"
    gate.parent.mkdir(parents=True, exist_ok=True)
    gate.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
