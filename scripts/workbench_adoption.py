"""安全采纳工作台草稿：校验当前依据、原子归档与撤销下游批准，不自动认证听看。"""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import uuid

try:
    from .pipeline_contracts import atomic_batch, digest, require_no_transaction, number
    from .storyboard_build import build as build_storyboard
    from .visual_development import validate as validate_development
    from .sequence_direction import require as require_sequence
except ImportError:
    from pipeline_contracts import atomic_batch, digest, require_no_transaction, number
    from storyboard_build import build as build_storyboard
    from visual_development import validate as validate_development
    from sequence_direction import require as require_sequence

DESIGNS = ('style', 'music_structure', 'development', 'story_plan', 'sequence_direction', 'visual_edit_plan')


def encoded(value):
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n'


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def basis(project, bundle, imported):
    project, bundle, imported = Path(project).resolve(), Path(bundle).resolve(), Path(imported).resolve()
    imported.relative_to(project/'state/review_workbench')
    require_no_transaction(project)
    for name in ('.workflow-execution.lock', '.asr-execution.lock'):
        if (project/name).exists():
            raise ValueError('上游仍在执行，禁止采纳草稿')
    version = digest(imported)
    record = read(imported)
    session_path = bundle/'session.json'
    session_version = digest(session_path)
    session = read(session_path)
    if (record.get('project_id') != str(project) or session.get('project_id') != str(project)
            or record.get('session_id') != session.get('session_id')
            or record.get('session_sha256') != session_version
            or record.get('decision') != 'pending' or record.get('dynamic_verified') is not False
            or record.get('artistic_approval') is not False):
        raise ValueError('采纳来源不是本项目有效待核会话')
    payload = record.get('payload')
    if (not isinstance(payload, dict) or payload.get('decision') != 'pending'
            or payload.get('session_id') != session.get('session_id')):
        raise ValueError('缺少待核草稿内容')
    versions = {Path(p).resolve(): sha for p, sha in session['inputs'].items()}
    required = [project/'state/timeline.json', project/'input/lyrics.txt',
                *[project/'state'/f'{name}.json' for name in DESIGNS]]
    if not versions or any(p not in versions for p in required):
        raise ValueError('会话缺少时间轴、歌词或六份设计依据，请用新版重新构建')
    for p, sha in versions.items():
        if (digest(p) if p.is_file() else None) != sha:
            raise ValueError('草稿已过期: '+str(p))
    for asset in session['assets'].values():
        path = (bundle/asset['file']).resolve()
        path.relative_to(bundle)
        if digest(path) != asset['sha256']:
            raise ValueError('审片副本已改变')
        versions[path] = asset['sha256']
    versions.update({imported: version, session_path: session_version})
    return copy.deepcopy(payload), versions


def invalidations(project, run_id):
    """返回同事务写入，保留旧文件；文件集合变化也必须重试。"""
    updates, expected = {}, {}
    groups = [('state/reviews', '*.json'), ('state/deliveries', '*.json'), ('state/executions', '*.json')]
    targets = [project/'state/director_review.json', project/'state/animatic_review.json',
               project/'state/repair_assessment.json']
    memberships = {}
    for folder, pattern in groups:
        parent = project/folder
        files = sorted(parent.glob(pattern)) if parent.exists() else []
        memberships[(folder, pattern)] = files
        targets += files
    for path in targets:
        path.resolve().relative_to(project)
        expected[path] = digest(path) if path.is_file() else None
        if not path.is_file():
            continue
        raw = path.read_bytes()
        old = json.loads(raw)
        replacement = {**old, 'invalidation_reason': '工作台草稿采纳，须按新依据重新执行/审片',
                       'invalidated_by': run_id}
        if path.parent.name == 'executions':
            replacement.update(status='invalidated', errors=['采纳了新的上游版本，旧执行不再有效'])
        elif path.parent.name == 'deliveries':
            replacement.update(status='failed', ready_for_delivery=False, user_received=False)
        elif path.name == 'repair_assessment.json':
            replacement.update(assessment='invalidated')
        else:
            replacement.update(previous_decision=old.get('decision'), decision='pending',
                               watched_full_duration=False, batch_generation_allowed=False)
        archive = project/'state/adoption_history'/run_id/path.relative_to(project)
        updates[archive], updates[path] = raw, encoded(replacement)
        expected[archive] = None
    return updates, expected, memberships


