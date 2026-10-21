"""正式素材的任务/下载来源；来源登记不构成动态审片或创作批准。"""
import json
from pathlib import Path
import uuid
import urllib.request

try:
    from .pipeline_contracts import atomic_batch, digest, require_no_transaction
    from .submission_jobs import job_path, pointer, validate_response, executed_contract_path
    from .media_verify import probe
except ImportError:
    from pipeline_contracts import atomic_batch, digest, require_no_transaction
    from submission_jobs import job_path, pointer, validate_response, executed_contract_path
    from media_verify import probe


def load(path):
    return json.loads(Path(path).read_text(encoding='utf8'))


def local(project, path):
    result = (project / path).resolve()
    result.relative_to(project)
    if not result.is_file():
        raise ValueError('来源文件不存在')
    return result


def completion(job):
    response = job['response']
    validate_response(response, job.get('provider_task_id'), job=job)
    if (job.get('status') != 'completed' or not job.get('provider_task_id')
            or response.get('status') != 'completed'
            or response.get('task_id') != job['provider_task_id']):
        raise ValueError('素材须绑定同一已完成供应商任务的原始响应')
    return {**{key: job[key] for key in ('job_id', 'project_id', 'chapter_id', 'provider_task_id',
                                        'contract_sha256', 'response')},
            'contract_path': str(executed_contract_path(job))}


def media(path):
    report = probe(path)
    video = [s for s in report.get('streams', []) if s.get('codec_type') == 'video']
    if not video or any(int(s.get('nb_read_frames', 0)) < 1 for s in video):
        raise ValueError('素材必须包含实际可读视频帧')
    return report


def publish(project, record, expected):
    path = project / 'state/media_origins' / f'{uuid.uuid4().hex}.json'
    record.update(schema_version=1, project_id=str(project), artistic_approval=False,
                  dynamic_verified=False, scope='source_registration_only')
    atomic_batch({path: json.dumps(record, ensure_ascii=False, indent=2, allow_nan=False)},
                 expected={**expected, path: None})
    return path


def register_download(project, job_id, *, downloaded_path, http_evidence_path, output_pointer):
    """消费适配器真实下载及HTTP证据，不猜URL、不在此重新生成或自动批准。"""
    project = Path(project).resolve()
    require_no_transaction(project)
    jp = job_path(project, job_id)
    versions = {jp: digest(jp)}
    job = load(jp)
    identity = completion(job)
    if identity['project_id'] != str(project):
        raise ValueError('生成任务项目不匹配')
    source, http_path = local(project, downloaded_path), local(project, http_evidence_path)
    versions.update({source: digest(source), http_path: digest(http_path)})
    http = load(http_path)
    url = pointer(job['response']['raw_response'], output_pointer)
    if (not isinstance(url, str) or not url.startswith(('https://', 'http://'))
            or http.get('url') != url or http.get('method') != 'GET'
            or type(http.get('status_code')) is not int or http['status_code'] != 200
            or (project / http.get('downloaded_path', '')).resolve() != source
            or http.get('downloaded_sha256') != versions[source]):
        raise ValueError('实际下载HTTP证据与原任务输出URL/文件SHA不一致')
    contract_path = Path(identity['contract_path'])
    if digest(contract_path) != identity['contract_sha256']:
        raise ValueError('生成任务提交契约已改变')
    contract = load(contract_path)
    versions[contract_path] = identity['contract_sha256']
    shots = [s['id'] for s in contract['edit_shots']]
    return publish(project, {'kind': 'generated', 'chapter_id': job['chapter_id'], 'shot_ids': shots,
                            'path': str(source.relative_to(project)), 'sha256': versions[source],
                            'job_id': job_id, 'completion': identity, 'output_pointer': output_pointer,
                            'http_evidence_path': str(http_path.relative_to(project)),
                            'http_sha256': versions[http_path], 'probe': media(source)}, versions)


def download(project, job_id, *, output_pointer):
    """实际GET原任务输出，保留下载事实；失败不批准，不重新派发生成任务。"""
    project = Path(project).resolve()
    require_no_transaction(project)
    jp = job_path(project, job_id)
    job_version = digest(jp)
    job = load(jp)
    completion(job)
    if job.get('project_id') != str(project):
        raise ValueError('生成任务项目不匹配')
    url = pointer(job['response']['raw_response'], output_pointer)
    if not isinstance(url, str) or not url.startswith(('https://', 'http://')):
        raise ValueError('须从原任务响应读取实际HTTP输出URL')
    directory = project / 'downloads' / job_id / uuid.uuid4().hex
    directory.mkdir(parents=True, exist_ok=False)
    source = directory / 'output.media'
    partial = directory / 'output.partial'
    http_path = directory / 'http.json'
    report = {'method': 'GET', 'url': url, 'job_id': job_id,
              'provider_task_id': job['provider_task_id'], 'status': 'running',
              'scope': 'actual_http_download_not_dynamic_review'}
    # 下载执行记录与原始供应商任务分开，网络失败不把生成任务改为失败或重提。
    http_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
    try:
        request = urllib.request.Request(url, method='GET')
        with urllib.request.urlopen(request, timeout=30) as response:
            report.update(status_code=response.status, effective_url=response.url,
                          response_headers=dict(response.headers.items()))
            if response.status != 200:
                raise ValueError('实际下载未返回HTTP 200')
            with partial.open('xb') as handle:
                while block := response.read(1024 * 1024):
                    handle.write(block)
        partial.replace(source)
        report.update(status='downloaded', downloaded_path=str(source), downloaded_sha256=digest(source))
        if digest(jp) != job_version:
            raise ValueError('下载期间任务版本已改变，保留下载文件待核验')
        http_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
        return register_download(project, job_id, downloaded_path=source, http_evidence_path=http_path,
                                 output_pointer=output_pointer)
    except Exception as exc:
        report.update(status='failed_unregistered', error_type=type(exc).__name__)
        http_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf8')
        raise


