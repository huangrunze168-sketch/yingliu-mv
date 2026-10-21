"""歌词角色解析、实际词匹配及来源/逐行复核失效测试。"""
import copy
import json
from pathlib import Path
import tempfile
import unittest
import wave

from evidence_fixtures import write_timing_fixture
from scripts.asr_capture import line_candidates
from scripts.lyrics_canonical import parse
from scripts.timing_evidence import errors
from scripts.pipeline_contracts import digest


class TimingEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name).resolve()
        (self.project/'input').mkdir()
        with wave.open(str(self.project/'input/song.wav'), 'wb') as handle:
            handle.setnchannels(1); handle.setsampwidth(2); handle.setframerate(8000)
            handle.writeframes(b'\0\0'*16000)
        (self.project/'input/lyrics.txt').write_text('第一句\n第二句', encoding='utf8')
        self.timeline = {'duration_sec': 2, 'master_audio': 'input/song.wav',
                         'production_timing_verified': True,
                         'lines': [{'index': 1, 'type': 'vocal', 'text': '第一句', 'start': 0, 'end': 1},
                                   {'index': 2, 'type': 'vocal', 'text': '第二句', 'start': 1, 'end': 2}]}
        self.run, self.review = write_timing_fixture(self.project, self.timeline)

    def test_current_bound_contract_passes(self):
        self.assertEqual(errors(self.project, self.timeline), [])

    def test_verified_boolean_without_source_blocks(self):
        timeline = copy.deepcopy(self.timeline)
        del timeline['timing_source']
        self.assertTrue(any('verified布尔' in e for e in errors(self.project, timeline)))

    def test_source_audio_replacement_blocks(self):
        (self.project/'input/song.wav').write_bytes(b'replaced')
        self.assertTrue(any('原曲' in e or '执行输入' in e for e in errors(self.project, self.timeline)))

    def test_original_asr_replacement_blocks(self):
        raw = Path(self.run['raw_path'])
        value = json.loads(raw.read_text(encoding='utf8'))
        value['words'][0]['word'] = '被修改'
        raw.write_text(json.dumps(value), encoding='utf8')
        self.assertTrue(any('原始ASR结果已改变' in e for e in errors(self.project, self.timeline)))

    def test_incomplete_per_line_review_blocks(self):
        self.review['lines'].pop()
        (self.project/'state/timing_review.json').write_text(json.dumps(self.review), encoding='utf8')
        self.assertTrue(any('逐行覆盖' in e for e in errors(self.project, self.timeline)))

    def test_unreviewed_capture_does_not_approve_timing(self):
        self.review.update(decision='pending', reviewed_full_audio=False)
        (self.project/'state/timing_review.json').write_text(json.dumps(self.review), encoding='utf8')
        self.assertTrue(any('音频时间复核' in e for e in errors(self.project, self.timeline)))

    def test_role_headers_are_metadata_and_cannot_add_lyrics(self):
        rows = parse('[裁判]\n(Referee) 开麦\n[Verse 1 - Rap, 诸葛亮]\n(A) 第一句\n[Verse 1 - Rap,周瑜]\n(B) 第二句')
        self.assertEqual([r['text'] for r in rows], ['开麦', '第一句', '第二句'])
        self.assertEqual([r['speaker'] for r in rows], ['裁判', '诸葛亮', '周瑜'])
        self.assertEqual([r['source_line'] for r in rows], [2, 4, 6])

    def test_missing_words_stay_pending_without_invented_timing(self):
        rows = line_candidates(parse('第一句\n第二句'), [{'word': '第一句', 'start': .3, 'end': 1}])
        self.assertTrue(all(r['pending_review'] for r in rows))
        # 第二句只有局部同字匹配，不能据此编造一个完整句子的范围或批准。
        self.assertLess(rows[1]['matched_character_ratio'], 1)
        empty = line_candidates(parse('没有识别结果'), [])
        self.assertIsNone(empty[0]['start_candidate'])
        self.assertIsNone(empty[0]['end_candidate'])


if __name__ == '__main__':
    unittest.main()
