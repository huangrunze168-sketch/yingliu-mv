#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把合法取得的电影镜头标注聚合为匿名视听语法画像。

输入是一行一镜的 JSONL；本工具不下载、保存或重新分发电影画面。画像只保留
聚合统计与来源清单，供分镜审计使用，不能替代 30/180 度、轴线和动线等硬规则。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable
try:
    from .pipeline_contracts import SHOT_SIZES
except ImportError:
    from pipeline_contracts import SHOT_SIZES


REQUIRED_RECORD_FIELDS = (
    "source_id", "film_id", "scene_id", "shot_index", "duration_sec", "shot_size",
    "camera_movement",
)
REQUIRED_SOURCE_FIELDS = ("id", "title", "url", "license_or_terms", "access_scope")
ALLOWED_MODES = {"advisory", "enforce_envelope"}


def fingerprint(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def quantile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("不能对空样本计算分位数")
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] * (upper - position) + ordered[upper] * (position - lower)


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise ValueError(f"第 {line_number} 行不是 JSON 对象")
        missing = [field for field in REQUIRED_RECORD_FIELDS if value.get(field) in (None, "")]
        if missing:
            raise ValueError(f"第 {line_number} 行缺少字段: {','.join(missing)}")
        try:
            duration_raw = value["duration_sec"]
            index_raw = value["shot_index"]
            if isinstance(duration_raw, bool) or isinstance(index_raw, bool):
                raise ValueError
            duration = float(duration_raw)
            if type(index_raw) is int:
                index = index_raw
            elif isinstance(index_raw, str) and re.fullmatch(r"[+-]?\d+", index_raw.strip()):
                index = int(index_raw)
            else:
                raise ValueError
        except (TypeError, ValueError) as exc:
            raise ValueError(f"第 {line_number} 行 duration_sec/shot_index 类型错误") from exc
        if not math.isfinite(duration) or duration <= 0 or index < 0:
            raise ValueError(f"第 {line_number} 行时长或镜号无效")
        value["duration_sec"] = duration
        value["shot_index"] = index
        records.append(value)
    return records


