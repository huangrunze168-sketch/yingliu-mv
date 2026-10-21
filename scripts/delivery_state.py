"""独立交付状态：输出、技术验证、可交付；不自动登记用户接收。"""
import argparse
import json
import hashlib
import uuid
import copy
import os
from contextlib import contextmanager
from pathlib import Path
try:
    from .pipeline_contracts import atomic_batch, digest, require_no_transaction, RevisionConflict
    from .director_review_gate import dependency_snapshot, delivery_approved, approved
    from .animatic_review import approved as animatic_approved
    from .media_verify import verify_delivery
    from .production_preflight import require_current
except ImportError:
    from pipeline_contracts import atomic_batch, digest, require_no_transaction, RevisionConflict
    from director_review_gate import dependency_snapshot, delivery_approved, approved
    from animatic_review import approved as animatic_approved
    from media_verify import verify_delivery
    from production_preflight import require_current


def record_path(project, output_name):
    if (not isinstance(output_name, str) or any(char in output_name for char in '/\\:*?"<>|\0\r\n')
            or Path(output_name).name != output_name or Path(output_name).suffix.lower() != '.mp4'):
        raise ValueError('交付文件名须为output目录下的.mp4，不能越出项目')
    root = Path(project).resolve()
    path = root / 'state/deliveries' / f'{output_name}.json'
    try:
        path.resolve().relative_to(root)
        (root / 'output' / output_name).resolve().relative_to(root)
    except ValueError as exc:
        raise ValueError('交付或预览路径通过链接越出项目') from exc
    return path


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def read_record(project, output_name):
    payload = record_path(project, output_name).read_bytes()
    record = json.loads(payload)
    record['_revision'] = 'sha256:' + hashlib.sha256(payload).hexdigest()
    return record


def specification_hash(specification):
    for field in ('width', 'height'):
        if type(specification[field]) is not int or specification[field] <= 0:
            raise ValueError('交付画布须为正整数')
    try:
        from .pipeline_contracts import number
    except ImportError:
        from pipeline_contracts import number
    for field in ('duration_sec', 'fps'):
        if number(specification[field]) <= 0: raise ValueError('交付时长和fps须为正数')
    return 'sha256:' + hashlib.sha256(json.dumps(specification, sort_keys=True, allow_nan=False).encode()).hexdigest()


def validate_specification(project, specification):
    specification_hash(specification)
    try:
        from .pipeline_contracts import number
    except ImportError:
        from pipeline_contracts import number
    project = Path(project).resolve()
    timeline = load(project / 'state/timeline.json')
    storyboard = load(project / 'state/storyboard.json')
    style = load(project / 'state/style.json')
    if (number(specification['duration_sec']) != number(timeline['duration_sec'])
            or number(specification['fps']) != number(storyboard['fps'])
            or (project / specification['master_audio']).resolve() != (project / timeline['master_audio']).resolve()):
        raise ValueError('导出时长/fps/母带与项目权威不一致')
    a, b = map(number, style['aspect_ratio'].split(':'))
    if a <= 0 or b <= 0: raise ValueError('项目比例须为正向有限数')
    if abs(specification['width']/specification['height'] - a/b) > 1e-6:
        raise ValueError('导出画布比例与项目不一致')


def save(project, record, updates=None, before_commit=None, additional_expected=None):
    path = record_path(project, record['output_name'])
    payload = json.dumps({k: v for k, v in record.items() if not k.startswith('_')}, ensure_ascii=False, indent=2)
    atomic_batch({**(updates or {}), path: payload}, expected={**(additional_expected or {}), path: record.get('_revision')},
                 before_commit=before_commit)
    record['_revision'] = 'sha256:' + hashlib.sha256(payload.encode('utf-8')).hexdigest()


def dependencies(project):
    """绑定整条序列，以及实际审批版本和审批观看对象。"""
    project = Path(project).resolve()
    result = dependency_snapshot(project, 'full_sequence')
    plan = load(project / 'state/chapter_plan.json')
    records = [project / 'state/director_review.json', project / 'state/animatic_review.json']
    records += [project / 'state/reviews' / f"{chapter['id']}.json" for chapter in plan['chapters']]
    records.append(project / 'state/reviews/full_sequence.json')
    for path in records:
        result[str(path.resolve())] = digest(path)
        review = load(path)
        media = (project / review['reviewed_media']).resolve()
        result[str(media)] = digest(media)
    master_lock = project / 'state/master_audio_lock.json'
    if master_lock.exists(): result[str(master_lock.resolve())] = digest(master_lock)
    try:
        from .chapter_admission import require as require_chapters
    except ImportError:
        from chapter_admission import require as require_chapters
    origins = {}
    require_chapters(project, load(project / 'state/storyboard.json'),
                     load(project / 'state/generated_clips.json')['clips'],
                     load(project / 'state/edit_selection.json')['selections'], origins)
    assembly = load(project / 'state/edit_assembly.json')
    if assembly.get('origin_versions') != {str(path): sha for path, sha in origins.items()}:
        raise ValueError('交付素材来源与正式选段记录不一致')
    result.update({str(path): sha for path, sha in origins.items()})
    try:
        from .repair_state import require_assessed
    except ImportError:
        from repair_state import require_assessed
    result.update({str(path): sha for path, sha in require_assessed(project).items()})
    return result


