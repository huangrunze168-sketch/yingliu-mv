"""导演审批更新版本冲突必须保留新记录，刷新不能继承审片事实。"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from scripts import director_review_gate as review
from scripts.pipeline_contracts import atomic_batch, RevisionConflict


class DirectorReviewVersions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        (self.project / 'state').mkdir()
        self.path = self.project / 'state/director_review.json'
        plan = {'production_policy': {'prototype_chapter': 'CH01'},
                'chapters': [{'id': 'CH01', 'duration_sec': 4}]}
        (self.project / 'state/chapter_plan.json').write_text(json.dumps(plan), encoding='utf-8')
        self.old = review.initial_review(plan)
        self.old.update(decision='approve', watched_full_duration=True, batch_generation_allowed=True)
        self.old['timecoded_findings'] = [{'observation': '旧证据'}]
        self.old['director_checks']['editing'] = {'status': 'pass', 'evidence': '旧剪辑证据'}

    def put(self, data):
        self.path.write_text(json.dumps(data), encoding='utf-8')

    def test_invalidate_archives_exact_bytes_and_clears_watch(self):
        self.put(self.old)
        before = self.path.read_bytes()
        result = review.update_review(self.project, 'invalidate')
        self.assertEqual(result['decision'], 'pending')
        self.assertFalse(result['watched_full_duration'])
        self.assertFalse(result['batch_generation_allowed'])
        self.assertEqual(next((self.path.parent / 'history').glob('*.json')).read_bytes(), before)

    def test_refresh_resets_all_review_evidence(self):
        self.put(self.old)
        review.update_review(self.project, 'invalidate')
        with patch.object(review, 'dependency_snapshot', return_value={'new': 'sha256:new'}):
            result = review.update_review(self.project, 'refresh')
        self.assertFalse(result['watched_full_duration'])
        self.assertEqual(result['timecoded_findings'], [])
        self.assertEqual(result['reviewed_media'], '')
        for item in [*result['director_checks'].values(), *result['sequence_checks'].values()]:
            self.assertEqual(item, {'status': 'pending', 'evidence': ''})
        self.assertEqual(len(list((self.path.parent / 'history').glob('*.json'))), 2)

    def race(self, action, previous):
        if previous is not None: self.put(previous)
        newer = {**self.old, 'newer_version': True}
        def intervening(updates, **kwargs):
            self.put(newer)
            return atomic_batch(updates, **kwargs)
        with patch.object(review, 'dependency_snapshot', return_value={'test': 'sha256:test'}), \
                patch.object(review, 'atomic_batch', side_effect=intervening):
            with self.assertRaises(RevisionConflict):
                review.update_review(self.project, action)
        self.assertEqual(json.loads(self.path.read_text(encoding='utf-8')), newer)
        self.assertFalse(list((self.path.parent / 'history').glob('*.json')))

    def test_init_cannot_overwrite_concurrent_record(self):
        self.race('init', None)

    def test_invalidate_cannot_overwrite_new_approval(self):
        self.race('invalidate', self.old)

    def test_refresh_cannot_overwrite_new_approval(self):
        self.race('refresh', {**self.old, 'decision': 'pending'})

    def test_refresh_requires_pending_and_init_rejects_existing(self):
        for decision in ('approve', 'reject', 'unknown'):
            self.put({**self.old, 'decision': decision})
            before = self.path.read_bytes()
            with self.assertRaises(ValueError): review.update_review(self.project, 'refresh')
            with self.assertRaises(FileExistsError): review.update_review(self.project, 'init')
            self.assertEqual(self.path.read_bytes(), before)

    def test_chapter_updates_use_independent_records(self):
        self.put(self.old)
        before = self.path.read_bytes()
        with patch.object(review, 'dependency_snapshot', return_value={'chapter': 'sha256:test'}):
            review.update_review(self.project, 'init', 'CH01')
        self.assertTrue((self.project / 'state/reviews/CH01.json').is_file())
        self.assertEqual(self.path.read_bytes(), before)

    def test_invalid_chapter_path_is_blocked(self):
        with self.assertRaises(ValueError):
            review.update_review(self.project, 'init', '../escape')
