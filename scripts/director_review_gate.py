#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""导演样片审片门禁：技术通过不等于艺术通过。"""
import argparse
import json
import math
import sys
import subprocess
import hashlib
import uuid
from pathlib import Path
try:
    from .pipeline_contracts import digest, atomic_batch, require_no_transaction
except ImportError:
    from pipeline_contracts import digest, atomic_batch, require_no_transaction

try:
    from .visual_development import fingerprint
except ImportError:
    from visual_development import fingerprint

SEQUENCE_DIMENSIONS = ("music_rhythm", "emotional_progression", "cut_relationships", "visual_grammar")
REVIEW_SCHEMA_VERSION = 3


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


DIMENSIONS = (
    "story_readability", "performance", "blocking_and_gaze", "camera_witness",
    "motion_physics", "source_light_and_material", "continuity", "editing",
    "lip_sync",
    # 剧组拉片：画面是否匹配歌曲情绪弧/母题（与设计数据、审批状态并列）
    "song_emotion_match",
)

# crew_emotion_pull.segments[].verdict
CREW_EMOTION_KEEP = "KEEP"
CREW_EMOTION_PARTIAL = "PARTIAL"
CREW_EMOTION_REGEN = "REGEN"
CREW_EMOTION_FAIL = "FAIL"
CREW_EMOTION_BLOCKING = frozenset({CREW_EMOTION_REGEN, CREW_EMOTION_FAIL})
CREW_EMOTION_PARTIAL_BLOCK_PRIORITIES = frozenset({"P0", "P1"})


def initial_review(plan: dict) -> dict:
    prototype = plan.get("production_policy", {}).get("prototype_chapter")
    if not prototype:
        raise ValueError("chapter_plan 缺少 production_policy.prototype_chapter")
    return {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "prototype_chapter": prototype,
        "reviewed_media": "",
        "reviewed_media_sha256": "",
        "dependencies": {},
        "watched_full_duration": False,
        "technical_checks": {
            "container_and_duration": "pending",
            "audio_alignment": "pending",
            "note": "技术检查与导演检查必须分开记录",
        },
        "director_checks": {
            name: {"status": "pending", "evidence": ""} for name in DIMENSIONS
        },
        "timecoded_findings": [],
        "sequence_checks": {name: {"status": "pending", "evidence": ""}
                            for name in SEQUENCE_DIMENSIONS},
        "decision": "pending",
        "batch_generation_allowed": False,
    }



