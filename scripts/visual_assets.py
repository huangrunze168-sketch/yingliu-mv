"""场景母版和分镜关键帧的依赖登记与过期检查，不提交任何生成任务。"""
import argparse
import hashlib
import json
import copy
from pathlib import Path

try:
    from .visual_development import fingerprint, load
except ImportError:
    from visual_development import fingerprint, load

try:
    from .pipeline_contracts import atomic_batch, digest, require_no_transaction
except ImportError:
    from pipeline_contracts import atomic_batch, digest, require_no_transaction


def dependency_snapshot(project: Path) -> dict:
    names = ("style.json", "timeline.json", "music_structure.json")
    snapshot = {"state/" + name: fingerprint(project / "state" / name)
                for name in names if (project / "state" / name).is_file()}
    for path in (project / "input").glob("protagonist.*"):
        snapshot[path.relative_to(project).as_posix()] = fingerprint(path)
    if (project/'state/reference_roles.json').is_file():
        try:
            from .reference_roles import require as require_roles
        except ImportError:
            from reference_roles import require as require_roles
        _, versions = require_roles(project)
        snapshot.update({str(p): value for p, value in versions.items()})
    return snapshot


def scoped_snapshot(project: Path, scene_id: str, shot_id: str | None) -> str:
    development = load(project / "state" / "development.json")
    storyboard = load(project / "state" / "storyboard.json")
    scene = next((s for s in development["scenes"] if s["id"] == scene_id), None)
    shot = next((s for s in storyboard["shots"] if s["id"] == shot_id), None) if shot_id else None
    if shot:
        shot = {k: v for k, v in shot.items() if k not in {"prompt_fingerprint", "status", "repair"}}
    relevant_beats = {scene.get("source_beat") if scene else None, shot.get("source_beat") if shot else None}
    value = {"scene": scene, "shot": shot, "treatment": development.get("treatment"),
             "director": development.get("director"), "references": development.get("references"),
             "crew_bible": development.get("crew_bible"),
             "visual_rules": development.get("visual_rules"),
             "ledger": [e for e in development.get("source_ledger", []) if e["id"] in relevant_beats],
             "continuity": storyboard.get("continuity_bible")}
    return "sha256:" + hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def asset_errors(project: Path, asset: dict, assets: dict) -> list:
    errors = []
    path = project / asset.get("path", "")
    if not path.is_file() or fingerprint(path) != asset.get("sha256"):
        errors.append("资产不存在或内容指纹已改变")
    if asset.get("status") != "approved" or not asset.get("review_evidence"):
        errors.append("资产未经过实际观察批准")
    review = asset.get("static_review") or {}
    if (review.get("media_sha256") != asset.get("sha256") or not review.get("observer")
            or not review.get("evidence")):
        errors.append("缺少绑定当前图片的独立静帧审查记录")
    for name, expected in asset.get("dependencies", {}).items():
        dep = project / name
        if not dep.is_file() or fingerprint(dep) != expected:
            errors.append(f"依赖过期: {name}")
    if not asset.get("dependencies"):
        errors.append("缺少依赖指纹")
    if scoped_snapshot(project, asset["scene_id"], asset.get("shot_id")) != asset.get("scope_sha256"):
        errors.append("场景或镜头的设计依赖已变化")
    parent_id = asset.get("parent_asset")
    if parent_id:
        parent = assets.get(parent_id)
        if not parent or parent.get("sha256") != asset.get("parent_sha256"):
            errors.append("母版版本已改变")
        elif parent.get("parent_asset"):
            errors.append("关键帧只能引用场景母版，禁止循环父依赖")
        else:
            errors.extend("母版: " + e for e in asset_errors(project, parent, {}))
    return errors


def require_keyframe(project: Path, shot: dict, registry: dict) -> dict:
    assets = registry.get("assets", {})
    frame = assets.get(shot["id"])
    if not frame or frame.get("kind") != "keyframe" or frame.get("scene_id") != shot["scene_id"]:
        raise ValueError(f"{shot['id']} 缺少同场景分镜关键帧")
    parent = assets.get(frame.get("parent_asset"), {})
    if parent.get("kind") != "scene_master" or parent.get("scene_id") != shot["scene_id"]:
        raise ValueError(f"{shot['id']} 关键帧母版引用无效")
    errors = asset_errors(project, frame, assets)
    if errors:
        raise ValueError(f"{shot['id']} 资产需复核: " + "; ".join(errors))
    return frame


