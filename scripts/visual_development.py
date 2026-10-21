"""ZY 视觉开发契约：验证来源、参考权限、导演路由与逐镜依赖；不自动创作。"""
import argparse
import hashlib
import json
import re
from pathlib import Path

try:
    from .crew_pipeline import validate_crew_pipeline
    from .film_grammar_corpus import audit_binding
except ImportError:
    from crew_pipeline import validate_crew_pipeline
    from film_grammar_corpus import audit_binding

AXES = ("light_contrast", "color_exposure", "lens_camera", "composition_space")
VENDOR = Path(__file__).resolve().parents[1] / "vendor" / "zy-cinematic-realism"


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def fingerprint(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def shot_fingerprint(shot: dict) -> str:
    fields = {key: value for key, value in shot.items()
              if key not in {"prompt_fingerprint", "status", "repair"}}
    return "sha256:" + hashlib.sha256(json.dumps(
        fields, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def indexed(items: list, label: str, errors: list) -> dict:
    result = {}
    for item in items:
        key = item.get("id")
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", key) or key in result:
            errors.append(f"{label} ID 为空或重复: {key}")
        else:
            result[key] = item
    return result


def validate(development: dict, storyboard: dict, style: dict,
             project: Path | None = None) -> list[str]:
    errors = []
    treatment = development.get("treatment", {})
    for key in ("concept", "form", "emotional_arc", "visual_motif", "climax", "performance_rationale"):
        if not treatment.get(key):
            errors.append(f"treatment 缺少 {key}")
    if treatment.get("status") not in {"proposed", "accepted"}:
        errors.append("treatment 必须区分 proposed / accepted")
    sources = indexed(development.get("sources", []), "source", errors)
    for sid, source in sources.items():
        if not source.get("locator") or not source.get("read_scope"):
            errors.append(f"来源 {sid} 缺少 locator/read_scope")
    if not sources or not development.get("source_ledger") or not development.get("scenes"):
        errors.append("视觉开发必须包含已读来源、故事台账与场景")
    ledger = indexed(development.get("source_ledger", []), "source_ledger", errors)
    for lid, entry in ledger.items():
        kind = entry.get("kind")
        if kind not in {"fact", "interpretation", "proposal"} or not entry.get("description"):
            errors.append(f"台账 {lid} 必须区分事实/解释/提案并给出内容")
        if entry.get("source_id") not in sources:
            errors.append(f"台账 {lid} 来源不存在")
        if kind == "proposal" and entry.get("status") not in {"proposed", "accepted"}:
            errors.append(f"提案 {lid} 不得自动成为用户锁")
    references = indexed(development.get("references", []), "reference", errors)
    rules = indexed(development.get("visual_rules", []), "visual_rule", errors)
    for rid, reference in references.items():
        if not reference.get("roles") or not reference.get("observed_scope"):
            errors.append(f"参考 {rid} 缺少职责与实际观察范围")
        if reference.get("kind") == "character" and set(reference.get("roles", [])) != {"likeness", "wardrobe"}:
            errors.append(f"人物参考 {rid} 只能控制 likeness + wardrobe")
        if reference.get("kind") == "visual_grammar":
            if not reference.get("medium") or not reference.get("transfer_scope"):
                errors.append(f"视觉参考 {rid} 缺少媒介与迁移范围")
    for rid, rule in rules.items():
        reference = references.get(rule.get("reference_id"), {})
        if reference.get("kind") != "visual_grammar" or not rule.get("evidence") or not rule.get("instruction"):
            errors.append(f"视觉规律 {rid} 必须来自实查视觉参考，不得从人物图抽取风格")
        if rule.get("transfer") not in {"strong", "conditional"}:
            errors.append(f"视觉规律 {rid} 必须声明迁移权限")
    if rules and not 5 <= len(rules) <= 8:
        errors.append("Decode Card 核心规律应为 5–8 条；无视觉参考时不制造规律")
    scenes = indexed(development.get("scenes", []), "scene", errors)
    for sid, scene in scenes.items():
        for key in ("topology", "light_map", "material_rules", "master_prompt"):
            if not scene.get(key):
                errors.append(f"场景 {sid} 缺少 {key}")
        if scene.get("source_beat") not in ledger:
            errors.append(f"场景 {sid} 缺少有效来源节拍")
    director = development.get("director", {})
    requested = style.get("director_ref", {}).get("name")
    if requested:
        if director.get("name") != requested:
            errors.append("导演路由与用户/项目选择不一致")
        path = (VENDOR / str(director.get("source_file", ""))).resolve()
        director_root = (VENDOR / "references" / "directors").resolve()
        if (path.parent != director_root or not path.is_file()
                or path.name in {"index.md", "recommendation-matrix.md"}):
            errors.append("指定导演必须引用实际导演文件，不能只填姓名")
        elif director.get("source_sha256") != fingerprint(path):
            errors.append("导演来源指纹缺失或已过期")
        try:
            from .director_catalog import resolve_director
        except ImportError:
            from director_catalog import resolve_director
        try:
            routed = resolve_director(requested, director_root)
            if path.name != routed['file']:
                errors.append('导演名称与ZY索引实际方法文件不匹配，禁止借用其他导演文件')
        except (OSError, ValueError) as exc:
            errors.append(f'导演索引路由失败: {exc}')
        if director.get("strength") not in {"轻微", "明确", "强烈"}:
            errors.append("导演强度无效")
        elif director["strength"] != style.get("director_ref", {}).get("intensity", "明确"):
            errors.append("导演强度与 style 不一致")
        active = director.get("active_axes", [])
        if not active or len(active) != len(set(active)) or not set(active) <= set(AXES):
            errors.append("导演必须明确激活轴，其他轴保留场景事实")
        if director.get("strength") == "轻微" and not 1 <= len(director.get("active_traits", [])) <= 2:
            errors.append("轻微导演方法只激活 1–2 个兼容特征")
        if not director.get("rationale"):
            errors.append("导演路由缺少与故事目标的关联")
    elif director.get("name"):
        errors.append("没有选择导演，不得自动注入导演方法")
    bible = storyboard.get("continuity_bible", {})
    for key in ("character_lock", "wardrobe_lock", "prop_lock", "location_topology", "lighting_bible"):
        if not bible.get(key):
            errors.append(f"Continuity Bible 缺少 {key}")
    previous_state = None
    for shot in storyboard.get("shots", []):
        sid = shot.get("id")
        if shot.get("scene_id") not in scenes or shot.get("source_beat") not in ledger:
            errors.append(f"{sid} 场景/来源节拍引用无效")
        if not isinstance(shot.get("state_before"), dict) or not isinstance(shot.get("state_after"), dict):
            errors.append(f"{sid} 必须声明前后状态字典")
        elif previous_state is not None and shot["state_before"] != previous_state:
            errors.append(f"{sid} 前态不等于上一镜后态，状态不能静默重置")
        previous_state = shot.get("state_after")
        active_rules = shot.get("active_rule_ids", [])
        if rules and not 3 <= len(active_rules) <= 5:
            errors.append(f"{sid} 必须选择 3–5 条相关视觉规律")
        if len(active_rules) != len(set(active_rules)) or not set(active_rules) <= set(rules):
            errors.append(f"{sid} 使用了未知或重复的视觉规律")
        decisions = shot.get("director_decisions", {})
        for axis in director.get("active_axes", []) if requested else []:
            item = decisions.get(axis, {})
            if not item.get("instruction") or not item.get("reason") or not item.get("observable_check"):
                errors.append(f"{sid} 导演轴 {axis} 缺少指令/场景理由/观察依据")
        if set(decisions) - set(director.get("active_axes", [])):
            errors.append(f"{sid} 不得扩大已选导演激活轴")
        motion = shot.get("motion_design", {})
        for key in ("start_state", "action", "physics", "camera_motion", "end_state"):
            if not motion.get(key):
                errors.append(f"{sid} 视频动作设计缺少 {key}")
    if not storyboard.get("shots"):
        errors.append("没有视觉分镜，不能编译生成制作包")
    else:
        errors.extend(validate_crew_pipeline(development, storyboard, project=project))
    # 可选画像绑定由 validate_crew_pipeline 校验；不能以统计认证审美。
    if "grammar_reference" in development:
        if not isinstance(development["grammar_reference"], dict) or not development["grammar_reference"]:
            errors.append("电影画像绑定必须是非空对象；不使用画像时请省略 grammar_reference")
        elif project is None:
            errors.append("声明电影画像时必须提供项目目录，禁止跳过画像检查")
    return errors


def require(project: Path, storyboard: dict, style: dict) -> dict:
    try:
        from .pipeline_contracts import require_no_transaction
    except ImportError:
        from pipeline_contracts import require_no_transaction
    require_no_transaction(project)
    path = project / "state" / "development.json"
    if not path.is_file():
        raise ValueError("缺少 state/development.json，请先完成 ZY 视觉开发")
    result = load(path)
    errors = validate(result, storyboard, style, project)
    for name, key in (("timeline.json", "timeline_fingerprint"),
                      ("story_plan.json", "story_plan_fingerprint"),
                      ("visual_edit_plan.json", "visual_edit_fingerprint")):
        source = project / "state" / name
        if not source.is_file() or fingerprint(source) != storyboard.get(key):
            errors.append(f"分镜依赖缺失或过期: {name}")
    music = project / "state" / "music_structure.json"
    if storyboard.get("music_structure_fingerprint") != (fingerprint(music) if music.is_file() else None):
        errors.append("音乐结构依赖过期")
    if errors:
        raise ValueError("ZY 视觉开发未通过:\n" + "\n".join(errors))
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("project_dir")
    args = parser.parse_args()
    project = Path(args.project_dir)
    errors = validate(load(project / "state" / "development.json"),
                      load(project / "state" / "storyboard.json"),
                      load(project / "state" / "style.json"), project)
    report = {"status": "blocked" if errors else "structural_pass", "errors": errors,
              "visual_quality": "requires_observed_review"}
    (project / "gates").mkdir(exist_ok=True)
    (project / "gates" / "visual_development.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    raise SystemExit(1 if errors else 0)