def validate_crew_emotion_pull(review: dict, duration: float | None = None) -> list[str]:
    """把剧组拉片情绪结论接入审片：情绪不合段（REGEN/FAIL，或 P0/P1 PARTIAL）阻断批准。

    期望结构 review["crew_emotion_pull"]:
      {
        "source": "可选，如 CREW_PULL_REPORT.md",
        "segments": [
          {"start_sec": 60.0, "end_sec": 90.0, "verdict": "REGEN",
           "priority": "P0", "reason": "缺悬崖假自由→回笼"}
        ]
      }
    """
    errors: list[str] = []
    pull = review.get("crew_emotion_pull")
    if not isinstance(pull, dict):
        errors.append("缺少 crew_emotion_pull：剧组拉片情绪结论须作为审片输入，与设计/审批维度并列")
        return errors
    segments = pull.get("segments")
    if not isinstance(segments, list) or not segments:
        errors.append("crew_emotion_pull.segments 不能为空")
        return errors
    blocking_ranges: list[str] = []
    for index, segment in enumerate(segments, 1):
        if not isinstance(segment, dict):
            errors.append(f"crew_emotion_pull.segments[{index}] 必须是对象")
            continue
        try:
            start = float(segment.get("start_sec"))
            end = float(segment.get("end_sec", start))
        except (TypeError, ValueError):
            errors.append(f"crew_emotion_pull.segments[{index}] 时间码无效")
            continue
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end < start:
            errors.append(f"crew_emotion_pull.segments[{index}] 时间范围无效")
            continue
        if duration is not None and end > duration + 1e-6:
            errors.append(
                f"crew_emotion_pull.segments[{index}] 时间码超出样片时长 {duration}s"
            )
        verdict = segment.get("verdict")
        if verdict not in {
            CREW_EMOTION_KEEP, CREW_EMOTION_PARTIAL, CREW_EMOTION_REGEN, CREW_EMOTION_FAIL
        }:
            errors.append(
                f"crew_emotion_pull.segments[{index}] verdict 无效（须 KEEP/PARTIAL/REGEN/FAIL）"
            )
            continue
        reason = str(segment.get("reason", "")).strip()
        if not reason:
            errors.append(f"crew_emotion_pull.segments[{index}] 缺少 reason")
        priority = str(segment.get("priority", "")).strip().upper()
        regen_required = segment.get("regen_required")
        if isinstance(regen_required, str):
            regen_required = regen_required.strip().lower() in {"1", "true", "yes", "y"}
        partial_blocks = (
            verdict == CREW_EMOTION_PARTIAL
            and (priority in CREW_EMOTION_PARTIAL_BLOCK_PRIORITIES or regen_required is True)
        )
        if verdict in CREW_EMOTION_BLOCKING or partial_blocks:
            label = f"{start:g}–{end:g}s"
            detail = reason or "未写原因"
            pri = f" · {priority}" if priority else ""
            blocking_ranges.append(f"{label} ({verdict}{pri}: {detail})")
            errors.append(
                f"歌曲情绪不合（须重生，审片不通过）: {label} · {verdict}{pri} · {detail}"
            )
    check = review.get("director_checks", {}).get("song_emotion_match", {})
    if blocking_ranges and check.get("status") == "pass":
        errors.append(
            "导演维度 song_emotion_match 不能为 pass：存在情绪不合段 "
            + "; ".join(blocking_ranges)
        )
    return errors


def requires_crew_emotion_pull(review: dict) -> bool:
    """新格式强制情绪拉片；仅兼容尚未包含两个新字段的 v2 审片记录。"""
    checks = review.get("director_checks")
    has_dimension = isinstance(checks, dict) and "song_emotion_match" in checks
    if has_dimension or "crew_emotion_pull" in review:
        return True
    return review.get("schema_version") != 2


