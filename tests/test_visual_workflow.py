import copy
import base64
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from scripts.storyboard_build import build, build_visual_shots, REQUIRED_STORY_FIELDS
from scripts.visual_development import validate, fingerprint, VENDOR
from scripts.visual_assets import register as register_pending, approve_static, require_keyframe
from scripts.prompt_build import compile_director_method
from scripts.chapter_prompt_build import build as build_chapters
from scripts.edit_assemble import compile_selection
from scripts.edit_assemble import build as render_edit
from scripts.visual_revision import check_remix, check_repair
from scripts.director_review_gate import initial_review, validate_review
from scripts.crew_pipeline import validate_crew_pipeline, validate_sequence_language
from scripts.ledger import record
from scripts.film_grammar_corpus import build_profile, audit_storyboard, audit_binding, fingerprint

ROOT = Path(__file__).resolve().parents[1]
PNG_BYTES = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a9WQAAAAASUVORK5CYII=")


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def register(*args, **kwargs):
    """隔离测试显式静帧审批；不是把真实登记自动当成观察。"""
    value = register_pending(*args, **kwargs)
    return approve_static(args[0], args[1], observed_sha256=value['sha256'],
                          observer='isolated-test-fixture', evidence=args[5])


def crew_bible_fixture():
    departments = {
        "screenwriting": "directing", "directing": "blocking",
        "production_design": "blocking", "character_design": "blocking",
        "blocking": "cinematography", "lighting": "cinematography",
    }
    handoffs = [
        {"from": source, "to": target, "deliverable": "结构化交接物",
         "acceptance": "目标部门逐项确认"} for source, target in departments.items()
    ] + [
        {"from": "production_design", "to": "cinematography", "deliverable": "空间与材质",
         "acceptance": "画面层次可拍"},
        {"from": "cinematography", "to": "editing", "deliverable": "覆盖与轴线",
         "acceptance": "切点可连续"},
        {"from": "directing", "to": "editing", "deliverable": "表演与信息节拍",
         "acceptance": "信息释放成立"},
    ]
    return {
        "schema_version": 2, "handoffs": handoffs,
        "screenwriting": {"dramatic_question": "她是否离开", "conflict_progression": "等待到决定",
                          "reveal_plan": "第二镜揭示决定"},
        "directing": {"scene_objective": "完成选择", "performance_arc": "迟疑到坚定",
                      "staging_principle": "门槛分隔内外",
                      "style_application_order": "after_film_grammar"},
        "cinematography": {"coverage_strategy": "先空间后反应",
                           "coverage_requirements": ["establish", "turn"],
                           "lens_strategy": "宽到近", "axis_policy": "门槛为主轴"},
        "lighting": {"motivated_source_strategy": "门外天光", "contrast_arc": "暗到亮",
                     "exposure_priority": "保护门外高光", "continuity_strategy": "门柱影线不漂移"},
        "production_design": {"set_story": "磨损门槛留下等待痕迹", "depth_layers": "门柱/人物/道路",
                              "material_palette": "旧木灰石白布", "hero_prop_strategy": "信纸推动决定"},
        "character_design": {"silhouette_rule": "长袖与门框分离", "wardrobe_arc": "白衣保持",
                             "wear_continuity": "袖口折痕连续"},
        "blocking": {"geography_rule": "先交代门内外", "movement_axis": "左向右出门",
                     "entrance_exit_logic": "同方向承接"},
        "editing": {"rhythm_map": "两秒建立、两秒决定", "hook_strategy": "开场门槛与信纸",
                    "cut_pattern": "动作到视线匹配", "sound_image_strategy": "重音切画，气口留白",
                    "pacing_policy": {
                        "max_average_shot_sec": 3.0, "slow_shot_threshold_sec": 2.5,
                        "max_consecutive_slow_shots": 2, "max_consecutive_same_rhythm": 2,
                        "hook_deadline_sec": 2.0, "max_hook_gap_sec": 4.0,
                        "max_visual_stasis_sec": 3.0, "min_shot_size_variety_per_scene": 2,
                    }},
    }


def shot_crew_fixture(index):
    first = index == 0
    return {
        "screenwriting": {"objective": "辨认信纸" if first else "作出决定", "obstacle": "犹豫",
                          "turn": "看见旧字" if first else "望向门外", "audience_gain": "理解她要离开"},
        "directing": {"performance_beat": "停顿" if first else "抬眼", "subtext": "舍不得",
                      "staging_intent": "手与门形成因果"},
        "cinematography": {
            "viewpoint_id": "table_side" if first else "door_reverse", "shot_size": "全景" if first else "近景",
            "camera_position": "桌侧" if first else "门外", "camera_height": "坐姿眼高",
            "camera_side": "left", "angle_deg": 0 if first else 35,
            "lens_behavior": "spatial_open" if first else "neutral",
            "camera_movement": "locked", "movement_motivation": "等待动作完成",
            "axis_id": "door_axis", "focal_length_mm": 35 if first else 65,
            "position_xy": [0, 1],
            "focus_target": "信纸" if first else "双眼", "subject_placement": "右三分位" if first else "左三分位",
            "headroom": 0.12, "look_room": 0.42, "depth_strategy": "门柱前景与道路后景",
            "frame_purpose": "交代空间" if first else "读取决定",
        },
        "lighting": {"source_used": "门外天光", "exposure_target": "保护窗外",
                     "face_priority": "允许半脸入暗", "shadow_behavior": "门柱形成遮挡",
                     "key_direction": "画面右后侧", "key_quality": "大面积柔光",
                     "fill_strategy": "室内负补光", "practicals": "桌面油灯仅作背景亮点",
                     "color_temp_k": 5600, "continuity_anchor": "鼻影与门柱影同向"},
        "production_design": {"foreground_story": "门柱", "midground_story": "人物与信纸",
                              "background_story": "道路", "hero_prop_state": "信纸展开" if first else "信纸入袖",
                              "visual_hook": "风掀纸角" if first else "门外冷光切入眼神",
                              "set_dressing": "桌角磨损与落灰", "material_response": "白布柔反射、旧木吸光",
                              "color_accent": "信纸暖黄", "atmospheric_layer": "门外薄尘",
                              "continuity_anchor": "信纸折痕朝上"},
        "character_design": {"silhouette_state": "长袖落在桌沿", "wardrobe_state": "白衣完整",
                             "grooming_state": "发簪与耳饰连续"},
        "blocking": {"start_mark": "桌内侧", "end_mark": "门槛前" if not first else "桌内侧",
                     "entry_direction": "none" if first else "left_to_right",
                     "exit_direction": "left_to_right", "gaze_target": "信纸" if first else "门外",
                     "screen_direction": "left_to_right", "body_orientation": "朝门三分之二侧身",
                     "entry_edge": "none" if first else "left", "exit_edge": "right",
                     "action_path": "桌内侧到门槛直线", "eyeline_vector": "右上方门外",
                     "crossing_rule": "不越过门槛轴"},
        "editing": {"editorial_role": "建立空间" if first else "揭示决定",
                    "incoming_relation": "song_start" if first else "动作匹配",
                    "outgoing_relation": "动作匹配" if first else "chapter_end",
                    "rhythm_class": "setup" if first else "turn", "music_cue": "song:start" if first else "cue:accent",
                    "novelty_axis": "prop" if first else "viewpoint",
                    "visual_change": "纸角被风掀起" if first else "视线转向门外",
                    "visual_change_code": "prop_state" if first else "gaze_shift",
                    "geography_status": "establish" if first else "established_from_previous",
                    "hook": first, "coverage_role": "establish" if first else "turn",
                    "transition_type": "hard_cut", "cut_motivation": "动作完成" if first else "音乐重音",
                    "audio_transition": "music_hit", "duration_intent": "留够动作与一次呼吸"},
    }



