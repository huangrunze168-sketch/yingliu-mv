"""返修事实、批准撤销及重审闭环；不执行生成、不自动写艺术批准。"""
import json
from pathlib import Path
import uuid

try:
    from .pipeline_contracts import atomic_batch, digest, number, require_no_transaction
    from .visual_revision import check_repair
    from .director_review_gate import approved, delivery_approved, validate_chapter_id, DIMENSIONS, SEQUENCE_DIMENSIONS
    from .ledger import locked
except ImportError:
    from pipeline_contracts import atomic_batch, digest, number, require_no_transaction
    from visual_revision import check_repair
    from director_review_gate import approved, delivery_approved, validate_chapter_id, DIMENSIONS, SEQUENCE_DIMENSIONS
    from ledger import locked


def load(path):
    return json.loads(Path(path).read_text(encoding='utf8'))


def records(project):
    project = Path(project).resolve()
    result = []
    for path in sorted((project / 'state/repairs').glob('*.json')):
        value = load(path)
        if (not isinstance(value, dict) or value.get('project_id') != str(project)
                or value.get('status') not in {'open', 'resolved'}
                or path_for(project, value.get('repair_id')).resolve() != path.resolve()):
            raise ValueError('返修记录项目、ID、路径或状态不一致')
        validate_chapter_id(value.get('chapter_id'))
        if not value.get('chapter_id') or value['chapter_id'] == 'full_sequence':
            raise ValueError('返修记录须绑定实际章节')
        result.append((path, value))
    return result


def path_for(project, repair_id):
    if not isinstance(repair_id, str) or len(repair_id) != 32 or any(c not in '0123456789abcdef' for c in repair_id):
        raise ValueError('返修ID无效')
    return Path(project) / 'state/repairs' / f'{repair_id}.json'


def text(value):
    return isinstance(value, str) and bool(value.strip())