def begin(project, output_name, specification):
    """任何重导出先撤销旧可交付状态，失败不能遗留旧ready。"""
    project = Path(project).resolve()
    require_no_transaction(project)
    path = record_path(project, output_name)
    specification = copy.deepcopy(specification)
    validate_specification(project, specification)
    record = {'schema_version': 2, 'project_id': str(project), 'output_name': output_name,
              'export_id': uuid.uuid4().hex, '_revision': digest(path) if path.exists() else None,
              'status': 'rendering', 'specification': specification,
              'specification_sha256': specification_hash(specification),
              'dependencies': dependencies(project), 'output_sha256': None,
              'verification_sha256': None, 'user_received': False,
              'history': ['rendering'], 'errors': []}
    save(project, record)
    return record


def register_output(project, record, staged=None):
    if record.get('status') != 'rendering': raise ValueError('只有当前渲染任务可登记输出')
    target = Path(project).resolve() / 'output' / record['output_name']
    source = Path(staged).resolve() if staged else target
    if staged: source.relative_to(Path(project).resolve() / 'output')
    record['output_sha256'] = digest(source)
    record['status'] = 'exported'
    record['history'].append('exported')
    save(project, record, before_commit=(lambda: os.replace(source, target)) if staged else None)


def check(project, output_name, persist=False):
    """每次按真实文件重新核验，不信任磁盘上孤立的ready字符串。"""
    project = Path(project).resolve()
    errors = []
    record = None
    superseded = False
    try:
        require_no_transaction(project)
        record = read_record(project, output_name)
        if record.get('schema_version') != 2 or not record.get('export_id'):
            errors.append('旧交付记录缺少任务版本，须重新验收')
        if record.get('specification_sha256') != specification_hash(record['specification']):
            errors.append('交付规格已改变')
        validate_specification(project, record['specification'])
        if record.get('project_id') != str(project) or record.get('output_name') != output_name:
            errors.append('交付记录项目或文件不匹配')
        if record.get('dependencies') != dependencies(project): errors.append('交付依赖或审批版本已改变')
        if record.get('output_sha256') is not None:
            if record['output_sha256'] != digest(project / 'output' / output_name):
                errors.append('最终文件已改变')
        elif record.get('status') in {'exported', 'technical_verified', 'ready'}:
            errors.append('交付状态缺少最终文件指纹')
        if record.get('status') in {'technical_verified', 'ready'}:
            report = project / 'output' / Path(output_name).with_suffix('.verification.json')
            if record.get('verification_sha256') != digest(report): errors.append('技术验收报告已改变')
            else:
                verification = load(report)
                if verification.get('status') != 'pass': errors.append('技术验收报告未通过')
                if (verification.get('specification_sha256') != record.get('specification_sha256')
                        or verification.get('specification') != record.get('specification')
                        or verification.get('export_id') != record.get('export_id')
                        or verification.get('output_sha256') != record.get('output_sha256')):
                    errors.append('技术验收与导出规格/任务/文件不一致')
                video = next(s for s in verification['streams'] if s['codec_type'] == 'video')
                spec = record['specification']
                try:
                    from .media_verify import ratio
                except ImportError:
                    from media_verify import ratio
                if ((video['width'], video['height']) != (spec['width'], spec['height'])
                        or abs(ratio(video['avg_frame_rate']) - float(spec['fps'])) > 1e-6
                        or int(video['nb_read_frames']) != int(float(spec['duration_sec'])*float(spec['fps'])+1e-6)):
                    errors.append('验收实测流属性不符合导出规格')
        if record.get('status') == 'ready':
            require_current(project)
            if record.get('delivery_package') != require_package(project, record):
                errors.append('交付包版本已改变')
            for validator in (approved, delivery_approved, animatic_approved):
                ok, failures = validator(project)
                if not ok: errors.extend(failures)
        valid_states = {'rendering', 'exported', 'technical_verified', 'ready', 'failed', 'invalidated'}
        if record.get('status') not in valid_states: errors.append('未知交付状态')
    except Exception as exc:
        errors.append(f'交付状态无法核验: {exc}')
        # 保留本次读取的版本；不能将旧检查错误归到异常后重读的新任务上。
        if record is None: record = {'output_name': output_name, 'status': 'missing', 'user_received': False}
    if record.get('_revision'):
        try:
            superseded = digest(record_path(project, output_name)) != record['_revision']
        except OSError:
            superseded = True
        if superseded: errors.append('检查期间任务版本已改变，请重新检查当前版本')
    result = {**{k: v for k, v in record.items() if not k.startswith('_')}, 'stored_status': record.get('status'),
              'status': 'superseded' if superseded else 'invalidated' if errors else record.get('status'),
              'ready_for_delivery': not errors and not superseded and record.get('status') == 'ready', 'errors': errors,
              'readiness_errors': record.get('errors', []) if record.get('status') == 'technical_verified' else []}
    if (persist and errors and not superseded and record.get('schema_version') == 2
            and record.get('project_id') == str(project) and record.get('output_name') == output_name):
        record.update(status='invalidated', errors=errors)
        try:
            save(project, record)
        except RevisionConflict:
            result.update(status='superseded', ready_for_delivery=False)
            result['errors'].append('失效回写前任务版本已改变，未修改新任务')
    return result


