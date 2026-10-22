"""合成夹具验证状态、观察范围和实际媒体链路，不认证艺术效果。"""
import importlib.util
import shutil
import tempfile
import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
spec = importlib.util.spec_from_file_location('mv', Path(__file__).resolve().parents[1] / 'scripts/mv.py')
mv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mv)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.test_root = Path(__file__).resolve().parent
        self.temp = tempfile.TemporaryDirectory(prefix='fixture-', dir=self.test_root)
        self.project = Path(self.temp.name) / '合成测试'
        mv.init(self.project)

    def tearDown(self):
        Path(self.temp.name).resolve().relative_to(self.test_root)
        self.temp.cleanup()

    def put(self, name, data):
        mv.write(self.project / name, data)

    def prepare(self):
        (self.project / 'input/song.wav').write_bytes(b'synthetic')
        (self.project / 'input/lyrics.txt').write_text('合成歌词', encoding='utf8')
        self.assertEqual(mv.approve(self.project, '测试用户', '测试明确确认')['stage'], 'concept')
        (self.project / 'input/alignment.txt').write_text('合成时间证据，不用于生产', encoding='utf8')
        self.put('state/timeline.json', {'duration': 2, 'verified': True, 'evidence_path': 'input/alignment.txt',
                                       'segments': [{'start': 0, 'end': 2, 'text': '合成歌词'}]})
        self.put('state/concept.json', {'treatment': '合成故事', 'visual_direction': '合成视觉', 'music_sections': ['测试']})
        mv.approve(self.project, '测试用户', '合成创意确认')

    def test_no_overwrite_and_paths(self):
        with self.assertRaises(FileExistsError):
            mv.init(self.project)
        with self.assertRaises(ValueError):
            mv.local(self.project, '../outside')
        self.assertEqual(mv.status(self.project)['status'], 'blocked')
        with self.assertRaises(ValueError):
            mv.approve(self.project, '测试', '未准备不能批准')

    def test_approval_invalidation(self):
        self.prepare()
        self.assertEqual(mv.status(self.project)['stage'], 'storyboard')
        (self.project / 'input/song.wav').write_bytes(b'changed')
        self.assertEqual(mv.status(self.project)['stage'], 'prepare')
        self.assertEqual(mv.status(self.project)['status'], 'waiting_approval')

    def test_compile_and_pack(self):
        self.prepare()
        self.put('state/design.json', {'method': 'zy-cinematic-realism',
            'global': {'wardrobe': '黑衣'}, 'scenes': {'A': {'lighting': '侧光'}},
            'shots': [{'id': 'SH001', 'scene': 'A', 'start': 0, 'end': 2, 'action': '转身',
                       'camera': '近景', 'cut_reason': '动作完成', 'lip_sync': False}]})
        self.assertEqual(mv.compile_prompts(self.project)['compiled'], 1)
        self.assertEqual(mv.read(self.project / 'prompts/SH001.json')['resolved']['wardrobe'], '黑衣')
        mv.pack(self.project, 'SH001')
        self.assertEqual(mv.read(self.project / 'state/context/SH001.json')['shot_context']['shots'][0]['id'], 'SH001')
        with self.assertRaises(ValueError):
            mv.windows([{'start': 0, 'end': 1}, {'start': 1.1, 'end': 2}], 2)
        with self.assertRaises(ValueError):
            mv.number(float('nan'))
        # 缺少 ZY 方法声明时，编译必须阻断。
        self.put('state/design.json', {'global': {}, 'scenes': {'A': {}}, 'shots': [
            {'id': 'SH001', 'scene': 'A', 'start': 0, 'end': 2, 'action': '转身',
             'camera': '近景', 'cut_reason': '动作完成', 'lip_sync': False}]})
        with self.assertRaises(ValueError) as blocked:
            mv.compile_prompts(self.project)
        self.assertIn('zy-cinematic-realism', str(blocked.exception))

    def observation(self, media, scope='audiovisual', refs=None):
        path = self.project / 'state/draft_observation.json'
        self.put('state/draft_observation.json', {'media': media, 'scope': scope, 'observer': '合成测试声明',
            'evidence': '测试夹具，不是真实观看', 'findings': [], 'unverified': [], 'references': refs or []})
        return mv.observe(self.project, path)['observation']

    def reviewed(self, media, scope='audiovisual'):
        return {'media': media, 'observations': [self.observation(media, scope)], 'decision': 'approve',
                'evidence': '合成测试审查声明', 'unverified': []}

    def test_observation_scope_and_staleness(self):
        (self.project / 'clips/pilot.mp4').write_bytes(b'synthetic-video')
        record = self.reviewed('clips/pilot.mp4', 'sampled_frames')
        with self.assertRaises(ValueError):
            mv.review(self.project, record)
        record = self.reviewed('clips/pilot.mp4')
        mv.review(self.project, record, final=True)
        self.assertTrue(mv.observations(self.project, 'clips/pilot.mp4')['records'][-1]['current'])
        (self.project / 'clips/pilot.mp4').write_bytes(b'new-version')
        with self.assertRaises(ValueError):
            mv.review(self.project, record)
        self.assertFalse(mv.observations(self.project, 'clips/pilot.mp4')['records'][-1]['current'])

    def test_project_relative_observation_and_simulation_isolation(self):
        (self.project / 'clips/pilot.mp4').write_bytes(b'synthetic-video')
        self.put('state/draft.json', {'media': 'clips/pilot.mp4', 'scope': 'image', 'observer': '测试',
            'evidence': '隔离字段测试', 'findings': [], 'unverified': [], 'simulation': True})
        with self.assertRaisesRegex(ValueError, '模拟观察'):
            mv.observe(self.project, Path('state/draft.json'))
        cfg = mv.config(self.project)
        cfg['simulation'] = True
        self.put('project.json', cfg)
        result = mv.observe(self.project, Path('state/draft.json'))
        self.assertTrue((self.project / result['observation']).is_file())

    def test_cumulative_frame_boundaries(self):
        counts = mv.frame_plan([0.51] * 100, 24)
        self.assertEqual(sum(counts), 1224)
        cursor = 0
        for i, count in enumerate(counts):
            cursor += count
            self.assertLessEqual(abs(cursor - (i + 1) * 0.51 * 24), 0.500001)
        with self.assertRaises(ValueError):
            mv.frame_plan([0.001], 24)

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), '需要 FFmpeg')
    def test_full_synthetic_media_flow(self):
        p = self.project
        mv.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=2', str(p / 'input/song.wav')])
        (p / 'input/lyrics.txt').write_text('合成歌词', encoding='utf8')
        mv.approve(p, '测试用户', '合成准备确认')
        (p / 'input/alignment.txt').write_text('合成证据', encoding='utf8')
        self.put('state/timeline.json', {'duration': 2, 'verified': True, 'evidence_path': 'input/alignment.txt',
            'segments': [{'start': 0, 'end': 2, 'text': '合成歌词'}]})
        self.put('state/concept.json', {'treatment': '测试', 'visual_direction': '测试', 'music_sections': ['测试']})
        mv.approve(p, '测试用户', '合成创意确认')
        mv.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=c=blue:s=160x90',
                '-frames:v', '1', str(p / 'input/scene.png')])
        cfg = mv.config(p)
        cfg.update(width=160, height=90)
        self.put('project.json', cfg)
        # 修改全片配置后重新确认其依赖阶段。
        mv.approve(p, '测试用户', '新画幅准备确认')
        mv.approve(p, '测试用户', '新画幅创意确认')
        self.put('state/design.json', {'method': 'zy-cinematic-realism', 'global': {}, 'scenes': {'A': {}}, 'shots': [
            {'id': 'SH001', 'scene': 'A', 'start': 0, 'end': 2, 'image': 'input/scene.png',
             'action': '静止测试', 'camera': '固定', 'cut_reason': '结束', 'lip_sync': False}]})
        mv.compile_prompts(p)
        mv.render(p, 'preview')
        self.put('state/animatic_review.json', self.reviewed('output/animatic_preview.mp4'))
        self.assertEqual(mv.approve(p, '测试用户', '合成预演确认')['stage'], 'pilot')
        shutil.copy2(p / 'output/animatic_preview.mp4', p / 'clips/pilot.mp4')
        frame_result = mv.frames(p, 'clips/pilot.mp4', 2)
        self.assertEqual(len(mv.read(p / frame_result['manifest'])['frames']), 2)
        self.put('state/pilot_review.json', self.reviewed('clips/pilot.mp4'))
        mv.approve(p, '测试用户', '合成样片确认')
        (p / 'state/source.txt').write_text('本地合成来源，费用0', encoding='utf8')
        self.put('state/edit.json', {'clips': [{'media': 'clips/pilot.mp4', 'in': 0, 'duration': 2, 'receipt': 'state/source.txt'}]})
        self.put('state/clips_review.json', {'reviews': [self.reviewed('clips/pilot.mp4')]})
        mv.approve(p, '测试用户', '合成选段确认')
        mv.render(p, 'final')
        self.put('state/final_review.json', self.reviewed('output/final_mv.mp4'))
        self.assertEqual(mv.approve(p, '测试用户', '合成成片确认')['status'], 'completed')
        self.assertEqual(mv.report(p)['state']['status'], 'completed')
        # 图像改变时，即使 JSON 未改，旧预演也必须失效。
        original_image = (p / 'input/scene.png').read_bytes()
        (p / 'input/scene.png').write_bytes(b'replaced-image')
        self.assertEqual(mv.status(p)['stage'], 'storyboard')
        (p / 'input/scene.png').write_bytes(original_image)
        # 当前素材替换后，旧批准不能继续使用。
        (p / 'clips/pilot.mp4').write_bytes(b'replaced')
        self.assertNotEqual(mv.status(p)['status'], 'completed')


if __name__ == '__main__':
    unittest.main()
