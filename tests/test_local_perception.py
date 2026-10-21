"""连续/重叠窗口与真实失败回执；不模拟模型已观察或批准。"""
import json
import math
from pathlib import Path
import tempfile
import unittest
import wave

from scripts.local_perception import windows, run


class LocalPerceptionTests(unittest.TestCase):
    def test_whole_track_coverage_has_no_holes_and_keeps_overlap(self):
        rows = windows(63.007333, chunk_sec=10, overlap_sec=1)
        self.assertEqual(rows[0][0], 0)
        self.assertEqual(rows[-1][1], 63.007333)
        self.assertTrue(all(a[1]-b[0] == 1 for a,b in zip(rows, rows[1:])))
        self.assertEqual(windows(65, start=13, end=19, chunk_sec=6, overlap_sec=0), [[13,19]])

    def test_invalid_ranges_cannot_claim_full_coverage(self):
        for kwargs in ({'start': -1}, {'end': 70}, {'chunk_sec': 1, 'overlap_sec': 1},
                       {'start': math.nan}, {'chunk_sec': True}):
            with self.assertRaises(ValueError):
                windows(65, **kwargs)

    def test_real_audio_missing_model_is_recorded_failed(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory)
            audio = project/'source.wav'
            with wave.open(str(audio), 'wb') as handle:
                handle.setnchannels(1);handle.setsampwidth(2);handle.setframerate(16000)
                handle.writeframes(b'\0\0'*32000)
            with self.assertRaises(OSError):
                run(project, audio, project/'missing-model', audio_only=True)
            jobs = list((project/'state/perception_jobs').glob('*.json'))
            self.assertEqual(len(jobs), 1)
            record = json.loads(jobs[0].read_text(encoding='utf-8'))
            self.assertEqual(record['status'], 'failed')
            self.assertEqual(record['review_decision'], 'pending')
            self.assertFalse(record['lipsync_verified'])
            self.assertFalse(record['artistic_approval'])
            self.assertNotIn('full_audio_range_processed', record)


if __name__ == '__main__':
    unittest.main()