def finish(project, record):
    """实际技术验证完成后才迁移；最后再次核对依赖和有效审批。"""
    project = Path(project).resolve()
    if record.get('status') != 'exported': raise ValueError('技术验收必须消费已输出记录')
    specification = record['specification']
    current = read_record(project, record['output_name'])
    if current['_revision'] != record.get('_revision') or current.get('export_id') != record.get('export_id'):
        raise ValueError('旧导出任务已失效，禁止验收或回写')
    try:
        report = verify_delivery(project / 'output' / record['output_name'],
                                 project / specification['master_audio'], specification['duration_sec'],
                                 specification['fps'], specification['width'], specification['height'], project=project)
    except Exception as exc:
        fail(project, record, exc)
        raise
    report_path = project / 'output' / Path(record['output_name']).with_suffix('.verification.json')
    report.update(specification=specification, specification_sha256=record['specification_sha256'],
                  export_id=record['export_id'], output_sha256=record['output_sha256'])
    report_payload = json.dumps(report, ensure_ascii=False, indent=2)
    record['verification_sha256'] = 'sha256:' + hashlib.sha256(report_payload.encode()).hexdigest()
    if report['errors']:
        record.update(status='failed', errors=report['errors'])
        save(project, record, {report_path: report_payload})
        raise ValueError('成片实际媒体验收失败: ' + '; '.join(report['errors']))
    record['status'] = 'technical_verified'
    record['history'].append('technical_verified')
    save(project, record, {report_path: report_payload})
    current = check(project, record['output_name'])
    if current['errors']:
        record.update(status='invalidated', errors=current['errors'])
        save(project, record)
        raise ValueError('交付版本失效: ' + '; '.join(current['errors']))
    errors = []
    require_current(project)
    for validator in (approved, delivery_approved, animatic_approved):
        ok, failures = validator(project)
        if not ok: errors.extend(failures)
    if errors:
        record.update(status='technical_verified', errors=errors)
        save(project, record)
        raise ValueError('技术验收通过，但交付审批未完成: ' + '; '.join(errors))
    promote(project, record)
    return report


def require_package(project, record):
    try:
        from .delivery_package import require_current as require_delivery_package
    except ImportError:
        from delivery_package import require_current as require_delivery_package
    return require_delivery_package(project, record)


def promote(project, record):
    """技术完成后，消费实际交付包及最终媒体复核，才进入ready。"""
    project = Path(project).resolve()
    if record.get('status') != 'technical_verified': raise ValueError('须先完成实际技术验收')
    current = check(project, record['output_name'])
    if current['errors'] or current.get('export_id') != record.get('export_id'):
        raise ValueError('交付批准前版本已失效')
    require_current(project)
    for validator in (approved, delivery_approved, animatic_approved):
        ok, errors = validator(project)
        if not ok: raise ValueError('交付审批未完成: ' + '; '.join(errors))
    try:
        package = require_package(project, record)
    except Exception as exc:
        raise ValueError('技术验收完成，完整交付包或最终媒体复核尚未通过: ' + str(exc)) from exc
    record['delivery_package'] = package
    record['status'] = 'ready'
    record['history'].append('ready')
    save(project, record, additional_expected={**record.get('dependencies', {}), **package.get('verified_versions', {})})
    return record


def fail(project, record, error):
    record.update(status='failed', errors=[str(error)])
    save(project, record)


@contextmanager
def rendering_guard(project, record):
    try:
        yield
    except Exception as exc:
        if record is not None: fail(project, record, exc)
        raise


def require_ready(project, output_name='final_mv.mp4'):
    result = check(project, output_name, persist=True)
    if not result['ready_for_delivery']: raise ValueError('成片不可交付: ' + str(result))
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('check',))
    parser.add_argument('project_dir', type=Path)
    parser.add_argument('--output-name', default='final_mv.mp4')
    args = parser.parse_args()
    result = check(args.project_dir, args.output_name, persist=True)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result['ready_for_delivery'] else 1)


if __name__ == '__main__': main()