def _open_repair(project, *, clip_id, plan_path, failure_review_path):
    """冻结失败媒体/观察和返修计划，同时原子撤销本章及全片旧批准。"""
    project = Path(project).resolve()
    require_no_transaction(project)
    plan_path, failure_review_path = (project / plan_path).resolve(), (project / failure_review_path).resolve()
    plan_path.relative_to(project); failure_review_path.relative_to(project)
    registry_path = project / 'state/generated_clips.json'
    chapter_plan_path = project / 'state/chapter_plan.json'
    expected = {p: digest(p) for p in (plan_path, failure_review_path, registry_path, chapter_plan_path)}
    plan, failure = load(plan_path), load(failure_review_path)
    problems = check_repair(plan)
    if problems: raise ValueError('; '.join(problems))
    clip = load(registry_path)['clips'][clip_id]
    cid = clip['chapter_id']
    validate_chapter_id(cid)
    previous = [(p, r) for p, r in records(project) if r.get('chapter_id') == cid]
    if any(r.get('status') != 'resolved' for _, r in previous):
        raise ValueError('本章已有未闭环返修，不能重复建立新计划掩盖旧问题')
    try:
        from .media_origin import require as require_origin, media
    except ImportError:
        from media_origin import require as require_origin, media
    require_origin(project, clip, expected)
    origin_record = load(project / clip['origin_receipt_path'])
    failed_prompt = None
    old_contract = None
    if origin_record.get('kind') == 'generated':
        old_contract = load(origin_record['completion']['contract_path'])
        failed_prompt = old_contract.get('prompt')
    source = (project / clip['path']).resolve()
    measured = media(source)
    video = next(s for s in measured['streams'] if s['codec_type'] == 'video')
    duration = number(video.get('duration', measured['format']['duration']))
    if (failure.get('decision') != 'reject' or failure.get('watched_full_duration') is not True
            or failure.get('simulation_only') is True
            or (project / failure.get('reviewed_media', '')).resolve() != source
            or failure.get('reviewed_media_sha256') != clip['sha256']):
        raise ValueError('返修须绑定实际失败媒体的完整动态审片声明，不能使用截图或泛化意见')
    findings = failure.get('timecoded_findings')
    if not isinstance(findings, list) or not findings:
        raise ValueError('失败审片缺少带时间码的观察')
    for finding in findings:
        start, end = number(finding['start_sec']), number(finding['end_sec'])
        if (start < 0 or end <= start or end > duration + 1e-6 or not text(finding.get('observation'))
                or finding.get('dimension') not in DIMENSIONS + SEQUENCE_DIMENSIONS):
            raise ValueError('失败观察须有实际视频内的正向时间范围及具体描述')
    for item in plan['dominant_failures']:
        if not any(f.get('dimension') == item['dimension'] and f.get('observation') == item['evidence'] for f in findings):
            raise ValueError('返修主导失败必须逐项引用实际审片观察，不能另造诊断')
    rid = uuid.uuid4().hex
    path = path_for(project, rid)
    record = {'schema_version': 1, 'repair_id': rid, 'project_id': str(project), 'chapter_id': cid,
              'status': 'open', 'failed_clip_id': clip_id, 'failed_clip': clip,
              'plan': plan, 'plan_sha256': expected[plan_path], 'failure_review': failure,
              'failure_review_sha256': expected[failure_review_path], 'artistic_approval': False,
              'supersedes': {r['repair_id']: digest(p) for p, r in previous},
              'failed_prompt': failed_prompt,
              'failed_origin_snapshot': origin_record, 'failed_request_snapshot': old_contract,
              'observation_scope': 'bound_statement_not_authenticated_actual_viewing'}
    updates = {path: json.dumps(record, ensure_ascii=False, indent=2)}
    expected.update({p: digest(p) for p, _ in previous})
    expected[path] = None
    chapter_plan = load(chapter_plan_path)
    reviews = [project / 'state/reviews' / f'{cid}.json', project / 'state/reviews/full_sequence.json']
    if chapter_plan['production_policy']['prototype_chapter'] == cid:
        reviews.append(project / 'state/director_review.json')
    for review_path in reviews:
        expected[review_path] = digest(review_path) if review_path.is_file() else None
        if not review_path.is_file(): continue
        old = review_path.read_bytes()
        pending = json.loads(old)
        pending.update(previous_decision=pending.get('decision'), decision='pending',
                       batch_generation_allowed=False, watched_full_duration=False, invalidated_by_repair=rid)
        updates[review_path] = json.dumps(pending, ensure_ascii=False, indent=2)
        history = review_path.parent / 'history' / f'{review_path.stem}-repair-{rid}.json'
        updates[history], expected[history] = old, None
    atomic_batch(updates, expected=expected)
    return record


def submission_errors(project, contract):
    """开放返修章节的新提交须显式绑定冻结计划，不能按普通批次隐藏返修。"""
    project = Path(project).resolve()
    active = [r for _, r in records(project) if r.get('status') == 'open' and r.get('chapter_id') == contract.get('chapter_id')]
    if not active:
        return ['提交引用不存在或已闭环返修'] if contract.get('repair_id') else []
    if len(active) != 1: return ['本章返修记录冲突']
    repair = active[0]
    if (repair.get('project_id') != str(project) or contract.get('repair_id') != repair['repair_id']
            or contract.get('repair_plan_sha256') != repair['plan_sha256']):
        return ['开放返修须绑定当前repair_id及冻结计划SHA']
    if (repair.get('failed_prompt') and contract.get('prompt') == repair['failed_prompt']
            and not text(repair['plan'].get('same_prompt_retry_reason'))):
        return ['返修提示词与失败请求相同；须重做设计编译，或在冻结计划中明确有据的同提示重试原因']
    return []