def validate_review(review: dict, plan: dict, *, production=False) -> list[str]:
    errors = []
    expected = plan.get("production_policy", {}).get("prototype_chapter")
    if review.get("prototype_chapter") != expected:
        errors.append("样片章节与 chapter_plan 不一致")
    if not review.get("reviewed_media"):
        errors.append("缺少 reviewed_media")
    elif Path(str(review["reviewed_media"])).suffix.lower() not in {".mp4", ".mov", ".mkv", ".webm"}:
        errors.append("reviewed_media 必须是动态视频，联系表或图片不可作为审片对象")
    if review.get("watched_full_duration") is not True:
        errors.append("必须完整观看动态样片，联系表或首帧不能替代")
    technical = review.get("technical_checks", {})
    for name in ("container_and_duration", "audio_alignment"):
        if technical.get(name) != "pass":
            errors.append(f"技术检查未通过: {name}")
    checks = review.get("director_checks", {})
    prototype = next((chapter for chapter in plan.get("chapters", [])
                      if chapter.get("id") == expected), None)
    duration = None
    if prototype is not None:
        raw_duration = prototype.get("duration_sec")
        try:
            window = prototype.get("window") or {}
            duration = float(raw_duration) if raw_duration is not None else (
                float(window["end"]) - float(window["start"]))
            if not math.isfinite(duration) or duration <= 0:
                raise ValueError
        except (KeyError, TypeError, ValueError):
            errors.append("样片章节缺少正向有限时长，不能验证审片时间码")
            duration = None
    else:
        errors.append("样片章节不存在")
    no_lipsync = (prototype is not None and bool(prototype.get("shot_deltas"))
                  and not any(item.get("lip_sync") for item in prototype["shot_deltas"]))
    emotion_gate_required = production or requires_crew_emotion_pull(review)
    required_dimensions = (
        DIMENSIONS if emotion_gate_required
        else tuple(name for name in DIMENSIONS if name != "song_emotion_match")
    )
    for name in required_dimensions:
        item = checks.get(name, {})
        not_applicable = name == "lip_sync" and no_lipsync and item.get("status") == "not_applicable"
        if not (item.get("status") == "pass" or not_applicable):
            errors.append(f"导演维度未通过: {name}")
        if not str(item.get("evidence", "")).strip():
            errors.append(f"导演维度缺少可观察证据: {name}")
    for name in SEQUENCE_DIMENSIONS:
        item = review.get("sequence_checks", {}).get(name, {})
        if item.get("status") != "pass" or not str(item.get("evidence", "")).strip():
            errors.append(f"段落审片未通过或缺少证据: {name}")
    if emotion_gate_required:
        errors.extend(validate_crew_emotion_pull(review, duration))
    findings = review.get("timecoded_findings", [])
    if not findings:
        errors.append("至少需要一条 timecoded_findings，证明审片基于动态时间线")
    for index, finding in enumerate(findings, 1):
        try:
            start = float(finding.get("start_sec"))
            end = float(finding.get("end_sec", start))
        except (TypeError, ValueError):
            errors.append(f"timecoded_findings[{index}] 时间码无效")
            continue
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end < start:
            errors.append(f"timecoded_findings[{index}] 时间范围无效")
        elif duration is not None and end > duration + 1e-6:
            errors.append(f"timecoded_findings[{index}] 时间码超出样片时长 {duration}s")
        if finding.get("dimension") not in DIMENSIONS:
            errors.append(f"timecoded_findings[{index}] dimension 无效")
        if not str(finding.get("observation", "")).strip():
            errors.append(f"timecoded_findings[{index}] 缺少 observation")
    if review.get("decision") != "approve":
        errors.append("decision 必须为 approve")
    if review.get("batch_generation_allowed") is not True:
        errors.append("batch_generation_allowed 必须为 true")
    return errors


def dependency_snapshot(project: Path, scope=None) -> dict:
    try:
        from .review_dependencies import snapshot
    except ImportError:
        from review_dependencies import snapshot
    return snapshot(project, scope)


def validate_chapter_id(chapter_id):
    if chapter_id is not None and (not isinstance(chapter_id, str) or not chapter_id
            or any(char in chapter_id for char in '/\\:') or chapter_id in {'.', '..'}):
        raise ValueError('章节ID不能包含路径')


def review_plan(project, chapter_id=None):
    validate_chapter_id(chapter_id)
    plan = json.loads((project / "state/chapter_plan.json").read_text(encoding="utf-8"))
    if chapter_id:
        plan["production_policy"]["prototype_chapter"] = chapter_id
    if chapter_id == "full_sequence":
        timeline = json.loads((project / "state/timeline.json").read_text(encoding="utf-8"))
        sb = json.loads((project / "state/storyboard.json").read_text(encoding="utf-8"))
        plan["chapters"].append({"id": chapter_id, "duration_sec": timeline["duration_sec"],
                                 "shot_deltas": sb["shots"]})
    return plan


