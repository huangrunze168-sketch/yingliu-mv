"""隔离实测函数，确定性复现审片读取期间的修改与删除。"""
import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch
from scripts import director_review_gate as director, animatic_review as animatic
from scripts.pipeline_contracts import digest


class ReviewReadConsistency(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        (self.project / 'state').mkdir()
        self.media = self.project / 'review.mp4'
        self.media.write_bytes(b'isolated media probe fixture')
        self.source = self.project / 'source.txt'
        self.source.write_bytes(b'original dependency')
        self.plan = {'production_policy': {'prototype_chapter': 'CH01'},
                     'chapters': [{'id': 'CH01', 'duration_sec': 4}]}
        self.put(self.project / 'state/chapter_plan.json', self.plan)
        self.put(self.project / 'state/timeline.json', {'duration_sec': 4,
                 'master_audio': 'source.txt', 'production_timing_verified': True})

    def put(self, path, data):
        path.write_text(json.dumps(data), encoding='utf-8')

    def snapshot(self, *args):
        return {str(self.source): digest(self.source)}

    def run_review(self, kind, change=None):
        if kind == 'director':
            module = director
            path = self.project / 'state/director_review.json'
            record = director.initial_review(self.plan)
            record['technical_checks'].update(container_and_duration='pass', audio_alignment='pass')
            for key in ('director_checks', 'sequence_checks'):
                record[key] = {name: {'status': 'pass', 'evidence': '隔离测试证据'} for name in record[key]}
            record['timecoded_findings'] = [{'start_sec': 0, 'end_sec': 4,
                                            'dimension': 'editing', 'observation': '测试'}]
            record['crew_emotion_pull'] = {
                'source': 'unit-test',
                'segments': [{'start_sec': 0.0, 'end_sec': 4.0, 'verdict': 'KEEP',
                              'reason': '隔离测试：情绪保留'}],
            }
            record['batch_generation_allowed'] = True
        else:
            module = animatic
            path = self.project / 'state/animatic_review.json'
            record = {'project_id': str(self.project), 'stage': 'animatic',
                      'checks': {name: {'status': 'pass', 'evidence': '测试'} for name in animatic.DIMENSIONS},
                      'timecoded_findings': [{'dimension': name, 'start_sec': 0, 'end_sec': 4,
                                             'observation': '测试'} for name in animatic.DIMENSIONS]}
        record.update(decision='approve', watched_full_duration=True,
                      reviewed_media='review.mp4', reviewed_media_sha256=digest(self.media),
                      dependencies=self.snapshot())
        self.put(path, record)
        def probe(*args):
            if change == 'approval': self.put(path, {**record, 'decision': 'pending'})
            if change == 'media': self.media.write_bytes(b'replaced media')
            if change == 'dependency': self.source.write_bytes(b'changed dependency')
            if change == 'delete': path.unlink()
            return {'streams': [{'codec_type': 'video', 'duration': '4'}, {'codec_type': 'audio'}],
                    'format': {'duration': '4'}}
        with ExitStack() as stack:
            if kind == 'director':
                stack.enter_context(patch.object(director, 'dependency_snapshot', side_effect=self.snapshot))
                stack.enter_context(patch('scripts.media_verify.probe', side_effect=probe))
            else:
                stack.enter_context(patch.object(animatic, 'dependencies', side_effect=self.snapshot))
                stack.enter_context(patch.object(animatic, 'probe', side_effect=probe))
                stack.enter_context(patch.object(animatic, 'audio_hash', return_value='isolated-audio-hash'))
            ok, errors = module.approved(self.project)
        if change:
            self.assertFalse(ok, errors)
            if change == 'approval':
                self.assertEqual(json.loads(path.read_text())['decision'], 'pending')
        else:
            self.assertTrue(ok, errors)

    def test_director_stable_passes(self): self.run_review('director')
    def test_director_revoked_during_probe(self): self.run_review('director', 'approval')
    def test_director_media_replaced_during_probe(self): self.run_review('director', 'media')
    def test_director_dependency_changed_during_probe(self): self.run_review('director', 'dependency')
    def test_director_record_deleted_during_probe(self): self.run_review('director', 'delete')
    def test_animatic_stable_passes(self): self.run_review('animatic')
    def test_animatic_revoked_during_probe(self): self.run_review('animatic', 'approval')
    def test_animatic_media_replaced_during_probe(self): self.run_review('animatic', 'media')
    def test_animatic_dependency_changed_during_probe(self): self.run_review('animatic', 'dependency')
    def test_animatic_record_deleted_during_probe(self): self.run_review('animatic', 'delete')