def write_sequence_stub(project, shots=None):
    """写入可过 sequence_direction.require 的最小导演序列，并回写分镜执行事实。"""
    project = Path(project).resolve()
    ref = project / "state" / "sequence_ref.txt"
    ref.parent.mkdir(parents=True, exist_ok=True)
    ref.write_text("fixture-sequence-ref", encoding="utf-8")
    from scripts.pipeline_contracts import digest
    sb_path = project / "state/storyboard.json"
    storyboard = None
    if sb_path.is_file():
        import json
        storyboard = json.loads(sb_path.read_text(encoding="utf-8"))
        shots = storyboard.get("shots") or shots
    plan_shots = []
    for shot in shots or []:
        window = shot.get("window") or {}
        start = float(window.get("start", 0) if isinstance(window, dict) else (window[0] if window else 0))
        end = float(window.get("end", start + 1) if isinstance(window, dict) else (window[1] if window else start + 1))
        cam = ((shot.get("crew") or {}).get("cinematography") or {})
        xy = cam.get("position_xy") or [0.0, 0.0]
        action = shot.get("current_action") or "保持动作"
        designed = {
            "id": shot["id"],
            "window": [start, end],
            "action": action,
            "audience_gain": "推进叙事",
            "camera_reason": "覆盖信息",
            "cut_reason": "节奏切点",
            "lighting": "主光稳定",
            "art": "空间层次保留",
            "performance": "表演节拍",
            "camera_xyz": [float(xy[0]), float(xy[1]), 0.0],
            "actor_start_xy": [0.0, 0.0],
            "actor_end_xy": [1.0, 0.0],
            "shot_size": cam.get("shot_size") or "中景",
            "reference_ids": ["ref1"],
        }
        plan_shots.append(designed)
        shot["current_action"] = action
        shot["sequence_direction"] = designed
        if cam.get("position_xy") is None:
            cam["position_xy"] = [float(xy[0]), float(xy[1])]
            (shot.setdefault("crew", {})).setdefault("cinematography", {}).update(cam)
        if not cam.get("shot_size"):
            (shot.setdefault("crew", {})).setdefault("cinematography", {})["shot_size"] = designed["shot_size"]
    plan = {
        "sources": {
            "ref1": {
                "path": str(ref.resolve()),
                "sha256": digest(ref),
                "scope": "annotation_only",
                "media_observed": False,
            }
        },
        "shots": plan_shots,
        "duration_sec": float(plan_shots[-1]["window"][1]) if plan_shots else 0.0,
        "artistic_approval": False,
    }
    write(project / "state/sequence_direction.json", plan)
    if storyboard is not None:
        write(sb_path, storyboard)
    return plan



def write_grammar_profile(project, *, profile_id="drama-fixture"):
    """建议性画像夹具；不能模拟可信生产认证。"""
    project = Path(project)
    path = project / "state/reference_profiles" / f"{profile_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    profile = {
        "schema_version": 1, "profile_id": profile_id,
        "status": "exploratory_only",
        "scene_mean_duration_sec": {"p10": 0.5, "p90": 8.0},
        "scene_kind_envelopes": {
            "narrative": {"p10": 0.5, "p90": 8.0},
            "performance": {"p10": 0.5, "p90": 8.0},
        },
        "shot_size_transitions": {"全景": {"近景": 1}, "近景": {"全景": 1}},
    }
    write(path, profile)
    from scripts.film_grammar_corpus import fingerprint
    return {
        "profile_path": f"state/reference_profiles/{profile_id}.json",
        "profile_sha256": fingerprint(path),
        "profile_id": profile_id,
        "mode": "advisory",
        "rationale": "测试夹具语料", "use_scope": ["editing"],
    }


def fixture(project):
    timeline = {"duration_sec": 4.0, "source_audio": "input/song.wav",
                "production_timing_verified": True, "pending_review": [], "lines": [
        {"index": 1, "type": "vocal", "text": "第一句", "start": 0.0, "end": 3.0},
        {"index": 2, "type": "vocal", "text": "第二句", "start": 3.0, "end": 4.0},
    ]}
    music = {"tempo_class": "medium", "cues": [
        {"id": "downbeat", "time_sec": 0.0, "kind": "downbeat", "evidence": "开场起拍"},
        {"id": "accent", "time_sec": 2.0, "kind": "accent", "evidence": "测试音乐重音"},
        {"id": "mid", "time_sec": 3.5, "kind": "accent", "evidence": "中段切点"},
    ]}
    edit = {
        "timing_status": "director_approved_final_cut",
        "director_cut_approved": True,
        "performance_policy": {"max_edit_shot_duration_sec": 3.0},
        "shots": [
        {"id": "SH001", "scene_id": "gate", "start_ref": "song:start", "end_ref": "cue:accent",
         "shot_type": "全景", "lip_sync": False, "cut_reason": "重音时人物停下",
         "cut_relation": "动作匹配", "editorial_role": "建立门内外空间"},
        {"id": "SH002", "scene_id": "gate", "start_ref": "cue:accent", "end_ref": "song:end",
         "shot_type": "近景", "lip_sync": True, "cut_reason": "人物完成决定",
         "editorial_role": "交代人物决定"},
    ]}
    bible = {key: "保持已确认的事实" for key in
             ("character_lock", "wardrobe_lock", "prop_lock", "location_topology", "lighting_bible")}
    stories = []
    for index in range(2):
        story = {key: "人物握住信纸，随后将信收入袖内" for key in REQUIRED_STORY_FIELDS}
        story.update(id=f"SH00{index + 1}", scene_id="gate", source_beat="B1",
                     state_before={"phase": index}, state_after={"phase": index + 1},
                     motion_design={"start_state": "手持信纸", "action": "收信",
                                    "physics": "接触后收拢", "camera_motion": "固定",
                                    "end_state": "信入袖内"}, crew=shot_crew_fixture(index))
        stories.append(story)
    development = {
        "creative_workflow_revision": 2,
        "treatment": {"concept": "等待后出关", "form": "叙事与演唱",
                      "emotional_arc": "犹豫到决定", "visual_motif": "门槛",
                      "climax": "跨门槛", "performance_rationale": "决定时演唱", "status": "proposed"},
        "sources": [{"id": "song", "locator": "input/lyrics.txt", "read_scope": "全部两行"}],
        "source_ledger": [{"id": "B1", "kind": "proposal", "source_id": "song",
                           "description": "把等待解释为出关前停顿", "status": "proposed"}],
        "scenes": [{"id": "gate", "source_beat": "B1",
                    "topology": {
                        "description": "门内桌区连接门外道路",
                        "nodes": [
                            {"id": "桌内侧", "kind": "mark", "axis_id": "door_axis"},
                            {"id": "门槛前", "kind": "entrance", "axis_id": "door_axis"},
                            {"id": "门柱", "kind": "barrier", "axis_id": "door_axis"},
                        ],
                    },
                    "kind": "narrative", "coverage_requirements": ["establish", "turn"],
                    "axes": {"door_axis": {"start": [0, 0], "end": [1, 0]}},
                    "light_map": "门外天光", "material_rules": "布与石分别受光", "master_prompt": "可通行的城门",
                    "art_direction": {"foreground": "门柱", "midground": "人物与桌",
                                      "background": "道路", "scenic_action": "风掀纸角",
                                      "hero_prop": "旧信纸", "negative_space": "门外留白",
                                      "material_contrast": "旧木、灰石、白布", "set_history": "多年候关留下磨损",
                                      "color_script": "室内褐灰到门外青白", "scale_cue": "门高三倍人身",
                                      "background_activity": "远处旗角与薄尘缓动"},
                    "lighting_plan": {"primary_source": "门外天光", "secondary_source": "室内弱反射",
                                      "shadow_zones": "门柱背面", "exposure_priority": "保护窗外",
                                      "color_temperature_relation": "门外冷、室内中性",
                                      "cue_changes": "决定时人物进入冷光", "key_direction": "右后侧",
                                      "key_quality": "柔而有方向", "fill_strategy": "室内负补光",
                                      "practicals": "油灯压低不抢脸",
                                      "continuity_anchor": "门柱投影位置固定"}}],
        "references": [], "visual_rules": [],
        "crew_bible": crew_bible_fixture(),
    }
    style = {"style_name": "写实", "fingerprint": "sha256:style", "aspect_ratio": "16:9"}
    for name, data in (("timeline", timeline), ("music_structure", music), ("visual_edit_plan", edit),
                       ("development", development), ("style", style),
                       ("story_plan", {"method": "zy_story_visual_development",
                                       "continuity_bible": bible, "shots": stories})):
        write(project / "state" / f"{name}.json", data)
    write_sequence_stub(project, stories)
    binding = write_grammar_profile(project)
    development["grammar_reference"] = binding
    write(project / "state/development.json", development)
    return timeline, music, edit, development, style


