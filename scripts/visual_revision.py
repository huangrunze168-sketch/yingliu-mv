"""检查单变量 Remix 与定向修复契约；不自动重新生成或接受提案。"""
import argparse
import json
from pathlib import Path

AXIS_FIELDS = {
    "camera": {"camera_witness", "camera", "information_hierarchy"},
    "moment": {"story_beat", "narrative_before", "current_action", "implied_next", "props", "state_after"},
    "light": {"light", "material_response", "time"},
    "blocking": {"blocking", "gaze_target", "current_action", "action_physics"},
    "spatial": {"information_hierarchy", "visual_center", "camera_witness"},
    "style": {"visual_rules", "director_decisions", "material_response"},
    "material": {"material_response", "props"},
    "weather": {"weather", "light", "material_response", "action_physics"},
}


def check_remix(base: dict, candidate: dict, axis: str) -> list:
    if axis not in AXIS_FIELDS:
        return ["必须选择一个已定义的 Remix 主轴"]
    changed = {key for key in set(base) | set(candidate) if base.get(key) != candidate.get(key)}
    errors = []
    locks = set(base.get("user_locked", [])) | {"character_lock", "wardrobe_lock", "scene_id", "topology"}
    if changed & locks:
        errors.append("改变用户锁或地点事实: " + ",".join(sorted(changed & locks)))
    unrelated = changed - AXIS_FIELDS[axis]
    if unrelated:
        errors.append("变化超出单轴及必要后果: " + ",".join(sorted(unrelated)))
    if not changed:
        errors.append("候选没有可比较变化")
    return errors


def check_repair(plan: dict) -> list:
    errors = []
    failures = plan.get("dominant_failures", [])
    if not 1 <= len(failures) <= 3:
        errors.append("每次只定位 1–3 项主导失败")
    for failure in failures:
        if (failure.get("classification") != "actual_drift" or not failure.get("evidence")
                or not failure.get("cause") or not failure.get("dimension")):
            errors.append("失败必须是有观察证据与原因的实际漂移，合理适配不进入修复")
    if plan.get("structural_failure") is True:
        if plan.get("action") != "rebuild_scene_master":
            errors.append("故事、空间、机位结构失败必须返回 Scene Master")
    elif plan.get("action") != "surgical_repair" or not plan.get("change_only"):
        errors.append("局部修复必须声明 surgical_repair 与 change_only")
    if not plan.get("preserve_exactly"):
        errors.append("修复必须声明 preserve_exactly，保护已成功事实")
    return errors


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("remix", "repair"))
    parser.add_argument("file")
    parser.add_argument("--candidate")
    parser.add_argument("--axis", choices=tuple(AXIS_FIELDS))
    args = parser.parse_args()
    original = json.loads(Path(args.file).read_text(encoding="utf-8"))
    if args.action == "remix":
        if not args.candidate or not args.axis:
            parser.error("remix 需要 --candidate 与 --axis")
        candidate = json.loads(Path(args.candidate).read_text(encoding="utf-8"))
        errors = check_remix(original, candidate, args.axis)
    else:
        errors = check_repair(original)
    print(json.dumps({"status": "blocked" if errors else "contract_pass", "errors": errors,
                      "generated": False}, ensure_ascii=False))
    raise SystemExit(1 if errors else 0)