def _close(project, repair_id, *, replacement_clip_id):
    """只在新任务回片及本章实际批准后闭环，原媒体不能替代修复结果。"""
    project = Path(project).resolve()
    require_no_transaction(project)
    path = path_for(project, repair_id)
    registry = project / 'state/generated_clips.json'
    expected = {path: digest(path), registry: digest(registry)}
    repair = load(path)
    if repair.get('project_id') != str(project) or repair.get('status') != 'open':
        raise ValueError('只能闭环当前项目的开放返修')
    failed_source = (project / repair['failed_clip']['path']).resolve()
    failed_source.relative_to(project)
    expected[failed_source] = digest(failed_source)
    if expected[failed_source] != repair['failed_clip']['sha256']:
        raise ValueError('原失败媒体已丢失或改变，不能丢弃历史证据后闭环')
    clip = load(registry)['clips'][replacement_clip_id]
    if clip.get('chapter_id') != repair['chapter_id'] or clip.get('sha256') == repair['failed_clip']['sha256']:
        raise ValueError('返修结果须为同章不同媒体，不能重新批准旧失败文件')
    try:
        from .media_origin import require as require_origin
    except ImportError:
        from media_origin import require as require_origin
    require_origin(project, clip, expected)
    origin_path = (project / clip['origin_receipt_path']).resolve()
    origin = load(origin_path)
    if origin.get('kind') == 'generated':
        binding = load(origin['completion']['contract_path'])
    elif origin.get('kind') == 'derived':
        binding = origin
        if origin.get('parent_clip') != repair['failed_clip']:
            raise ValueError('本地返修源不是该计划的原失败素材')
    else:
        raise ValueError('返修须有新任务或实际本地变换来源，不能改标签冒充保留原片')
    if binding.get('repair_id') != repair_id or binding.get('repair_plan_sha256') != repair['plan_sha256']:
        raise ValueError('返修结果未绑定该冻结计划')
    review_path = project / 'state/reviews' / f"{repair['chapter_id']}.json"
    expected[review_path] = digest(review_path)
    review = load(review_path)
    expected.update({Path(p): digest(p) for p in review.get('dependencies', {})})
    ok, errors = approved(project, repair['chapter_id'])
    if not ok: raise ValueError('返修新媒体尚未通过本章重审: ' + '; '.join(errors))
    if ((project / review['reviewed_media']).resolve() != (project / clip['path']).resolve()
            or review['reviewed_media_sha256'] != clip['sha256']):
        raise ValueError('返修重审批准的不是当前替换媒体')
    addressed = {f.get('dimension') for f in review.get('timecoded_findings', []) if text(f.get('observation'))}
    if any(f['dimension'] not in addressed for f in repair['plan']['dominant_failures']):
        raise ValueError('重审须逐项记录原主导失败维度的实际结果')
    clip = {**clip, 'status': 'reviewed', 'evidence': '本章有效复核声明已绑定该媒体: ' + expected[review_path]}
    clips = load(registry)
    clips['clips'][replacement_clip_id] = clip
    repair.update(status='resolved', replacement_clip_id=replacement_clip_id,
                  replacement_clip=clip, replacement_review_sha256=expected[review_path],
                  replacement_review=review)
    atomic_batch({path: json.dumps(repair, ensure_ascii=False, indent=2),
                  registry: json.dumps(clips, ensure_ascii=False, indent=2)}, expected=expected)
    return repair


def require_closed(project):
    project = Path(project).resolve()
    expected = {}
    rows = records(project)
    by_id = {r['repair_id']: (p, r) for p, r in rows}
    superseded = set()
    for path, record in rows:
        expected[path] = digest(path)
        if record.get('project_id') != str(project) or record.get('status') != 'resolved':
            raise ValueError('存在未闭环返修，不能正式选段或交付')
        failed_source = (project / record['failed_clip']['path']).resolve()
        failed_source.relative_to(project)
        expected[failed_source] = digest(failed_source)
        if expected[failed_source] != record['failed_clip']['sha256']:
            raise ValueError('历史失败媒体已改变或丢失')
        for rid, sha in record.get('supersedes', {}).items():
            prior_path, prior = by_id[rid]
            if (rid == record['repair_id'] or prior['chapter_id'] != record['chapter_id']
                    or digest(prior_path) != sha):
                raise ValueError('返修历史链接不一致')
            superseded.add(rid)
    for path, record in rows:
        if record['repair_id'] in superseded: continue
        registry = project / 'state/generated_clips.json'
        expected[registry] = digest(registry)
        clip = load(registry)['clips'][record['replacement_clip_id']]
        if clip != record['replacement_clip']:
            raise ValueError('返修闭环后的替换素材已改变，须重新审查返修状态')
        try:
            from .media_origin import require as require_origin
        except ImportError:
            from media_origin import require as require_origin
        require_origin(project, clip, expected)
        review_path = project / 'state/reviews' / f"{record['chapter_id']}.json"
        expected[review_path] = digest(review_path)
        if expected[review_path] != record['replacement_review_sha256']:
            raise ValueError('返修闭环后的重审版本已改变')
    if any(digest(path) != sha for path, sha in expected.items()):
        raise ValueError('返修检查期间依据已变化')
    return expected


