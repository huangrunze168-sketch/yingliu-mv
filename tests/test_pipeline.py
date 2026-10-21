import json
import math
import os
import random
import shutil
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(ROOT))

from scripts.gate_checks import REF_DUTIES, check_storyboard
from scripts.chapter_plan_build import (
    group_shots, select_prototype, require_production_timing, require_director_final_cut,
)
from scripts.chapter_audio_build import build_audio_segments
from scripts.director_review_gate import DIMENSIONS, initial_review, validate_review
from scripts.chapter_prompt_build import build as build_chapter_prompts, compile_chapter
from scripts.prompt_build import compile_director_method, keyframe_moment
from scripts.storyboard_build import apply_story_plan, REQUIRED_STORY_FIELDS
from scripts.render_preview import resolve_visual_asset
from scripts.audio_master import resolve_master, master_encoding_args, probe_audio
from scripts.submission_contract import validate_submission_contract as validate_submission_full

def validate_submission_structure_only(contract):
    """仅结构；全量门禁由 test_production_contracts 覆盖。"""
    return validate_submission_full(contract, structural_only=True)

def validate_submission_contract(contract):
    """本文件历史结构用例；名称保留但显式只跑 structural_only。"""
    return validate_submission_structure_only(contract)
from scripts.omni_reference import (
    build_omni_reference_blueprint, validate_omni_reference_blueprint,
)


