"""合成入口须独立核对上游清单与安全路径，而非信任已运行构建器。"""
import copy
import tempfile
import unittest
from pathlib import Path
from test_visual_workflow import fixture
from scripts.storyboard_build import build
from scripts.edit_assemble import compile_selection, validate_assembly
from scripts.delivery_state import record_path


class ChainHandoffs(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        fixture(self.project)
        self.sb = build(self.project)
        self.clips, self.selections = {}, []
        for i, shot in enumerate(self.sb['shots']):
            cid = f'C{i}'
            self.clips[cid] = {'path': f'{cid}.mp4', 'sha256': f'sha256:{i}',
                'status': 'reviewed', 'evidence': '隔离编译测试', 'scene_id': shot['scene_id'],
                'fps': 24, 'frame_count': 72}
            self.selections.append({'shot_id': shot['id'], 'clip_id': cid, 'in_frame': 0,
                'out_frame': 48, 'edit_reason': '测试', 'state_check': '测试'})
        self.assembly = {'entries': compile_selection(self.sb, self.clips, self.selections)}

    def check(self): return validate_assembly(self.sb, self.clips, self.selections, self.assembly)

    def test_valid_handoff(self): self.assertTrue(self.check())

    def test_reordered_entries_blocked(self):
        self.assembly['entries'].reverse()
        with self.assertRaises(ValueError): self.check()

    def test_truncated_entries_blocked(self):
        self.assembly['entries'].pop()
        with self.assertRaises(ValueError): self.check()

    def test_forged_source_or_window_blocked(self):
        original = copy.deepcopy(self.assembly)
        for key, value in [('source_path', 'other.mp4'), ('in_frame', 1),
                           ('frames', 999), ('clip_id', 'other')]:
            self.assembly = copy.deepcopy(original)
            self.assembly['entries'][0][key] = value
            with self.assertRaises(ValueError): self.check()

    def test_forged_transition_blocked(self):
        self.assembly['entries'][0]['video_transition'] = {'type': 'dissolve', 'duration_frames': 12}
        with self.assertRaises(ValueError): self.check()

    def test_duplicate_storyboard_id_blocked(self):
        self.sb['shots'][1]['id'] = self.sb['shots'][0]['id']
        with self.assertRaises(ValueError): compile_selection(self.sb, self.clips, self.selections)

    def test_gap_in_storyboard_blocked(self):
        self.sb['shots'][1]['window']['start'] += .25
        with self.assertRaises(ValueError): compile_selection(self.sb, self.clips, self.selections)

    def test_float_and_bool_clip_frames_blocked(self):
        for value in (72.5, True):
            self.clips['C0']['frame_count'] = value
            with self.assertRaises(ValueError): compile_selection(self.sb, self.clips, self.selections)

    def test_bool_fps_blocked(self):
        self.sb['fps'] = True
        with self.assertRaises(ValueError): compile_selection(self.sb, self.clips, self.selections)

    def test_preview_names_cannot_escape_or_use_ads(self):
        for name in ('../preview.mp4', '..\\preview.mp4', 'preview:payload.mp4', 'preview\n.mp4'):
            with self.assertRaises(ValueError): record_path(self.project, name)
        self.assertEqual(record_path(self.project, '技术preview.mp4').parent, self.project/'state/deliveries')
