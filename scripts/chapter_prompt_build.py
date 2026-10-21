#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 ZY Scene Master / Continuity / Shot Deltas 编译为模型中立章节制作包。"""
import argparse
import hashlib
import json
import sys
from pathlib import Path
try:
    from .crew_execution import execution_lines
except ImportError:
    from crew_execution import execution_lines

try:
    from .visual_development import require as require_development, fingerprint, shot_fingerprint
    from .visual_assets import require_keyframe
    from .gate_checks import check_storyboard, check_prompt
    from .omni_reference import build_omni_reference_blueprint
except ImportError:
    from visual_development import require as require_development, fingerprint, shot_fingerprint
    from visual_assets import require_keyframe
    from gate_checks import check_storyboard, check_prompt
    from omni_reference import build_omni_reference_blueprint


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def digest(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def relative_window(shot: dict, chapter_start: float) -> str:
    start = float(shot["meta"]["window"]["start"]) - chapter_start
    end = float(shot["meta"]["window"]["end"]) - chapter_start
    return f"{start:05.2f}-{end:05.2f}"


def compile_chapter(chapter: dict, prompt_map: dict[str, dict], aspect_ratio: str,
                    has_character_reference: bool = True, character_names: list[str] | None = None) -> dict:
    chapter_start = float(chapter["window"]["start"])
    shots = [prompt_map[sid] for sid in chapter["shot_ids"]]
    continuity = chapter.get("continuity_lock", {})
    chapter_shots = {item.get("id"): item for item in chapter.get("shot_deltas", [])}
    beats = []
    if character_names is not None and (len(character_names) != len(set(character_names))
            or any(not isinstance(n, str) or not n.strip() for n in character_names)):
        raise ValueError('人物名称须非空且唯一')
    character_count = len(character_names) if character_names is not None else int(has_character_reference)
    if character_count and not has_character_reference:
        raise ValueError('人物名称不能缺失实际人物参考')
    first_keyframe_index = character_count+1
    for shot_index, shot in enumerate(shots, first_keyframe_index):
        meta = shot["meta"]
        direction = shot.get("direction", {})
        crew = direction.get("crew", {})
        method = direction.get("method", {})
        scene_master = direction.get("scene_master", {})
        missing_axes = [key for key in (
            "light_contrast", "color_exposure", "lens_camera", "composition_space"
        ) if not str(method.get(key, "")).strip()]
        missing_scene_master = [key for key in (
            "story_intent", "obstacle", "audience_information",
            "shooting_station", "frame_boundary", "visual_acceptance",
        ) if not str(scene_master.get(key, "")).strip()]
        if (missing_axes or missing_scene_master
                or not str(direction.get("edit_trigger", "")).strip()):
            raise ValueError(
                f"{meta.get('shot_id')} 未通过导演编译：missing_axes={missing_axes} "
                f"missing_scene_master={missing_scene_master} "
                f"edit_trigger={bool(direction.get('edit_trigger'))}"
            )
        lip = bool(meta.get("lip_sync"))
        start_sec = round(float(meta["window"]["start"]) - chapter_start, 10)
        end_sec = round(float(meta["window"]["end"]) - chapter_start, 10)
        chapter_shot = chapter_shots.get(meta["shot_id"], {})
        beats.append({
            "time": relative_window(shot, chapter_start),
            "start_sec": start_sec,
            "end_sec": end_sec,
            "shot_id": meta["shot_id"],
            "story_change": shot["story"]["shot_delta"],
            "current_action": shot["performance"].splitlines()[0],
            "space_and_blocking": shot["scene"],
            "character_and_wardrobe": shot.get("subject", ""),
            "camera_witness": shot["camera"],
            "director_axes": method,
            "scene_master": scene_master,
            "crew": crew,
            "motion_physics": direction.get("anti_ai", {}).get("motion_rule", ""),
            "performance_audio_intent": (
                f"需要演唱口型；完整行歌词仅作上下文：{meta.get('lyric_text', '')}；实际口型服从本镜音频窗口，禁止重唱被裁掉的部分" if lip
                else "非演唱镜头；嘴唇自然闭合，不跟随人声开合"
            ),
            "cut_reason": direction["edit_trigger"],
            "film_grammar": direction.get('film_grammar'),
            "cut_relation": chapter_shot.get("cut_relation", ""),
            "editorial_role": chapter_shot.get("editorial_role", ""),
            "motion_design": shot.get("motion_design", {}),
            "visual_rules": shot.get("visual_rules", []),
            "continuity": shot.get("continuity", {}),
            "keyframe_handle": f"@图片 {shot_index}",
        })

    reference_lines = []
    if character_names:
        reference_lines.extend(f'@图片 {i}：{name}，只参考五官、发型、耳饰和服装，不继承背景、姿态、构图或灯光。'
                               for i, name in enumerate(character_names, 1))
    elif has_character_reference:
        reference_lines.append(
            "@图片 1：只参考同一人物的五官比例、发型、耳饰与服装构造；"
            "不参考背景、姿势、构图和灯光。"
        )
    reference_lines.extend(
        f"{beat['keyframe_handle']}：只参考 {beat['shot_id']} 的构图起点、人物站位与场景地理；"
        + (('人物身份和服装分别服从 '+ '、'.join(f'@图片 {i}' for i in range(1, character_count+1))+'。') if has_character_reference else
           "不得从该构图参考虚构固定人物身份。")
        for beat in beats
    )
    lines = [
        f"模型中立的 {aspect_ratio} 写实叙事音乐 MV 章节制作包。参考图只负责已声明的身份、服装或场景职责；不得继承未授权的构图、背景或灯光。",
        "本章是生成包，不是一个长镜头。每个定时段都是独立剪辑镜头；必须按切点改变机位、景别或观看对象，不得用同一场景连续运动替代多镜覆盖。",
        "后端名称、请求模式、参数字段和音频绑定方式均由用户选定后，再由外部适配层按实测契约编译；本提示词不假设任何平台能力。",
        "",
        "【全能参考素材职责】",
        *reference_lines,
        "@音频 1：只参考本章表演节奏、演唱进入点和切点；不得改编、重唱或替换最终母带。",
        "所有参考均按上述职责使用，不得把一张素材的全部画面属性扩散到其他镜头。",
        "",
        "【本章叙事弧】",
        f"开始状态：{shots[0]['story']['before']}",
        f"本章变化：{'；'.join(shot['story']['shot_delta'] for shot in shots)}",
        f"结束指向：{shots[-1]['story']['next']}",
        "",
        "【连续性硬锁】",
        f"人物：{continuity.get('character_lock', '')}",
        f"服装：{continuity.get('wardrobe_lock', '')}",
        f"道具：{continuity.get('prop_lock', '')}",
        f"地理与方向轴：{continuity.get('location_topology', '')}",
        f"光源连续性：{continuity.get('lighting_bible', '')}",
        "",
        "【按时间执行的镜头】",
    ]
    for beat in beats:
        try:
            from .film_grammar_bridge import organization_text
        except ImportError:
            from film_grammar_bridge import organization_text
        if beat.get('film_grammar'):
            lines.append('- '+organization_text(beat['film_grammar']))
        lines.extend([
            f"[{beat['time']}] {beat['shot_id']}",
            f"- 变化：{beat['story_change']}",
            f"- 表演：{beat['current_action']}",
            f"- 空间：{beat['space_and_blocking']}",
            f"- 构图起点参考：{beat['keyframe_handle']}；只取本镜构图、站位和场景关系",
            f"- 人物与服装锁：{beat['character_and_wardrobe']}",
            f"- 观看：{beat['camera_witness']}",
            f"- 意图与阻力：{beat['scene_master']['story_intent']}；{beat['scene_master']['obstacle']}",
            f"- 观众信息：{beat['scene_master']['audience_information']}",
            f"- 实际机位与画框：{beat['scene_master']['shooting_station']}；{beat['scene_master']['frame_boundary']}",
            f"- 可观察验收：{beat['scene_master']['visual_acceptance']}",
            *('- ' + instruction for instruction in execution_lines(beat['crew'])),
            "- 导演四轴：" + "；".join(
                str(beat["director_axes"].get(key, "")) for key in
                ("light_contrast", "color_exposure", "lens_camera", "composition_space")
            ),
            "- 已选视觉特征：" + "；".join(
                str(item) for item in beat["director_axes"].get("active_traits", [])
            ),
            f"- 物理：{beat['motion_physics']}",
            f"- 声画意图：{beat['performance_audio_intent']}",
            f"- 切点：{beat['cut_reason']}",
            f"- 剪辑关系：{beat['cut_relation'] or ('章节结束' if beat is beats[-1] else '未定义')}",
            f"- 剪辑职责：{beat['editorial_role'] or '保持本镜叙事职责'}",
            "- 动作起止与过程：" + json.dumps(beat["motion_design"], ensure_ascii=False),
            "- 连续性变化：" + json.dumps(beat["continuity"], ensure_ascii=False),
            "- 相关参考视觉规律：" + "；".join(beat["visual_rules"]),
        ])
    lines.extend([
        "", "【去 AI 化成片约束】",
        "- 不做写真、海报、游戏 CG 或服装广告；人物动作服务叙事，不持续正面居中展示美貌。",
        "- 皮肤、布、木、纸、石与金属保留真实受光差异；只让一到两个叙事区域清楚。",
        "- 身体、衣料、头发、尘沙与道具分别服从重量、惯性、阻力、遮挡和接触关系。",
        "- 禁止无来源轮廓光、装饰雾、静态图数字推拉、无因果溶解和变形转场。",
    ])
    prompt = "\n".join(lines).strip() + "\n"
    return {
        "chapter_id": chapter["id"],
        "format": "model-neutral",
        "backend": None,
        "beats": beats,
        "reference_mode": "omni_reference_blueprint",
        "prompt": prompt,
        "prompt_sha256": digest(prompt),
    }


def build(project: Path) -> dict:
    try:
        from .pipeline_contracts import atomic_batch
    except ImportError:
        from pipeline_contracts import atomic_batch
    updates = {}
    project = Path(project).resolve()
    tracked = [project/'state'/f'{name}.json' for name in (
        'chapter_plan', 'style', 'storyboard', 'development', 'timeline', 'visual_assets',
        'story_plan', 'visual_edit_plan', 'music_structure')]
    tracked.append(project/'gates/gate5.json')
    tracked.append(Path(__file__).resolve().parents[1]/'vendor/zy-cinematic-realism/references/directors/index.md')
    tracked.extend((project/'prompts').glob('SH*.json'))
    tracked.extend((project/'prompts/motion').glob('SH*.json'))
    expected = {path: fingerprint(path) if path.is_file() else None for path in tracked}
    plan_path = project / "state" / "chapter_plan.json"
    plan = load(plan_path)
    style = load(project / "state" / "style.json")
    storyboard = load(project / "state" / "storyboard.json")
    if plan.get("source_storyboard_fingerprint") != fingerprint(project / "state" / "storyboard.json"):
        raise ValueError("章节计划依赖旧分镜，请重建章节计划")
    try:
        from .crew_rehearsal import require_before_prompts
    except ImportError:
        from crew_rehearsal import require_before_prompts
    require_before_prompts(project)
    try:
        from .film_grammar_bridge import require as require_organization
    except ImportError:
        from film_grammar_bridge import require as require_organization
    expected.update(require_organization(project, storyboard))
    development = require_development(project, storyboard, style)
    if style.get('director_ref', {}).get('name'):
        try:
            from .visual_development import VENDOR
        except ImportError:
            from visual_development import VENDOR
        expected[(VENDOR/development['director']['source_file']).resolve()] = development['director']['source_sha256']
    if development.get('grammar_reference'):
        binding = development['grammar_reference']
        expected[(project/binding['profile_path']).resolve()] = binding['profile_sha256']
    timeline = load(project / "state" / "timeline.json")
    try:
        from .chapter_plan_build import require_production_timing
    except ImportError:
        from chapter_plan_build import require_production_timing
    require_production_timing(timeline, storyboard["shots"])
    if not all(item["pass"] for item in check_storyboard(storyboard, timeline)):
        raise ValueError("章节编译前必须通过关6")
    gate = load(project / "gates" / "gate5.json")
    if not gate.get("summary", {}).get("pass"):
        raise ValueError("章节编译前必须通过关5")
    recorded = {item["file"]: item["fingerprint"] for item in gate.get("shots", [])}
    for shot in storyboard["shots"]:
        path = project / "prompts" / f"{shot['id']}.json"
        if recorded.get(path.name) != hashlib.sha256(path.read_bytes()).hexdigest():
            raise ValueError(f"{shot['id']} 提示词已变动，重新运行关5")
        prompt = load(path)
        try:
            from .crew_execution import validate_motion_package
        except ImportError:
            from crew_execution import validate_motion_package
        motion_errors = validate_motion_package(prompt, load(project/'prompts/motion'/path.name), fingerprint(path))
        if motion_errors: raise ValueError('; '.join(motion_errors))
        checks = check_prompt(prompt, timeline, style, any((project / "input").glob("protagonist.*"))
                              or (project/'state/reference_roles.json').is_file(), shot)
        if (not all(item["pass"] for item in checks)
                or prompt["meta"].get("development_fingerprint") != fingerprint(project / "state/development.json")
                or prompt["meta"].get("shot_design_fingerprint") != shot_fingerprint(shot)):
            raise ValueError(f"{shot['id']} 制作包对应旧设计，重新编译并运行关5")
    registry = load(project / "state" / "visual_assets.json")
    for asset in registry['assets'].values():
        expected[(project/asset['path']).resolve()] = asset['sha256']
    frames = {shot["id"]: require_keyframe(project, shot, registry) for shot in storyboard["shots"]}
    aspect_ratio = str(style.get("aspect_ratio", "")).strip()
    if not aspect_ratio:
        raise ValueError("style.aspect_ratio 不能为空")
    prompt_map = {path.stem: load(path) for path in (project / "prompts").glob("SH*.json")}
    out_dir = project / "prompts" / "chapters"
    out_dir.mkdir(parents=True, exist_ok=True)
    outputs = []
    protagonist = next(iter((project / "input").glob("protagonist.*")), None)
    if protagonist: expected[protagonist] = fingerprint(protagonist)
    characters = None
    if (project/'state/reference_roles.json').is_file():
        try:
            from .reference_roles import require as require_roles
        except ImportError:
            from reference_roles import require as require_roles
        roles, role_versions = require_roles(project)
        expected.update(role_versions)
        characters = roles['reference_bindings']
    for chapter in plan.get("chapters", []):
        missing = [sid for sid in chapter.get("shot_ids", []) if sid not in prompt_map]
        if missing:
            raise FileNotFoundError("缺少逐镜提示词: " + ", ".join(missing))
        compiled = compile_chapter(
            chapter, prompt_map, aspect_ratio,
            has_character_reference=bool(characters) or protagonist is not None,
            character_names=[c['character'] for c in characters] if characters is not None else None,
        )
        compiled["keyframe_references"] = {
            sid: {"path": frames[sid]["path"], "sha256": frames[sid]["sha256"],
                  "roles": ["shot_composition", "blocking_start_state", "location_continuity"]}
            for sid in chapter["shot_ids"]}
        protagonist_source = ({
            "local_path": str(protagonist.relative_to(project)).replace("\\", "/"),
            "sha256": hashlib.sha256(protagonist.read_bytes()).hexdigest(),
        } if protagonist else None)
        blueprint = build_omni_reference_blueprint(
            chapter, compiled["beats"], protagonist_source=protagonist_source,
            character_sources=characters,
            keyframe_sources={
                sid: {"local_path": frames[sid]["path"], "sha256": frames[sid]["sha256"]}
                for sid in chapter["shot_ids"]
            },
        )
        compiled["omni_reference"] = blueprint
        compiled["reference_bindings"] = blueprint["reference_bindings"]
        json_path = out_dir / f"{chapter['id']}.json"
        text_path = out_dir / f"{chapter['id']}.txt"
        updates[json_path] = json.dumps(compiled, ensure_ascii=False, indent=2) + "\n"
        updates[text_path] = compiled["prompt"]
        chapter["chapter_prompt"] = {
            "path": str(text_path.relative_to(project)).replace("\\", "/"),
            "sha256": compiled["prompt_sha256"],
            "compiler": "zy_scene_master_to_model_neutral_chapter_v1",
        }
        outputs.append(str(text_path))
    updates[plan_path] = json.dumps(plan, ensure_ascii=False, indent=2) + "\n"

    prototype = plan.get("production_policy", {}).get("prototype_chapter")
    manifest = {
        "status": "awaiting_user_backend",
        "submission_ready": False,
        "backend": None,
        "required_reference_mode": "omni_reference",
        "backend_selection_policy": {
            "provider_is_user_selected": True,
            "model_must_support_multimodal_omni_reference": True,
            "fallback_to_non_omni_model": False,
        },
        "prototype_chapter": prototype,
        "chapter_packages": outputs,
        "required_user_decisions": [
            "backend_name_and_access", "verified_omni_reference_schema", "duration_and_resolution_tiers",
            "audio_capability_and_binding", "concurrency", "cash_limit_cny",
        ],
        "warning": "核心流程不提交生成任务；用户选定后端并核验真实契约后，才由外部适配层编译和提交代表样片",
    }
    manifest_path = project / "state" / "generation_manifest.json"
    updates[manifest_path] = json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    for path in updates:
        expected[path] = fingerprint(path) if path.is_file() else None
    atomic_batch(updates, expected=expected)
    return {"chapters": len(outputs), "outputs": outputs,
            "generation_manifest": str(manifest_path), "submission_ready": False}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("project_dir")
    args = parser.parse_args()
    print(json.dumps(build(Path(args.project_dir).resolve()), ensure_ascii=False))


if __name__ == "__main__":
    main()
