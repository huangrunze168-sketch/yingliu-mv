"""视频生成前的剪辑预演批准；不证明运动、表演或口型。"""
import argparse
import json
import hashlib
import uuid
from pathlib import Path
try:
    from .pipeline_contracts import digest, number, atomic_batch, require_no_transaction
    from .media_verify import probe, audio_hash
except ImportError:
    from pipeline_contracts import digest, number, atomic_batch, require_no_transaction
    from media_verify import probe, audio_hash

DIMENSIONS = ('information_progression', 'climax', 'music_rhythm', 'cut_relationships')


def dependencies(project):
    names = ['state/' + name + '.json' for name in
             ('timeline', 'style', 'development', 'story_plan', 'storyboard', 'visual_edit_plan')]
    timeline = json.loads((project / 'state/timeline.json').read_text(encoding='utf-8'))
    names.append(timeline['master_audio'])
    for path in (project / 'input').glob('protagonist.*'): names.append(str(path))
    registry = project / 'state/visual_assets.json'
    sequence = project / 'state/sequence_direction.json'
    development = json.loads((project / 'state/development.json').read_text(encoding='utf-8'))
    # P3.1：默认强制导演序列；revision≠2 不合格
    if development.get('creative_workflow_revision') != 2:
        raise ValueError(
            'creative_workflow_revision 必须为 2；revision≠2 禁止跳过 sequence_direction'
        )
    if not sequence.is_file():
        raise ValueError('工作流缺少导演序列 sequence_direction')
    names.append(str(sequence))
    design = json.loads(sequence.read_text(encoding='utf-8'))
    names.extend(source['path'] for source in design['sources'].values())
    if registry.exists():
        names.append(str(registry))
        assets = json.loads(registry.read_text(encoding='utf-8'))['assets']
        names.extend(a['path'] for a in assets.values())
    versions = {str((project / name).resolve()): digest(project / name) for name in names}
    if (project/'state/reference_roles.json').exists():
        try:
            from .reference_roles import require
        except ImportError:
            from reference_roles import require
        _, identities = require(project)
        versions.update({str(p): value for p, value in identities.items()})
    return versions


def approved(project):
    project = Path(project).resolve()
    errors = []
    try:
        require_no_transaction(project)
        path = project / 'state/animatic_review.json'
        payload = path.read_bytes()
        version = 'sha256:' + hashlib.sha256(payload).hexdigest()
        record = json.loads(payload)
        before = dependencies(project)
        if record.get('project_id') != str(project) or record.get('stage') != 'animatic':
            errors.append('剪辑预演项目/阶段不一致')
        if record.get('decision') != 'approve' or record.get('watched_full_duration') is not True or record.get('simulation_only') is True:
            errors.append('剪辑预演尚未完整观看并批准')
        if record.get('dependencies') != before: errors.append('剪辑预演设计或素材依赖已改变')
        media = project / record['reviewed_media']
        if digest(media) != record['reviewed_media_sha256']: errors.append('剪辑预演媒体已改变')
        data = probe(media)
        video = next(s for s in data['streams'] if s['codec_type'] == 'video')
        if not any(s['codec_type'] == 'audio' for s in data['streams']):
            errors.append('剪辑预演须带音乐，不能以静音视频审音乐节奏')
        timeline = json.loads((project / 'state/timeline.json').read_text(encoding='utf-8'))
        if audio_hash(media) != audio_hash(project / timeline['master_audio']):
            errors.append('剪辑预演音乐须绑定原始母带')
        duration = number(json.loads((project / 'state/timeline.json').read_text(encoding='utf-8'))['duration_sec'])
        if abs(number(video.get('duration', data['format']['duration'])) - duration) > .05:
            errors.append('剪辑预演没有覆盖全曲')
        for name in DIMENSIONS:
            item = record.get('checks', {}).get(name, {})
            if item.get('status') != 'pass' or not str(item.get('evidence', '')).strip():
                errors.append(f'剪辑预演缺少批准证据: {name}')
            findings = [f for f in record.get('timecoded_findings', []) if f.get('dimension') == name]
            if not findings: errors.append(f'剪辑预演缺少时间码: {name}')
            for finding in findings:
                start, end = number(finding['start_sec']), number(finding['end_sec'])
                if not 0 <= start < end <= duration or not str(finding.get('observation', '')).strip():
                    errors.append(f'剪辑预演时间码/观察无效: {name}')
        if dependencies(project) != before:
            errors.append('剪辑预演依赖在检查期间改变，请重新核验')
        if digest(path) != version:
            errors.append('剪辑预演审批版本在检查期间改变，请重新核验')
        if digest(media) != record['reviewed_media_sha256']:
            errors.append('剪辑预演媒体在检查期间改变，请重新核验')
        require_no_transaction(project)
    except Exception as exc:
        errors.append(f'剪辑预演无法核验: {exc}')
    return not errors, errors


def require(project):
    ok, errors = approved(project)
    if not ok: raise ValueError('首次视频生成前须通过剪辑预演: ' + '; '.join(errors))


def update_review(project, action, media=None):
    project = Path(project).resolve()
    require_no_transaction(project)
    path = project / 'state/animatic_review.json'
    previous = path.read_bytes() if path.exists() else None
    version = 'sha256:' + hashlib.sha256(previous).hexdigest() if previous is not None else None
    old = json.loads(previous) if previous is not None else None
    if action == 'invalidate':
        if old is None: raise FileNotFoundError('缺少剪辑预演记录')
        record = {**old, 'previous_decision': old.get('decision'), 'decision': 'pending', 'watched_full_duration': False}
        archive = path.parent / 'review_history' / f'animatic-{uuid.uuid4().hex}.json'
        atomic_batch({path: json.dumps(record, ensure_ascii=False, indent=2), archive: previous}, expected={path: version})
        return record
    if action == 'init' and old is not None: raise FileExistsError('预演记录已存在，拒绝覆盖')
    if action == 'refresh' and (old is None or old.get('decision') != 'pending'):
        raise ValueError('refresh须先显式invalidate，禁止沿用批准')
    if action not in {'init', 'refresh'}: raise ValueError('不支持的预演操作')
    media = media or (old or {}).get('reviewed_media')
    if not media: raise ValueError('须指定实际预演视频')
    media = (project / media).resolve()
    record = {'schema_version': 1, 'stage': 'animatic', 'project_id': str(project),
              'reviewed_media': str(media), 'reviewed_media_sha256': digest(media),
              'dependencies': dependencies(project), 'decision': 'pending', 'watched_full_duration': False,
              'timecoded_findings': [], 'checks': {name: {'status': 'pending', 'evidence': ''} for name in DIMENSIONS},
              'scope': '仅叙事、高潮、节奏及切镜关系；不批准运动/表演/口型'}
    updates = {path: json.dumps(record, ensure_ascii=False, indent=2)}
    if action == 'refresh':
        updates[path.parent / 'review_history' / f'animatic-{uuid.uuid4().hex}.json'] = previous
    atomic_batch(updates, expected={path: version})
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('init', 'check', 'invalidate', 'refresh'))
    parser.add_argument('project_dir', type=Path)
    parser.add_argument('--media', type=Path)
    args = parser.parse_args()
    project = args.project_dir.resolve()
    if args.action != 'check':
        update_review(project, args.action, args.media)
        print('pending'); return
    ok, errors = approved(project)
    print(json.dumps({'approved': ok, 'errors': errors}, ensure_ascii=False))
    raise SystemExit(0 if ok else 1)


if __name__ == '__main__': main()
