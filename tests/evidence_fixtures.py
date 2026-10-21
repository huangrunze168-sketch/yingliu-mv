"""隔离契约测试的合成ASR/复核输入，不代表真实识别或真实人工批准。"""
import json
from pathlib import Path

from scripts.pipeline_contracts import digest


def write_timing_fixture(project, timeline):
    project = Path(project).resolve()
    timeline['timing_source'] = {'execution_path': 'state/asr_execution.json',
                                 'review_path': 'state/timing_review.json'}
    state = project/'state'
    state.mkdir(parents=True, exist_ok=True)
    (state/'timeline.json').write_text(json.dumps(timeline, ensure_ascii=False, indent=2), encoding='utf8')
    vocal = [row for row in timeline['lines'] if row['type'] != 'interlude']
    audio = (project/timeline.get('master_audio', timeline.get('source_audio'))).resolve()
    lyrics = project/'input/lyrics.txt'
    raw = {'run_id': 'isolated-timing-contract-fixture', 'audio_path': str(audio),
           'audio_sha256': digest(audio), 'lyrics_sha256': digest(lyrics),
           'duration_sec': timeline['duration_sec'],
           'words': [{'word': row['text'], 'start': row['start'], 'end': row['end']} for row in vocal]}
    raw_path = project/'analysis/asr_fixture.json'
    raw_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path.write_text(json.dumps(raw, ensure_ascii=False), encoding='utf8')
    run = {'project_id': str(project), 'run_id': raw['run_id'], 'status': 'completed_unreviewed',
           'engine': 'isolated-contract-fixture', 'engine_version': 'fixture-v1',
           'inputs': {str(audio): digest(audio), str(lyrics): digest(lyrics)},
           'raw_path': str(raw_path), 'raw_sha256': digest(raw_path)}
    (state/'asr_execution.json').write_text(json.dumps(run), encoding='utf8')
    review = {'decision': 'approve', 'reviewed_full_audio': True, 'reviewer': 'isolated-test-fixture',
              'asr_run_id': raw['run_id'], 'raw_sha256': run['raw_sha256'],
              'timeline_sha256': digest(state/'timeline.json'),
              'lines': [{**row, 'asr_word_range': [i, i], 'observation': '合成边界契约测试'}
                        for i, row in enumerate(vocal)]}
    (state/'timing_review.json').write_text(json.dumps(review, ensure_ascii=False), encoding='utf8')
    return run, review
