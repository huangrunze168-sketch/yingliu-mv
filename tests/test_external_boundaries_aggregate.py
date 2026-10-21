"""外部语料未验收不能升级生产；逐章通过不能掩盖汇总中的撤销。"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from scripts import director_review_gate as director
from scripts.film_grammar_corpus import build_profile, audit_storyboard
from scripts.pipeline_contracts import digest


class ExternalBoundariesAggregate(unittest.TestCase):
    def test_count_threshold_does_not_certify_real_corpus(self):
        rows = [{'source_id': 'S', 'film_id': f'F{i}', 'scene_id': 'scene',
                 'shot_index': 0, 'duration_sec': 2, 'shot_size': 'medium',
                 'camera_movement': 'static'} for i in range(200)]
        manifest = {'schema_version': 1, 'media_policy': 'annotations_only', 'sources': [
            {'id': 'S', 'title': '虚构单元测试', 'url': 'https://example.invalid',
             'license_or_terms': '未经外部验证的声明', 'access_scope': 'test'}]}
        profile = build_profile(rows, manifest, profile_id='synthetic', min_films=200)
        self.assertTrue(profile['production_validation']['sample_count_threshold_met'])
        self.assertEqual(profile['status'], 'exploratory_only')
        self.assertEqual(profile['production_validation']['status'], 'unverified')

    def test_self_declared_production_cannot_enable_hard_gate(self):
        profile = {'schema_version': 1, 'status': 'production_reference',
                   'production_validation': {'status': 'verified'},
                   'scene_mean_duration_sec': {'p10': 1, 'p90': 3}}
        sb = {'shots': [{'id': 'S1', 'scene_id': 'A', 'window': {'start': 0, 'end': 2}}]}
        self.assertEqual(audit_storyboard(profile, sb, mode='enforce_envelope')['status'], 'blocked')
        self.assertNotEqual(audit_storyboard(profile, sb)['status'], 'blocked')

    def aggregate(self, change=None):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            reviews = project / 'state/reviews'
            reviews.mkdir(parents=True)
            plan_path = project / 'state/chapter_plan.json'
            plan = {'chapters': [{'id': 'CH01'}, {'id': 'CH02'}]}
            plan_path.write_text(json.dumps(plan), encoding='utf-8')
            for cid in ('CH01', 'CH02', 'full_sequence'):
                media = project / f'{cid}.mp4'
                media.write_bytes(b'isolated aggregate media fixture')
                record = {'decision': 'approve', 'reviewed_media': media.name,
                          'reviewed_media_sha256': digest(media)}
                (reviews / f'{cid}.json').write_text(json.dumps(record), encoding='utf-8')
            if change == 'wrong_hash':
                (project / 'CH01.mp4').write_bytes(b'pre-existing replacement')
            if change == 'missing_media':
                record = json.loads((reviews / 'CH01.json').read_text())
                record.pop('reviewed_media')
                (reviews / 'CH01.json').write_text(json.dumps(record))
            if change == 'path_id':
                plan_path.write_text(json.dumps({'chapters': [{'id': '../escape'}]}))
            source = project / 'source.txt'
            source.write_bytes(b'original')
            def snapshot(*args): return {str(source): digest(source)}
            def approved(*args):
                if args[1] == 'CH02':
                    if change == 'revoke': (reviews / 'CH01.json').write_text('{"decision":"pending"}')
                    if change == 'delete': (reviews / 'CH01.json').unlink()
                    if change == 'plan': plan_path.write_text(json.dumps({'chapters': [{'id': 'CH01'}]}))
                    if change == 'source': source.write_bytes(b'new')
                    if change == 'media': (project / 'CH01.mp4').write_bytes(b'replaced reviewed media')
                    if change == 'delete_media': (project / 'CH01.mp4').unlink()
                    if change == 'full_media': (project / 'full_sequence.mp4').write_bytes(b'new full review')
                return True, []
            with patch.object(director, 'approved', side_effect=approved), \
                    patch.object(director, 'dependency_snapshot', side_effect=snapshot):
                result, errors = director.delivery_approved(project)
            self.assertEqual(result, change is None, errors)
            if change == 'revoke':
                self.assertEqual(json.loads((reviews / 'CH01.json').read_text())['decision'], 'pending')

    def test_stable_aggregate_passes(self): self.aggregate()
    def test_later_check_cannot_hide_earlier_revocation(self): self.aggregate('revoke')
    def test_later_check_cannot_hide_earlier_deletion(self): self.aggregate('delete')
    def test_plan_changed_during_aggregate_is_blocked(self): self.aggregate('plan')
    def test_dependency_changed_during_aggregate_is_blocked(self): self.aggregate('source')
    def test_earlier_media_changed_during_later_review_is_blocked(self): self.aggregate('media')
    def test_earlier_media_deleted_during_later_review_is_blocked(self): self.aggregate('delete_media')
    def test_full_review_media_changed_is_blocked(self): self.aggregate('full_media')
    def test_initial_media_hash_mismatch_is_blocked(self): self.aggregate('wrong_hash')
    def test_missing_reviewed_media_is_blocked(self): self.aggregate('missing_media')
    def test_path_chapter_id_is_blocked(self): self.aggregate('path_id')
    def test_read_entry_rejects_path_before_reading(self):
        with patch.object(Path, 'read_bytes', side_effect=AssertionError('不应读取外部记录')):
            ok, errors = director.approved(Path('unused-project'), '../escape')
        self.assertFalse(ok)
        self.assertTrue(any('章节ID' in error for error in errors))
