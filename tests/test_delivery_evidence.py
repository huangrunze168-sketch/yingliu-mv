"""实际本地媒体、权威LRC及最终复核契约；观察内容为明确测试夹具。"""
import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from test_visual_workflow import fixture, write
from test_production_contracts import ffmpeg
from evidence_fixtures import write_timing_fixture
from scripts import delivery_evidence as evidence, delivery_package as package
from scripts.pipeline_contracts import digest
from scripts.storyboard_build import build


class DeliveryEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name).resolve()
        self.timeline, *_ = fixture(self.project)
        self.sb = build(self.project)
        (self.project / 'input').mkdir()
        (self.project / 'output').mkdir()
        self.audio = self.project / 'input/song.wav'
        ffmpeg('-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=4', '-ac', 2, self.audio)
        self.timeline.update(master_audio='input/song.wav', master_audio_sha256=digest(self.audio),
                             no_interlude=True, asr_engine='synthetic_fixture_only')
        (self.project / 'input/lyrics.txt').write_text('\n'.join(r['text'] for r in self.timeline['lines']), encoding='utf8')
        write_timing_fixture(self.project, self.timeline)
        self.video = self.project / 'output/final_mv.mp4'
        ffmpeg('-f', 'lavfi', '-i', 'color=black:s=160x90:r=24:d=4', '-i', self.audio,
               '-c:v', 'libx264', '-c:a', 'alac', self.video)
        self.review_path = self.project / 'state/final_review.json'

    def checked_fixture(self):
        value = evidence.review_template(self.project, 'final_mv.mp4')
        value.update(observer='单元测试复核声明', watched_full_audio_video=True)
        for row in value['shots']:
            row.update(performance_status='pass', sound_action_status='pass', continuity_status='pass',
                       mouth_status='pass' if row['lip_sync_required'] else 'not_applicable', observation='测试观察')
            if row['lip_sync_required']:
                start, end = row['window']['start'], row['window']['end']
                row['mouth_samples'] = [{'time_sec': start, 'observation': '测试开口'},
                                        {'time_sec': (start+end)/2, 'observation': '测试闭口'}]
        write(self.review_path, value)
        return value

    def test_lrc_has_exact_official_text_and_timing(self):
        value = evidence.lrc(self.project)
        self.assertEqual(len(value.splitlines()), len(self.timeline['lines']))
        self.assertTrue(value.startswith('[00:00.00]'))
        for row, text in zip(self.timeline['lines'], value.splitlines()):
            self.assertEqual(text.split(']', 1)[1], row['text'])

    def test_pending_timing_and_changed_official_lyrics_block_lrc(self):
        tl_path = self.project / 'state/timeline.json'
        tl_path.write_text(json.dumps({**self.timeline, 'production_timing_verified': False}), encoding='utf8')
        with self.assertRaises(ValueError): evidence.lrc(self.project)
        write_timing_fixture(self.project, self.timeline)
        (self.project / 'input/lyrics.txt').write_text('替换官方歌词', encoding='utf8')
        with self.assertRaises(ValueError): evidence.lrc(self.project)

    def test_pending_review_never_means_watched(self):
        template = evidence.review_template(self.project, 'final_mv.mp4')
        self.assertFalse(template['watched_full_audio_video'])
        write(self.review_path, template)
        with self.assertRaises(ValueError): evidence.require_review(self.project, 'final_mv.mp4', self.review_path)

    def test_changed_video_or_scope_invalidates_final_review(self):
        value = self.checked_fixture()
        evidence.require_review(self.project, 'final_mv.mp4', self.review_path)
        value['scope'] = 'independently_verified_lips'
        write(self.review_path, value)
        with self.assertRaises(ValueError): evidence.require_review(self.project, 'final_mv.mp4', self.review_path)
        self.checked_fixture()
        self.video.write_bytes(self.video.read_bytes() + b'changed')
        with self.assertRaises(ValueError): evidence.require_review(self.project, 'final_mv.mp4', self.review_path)

    def test_lip_shot_needs_timecoded_mouth_observations(self):
        self.sb['shots'][0]['lip_sync'] = True
        write(self.project / 'state/storyboard.json', self.sb)
        value = self.checked_fixture()
        value['shots'][0]['mouth_samples'] = []
        write(self.review_path, value)
        with self.assertRaises(ValueError): evidence.require_review(self.project, 'final_mv.mp4', self.review_path)
        value = self.checked_fixture()
        value['shots'][0]['mouth_samples'][0]['time_sec'] = 99
        write(self.review_path, value)
        with self.assertRaises(ValueError): evidence.require_review(self.project, 'final_mv.mp4', self.review_path)

    def test_actual_alignment_extracts_candidate_track_and_does_not_verify_lips(self):
        directory = self.project / 'analysis/align-run'
        report = evidence.run_alignment(self.project, 'final_mv.mp4', directory)
        self.assertEqual(report['overall'], 'PASS')
        self.assertTrue(report['candidate_audio_bound_to_video'])
        self.assertFalse(report['lip_sync_verified'])
        receipt = evidence.load(directory / 'alignment_execution.json')
        self.assertEqual(receipt['status'], 'completed')
        self.assertEqual(len(receipt['commands']), 3)
        self.assertTrue(all(r['returncode'] == 0 for r in receipt['process_outputs']))
        self.assertEqual(receipt['report_sha256'], digest(directory / 'alignment.json'))
        self.assertTrue(all(r['method'] == 'exact_pcm_samples' for r in report['check2_waveform_lag']))

    def test_silent_candidate_cannot_substitute_source_audio(self):
        silent = self.project / 'output/silent.mp4'
        ffmpeg('-i', self.video, '-map', '0:v:0', '-c:v', 'copy', '-an', silent)
        directory = self.project / 'analysis/failed-align'
        with self.assertRaises(ValueError): evidence.run_alignment(self.project, 'silent.mp4', directory)
        receipt = evidence.load(directory / 'alignment_execution.json')
        self.assertEqual(receipt['status'], 'failed')
        self.assertNotEqual(receipt['process_outputs'][-1]['returncode'], 0)

    def test_embedded_variant_requires_real_source_audio(self):
        silent = self.project / 'silent.mp4'
        ffmpeg('-i', self.video, '-map', '0:v:0', '-c:v', 'copy', '-an', silent)
        write(self.project / 'state/edit_assembly.json', {'entries': [{'source_path': 'silent.mp4',
              'source_sha256': digest(silent), 'in_frame': 0, 'fps': 24, 'frames': 96, 'shot_id': 'SH001'}]})
        with self.assertRaisesRegex(ValueError, '内嵌音轨'):
            package.embedded_audio(self.project, self.video, self.project / 'fake.mp4', self.project)

    def test_tool_start_failure_is_recorded_as_failed_not_running(self):
        directory = self.project / 'analysis/tool-start-failed'
        with patch.object(evidence.subprocess, 'run', side_effect=FileNotFoundError('isolated ffmpeg startup failure')):
            with self.assertRaises(FileNotFoundError):
                evidence.run_alignment(self.project, 'final_mv.mp4', directory)
        receipt = evidence.load(directory / 'alignment_execution.json')
        self.assertEqual(receipt['status'], 'failed')
        self.assertEqual(receipt['process_outputs'][0]['error_type'], 'FileNotFoundError')
        self.assertIsNone(receipt['process_outputs'][0]['returncode'])
        self.assertTrue(receipt['completed_at'])