def adopt(project, bundle, imported, *, scope):
    if scope not in {'design', 'timing'}:
        raise ValueError('只能明确采纳design或timing，不接受任意写入')
    project = Path(project).resolve()
    payload, expected = basis(project, bundle, imported)
    run_id = uuid.uuid4().hex
    updates = {}
    if scope == 'timing':
        path = project/'state/timeline.json'
        timeline = read(path)
        originals = [r for r in timeline['lines'] if r.get('type') != 'interlude']
        rows = payload.get('lines', [])
        rows = [r for r in rows if r.get('type') != 'interlude']
        machine_source = payload.get('machine_timing_source')
        aligned = None
        if machine_source:
            try:
                from .alignment_evidence import require as require_alignment
            except ImportError:
                from alignment_evidence import require as require_alignment
            job, result, checked = require_alignment(project, timeline, machine_source)
            job_path = (project/machine_source['job_path']).resolve()
            result_path = Path(job['result_path']).resolve()
            aligned = result['lines']
            if len(aligned) != len(rows) or any(
                any(a.get(k) != b.get(k) for k in ('index','text','speaker'))
                or a['start_candidate'] != b['start'] or a['end_candidate'] != b['end']
                for a,b in zip(aligned, rows)):
                raise ValueError('草稿边界未逐行对应实际强制对齐结果')
            expected.update(checked)
            timeline['machine_candidate_source'] = {'job_path': str(job_path), 'result_path': str(result_path),
                                                    'run_id': job['run_id'], 'status': 'unreviewed'}
        if len(rows) != len(originals):
            raise ValueError('时间草稿必须保留全部官方歌词')
        previous = 0
        corrected = []
        for old, row in zip(originals, rows):
            if any(old.get(k) != row.get(k) for k in ('index', 'type', 'text')):
                raise ValueError('禁止采纳时改写官方歌词身份')
            start, end = number(row['start']), number(row['end'])
            if not 0 <= previous <= start < end <= number(timeline['duration_sec']):
                raise ValueError('歌词边界倒序、重叠或超出原曲')
            if not str(row.get('speaker', '')).strip() or not str(row.get('observation', '')).strip():
                raise ValueError('每行须有演唱者及具体边界复核说明')
            corrected.append({**old, 'start': start, 'end': end, 'speaker': row['speaker']})
            previous = end
        interludes = payload.get('interludes')
        if interludes is None:
            # 兼容旧导出：缺字段代表未编辑，不能默默删除已经登记的间奏。
            interludes = [r for r in timeline['lines'] if r.get('type') == 'interlude']
        if not isinstance(interludes, list):
            raise ValueError('间奏必须逐段记录')
        for index, row in enumerate(interludes, max(r['index'] for r in corrected)+1):
            start, end = number(row['start']), number(row['end'])
            if not (0 <= start < end <= timeline['duration_sec']) or not str(row.get('observation', '')).strip():
                raise ValueError('间奏须有有效区间与实际观察说明')
            corrected.append({'index': index, 'type': 'interlude', 'start': start, 'end': end,
                              'text': '', 'observation': row['observation'], 'classification_status': 'pending'})
        corrected.sort(key=lambda row: row['start'])
        if any(a['end'] > b['start'] for a, b in zip(corrected, corrected[1:])):
            raise ValueError('间奏与人声或另一间奏重叠')
        # 边界修订后旧空档也须重算，不能保留已过期的ASR空白范围。
        gaps, cursor = [], 0.0
        for row in corrected:
            if row['start'] > cursor:
                gaps.append([cursor, row['start']])
            cursor = row['end']
        if cursor < timeline['duration_sec']:
            gaps.append([cursor, timeline['duration_sec']])
        timeline['unclassified_audio_ranges'] = gaps
        timeline.update(lines=corrected, authority='pending_workbench_review', production_timing_verified=False,
                        pending_review=[r['index'] for r in corrected], adoption_id=run_id)
        updates[path] = encoded(timeline)
        review_path = project/'state/timing_review.json'
        expected[review_path] = digest(review_path)
        review = read(review_path)
        by_id = {r['index']: r for r in review['lines']}
        review.update(decision='pending', reviewer='', reviewed_full_audio=False,
                      timeline_sha256='sha256:'+hashlib.sha256(updates[path].encode()).hexdigest(),
                      lines=[{**by_id.get(r['index'], {}), **r,
                              'observation': next(v['observation'] for v in rows if v['index'] == r['index'])}
                             for r in corrected if r['type'] != 'interlude'])
        if aligned is not None:
            review.update(boundary_source='forced_alignment', alignment_run_id=job['run_id'],
                          alignment_result_sha256=job['result_sha256'])
            for row, group in zip(review['lines'], aligned):
                row['forced_alignment_word_range'] = [group['word_start_index'], group['word_end_index']-1]
        else:
            timeline.pop('machine_candidate_source', None)
            updates[path] = encoded(timeline)
            review['timeline_sha256'] = 'sha256:'+hashlib.sha256(updates[path].encode()).hexdigest()
            for key in ('boundary_source','alignment_run_id','alignment_result_sha256'):
                review.pop(key, None)
        updates[review_path] = encoded(review)
    else:
        drafts = payload.get('design_drafts', {})
        if any(name not in drafts or not isinstance(drafts[name], dict) for name in DESIGNS):
            raise ValueError('必须同时提供六份设计，禁止部分新设计与旧设计混用')
        values = {name: copy.deepcopy(drafts[name]) for name in DESIGNS}
        for value in values.values():
            if value.get('artistic_approval') is True:
                raise ValueError('设计草稿不能携带艺术批准')
            value.pop('draft_only', None)
            value.pop('required_shot_fields', None)
        values['development'].setdefault('treatment', {})['status'] = 'proposed'
        style_basis = {k: v for k, v in values['style'].items() if k != 'fingerprint'}
        values['style']['fingerprint'] = 'sha256:'+hashlib.sha256(
            json.dumps(style_basis, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()
        values['visual_edit_plan'].update(director_cut_approved=False, timing_status='pending_director_review')
        values['sequence_direction']['artistic_approval'] = False
        analysis = project/'analysis'
        analysis.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='adoption-check-', dir=analysis) as directory:
            staged = Path(directory)
            (staged/'state').mkdir()
            (staged/'state/timeline.json').write_bytes((project/'state/timeline.json').read_bytes())
            for name, value in values.items():
                (staged/'state'/f'{name}.json').write_text(encoded(value), encoding='utf-8')
            sb = build_storyboard(staged)
            if sb.get('story_mode') != 'zy_story_visual_development':
                raise ValueError('不接受空骨架充当正式设计')
            errors = validate_development(values['development'], sb, values['style'], project)
            require_sequence(staged)
            if errors:
                raise ValueError('设计契约未通过: '+'; '.join(errors))
            for name in (*DESIGNS, 'storyboard'):
                updates[project/'state'/f'{name}.json'] = (staged/'state'/f'{name}.json').read_bytes()
            for source in values['sequence_direction']['sources'].values():
                expected[Path(source['path']).resolve()] = source['sha256']
    changed = [p for p, data in updates.items() if not p.is_file() or p.read_bytes() != (data if isinstance(data, bytes) else data.encode())]
    if not changed:
        raise ValueError('草稿没有实际变化，拒绝制造新采纳记录')
    for path in list(updates):
        path.resolve().relative_to(project)
        expected.setdefault(path, digest(path) if path.is_file() else None)
        if path.is_file():
            archive = project/'state/adoption_history'/run_id/path.relative_to(project)
            archive.resolve().relative_to(project)
            expected[archive] = None
            updates[archive] = path.read_bytes()
    invalid, versions, memberships = invalidations(project, run_id)
    updates.update(invalid)
    expected.update(versions)
    receipt_path = project/'state/adoptions'/f'{run_id}.json'
    receipt = {'schema_version': 1, 'project_id': str(project), 'scope': scope, 'adoption_id': run_id,
               'source_record': str(Path(imported).resolve()), 'changed_files': [str(p) for p in changed],
               'status': 'adopted_pending_review', 'artistic_approval': False,
               'production_allowed': False, 'previous_versions': {str(p): v for p, v in expected.items()},
               'limits': '仅结构采纳和撤销；不认证实际听看、时间权威、导演剪辑或艺术批准'}
    updates[receipt_path] = encoded(receipt)
    expected[receipt_path] = None
    def before_commit():
        for (folder, pattern), files in memberships.items():
            if sorted((project/folder).glob(pattern)) != files:
                raise ValueError('审批/执行记录集合在采纳期间改变')
        for lock in ('.workflow-execution.lock', '.asr-execution.lock'):
            if (project/lock).exists():
                raise ValueError('采纳期间上游开始执行')
    atomic_batch(updates, expected=expected, before_commit=before_commit)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('scope', choices=('timing', 'design'))
    parser.add_argument('project', type=Path)
    parser.add_argument('bundle', type=Path)
    parser.add_argument('imported', type=Path)
    args = parser.parse_args()
    try:
        result = adopt(args.project, args.bundle, args.imported, scope=args.scope)
        print(encoded(result))
        return 0
    except Exception as exc:
        print(encoded({'status': 'blocked', 'errors': [str(exc)], 'production_allowed': False}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
