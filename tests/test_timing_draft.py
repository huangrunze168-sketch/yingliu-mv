"""真实来源绑定后仍只能生成pending；失败不能造时间或覆盖旧复核。"""
import json
from pathlib import Path
import tempfile
import unittest
import wave

from evidence_fixtures import write_timing_fixture
from scripts.asr_capture import line_candidates
from scripts.lyrics_canonical import parse
from scripts.pipeline_contracts import digest
from scripts.timing_draft import build


class TimingDraftTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name).resolve()
        (self.project/'input').mkdir()
        with wave.open(str(self.project/'input/song.wav'), 'wb') as handle:
            handle.setnchannels(1); handle.setsampwidth(2); handle.setframerate(8000)
            handle.writeframes(b'\0\0'*16000)
        lyrics = self.project/'input/lyrics.txt'
        lyrics.write_text('第一句\n第二句', encoding='utf8')
        tl = {'duration_sec': 2, 'master_audio': 'input/song.wav',
              'lines': [{'index': 1, 'type': 'vocal', 'text': '第一句', 'start': .1, 'end': .8},
                        {'index': 2, 'type': 'vocal', 'text': '第二句', 'start': 1, 'end': 1.8}]}
        self.run, _ = write_timing_fixture(self.project, tl)
        for name in ('timeline', 'timing_review'):
            (self.project/'state'/f'{name}.json').unlink()
        raw = json.loads(Path(self.run['raw_path']).read_text(encoding='utf8'))
        self.candidates = {'run_id': self.run['run_id'],
                           'lines': line_candidates(parse(lyrics.read_text(encoding='utf8')), raw['words'])}
        self.candidate_path = self.project/'analysis/candidates.json'
        self.save_candidates()

    def save_candidates(self):
        self.candidate_path.write_text(json.dumps(self.candidates, ensure_ascii=False), encoding='utf8')
        self.run.update(candidate_path=str(self.candidate_path), candidate_sha256=digest(self.candidate_path))
        (self.project/'state/asr_execution.json').write_text(json.dumps(self.run), encoding='utf8')

    def test_draft_never_approves_timing_or_guesses_interludes(self):
        result = build(self.project)
        self.assertFalse(result['production_timing_verified'])
        self.assertEqual(result['pending_review'], [1, 2])
        self.assertNotIn('no_interlude', result)
        self.assertTrue(result['unclassified_audio_ranges'])
        review = json.loads((self.project/'state/timing_review.json').read_text(encoding='utf8'))
        self.assertEqual(review['decision'], 'pending')
        self.assertFalse(review['reviewed_full_audio'])

    def test_existing_timeline_is_not_overwritten(self):
        build(self.project)
        path = self.project/'state/timeline.json'
        before = path.read_bytes()
        with self.assertRaises(FileExistsError):
            build(self.project)
        self.assertEqual(path.read_bytes(), before)

    def test_missing_matching_words_cannot_get_invented_time(self):
        self.candidates['lines'][1]['matched_word_indices'] = []
        self.save_candidates()
        with self.assertRaisesRegex(ValueError, '禁止编造'):
            build(self.project)
        self.assertFalse((self.project/'state/timeline.json').exists())

    def test_overlapping_matching_boundaries_block_without_partial_outputs(self):
        self.candidates['lines'][1]['start_candidate'] = .5
        self.save_candidates()
        with self.assertRaises(ValueError):
            build(self.project)
        self.assertFalse((self.project/'state/timeline.json').exists())
        self.assertFalse((self.project/'state/timing_review.json').exists())

    def test_modified_candidate_hash_blocks(self):
        self.candidate_path.write_text('{}', encoding='utf8')
        with self.assertRaisesRegex(ValueError, '结果已改变'):
            build(self.project)


if __name__ == '__main__':
    unittest.main()
