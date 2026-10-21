"""复核独立MMS对齐来源与逐词范围；不授予时间或听辨批准。"""
import json
import re
from pathlib import Path

try:
    from .pipeline_contracts import digest, number
    from .lyrics_canonical import parse
except ImportError:
    from pipeline_contracts import digest, number
    from lyrics_canonical import parse


def require(project, timeline, source=None):
    project = Path(project).resolve()
    source = source if source is not None else timeline.get('machine_candidate_source')
    if not isinstance(source, dict) or not source.get('job_path'):
        raise ValueError('缺少独立对齐来源')
    job_path = (project/source['job_path']).resolve()
    job_path.relative_to(project/'state/forced_alignment_jobs')
    versions = {job_path: digest(job_path)}
    job = json.loads(job_path.read_text(encoding='utf8'))
    run_id = job.get('run_id')
    if (not isinstance(run_id, str) or not re.fullmatch('[0-9a-f]{32}', run_id)
            or job_path.name != run_id+'.json' or job.get('project_id') != str(project)
            or job.get('status') != 'completed_alignment_unreviewed'
            or job.get('model') != 'torchaudio.pipelines.MMS_FA'
            or not job.get('engine_versions')
            or type(job.get('decode', {}).get('returncode')) is not int
            or job['decode']['returncode'] != 0):
        raise ValueError('独立对齐缺少本项目已完成的实际执行证据')
    result_path = Path(job['result_path']).resolve()
    result_path.relative_to(project/'analysis/forced_alignment'/run_id)
    versions[result_path] = digest(result_path)
    if versions[result_path] != job.get('result_sha256'):
        raise ValueError('独立对齐结果已改变')
    for key, expected in (('run_id', run_id), ('result_path', str(result_path))):
        if source.get(key) is not None and source[key] != expected:
            raise ValueError('时间轴绑定了其他独立对齐来源')
    result = json.loads(result_path.read_text(encoding='utf8'))
    audio, lyrics = (project/timeline['master_audio']).resolve(), project/'input/lyrics.txt'
    inputs = job.get('inputs', {})
    for path in (audio, lyrics):
        versions[path] = digest(path)
        if inputs.get(str(path)) != versions[path]:
            raise ValueError('独立对齐原曲或官方歌词已改变')
    # 捕获时的timeline仅留作历史快照；采纳新边界必然改变它，不能循环锁死。
    for name, value in inputs.items():
        path = Path(name).resolve()
        if path != project/'state/timeline.json':
            versions[path] = digest(path)
            if versions[path] != value:
                raise ValueError('独立对齐执行输入已改变')
    duration = number(timeline['duration_sec'])
    if (result.get('run_id') != run_id or result.get('source_audio_sha256') != versions[audio]
            or abs(number(result['duration_sec'])-duration) > .05):
        raise ValueError('独立对齐原始结果与任务或母带不一致')
    words, groups = result['words'], result['lines']
    official = parse(lyrics.read_text(encoding='utf8'))
    if (not isinstance(words, list) or not words or not isinstance(groups, list)
            or len(groups) != len(official) or job.get('lines') != len(groups)
            or job.get('romanized_words') != [w.get('word') for w in words]):
        raise ValueError('独立对齐缺少完整歌词及逐词结果')
    previous = 0
    for word in words:
        start, end, score = number(word['start']), number(word['end']), number(word['score'])
        if not (previous <= start < end <= duration+.05 and 0 <= score <= 1
                and isinstance(word['word'], str) and word['word'].strip()):
            raise ValueError('独立对齐逐词范围或分数无效')
        previous = end
    cursor = 0
    for row, original in zip(groups, official):
        a, b = row['word_start_index'], row['word_end_index']
        if (any(row.get(k) != original.get(k) for k in ('index','text','speaker'))
                or type(a) is not int or type(b) is not int or a != cursor or not a < b <= len(words)
                or row['start_candidate'] != words[a]['start'] or row['end_candidate'] != words[b-1]['end']):
            raise ValueError('独立对齐逐行身份或完整词范围不一致')
        cursor = b
    if cursor != len(words):
        raise ValueError('独立对齐有未归属的词')
    if any(digest(path) != version for path, version in versions.items()):
        raise ValueError('独立对齐来源在核验期间变化')
    return job, result, versions
