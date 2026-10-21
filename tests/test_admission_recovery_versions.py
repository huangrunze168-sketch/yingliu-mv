"""准入读写竞态与损坏恢复日志不能放行或部分修改文件。"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from scripts.chapter_admission import require
from scripts.pipeline_contracts import digest, recover_transaction


class AdmissionRecoveryVersions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        (self.project / 'state/reviews').mkdir(parents=True)

    def put(self, path, value):
        path.write_text(json.dumps(value), encoding='utf-8')

    def admission(self, mutate=False):
        media = self.project / 'clip.mp4'
        media.write_bytes('用于准入版本单元测试的占位文件'.encode('utf-8'))
        review = self.project / 'state/reviews/CH01.json'
        self.put(review, {'decision': 'approve', 'reviewed_media': 'clip.mp4',
                          'reviewed_media_sha256': digest(media)})
        self.put(self.project / 'state/chapter_plan.json',
                 {'chapters': [{'id': 'CH01', 'shot_ids': ['S1']}]})
        def validate(*args):
            if mutate:
                data = json.loads(review.read_text())
                data['decision'] = 'pending'
                self.put(review, data)
            return True, []
        # 本组隔离审片版本竞态；实际媒体来源链由MediaOrigin和ProductionContracts覆盖。
        with patch('scripts.chapter_admission.approved', side_effect=validate), \
                patch('scripts.media_origin.require', return_value={}):
            return require(self.project, {'shots': [{'id': 'S1'}]}, {'C1': {
                'path': 'clip.mp4', 'sha256': digest(media), 'chapter_id': 'CH01',
                'shot_ids': ['S1']}}, [{'shot_id': 'S1', 'clip_id': 'C1'}])

    def test_changed_review_after_validation_is_blocked(self):
        with self.assertRaisesRegex(ValueError, '版本'):
            self.admission(mutate=True)

    def test_unchanged_review_returns_exact_version(self):
        result = self.admission()
        self.assertEqual(result['CH01'], digest(self.project / 'state/reviews/CH01.json'))

    def recovery_case(self, second, stages=None):
        first = self.project / 'first.txt'
        first.write_bytes(b'current')
        journal = self.project / '.pipeline-transaction.json'
        self.put(journal, {'originals': {str(first): b'old'.hex(), **second},
                           'stages': stages or {}})
        with self.assertRaises((ValueError, TypeError)):
            recover_transaction(journal)
        self.assertEqual(first.read_bytes(), b'current')
        self.assertTrue(journal.exists())

    def test_escape_in_later_target_does_not_partially_restore(self):
        self.recovery_case({str(self.project.parent / 'outside.txt'): '00'})

    def test_invalid_later_payload_does_not_partially_restore(self):
        self.recovery_case({str(self.project / 'second.txt'): 'not hex'})

    def test_stage_cannot_delete_normal_media(self):
        media = self.project / 'master.wav'
        media.write_bytes(b'original master')
        self.recovery_case({}, {str(self.project / 'first.txt'): str(media)})
        self.assertEqual(media.read_bytes(), b'original master')

    def test_valid_recovery_restores_and_cleans_stage(self):
        first = self.project / 'first.txt'
        first.write_bytes(b'current')
        stage = self.project / '.pipeline-test'
        stage.write_bytes(b'new')
        journal = self.project / '.pipeline-transaction.json'
        self.put(journal, {'originals': {str(first): b'old'.hex()},
                           'stages': {str(first): str(stage)}})
        recover_transaction(journal)
        self.assertEqual(first.read_bytes(), b'old')
        self.assertFalse(stage.exists())
        self.assertFalse(journal.exists())
