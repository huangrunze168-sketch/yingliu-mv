"""对审计反例验证真实生产入口及有效对照。"""
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from test_visual_workflow import fixture, write, shot_crew_fixture, crew_bible_fixture, PNG_BYTES
from scripts.storyboard_build import build
from scripts.visual_development import validate, require, fingerprint
from scripts.visual_assets import asset_errors
from test_visual_workflow import register
from scripts.crew_pipeline import validate_sequence_language
from scripts.submission_contract import validate_submission_contract as validate_submission_full

def validate_submission_structure_only(contract):
    """仅结构；全量校验见 validate_submission_full / test_production_contracts。"""
    return validate_submission_full(contract, structural_only=True)

# 历史别名：本文件内结构用例显式走 structure_only，避免掩盖全量门禁
def validate_submission_contract(contract):
    return validate_submission_structure_only(contract)
from scripts.director_review_gate import initial_review, validate_review
from scripts.chapter_prompt_build import build as build_chapters

ROOT = Path(__file__).resolve().parents[1]


class FiveGateFixTests(unittest.TestCase):
    def test_missing_profile_blocks_visual_gate5_and_compiler_entrypoints(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            project = Path(temp)
            _, _, _, development, style = fixture(project)
            storyboard = build(project)
            subprocess.run([sys.executable, str(ROOT / "scripts/prompt_build.py"), str(project)],
                           capture_output=True, check=True)
            development["grammar_reference"] = {
                "profile_path": "state/profile.json", "profile_sha256": "sha256:missing",
                "profile_id": "drama", "mode": "enforce_envelope",
                "rationale": "剧情类", "use_scope": ["editing"],
            }
            write(project / "state/development.json", development)
            self.assertTrue(any("项目目录" in e for e in validate(development, storyboard, style)))
            self.assertTrue(any("画像不存在" in e for e in validate(development, storyboard, style, project)))
            with self.assertRaisesRegex(ValueError, "画像不存在"):
                require(project, storyboard, style)
            for script, args in (("visual_development.py", []), ("prompt_build.py", []),
                                 ("gate_checks.py", ["gate5"])):
                result = subprocess.run([sys.executable, str(ROOT / "scripts" / script), *args,
                                         str(project)], capture_output=True, text=True, encoding="utf-8",
                                        env={**os.environ, "PYTHONIOENCODING": "utf-8"})
                self.assertNotEqual(result.returncode, 0, script)
                self.assertIn("画像不存在", result.stdout + result.stderr, script)
            write(project / "state/chapter_plan.json", {
                "source_storyboard_fingerprint": fingerprint(project / "state/storyboard.json")})
            with self.assertRaisesRegex(ValueError, "画像不存在"):
                build_chapters(project)

    def test_valid_profile_passes_and_modified_or_insufficient_profile_blocks(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            project = Path(temp)
            _, _, _, development, style = fixture(project)
            storyboard = build(project)
            path = project / "state/profile.json"
            profile = {"schema_version": 1, "profile_id": "drama",
                       "status": "production_reference", "duration_sec": {"p10": 1, "p90": 3},
                       "scene_mean_duration_sec": {"p10": 1, "p90": 3},
                       "scene_kind_envelopes": {"narrative": {"p10": 1, "p90": 3}},
                       "shot_size_transitions": {"全景": {"近景": 1}}}
            write(path, profile)
            development["grammar_reference"] = {
                "profile_path": "state/profile.json", "profile_sha256": fingerprint(path),
                "profile_id": "drama", "mode": "advisory",
                "rationale": "剧情类", "use_scope": ["editing"]}
            self.assertEqual(validate(development, storyboard, style, project), [])
            development['grammar_reference']['mode'] = 'enforce_envelope'
            self.assertTrue(any('生产语料认证未通过' in e for e in validate(development, storyboard, style, project)))
            profile['status'] = 'exploratory_only'
            write(path, profile)
            self.assertTrue(any('指纹' in e for e in validate(development, storyboard, style, project)))
            development['grammar_reference']['profile_sha256'] = fingerprint(path)
            self.assertTrue(any('生产语料认证未通过' in e for e in validate(development, storyboard, style, project)))
            development['grammar_reference']['mode'] = 'advisory'
            self.assertEqual(validate(development, storyboard, style, project), [])
            development.pop('grammar_reference')
            self.assertEqual(validate(development, storyboard, style, project), [])

    def test_global_lighting_change_invalidates_master_and_child(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            project = Path(temp)
            _, _, _, development, _ = fixture(project)
            build(project)
            image = project / "frame.png"
            image.write_bytes(PNG_BYTES)
            register(project, "M1", "scene_master", "gate", image, "静态观察")
            register(project, "SH001", "keyframe", "gate", image, "静态观察", "M1")
            assets = json.loads((project / "state/visual_assets.json").read_text(encoding="utf-8"))["assets"]
            self.assertEqual(asset_errors(project, assets["M1"], assets), [])
            development["crew_bible"]["lighting"]["contrast_arc"] = "改变全片对比与曝光"
            write(project / "state/development.json", development)
            for aid in ("M1", "SH001"):
                self.assertTrue(any("设计依赖" in e for e in asset_errors(project, assets[aid], assets)), aid)

    def test_circular_camera_angle_checks_both_directions_and_accepts_large_change(self):
        editing = crew_bible_fixture()["editing"]
        editing["pacing_policy"]["min_shot_size_variety_per_scene"] = 1
        music = {"tempo_class": "medium", "cues": [
            {"id": "downbeat", "time_sec": 0.0, "kind": "downbeat", "evidence": "起拍"},
            {"id": "accent", "time_sec": 2.0, "kind": "accent", "evidence": "重音"},
        ]}
        timeline = {"duration_sec": 4.0, "lines": []}
        for angles, blocked in (((179, -179), True), ((-179, 179), True), ((170, -140), False)):
            shots = []
            for index, angle in enumerate(angles):
                crew = shot_crew_fixture(index)
                crew["cinematography"].update(shot_size="medium", angle_deg=angle)
                crew["editing"]["music_cue"] = ("downbeat", "accent")[index]
                shots.append({"id": f"SH{index}", "scene_id": "gate", "crew": crew,
                              "window": {"start": index * 2, "end": index * 2 + 2}})
            errors = validate_sequence_language(
                shots, editing, music_structure=music, timeline=timeline, fps=24,
            )
            self.assertEqual(
                any("30 度" in e or "景别变化不能豁免" in e for e in errors), blocked, (angles, errors),
            )

    def test_submission_rejects_reversed_overlapping_missing_and_nonfinite_windows(self):
        contract = {"prompt": "参考 @图片1 构图", "reference_mode": "omni_reference",
                    "reference_bindings": [{"handle": "@图片1", "modality": "image",
                                            "source": {"asset_id": "frame"}, "intents": ["composition"]}],
                    "backend_capabilities": {"verified": True, "omni_reference": True,
                                             "multi_shot": True, "timestamp_control": True},
                    "director_cut_status": "approved", "edit_shots": [
                        {"id": "SH1", "start_sec": 0, "end_sec": 2, "cut_relation": "match"},
                        {"id": "SH2", "start_sec": 2, "end_sec": 4}]}
        self.assertEqual(validate_submission_contract(contract), [])
        for start, end in ((3, 2), (1, 4), (3, 4), (float("nan"), 4), (2, float("inf")), (None, 4)):
            altered = copy.deepcopy(contract)
            altered["edit_shots"][1].update(start_sec=start, end_sec=end)
            self.assertTrue(any("时间窗" in e for e in validate_submission_contract(altered)), (start, end))
        for shot in contract["edit_shots"]:
            shot["window"] = {"start": shot.pop("start_sec"), "end": shot.pop("end_sec")}
        self.assertEqual(validate_submission_contract(contract), [])

    def test_review_timecodes_are_chapter_relative_and_bounded(self):
        plan = {"production_policy": {"prototype_chapter": "CH1"}, "chapters": [{
            "id": "CH1", "window": {"start": 10, "end": 14}, "duration_sec": 4,
            "shot_deltas": [{"lip_sync": False}]}]}
        review = initial_review(plan)
        review.update(reviewed_media="clip.mp4", watched_full_duration=True,
                      decision="approve", batch_generation_allowed=True)
        review["technical_checks"].update(container_and_duration="pass", audio_alignment="pass")
        for checks in (review["director_checks"], review["sequence_checks"]):
            for item in checks.values():
                item.update(status="pass", evidence="观察证据")
        review["crew_emotion_pull"] = {
            "source": "unit-test",
            "segments": [{"start_sec": 0.0, "end_sec": 4.0, "verdict": "KEEP",
                          "reason": "单元测试：情绪段保留"}],
        }
        for start, end, blocked in ((0, 4, False), (999, 1000, True), (3, 5, True)):
            review["timecoded_findings"] = [{"start_sec": start, "end_sec": end,
                                            "dimension": "editing", "observation": "动作匹配"}]
            self.assertEqual(any("超出样片" in e for e in validate_review(review, plan)), blocked)
            if not blocked:
                self.assertEqual(validate_review(review, plan), [])


if __name__ == "__main__":
    unittest.main()


    def test_full_submission_requires_project_dir_not_structural_only(self):
        contract = {
            "reference_mode": "omni_reference", "director_cut_status": "approved",
            "prompt": "@hero walks", "subject_reference": {"required": False},
            "backend_capabilities": {"verified": True, "omni_reference": True,
                                     "timestamp_control": True, "multi_shot": True,
                                     "reference_limits": {"image": 1, "audio": 0, "video": 0},
                                     "max_duration_sec": 4, "resolutions": ["480p"]},
            "reference_bindings": [{
                "handle": "@hero", "modality": "image", "intents": ["subject"],
                "binding_status": "uploaded", "asset_id": "a1",
                "source": {"sha256": "sha256:abc"},
            }],
            "edit_shots": [{"id": "SH001", "start_sec": 0, "end_sec": 2, "cut_relation": "cut"}],
            "duration_sec": 2, "resolution": "480p",
            "estimated_cash_cny": 1, "cash_limit_cny": 10,
        }
        errors = validate_submission_full(contract, structural_only=False)
        self.assertTrue(any("project_dir" in e for e in errors), errors)
        structural = validate_submission_structure_only(contract)
        # 结构路径不应要求 project_dir
        self.assertFalse(any("project_dir" in e for e in structural), structural)
