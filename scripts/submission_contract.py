#!/usr/bin/env python3
"""校验外部视频后端提交包，阻止角色引用静默降级。

核心流水线保持后端中立。本模块只校验外部适配器已经解析出的事实，
不保存凭证、不调用供应商 API，也不猜测任何供应商专属字段。
"""

from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from typing import Any
import hashlib
try:
    from .pipeline_contracts import REFERENCE_INTENTS, timeline_errors, digest, number
    from .director_review_gate import approved
except ImportError:
    from pipeline_contracts import REFERENCE_INTENTS, timeline_errors, digest, number
    from director_review_gate import approved


MENTION_RE = re.compile(
    r"(?<![\w@])(@(?:图片|视频|音频)\s*\d+|@[\w\-\u3400-\u9fff]+)"
)
SUBJECT_BINDING_KINDS = {
    "library_subject", "structured_mention", "omni_image_reference",
}
SUBJECT_ID_FIELDS = ("subject_id", "asset_id", "mention_id", "url")
OMNI_REFERENCE_MODALITIES = {"image", "video", "audio"}
OMNI_REFERENCE_INTENTS = REFERENCE_INTENTS
REQUIRED_REFERENCE_MODE = "omni_reference"


def _structured_mention_names(contract: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for item in contract.get("structured_mentions", []):
        if isinstance(item, str):
            names.add(item.lstrip("@").strip())
        elif isinstance(item, dict):
            name = str(item.get("name", "")).lstrip("@").strip()
            if name:
                names.add(name)
    return names


def _prompt_handles(prompt: str) -> set[str]:
    return {match.group(1).replace(" ", "") for match in MENTION_RE.finditer(prompt)}


def _binding_handle(binding: dict[str, Any]) -> str:
    return str(binding.get("handle", "")).strip().replace(" ", "")


def _has_asset_locator(binding: dict[str, Any]) -> bool:
    source = binding.get("source") or {}
    return any(str(source.get(key, "")).strip() for key in ("asset_id", "url"))


def validate_submission_contract(contract: dict[str, Any], project: Path | None = None,
                                 *, structural_only=False) -> list[str]:
    """返回提交包错误；空列表表示可以交给外部适配器提交。"""

    errors: list[str] = []
    prompt = str(contract.get("prompt", ""))
    reference_mode = contract.get("reference_mode")
    if reference_mode != REQUIRED_REFERENCE_MODE:
        errors.append(
            "正式视频提交必须使用 reference_mode=omni_reference；"
            "更换供应商不能降级为文生视频、单首帧图生视频或仅角色库模式"
        )
    literal_mentions = _prompt_handles(prompt)
    bound_mentions = {f"@{name}" for name in _structured_mention_names(contract)}
    bound_mentions.update(
        _binding_handle(item) for item in contract.get("reference_bindings", [])
        if isinstance(item, dict) and _binding_handle(item)
    )

    for name in sorted(literal_mentions - bound_mentions):
        errors.append(
            f"{name} 只是提示词文本，未绑定真实参考素材；禁止让模型自行猜测"
        )

    subject = contract.get("subject_reference") or {}
    if subject.get("required"):
        binding = subject.get("binding") or {}
        kind = binding.get("kind")
        status = binding.get("review_status")
        identity = next((binding.get(key) for key in SUBJECT_ID_FIELDS if binding.get(key)), None)

        if kind not in SUBJECT_BINDING_KINDS:
            errors.append(
                "人物一致性镜头缺少可追溯绑定；须使用角色库标识或全能参考图片资产"
            )
        if kind in {"library_subject", "structured_mention"} and status != "approved":
            errors.append("角色资产尚未明确标记为 approved")
        if not identity:
            errors.append("角色绑定缺少 subject_id、asset_id、mention_id 或托管 URL")

        expected_handle = str(subject.get("handle") or subject.get("name", "")).strip()
        if expected_handle and not expected_handle.startswith("@"):
            expected_handle = "@" + expected_handle
        if expected_handle and expected_handle.replace(" ", "") not in bound_mentions:
            errors.append(f"角色 {expected_handle} 未绑定到提示词引用")

    audio = contract.get("audio_reference") or {}
    request = contract.get("request") or {}
    if audio.get("condition_only") and request.get("generate_audio") is not False:
        errors.append(
            "参考音频声明为仅驱动表演/口型时，generate_audio 必须显式为 false；"
            "最终成片应重贴原始母带"
        )

    bindings = contract.get("reference_bindings") or []
    capabilities = contract.get("backend_capabilities") or {}
    if reference_mode == REQUIRED_REFERENCE_MODE:
        if capabilities.get("verified") is not True:
            errors.append("全能参考模式缺少已核验的后端能力回执")
        if capabilities.get("omni_reference") is not True:
            errors.append("所选后端未声明支持多模态全能参考")
        if not bindings:
            errors.append("全能参考模式至少需要一个真实参考素材")

        seen_handles: set[str] = set()
        counts = {key: 0 for key in OMNI_REFERENCE_MODALITIES}
        for binding in bindings:
            if not isinstance(binding, dict):
                errors.append("reference_bindings 必须是对象列表")
                continue
            handle = _binding_handle(binding)
            modality = str(binding.get("modality", "")).strip()
            intents = set(binding.get("intents") or [])
            if not handle or not handle.startswith("@"):
                errors.append("全能参考素材缺少可在提示词中点名的 handle")
            elif handle in seen_handles:
                errors.append(f"全能参考 handle 重复: {handle}")
            else:
                seen_handles.add(handle)
            if modality not in OMNI_REFERENCE_MODALITIES:
                errors.append(f"{handle or '未命名素材'} 的 modality 无效: {modality}")
            else:
                counts[modality] += 1
            if not _has_asset_locator(binding):
                errors.append(f"{handle or '未命名素材'} 缺少 asset_id 或 Lovart 托管 URL")
            if not intents:
                errors.append(f"{handle or '未命名素材'} 未声明参考意图")
            unknown = sorted(intents - OMNI_REFERENCE_INTENTS)
            if unknown:
                errors.append(f"{handle or '未命名素材'} 包含未知参考意图: {','.join(unknown)}")
            if handle and handle.replace(" ", "") not in literal_mentions:
                errors.append(f"{handle} 已上传但未在提示词中点名其参考职责")

        limits = capabilities.get("reference_limits") or {}
        for modality, count in counts.items():
            limit = limits.get(modality)
            if isinstance(limit, int) and count > limit:
                errors.append(f"{modality} 参考数量 {count} 超过后端上限 {limit}")

    edit_shots = contract.get("edit_shots") or []
    if not isinstance(edit_shots, list):
        errors.append("edit_shots 必须是列表")
        edit_shots = []
    previous_end = None
    seen_shot_ids: set[str] = set()
    for index, shot in enumerate(edit_shots):
        if not isinstance(shot, dict):
            errors.append(f"edit_shots[{index}] 必须是对象")
            continue
        sid = shot.get("id")
        if not isinstance(sid, str) or not sid.strip() or sid in seen_shot_ids:
            errors.append(f"edit_shots[{index}] 镜头 ID 为空或重复")
        else:
            seen_shot_ids.add(sid)
        window = shot.get("window") or {}
        if not isinstance(window, dict):
            errors.append(f"{sid or index} window 必须是对象")
            continue
        try:
            start_raw = shot.get("start_sec", window.get("start"))
            end_raw = shot.get("end_sec", window.get("end"))
            if isinstance(start_raw, bool) or isinstance(end_raw, bool):
                raise ValueError
            start, end = float(start_raw), float(end_raw)
            if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start:
                raise ValueError
        except (TypeError, ValueError):
            errors.append(f"{sid or index} 镜头时间窗缺失、非有限数或倒序")
            continue
        if previous_end is not None and abs(start - previous_end) > 1e-6:
            errors.append(f"{sid} 镜头时间窗重叠、倒序或不连续")
        previous_end = end
    if len(edit_shots) > 1:
        if capabilities.get("multi_shot") is not True:
            errors.append("多镜生成包所选后端未核验 multi_shot 能力")
        if capabilities.get("timestamp_control") is not True:
            errors.append("多镜生成包所选后端未核验 timestamp_control 能力")
        for shot in edit_shots[:-1]:
            if not isinstance(shot, dict):
                continue
            if not str(shot.get("cut_relation", "")).strip():
                errors.append(f"{shot.get('id', 'unknown')} 缺少 cut_relation")

    if contract.get("director_cut_status") != "approved":
        errors.append("提交前必须通过导演剪辑门禁 director_cut_status=approved")

    if not structural_only:
        if project is not None:
            try:
                from .submission_jobs import request_errors, upload_errors
            except ImportError:
                from submission_jobs import request_errors, upload_errors
            errors.extend(request_errors(contract))
            upload_failures, _ = upload_errors(Path(project).resolve(), contract)
            errors.extend(upload_failures)
        errors.extend(timeline_errors(edit_shots, contract.get("duration_sec")))
        limits = capabilities.get('reference_limits') or {}
        for modality in OMNI_REFERENCE_MODALITIES:
            count = sum(isinstance(b, dict) and b.get('modality') == modality for b in bindings)
            limit = limits.get(modality)
            if count and (type(limit) is not int or limit < count):
                errors.append(f'{modality}参考输入上限未严格核验或已超限')
        if capabilities.get("timestamp_control") is not True:
            errors.append("正式提交必须核验timestamp_control")
        for binding in bindings:
            if isinstance(binding, dict) and (binding.get("binding_status") != "uploaded"
                    or not binding.get("source", {}).get("sha256")):
                errors.append("提交素材必须uploaded并保留来源SHA-256")
        try:
            cost = number(contract.get("estimated_cash_cny"))
            budget = number(contract.get("cash_limit_cny"))
            if cost < 0 or budget < cost:
                raise ValueError
            limit = number(capabilities.get("max_duration_sec"))
            if number(contract.get("duration_sec")) > limit:
                raise ValueError
            if contract.get("resolution") not in capabilities.get("resolutions", []):
                raise ValueError
        except (TypeError, ValueError):
            errors.append("预算/估价/模型时长/分辨率未核验或超出上限")
        if project is None:
            errors.append("正式提交须提供project_dir以核验项目、章节和审片事实")
        else:
            try:
                project = Path(project).resolve()
                try:
                    from .production_preflight import generation_errors
                except ImportError:
                    from production_preflight import generation_errors
                errors.extend(generation_errors(project))
                try:
                    from .animatic_review import require as require_animatic
                except ImportError:
                    from animatic_review import require as require_animatic
                require_animatic(project)
                try:
                    from .visual_development import require as require_development
                    from .chapter_plan_build import require_director_final_cut, require_production_timing
                except ImportError:
                    from visual_development import require as require_development
                    from chapter_plan_build import require_director_final_cut, require_production_timing
                storyboard = json.loads((project / "state/storyboard.json").read_text(encoding="utf-8"))
                style = json.loads((project / "state/style.json").read_text(encoding="utf-8"))
                timeline = json.loads((project / "state/timeline.json").read_text(encoding="utf-8"))
                edit = json.loads((project / "state/visual_edit_plan.json").read_text(encoding="utf-8"))
                require_development(project, storyboard, style)
                require_production_timing(timeline, storyboard["shots"])
                require_director_final_cut(edit, storyboard["shots"])
                cid = contract["chapter_id"]
                plan = json.loads((project / "state/chapter_plan.json").read_text(encoding="utf-8"))
                chapter = next(c for c in plan["chapters"] if c["id"] == cid)
                if contract.get("project_id") != str(project):
                    errors.append("提交项目ID不一致")
                package_path = project / "prompts/chapters" / f"{cid}.json"
                if contract.get("source_package_sha256") != digest(package_path):
                    errors.append("提交章节制作包指纹已过期")
                package = json.loads(package_path.read_text(encoding="utf-8"))
                if prompt != package.get("prompt"):
                    errors.append("提交提示词与批准制作包不一致，须重新编译和审查")
                if plan.get("source_storyboard_fingerprint") != digest(project / "state/storyboard.json"):
                    errors.append("章节计划来自旧分镜")
                beats = package["beats"]
                expected = [(b["shot_id"], b["start_sec"], b["end_sec"]) for b in beats]
                actual = [(s["id"], s.get("start_sec", s.get("window", {}).get("start")),
                           s.get("end_sec", s.get("window", {}).get("end"))) for s in edit_shots]
                if actual != expected or number(contract["duration_sec"]) != number(chapter["duration_sec"]):
                    errors.append("提交镜头时间与批准章节不一致")
                sources = {b["handle"].replace(" ", ""): b for b in package["reference_bindings"]}
                for binding in bindings:
                    source = sources.get(_binding_handle(binding))
                    if not source or binding["source"].get("sha256") != source["source"].get("sha256"):
                        errors.append("上传参考与制作包来源不一致")
                    elif source["source"].get("local_path"):
                        if digest(project / source["source"]["local_path"]).removeprefix("sha256:") != str(
                                binding["source"]["sha256"]).removeprefix("sha256:"):
                            errors.append("参考本地文件已变化")
                    if source and (binding.get("modality") != source.get("modality") or
                                   binding.get("intents") != source.get("intents")):
                        errors.append("提交参考职责与批准制作包不一致")
                if set(sources) != {_binding_handle(b) for b in bindings}:
                    errors.append("提交参考集合不完整")
                expected_prompt_sha256 = "sha256:" + hashlib.sha256(prompt.encode("utf-8")).hexdigest()
                if contract.get("prompt_sha256") != expected_prompt_sha256:
                    errors.append("提交提示词指纹不匹配")
                if cid != plan["production_policy"]["prototype_chapter"]:
                    ok, failures = approved(project)
                    if not ok:
                        errors.extend("批量审片未通过: " + f for f in failures)
                elif contract.get("submission_phase") != "prototype":
                    errors.append("代表章提交阶段必须为prototype")
            except (OSError, ValueError, KeyError, StopIteration, TypeError) as exc:
                errors.append(f"提交项目绑定校验失败: {exc}")

    if project is not None and not structural_only:
        try:
            try:
                from .repair_state import submission_errors
            except ImportError:
                from repair_state import submission_errors
            errors.extend(submission_errors(project, contract))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            errors.append(f'返修提交绑定无法核验: {exc}')
    return errors


def require_submission_contract(contract: dict[str, Any], project: Path | None = None) -> None:
    errors = validate_submission_contract(contract, project)
    if errors:
        raise ValueError("后端提交包未通过：" + "；".join(errors))


def main() -> int:
    parser = argparse.ArgumentParser(description="校验外部视频后端提交契约")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--project-dir", type=Path, required=True)
    args = parser.parse_args()
    contract = json.loads(args.manifest.read_text(encoding="utf-8"))
    errors = validate_submission_contract(contract, args.project_dir)
    print(json.dumps({"pass": not errors, "errors": errors, "scope": "package_validation_only",
                      "dispatch_allowed": False, "next_required": "submission_jobs.prepare -> dispatch"}, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