def _approved(project: Path, chapter_id=None) -> tuple[bool, list[str]]:
    plan = review_plan(project, chapter_id)
    path = (project / "state/reviews" / f"{chapter_id}.json" if chapter_id
            else project / "state/director_review.json")
    if not path.is_file():
        return False, ["缺少 state/director_review.json"]
    review = json.loads(path.read_text(encoding="utf-8"))
    errors = validate_review(review, plan, production=True)
    try:
        from .pipeline_contracts import require_no_transaction, timing_authority
    except ImportError:
        from pipeline_contracts import require_no_transaction, timing_authority
    try:
        require_no_transaction(project)
        validate_chapter_id(chapter_id)
        timeline = json.loads((project / 'state/timeline.json').read_text(encoding='utf-8'))
        if timing_authority(timeline) != 'production':
            errors.append('生产批准须有权威时间轴')
    except (OSError, ValueError) as exc:
        errors.append(str(exc))
    if review.get("simulation_only") is True:
        errors.append("模拟审片不能作为正式生成或交付批准")
    media = project / review.get("reviewed_media", "")
    if not media.is_file() or fingerprint(media) != review.get("reviewed_media_sha256"):
        errors.append("审片对象不存在或样片指纹已改变")
    else:
        try:
            from .media_verify import probe
        except ImportError:
            from media_verify import probe
        try:
            measured = probe(media)
            video = next(s for s in measured['streams'] if s['codec_type'] == 'video')
            actual = float(video.get('duration', measured["format"]["duration"]))
            chapter = next(c for c in plan['chapters'] if c['id'] == plan['production_policy']['prototype_chapter'])
            if abs(actual-float(chapter['duration_sec'])) > .05:
                errors.append('审片媒体实际时长与批准章节不一致')
            for finding in review.get("timecoded_findings", []):
                if float(finding.get("end_sec", finding.get("start_sec", 0))) > actual + 1e-6:
                    errors.append("审片时间码超出实际媒体时长")
        except (ValueError, KeyError, OSError, StopIteration, subprocess.CalledProcessError) as exc:
            errors.append(f"审片媒体无法实测: {exc}")
    dependencies = review.get("dependencies", {})
    if not dependencies:
        errors.append("审片记录缺少制作依赖指纹，请重新初始化审片记录")
    current = {}
    try:
        current = dependency_snapshot(project, chapter_id)
        if set(current) != set(dependencies):
            errors.append("审片依赖集合缺失或改变，必须重新审片")
    except (OSError, ValueError, KeyError, StopIteration, TypeError) as exc:
        errors.append(f"审片必需依赖缺失: {exc}")
    for name, expected in dependencies.items():
        if current.get(name) != expected:
            errors.append(f"审片依据过期: {name}")
    return not errors, errors


def approved(project: Path, chapter_id=None) -> tuple[bool, list[str]]:
    """核验前后使用相同审批、媒体及作用域依赖；变化时失败关闭。"""
    project = Path(project).resolve()
    errors = []
    try:
        require_no_transaction(project)
        validate_chapter_id(chapter_id)
        path = (project / 'state/reviews' / f'{chapter_id}.json' if chapter_id
                else project / 'state/director_review.json')
        payload = path.read_bytes()
        version = 'sha256:' + hashlib.sha256(payload).hexdigest()
        record = json.loads(payload)
        before = dependency_snapshot(project, chapter_id)
        _, errors = _approved(project, chapter_id)
        if dependency_snapshot(project, chapter_id) != before:
            errors.append('审片依赖在检查期间改变，请重新核验')
        if digest(path) != version:
            errors.append('审片审批版本在检查期间改变，请重新核验')
        media = project / record.get('reviewed_media', '')
        if not media.is_file() or digest(media) != record.get('reviewed_media_sha256'):
            errors.append('审片媒体在检查期间改变，请重新核验')
        require_no_transaction(project)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, StopIteration) as exc:
        errors.append(f'审片无法一致核验: {exc}')
    return not errors, errors


