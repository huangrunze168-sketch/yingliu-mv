"""技术预览失败或版本冲突，不得覆盖旧媒体及旧验收报告。"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from scripts.stitch_chapters import publish_preview
from scripts.pipeline_contracts import digest, RevisionConflict
from scripts.transition_render import render_diegetic_audio


class PreviewPublishTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        (self.project / 'state').mkdir()
        (self.project / 'output').mkdir()
        self.output = self.project / 'output/technical_preview.mp4'
        self.report = self.output.with_suffix('.verification.json')
        self.output.write_bytes(b'previous success')
        self.report.write_bytes(b'previous report')
        self.staged = self.project / 'new.mp4'
        self.staged.write_bytes(b'new staged media')
        self.source = self.project / 'source.mp4'
        self.source.write_bytes(b'old source')
        self.expected = {path: digest(path) for path in (self.output, self.report, self.source)}
        self.verification = {'status': 'pass', 'errors': []}

    def preserved(self):
        self.assertEqual(self.output.read_bytes(), b'previous success')
        self.assertEqual(self.report.read_bytes(), b'previous report')

    def test_failed_verification_preserves_old_media_and_report(self):
        with self.assertRaises(ValueError):
            publish_preview(self.project, self.staged, self.output, {'status': 'blocked', 'errors': ['帧数错误']}, self.expected)
        self.preserved()

    def test_input_change_rejects_preview_publish(self):
        self.source.write_bytes(b'new source')
        with self.assertRaises(RevisionConflict):
            publish_preview(self.project, self.staged, self.output, self.verification, self.expected)
        self.preserved()

    def test_newer_output_cannot_be_overwritten_by_old_task(self):
        self.output.write_bytes(b'newer task success')
        with self.assertRaises(RevisionConflict):
            publish_preview(self.project, self.staged, self.output, self.verification, self.expected)
        self.assertEqual(self.output.read_bytes(), b'newer task success')
        self.assertEqual(self.report.read_bytes(), b'previous report')

    def test_report_commit_failure_rolls_back_media(self):
        import os
        actual = os.replace
        calls = [0]
        def interrupted(*args):
            calls[0] += 1
            if calls[0] == 2: raise OSError('报告发布失败')
            return actual(*args)
        with patch('scripts.pipeline_contracts.os.replace', side_effect=interrupted):
            with self.assertRaises(OSError):
                publish_preview(self.project, self.staged, self.output, self.verification, self.expected)
        self.preserved()

    def test_success_publishes_media_and_report_together(self):
        publish_preview(self.project, self.staged, self.output, self.verification, self.expected)
        self.assertEqual(self.output.read_bytes(), b'new staged media')
        self.assertEqual(json.loads(self.report.read_text()), self.verification)

    def test_bool_channels_rejected_before_media_access(self):
        with self.assertRaises(ValueError):
            render_diegetic_audio([{}], self.project/'bad.wav', 24, channels=True)

    def test_straight_cannot_hide_nonzero_offset(self):
        with self.assertRaisesRegex(ValueError, 'straight'):
            render_diegetic_audio([{'frames': 48, 'audio_in_frame': 0,
                                    'audio_transition': {'type': 'straight', 'duration_frames': 12}}],
                                   self.project/'bad.wav', 24)

    def test_negative_audio_source_window_rejected(self):
        with self.assertRaises(ValueError):
            render_diegetic_audio([{'frames': 48, 'audio_in_frame': -1}], self.project/'bad.wav', 24)

    def test_J_cut_cannot_consume_previous_shot(self):
        with self.assertRaisesRegex(ValueError, 'J_cut'):
            render_diegetic_audio([{'frames': 48, 'audio_in_frame': 100,
                                    'audio_transition': {'type': 'J_cut', 'duration_frames': 48}},
                                   {'frames': 48}], self.project/'bad.wav', 24)
