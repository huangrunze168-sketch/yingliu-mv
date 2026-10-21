"""剪辑失败、版本变更不能覆盖旧成功片段与清单。"""
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from test_visual_workflow import fixture, write
from test_production_contracts import ffmpeg, ROOT
from scripts.storyboard_build import build
from scripts.edit_assemble import build as assemble
from scripts.pipeline_contracts import digest, RevisionConflict


class EditRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        fixture(self.project)
        build(self.project)
        source = self.project / 'source.mp4'
        ffmpeg('-f', 'lavfi', '-i', 'testsrc2=size=160x90:rate=24:duration=5', '-c:v', 'libx264', source)
        write(self.project / 'state/generated_clips.json', {'clips': {'C1': {
            'path': 'source.mp4', 'sha256': digest(source), 'fps': 24, 'frame_count': 120,
            'scene_id': 'gate', 'status': 'reviewed', 'evidence': '技术测试素材'}}})
        self.selections = [{'shot_id': f'SH00{i+1}', 'clip_id': 'C1', 'in_frame': i*48,
                            'out_frame': (i+1)*48, 'edit_reason': '测试', 'state_check': '测试'} for i in range(2)]
        write(self.project / 'state/edit_selection.json', {'selections': self.selections})
        old = assemble(self.project, render=True, technical_preview=True)
        self.old_files = {self.project / e['target_path']: digest(self.project / e['target_path']) for e in old['entries']}
        self.manifest = self.project / 'state/edit_assembly.json'
        self.manifest_sha = digest(self.manifest)
        self.selections[0].update(in_frame=24, out_frame=72)
        write(self.project / 'state/edit_selection.json', {'selections': self.selections})

    def preserved(self):
        self.assertEqual(digest(self.manifest), self.manifest_sha)
        for path, sha in self.old_files.items(): self.assertEqual(digest(path), sha)

    def test_second_encoder_failure_preserves_old_outputs(self):
        actual = subprocess.run
        calls = [0]
        def interrupted(command, *args, **kwargs):
            if command[0] == 'ffmpeg':
                calls[0] += 1
                if calls[0] == 2: raise subprocess.CalledProcessError(1, command, stderr=b'test failure')
            return actual(command, *args, **kwargs)
        with patch('scripts.edit_assemble.subprocess.run', side_effect=interrupted):
            with self.assertRaises(subprocess.CalledProcessError):
                assemble(self.project, render=True, technical_preview=True)
        self.preserved()

    def test_manifest_commit_failure_preserves_old_outputs(self):
        with patch('scripts.pipeline_contracts.atomic_batch', side_effect=OSError('清单提交中断')):
            with self.assertRaises(OSError): assemble(self.project, render=True, technical_preview=True)
        self.preserved()

    def test_changed_selection_during_render_rejects_publish(self):
        actual = subprocess.run
        calls = [0]
        def changed(command, *args, **kwargs):
            result = actual(command, *args, **kwargs)
            if command[0] == 'ffmpeg':
                calls[0] += 1
                if calls[0] == 2:
                    write(self.project / 'state/edit_selection.json', {'selections': self.selections, 'note': '新的选段版本'})
            return result
        with patch('scripts.edit_assemble.subprocess.run', side_effect=changed):
            with self.assertRaises(RevisionConflict): assemble(self.project, render=True, technical_preview=True)
        self.preserved()

    def test_success_uses_new_generation_without_overwriting_old_files(self):
        newer = assemble(self.project, render=True, technical_preview=True)
        self.assertNotEqual(digest(self.manifest), self.manifest_sha)
        for path, sha in self.old_files.items(): self.assertEqual(digest(path), sha)
        self.assertTrue(all(self.project / e['target_path'] not in self.old_files for e in newer['entries']))


if __name__ == '__main__': unittest.main()