def register_retained(project, *, source_path, chapter_id, shot_ids, authorization_path,
                      authorization_quote, observer, purpose):
    """记录用户明确允许保留的素材；声明须附原始授权文本，仍需逐章动态审片。"""
    project = Path(project).resolve()
    require_no_transaction(project)
    source, auth = local(project, source_path), local(project, authorization_path)
    versions = {source: digest(source), auth: digest(auth)}
    if (not isinstance(authorization_quote, str) or not authorization_quote.strip()
            or authorization_quote not in auth.read_text(encoding='utf8')
            or not isinstance(observer, str) or not observer.strip()
            or not isinstance(purpose, str) or not purpose.strip()
            or not isinstance(chapter_id, str) or not chapter_id.strip()
            or not isinstance(shot_ids, list) or not shot_ids
            or any(not isinstance(s, str) or not s.strip() for s in shot_ids)
            or len(shot_ids) != len(set(shot_ids))):
        raise ValueError('保留素材须明确章节/镜头、授权原文、记录者及用途')
    return publish(project, {'kind': 'retained', 'chapter_id': chapter_id, 'shot_ids': shot_ids,
                            'path': str(source.relative_to(project)), 'sha256': versions[source],
                            'authorization_path': str(auth.relative_to(project)),
                            'authorization_sha256': versions[auth], 'authorization_quote': authorization_quote,
                            'observer': observer, 'purpose': purpose, 'probe': media(source),
                            'authorization_scope': 'recorded_statement_not_authenticated_user_identity'}, versions)


def require(project, clip, versions=None, *, _seen=None):
    """读取时重算所有来源依赖；返回版本供渲染事务防止检查后替换。"""
    project = Path(project).resolve()
    checked = {}
    def read(path):
        path = local(project, path)
        checked[path] = digest(path)
        return load(path)
    try:
        receipt_path = local(project, clip['origin_receipt_path'])
        seen = set(_seen or ())
        if receipt_path in seen or len(seen) >= 64:
            raise ValueError('来源链存在循环或深度异常，禁止递归自认证')
        seen.add(receipt_path)
        record = read(receipt_path)
        source = local(project, clip['path'])
        checked[source] = digest(source)
        if (record.get('project_id') != str(project) or record.get('path') != str(source.relative_to(project))
                or record.get('sha256') != checked[source] or clip['sha256'] != checked[source]
                or record.get('chapter_id') != clip.get('chapter_id')
                or not set(clip['shot_ids']) <= set(record.get('shot_ids', []))):
            raise ValueError('素材来源与实际媒体/章节/镜头不一致')
        if record.get('kind') == 'generated':
            job = read(job_path(project, record['job_id']))
            if (completion(job) != record['completion'] or job.get('project_id') != str(project)
                    or job.get('chapter_id') != record['chapter_id']):
                raise ValueError('原任务完成回执已经改变')
            if job['response'].get('adapter') == 'lovart_mcp':
                try:
                    from .lovart_adapter import context_versions
                except ImportError:
                    from lovart_adapter import context_versions
                checked.update(context_versions(job['response'], job))
            contract_path = local(project, record['completion']['contract_path'])
            checked[contract_path] = digest(contract_path)
            if checked[contract_path] != job['contract_sha256']:
                raise ValueError('原任务契约已经改变')
            if record['shot_ids'] != [s['id'] for s in load(contract_path)['edit_shots']]:
                raise ValueError('素材镜头来源与原任务契约不一致')
            http_path = local(project, record['http_evidence_path'])
            http = read(http_path)
            if (checked[http_path] != record['http_sha256'] or http.get('method') != 'GET'
                    or type(http.get('status_code')) is not int or http['status_code'] != 200
                    or http.get('url') != pointer(job['response']['raw_response'], record['output_pointer'])
                    or http.get('downloaded_sha256') != checked[source]
                    or (project / http.get('downloaded_path', '')).resolve() != source):
                raise ValueError('原任务下载证据失效')
        elif record.get('kind') == 'derived':
            try:
                from .local_transform import require_origin
            except ImportError:
                from local_transform import require_origin
            require_origin(project, clip, record, checked, seen)
        elif record.get('kind') == 'retained':
            auth = local(project, record['authorization_path'])
            checked[auth] = digest(auth)
            if (checked[auth] != record['authorization_sha256'] or not record.get('authorization_quote', '').strip()
                    or record['authorization_quote'] not in auth.read_text(encoding='utf8')
                    or not record.get('observer', '').strip() or not record.get('purpose', '').strip()):
                raise ValueError('保留素材授权依据失效')
        else:
            raise ValueError('不允许缺少来源类别或用通用导入绕过任务来源')
        if any(digest(p) != sha for p, sha in checked.items()):
            raise ValueError('来源检查期间依赖已改变')
    except (OSError, KeyError, TypeError, IndexError) as exc:
        raise ValueError('正式素材缺少可核验来源登记') from exc
    if versions is not None:
        versions.update(checked)
    return checked


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project_dir', type=Path)
    parser.add_argument('--job-id', required=True)
    parser.add_argument('--output-pointer', required=True)
    args = parser.parse_args()
    try:
        path = download(args.project_dir, args.job_id, output_pointer=args.output_pointer)
        print(json.dumps({'status': 'source_registered', 'origin_receipt_path': str(path),
                          'artistic_approval': False, 'dynamic_verified': False}, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({'status': 'blocked', 'error_type': type(exc).__name__, 'errors': [str(exc)]}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
