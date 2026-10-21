#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""构建并校验模型中立的多模态全能参考蓝图。

本模块只描述“哪份素材负责什么”和“每个时间段怎样使用它”。它不上传素材、
不选择视频模型、不拼装供应商 API 参数，也不提交生成任务。
"""
from __future__ import annotations

from typing import Any
try:
    from .pipeline_contracts import REFERENCE_INTENTS, timeline_errors
except ImportError:
    from pipeline_contracts import REFERENCE_INTENTS, timeline_errors


ALLOWED_MODALITIES = {"image", "video", "audio"}
ALLOWED_INTENTS = REFERENCE_INTENTS
CHARACTER_INTENTS = ["character_identity", "wardrobe"]
CHARACTER_EXCLUSIONS = ["background", "composition", "lighting", "pose"]
KEYFRAME_INTENTS = ["composition", "location", "blocking_start_state"]
KEYFRAME_EXCLUSIONS = ["character_identity", "wardrobe", "performance_timing"]


def _source_present(binding: dict[str, Any]) -> bool:
    source = binding.get("source") or {}
    return any(str(source.get(key, "")).strip()
               for key in ("local_path", "asset_id", "url"))


def build_omni_reference_blueprint(
        chapter: dict[str, Any], beats: list[dict[str, Any]], *,
        protagonist_source: dict[str, Any] | None,
        keyframe_sources: dict[str, dict[str, Any]],
        character_sources: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """把人物、逐镜关键帧和章节音频编译成后端无关的全能参考蓝图。"""

    bindings: list[dict[str, Any]] = []
    subject_definitions: list[dict[str, Any]] = []
    characters = character_sources if character_sources is not None else ([{
        'character': 'protagonist', 'source': protagonist_source}] if protagonist_source else [])
    character_handles = []
    for number, character in enumerate(characters, 1):
        handle = f'@图片 {number}'
        character_handles.append(handle)
        binding = {
            "handle": handle, "modality": "image",
            "source": character['source'],
            "intents": CHARACTER_INTENTS,
            "do_not_inherit": CHARACTER_EXCLUSIONS,
            "binding_status": character.get('binding_status', 'awaiting_adapter_upload'),
        }
        if character.get('upload_receipt_path'):
            binding['upload_receipt_path'] = character['upload_receipt_path']
        bindings.append(binding)
        subject_definitions.append({
            "subject_id": character['character'], "handle": handle,
            "retain": ["五官比例", "发型", "耳饰", "同一套服装的结构与材质"],
            "allow_change": ["表情", "视线", "姿态", "衣料运动", "自然受光"],
            "reject_transfer": CHARACTER_EXCLUSIONS,
        })

    for beat in beats:
        shot_id = beat["shot_id"]
        source = keyframe_sources[shot_id]
        bindings.append({
            "handle": beat["keyframe_handle"], "modality": "image",
            "source": source,
            "intents": KEYFRAME_INTENTS,
            "do_not_inherit": KEYFRAME_EXCLUSIONS,
            "shot_id": shot_id,
            "binding_status": "awaiting_adapter_upload",
        })

    audio_source = chapter.get("audio_asset") or {"chapter_id": chapter["id"]}
    bindings.append({
        "handle": "@音频 1", "modality": "audio", "source": audio_source,
        "intents": ["performance_timing", "action_timing"],
        "condition_only": True,
        "generate_audio": False,
        "final_master_replaced_locally": True,
        "binding_status": "available" if chapter.get("audio_asset") else "planned",
    })

    timeline = []
    for index, beat in enumerate(beats):
        handles = [beat["keyframe_handle"], "@音频 1"]
        handles = character_handles+handles
        timeline.append({
            "shot_id": beat["shot_id"],
            "start_sec": beat["start_sec"],
            "end_sec": beat["end_sec"],
            "reference_handles": handles,
            "editorial_role": beat.get("editorial_role", ""),
            "cut_relation": beat.get("cut_relation", "") if index < len(beats) - 1 else "chapter_end",
            "story_change": beat["story_change"],
            "film_grammar": beat.get('film_grammar'),
            "action": beat["current_action"],
            "camera": beat["camera_witness"],
        })

    blueprint = {
        "schema_version": 1,
        "mode": "omni_reference_blueprint",
        "backend": None,
        "adapter_status": "not_in_scope",
        "chapter_id": chapter["id"],
        "duration_sec": chapter["window"]["end"] - chapter["window"]["start"],
        "time_basis": "chapter_relative",
        "subject_definitions": subject_definitions,
        "reference_bindings": bindings,
        "retention_analysis": [
            {
                "handle": item["handle"],
                "retain": item["intents"],
                "discard": item.get("do_not_inherit", []),
            }
            for item in bindings
        ],
        "timeline": timeline,
        "audio_policy": {
            "reference_audio_is_condition_only": True,
            "generate_audio": False,
            "delivery_audio": "timeline.master_audio",
        },
    }
    require_omni_reference_blueprint(blueprint)
    return blueprint


def validate_omni_reference_blueprint(blueprint: dict[str, Any]) -> list[str]:
    """校验核心蓝图；不要求任何后端能力或托管 URL。"""

    errors: list[str] = []
    if blueprint.get("mode") != "omni_reference_blueprint":
        errors.append("mode 必须是 omni_reference_blueprint")
    if blueprint.get("backend") is not None:
        errors.append("核心全能参考蓝图不得绑定视频后端")

    bindings = blueprint.get("reference_bindings") or []
    handles: dict[str, dict[str, Any]] = {}
    for binding in bindings:
        if not isinstance(binding, dict):
            errors.append("reference_bindings 必须是对象列表")
            continue
        handle = str(binding.get("handle", "")).replace(" ", "")
        modality = binding.get("modality")
        intents = set(binding.get("intents") or [])
        if not handle.startswith("@"):
            errors.append("参考素材缺少 @ handle")
        elif handle in handles:
            errors.append(f"参考 handle 重复: {handle}")
        else:
            handles[handle] = binding
        if modality not in ALLOWED_MODALITIES:
            errors.append(f"{handle or '未命名素材'} modality 无效")
        if not _source_present(binding) and not (binding.get("binding_status") == "planned"
                                               and modality == "audio"):
            errors.append(f"{handle or '未命名素材'} 缺少本地来源或资产定位")
        if not intents:
            errors.append(f"{handle or '未命名素材'} 未声明参考职责")
        unknown = sorted(intents - ALLOWED_INTENTS)
        if unknown:
            errors.append(f"{handle or '未命名素材'} 包含未知职责: {','.join(unknown)}")

    subjects = blueprint.get("subject_definitions") or []
    for subject in subjects:
        handle = str(subject.get("handle", "")).replace(" ", "")
        binding = handles.get(handle)
        if not binding:
            errors.append(f"人物定义 {handle or '未命名'} 没有真实参考绑定")
            continue
        if set(binding.get("intents") or []) != set(CHARACTER_INTENTS):
            errors.append(f"{handle} 人物图只能锁定 character_identity + wardrobe")
        missing_exclusions = set(CHARACTER_EXCLUSIONS) - set(binding.get("do_not_inherit") or [])
        if missing_exclusions:
            errors.append(f"{handle} 未隔离人物图的背景/构图/灯光/姿势")

    timeline = blueprint.get("timeline") or []
    errors.extend(timeline_errors(timeline, blueprint.get("duration_sec"), "shot_id"))
    previous_end: float | None = None
    for index, shot in enumerate(timeline):
        shot_id = shot.get("shot_id", "unknown")
        try:
            start = float(shot["start_sec"])
            end = float(shot["end_sec"])
        except (KeyError, TypeError, ValueError):
            errors.append(f"{shot_id} 时间窗无效")
            continue
        if end <= start:
            errors.append(f"{shot_id} 时间窗必须正向")
        if previous_end is not None and abs(start - previous_end) > 1e-6:
            errors.append(f"{shot_id} 与上一镜时间窗不连续")
        previous_end = end
        used = {str(item).replace(" ", "") for item in shot.get("reference_handles", [])}
        for handle in sorted(used - set(handles)):
            errors.append(f"{shot_id} 使用了未绑定参考 {handle}")
        if index < len(timeline) - 1 and not str(shot.get("cut_relation", "")).strip():
            errors.append(f"{shot_id} 缺少 cut_relation")

    audio = blueprint.get("audio_policy") or {}
    if audio.get("reference_audio_is_condition_only") is not True:
        errors.append("章节音频必须标记为 condition_only")
    if audio.get("generate_audio") is not False:
        errors.append("核心蓝图必须关闭模型音频生成")
    if audio.get("delivery_audio") != "timeline.master_audio":
        errors.append("交付音频必须回贴 timeline.master_audio")
    return errors


def require_omni_reference_blueprint(blueprint: dict[str, Any]) -> None:
    errors = validate_omni_reference_blueprint(blueprint)
    if errors:
        raise ValueError("全能参考蓝图未通过：" + "；".join(errors))
