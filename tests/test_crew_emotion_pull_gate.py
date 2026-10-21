#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""剧组拉片情绪维度接入 director_review_gate。"""
import unittest

from scripts.director_review_gate import (
    DIMENSIONS,
    REVIEW_SCHEMA_VERSION,
    initial_review,
    requires_crew_emotion_pull,
    validate_crew_emotion_pull,
    validate_review,
)


def _base_plan(duration=119.04):
    return {
        "production_policy": {"prototype_chapter": "full_sequence"},
        "chapters": [{
            "id": "full_sequence",
            "duration_sec": duration,
            "shot_deltas": [{"lip_sync": True}],
        }],
    }


def _pass_review(plan, duration=119.04):
    review = initial_review(plan)
    review.update(
        reviewed_media="deliverables/cage-mv-119s-v10.mp4",
        watched_full_duration=True,
        decision="approve",
        batch_generation_allowed=True,
    )
    review["technical_checks"].update(
        container_and_duration="pass", audio_alignment="pass",
    )
    for field in ("director_checks", "sequence_checks"):
        for item in review[field].values():
            item.update(status="pass", evidence="可观察证据")
    review["timecoded_findings"] = [{
        "start_sec": 0.0, "end_sec": min(4.0, duration),
        "dimension": "editing", "observation": "时间线可审",
    }]
    return review


class CrewEmotionPullGateTests(unittest.TestCase):
    def test_dimension_registered(self):
        self.assertIn("song_emotion_match", DIMENSIONS)

    def test_new_review_uses_v3_and_requires_pull(self):
        review = initial_review(_base_plan())
        self.assertEqual(review["schema_version"], REVIEW_SCHEMA_VERSION)
        self.assertTrue(requires_crew_emotion_pull(review))

    def test_missing_pull_blocks(self):
        errors = validate_crew_emotion_pull({})
        self.assertTrue(any("crew_emotion_pull" in e for e in errors))

    def test_legacy_v2_without_new_fields_remains_compatible(self):
        plan = _base_plan(4.0)
        plan["chapters"][0]["duration_sec"] = 4.0
        review = _pass_review(plan, 4.0)
        review["schema_version"] = 2
        review["director_checks"].pop("song_emotion_match")

        self.assertFalse(requires_crew_emotion_pull(review))
        self.assertEqual(validate_review(review, plan), [])

    def test_v3_without_new_fields_is_blocked(self):
        plan = _base_plan(4.0)
        plan["chapters"][0]["duration_sec"] = 4.0
        review = _pass_review(plan, 4.0)
        review["director_checks"].pop("song_emotion_match")

        errors = validate_review(review, plan)
        self.assertTrue(any("song_emotion_match" in error for error in errors), errors)
        self.assertTrue(any("crew_emotion_pull" in error for error in errors), errors)

    def test_legacy_schema_cannot_bypass_production_emotion_gate(self):
        plan = _base_plan(4.0)
        review = _pass_review(plan, 4.0)
        review['schema_version'] = 2
        review['director_checks'].pop('song_emotion_match')
        errors = validate_review(review, plan, production=True)
        self.assertTrue(any('song_emotion_match' in e for e in errors), errors)
        self.assertTrue(any('crew_emotion_pull' in e for e in errors), errors)

    def test_partially_migrated_v2_still_enforces_new_gate(self):
        plan = _base_plan(4.0)
        plan["chapters"][0]["duration_sec"] = 4.0
        review = _pass_review(plan, 4.0)
        review["schema_version"] = 2

        self.assertTrue(requires_crew_emotion_pull(review))
        self.assertTrue(any("crew_emotion_pull" in error
                            for error in validate_review(review, plan)))

    def test_v10_regen_segments_block_approve(self):
        """对标《笼》v10 拉片：60–90 / 90–109 P0 REGEN，52–60 P1 PARTIAL 须重生。"""
        plan = _base_plan()
        review = _pass_review(plan)
        review["crew_emotion_pull"] = {
            "source": "lovart/out/v10-crew-pull/CREW_PULL_REPORT.md",
            "segments": [
                {"start_sec": 0.0, "end_sec": 33.0, "verdict": "KEEP",
                 "reason": "坠入/笼压成立"},
                {"start_sec": 33.0, "end_sec": 45.0, "verdict": "KEEP",
                 "reason": "起唱肖像挂钩对"},
                {"start_sec": 45.0, "end_sec": 52.0, "verdict": "KEEP",
                 "reason": "笼景母题展示好"},
                {"start_sec": 52.0, "end_sec": 60.0, "verdict": "PARTIAL",
                 "priority": "P1", "regen_required": True,
                 "reason": "识破/泡沫破几乎未见"},
                {"start_sec": 60.0, "end_sec": 90.0, "verdict": "REGEN",
                 "priority": "P0",
                 "reason": "缺悬崖假自由→回笼，水下栏中打转"},
                {"start_sec": 90.0, "end_sec": 109.4, "verdict": "REGEN",
                 "priority": "P0",
                 "reason": "缺烛厅暖冷空间+双笼并置"},
                {"start_sec": 109.4, "end_sec": 119.04, "verdict": "KEEP",
                 "reason": "近脸收束+余韵可留"},
            ],
        }
        errors = validate_review(review, plan)
        joined = "\n".join(errors)
        self.assertIn("60–90s", joined)
        self.assertIn("90–109.4s", joined)
        self.assertIn("52–60s", joined)
        self.assertTrue(any("须重生" in e for e in errors), errors)
        self.assertTrue(any("song_emotion_match 不能为 pass" in e for e in errors), errors)
        self.assertNotEqual(errors, [])

    def test_all_keep_allows_emotion_dimension(self):
        plan = _base_plan(4.0)
        plan["chapters"][0]["id"] = "CH01"
        plan["production_policy"]["prototype_chapter"] = "CH01"
        plan["chapters"][0]["duration_sec"] = 4.0
        review = _pass_review(plan, 4.0)
        review["crew_emotion_pull"] = {
            "source": "unit-test",
            "segments": [{"start_sec": 0.0, "end_sec": 4.0, "verdict": "KEEP",
                          "reason": "情绪与母题匹配"}],
        }
        errors = validate_review(review, plan)
        self.assertFalse(any("歌曲情绪不合" in e for e in errors), errors)
        self.assertFalse(any("crew_emotion_pull" in e for e in errors), errors)


if __name__ == "__main__":
    unittest.main()
