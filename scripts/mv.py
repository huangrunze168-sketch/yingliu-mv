#!/usr/bin/env python3
"""映流 MV：本地状态、输入包、证据和媒体处理，不调用视觉模型或供应商。"""
import argparse
import hashlib
import json
import math
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
try:
    from .dependencies import doctor
except ImportError:
    from dependencies import doctor

ROOT = Path(__file__).resolve().parents[1]
STAGES = ('prepare', 'concept', 'storyboard', 'pilot', 'production', 'delivery')
FILES = {
    'prepare': [],
    'concept': ['state/timeline.json', 'state/concept.json'],
    'storyboard': ['state/design.json', 'state/animatic_review.json'],
    'pilot': ['state/pilot_review.json'],
    'production': ['state/edit.json', 'state/clips_review.json', 'state/jobs.json'],
    'delivery': ['output/final_mv.mp4', 'state/final_review.json'],
}
REFS = {'prepare': 'data.md', 'concept': 'creative.md', 'storyboard': 'creative.md',
        'pilot': 'vision-generation.md', 'production': 'vision-generation.md', 'delivery': 'delivery.md'}


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf8')
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def local(project, name):
    path = (project / name).resolve()
    path.relative_to(project.resolve())
    return path


def number(value, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('需要有限数值')
    if value < 0 or (positive and value <= 0):
        raise ValueError('时间/尺寸必须为非负值，时长/尺寸须大于零')
    return float(value)


def nonempty(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('必需文本尚未填写')
    return value


def run(command):
    result = subprocess.run(command, capture_output=True, text=True, encoding='utf8', errors='replace')
    if result.returncode:
        raise ValueError(result.stderr[-3000:] or result.stdout[-3000:])
    return result.stdout


def probe(path):
    return json.loads(run(['ffprobe', '-v', 'error', '-show_format', '-show_streams', '-of', 'json', str(path)]))


def config(project):
    cfg = read(project / 'project.json')
    for key in ('width', 'height', 'fps'):
        number(cfg[key], True)
    for key in ('width', 'height'):
        if not isinstance(cfg[key], int) or cfg[key] % 2:
            raise ValueError('width/height 须为正偶整数')
    return cfg


def init(project, simulation=False):
    project.mkdir(parents=True, exist_ok=False)
    for folder in ('input', 'state/observations', 'clips', 'output', 'prompts'):
        (project / folder).mkdir(parents=True, exist_ok=True)
    write(project / 'project.json', {
        'name': project.name, 'workflow': 'yingliu-mv', 'version': '1.0.2', 'simulation': simulation,
        'audio': 'input/song.wav', 'lyrics': 'input/lyrics.txt', 'width': 1280, 'height': 720, 'fps': 24,
        'backend': None, 'budget': {'amount': None, 'currency': None}, 'authorization': '',
        'agent_capabilities': {'image': 'unknown', 'video': 'unknown', 'audio': 'unknown'},
    })
    review = {'media': '', 'observations': [], 'decision': 'pending', 'evidence': '', 'unverified': ['待实际审查']}
    defaults = {
        'timeline': {'duration': 0, 'verified': False, 'evidence_path': '', 'segments': []},
        'concept': {'treatment': '', 'visual_direction': '', 'music_sections': []},
        'design': {'global': {}, 'scenes': {}, 'shots': []},
        'animatic_review': review, 'pilot_review': review, 'final_review': review,
        'clips_review': {'reviews': []}, 'edit': {'clips': []}, 'jobs': {'jobs': []}, 'approvals': {},
    }
    for name, value in defaults.items():
        write(project / f'state/{name}.json', value)
    return {'project': str(project), 'next': '填写 project.json，放入原曲和歌词；运行 status/pack'}


def windows(rows, duration):
    if not rows:
        raise ValueError('时间窗口为空')
    cursor = 0.0
    for row in rows:
        start, end = number(row['start']), number(row['end'], True)
        if abs(start - cursor) > 0.001 or end <= start:
            raise ValueError('时间窗口存在空缺、重叠、乱序或非正时长')
        cursor = end
    if abs(cursor - duration) > 0.001:
        raise ValueError('时间窗口没有覆盖全曲')


def timeline(project):
    data = read(project / 'state/timeline.json')
    duration = number(data['duration'], True)
    if data['verified'] is not True:
        raise ValueError('时间轴尚未实际复核')
    evidence = local(project, nonempty(data['evidence_path']))
    if not evidence.is_file() or not evidence.stat().st_size:
        raise ValueError('时间轴依据文件缺失或为空')
    windows(data['segments'], duration)
    return data


def design(project):
    data = read(project / 'state/design.json')
    if not isinstance(data['global'], dict) or not isinstance(data['scenes'], dict):
        raise ValueError('global/scenes 须为对象')
    windows(data['shots'], timeline(project)['duration'])
    ids = set()
    for row in data['shots']:
        sid = row['id']
        if not isinstance(sid, str) or not re.fullmatch(r'SH[0-9]{3,6}', sid) or sid in ids:
            raise ValueError('镜头 ID 须唯一并符合 SH001 格式')
        ids.add(sid)
        if row['scene'] not in data['scenes']:
            raise ValueError(f'{sid} 场景不存在')
        for key in ('action', 'camera', 'cut_reason'):
            nonempty(row[key])
        if not isinstance(row['lip_sync'], bool):
            raise ValueError('lip_sync 须为布尔值')
    return data


def observe(project, record_path):
    record_path = Path(record_path)
    if not record_path.is_absolute():
        record_path = local(project, record_path)
    record = read(record_path)
    simulation = config(project).get('simulation', False)
    if record.get('simulation', False) != simulation:
        raise ValueError('模拟观察与真实项目不能混用')
    nonempty(record['observer'])
    nonempty(record['evidence'])
    if record['scope'] not in ('image', 'sampled_frames', 'full_video', 'audiovisual'):
        raise ValueError('未知观察范围')
    if not isinstance(record['findings'], list) or not isinstance(record['unverified'], list):
        raise ValueError('findings/unverified 须为数组')
    paths = [nonempty(record['media']), *record.get('references', [])]
    if record.get('frame_manifest'):
        paths.append(record['frame_manifest'])
        manifest = read(local(project, record['frame_manifest']))
        if manifest['source'] != record['media'] or manifest['source_sha256'] != sha(local(project, record['media'])):
            raise ValueError('抽帧来源与当前观察素材不一致')
        paths.extend(row['path'] for row in manifest['frames'])
    record['bindings'] = {p: sha(local(project, p)) for p in paths}
    target = f'state/observations/{uuid.uuid4().hex}.json'
    write(project / target, record)
    return {'observation': target, 'scope': record['scope'], 'unverified': record['unverified']}


def review(project, record, final=False):
    if record.get('decision') != 'approve' or record.get('unverified') != []:
        raise ValueError('审查待完成或仍有未核实项')
    nonempty(record.get('evidence'))
    media = nonempty(record['media'])
    refs = record.get('observations', [])
    if not refs:
        raise ValueError('缺少实际观察记录')
    sufficient = False
    for ref in refs:
        item = read(local(project, ref))
        if item.get('simulation', False) != config(project).get('simulation', False):
            raise ValueError('模拟观察不能作为真实制作审查')
        if item.get('media') != media or not item.get('bindings') or item['bindings'].get(media) != sha(local(project, media)):
            raise ValueError('观察未绑定当前媒体')
        for path, fingerprint in item['bindings'].items():
            if sha(local(project, path)) != fingerprint:
                raise ValueError('观察素材或参考已变化，需要复核')
        if item.get('unverified'):
            raise ValueError('观察仍有未核实项')
        if any(x.get('severity') in ('major', 'critical') for x in item.get('findings', [])):
            raise ValueError('观察存在未解决的主要问题')
        sufficient |= item['scope'] == 'audiovisual' if final else item['scope'] in ('full_video', 'audiovisual')
    if not sufficient:
        raise ValueError('观察范围不足：抽帧不能代替动态审查，最终须有声审查')


def render_evidence(project, media, source):
    evidence = read(local(project, media).with_suffix('.technical.json'))
    if evidence.get('sha256') != sha(local(project, media)) or evidence.get('source_sha256') != sha(project / source):
        raise ValueError('渲染与当前媒体/设计/剪辑版本不一致')
    if not evidence.get('input_bindings'):
        raise ValueError('缺少渲染输入版本')
    for path, fingerprint in evidence['input_bindings'].items():
        if sha(local(project, path)) != fingerprint:
            raise ValueError('渲染输入已变化，请重做并重审')


def check(project, stage):
    cfg = config(project)
    if stage == 'prepare':
        for key in ('audio', 'lyrics'):
            path = local(project, nonempty(cfg[key]))
            if not path.is_file() or path.stat().st_size == 0:
                raise ValueError(f'{key} 素材缺失或为空')
    elif stage == 'concept':
        timeline(project)
        item = read(project / 'state/concept.json')
        nonempty(item['treatment'])
        nonempty(item['visual_direction'])
        if not item['music_sections']:
            raise ValueError('缺少音乐段落分析')
    elif stage == 'storyboard':
        design(project)
        item = read(project / 'state/animatic_review.json')
        review(project, item)
        render_evidence(project, item['media'], 'state/design.json')
    elif stage == 'pilot':
        review(project, read(project / 'state/pilot_review.json'))
    elif stage == 'production':
        clips = read(project / 'state/edit.json')['clips']
        reviews = read(project / 'state/clips_review.json')['reviews']
        if not clips:
            raise ValueError('选段清单为空')
        total = 0.0
        for clip in clips:
            number(clip['in'])
            total += number(clip['duration'], True)
            streams = probe(local(project, clip['media']))
            if not any(s['codec_type'] == 'video' for s in streams['streams']):
                raise ValueError('选段素材没有视频轨')
            if clip['in'] + clip['duration'] > float(streams['format']['duration']) + 0.001:
                raise ValueError('选段超出素材时长，请先修改 edit.json')
            nonempty(clip['receipt'])
            if not local(project, clip['receipt']).is_file():
                raise ValueError('素材来源回执缺失')
            matches = [r for r in reviews if r['media'] == clip['media']]
            if not matches:
                raise ValueError('选段缺少逐段审查')
            review(project, matches[-1])
        if abs(total - timeline(project)['duration']) > 1 / cfg['fps']:
            raise ValueError('选段总时长与全曲不一致')
        for job in read(project / 'state/jobs.json')['jobs']:
            if job['status'] not in ('completed', 'failed', 'cancelled'):
                raise ValueError('存在未结任务，先回查')
            number(job['cost'])
            nonempty(job['currency'])
            if not local(project, nonempty(job['receipt'])).is_file():
                raise ValueError('缺少费用/任务回执')
    elif stage == 'delivery':
        item = read(project / 'state/final_review.json')
        if item['media'] != 'output/final_mv.mp4':
            raise ValueError('最终审查须绑定 output/final_mv.mp4')
        review(project, item, final=True)
        render_evidence(project, item['media'], 'state/edit.json')
        info = probe(project / item['media'])
        if not any(s['codec_type'] == 'audio' for s in info['streams']):
            raise ValueError('成片缺音轨')
        video = next(s for s in info['streams'] if s['codec_type'] == 'video')
        if video['width'] != cfg['width'] or video['height'] != cfg['height']:
            raise ValueError('成片画幅不符')
        if abs(float(info['format']['duration']) - timeline(project)['duration']) > max(0.1, 2 / cfg['fps']):
            raise ValueError('成片时长不符')


def bindings(project, stage):
    """保守绑定前序证据及显式媒体引用；不扫描无关项目。"""
    cfg = config(project)
    pending = ['project.json', cfg['audio'], cfg['lyrics'], str(ROOT / 'scripts/mv.py')]
    for name in STAGES[:STAGES.index(stage) + 1]:
        pending.extend(FILES[name])
    result = {}
    # 当前工作流版本独立纳入摘要；不允许项目 JSON 引用外部文件。
    tool = pending.pop(3)
    result['@workflow'] = sha(tool)
    result['@dependencies'] = sha(ROOT / 'scripts/dependencies.py')
    while pending:
        name = pending.pop()
        if name in result:
            continue
        path = local(project, name)
        result[name] = sha(path)
        if path.suffix.lower() == '.json' and name != 'project.json':
            def walk(value):
                if isinstance(value, dict):
                    for key, child in value.items():
                        if key in ('media', 'image', 'evidence_path', 'receipt', 'frame_manifest') and isinstance(child, str) and child:
                            pending.append(child)
                        elif key == 'bindings' and isinstance(child, dict):
                            pending.extend(child)
                        elif key in ('observations', 'references') and isinstance(child, list):
                            pending.extend(child)
                        else:
                            walk(child)
                elif isinstance(value, list):
                    for child in value:
                        walk(child)
            walk(read(path))
    return result


def status(project):
    approvals = read(project / 'state/approvals.json')
    for stage in STAGES:
        try:
            check(project, stage)
            current = bindings(project, stage)
        except (ValueError, OSError, KeyError, TypeError, StopIteration) as exc:
            return {'status': 'blocked', 'stage': stage, 'issue': str(exc), 'reference': 'references/' + REFS[stage]}
        old = approvals.get(stage)
        if not old or old.get('bindings') != current:
            return {'status': 'waiting_approval', 'stage': stage, 'issue': '当前产物需要用户确认'}
    simulation = config(project).get('simulation', False)
    return {'status': 'simulation_completed' if simulation else 'completed', 'stage': 'delivery',
            'simulation': simulation}


def approve(project, reviewer, evidence):
    nonempty(reviewer)
    nonempty(evidence)
    current = status(project)
    if current['status'] != 'waiting_approval':
        raise ValueError('当前阶段未通过检查或已全部完成：' + json.dumps(current, ensure_ascii=False))
    approvals = read(project / 'state/approvals.json')
    stage = current['stage']
    approvals[stage] = {'reviewer': reviewer, 'evidence': evidence, 'bindings': bindings(project, stage),
                        'simulation': config(project).get('simulation', False)}
    write(project / 'state/approvals.json', approvals)
    return status(project)


def pack(project, shot_id=None):
    current = status(project)
    stage = current['stage']
    selected = None
    if shot_id:
        data = design(project)
        index = next((i for i, row in enumerate(data['shots']) if row['id'] == shot_id), None)
        if index is None:
            raise ValueError('镜头不存在')
        rows = data['shots'][max(0, index-1):index+2]
        selected = {'global': data['global'], 'scenes': {row['scene']: data['scenes'][row['scene']] for row in rows}, 'shots': rows}
    cfg = config(project)
    inputs = {'prepare': ['project.json'], 'concept': ['project.json', cfg['audio'], cfg['lyrics']],
              'storyboard': ['state/timeline.json', 'state/concept.json'],
              'pilot': ['prompts/index.json', 'state/animatic_review.json'],
              'production': ['state/pilot_review.json', 'state/jobs.json'],
              'delivery': ['state/edit.json', 'state/clips_review.json']}
    value = {'current': current, 'reference': str(ROOT / 'references' / REFS[stage]),
             'inputs': inputs[stage], 'outputs': FILES[stage], 'shot_context': selected,
             'instruction': '只读当前输入；需要观察时使用 Agent 原生工具。外部工具与人工输入未完成时如实交接。'}
    target = project / 'state/context' / (f'{shot_id}.json' if shot_id else 'current.json')
    write(target, value)
    return {'context': str(target), 'stage': stage}


def review_init(project, stage, media, refs):
    """创建待审模板；不声称观察或自动批准。"""
    targets = {'storyboard': 'animatic_review', 'pilot': 'pilot_review', 'delivery': 'final_review'}
    sha(local(project, media))
    for ref in refs:
        read(local(project, ref))
    target = project / f'state/{targets[stage]}.json'
    if target.is_file() and read(target).get('media'):
        raise ValueError('已有审查记录，请修改现有记录并重审；不覆盖')
    write(target, {'media': media, 'observations': refs, 'decision': 'pending', 'evidence': '',
                   'unverified': ['请实际审查后填写'], 'simulation': config(project).get('simulation', False)})
    return {'review': target.relative_to(project).as_posix(), 'status': 'pending',
            'next': '实际审查后填写 evidence/unverified/decision；再 status，不自动批准'}


def frame_plan(lengths, fps):
    """按累计音乐边界取整，避免逐镜取整造成切点累计漂移。"""
    cursor, previous, result = 0.0, 0, []
    for length in lengths:
        cursor += number(length, True)
        boundary = math.floor(cursor * fps + 0.5)
        count = boundary - previous
        if count <= 0:
            raise ValueError('镜头不足一帧，请调整镜头边界')
        result.append(count)
        previous = boundary
    return result


def compile_prompts(project):
    data = design(project)
    output = project / 'prompts'
    rows = {}
    for row in data['shots']:
        shared = {**data['global'], **data['scenes'][row['scene']]}
        rows[row['id']] = {'shot_id': row['id'], 'start': row['start'], 'end': row['end'],
                           'resolved': {**shared, **row}, 'source_sha256': sha(project / 'state/design.json')}
    output.mkdir(exist_ok=True)
    # 仅处理本编译器声明的 SH 文件；旧镜头移入历史，不删除用户素材。
    for path in output.glob('SH*.json'):
        if path.stem not in rows:
            archive = output / 'history' / uuid.uuid4().hex
            archive.mkdir(parents=True)
            path.replace(archive / path.name)
    for sid, row in rows.items():
        write(output / (sid + '.json'), row)
    write(output / 'index.json', {'source_sha256': sha(project / 'state/design.json'),
                                 'shots': [f'prompts/{sid}.json' for sid in rows]})
    return {'compiled': len(rows), 'index': 'prompts/index.json'}


def frames(project, media, count):
    if not 1 <= count <= 24:
        raise ValueError('抽帧数须在 1–24 之间')
    source = local(project, media)
    info = probe(source)
    duration = number(float(info['format']['duration']), True)
    folder = project / 'state/frames' / uuid.uuid4().hex
    folder.mkdir(parents=True)
    records = []
    for i in range(count):
        point = duration * (i + 0.5) / count
        path = folder / f'{i:02}.jpg'
        run(['ffmpeg', '-v', 'error', '-nostdin', '-ss', str(point), '-i', str(source), '-frames:v', '1',
             '-vf', 'scale=960:-2', '-y', str(path)])
        if not path.is_file():
            raise ValueError('没有抽出视频帧')
        records.append({'time': round(point, 6), 'path': path.relative_to(project).as_posix(), 'sha256': sha(path)})
    target = folder / 'manifest.json'
    write(target, {'source': media, 'source_sha256': sha(source), 'scope': 'sampled_frames', 'frames': records})
    return {'manifest': target.relative_to(project).as_posix(), 'count': count, 'scope': 'sampled_frames'}


def observations(project, media):
    fingerprint = sha(local(project, media))
    records = []
    for path in (project / 'state/observations').glob('*.json'):
        item = read(path)
        if item.get('media') != media:
            continue
        valid = item.get('bindings', {}).get(media) == fingerprint
        for name, value in item.get('bindings', {}).items():
            target = local(project, name)
            valid = valid and target.is_file() and sha(target) == value
        records.append({'path': path.relative_to(project).as_posix(), 'current': valid,
                        'scope': item.get('scope'), 'unverified': item.get('unverified'), 'findings': item.get('findings')})
    return {'media': media, 'records': records, 'instruction': '仅复用 current=true 且观察范围满足当前问题的记录'}


def render(project, mode):
    cfg = config(project)
    duration = timeline(project)['duration']
    if mode == 'final':
        state = status(project)
        if state['stage'] != 'delivery':
            raise ValueError('正式渲染要求前五阶段当前批准有效：' + json.dumps(state, ensure_ascii=False))
        rows = read(project / 'state/edit.json')['clips']
    else:
        rows = [{'media': row['image'], 'in': 0, 'duration': row['end'] - row['start']} for row in design(project)['shots']]
    audio = local(project, cfg['audio'])
    source_name = 'state/design.json' if mode == 'preview' else 'state/edit.json'
    planned_frames = frame_plan([row['duration'] for row in rows], cfg['fps'])
    input_paths = ['project.json', cfg['audio'], source_name, *[row['media'] for row in rows]]
    input_bindings = {name: sha(local(project, name)) for name in input_paths}
    if abs(float(probe(audio)['format']['duration']) - duration) > max(0.1, 2 / cfg['fps']):
        raise ValueError('原曲时长与权威时间轴不一致')
    output = project / 'output' / ('animatic_preview.mp4' if mode == 'preview' else 'final_mv.mp4')
    # 临时渲染目录固定在项目 output 内，退出前验证清理目标。
    with tempfile.TemporaryDirectory(prefix='yingliu-', dir=project / 'output') as temp:
        folder = Path(temp)
        folder.resolve().relative_to((project / 'output').resolve())
        segments = []
        for i, row in enumerate(rows):
            source = local(project, row['media'])
            length = number(row['duration'], True)
            command = ['ffmpeg', '-v', 'error', '-nostdin', '-y']
            if mode == 'preview':
                command += ['-loop', '1', '-i', str(source)]
            else:
                start = number(row['in'])
                if start + length > float(probe(source)['format']['duration']) + 0.001:
                    raise ValueError('选段超出原始素材时长')
                command += ['-ss', str(start), '-i', str(source)]
            segment = folder / f'{i:05}.mp4'
            command += ['-frames:v', str(planned_frames[i]), '-an', '-vf',
                        f"scale={cfg['width']}:{cfg['height']}:force_original_aspect_ratio=decrease,pad={cfg['width']}:{cfg['height']}:(ow-iw)/2:(oh-ih)/2,setsar=1",
                        '-r', str(cfg['fps']), '-c:v', 'libx264', '-preset', 'fast', '-crf', '18', '-pix_fmt', 'yuv420p', str(segment)]
            run(command)
            actual = next(s for s in probe(segment)['streams'] if s['codec_type'] == 'video')
            if int(actual.get('nb_frames', 0)) != planned_frames[i]:
                raise ValueError('片段帧数不足，请检查选段或源视频')
            segments.append(segment)
        listing = folder / 'concat.txt'
        listing.write_text('\n'.join(f"file '{p.name}'" for p in segments), encoding='utf8')
        joined = folder / 'joined.mp4'
        run(['ffmpeg', '-v', 'error', '-nostdin', '-y', '-f', 'concat', '-safe', '1', '-i', str(listing), '-c', 'copy', str(joined)])
        codec = next(s['codec_name'] for s in probe(audio)['streams'] if s['codec_type'] == 'audio')
        audio_codec = 'copy' if codec in ('aac', 'mp3', 'alac') else 'alac'
        candidate = folder / 'candidate.mp4'
        run(['ffmpeg', '-v', 'error', '-nostdin', '-y', '-i', str(joined), '-i', str(audio),
             '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'copy', '-c:a', audio_codec, '-t', str(duration),
             '-movflags', '+faststart', str(candidate)])
        info = probe(candidate)
        if abs(float(info['format']['duration']) - duration) > max(0.1, 2 / cfg['fps']):
            raise ValueError('渲染时长核验失败，保留旧输出')
        if any(sha(local(project, name)) != value for name, value in input_bindings.items()):
            raise ValueError('渲染期间输入改变，保留旧输出')
        candidate.replace(output)
    write(output.with_suffix('.technical.json'), {'media': output.relative_to(project).as_posix(),
          'sha256': sha(output), 'master_sha256': sha(audio), 'audio_codec': audio_codec, 'probe': info,
          'source_sha256': sha(project / ('state/design.json' if mode == 'preview' else 'state/edit.json')),
          'input_bindings': input_bindings,
          'frame_plan': planned_frames,
          'artistic_approval': False, 'lip_sync_verified': False})
    return {'media': str(output), 'artistic_approval': False, 'next': '实际观察后登记审查；渲染不自动批准'}


def report(project):
    state = status(project)
    cfg = config(project)
    final = project / 'output/final_mv.mp4'
    result = {'workflow': 'yingliu-mv', 'project': cfg['name'], 'state': state,
              'media_sha256': sha(final) if final.is_file() else None,
              'approval_type': '模拟夹具声明' if cfg.get('simulation') else '用户确认声明；非独立签名',
              'simulation': cfg.get('simulation', False), 'production_acceptance': state['status'] == 'completed',
              'cost_totals': {}}
    for job in read(project / 'state/jobs.json')['jobs']:
        if job.get('cost') is not None:
            currency = nonempty(job.get('currency'))
            result['cost_totals'][currency] = result['cost_totals'].get(currency, 0) + number(job['cost'])
    write(project / 'output/delivery_report.json', result)
    return {'report': 'output/delivery_report.json', 'state': state}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest='command', required=True)
    doctor_parser = subs.add_parser('doctor')
    doctor_parser.add_argument('project', type=Path, nargs='?')
    doctor_parser.add_argument('--smoke', action='store_true')
    for name in ('init', 'status', 'pack', 'approve', 'compile', 'frames', 'observe', 'observations', 'render', 'report', 'review-init'):
        item = subs.add_parser(name)
        item.add_argument('project', type=Path)
        if name == 'init':
            item.add_argument('--simulation', action='store_true')
        elif name == 'pack':
            item.add_argument('--shot')
        elif name == 'approve':
            item.add_argument('--reviewer', required=True)
            item.add_argument('--evidence', required=True)
        elif name == 'frames':
            item.add_argument('--media', required=True)
            item.add_argument('--count', type=int, default=8)
        elif name == 'observe':
            item.add_argument('--record', type=Path, required=True)
        elif name == 'observations':
            item.add_argument('--media', required=True)
        elif name == 'render':
            item.add_argument('--mode', choices=('preview', 'final'), required=True)
        elif name == 'review-init':
            item.add_argument('--stage', choices=('storyboard', 'pilot', 'delivery'), required=True)
            item.add_argument('--media', required=True)
            item.add_argument('--observations', nargs='+', required=True)
    args = parser.parse_args()
    if args.command == 'doctor':
        return doctor(args.project, args.smoke)
    project = args.project.resolve()
    actions = {'init': lambda: init(project, args.simulation), 'status': lambda: status(project),
               'pack': lambda: pack(project, args.shot), 'approve': lambda: approve(project, args.reviewer, args.evidence),
               'compile': lambda: compile_prompts(project), 'frames': lambda: frames(project, args.media, args.count),
               'observe': lambda: observe(project, args.record), 'render': lambda: render(project, args.mode),
               'observations': lambda: observations(project, args.media),
               'review-init': lambda: review_init(project, args.stage, args.media, args.observations),
               'report': lambda: report(project)}
    return actions[args.command]()


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf8')
    try:
        result = main()
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        if result.get('local_ready') is False:
            sys.exit(1)
    except (ValueError, OSError, KeyError, TypeError, StopIteration) as error:
        print(json.dumps({'status': 'error', 'issue': str(error)}, ensure_ascii=False))
        sys.exit(1)