def register(project: Path, aid: str, kind: str, scene_id: str, path: Path,
             evidence: str, parent_id: str | None = None) -> dict:
    project, path = Path(project).resolve(), Path(path).resolve()
    require_no_transaction(project)
    registry_path = project/'state/visual_assets.json'
    inputs = [project/'state'/name for name in ('development.json', 'storyboard.json',
                                               'style.json', 'timeline.json', 'music_structure.json')]
    inputs += [path, registry_path]
    expected = {p: digest(p) if p.is_file() else None for p in inputs}
    if not evidence.strip() or not path.is_file():
        raise ValueError("登记批准资产必须提供实际审查证据与存在的文件")
    import subprocess
    if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}:
        raise ValueError("静态资产不接受视频容器")
    measured = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                               "-count_frames", "-show_entries",
                               "stream=width,height,codec_name,nb_read_frames",
                               "-of", "json", str(path)], capture_output=True, text=True, check=True)
    streams = json.loads(measured.stdout).get("streams", [])
    if (not streams or streams[0].get("codec_name") not in {"png", "mjpeg", "webp", "gif", "bmp"}
            or streams[0].get("width", 0) <= 0 or streams[0].get("height", 0) <= 0
            or str(streams[0].get("nb_read_frames")) != "1"):
        raise ValueError("资产须为可解码的单帧静态图片，并有有效尺寸")
    subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-frames:v", "1", "-f", "null", "-"],
                   capture_output=True, check=True)
    development = load(project / "state" / "development.json")
    if scene_id not in {s["id"] for s in development["scenes"]}:
        raise ValueError("场景不存在")
    registry_path = project / "state" / "visual_assets.json"
    registry = load(registry_path) if registry_path.is_file() else {"assets": {}}
    record = {"kind": kind, "scene_id": scene_id, "path": str(path.resolve()),
              "sha256": fingerprint(path), "status": "pending", "review_evidence": "",
              "registration_note": evidence,
              "review_scope": "declared_static_structure", "agent_inspected": False,
              "inspection_source": "caller_declared_evidence", "media_probe": streams[0],
              "user_accepted": False, "dynamic_verified": False,
              "quality_claim": "not_certified_by_registry",
              "dependencies": dependency_snapshot(project),
              "scope_sha256": scoped_snapshot(project, scene_id, aid if kind == "keyframe" else None)}
    if kind == "keyframe":
        storyboard = load(project / "state" / "storyboard.json")
        shot = next((s for s in storyboard["shots"] if s["id"] == aid), None)
        if not shot or shot["scene_id"] != scene_id:
            raise ValueError("关键帧必须匹配 storyboard 镜头及场景")
        parent = registry["assets"].get(parent_id, {})
        if parent.get("kind") != "scene_master" or parent.get("scene_id") != scene_id:
            raise ValueError("关键帧须引用同场景母版")
        errors = asset_errors(project, parent, registry["assets"])
        if errors:
            raise ValueError("母版需复核: " + "; ".join(errors))
        record.update(parent_asset=parent_id, parent_sha256=parent["sha256"], shot_id=aid)
    registry["assets"][aid] = record
    atomic_batch({registry_path: json.dumps(registry, ensure_ascii=False, indent=2)+'\n'}, expected=expected)
    return record


def approve_static(project, aid, *, observed_sha256, observer, evidence):
    """显式记录审查者声明；登记和解码检查绝不自动批准，也不认证实际看图。"""
    project = Path(project).resolve()
    require_no_transaction(project)
    path = project/'state/visual_assets.json'
    version = digest(path)
    expected = {project/'state'/name: digest(project/'state'/name)
                for name in ('development.json', 'storyboard.json')}
    registry = load(path)
    record = copy.deepcopy(registry['assets'][aid])
    if (not isinstance(observer, str) or not observer.strip()
            or not isinstance(evidence, str) or not evidence.strip()):
        raise ValueError('须有明确审查者和实际观察证据')
    if observed_sha256 != record['sha256'] or digest(project/record['path']) != observed_sha256:
        raise ValueError('实际观察的图片版本不匹配，不能批准替换后的图片')
    record.update(status='approved', review_evidence=evidence,
                  static_review={'media_sha256': observed_sha256, 'observer': observer,
                                 'evidence': evidence, 'scope': 'reviewer_attested_static_only'})
    errors = asset_errors(project, record, registry['assets'])
    if errors:
        raise ValueError('静帧批准依据失效: ' + '; '.join(errors))
    registry['assets'][aid] = record
    expected.update({path: version, project/record['path']: observed_sha256})
    for name, value in record['dependencies'].items():
        expected[project/name] = value
    atomic_batch({path: json.dumps(registry, ensure_ascii=False, indent=2)+'\n'}, expected=expected)
    return record


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("register", "approve-static", "check"))
    parser.add_argument("project_dir")
    parser.add_argument("--id")
    parser.add_argument("--kind", choices=("scene_master", "keyframe"))
    parser.add_argument("--scene")
    parser.add_argument("--path")
    parser.add_argument("--evidence")
    parser.add_argument("--parent")
    parser.add_argument("--observed-sha256")
    parser.add_argument("--observer")
    args = parser.parse_args()
    project = Path(args.project_dir).resolve()
    if args.action == "register":
        if not all((args.id, args.kind, args.scene, args.path, args.evidence)):
            parser.error("register 需要 --id --kind --scene --path --evidence")
        result = register(project, args.id, args.kind, args.scene, Path(args.path), args.evidence, args.parent)
        print(json.dumps(result, ensure_ascii=False))
    elif args.action == "approve-static":
        if not all((args.id, args.observed_sha256, args.observer, args.evidence)):
            parser.error("approve-static需要 --id --observed-sha256 --observer --evidence")
        print(json.dumps(approve_static(project, args.id, observed_sha256=args.observed_sha256,
                                       observer=args.observer, evidence=args.evidence), ensure_ascii=False))
    else:
        registry = load(project / "state" / "visual_assets.json")
        errors = {key: asset_errors(project, value, registry["assets"])
                  for key, value in registry["assets"].items()}
        failures = {key: value for key, value in errors.items() if value}
        print(json.dumps({"status": "needs_review" if failures else "current", "errors": failures}, ensure_ascii=False))
        raise SystemExit(1 if failures else 0)