class VisualWorkflowTests(unittest.TestCase):
    def test_crew_handoff_and_physical_camera_values_are_hard_gates(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            project = Path(tmp)
            _, _, _, development, _ = fixture(project)
            storyboard = build(project)
            development["crew_bible"]["handoffs"] = [
                item for item in development["crew_bible"]["handoffs"]
                if not (item["from"] == "lighting" and item["to"] == "cinematography")
            ]
            storyboard["shots"][0]["crew"]["cinematography"]["headroom"] = 1.4
            storyboard["shots"][0]["crew"]["lighting"]["color_temp_k"] = 25000
            errors = validate_crew_pipeline(development, storyboard, project=project)
            self.assertTrue(any("lighting->cinematography" in error for error in errors))
            self.assertTrue(any("headroom" in error for error in errors))
            self.assertTrue(any("color_temp_k" in error for error in errors))

    def _music_ctx(self):
        return {
            "tempo_class": "medium",
            "cues": [
                {"id": "downbeat", "time_sec": 0.0, "kind": "downbeat", "evidence": "起拍"},
                {"id": "accent", "time_sec": 2.0, "kind": "accent", "evidence": "重音"},
                {"id": "mid", "time_sec": 4.0, "kind": "accent", "evidence": "中段"},
                {"id": "late", "time_sec": 6.0, "kind": "accent", "evidence": "后段"},
            ],
        }, {"duration_sec": 8.0, "lines": [
            {"index": 1, "start": 0.0, "end": 3.0, "text": "一"},
            {"index": 2, "start": 3.0, "end": 8.0, "text": "二"},
        ]}, 24.0

    def test_attention_refresh_and_coverage_roles_are_enforced(self):
        editing = crew_bible_fixture()["editing"]
        # 保持不放宽默认 pacing；用短镜与足够 hook 专门测覆盖角色与视觉停滞
        editing["pacing_policy"].update(
            max_average_shot_sec=3.0, slow_shot_threshold_sec=4.0,
            max_hook_gap_sec=3.0, max_visual_stasis_sec=3.0,
            min_shot_size_variety_per_scene=2, hook_deadline_sec=2.0,
        )
        music, timeline, fps = self._music_ctx()
        cues = ["downbeat", "accent", "mid"]
        shots = []
        for index in range(3):
            crew = shot_crew_fixture(0 if index == 0 else 1)
            crew["editing"].update(
                hook=index == 0, visual_change="人物仍坐在桌前", visual_change_code="pose_shift",
                novelty_axis="performance", coverage_role="develop",
                geography_status="establish" if index == 0 else "established_from_previous",
                music_cue=cues[index],
                incoming_relation="song_start" if index == 0 else "cut",
                outgoing_relation="cut" if index < 2 else "chapter_end",
            )
            crew["cinematography"].update(
                shot_size="medium" if index < 2 else "close",
                viewpoint_id=f"view-{index}",
                angle_deg=index * 40,
                position_xy=[index * 0.3, 0.5],
            )
            shots.append({"id": f"SH00{index + 1}", "scene_id": "gate",
                          "window": {"start": index * 2.0, "end": (index + 1) * 2.0},
                          "crew": crew})
        scenes = [{"id": "gate", "kind": "narrative"}]
        errors = validate_sequence_language(
            shots, editing, music_structure=music, timeline=timeline, fps=fps, scenes=scenes,
        )
        self.assertTrue(any("钩子间隔" in error for error in errors), errors)
        self.assertTrue(any("相同注意力指纹持续" in error or "相同视觉事件持续" in error for error in errors), errors)
        self.assertTrue(any("reaction/detail" in error for error in errors), errors)
        self.assertTrue(any("turn/payoff" in error for error in errors), errors)


    def test_blocking_screen_eyeline_and_static_handoff_are_enforced(self):
        editing = crew_bible_fixture()["editing"]
        music, timeline, fps = self._music_ctx()
        shots = []
        for index in range(2):
            crew = shot_crew_fixture(index)
            crew["editing"].update(
                hook=index == 0, music_cue=("downbeat", "accent")[index],
                geography_status="establish" if index == 0 else "established_from_previous",
                coverage_role=("establish", "turn")[index],
                time_relation="continuous",
                incoming_relation="song_start" if index == 0 else "cut",
                outgoing_relation="cut" if index == 0 else "chapter_end",
            )
            crew["cinematography"].update(angle_deg=0 if index == 0 else 40, position_xy=[0, 1])
            crew["blocking"].update(
                entry_direction="left_to_right", exit_direction="left_to_right",
                screen_direction="left_to_right" if index == 0 else "right_to_left",
                eyeline_vector="右上方门外" if index == 0 else "左下方室内",
                start_mark="桌内侧", end_mark="门槛前",
                crossing_rule="不越过门槛轴",
            )
            shots.append({"id": f"SH00{index + 1}", "scene_id": "gate",
                          "window": {"start": index * 2.0, "end": (index + 1) * 2.0},
                          "crew": crew})
        errors = validate_sequence_language(
            shots, editing, music_structure=music, timeline=timeline, fps=fps,
            scenes=[{"id": "gate", "kind": "narrative"}],
        )
        self.assertTrue(any("screen_direction" in e and "反向" in e for e in errors), errors)
        self.assertTrue(any("eyeline_vector" in e and "反向" in e for e in errors), errors)

        # static 口型段：缺 crossing_rule / 站位不承接
        for shot in shots:
            shot["crew"]["blocking"].update(
                entry_direction="static", exit_direction="static",
                screen_direction="left_to_right", eyeline_vector="右上方门外",
                crossing_rule="", start_mark="A点", end_mark="B点",
            )
        shots[0]["crew"]["blocking"]["end_mark"] = "旧站位"
        shots[1]["crew"]["blocking"]["start_mark"] = "新站位"
        errors = validate_sequence_language(
            shots, editing, music_structure=music, timeline=timeline, fps=fps,
            scenes=[{"id": "gate", "kind": "narrative"}],
        )
        self.assertTrue(any("crossing_rule" in e for e in errors), errors)
        self.assertTrue(any("站位未承接" in e for e in errors), errors)

        # 正例：同向 + static 承接
        for shot in shots:
            shot["crew"]["blocking"].update(
                entry_direction="static", exit_direction="static",
                screen_direction="left_to_right", eyeline_vector="右上方门外",
                crossing_rule="口型段不越轴，脚位锁在桌内侧",
                start_mark="桌内侧", end_mark="桌内侧",
            )
        errors = validate_sequence_language(
            shots, editing, music_structure=music, timeline=timeline, fps=fps,
            scenes=[{"id": "gate", "kind": "narrative"}],
        )
        self.assertFalse(any("screen_direction" in e and "反向" in e for e in errors), errors)
        self.assertFalse(any("站位未承接" in e for e in errors), errors)
        self.assertFalse(any("crossing_rule" in e for e in errors), errors)


    def test_noncontinuous_time_relation_requires_evidence(self):
        editing = crew_bible_fixture()["editing"]
        music, timeline, fps = self._music_ctx()
        shots = []
        for index in range(2):
            crew = shot_crew_fixture(index)
            crew["editing"].update(
                hook=index == 0, music_cue=("downbeat", "accent")[index],
                geography_status="establish" if index == 0 else "established_from_previous",
                coverage_role=("establish", "turn")[index],
                time_relation="ellipsis" if index == 1 else "continuous",
                incoming_relation="song_start" if index == 0 else "cut",
                outgoing_relation="cut" if index == 0 else "chapter_end",
            )
            crew["cinematography"].update(angle_deg=0 if index == 0 else 45, position_xy=[0, 1])
            shots.append({"id": f"SH00{index + 1}", "scene_id": "gate",
                          "window": {"start": index * 2.0, "end": (index + 1) * 2.0},
                          "crew": crew})
        errors = validate_sequence_language(
            shots, editing, music_structure=music, timeline=timeline, fps=fps,
            scenes=[{"id": "gate", "kind": "narrative"}],
        )
        self.assertTrue(any("time_relation_evidence" in e for e in errors), errors)
        shots[1]["crew"]["editing"]["time_relation_evidence"] = "回忆插入，动作不承接"
        errors = validate_sequence_language(
            shots, editing, music_structure=music, timeline=timeline, fps=fps,
            scenes=[{"id": "gate", "kind": "narrative"}],
        )
        self.assertFalse(any("time_relation_evidence" in e for e in errors), errors)

    def test_direction_reverse_requires_evidence_not_bare_bool(self):
        editing = crew_bible_fixture()["editing"]
        music, timeline, fps = self._music_ctx()
        shots = []
        for index in range(2):
            crew = shot_crew_fixture(index)
            crew["editing"].update(
                hook=index == 0, music_cue=("downbeat", "accent")[index],
                geography_status="establish" if index == 0 else "established_from_previous",
                coverage_role=("establish", "turn")[index],
                time_relation="continuous",
                incoming_relation="song_start" if index == 0 else "cut",
                outgoing_relation="cut" if index == 0 else "chapter_end",
            )
            crew["cinematography"].update(angle_deg=0 if index == 0 else 40, position_xy=[0, 1])
            crew["blocking"].update(
                entry_direction="left_to_right", exit_direction="left_to_right",
                screen_direction="left_to_right" if index == 0 else "right_to_left",
                eyeline_vector="右上方门外" if index == 0 else "左下方室内",
                start_mark="桌内侧", end_mark="桌内侧",
                crossing_rule="同场连续",
                direction_reverse_justified=True,  # 旧绕过字段，不应再豁免
                eyeline_reverse_justified=True,
            )
            shots.append({"id": f"SH00{index + 1}", "scene_id": "gate",
                          "window": {"start": index * 2.0, "end": (index + 1) * 2.0},
                          "crew": crew})
        errors = validate_sequence_language(
            shots, editing, music_structure=music, timeline=timeline, fps=fps,
            scenes=[{"id": "gate", "kind": "narrative"}],
        )
        self.assertTrue(any("direction_reverse_evidence" in e for e in errors), errors)
        self.assertTrue(any("eyeline_reverse_evidence" in e for e in errors), errors)
        shots[1]["crew"]["blocking"]["direction_reverse_evidence"] = "人物转身回看门内"
        shots[1]["crew"]["blocking"]["eyeline_reverse_evidence"] = "视线落到钥匙托盘"
        errors = validate_sequence_language(
            shots, editing, music_structure=music, timeline=timeline, fps=fps,
            scenes=[{"id": "gate", "kind": "narrative"}],
        )
        self.assertFalse(any("direction_reverse_evidence" in e for e in errors), errors)
        self.assertFalse(any("eyeline_reverse_evidence" in e for e in errors), errors)

    def test_novelty_axis_and_visual_change_code_are_controlled(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            project = Path(tmp)
            _, _, _, development, _ = fixture(project)
            storyboard = build(project)
            storyboard["shots"][0]["crew"]["editing"]["novelty_axis"] = "随便换个说法"
            storyboard["shots"][0]["crew"]["editing"]["visual_change_code"] = "not_a_code"
            storyboard["shots"][0]["crew"]["editing"]["visual_change"] = "电影感再来一次"
            errors = validate_crew_pipeline(development, storyboard, project=project)
            self.assertTrue(any("novelty_axis" in e for e in errors), errors)
            self.assertTrue(any("visual_change_code" in e for e in errors), errors)
            self.assertTrue(any("空泛审美词" in e for e in errors), errors)
            # 只改措辞、code/novelty 不变 → 三连指纹失败
            editing = crew_bible_fixture()["editing"]
            music, timeline, fps = self._music_ctx()
            shots = []
            for index in range(3):
                crew = shot_crew_fixture(0 if index == 0 else 1)
                crew["editing"].update(
                    novelty_axis="prop", visual_change_code="prop_state",
                    visual_change=f"纸角微微动了一下变体{index}",
                    hook=index == 0, coverage_role=("establish", "develop", "reaction")[index],
                    geography_status="establish" if index == 0 else "established_from_previous",
                    music_cue=("downbeat", "accent", "mid")[index],
                    rhythm_class=f"r{index}",
                    incoming_relation="song_start" if index == 0 else "cut",
                    outgoing_relation="cut" if index < 2 else "chapter_end",
                )
                crew["cinematography"].update(
                    angle_deg=index * 40, shot_size=("全景", "近景", "中景")[index],
                    viewpoint_id=f"v{index}", position_xy=[index * 0.2, 0.5],
                )
                shots.append({"id": f"SH00{index + 1}", "scene_id": "gate",
                              "window": {"start": index * 2.0, "end": (index + 1) * 2.0},
                              "crew": crew})
            errors = validate_sequence_language(
                shots, editing, music_structure=music, timeline=timeline, fps=fps,
                scenes=[{"id": "gate", "kind": "narrative"}],
            )
            self.assertTrue(any("指纹相同" in e or "只改措辞" in e for e in errors), errors)

    def test_character_design_rejects_vague_words(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            project = Path(tmp)
            _, _, _, development, _ = fixture(project)
            storyboard = build(project)
            development["crew_bible"]["character_design"]["silhouette_rule"] = "唯美高级感剪影"
            storyboard["shots"][0]["crew"]["character_design"]["wardrobe_state"] = "大片氛围感白衣"
            errors = validate_crew_pipeline(development, storyboard, project=project)
            self.assertTrue(any("character_design" in e and "空泛审美词" in e for e in errors), errors)

    def test_batch_ledger_requires_review_approvals(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            project = Path(tmp)
            ledger = project / "ledger.jsonl"
            event = {
                "event_id": "E-batch-1", "task_id": "T-batch-1", "action": "reserve",
                "cash_cny": "1.0", "coins": "0", "duration_sec": "4",
                "batch": True, "submission_phase": "batch", "project_dir": str(project),
            }
            with self.assertRaisesRegex(ValueError, "剪辑预演未批准|project_dir"):
                record(ledger, event, "10")

    def test_same_angle_with_shot_size_cycle_is_blocked(self):
        """同 angle_deg=0、同侧、景别 medium/close/medium 必须失败（P0）。"""
        editing = crew_bible_fixture()["editing"]
        music, timeline, fps = self._music_ctx()
        sizes = ("medium", "close", "medium")
        cues = ("downbeat", "accent", "mid")
        shots = []
        for index, size in enumerate(sizes):
            crew = shot_crew_fixture(0)
            crew["cinematography"].update(
                viewpoint_id="front_table", shot_size=size, camera_side="left",
                angle_deg=0, axis_id="door_axis", position_xy=[0.2, 0.5],
                camera_height="eye", lens_behavior="prime",
            )
            crew["editing"].update(
                hook=index == 0, coverage_role=("establish", "develop", "reaction")[index],
                geography_status="establish" if index == 0 else "established_from_previous",
                music_cue=cues[index], visual_change=f"变化{index}", visual_change_code="performance_beat",
                rhythm_class=f"r{index}",
                incoming_relation="song_start" if index == 0 else "cut",
                outgoing_relation="cut" if index < 2 else "chapter_end",
            )
            shots.append({"id": f"SH00{index + 1}", "scene_id": "gate",
                          "window": {"start": index * 2.0, "end": (index + 1) * 2.0},
                          "crew": crew})
        errors = validate_sequence_language(
            shots, editing, music_structure=music, timeline=timeline, fps=fps,
            scenes=[{"id": "gate", "kind": "narrative"}],
        )
        self.assertTrue(any("30 度" in e or "景别变化不能豁免" in e for e in errors), errors)
        self.assertTrue(any("近同角度连续" in e for e in errors), errors)

    def test_music_cue_must_align_to_structure_anchors(self):
        editing = crew_bible_fixture()["editing"]
        music, timeline, fps = self._music_ctx()
        shots = []
        for index in range(2):
            crew = shot_crew_fixture(index)
            crew["editing"]["music_cue"] = "起拍"  # 非锚点自由文本
            shots.append({"id": f"SH00{index + 1}", "scene_id": "gate",
                          "window": {"start": index * 2.0, "end": (index + 1) * 2.0},
                          "crew": crew})
        errors = validate_sequence_language(
            shots, editing, music_structure=music, timeline=timeline, fps=fps,
        )
        self.assertTrue(any("music_cue" in e and "未引用" in e for e in errors), errors)
        errors_missing = validate_sequence_language(shots, editing)
        self.assertTrue(any("缺少 music_structure" in e for e in errors_missing), errors_missing)

    def test_loose_pacing_without_exception_is_blocked(self):
        editing = crew_bible_fixture()["editing"]
        editing["pacing_policy"].update(
            max_average_shot_sec=15.0, slow_shot_threshold_sec=20.0,
            max_consecutive_slow_shots=10, max_hook_gap_sec=40.0,
            max_visual_stasis_sec=40.0,
        )
        music = {"tempo_class": "medium", "cues": [
            {"id": f"c{i}", "time_sec": float(i * 6), "kind": "accent", "evidence": f"切点{i}"}
            for i in range(4)
        ]}
        timeline = {"duration_sec": 24.0, "lines": []}
        fps = 24.0
        roles = ("establish", "develop", "reaction", "turn")
        shots = []
        for index in range(4):
            crew = shot_crew_fixture(0 if index == 0 else 1)
            crew["cinematography"].update(
                angle_deg=index * 40, viewpoint_id=f"v{index}",
                shot_size=("wide", "medium", "close", "wide")[index], position_xy=[index, 0.0],
            )
            crew["editing"].update(
                hook=index == 0, music_cue=f"c{index}",
                coverage_role=roles[index],
                visual_change=f"v{index}", visual_change_code="reveal", rhythm_class=f"r{index}",
                geography_status="establish" if index == 0 else "established_from_previous",
                incoming_relation="song_start" if index == 0 else "cut",
                outgoing_relation="cut" if index < 3 else "chapter_end",
            )
            shots.append({"id": f"SH00{index + 1}", "scene_id": "gate",
                          "window": {"start": index * 6.0, "end": (index + 1) * 6.0},
                          "crew": crew})
        errors = validate_sequence_language(
            shots, editing, music_structure=music, timeline=timeline, fps=fps,
            scenes=[{"id": "gate", "kind": "narrative"}],
        )
        self.assertTrue(any("超过曲速默认" in e for e in errors), errors)
        # 无例外时即使策略写了 15s，生效上限仍是默认 3.5，平均 6s 也会被挡
        self.assertTrue(any("平均镜头时长" in e for e in errors), errors)
        editing["pacing_exception"] = {
            "reason": "慢板叙事长镜", "evidence": "副歌段 24s 内信息密度低，导演批准",
        }
        errors_ok_policy = validate_sequence_language(
            shots, editing, music_structure=music, timeline=timeline, fps=fps,
            scenes=[{"id": "gate", "kind": "narrative"}],
        )
        self.assertFalse(any("超过曲速默认" in e for e in errors_ok_policy), errors_ok_policy)
        self.assertFalse(any("平均镜头时长" in e for e in errors_ok_policy), errors_ok_policy)

    def test_film_grammar_profile_is_anonymous_and_requires_enough_films_for_enforcement(self):
        records = []
        for film_index in range(2):
            for shot_index, (duration, size, movement) in enumerate((
                (1.0, "wide", "locked"), (2.0, "medium", "dolly"),
                (3.0, "close", "locked"),
            )):
                records.append({
                    "source_id": "test", "film_id": f"F{film_index}", "scene_id": "S1",
                    "shot_index": shot_index,
                    "duration_sec": duration, "shot_size": size,
                    "camera_movement": movement, "rating": 8.2,
                })
        manifest = {
            "schema_version": 1, "media_policy": "annotations_only",
            "sources": [{"id": "test", "title": "测试标注", "url": "https://example.test",
                         "license_or_terms": "test-only", "access_scope": "annotations"}],
        }
        profile = build_profile(records, manifest, profile_id="high-score-drama",
                                min_rating=8.0, min_films=3)
        self.assertEqual(profile["status"], "exploratory_only")
        self.assertFalse(profile["provenance"]["contains_frames"])
        shots = []
        for index, duration in enumerate((6.0, 6.0)):
            crew = shot_crew_fixture(index)
            crew["cinematography"]["shot_size"] = "wide" if index == 0 else "close"
            shots.append({"id": f"SH00{index + 1}", "scene_id": "gate",
                          "window": {"start": index * duration, "end": (index + 1) * duration},
                          "crew": crew})
        advisory = audit_storyboard(profile, {"shots": shots}, mode="advisory")
        self.assertFalse(advisory["errors"])
        self.assertTrue(any("平均镜长" in warning for warning in advisory["warnings"]))
        enforced = audit_storyboard(profile, {"shots": shots}, mode="enforce_envelope")
        self.assertTrue(any("缺少独立签名验收" in error for error in enforced["errors"]))
        self.assertTrue(any("平均镜长" in error for error in enforced["errors"]))
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            project = Path(tmp)
            profile_path = project / "state/reference_profiles/high-score-drama.json"
            write(profile_path, profile)
            development = {"grammar_reference": {
                "profile_path": "state/reference_profiles/high-score-drama.json",
                "profile_sha256": fingerprint(profile_path), "profile_id": "high-score-drama",
                "mode": "advisory", "rationale": "匹配剧情类 MV", "use_scope": ["editing"],
            }}
            denied = audit_binding(development, {"shots": shots}, project)
            self.assertFalse(denied["errors"], denied)
            development["grammar_reference"]["mode"] = "enforce_envelope"
            blocked = audit_binding(development, {"shots": shots}, project)
            self.assertTrue(any("缺少独立签名验收" in e for e in blocked["errors"]), blocked)
            missing = audit_binding({}, {"shots": shots}, project)
            self.assertIsNone(missing)
            profile_path.write_text("{}", encoding="utf-8")
            changed = audit_binding(development, {"shots": shots}, project)
            self.assertTrue(any("指纹" in error for error in changed["errors"]))

    def test_crew_pipeline_blocks_three_repeated_angles_and_broken_blocking(self):
        editing = crew_bible_fixture()["editing"]
        music, timeline, fps = self._music_ctx()
        shots = []
        for index in range(3):
            crew = shot_crew_fixture(0)
            crew["editing"].update(
                hook=index == 0,
                geography_status="establish" if index == 0 else "established_from_previous",
                incoming_relation="song_start" if index == 0 else "same_angle_cut",
                outgoing_relation="same_angle_cut" if index < 2 else "chapter_end",
                rhythm_class="same",
                visual_change="人物仍坐在桌前",
                music_cue=("downbeat", "accent", "mid")[index],
                coverage_role=("establish", "develop", "reaction")[index],
            )
            crew["blocking"]["entry_direction"] = "right_to_left" if index == 1 else "left_to_right"
            shots.append({"id": f"SH00{index + 1}", "scene_id": "gate",
                          "window": {"start": index * 2.0, "end": (index + 1) * 2.0},
                          "crew": crew})
        errors = validate_sequence_language(
            shots, editing, music_structure=music, timeline=timeline, fps=fps,
            scenes=[{"id": "gate", "kind": "narrative"}],
        )
        self.assertTrue(any("连续三个同角度" in error for error in errors), errors)
        self.assertTrue(any("人物动线跳变" in error for error in errors), errors)
        self.assertTrue(any("30 度" in error or "景别变化不能豁免" in error for error in errors), errors)
        self.assertTrue(any("没有新的视觉变化" in error or "指纹相同" in error or "只改措辞" in error for error in errors), errors)

    def test_director_style_cannot_precede_film_grammar(self):
        development = {"crew_bible": crew_bible_fixture(), "scenes": [{
            "id": "gate",
            "art_direction": {key: "已设计" for key in (
                "foreground", "midground", "background", "scenic_action", "hero_prop",
                "negative_space", "material_contrast")},
            "lighting_plan": {key: "已设计" for key in (
                "primary_source", "secondary_source", "shadow_zones", "exposure_priority",
                "color_temperature_relation", "cue_changes")},
        }]}
        shot = {"id": "SH001", "scene_id": "gate", "window": {"start": 0.0, "end": 2.0},
                "crew": shot_crew_fixture(0)}
        development["crew_bible"]["directing"]["style_application_order"] = "style_first"
        music = {"tempo_class": "medium", "cues": [
            {"id": "downbeat", "time_sec": 0.0, "kind": "downbeat", "evidence": "起拍"}]}
        shot["crew"]["editing"]["music_cue"] = "downbeat"
        errors = validate_crew_pipeline(
            development, {"shots": [shot]}, music_structure=music,
            timeline={"duration_sec": 2.0, "lines": []},
        )
        self.assertTrue(any("导演风格只能" in error for error in errors), errors)

    def test_narrative_sample_can_mark_lipsync_not_applicable_but_not_other_checks(self):
        plan = {"production_policy": {"prototype_chapter": "CH01"},
                "chapters": [{"id": "CH01", "shot_deltas": [{"lip_sync": False}]}]}
        review = initial_review(plan)
        review["director_checks"]["lip_sync"] = {"status": "not_applicable", "evidence": "本段无演唱，检查自然闭口"}
        errors = validate_review(review, plan)
        self.assertFalse(any("导演维度未通过: lip_sync" == e for e in errors))
        self.assertTrue(any("导演维度未通过: performance" == e for e in errors))
        plan["chapters"][0]["shot_deltas"][0]["lip_sync"] = True
        self.assertTrue(any("导演维度未通过: lip_sync" == e for e in validate_review(review, plan)))
    def test_remix_blocks_identity_and_multiple_axis_changes(self):
        base = {"scene_id": "gate", "character_lock": "same face", "wardrobe_lock": "white",
                "camera_witness": "门内", "light": "天光"}
        candidate = {**base, "camera_witness": "门侧"}
        self.assertEqual(check_remix(base, candidate, "camera"), [])
        candidate["light"] = "烛光"
        self.assertTrue(check_remix(base, candidate, "camera"))
        candidate["character_lock"] = "another face"
        self.assertTrue(any("用户锁" in e for e in check_remix(base, candidate, "camera")))

    def test_repair_requires_actual_drift_and_structural_return(self):
        plan = {"dominant_failures": [{"dimension": "space", "classification": "actual_drift",
                                       "evidence": "门的位置变化", "cause": "空间布局重新设计"}],
                "structural_failure": True, "action": "surgical_repair", "preserve_exactly": ["人物"]}
        self.assertTrue(check_repair(plan))
        plan["action"] = "rebuild_scene_master"
        self.assertEqual(check_repair(plan), [])
        plan["dominant_failures"][0]["classification"] = "valid_adaptation"
        self.assertTrue(check_repair(plan))

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "需要 ffmpeg")
    def test_local_render_cuts_long_material_to_target_frames(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            project = Path(tmp)
            fixture(project)
            sb = build(project)
            source = project / "source.mp4"
            subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                            "color=s=64x64:r=24:d=5", "-c:v", "libx264", str(source)],
                           check=True, capture_output=True)
            write(project / "state/generated_clips.json", {"clips": {"C1": {
                "path": "source.mp4", "sha256": fingerprint(source), "fps": 24,
                "frame_count": 120, "scene_id": "gate", "status": "reviewed", "evidence": "测试素材"}}})
            write(project / "state/edit_selection.json", {"selections": [
                {"shot_id": shot["id"], "clip_id": "C1", "in_frame": 12, "out_frame": 60,
                 "edit_reason": "测试两秒动作", "state_check": "连续"} for shot in sb["shots"]]})
            assembly = render_edit(project, render=True, technical_preview=True)
            self.assertEqual(assembly["status"], "rendered")
            for entry in assembly["entries"]:
                info = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                                       "-count_frames", "-show_entries", "stream=nb_read_frames",
                                       "-of", "csv=p=0", str(project / entry["target_path"])],
                                      check=True, capture_output=True, text=True)
                self.assertEqual(int(info.stdout.strip()), 48)

    def test_visual_cuts_can_split_and_span_lyrics_without_rewriting_audio(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            project = Path(tmp)
            timeline, music, edit, _, _ = fixture(project)
            old = copy.deepcopy(timeline)
            shots = build_visual_shots(timeline, music, edit, 24)
            self.assertEqual(shots[0]["lyric_lines"], [1])
            self.assertEqual(shots[1]["lyric_lines"], [1, 2])
            self.assertEqual(timeline, old)
            self.assertEqual(shots[0]["shot_type"], "全景")

    def test_unknown_music_cut_and_collapsed_frame_are_blocked(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            tl, music, edit, _, _ = fixture(Path(tmp))
            edit["shots"][0]["end_ref"] = "cue:invented"
            with self.assertRaises(ValueError):
                build_visual_shots(tl, music, edit, 24)
            edit["shots"][0]["end_ref"] = "cue:accent"
            next(c for c in music["cues"] if c["id"] == "accent")["time_sec"] = 0.001
            with self.assertRaises(ValueError):
                build_visual_shots(tl, music, edit, 24)

    def test_selected_coverage_requires_an_independent_timed_shot(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            project = Path(tmp)
            fixture(project)
            plan_path = project / "state/story_plan.json"
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            plan["shots"][0]["coverage_plan"] = [{
                "id": "SH001_INSERT_A", "purpose": "保留人物犹豫时的手部证据",
                "keyframe_moment": "指尖停在信封折角", "selection_status": "selected",
            }]
            write(plan_path, plan)
            with self.assertRaisesRegex(ValueError, "独立定时镜头"):
                build(project)

            plan["shots"][0]["coverage_plan"][0]["selection_status"] = "candidate"
            write(plan_path, plan)
            result = build(project)
            self.assertEqual(result["shots"][0]["coverage_plan"][0]["selection_status"],
                             "candidate")

    def test_no_edit_plan_produces_skeleton_without_fake_lipsync(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            project = Path(tmp)
            fixture(project)
            (project / "state" / "visual_edit_plan.json").unlink()
            result = build(project)
            self.assertEqual(result["shots"], [])
            self.assertEqual(result["story_mode"], "timing_only")

    def test_director_name_without_routed_method_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "逐镜路由"):
            compile_director_method({"director_ref": {"name": "张艺谋"}}, {})

    def test_continuity_reset_and_character_style_leak_are_detected(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            project = Path(tmp)
            _, _, _, dev, style = fixture(project)
            sb = build(project)
            self.assertEqual(validate(dev, sb, style, project), [])
            sb["shots"][1]["state_before"] = {"phase": 0}
            dev["references"] = [{"id": "person", "kind": "character",
                                  "roles": ["likeness", "wardrobe", "lighting"], "observed_scope": "图"}]
            errors = validate(dev, sb, style, project)
            self.assertTrue(any("静默重置" in e for e in errors))
            self.assertTrue(any("只能控制" in e for e in errors))

    def test_subtle_director_preserves_inactive_axes_and_requires_real_source(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            project = Path(tmp)
            _, _, _, dev, style = fixture(project)
            sb = build(project)
            style["director_ref"] = {"name": "胡金铨", "intensity": "轻微"}
            dev["director"] = {"name": "胡金铨", "strength": "轻微", "active_axes": ["composition_space"],
                               "active_traits": ["门槛组织空间"], "rationale": "人物决定跨门槛",
                               "source_file": "references/directors/king-hu.md",
                               "source_sha256": fingerprint(VENDOR / "references/directors/king-hu.md"),
                               "crew_constraints": {
                                   "exposure_bias": "protect_highlights",
                                   "camera_height_policy": "grounded_threshold",
                               }}
            for shot in sb["shots"]:
                shot["director_decisions"] = {"composition_space": {
                    "instruction": "门柱、人物与道路层次可读", "reason": "决定是否出关",
                    "observable_check": "观众能读懂门内外关系"}}
                shot["crew"]["lighting"]["exposure_bias"] = "protect_highlights"
                shot["crew"]["cinematography"]["height_strategy"] = "grounded_threshold"
            self.assertEqual(validate(dev, sb, style, project), [])
            style["director_ref"].update(active_axes=["composition_space"], active_traits=["门槛"])
            method = compile_director_method(style, sb["shots"][0])
            self.assertEqual(method["axis_status"]["light_contrast"], "preserved")
            dev["director"]["source_sha256"] = "old"
            self.assertTrue(any("指纹" in e for e in validate(dev, sb, style, project)))

    def test_vague_lighting_and_art_words_are_blocked(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            project = Path(tmp)
            _, _, _, development, _ = fixture(project)
            storyboard = build(project)
            development["scenes"][0]["lighting_plan"]["key_direction"] = "电影感冷暖光"
            development["scenes"][0]["art_direction"]["foreground"] = "唯美大片门柱"
            storyboard["shots"][0]["crew"]["lighting"]["key_quality"] = "高级感氛围光"
            errors = validate_crew_pipeline(development, storyboard, project=project)
            self.assertTrue(any("空泛审美词" in e and "电影感" in e for e in errors), errors)
            self.assertTrue(any("空泛审美词" in e and ("唯美" in e or "大片" in e) for e in errors), errors)
            self.assertTrue(any("key_direction" in e and "受控方位" in e for e in errors), errors)

    def test_structured_topology_cross_refs_axes_and_marks(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            project = Path(tmp)
            _, _, _, development, _ = fixture(project)
            storyboard = build(project)
            self.assertEqual(validate_crew_pipeline(development, storyboard, project=project), [])
            development["scenes"][0]["topology"] = "电影感空间"
            errors = validate_crew_pipeline(development, storyboard, project=project)
            self.assertTrue(any("topology 必须为含 nodes" in e for e in errors), errors)
            self.assertTrue(any("空泛审美词" in e for e in errors), errors)
            development["scenes"][0]["topology"] = {
                "nodes": [
                    {"id": "桌内侧", "kind": "mark", "axis_id": "door_axis"},
                    {"id": "门槛前", "kind": "entrance", "axis_id": "missing_axis"},
                ],
            }
            errors = validate_crew_pipeline(development, storyboard, project=project)
            self.assertTrue(any("未知轴线" in e for e in errors), errors)
            development["scenes"][0]["topology"] = {
                "nodes": [
                    {"id": "桌内侧", "kind": "mark", "axis_id": "door_axis"},
                    {"id": "门槛前", "kind": "entrance", "axis_id": "door_axis"},
                    {"id": "门柱", "kind": "barrier", "axis_id": "door_axis"},
                ],
            }
            storyboard["shots"][1]["crew"]["blocking"]["end_mark"] = "未注册站位"
            errors = validate_crew_pipeline(development, storyboard, project=project)
            self.assertTrue(any("未在场景 gate topology.nodes 注册" in e for e in errors), errors)

    def test_director_requires_grammar_and_crew_constraints(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            project = Path(tmp)
            _, _, _, development, style = fixture(project)
            storyboard = build(project)
            # 无导演仍可出片
            self.assertEqual(validate(development, storyboard, style, project), [])
            style["director_ref"] = {"name": "胡金铨", "intensity": "轻微"}
            development["director"] = {
                "name": "胡金铨", "strength": "轻微", "active_axes": ["composition_space"],
                "active_traits": ["门槛"], "rationale": "跨门槛",
                "source_file": "references/directors/king-hu.md",
                "source_sha256": fingerprint(VENDOR / "references/directors/king-hu.md"),
            }
            for shot in storyboard["shots"]:
                shot["director_decisions"] = {"composition_space": {
                    "instruction": "门槛可读", "reason": "决定", "observable_check": "门内外"}}
            errors = validate(development, storyboard, style, project)
            self.assertTrue(any("crew_constraints" in e for e in errors), errors)
            development["director"]["crew_constraints"] = {
                "exposure_bias": "protect_highlights",
                "camera_height_policy": "grounded_threshold",
            }
            errors = validate(development, storyboard, style, project)
            self.assertTrue(any("exposure_bias 必须对齐" in e or "height_strategy 必须对齐" in e
                                for e in errors), errors)
            for shot in storyboard["shots"]:
                shot["crew"]["lighting"]["exposure_bias"] = "protect_highlights"
                shot["crew"]["cinematography"]["height_strategy"] = "grounded_threshold"
            self.assertEqual(validate(development, storyboard, style, project), [])
            # 基础语法未过时不能进入风格化路径
            storyboard["shots"][0]["crew"]["cinematography"]["angle_deg"] = 0
            storyboard["shots"][1]["crew"]["cinematography"]["angle_deg"] = 0
            storyboard["shots"][1]["crew"]["cinematography"]["camera_side"] = "left"
            storyboard["shots"][1]["crew"]["cinematography"]["axis_id"] = "door_axis"
            storyboard["shots"][1]["crew"]["cinematography"]["shot_size"] = "近景"
            grammar_errors = validate_crew_pipeline(development, storyboard, project=project)
            self.assertTrue(
                any("30" in e or "景别变化不能豁免" in e or "近同角度" in e for e in grammar_errors),
                grammar_errors,
            )
            from scripts.crew_rehearsal import analyze as rehearsal_analyze
            report = rehearsal_analyze(development, storyboard, project=project)
            self.assertFalse(report["director_stylization"]["eligible_for_routing"])

    def test_changed_scene_master_invalidates_only_dependent_keyframe(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            project = Path(tmp)
            fixture(project)
            sb = build(project)
            master, frame = project / "master.png", project / "frame.png"
            master.write_bytes(PNG_BYTES)
            frame.write_bytes(PNG_BYTES)
            register(project, "M1", "scene_master", "gate", master, "测试批准记录")
            register(project, "SH001", "keyframe", "gate", frame, "测试批准记录", "M1")
            registry = json.loads((project / "state" / "visual_assets.json").read_text(encoding="utf-8"))
            self.assertEqual(require_keyframe(project, sb["shots"][0], registry)["status"], "approved")
            master.write_bytes(b"changed master asset")
            with self.assertRaisesRegex(ValueError, "复核"):
                require_keyframe(project, sb["shots"][0], registry)

    def test_unrelated_scene_change_does_not_invalidate_other_master(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            project = Path(tmp)
            _, _, _, dev, _ = fixture(project)
            dev["scenes"].append({**dev["scenes"][0], "id": "road"})
            write(project / "state/development.json", dev)
            sb = build(project)
            asset = project / "asset.png"
            asset.write_bytes(PNG_BYTES)
            register(project, "M1", "scene_master", "gate", asset, "观察记录")
            register(project, "SH001", "keyframe", "gate", asset, "观察记录", "M1")
            dev["scenes"][1]["master_prompt"] = "只修改道路地点"
            write(project / "state/development.json", dev)
            registry = json.loads((project / "state/visual_assets.json").read_text(encoding="utf-8"))
            self.assertEqual(require_keyframe(project, sb["shots"][0], registry)["status"], "approved")

    def test_full_compilation_carries_multiline_lyrics_and_keeps_backend_unselected(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            project = Path(tmp)
            fixture(project)
            sb = build(project)
            write_sequence_stub(project, sb.get("shots") or [])
            def run(script, *args):
                completed = subprocess.run([sys.executable, str(ROOT / "scripts" / script),
                                            str(project), *args], capture_output=True,
                                           encoding="utf-8")
                self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
            run("prompt_build.py")
            for gate in ("gate5", "gate6"):
                completed = subprocess.run([sys.executable, str(ROOT / "scripts/gate_checks.py"),
                                            gate, str(project)], capture_output=True, encoding="utf-8")
                self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            sb = json.loads((project / "state" / "storyboard.json").read_text(encoding="utf-8"))
            p2 = json.loads((project / "prompts/SH002.json").read_text(encoding="utf-8"))
            self.assertEqual(p2["meta"]["lyric_text"], "第一句\n第二句")
            run("chapter_plan_build.py", "--max-duration", "4")
            asset = project / "master.png"
            asset.write_bytes(PNG_BYTES)
            register(project, "M1", "scene_master", "gate", asset, "测试观察")
            for shot in sb["shots"]:
                register(project, shot["id"], "keyframe", "gate", asset, "测试观察", "M1")
            result = build_chapters(project)
            self.assertFalse(result["submission_ready"])
            manifest = json.loads(
                (project / "state/generation_manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(manifest["required_reference_mode"], "omni_reference")
            self.assertFalse(
                manifest["backend_selection_policy"]["fallback_to_non_omni_model"]
            )
            chapter_plan = json.loads(
                (project / "state/chapter_plan.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                chapter_plan["backend_selection_policy"]["required_reference_mode"],
                "omni_reference",
            )
            package = json.loads((project / "prompts/chapters/CH01.json").read_text(encoding="utf-8"))
            self.assertEqual(len(package["keyframe_references"]), 2)
            self.assertIn("动作起止与过程", package["prompt"])
            self.assertEqual(package["omni_reference"]["mode"], "omni_reference_blueprint")
            self.assertIsNone(package["omni_reference"]["backend"])
            self.assertEqual(
                package["omni_reference"]["timeline"][0]["cut_relation"], "动作匹配",
            )
            self.assertEqual(
                package["omni_reference"]["audio_policy"]["delivery_audio"],
                "timeline.master_audio",
            )
            # 修改上游后不能用旧批准资产或旧机检报告继续编译。
            dev = json.loads((project / "state/development.json").read_text(encoding="utf-8"))
            dev["treatment"]["concept"] = "改变故事"
            write(project / "state/development.json", dev)
            with self.assertRaises(ValueError):
                build_chapters(project)

    def test_edit_uses_selected_frames_not_whole_generated_clip(self):
        sb = {"fps": 24, "shots": [{"id": "SH001", "scene_id": "gate",
                                      "window": {"start": 0, "end": 2}}]}
        clips = {"C1": {"status": "reviewed", "evidence": "实际看过", "frame_count": 360,
                         "fps": 24, "scene_id": "gate", "path": "clips/C1.mp4", "sha256": "test"}}
        selected = [{"shot_id": "SH001", "clip_id": "C1", "in_frame": 24, "out_frame": 72,
                     "edit_reason": "只使用完整动作", "state_check": "状态连续"}]
        result = compile_selection(sb, clips, selected)
        self.assertEqual(result[0]["frames"], 48)
        selected[0]["out_frame"] = 360
        with self.assertRaises(ValueError):
            compile_selection(sb, clips, selected)



    def test_body_orientation_reverse_requires_evidence(self):
        editing = crew_bible_fixture()["editing"]
        music, timeline, fps = self._music_ctx()
        shots = []
        for index in range(2):
            crew = shot_crew_fixture(index)
            crew["editing"].update(
                hook=index == 0, music_cue=("downbeat", "accent")[index],
                geography_status="establish" if index == 0 else "established_from_previous",
                coverage_role=("establish", "turn")[index],
                time_relation="continuous",
                incoming_relation="song_start" if index == 0 else "cut",
                outgoing_relation="cut" if index == 0 else "chapter_end",
            )
            crew["cinematography"].update(angle_deg=0 if index == 0 else 45, position_xy=[0, 1])
            crew["blocking"]["body_orientation"] = "朝右" if index == 0 else "朝左"
            shots.append({"id": f"SH00{index + 1}", "scene_id": "gate",
                          "window": {"start": index * 2.0, "end": (index + 1) * 2.0},
                          "crew": crew})
        errors = validate_sequence_language(
            shots, editing, music_structure=music, timeline=timeline, fps=fps,
            scenes=[{"id": "gate", "kind": "narrative"}],
        )
        self.assertTrue(any("body_orientation_evidence" in e for e in errors), errors)
        shots[1]["crew"]["blocking"]["body_orientation_evidence"] = "人物转身面向来路"
        errors = validate_sequence_language(
            shots, editing, music_structure=music, timeline=timeline, fps=fps,
            scenes=[{"id": "gate", "kind": "narrative"}],
        )
        self.assertFalse(any("body_orientation_evidence" in e for e in errors), errors)

    def test_cross_scene_same_axis_short_window_counts_as_three_peat(self):
        editing = crew_bible_fixture()["editing"]
        editing["pacing_policy"]["min_shot_size_variety_per_scene"] = 1
        music, timeline, fps = self._music_ctx()
        shots = []
        for index in range(3):
            crew = shot_crew_fixture(0)
            crew["editing"].update(
                hook=index == 0, music_cue=("downbeat", "accent", "mid")[index],
                geography_status="establish",
                coverage_role=("establish", "develop", "reaction")[index],
                novelty_axis=("a", "b", "c")[index],
                visual_change_code=("prop_state", "gaze_shift", "blocking_shift")[index],
                visual_change=("一", "二", "三")[index],
                incoming_relation="song_start" if index == 0 else "cut",
                outgoing_relation="cut" if index < 2 else "chapter_end",
            )
            crew["cinematography"].update(
                viewpoint_id="shared_vp", axis_id="shared_axis",
                camera_side="left", angle_deg=0, shot_size="中景",
                position_xy=[0, index],
            )
            shots.append({
                "id": f"SH00{index + 1}", "scene_id": f"scene_{index}",
                "window": {"start": index * 2.0, "end": (index + 1) * 2.0},
                "crew": crew,
            })
        errors = validate_sequence_language(
            shots, editing, music_structure=music, timeline=timeline, fps=fps,
            scenes=[{"id": f"scene_{i}", "kind": "narrative"} for i in range(3)],
        )
        self.assertTrue(any("切碎 scene_id" in e or "跨 scene" in e for e in errors), errors)

    def test_expanded_vague_aesthetic_english_and_chinese_blocked(self):
        errors = []
        from scripts.crew_pipeline import _reject_vague_aesthetic
        _reject_vague_aesthetic("t", {"note": "cinematic vibe and 绝美光影"}, errors)
        self.assertTrue(any("cinematic vibe" in e for e in errors), errors)
        self.assertTrue(any("绝美" in e for e in errors), errors)

    def test_pacing_exception_rejects_short_or_echo_evidence(self):
        editing = crew_bible_fixture()["editing"]
        editing["pacing_policy"]["max_average_shot_sec"] = 15.0
        editing["pacing_exception"] = {"reason": "慢", "evidence": "慢"}
        music, timeline, fps = self._music_ctx()
        shots = []
        for index in range(2):
            crew = shot_crew_fixture(index)
            crew["editing"].update(
                hook=index == 0, music_cue=("downbeat", "accent")[index],
                geography_status="establish" if index == 0 else "established_from_previous",
                coverage_role=("establish", "turn")[index],
                incoming_relation="song_start" if index == 0 else "cut",
                outgoing_relation="cut" if index == 0 else "chapter_end",
            )
            shots.append({"id": f"SH00{index + 1}", "scene_id": "gate",
                          "window": {"start": index * 6.0, "end": (index + 1) * 6.0},
                          "crew": crew})
        errors = validate_sequence_language(
            shots, editing, music_structure=music, timeline=timeline, fps=fps,
            scenes=[{"id": "gate", "kind": "narrative"}],
        )
        self.assertTrue(any("超过曲速默认" in e for e in errors), errors)

    def test_revision_not_2_blocks_crew_pipeline_when_project_present(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            project = Path(tmp)
            _, _, _, development, _ = fixture(project)
            storyboard = build(project)
            development["creative_workflow_revision"] = 1
            errors = validate_crew_pipeline(development, storyboard, project=project)
            self.assertTrue(any("creative_workflow_revision" in e for e in errors), errors)

if __name__ == "__main__":
    unittest.main()
