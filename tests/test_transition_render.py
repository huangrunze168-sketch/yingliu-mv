"""用真实本地颜色视频和双频声音验证转场，不调用供应商。"""
import json
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path
from test_visual_workflow import fixture, write
from test_production_contracts import ffmpeg, ROOT
from scripts.storyboard_build import build
from scripts.edit_assemble import build as assemble, compile_selection
from scripts.transition_render import join_picture, render_diegetic_audio
from scripts.media_verify import probe
from scripts.pipeline_contracts import digest


class TransitionRenderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)

    def pictures(self):
        fixture(self.project)
        sb = build(self.project)
        sb['shots'][0]['crew']['editing'].update(transition_type='dissolve', transition_duration_frames=12)
        write(self.project / 'state/storyboard.json', sb)
        clips, selections = {}, []
        for index, color in enumerate(('red', 'blue')):
            path = self.project / f'{color}.mp4'
            ffmpeg('-f', 'lavfi', '-i', f'color={color}:s=160x90:r=24:d=3', '-c:v', 'libx264', path)
            sid = sb['shots'][index]['id']
            clips[color] = {'path': path.name, 'sha256': digest(path), 'frame_count': 72,
                            'fps': 24, 'scene_id': sb['shots'][index]['scene_id'],
                            'status': 'reviewed', 'evidence': '测试素材'}
            selections.append({'shot_id': sid, 'clip_id': color, 'in_frame': 0,
                               'out_frame': 48, 'edit_reason': '测试', 'state_check': '测试'})
        write(self.project / 'state/generated_clips.json', {'clips': clips})
        write(self.project / 'state/edit_selection.json', {'selections': selections})
        return sb, clips, selections

    def test_dissolve_assemble_to_join_keeps_music_windows_and_blends_pixels(self):
        self.pictures()
        assembly = assemble(self.project, render=True, technical_preview=True)
        self.assertEqual(assembly['entries'][0]['postroll_frames'], 12)
        output = self.project / 'dissolve.mp4'
        join_picture(self.project, assembly['entries'], output, 24, 160, 90)
        video = next(s for s in probe(output)['streams'] if s['codec_type'] == 'video')
        self.assertEqual(int(video['nb_read_frames']), 96)
        result = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(output), '-vf',
                                 r'select=eq(n\,54)', '-frames:v', '1', '-pix_fmt', 'rgb24',
                                 '-f', 'rawvideo', '-'], capture_output=True, check=True)
        pixel = result.stdout[:3]
        self.assertGreater(pixel[0], 60)
        self.assertGreater(pixel[2], 60)

    def test_dissolve_without_real_postroll_is_blocked(self):
        sb, clips, selections = self.pictures()
        clips['red']['frame_count'] = 48
        with self.assertRaisesRegex(ValueError, '后延'): compile_selection(sb, clips, selections)

    def test_dissolve_longer_than_next_window_is_blocked(self):
        sb, clips, selections = self.pictures()
        sb['shots'][0]['crew']['editing']['transition_duration_frames'] = 49
        with self.assertRaises(ValueError): compile_selection(sb, clips, selections)

    def audio(self, kind, in_frame=24):
        entries = []
        for index, freq in enumerate((440, 880)):
            path = self.project / f'{freq}.wav'
            ffmpeg('-f', 'lavfi', '-i', f'sine=frequency={freq}:sample_rate=48000:duration=4', path)
            entries.append({'frames': 48, 'audio_in_frame': in_frame,
                            'audio_path': str(path), 'audio_sha256': digest(path)})
        entries[0]['audio_transition'] = {'type': kind, 'duration_frames': 12}
        return entries

    def frequency(self, path, time):
        result = subprocess.run(['ffmpeg', '-v', 'error', '-ss', str(time), '-i', str(path),
                                 '-t', '0.1', '-ac', '1', '-f', 'f32le', '-'],
                                capture_output=True, check=True)
        values = struct.unpack('<' + 'f'*(len(result.stdout)//4), result.stdout)
        crossings = sum(a <= 0 < b for a, b in zip(values, values[1:]))
        return crossings / (len(values)/48000)

    def test_J_cut_next_sound_starts_before_picture_cut(self):
        entries = self.audio('J_cut')
        source_hashes = [digest(e['audio_path']) for e in entries]
        output = self.project / 'j.wav'
        report = render_diegetic_audio(entries, output, 24, channels=1)
        self.assertAlmostEqual(float(probe(output)['format']['duration']), 4, places=4)
        self.assertAlmostEqual(self.frequency(output, 1.7), 880, delta=20)
        self.assertFalse(report['master_audio_modified'])
        self.assertEqual([digest(e['audio_path']) for e in entries], source_hashes)

    def test_L_cut_previous_sound_continues_after_picture_cut(self):
        output = self.project / 'l.wav'
        render_diegetic_audio(self.audio('L_cut'), output, 24, channels=1)
        self.assertAlmostEqual(self.frequency(output, 2.2), 440, delta=20)
        self.assertAlmostEqual(self.frequency(output, 2.7), 880, delta=20)

    def test_J_cut_missing_preroll_is_blocked(self):
        output = self.project / 'bad.wav'
        with self.assertRaisesRegex(ValueError, '前延'):
            render_diegetic_audio(self.audio('J_cut', in_frame=0), output, 24)
        self.assertFalse(output.exists())

    def test_audio_does_not_overwrite_previous_success(self):
        output = self.project / 'old.wav'
        output.write_bytes(b'old success')
        with self.assertRaises(FileExistsError): render_diegetic_audio(self.audio('L_cut'), output, 24)
        self.assertEqual(output.read_bytes(), b'old success')
