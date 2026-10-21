"""从实际ASR结果建立待核时间轴；不猜缺失句、间奏或写入正式批准。"""
import argparse
import json
from pathlib import Path

try:
    from .pipeline_contracts import atomic_batch, digest, number, require_no_transaction
    from .lyrics_canonical import parse
except ImportError:
    from pipeline_contracts import atomic_batch, digest, number, require_no_transaction
    from lyrics_canonical import parse


def build(project):
    project = Path(project).resolve()
    require_no_transaction(project)
    if (project/'.asr-execution.lock').exists():
        raise ValueError('ASR尚在执行，不读取半套结果')
    timeline_path, review_path = project/'state/timeline.json', project/'state/timing_review.json'
    if timeline_path.exists() or review_path.exists():
        raise FileExistsError('已有时间轴或复核记录，拒绝覆盖；变更须显式撤销并归档后重建')
    execution = project/'state/asr_execution.json'
    version = digest(execution)
    run = json.loads(execution.read_text(encoding='utf8'))
    if run.get('status') != 'completed_unreviewed' or run.get('project_id') != str(project):
        raise ValueError('没有本项目实际已完成的ASR')
    raw_path, candidate_path = Path(run['raw_path']), Path(run['candidate_path'])
    for p in (raw_path, candidate_path):
        p.resolve().relative_to(project)
    if digest(raw_path) != run['raw_sha256'] or digest(candidate_path) != run['candidate_sha256']:
        raise ValueError('ASR或匹配结果已改变，不能建立待核时间轴')
    raw = json.loads(raw_path.read_text(encoding='utf8'))
    candidates = json.loads(candidate_path.read_text(encoding='utf8'))
    if raw['run_id'] != run['run_id'] or candidates['run_id'] != run['run_id']:
        raise ValueError('候选与实际ASR任务不一致')
    audio, lyrics = Path(raw['audio_path']), project/'input/lyrics.txt'
    if digest(audio) != raw['audio_sha256'] or digest(lyrics) != raw['lyrics_sha256']:
        raise ValueError('原曲或歌词已改变')
    official = parse(lyrics.read_text(encoding='utf8'))
    fields = ('index', 'text', 'speaker', 'source_line', 'section')
    if [[r.get(k) for k in fields] for r in official] != [[r.get(k) for k in fields] for r in candidates['lines']]:
        raise ValueError('候选没有逐行保留当前官方歌词')
    duration, lines, reviews = number(raw['duration_sec']), [], []
    previous = 0
    for row in candidates['lines']:
        start, end = number(row['start_candidate']), number(row['end_candidate'])
        if not 0 <= previous <= start < end <= duration:
            raise ValueError('实际词匹配没有给出有效且不重叠的句界，须先处理，禁止补均匀时间')
        indices = row['matched_word_indices']
        if not indices or any(type(i) is not int or not 0 <= i < len(raw['words']) for i in indices):
            raise ValueError('缺少实际ASR词引用，禁止编造句界')
        line = {'index': row['index'], 'type': 'vocal', 'text': row['text'], 'speaker': row.get('speaker'),
                'start': start, 'end': end, 'confidence': number(row['matched_character_ratio'])}
        lines.append(line)
        reviews.append({**line, 'asr_word_range': [min(indices), max(indices)], 'observation': ''})
        previous = end
    timeline = {'duration_sec': duration, 'master_audio': str(audio), 'source_audio': str(audio),
                'master_audio_sha256': raw['audio_sha256'], 'lyrics_source': 'user_official',
                'authority': 'provisional_asr_character_match', 'asr_engine': run['engine'],
                'production_timing_verified': False, 'pending_review': [r['index'] for r in lines],
                'lines': lines, 'timing_source': {'execution_path': 'state/asr_execution.json',
                                                 'review_path': 'state/timing_review.json'},
                'unclassified_audio_ranges': [[a, b] for a, b in zip(
                    [0]+[r['end'] for r in lines], [r['start'] for r in lines]+[duration]) if b > a],
                'limits': '空档未听辨，不能直接当作间奏；全部句界待核，不允许正式口型生产。'}
    timeline_text = json.dumps(timeline, ensure_ascii=False, indent=2)
    import hashlib
    review = {'schema_version': 1, 'decision': 'pending', 'reviewer': '', 'reviewed_full_audio': False,
              'asr_run_id': run['run_id'], 'raw_sha256': run['raw_sha256'],
              'timeline_sha256': 'sha256:'+hashlib.sha256(timeline_text.encode()).hexdigest(),
              'lines': reviews}
    expected = {execution: version, raw_path: run['raw_sha256'], candidate_path: run['candidate_sha256'],
                audio: raw['audio_sha256'], lyrics: raw['lyrics_sha256'], timeline_path: None, review_path: None}
    atomic_batch({timeline_path: timeline_text, review_path: json.dumps(review, ensure_ascii=False, indent=2)}, expected=expected)
    return timeline


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project_dir', type=Path)
    args = parser.parse_args()
    result = build(args.project_dir)
    print(json.dumps({'status': 'pending_review', 'lines': len(result['lines']),
                      'production_timing_verified': False}, ensure_ascii=False))