def open_repair(project, **kwargs):
    project = Path(project).resolve()
    with locked(project / 'state/repair.index'):
        return _open_repair(project, **kwargs)


def close(project, repair_id, *, replacement_clip_id):
    project = Path(project).resolve()
    with locked(project / 'state/repair.index'):
        return _close(project, repair_id, replacement_clip_id=replacement_clip_id)


def assessment_dependencies(project):
    project = Path(project).resolve()
    expected = require_closed(project)
    for relative in ('state/generated_clips.json', 'state/edit_selection.json', 'state/reviews/full_sequence.json'):
        path = project / relative
        expected[path] = digest(path)
    review = load(project / 'state/reviews/full_sequence.json')
    expected.update({Path(p): digest(p) for p in review.get('dependencies', {})})
    ok, errors = delivery_approved(project)
    if not ok: raise ValueError('返修汇总前须通过逐章及全片实际审片契约: ' + '; '.join(errors))
    if any(digest(path) != sha for path, sha in expected.items()):
        raise ValueError('返修汇总检查期间依据已变化')
    return expected


def assess(project):
    """实际检查当前批准链与返修闭环；没有返修须明确记录，不凭空补修复历史。"""
    project = Path(project).resolve()
    require_no_transaction(project)
    with locked(project / 'state/repair.index'):
        versions = assessment_dependencies(project)
        path = project / 'state/repair_assessment.json'
        record = {'schema_version': 1, 'project_id': str(project),
                  'assessment': 'all_repairs_resolved' if records(project) else 'no_registered_repairs',
                  'dependencies': {str(p): sha for p, sha in versions.items()},
                  'artistic_approval': False,
                  'scope': 'current_approval_and_repair_state_check_not_actual_viewing_attestation'}
        atomic_batch({path: json.dumps(record, ensure_ascii=False, indent=2)},
                     expected={**versions, path: digest(path) if path.exists() else None})
        return record


def require_assessed(project):
    project = Path(project).resolve()
    path = project / 'state/repair_assessment.json'
    version, record = digest(path), load(path)
    expected = assessment_dependencies(project)
    conclusion = 'all_repairs_resolved' if records(project) else 'no_registered_repairs'
    if (record.get('project_id') != str(project) or record.get('assessment') != conclusion
            or record.get('dependencies') != {str(p): sha for p, sha in expected.items()}
            or digest(path) != version):
        raise ValueError('返修汇总缺失或依据已经变化，须重新执行assess')
    return {**expected, path: version}


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('open', 'close', 'assess', 'check'))
    parser.add_argument('project_dir', type=Path)
    parser.add_argument('--clip-id')
    parser.add_argument('--plan', type=Path)
    parser.add_argument('--failure-review', type=Path)
    parser.add_argument('--repair-id')
    args = parser.parse_args()
    try:
        if args.action == 'open':
            if not args.clip_id or not args.plan or not args.failure_review:
                parser.error('open需要--clip-id、--plan和--failure-review')
            result = open_repair(args.project_dir, clip_id=args.clip_id, plan_path=args.plan,
                                 failure_review_path=args.failure_review)
        elif args.action == 'close':
            if not args.clip_id or not args.repair_id: parser.error('close需要--clip-id和--repair-id')
            result = close(args.project_dir, args.repair_id, replacement_clip_id=args.clip_id)
        elif args.action == 'assess': result = assess(args.project_dir)
        else:
            require_assessed(args.project_dir)
            result = {'status': 'checked', 'artistic_approval': False}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({'status': 'blocked', 'errors': [str(exc)]}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