def write_wav(path: Path, duration: float, silence: bool = False) -> None:
    sample_rate = 16000
    samples = []
    for i in range(int(duration * sample_rate)):
        if silence:
            value = 0
        else:
            block = i // 400
            amplitude = 0.15 + 0.70 * random.Random(block).random()
            value = int(26000 * amplitude * math.sin(2 * math.pi * 220 * i / sample_rate))
        samples.append(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(b"".join(v.to_bytes(2, "little", signed=True) for v in samples))


class PipelineTests(unittest.TestCase):
    def test_omni_blueprint_is_backend_neutral_and_routes_each_reference(self):
        chapter = {
            "id": "CH01",
            "window": {"start": 0, "end": 4},
            "audio_asset": {"local_path": "input/chapter_audio/CH01.wav", "sha256": "audio"},
        }
        beats = [
            {
                "shot_id": "SH001", "start_sec": 0.0, "end_sec": 2.0,
                "keyframe_handle": "@图片 2", "story_change": "她发现信纸",
                "current_action": "手指停在旧字上", "camera_witness": "桌侧近景",
                "editorial_role": "建立线索", "cut_relation": "视线匹配",
            },
            {
                "shot_id": "SH002", "start_sec": 2.0, "end_sec": 4.0,
                "keyframe_handle": "@图片 3", "story_change": "她抬眼看门外",
                "current_action": "视线越过门框", "camera_witness": "门外反打",
                "editorial_role": "揭示目标", "cut_relation": "",
            },
        ]
        blueprint = build_omni_reference_blueprint(
            chapter, beats,
            protagonist_source={"local_path": "input/protagonist.png", "sha256": "hero"},
            keyframe_sources={
                "SH001": {"local_path": "keyframes/SH001.png", "sha256": "one"},
                "SH002": {"local_path": "keyframes/SH002.png", "sha256": "two"},
            },
        )
        self.assertIsNone(blueprint["backend"])
        self.assertEqual(blueprint["adapter_status"], "not_in_scope")
        self.assertEqual(validate_omni_reference_blueprint(blueprint), [])
        self.assertEqual(
            blueprint["timeline"][0]["reference_handles"],
            ["@图片 1", "@图片 2", "@音频 1"],
        )
        self.assertEqual(blueprint["audio_policy"]["delivery_audio"],
                         "timeline.master_audio")

    def test_omni_blueprint_rejects_character_role_leak(self):
        blueprint = {
            "mode": "omni_reference_blueprint", "backend": None,
            "subject_definitions": [{"subject_id": "hero", "handle": "@图片 1"}],
            "reference_bindings": [{
                "handle": "@图片 1", "modality": "image",
                "source": {"local_path": "hero.png"},
                "intents": ["character_identity", "wardrobe", "composition"],
                "do_not_inherit": [],
            }],
            "timeline": [],
            "audio_policy": {
                "reference_audio_is_condition_only": True,
                "generate_audio": False,
                "delivery_audio": "timeline.master_audio",
            },
        }
        errors = validate_omni_reference_blueprint(blueprint)
        self.assertTrue(any("只能锁定" in error for error in errors))
        self.assertTrue(any("未隔离" in error for error in errors))

    def test_literal_subject_mention_cannot_fall_back_to_random_person(self):
        contract = {
            "prompt": "@女主 在庭院中演唱",
            "subject_reference": {"required": True, "name": "女主"},
            "request": {"generate_audio": False},
        }
        errors = validate_submission_contract(contract)
        self.assertTrue(any("只是提示词文本" in error for error in errors))
        self.assertTrue(any("可追溯绑定" in error for error in errors))

    def test_unbound_generic_upload_cannot_impersonate_subject(self):
        contract = {
            "prompt": "女主在庭院中演唱",
            "subject_reference": {
                "required": True,
                "name": "女主",
                "binding": {
                    "kind": "generic_upload_url",
                    "review_status": "approved",
                    "asset_id": "ordinary-upload",
                },
            },
            "structured_mentions": [{"name": "女主", "mention_id": "m1"}],
            "request": {"generate_audio": False},
        }
        errors = validate_submission_contract(contract)
        self.assertTrue(any("可追溯绑定" in error for error in errors))

    def test_approved_structured_subject_binding_can_submit(self):
        contract = {
            "prompt": "@女主 在庭院中演唱",
            "reference_mode": "omni_reference",
            "reference_bindings": [{
                "handle": "@女主", "modality": "image",
                "source": {"asset_id": "subject-123"},
                "intents": ["character_identity", "wardrobe"],
            }],
            "backend_capabilities": {
                "verified": True, "omni_reference": True,
                "multi_shot": True, "timestamp_control": True,
            },
            "subject_reference": {
                "required": True,
                "name": "女主",
                "binding": {
                    "kind": "library_subject",
                    "review_status": "approved",
                    "subject_id": "subject-123",
                },
            },
            "structured_mentions": [{"name": "女主", "mention_id": "subject-123"}],
            "audio_reference": {"condition_only": True},
            "request": {"generate_audio": False},
            "director_cut_status": "approved",
        }
        self.assertEqual(validate_submission_contract(contract), [])

    def test_every_video_backend_must_use_omni_reference_model(self):
        base = {
            "prompt": "庭院中人物转身",
            "request": {"generate_audio": False},
            "director_cut_status": "approved",
        }
        for mode in (None, "text_to_video", "first_frame_to_video", "subject_library"):
            contract = {**base, "reference_mode": mode}
            errors = validate_submission_contract(contract)
            self.assertTrue(any("必须使用 reference_mode=omni_reference" in e
                                for e in errors), mode)

    def test_all_reference_requires_verified_roles_and_multishot_capability(self):
        contract = {
            "prompt": "参考 @图片 1 的人物五官与服装；参考 @图片 2 的场景地理；"
                      "参考 @视频 1 的镜头运动；参考 @音频 1 的表演节奏。",
            "reference_mode": "omni_reference",
            "reference_bindings": [
                {"handle": "@图片 1", "modality": "image",
                 "source": {"asset_id": "hero"},
                 "intents": ["character_identity", "wardrobe"]},
                {"handle": "@图片 2", "modality": "image",
                 "source": {"asset_id": "scene"},
                 "intents": ["location", "composition"]},
                {"handle": "@视频 1", "modality": "video",
                 "source": {"asset_id": "camera"},
                 "intents": ["camera_motion"]},
                {"handle": "@音频 1", "modality": "audio",
                 "source": {"asset_id": "performance"},
                 "intents": ["performance_timing"]},
            ],
            "backend_capabilities": {
                "verified": True, "omni_reference": True, "multi_shot": True,
                "timestamp_control": True,
                "reference_limits": {"image": 30, "video": 10, "audio": 10},
            },
            "audio_reference": {"condition_only": True},
            "request": {"generate_audio": False},
            "edit_shots": [
                {"id": "SH001", "start_sec": 0.0, "end_sec": 2.0, "cut_relation": "动作匹配"},
                {"id": "SH002", "start_sec": 2.0, "end_sec": 4.0},
            ],
            "director_cut_status": "approved",
        }
        self.assertEqual(validate_submission_contract(contract), [])
        contract["reference_bindings"] = contract["reference_bindings"][:-1]
        contract["backend_capabilities"]["multi_shot"] = False
        errors = validate_submission_contract(contract)
        self.assertTrue(any("@音频1" in error and "未绑定" in error for error in errors))
        self.assertTrue(any("multi_shot" in error for error in errors))

    def test_omni_reference_direct_image_can_lock_character(self):
        contract = {
            "prompt": "参考 @图片 1 的五官、发型和服装，不参考其背景与构图。",
            "reference_mode": "omni_reference",
            "reference_bindings": [{
                "handle": "@图片 1", "modality": "image",
                "source": {"url": "https://a.lovart.ai/assets/hero.png"},
                "intents": ["character_identity", "wardrobe"],
            }],
            "subject_reference": {
                "required": True, "handle": "@图片 1",
                "binding": {"kind": "omni_image_reference",
                            "url": "https://a.lovart.ai/assets/hero.png"},
            },
            "backend_capabilities": {
                "verified": True, "omni_reference": True,
                "multi_shot": True, "timestamp_control": True,
                "reference_limits": {"image": 30, "video": 10, "audio": 10},
            },
            "request": {"generate_audio": False},
            "director_cut_status": "approved",
        }
        self.assertEqual(validate_submission_contract(contract), [])

    def test_director_cut_rejects_model_limit_as_edit_duration(self):
        shots = [{"id": "SH001", "window": {"start": 0.0, "end": 15.0}}]
        edit = {
            "timing_status": "director_approved_final_cut",
            "director_cut_approved": True,
            "performance_policy": {"max_edit_shot_duration_sec": 6.0},
            "shots": [{"id": "SH001", "editorial_role": "建立空间"}],
        }
        with self.assertRaisesRegex(ValueError, "长镜头理由"):
            require_director_final_cut(edit, shots)
        edit["shots"][0].update(
            director_approved_long_take=True,
            long_take_justification="表演在同一空间内连续完成，切开会破坏信息释放",
        )
        require_director_final_cut(edit, shots)

    def test_conditioning_audio_cannot_be_regenerated(self):
        contract = {
            "prompt": "庭院空镜",
            "audio_reference": {"condition_only": True},
            "request": {"generate_audio": True},
        }
        errors = validate_submission_contract(contract)
        self.assertTrue(any("generate_audio" in error for error in errors))

    def test_storyboard_preserves_keyframe_moment_and_shooting_design(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            project = Path(tmp)
            (project / "state").mkdir()
            item = {key: "本镜设计" for key in REQUIRED_STORY_FIELDS}
            item.update(id="SH001", scene_id="room", source_beat="P1",
                        state_before={"hand": "on_paper"}, state_after={"hand": "free"},
                        keyframe_moment="指腹压住回翘折角", shooting_station="C1",
                        visual_acceptance="脸在画外", audience_information="重复动作无效",
                        design_revision="r2", decision_authority={"USER-LOCKED": ["16:9"]})
            (project / "state/story_plan.json").write_text(json.dumps({
                "method": "zy_story_visual_development", "shots": [item],
            }, ensure_ascii=False), encoding="utf-8")
            shots = [{"id": "SH001", "scene_id": "room"}]
            apply_story_plan(project, shots)
            for key in ("keyframe_moment", "shooting_station", "visual_acceptance",
                        "audience_information", "design_revision", "decision_authority"):
                self.assertEqual(shots[0][key], item[key])
            self.assertEqual(keyframe_moment(shots[0]), "指腹压住回翘折角")

    def test_keyframe_moment_does_not_stack_video_actions(self):
        shot = {"id": "SH006", "current_action": "回屋、合匣、移手",
                "keyframe_moment": "匣已合上，右指尖仍接触盖面"}
        self.assertEqual(keyframe_moment(shot), shot["keyframe_moment"])
        self.assertEqual(shot["current_action"], "回屋、合匣、移手")

    def test_keyframe_moment_legacy_fallback(self):
        self.assertEqual(keyframe_moment({"current_action": "手压折角"}), "手压折角")

    def test_keyframe_moment_rejects_explicit_empty_value(self):
        for value in ("", "  ", None, []):
            with self.subTest(value=value), self.assertRaises(ValueError):
                keyframe_moment({"current_action": "完整动作", "keyframe_moment": value})

    def test_lipsync_chapter_rejects_provisional_timing(self):
        shots = [{"id": "SH001", "lip_sync": True}]
        provisional = {
            "production_timing_verified": False,
            "simulation_only": True,
            "authority": "provisional_not_for_final_lipsync",
            "pending_review": [1],
        }
        with self.assertRaisesRegex(ValueError, "临时或待核时间轴"):
            require_production_timing(provisional, shots)
        verified = {"production_timing_verified": True, "pending_review": []}
        require_production_timing(verified, shots)

    def test_narrative_chapter_can_use_provisional_timing_without_lipsync_claim(self):
        require_production_timing(
            {"simulation_only": True, "pending_review": [1]},
            [{"id": "SH001", "lip_sync": False}],
        )

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "需要 ffmpeg")
    def test_analysis_copy_cannot_silently_replace_master(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            project = Path(tmp)
            write_wav(project / "analysis.wav", 0.5)
            with self.assertRaisesRegex(ValueError, "低采样率分析音频"):
                resolve_master(project, {"source_audio": "analysis.wav"})
            self.assertEqual(resolve_master(project, {
                "source_audio": "analysis.wav", "master_audio": "analysis.wav",
            }), project / "analysis.wav")

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "需要 ffmpeg")
    def test_mp3_master_survives_mux_with_identical_audio_packets(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            source = Path(tmp) / "original.mp3"
            output = Path(tmp) / "delivered.mp4"
            subprocess.run([
                "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                "sine=frequency=12000:sample_rate=44100:duration=0.5",
                "-ac", "2", "-c:a", "libmp3lame", "-b:a", "192k", str(source),
            ], check=True, capture_output=True)
            subprocess.run([
                "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                "color=s=64x64:r=24:d=0.5", "-i", str(source),
                "-map", "0:v:0", "-map", "1:a:0", "-c:v", "libx264",
                *master_encoding_args(source), str(output),
            ], check=True, capture_output=True)
            info = probe_audio(output)
            self.assertEqual(info["codec_name"], "mp3")
            self.assertEqual(int(info["sample_rate"]), 44100)
            self.assertEqual(info["channels"], 2)
            def payload_hash(path):
                return subprocess.run([
                    "ffmpeg", "-v", "error", "-i", str(path), "-map", "0:a:0",
                    "-c:a", "copy", "-f", "hash", "-hash", "sha256", "-",
                ], check=True, capture_output=True, text=True).stdout.strip()
            self.assertEqual(payload_hash(source), payload_hash(output))

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "需要 ffmpeg")
    def test_pcm_master_uses_lossless_encoding(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            source, output = Path(tmp) / "master.wav", Path(tmp) / "delivery.m4a"
            write_wav(source, 0.5)
            subprocess.run([
                "ffmpeg", "-y", "-v", "error", "-i", str(source),
                *master_encoding_args(source), str(output),
            ], check=True, capture_output=True)
            def decoded(path):
                return subprocess.run([
                    "ffmpeg", "-v", "error", "-i", str(path),
                    "-f", "s16le", "-c:a", "pcm_s16le", "-",
                ], check=True, capture_output=True).stdout
            self.assertEqual(decoded(source), decoded(output))

    def run_script(self, name: str, *args: object) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        return subprocess.run(
            [sys.executable, str(SCRIPTS / name), *(str(arg) for arg in args)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=env,
        )

    def test_zy_cinematic_vendor_keeps_license_and_mv_boundary(self):
        vendor = ROOT / "vendor" / "zy-cinematic-realism"
        self.assertTrue((vendor / "SKILL.md").is_file())
        self.assertIn("CC BY-NC 4.0", (vendor / "LICENSE").read_text(encoding="utf-8"))
        self.assertIn("popopo-99", (vendor / "NOTICE.md").read_text(encoding="utf-8"))
        integration = (ROOT / "references" / "zy-cinematic-integration.md").read_text(
            encoding="utf-8")
        for protected_field in ("window", "lyric_text", "lip_sync", "audio_policy"):
            self.assertIn(protected_field, integration)

    def test_character_reference_can_lock_wardrobe(self):
        self.assertIn("wardrobe", REF_DUTIES)
        framework = (ROOT / "references" / "prompt-framework.md").read_text(encoding="utf-8")
        self.assertIn("likeness + wardrobe", framework)

    def test_preview_distributes_scene_assets_by_chapter(self):
        scenes = [Path(f"scene_{i}.png") for i in range(4)]
        selected = [resolve_visual_asset(Path("."), {}, i, 8, scenes, None)[0].name
                    for i in range(8)]
        self.assertEqual(selected, ["scene_0.png", "scene_0.png", "scene_1.png",
                                    "scene_1.png", "scene_2.png", "scene_2.png",
                                    "scene_3.png", "scene_3.png"])

    def test_preview_prefers_shot_keyframe_then_scene_master(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            input_dir = root / "input"
            input_dir.mkdir()
            files = {
                "protagonist.png": b"p",
                "master-hotel-native.png": b"h",
                "cage-keyframe-reviewed.png": b"c",
                "keyframe-SH001-native-reviewed.png": b"k1",
                "scene_pool.png": b"sp",
            }
            for name, body in files.items():
                (input_dir / name).write_bytes(body)
            images = sorted(input_dir.iterdir())
            scenes = [p for p in images if p.stem.startswith("scene_")]
            fallback = input_dir / "protagonist.png"

            path, via = resolve_visual_asset(
                root, {"id": "SH001", "scene_id": "cage"}, 0, 4, scenes, fallback, images,
            )
            self.assertEqual(path.name, "keyframe-SH001-native-reviewed.png")
            self.assertTrue(via.startswith("shot_keyframe:"))

            path, via = resolve_visual_asset(
                root, {"id": "SH010", "scene_id": "hotel"}, 1, 4, scenes, fallback, images,
            )
            self.assertEqual(path.name, "master-hotel-native.png")
            self.assertTrue(via.startswith("scene_match:"))

            path, via = resolve_visual_asset(
                root, {"id": "SH020", "scene_id": "cage"}, 2, 4, scenes, fallback, images,
            )
            self.assertEqual(path.name, "cage-keyframe-reviewed.png")

            path, via = resolve_visual_asset(
                root, {"id": "SH030", "scene_id": "pool"}, 3, 4, scenes, fallback, images,
            )
            self.assertEqual(path.name, "scene_pool.png")

            path, via = resolve_visual_asset(
                root, {"id": "SH099", "scene_id": "unknown"}, 0, 1, [], fallback, images,
            )
            self.assertEqual(path.name, "protagonist.png")
            self.assertTrue(via.startswith("fallback:"))

    def test_chapter_plan_respects_model_duration(self):
        shots = [
            {"id": "SH001", "window": {"start": 0.0, "end": 6.0}},
            {"id": "SH002", "window": {"start": 6.0, "end": 13.0}},
            {"id": "SH003", "window": {"start": 13.0, "end": 20.0}},
        ]
        groups = group_shots(shots, 15.0)
        self.assertEqual([[s["id"] for s in group] for group in groups],
                         [["SH001", "SH002"], ["SH003"]])

    def test_chapter_plan_prefers_story_breaks(self):
        shots = [
            {"id": "SH001", "window": {"start": 0.0, "end": 4.0}},
            {"id": "SH002", "window": {"start": 4.0, "end": 8.0}},
            {"id": "SH003", "window": {"start": 8.0, "end": 12.0}},
        ]
        groups = group_shots(shots, 15.0, {"SH002"})
        self.assertEqual([[s["id"] for s in group] for group in groups],
                         [["SH001", "SH002"], ["SH003"]])

    def test_prototype_prefers_lipsync_and_story_complexity(self):
        chapters = [
            {"id": "CH01", "duration_sec": 14, "shot_deltas": [
                {"lip_sync": False}, {"lip_sync": False}, {"lip_sync": False}]},
            {"id": "CH02", "duration_sec": 12, "shot_deltas": [
                {"lip_sync": True}, {"lip_sync": True}]},
            {"id": "CH03", "duration_sec": 11, "shot_deltas": [
                {"lip_sync": True}, {"lip_sync": True}, {"lip_sync": True}]},
        ]
        self.assertEqual(select_prototype(chapters), "CH03")

    def test_director_name_is_expanded_to_executable_axes(self):
        method = compile_director_method(
            {"director_ref": {
                "name": "用户选择的导演参考", "intensity": "轻微",
                "active_axes": ["composition_space"],
                "active_traits": ["主导色来自真实物体，不使用全局滤镜"],
            }},
            {"light": "门外冷天光与室内余火", "camera_witness": "门柱后腰高机位",
             "visual_center": "鼓槌停在鼓面上方的手",
             "information_hierarchy": "先读手与鼓，再读侧脸，最后读空城门",
             "blocking": "人物朝右侧关外，保持方向轴",
             "director_decisions": {"composition_space": {"instruction": "秩序中一次偏离"}}},
        )
        self.assertTrue(all(method[key] for key in
                            ("light_contrast", "color_exposure", "lens_camera",
                             "composition_space")))
        self.assertNotIn("用户选择的导演参考", json.dumps(method, ensure_ascii=False))
        self.assertIn("真实物体", method["active_traits"][0])

    def test_director_review_blocks_contact_sheet_only(self):
        plan = {"production_policy": {"prototype_chapter": "CH03"}}
        review = initial_review(plan)
        review["reviewed_media"] = "output/contact_sheet.jpg"
        review["watched_full_duration"] = True
        review["technical_checks"]["container_and_duration"] = "pass"
        review["technical_checks"]["audio_alignment"] = "pass"
        review["decision"] = "approve"
        review["batch_generation_allowed"] = True
        review["timecoded_findings"] = [{
            "start_sec": 0.0, "end_sec": 1.0,
            "dimension": "story_readability", "observation": "状态变化可读",
        }]
        for name in DIMENSIONS:
            review["director_checks"][name] = {"status": "pass", "evidence": "有证据"}
        errors = validate_review(review, plan)
        self.assertTrue(any("动态视频" in error for error in errors))

    def test_director_review_requires_technical_and_timecoded_evidence(self):
        plan = {"production_policy": {"prototype_chapter": "CH01"}}
        review = initial_review(plan)
        review["reviewed_media"] = "output/prototype.mp4"
        review["watched_full_duration"] = True
        review["decision"] = "approve"
        review["batch_generation_allowed"] = True
        for name in DIMENSIONS:
            review["director_checks"][name] = {"status": "pass", "evidence": "可观察"}
        errors = validate_review(review, plan)
        self.assertTrue(any("技术检查未通过" in error for error in errors))
        self.assertTrue(any("timecoded_findings" in error for error in errors))

    def test_chapter_prompt_contains_routed_director_axes(self):
        chapter = {"id": "CH01", "window": {"start": 0.0, "end": 2.0},
                   "shot_ids": ["SH001"], "continuity_lock": {}}
        axes = {
            "light_contrast": "门外冷光，屋内暗部保留",
            "color_exposure": "颜色来自旧布",
            "lens_camera": "门框后固定观察",
            "composition_space": "前景门框遮挡，人物居中景",
            "active_traits": ["秩序中一次偏离"],
        }
        prompt_map = {"SH001": {
            "meta": {"shot_id": "SH001", "window": {"start": 0.0, "end": 2.0},
                     "lip_sync": False},
            "story": {"before": "门关", "next": "人离开", "shot_delta": "门开"},
            "performance": "人物推门", "scene": "门内", "camera": "固定机位",
            "direction": {"method": axes, "edit_trigger": "门碰墙时切",
                          "scene_master": {
                              "story_intent": "离开", "obstacle": "门未开",
                              "audience_information": "她已决定离开",
                              "shooting_station": "门柱内侧腰高",
                              "frame_boundary": "门柱截住左侧空间",
                              "visual_acceptance": "先读推门的手，再读门外道路",
                          },
                          "anti_ai": {"motion_rule": "手接触门后门才转动"}},
        }}
        compiled = compile_chapter(chapter, prompt_map, "16:9")
        self.assertIn("门外冷光，屋内暗部保留", compiled["prompt"])
        self.assertIn("秩序中一次偏离", compiled["prompt"])
        self.assertIn("门柱内侧腰高", compiled["prompt"])
        self.assertIn("@图片 1：只参考同一人物", compiled["prompt"])
        self.assertIn("@图片 2：只参考 SH001", compiled["prompt"])
        self.assertIn("@音频 1：只参考本章表演节奏", compiled["prompt"])

    def test_chapter_prompt_without_character_starts_keyframes_at_image_one(self):
        chapter = {"id": "CH01", "window": {"start": 0.0, "end": 2.0},
                   "shot_ids": ["SH001"], "continuity_lock": {}}
        prompt_map = {"SH001": {
            "meta": {"shot_id": "SH001", "window": {"start": 0.0, "end": 2.0},
                     "lip_sync": False},
            "story": {"before": "门关", "next": "门开", "shot_delta": "门开"},
            "performance": "门被风推开", "scene": "空房", "camera": "固定机位",
            "direction": {"method": {
                "light_contrast": "窗光", "color_exposure": "木色",
                "lens_camera": "门后固定", "composition_space": "门框分层",
            }, "edit_trigger": "门停时切", "scene_master": {
                "story_intent": "揭示出口", "obstacle": "门未开",
                "audience_information": "出口出现", "shooting_station": "门后腰高",
                "frame_boundary": "门柱截边", "visual_acceptance": "先见门后见院",
            }, "anti_ai": {"motion_rule": "风先动帘，门后转动"}},
        }}
        compiled = compile_chapter(
            chapter, prompt_map, "16:9", has_character_reference=False,
        )
        self.assertNotIn("只参考同一人物", compiled["prompt"])
        self.assertIn("@图片 1：只参考 SH001", compiled["prompt"])

    def test_chapter_manifest_blocks_submission_until_user_selects_backend(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            project = Path(tmp)
            (project / "state").mkdir()
            (project / "prompts").mkdir()
            (project / "state" / "style.json").write_text(
                json.dumps({"aspect_ratio": "16:9"}), encoding="utf-8")
            (project / "state" / "storyboard.json").write_text(
                json.dumps({"shots": []}), encoding="utf-8")
            (project / "state" / "chapter_plan.json").write_text(json.dumps({
                "production_policy": {"prototype_chapter": "CH01"},
                "chapters": [{"id": "CH01", "window": {"start": 0.0, "end": 2.0},
                              "shot_ids": ["SH001"], "continuity_lock": {}}],
            }), encoding="utf-8")
            (project / "prompts" / "SH001.json").write_text(json.dumps({
                "meta": {"shot_id": "SH001", "window": {"start": 0.0, "end": 2.0},
                         "lip_sync": False},
                "story": {"before": "门关", "next": "人离开", "shot_delta": "门开"},
                "performance": "人物推门", "scene": "门内", "camera": "固定机位",
                "direction": {"method": {
                    "light_contrast": "冷光", "color_exposure": "旧布承色",
                    "lens_camera": "固定观察", "composition_space": "门框前景",
                }, "edit_trigger": "门碰墙时切",
                    "anti_ai": {"motion_rule": "接触后转动"}},
            }), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "旧分镜|development.json"):
                build_chapter_prompts(project)

    def test_chapter_compiler_rejects_story_summary_without_director_axes(self):
        chapter = {"id": "CH01", "window": {"start": 0.0, "end": 5.0},
                   "shot_ids": ["SH001"], "continuity_lock": {}}
        prompt_map = {"SH001": {
            "meta": {"shot_id": "SH001", "window": {"start": 0.0, "end": 5.0},
                     "lip_sync": False},
            "story": {"before": "门未开", "next": "人物离开", "shot_delta": "门打开"},
            "performance": "人物推门",
            "scene": "门内到门外",
            "camera": "门柱后观察",
        }}
        with self.assertRaisesRegex(ValueError, "未通过导演编译"):
            compile_chapter(chapter, prompt_map, "16:9")

    def test_chapter_audio_build_stays_backend_neutral(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "input").mkdir()
            (project / "state").mkdir()
            write_wav(project / "input" / "song.wav", 1.0)
            (project / "state" / "timeline.json").write_text(json.dumps({
                "source_audio": "input/song.wav", "master_audio": "input/song.wav",
                "duration_sec": 1.0,
                "production_timing_verified": True,
            }), encoding="utf-8")
            (project / "state" / "chapter_plan.json").write_text(json.dumps({
                "chapters": [{
                    "id": "CH01", "window": {"start": 0.0, "end": 1.0},
                    "duration_sec": 1.0, "shot_deltas": [{"lip_sync": True}],
                }],
            }), encoding="utf-8")
            build_audio_segments(project, sample_rate=16000)
            updated = json.loads((project / "state" / "chapter_plan.json")
                                 .read_text(encoding="utf-8"))
            asset = updated["chapters"][0]["audio_asset"]
            self.assertTrue(asset["contains_lip_sync_shots"])
            self.assertTrue((project / asset["local_path"]).is_file())
            self.assertNotIn("h3", json.dumps(asset).lower())
            self.assertNotIn("audio_list", json.dumps(asset).lower())

    def test_core_scripts_do_not_pin_video_backend(self):
        active = [
            ROOT / "scripts" / "storyboard_build.py",
            ROOT / "scripts" / "prompt_build.py",
            ROOT / "scripts" / "chapter_plan_build.py",
            ROOT / "scripts" / "chapter_audio_build.py",
            ROOT / "scripts" / "chapter_prompt_build.py",
            ROOT / "scripts" / "stitch_chapters.py",
        ]
        forbidden = ("minimax", "lovart", "audio_list", "reference-to-video")
        text = "\n".join(path.read_text(encoding="utf-8").lower() for path in active)
        for token in forbidden:
            self.assertNotIn(token, text)

    def test_stitch_script_supports_package_import(self):
        import scripts.stitch_chapters as stitch
        self.assertTrue(callable(stitch.main))

    def test_fallback_keeps_many_lyrics_inside_short_silent_audio(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            wav_path = project / "input" / "short.wav"
            lyrics_path = project / "input" / "lyrics.txt"
            timeline_path = project / "state" / "timeline.json"
            write_wav(wav_path, 1.0, silence=True)
            lyrics_path.write_text("\n".join(f"第{i}行" for i in range(10)), encoding="utf-8")

            result = self.run_script("timeline_fallback.py", wav_path, lyrics_path, timeline_path)
            self.assertEqual(result.returncode, 0, result.stderr)
            timeline = json.loads(timeline_path.read_text(encoding="utf-8"))
            self.assertEqual(timeline["source_audio"], "input/short.wav")
            self.assertEqual(timeline["lyrics_source"], "user_supplied")
            self.assertIs(timeline["production_timing_verified"], False)
            self.assertTrue(timeline.get("no_interlude"))
            self.assertTrue(all(0 <= row["start"] < row["end"] <= 1.0
                                for row in timeline["lines"]))

            storyboard = self.run_script("storyboard_build.py", project, "--fps", 24)
            self.assertEqual(storyboard.returncode, 0, storyboard.stderr)

    def test_fallback_rejects_nonfinite_offsets_without_overwriting(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wav = root / "song.wav"
            lyrics = root / "lyrics.txt"
            output = root / "timeline.json"
            write_wav(wav, 1.0, silence=True)
            lyrics.write_text("一行", encoding="utf-8")
            output.write_bytes(b"old-timeline")
            for argument in ("--intro", "--outro"):
                for value in ("nan", "inf", "-inf"):
                    with self.subTest(argument=argument, value=value):
                        result = self.run_script("timeline_fallback.py", wav, lyrics, output,
                                                 argument + "=" + value)
                        self.assertNotEqual(result.returncode, 0)
                        self.assertEqual(output.read_bytes(), b"old-timeline")
                        self.assertFalse(output.with_suffix(".lrc").exists())

    def test_fallback_rejects_precision_collapse_without_publishing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wav = root / "song.wav"
            lyrics = root / "lyrics.txt"
            output = root / "timeline.json"
            write_wav(wav, 0.01, silence=True)
            lyrics.write_text("\n".join("歌词" for _ in range(100)), encoding="utf-8")
            result = self.run_script("timeline_fallback.py", wav, lyrics, output)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(output.exists())
            self.assertFalse(output.with_suffix(".lrc").exists())

    def test_fallback_intro_outro_use_positive_unique_indexes(self):
        from scripts.gate_checks import check_timeline
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            wav, lyrics, output = root / "song.wav", root / "lyrics.txt", root / "timeline.json"
            write_wav(wav, 6.0, silence=True)
            lyrics.write_text("用户歌词", encoding="utf-8")
            result = self.run_script("timeline_fallback.py", wav, lyrics, output)
            self.assertEqual(result.returncode, 0, result.stderr)
            timeline = json.loads(output.read_text(encoding="utf-8"))
            indexes = [line['index'] for line in timeline['lines']]
            self.assertTrue(all(index > 0 for index in indexes))
            self.assertEqual(len(indexes), len(set(indexes)))
            checks = check_timeline(timeline, "用户歌词", 6.0)
            self.assertTrue(next(check['pass'] for check in checks
                                 if check['item'] == '歌词索引唯一且时间正向有限'))

    def test_gate6_rejects_contiguous_windows_shifted_off_song(self):
        timeline = {"duration_sec": 10.0, "lines": [
            {"index": 1, "type": "vocal", "start": 0.0, "end": 5.0},
            {"index": 2, "type": "vocal", "start": 5.0, "end": 10.0},
        ]}
        storyboard = {"shots": [
            {"id": "SH001", "window": {"start": 1.0, "end": 6.0},
             "lip_sync": True, "shot_type": "特写"},
            {"id": "SH002", "window": {"start": 6.0, "end": 11.0},
             "lip_sync": False, "shot_type": "空镜"},
        ]}
        checks = check_storyboard(storyboard, timeline)
        endpoint_check = next(c for c in checks if c["item"].startswith("窗口首尾相接"))
        self.assertFalse(endpoint_check["pass"])

    @unittest.skipUnless(shutil.which("ffprobe"), "需要 ffprobe")
    def test_gate1_compares_declared_duration_with_audio(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "state").mkdir()
            wav_path = project / "input" / "song.wav"
            write_wav(wav_path, 1.0)
            (project / "input" / "lyrics.txt").write_text("甲", encoding="utf-8")
            timeline = {
                "duration_sec": 2.0, "source_audio": "input/song.wav",
                "lyrics_source": "official", "asr_engine": "test", "no_interlude": True,
                "pending_review": [], "lines": [
                    {"index": 1, "type": "vocal", "text": "甲", "start": 0.0,
                     "end": 1.0, "confidence": 1.0},
                ],
            }
            (project / "state" / "timeline.json").write_text(json.dumps(timeline), encoding="utf-8")
            result = self.run_script("gate_checks.py", "gate1", project)
            self.assertEqual(result.returncode, 1)
            report = json.loads(result.stdout)
            duration_check = next(c for c in report["checks"] if c["item"].startswith("全曲时长"))
            self.assertFalse(duration_check["pass"])

    def test_gate5_rejects_missing_prompt_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "state").mkdir()
            (project / "input").mkdir()
            (project / "prompts").mkdir()
            timeline = {"duration_sec": 2.0, "lines": [
                {"index": 1, "type": "vocal", "text": "甲", "start": 0.0, "end": 1.0},
                {"index": 2, "type": "vocal", "text": "乙", "start": 1.0, "end": 2.0},
            ]}
            shots = [
                {"id": "SH001", "window": {"start": 0.0, "end": 1.0},
                 "shot_type": "特写", "lip_sync": True},
                {"id": "SH002", "window": {"start": 1.0, "end": 2.0},
                 "shot_type": "空镜", "lip_sync": False},
            ]
            (project / "state" / "timeline.json").write_text(json.dumps(timeline), encoding="utf-8")
            (project / "state" / "style.json").write_text(
                json.dumps({"fingerprint": "sha256:style"}), encoding="utf-8")
            (project / "state" / "storyboard.json").write_text(
                json.dumps({"shots": shots}), encoding="utf-8")
            prompt = {
                "meta": {"shot_id": "SH001", "window": shots[0]["window"],
                         "duration_sec": 1.0, "shot_type": "特写", "lip_sync": True,
                         "lyric_line": 1, "lyric_text": "甲",
                         "style_fingerprint": "sha256:style", "reference_duties": ["none"]},
                "style": "风格", "scene": "场景", "subject": "人物",
                "performance": "人物抬头凝望\n甲", "camera": "缓慢推近",
                "audio_policy": "人声驱动口型", "negative_prompt": "水印",
            }
            (project / "prompts" / "SH001.json").write_text(json.dumps(prompt), encoding="utf-8")

            result = self.run_script("gate_checks.py", "gate5", project)
            self.assertEqual(result.returncode, 1)
            report = json.loads(result.stdout)
            self.assertTrue(any(not item["pass"] for item in report["checks"]))

    def test_prompt_build_rejects_timing_only_storyboard(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as tmp:
            project = Path(tmp)
            (project / "state").mkdir()
            (project / "input").mkdir()
            (project / "state" / "timeline.json").write_text(
                json.dumps({"duration_sec": 1.0, "lines": []}), encoding="utf-8")
            (project / "state" / "style.json").write_text(
                json.dumps({"fingerprint": "sha256:style"}), encoding="utf-8")
            (project / "state" / "storyboard.json").write_text(
                json.dumps({"story_mode": "timing_only", "shots": []}), encoding="utf-8")

            result = self.run_script("prompt_build.py", project)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("ZY Cinematic Realism", result.stderr)

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "需要 ffmpeg")
    def test_duration_fit_copy_path_removes_audio(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.mp4"
            output = Path(tmp) / "nested" / "fitted.mp4"
            make = subprocess.run([
                "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                "color=c=black:s=64x64:r=24:d=1", "-f", "lavfi", "-i",
                "sine=frequency=440:sample_rate=16000:duration=1", "-shortest",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(source),
            ], capture_output=True, text=True)
            self.assertEqual(make.returncode, 0, make.stderr)

            result = self.run_script("duration_fit.py", source, output, 1.0, "--fps", 24)
            self.assertEqual(result.returncode, 0, result.stderr)
            info = json.loads(result.stdout)
            self.assertEqual(info["mode"], "copy")
            probe = subprocess.run([
                "ffprobe", "-v", "error", "-select_streams", "a",
                "-show_entries", "stream=index", "-of", "csv=p=0", str(output),
            ], capture_output=True, text=True)
            self.assertEqual(probe.stdout.strip(), "")

    @unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "需要 ffmpeg")
    def test_preview_supports_wav_and_no_image(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "state").mkdir()
            wav_path = project / "input" / "master.wav"
            write_wav(wav_path, 1.0)
            timeline = {"duration_sec": 1.0, "source_audio": "input/master.wav",
                        "master_audio": "input/master.wav", "lines": [
                {"index": 1, "type": "vocal", "text": "甲", "start": 0.0, "end": 0.5},
                {"index": 2, "type": "vocal", "text": "乙", "start": 0.5, "end": 1.0},
            ]}
            storyboard = {"shots": [
                {"id": "SH001", "window": {"start": 0.0, "end": 0.5},
                 "shot_type": "特写", "lip_sync": True},
                {"id": "SH002", "window": {"start": 0.5, "end": 1.0},
                 "shot_type": "空镜", "lip_sync": False},
            ]}
            (project / "state" / "timeline.json").write_text(json.dumps(timeline), encoding="utf-8")
            (project / "state" / "storyboard.json").write_text(json.dumps(storyboard), encoding="utf-8")

            result = self.run_script("render_preview.py", project, "--width", 64, "--height", 64)
            self.assertEqual(result.returncode, 0, result.stderr)
            output = json.loads(result.stdout.splitlines()[-1])
            self.assertTrue(Path(output["final"]).is_file())
            self.assertEqual(output["verification"], "PASS")
            report_path = Path(output["verification_report"])
            self.assertTrue(report_path.is_file())
            self.assertEqual(json.loads(report_path.read_text(encoding="utf-8"))["mode"], "candidate")


if __name__ == "__main__":
    unittest.main()
