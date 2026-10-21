"""权威LRC、实际成片对齐执行与逐镜动态复核声明；音频不认证嘴型。"""
import json
import subprocess
import sys
import uuid
from pathlib import Path
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, timezone

try:
    from .pipeline_contracts import digest, number, timing_authority
    from .timing_evidence import errors as timing_errors
    from .lyrics_canonical import parse
except ImportError:
    from pipeline_contracts import digest, number, timing_authority
    from timing_evidence import errors as timing_errors
    from lyrics_canonical import parse


def load(path):
    return json.loads(Path(path).read_text(encoding='utf8'))


def lrc(project):
    project = Path(project).resolve()
    tl = load(project / 'state/timeline.json')
    failures = timing_errors(project, tl)
    try:
        from .gate_checks import check_timeline, probe_audio_duration
    except ImportError:
        from gate_checks import check_timeline, probe_audio_duration
    official_text = (project / 'input/lyrics.txt').read_text(encoding='utf8')
    failures.extend(r['item'] for r in check_timeline(tl, official_text,
                    probe_audio_duration(project / tl['master_audio'])) if not r['pass'])
    if timing_authority(tl) != 'production' or failures:
        raise ValueError('LRC只消费已批准的权威逐行时间: ' + '; '.join(failures))
    lines = [r for r in tl['lines'] if r['type'] != 'interlude']
    canonical = parse(official_text)
    if [r['text'] for r in lines] != [r['text'] for r in canonical]:
        raise ValueError('LRC歌词与官方原文不一致')
    output, previous = [], -1
    for row in lines:
        ticks = int((Decimal(str(number(row['start'])))*100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
        if ticks <= previous: raise ValueError('LRC百分之一秒网格出现重叠，不静默合并歌词')
        previous = ticks
        minutes, remainder = divmod(ticks, 6000)
        seconds, fraction = divmod(remainder, 100)
        output.append(f'[{minutes:02d}:{seconds:02d}.{fraction:02d}]{row["text"]}')
    return '\n'.join(output) + '\n'


def review_template(project, output_name):
    project = Path(project).resolve()
    sb = load(project / 'state/storyboard.json')
    return {'schema_version': 1, 'project_id': str(project), 'output_name': output_name,
            'media_sha256': digest(project / 'output' / output_name),
            'storyboard_sha256': digest(project / 'state/storyboard.json'),
            'timeline_sha256': digest(project / 'state/timeline.json'),
            'observer': '', 'watched_full_audio_video': False, 'simulation_only': False,
            'scope': 'bound_observer_statement_not_independent_lip_measurement',
            'shots': [{'shot_id': s['id'], 'window': s['window'],
                       'lyric_line_indices': s.get('lyric_lines', []), 'lip_sync_required': s['lip_sync'],
                       'mouth_status': 'pending' if s['lip_sync'] else 'not_applicable',
                       'performance_status': 'pending', 'sound_action_status': 'pending',
                       'continuity_status': 'pending', 'observation': '', 'mouth_samples': []}
                      for s in sb['shots']]}


def require_review(project, output_name, path):
    expected = review_template(project, output_name)
    actual = load(path)
    project = Path(project).resolve()
    if (project/'state/lip_measurement.json').is_file():
        try:
            from .lip_measure import require as require_lip
        except ImportError:
            from lip_measure import require as require_lip
        measurement = require_lip(project)
        if load(project/'state/lip_measurement.json')['media_sha256'] != expected['media_sha256']:
            raise ValueError('独立口型测量不是当前最终媒体')
        actual['independent_lip_measurement'] = measurement
    if (project/'state/sound_edit_plan.json').is_file():
        if actual.get('sound_mix_review') != {'audio_track': 1, 'watched_full_with_mix': True}:
            raise ValueError('须切换到第2音轨，完整复核J/L同期声混音并登记sound_mix_review')
    for key in ('project_id', 'output_name', 'media_sha256', 'storyboard_sha256', 'timeline_sha256'):
        if actual.get(key) != expected[key]: raise ValueError('最终动态复核绑定了旧文件或其他项目: ' + key)
    if (actual.get('scope') != expected['scope'] or actual.get('watched_full_audio_video') is not True or actual.get('simulation_only') is not False
            or not isinstance(actual.get('observer'), str) or not actual['observer'].strip()):
        raise ValueError('缺少最终实际媒体的完整有声观看声明和观察者；不自动批准')
    rows = actual.get('shots')
    if not isinstance(rows, list) or len(rows) != len(expected['shots']): raise ValueError('最终动态复核未覆盖全部镜头')
    for row, target in zip(rows, expected['shots']):
        for key in ('shot_id', 'window', 'lyric_line_indices', 'lip_sync_required'):
            if row.get(key) != target[key] or (key == 'lip_sync_required' and type(row.get(key)) is not bool):
                raise ValueError('动态复核逐镜归属/歌词/音乐窗口不一致')
        if (any(row.get(k) != 'pass' for k in ('performance_status', 'sound_action_status', 'continuity_status'))
                or not isinstance(row.get('observation'), str) or not row['observation'].strip()):
            raise ValueError('最终表演/声画动作/连续性尚未逐镜复核')
        if row.get('mouth_status') != ('pass' if target['lip_sync_required'] else 'not_applicable'):
            raise ValueError('最终口型复核未通过，不能以能量或波形相关性代替')
        if target['lip_sync_required']:
            samples = row.get('mouth_samples')
            if not isinstance(samples, list) or len(samples) < 2: raise ValueError('口型镜头至少需要两处具体口形观察')
            times = []
            for sample in samples:
                time = number(sample['time_sec'])
                if (not target['window']['start'] <= time < target['window']['end']
                        or not isinstance(sample.get('observation'), str) or not sample['observation'].strip()):
                    raise ValueError('口形观察缺少有效时间或具体描述')
                times.append(time)
            if len(set(times)) != len(times): raise ValueError('口形观察时间重复')
    return actual


def run_alignment(project, output_name, directory):
    project, directory = Path(project).resolve(), Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    tl = load(project / 'state/timeline.json')
    master = (project / tl['master_audio']).resolve()
    candidate = project / 'output' / output_name
    script = Path(__file__).with_name('align_verify.py')
    paths = [master, candidate, project / 'state/timeline.json', project / 'state/storyboard.json', script]
    versions = {str(p): digest(p) for p in paths}
    commands, outputs = [], []
    run_id = uuid.uuid4().hex
    started = datetime.now(timezone.utc).isoformat()
    receipt_path = directory / 'alignment_execution.json'
    def save_receipt(status):
        stamp = datetime.now(timezone.utc).isoformat()
        receipt_path.write_text(json.dumps({'run_id': run_id, 'started_at': started,
                                'last_updated_at': stamp, 'completed_at': stamp if status != 'running' else None, 'status': status,
                                'commands': commands, 'process_outputs': outputs, 'input_versions': versions,
                                'lip_sync_verified': False, 'scope': 'actual_audio_alignment_not_lip_measurement'},
                                ensure_ascii=False, indent=2), encoding='utf8')
    def execute(cmd, **kwargs):
        commands.append(cmd)
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, **kwargs)
        except BaseException as exc:
            outputs.append({'returncode': None, 'stdout': '', 'stderr': '', 'error_type': type(exc).__name__})
            save_receipt('failed')
            raise
        outputs.append({'returncode': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr})
        save_receipt('running' if result.returncode == 0 else 'failed')
        return result
    save_receipt('running')
    for source, name in ((master, 'source.wav'), (candidate, 'candidate.wav')):
        cmd = ['ffmpeg', '-y', '-v', 'error', '-i', str(source), '-map', '0:a:0',
               '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', str(directory / name)]
        result = execute(cmd)
        if result.returncode: raise ValueError('实际成片音轨提取失败，不用源音频冒充候选音轨')
    report_path = directory / 'alignment.json'
    cmd = [sys.executable, str(script), str(directory / 'source.wav'), str(project / 'state/storyboard.json'),
           str(project / 'state/timeline.json'), '--candidate-wav', str(directory / 'candidate.wav'),
           '--candidate-video', str(candidate), '--out', str(report_path)]
    result = execute(cmd, encoding='utf8')
    receipt = {'run_id': run_id, 'started_at': started, 'completed_at': datetime.now(timezone.utc).isoformat(),
               'commands': commands, 'process_outputs': outputs, 'input_versions': versions,
               'status': 'completed' if result.returncode == 0 else 'failed',
               'lip_sync_verified': False, 'scope': 'actual_audio_alignment_not_lip_measurement'}
    if report_path.is_file(): receipt['report_sha256'] = digest(report_path)
    (directory / 'alignment_execution.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf8')
    if result.returncode or any(digest(p) != sha for p, sha in versions.items()):
        save_receipt('failed')
        raise ValueError('实际对齐失败或执行期间输入已改变')
    report = load(report_path)
    if (report.get('overall') != 'PASS' or report.get('mode') != 'candidate'
            or report.get('candidate_audio_bound_to_video') is not True
            or report.get('candidate_video_sha256') != digest(candidate) or report.get('lip_sync_verified') is not False):
        save_receipt('failed')
        raise ValueError('对齐报告不是当前成片的实际音频验证')
    return report