def delivery_approved(project):
    project = Path(project).resolve()
    errors = []
    try:
        require_no_transaction(project)
        plan_path = project / 'state/chapter_plan.json'
        plan_bytes = plan_path.read_bytes()
        plan_version = 'sha256:' + hashlib.sha256(plan_bytes).hexdigest()
        plan = json.loads(plan_bytes)
        ids = [chapter['id'] for chapter in plan['chapters']]
        for cid in ids: validate_chapter_id(cid)
        if not ids or len(ids) != len(set(ids)) or 'full_sequence' in ids:
            raise ValueError('章节列表为空、重复或含保留ID')
        ids.append('full_sequence')
        versions, media_versions = {}, {}
        for cid in ids:
            path = project / 'state/reviews' / f'{cid}.json'
            payload = path.read_bytes()
            versions[cid] = 'sha256:' + hashlib.sha256(payload).hexdigest()
            record = json.loads(payload)
            if not isinstance(record.get('reviewed_media'), str) or not record['reviewed_media'].strip():
                raise ValueError(f'{cid}: 缺少实际审片媒体')
            media = (project / record['reviewed_media']).resolve()
            actual = digest(media)
            if actual != record.get('reviewed_media_sha256'):
                raise ValueError(f'{cid}: 实际审片媒体版本不匹配')
            media_versions[media] = actual
        before = dependency_snapshot(project, 'full_sequence')
        for cid in ids:
            ok, failures = approved(project, cid)
            if not ok:
                errors.extend(f'{cid}: {error}' for error in failures)
        if digest(plan_path) != plan_version or dependency_snapshot(project, 'full_sequence') != before:
            errors.append('全片批准汇总期间章节计划或制作依赖改变，请重新核验')
        for cid, version in versions.items():
            if digest(project / 'state/reviews' / f'{cid}.json') != version:
                errors.append(f'{cid}: 全片批准汇总期间审批版本改变，请重新核验')
        for media, version in media_versions.items():
            if digest(media) != version:
                errors.append('全片批准汇总期间实际审片媒体改变，请重新核验')
        require_no_transaction(project)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, StopIteration) as exc:
        errors.append(f'全片批准无法一致核验: {exc}')
    return not errors, errors


def update_review(project, action, chapter_id=None):
    """仅按读取版本修改审批，旧任务不能覆盖新批准或新撤销。"""
    project = Path(project).resolve()
    require_no_transaction(project)
    validate_chapter_id(chapter_id)
    path = (project / 'state/reviews' / f'{chapter_id}.json' if chapter_id
            else project / 'state/director_review.json')
    previous = path.read_bytes() if path.exists() else None
    version = 'sha256:' + hashlib.sha256(previous).hexdigest() if previous is not None else None
    old = json.loads(previous) if previous is not None else None
    if action == 'invalidate':
        if old is None: raise FileNotFoundError('缺少导演审片记录')
        record = {**old, 'previous_decision': old.get('decision'), 'decision': 'pending',
                  'batch_generation_allowed': False, 'watched_full_duration': False}
    elif action in {'init', 'refresh'}:
        if action == 'init' and old is not None:
            raise FileExistsError(f'审片记录已存在，拒绝覆盖: {path}')
        if action == 'refresh' and (old is None or old.get('decision') != 'pending'):
            raise ValueError('refresh仅用于已撤销的待审记录；先显式invalidate')
        plan = review_plan(project, chapter_id)
        if chapter_id and chapter_id not in {c['id'] for c in plan['chapters']}:
            raise ValueError('章节不存在')
        record = initial_review(plan)
        record['dependencies'] = dependency_snapshot(project, chapter_id)
    else:
        raise ValueError('不支持的审片操作')
    updates = {path: json.dumps(record, ensure_ascii=False, indent=2) + '\n'}
    if previous is not None:
        updates[path.parent / 'history' / f'{path.stem}-{uuid.uuid4().hex}.json'] = previous
    atomic_batch(updates, expected={path: version})
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("init", "check", "invalidate", "refresh"))
    parser.add_argument("project_dir")
    parser.add_argument("--chapter", help="逐章ID或full_sequence；省略为代表样片")
    args = parser.parse_args()
    project = Path(args.project_dir).resolve()
    if args.action != 'check':
        update_review(project, args.action, args.chapter)
        print('pending: 须重新观看审片，不能沿用旧批准')
        return
    ok, errors = approved(project, args.chapter)
    print(json.dumps({"status": "approved" if ok else "blocked", "errors": errors},
                     ensure_ascii=False))
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