def validate_manifest(manifest: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if manifest.get("schema_version") != 1:
        errors.append("语料来源 manifest.schema_version 必须为 1")
    if manifest.get("media_policy") != "annotations_only":
        errors.append("media_policy 必须为 annotations_only，禁止把电影正片纳入仓库")
    sources = manifest.get("sources") or []
    if not sources:
        errors.append("语料来源清单为空")
    for source in sources:
        missing = [field for field in REQUIRED_SOURCE_FIELDS if source.get(field) in (None, "")]
        if missing:
            errors.append(f"来源 {source.get('id', 'unknown')} 缺少: {','.join(missing)}")
    return errors


def _transition_matrix(sequences: Iterable[list[str]]) -> dict[str, dict[str, float]]:
    counts: dict[str, Counter[str]] = defaultdict(Counter)
    for sequence in sequences:
        for left, right in zip(sequence, sequence[1:]):
            counts[left][right] += 1
    result: dict[str, dict[str, float]] = {}
    for left, row in sorted(counts.items()):
        total = sum(row.values())
        result[left] = {right: round(count / total, 6) for right, count in sorted(row.items())}
    return result


def build_profile(records: list[dict[str, Any]], manifest: dict[str, Any], *,
                  profile_id: str, min_rating: float = 0.0,
                  min_films: int = 200) -> dict[str, Any]:
    errors = validate_manifest(manifest)
    if errors:
        raise ValueError("；".join(errors))
    if type(min_films) is not int or min_films <= 0 or not math.isfinite(min_rating):
        raise ValueError("min_films 必须为正整数，min_rating 必须为有限数")
    source_ids = {str(source["id"]) for source in manifest["sources"]}
    unknown_sources = sorted({str(record["source_id"]) for record in records} - source_ids)
    if unknown_sources:
        raise ValueError("镜头引用了 manifest 外来源: " + ",".join(unknown_sources))
    selected = []
    seen_shots: set[tuple[str, str, str, int]] = set()
    for record in records:
        if (type(record.get("shot_index")) is not int or record["shot_index"] < 0 or
                isinstance(record.get("duration_sec"), bool) or
                not math.isfinite(float(record.get("duration_sec", 0))) or float(record["duration_sec"]) <= 0):
            raise ValueError("语料镜号须非负整数，镜长须正向有限")
        try:
            rating = float(record.get("rating", 0))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{record['film_id']} 的 rating 不是数值") from exc
        if not math.isfinite(rating):
            raise ValueError(f"{record['film_id']} 的 rating 不是有限数")
        key = (str(record["source_id"]), str(record.get("edition", "unspecified")), str(record["film_id"]),
               str(record["scene_id"]), int(record["shot_index"]))
        if key in seen_shots:
            raise ValueError("发现重复镜头键: " + "/".join(map(str, key)))
        seen_shots.add(key)
        if rating >= min_rating:
            selected.append(record)
    film_key = lambda r: (str(r["source_id"]), str(r["film_id"]), str(r.get("edition", "unspecified")))
    films = {film_key(record) for record in selected}
    if not selected:
        raise ValueError("筛选后没有镜头样本")
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in selected:
        grouped[(*film_key(record), str(record["scene_id"]))].append(record)
    for shots in grouped.values():
        shots.sort(key=lambda item: item["shot_index"])
    durations = [record["duration_sec"] for record in selected]
    size_sequences = [[SHOT_SIZES.get(str(shot["shot_size"]), str(shot["shot_size"])) for shot in shots] for shots in grouped.values()]
    movement_sequences = [
        ["static" if str(shot["camera_movement"]) == "locked" else str(shot["camera_movement"]) for shot in shots] for shots in grouped.values()
    ]
    same_size_pairs = sum(
        left == right for sequence in size_sequences for left, right in zip(sequence, sequence[1:])
    )
    total_pairs = sum(max(0, len(sequence) - 1) for sequence in size_sequences)
    # 数量仅是统计，不认证来源、标注质量或逐镜适用性。
    status = "exploratory_only"
    scene_means = [sum(s["duration_sec"] for s in group)/len(group) for group in grouped.values()]
    by_film = defaultdict(list)
    for record in selected:
        by_film[film_key(record)].append(record["duration_sec"])
    film_means = [sum(values)/len(values) for values in by_film.values()]
    by_kind = defaultdict(list)
    for group in grouped.values():
        kinds = {str(shot.get("scene_kind", "unknown")) for shot in group}
        if len(kinds) != 1:
            raise ValueError("同一场景的scene_kind不能冲突")
        by_kind[next(iter(kinds))].append(sum(s["duration_sec"] for s in group)/len(group))
    return {
        "schema_version": 1,
        "profile_id": profile_id,
        "status": status,
        "production_validation": {"status": "unverified",
                                  "implemented": False,
                                  "sample_count_threshold_met": len(films) >= min_films},
        "selection": {"min_rating": min_rating, "minimum_films": min_films},
        "sample": {
            "film_count": len(films), "scene_count": len(grouped), "shot_count": len(selected),
        },
        "duration_sec": {
            "mean": round(sum(durations) / len(durations), 4),
            "p10": round(quantile(durations, 0.10), 4),
            "p50": round(quantile(durations, 0.50), 4),
            "p90": round(quantile(durations, 0.90), 4),
        },
        "scene_mean_duration_sec": {"p10": quantile(scene_means, .1), "p50": quantile(scene_means, .5),
                                    "p90": quantile(scene_means, .9)},
        "film_mean_duration_sec": {"p10": quantile(film_means, .1), "p50": quantile(film_means, .5),
                                   "p90": quantile(film_means, .9)},
        "scene_kind_envelopes": {kind: {"p10": quantile(values, .1), "p90": quantile(values, .9),
                                       "scene_count": len(values)} for kind, values in by_kind.items()},
        "weighting": "one_scene_one_vote; one_film_one_vote",
        "shot_size_frequency": dict(Counter(SHOT_SIZES.get(str(r["shot_size"]), str(r["shot_size"])) for r in selected)),
        "camera_movement_frequency": dict(Counter("static" if r["camera_movement"] == "locked" else str(r["camera_movement"]) for r in selected)),
        "shot_size_transitions": _transition_matrix(size_sequences),
        "camera_movement_transitions": _transition_matrix(movement_sequences),
        "same_size_cut_rate": round(same_size_pairs / total_pairs, 6) if total_pairs else None,
        "provenance": {
            "media_policy": manifest["media_policy"],
            "sources": manifest["sources"],
            "record_fields_retained": list(REQUIRED_RECORD_FIELDS) + ["rating"],
            "contains_frames": False,
        },
        "usage_limits": [
            "只用于聚合统计和序列审计，不复制具体影片镜头",
            "不得把片名、导演名或相邻关键帧写入生成提示词",
            "统计包络不能覆盖 30/180 度、轴线、动线和叙事因果硬规则",
        ],
    }


def audit_storyboard(profile: dict[str, Any], storyboard: dict[str, Any], *,
                     mode: str = "advisory", project=None, certificate_path=None) -> dict[str, Any]:
    if mode not in ALLOWED_MODES:
        raise ValueError("mode 必须为 advisory 或 enforce_envelope")
    errors: list[str] = []
    warnings: list[str] = []
    if profile.get("schema_version") != 1:
        errors.append("电影语法画像 schema_version 必须为 1")
    if mode == "enforce_envelope":
        try:
            try:
                from .corpus_validation import require as require_certificate
            except ImportError:
                from corpus_validation import require as require_certificate
            if project is None or not certificate_path:
                raise ValueError('缺少独立签名验收及逐镜语义适配')
            require_certificate(project, profile, storyboard, certificate_path)
        except Exception as exc:
            errors.append('生产语料认证未通过，enforce_envelope 不可用；自报 production_reference 不能认证: '+str(exc))
    shots = storyboard.get("shots") or []
    if not shots:
        errors.append("没有可审计镜头")
        return {"status": "blocked", "errors": errors, "warnings": warnings, "metrics": {}}
    durations = []
    sizes = []
    movements = []
    valid_shots = []
    for shot in shots:
        window = shot.get("window") or {}
        try:
            duration = float(window["end"]) - float(window["start"])
        except (KeyError, TypeError, ValueError):
            errors.append(f"{shot.get('id', 'unknown')} 缺少有效 window")
            continue
        if not math.isfinite(duration) or duration <= 0:
            errors.append(f"{shot.get('id', 'unknown')} window 时长必须为正向有限数")
            continue
        durations.append(duration)
        valid_shots.append(shot)
        camera = ((shot.get("crew") or {}).get("cinematography") or {})
        size = str(camera.get("shot_size", "unknown"))
        sizes.append(SHOT_SIZES.get(size, size))
        movement = str(camera.get("camera_movement", "unknown"))
        movements.append("static" if movement == "locked" else movement)
    average = sum(durations) / len(durations) if durations else None
    envelope = profile.get("scene_mean_duration_sec") or {}
    if not envelope:
        (errors if mode == "enforce_envelope" else warnings).append("画像缺少同层scene_mean_duration_sec，需重新建库")
    scenes = defaultdict(list)
    for shot, duration in zip(valid_shots, durations):
        scenes[shot.get("scene_id")].append(duration)
    if envelope.get("p10") is not None and envelope.get("p90") is not None:
        for sid, values in scenes.items():
            kind = next((s.get("scene_kind", "unknown") for s in valid_shots if s.get("scene_id") == sid), "unknown")
            selected_envelope = (profile.get("scene_kind_envelopes") or {}).get(kind)
            if kind != "unknown" and not selected_envelope:
                (errors if mode == "enforce_envelope" else warnings).append(f"缺少场景类型{kind}的真实参考语料，禁止混用类型")
                continue
            envelope = selected_envelope or profile.get("scene_mean_duration_sec") or {}
            scene_mean = sum(values)/len(values)
            if scene_mean >= float(envelope["p10"]) and scene_mean <= float(envelope["p90"]):
                continue
            message = (
                f"场景 {sid} 平均镜长 {scene_mean:.2f}s 超出参考场景均值 p10-p90 "
                f"({float(envelope['p10']):.2f}-{float(envelope['p90']):.2f}s)"
            )
            (errors if mode == "enforce_envelope" else warnings).append(message)
    unseen_size = []
    matrix = profile.get("shot_size_transitions") or {}
    for left, right in zip(sizes, sizes[1:]):
        if right not in (matrix.get(left) or {}):
            unseen_size.append(f"{left}->{right}")
    if unseen_size:
        warnings.append("参考语料中未见的景别转移: " + ",".join(sorted(set(unseen_size))))
    movement_changes = sum(left != right for left, right in zip(movements, movements[1:]))
    transition_count = max(0, len(movements) - 1)
    return {
        "status": "blocked" if errors else "pass_with_notes" if warnings else "pass",
        "errors": errors,
        "warnings": warnings,
        "metrics": {
            "shot_count": len(shots),
            "average_shot_sec": round(average, 4) if average is not None else None,
            "movement_change_rate": round(movement_changes / transition_count, 4)
            if transition_count else None,
        },
        "interpretation": "画像只判断统计异常；基础视听语法由 crew_pipeline 独立硬审。",
    }


def audit_binding(development: dict[str, Any], storyboard: dict[str, Any],
                  project: Path) -> dict[str, Any] | None:
    """画像是可选建议；一旦声明，仍须核验来源、指纹和实际使用模式。"""
    if "grammar_reference" not in development:
        return None
    binding = development["grammar_reference"]
    if not isinstance(binding, dict) or not binding:
        return {"status": "blocked", "errors": ["电影语法画像绑定必须为非空对象"],
                "warnings": [], "metrics": {}}
    required = ("profile_path", "profile_sha256", "profile_id", "mode", "rationale", "use_scope")
    missing = [field for field in required if binding.get(field) in (None, "", [], {})]
    if missing:
        return {"status": "blocked", "errors": ["电影语法画像绑定缺少: " + ",".join(missing)],
                "warnings": [], "metrics": {}}
    mode = str(binding["mode"])
    if mode not in ALLOWED_MODES:
        return {"status": "blocked",
                "errors": ["mode 必须为 advisory 或 enforce_envelope"],
                "warnings": [], "metrics": {}}
    relative = Path(str(binding["profile_path"]))
    if relative.is_absolute() or ".." in relative.parts:
        return {"status": "blocked", "errors": ["profile_path 必须是项目内相对路径"],
                "warnings": [], "metrics": {}}
    path = (project / relative).resolve()
    try:
        path.relative_to(project.resolve())
    except ValueError:
        return {"status": "blocked", "errors": ["profile_path 越出项目目录"],
                "warnings": [], "metrics": {}}
    if not path.is_file():
        return {"status": "blocked", "errors": [f"电影语法画像不存在: {relative}"],
                "warnings": [], "metrics": {}}
    actual = fingerprint(path)
    if binding["profile_sha256"] != actual:
        return {"status": "blocked", "errors": ["电影语法画像指纹已变化，必须重新批准"],
                "warnings": [], "metrics": {}}
    profile = json.loads(path.read_text(encoding="utf-8"))
    if binding["profile_id"] != profile.get("profile_id"):
        return {"status": "blocked", "errors": ["电影语法画像 profile_id 与绑定不一致"],
                "warnings": [], "metrics": {}}
    report = audit_storyboard(profile, storyboard, mode=mode, project=project,
                              certificate_path=binding.get('certificate_path'))
    report["profile"] = {"path": str(relative), "sha256": actual,
                         "profile_id": profile.get("profile_id")}
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="建立或审计匿名电影视听语法画像")
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build")
    build.add_argument("records", type=Path)
    build.add_argument("manifest", type=Path)
    build.add_argument("output", type=Path)
    build.add_argument("--profile-id", required=True)
    build.add_argument("--min-rating", type=float, default=0.0)
    build.add_argument("--min-films", type=int, default=200)
    audit = subparsers.add_parser("audit")
    audit.add_argument("project_dir", type=Path)
    audit.add_argument("profile", type=Path)
    audit.add_argument("--mode", choices=sorted(ALLOWED_MODES), default="advisory")
    audit.add_argument("--certificate", help="项目内独立签名验收证书相对路径；生产模式必需")
    args = parser.parse_args()
    if args.command == "build":
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        profile = build_profile(load_jsonl(args.records), manifest, profile_id=args.profile_id,
                                min_rating=args.min_rating, min_films=args.min_films)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n",
                               encoding="utf-8")
        print(json.dumps(profile, ensure_ascii=False, indent=2))
        return 0
    project = args.project_dir.resolve()
    profile = json.loads(args.profile.read_text(encoding="utf-8"))
    storyboard = json.loads((project / "state/storyboard.json").read_text(encoding="utf-8"))
    report = audit_storyboard(profile, storyboard, mode=args.mode, project=project,
                              certificate_path=args.certificate)
    report["profile"] = {
        "path": str(args.profile.resolve()), "sha256": fingerprint(args.profile),
        "profile_id": profile.get("profile_id"),
    }
    output = project / "gates/film_grammar_reference.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
