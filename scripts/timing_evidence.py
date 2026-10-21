"""核验歌词时间的原始ASR来源与逐行复核声明；不认证声明者实际听过音频。"""
import json
from pathlib import Path

try:
    from .pipeline_contracts import digest, number
except ImportError:
    from pipeline_contracts import digest, number


def source_paths(project, timeline):
    source = timeline.get('timing_source') or {}
    if not isinstance(source, dict):
        return []
    return [Path(project)/source[key] for key in ('execution_path', 'review_path') if source.get(key)]


def alignment_versions(project, timeline):
    if not timeline.get('machine_candidate_source'):
        return {}
    try:
        from .alignment_evidence import require
    except ImportError:
        from alignment_evidence import require
    return require(project, timeline)[2]


def errors(project, timeline):
    project = Path(project).resolve()
    failures = []
    try:
        if (project/'.asr-execution.lock').exists():
            return ['ASR仍执行中或中断，不能沿用旧时间批准']
        source = timeline.get('timing_source')
        if not isinstance(source, dict) or not all(source.get(k) for k in ('execution_path', 'review_path')):
            return ['缺少实际ASR执行来源和逐行时间复核；verified布尔不能替代证据']
        execution_path, review_path = source_paths(project, timeline)
        for p in (execution_path, review_path):
            p.resolve().relative_to(project)
        run = json.loads(execution_path.read_text(encoding='utf8'))
        if run.get('project_id') != str(project) or run.get('status') != 'completed_unreviewed':
            failures.append('ASR执行记录不存在当前已完成的原始识别')
        raw_path = Path(run['raw_path'])
        raw_path.resolve().relative_to(project)
        if digest(raw_path) != run.get('raw_sha256'):
            failures.append('原始ASR结果已改变')
        raw = json.loads(raw_path.read_text(encoding='utf8'))
        if raw.get('run_id') != run.get('run_id'):
            failures.append('ASR任务与实际原始结果不一致')
        audio = (project/timeline['master_audio']).resolve()
        lyrics = project/'input/lyrics.txt'
        if (raw.get('audio_sha256') != digest(audio) or raw.get('lyrics_sha256') != digest(lyrics)
                or str(audio) != raw.get('audio_path')):
            failures.append('ASR来源与当前原曲或官方歌词不一致')
        if not run.get('inputs') or not run.get('engine') or not run.get('engine_version'):
            failures.append('ASR缺少实际输入和引擎版本')
        for name, expected in run.get('inputs', {}).items():
            if not Path(name).is_file() or digest(name) != expected:
                failures.append('ASR执行输入已变化: ' + name)
        duration = number(timeline['duration_sec'])
        if abs(number(raw['duration_sec'])-duration) > .05:
            failures.append('ASR实测时长与当前时间轴不一致')
        words = raw['words']
        if not isinstance(words, list) or not words:
            return failures + ['ASR没有实际逐词时间，不能批准口型时间']
        for word in words:
            if not (0 <= number(word['start']) <= number(word['end']) <= duration+.05
                    and isinstance(word['word'], str) and word['word'].strip()):
                failures.append('原始ASR逐词时间或文本无效')
        review = json.loads(review_path.read_text(encoding='utf8'))
        aligned = None
        if timeline.get('machine_candidate_source'):
            try:
                from .alignment_evidence import require
            except ImportError:
                from alignment_evidence import require
            alignment_run, alignment_result, alignment_inputs = require(project, timeline)
            aligned = alignment_result['lines']
            if (review.get('boundary_source') != 'forced_alignment'
                    or review.get('alignment_run_id') != alignment_run['run_id']
                    or review.get('alignment_result_sha256') != alignment_run['result_sha256']):
                failures.append('逐行复核未绑定当前独立对齐结果')
        if (review.get('decision') != 'approve' or review.get('reviewed_full_audio') is not True
                or not str(review.get('reviewer', '')).strip()):
            failures.append('尚无逐行真实音频时间复核声明')
        if (review.get('asr_run_id') != run.get('run_id') or review.get('raw_sha256') != run.get('raw_sha256')
                or review.get('timeline_sha256') != digest(project/'state/timeline.json')):
            failures.append('逐行复核绑定旧ASR或旧时间轴')
        vocal = [row for row in timeline['lines'] if row['type'] != 'interlude']
        rows = review.get('lines', [])
        if not isinstance(rows, list) or len(rows) != len(vocal):
            return failures + ['时间复核没有逐行覆盖当前全部歌词']
        for index, (line, checked) in enumerate(zip(vocal, rows)):
            if any(checked.get(k) != line.get(k) for k in ('index', 'text', 'start', 'end', 'speaker')):
                failures.append('时间复核与正式行不一致: ' + str(line.get('index')))
            if not str(checked.get('observation', '')).strip():
                failures.append('缺少逐行边界听辨说明')
            if aligned is not None:
                group = aligned[index]
                expected_range = [group['word_start_index'], group['word_end_index']-1]
                if (checked.get('forced_alignment_word_range') != expected_range
                        or any(group.get(k) != line.get(k) for k in ('index','text','speaker'))
                        or abs(number(line['start'])-number(group['start_candidate'])) > .25
                        or abs(number(line['end'])-number(group['end_candidate'])) > .25):
                    failures.append('逐行边界没有绑定当前独立对齐的完整词范围')
                continue
            a, b = checked['asr_word_range']
            if type(a) is not int or type(b) is not int or not 0 <= a <= b < len(words):
                failures.append('逐行ASR词范围无效')
                continue
            if (number(words[b]['end']) <= number(words[a]['start'])
                    or abs(number(line['start'])-number(words[a]['start'])) > .25
                    or abs(number(line['end'])-number(words[b]['end'])) > .25):
                failures.append('逐行边界没有绑定邻近的有效ASR词范围，不能用局部词认证整段手写时间')
        if aligned is not None and any(digest(p) != v for p, v in alignment_inputs.items()):
            failures.append('独立对齐依据在复核期间改变')
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        failures.append('歌词时间来源无法核验: ' + str(exc))
    return failures
