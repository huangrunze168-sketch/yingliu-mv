"""后端中立的预占、派发与回执记录；不内置供应商、不把未知结果自动重试。"""
import json
import hashlib
from decimal import ROUND_CEILING
from pathlib import Path
import uuid

try:
    from .pipeline_contracts import atomic_batch, digest, require_no_transaction
    from .submission_contract import validate_submission_contract
    from .ledger import record as ledger_record, money, locked
except ImportError:
    from pipeline_contracts import atomic_batch, digest, require_no_transaction
    from submission_contract import validate_submission_contract
    from ledger import record as ledger_record, money, locked

def load(path):
    return json.loads(Path(path).read_text(encoding='utf8'))


def pointer(value, path):
    if not isinstance(path, str) or not path.startswith('/'):
        raise ValueError('原生请求字段须使用明确JSON Pointer')
    for key in path[1:].split('/'):
        key = key.replace('~1', '/').replace('~0', '~')
        value = value[int(key)] if isinstance(value, list) else value[key]
    return value


PROVIDER_STATUS_MAP = {'pending': 'pending', 'completed': 'completed', 'failed': 'failed',
                       'moderated': 'failed', 'not_found': 'failed'}


def validate_response(response, expected_task_id=None, *, job=None):
    """标准化不能伪称原始字段存在；终态回查可明确绑定原请求上下文。"""
    if (not isinstance(response, dict) or not isinstance(response.get('task_id'), str)
            or not response['task_id'] or response.get('status') not in {'pending', 'completed', 'failed'}):
        raise ValueError('外部适配器未返回可回查的真实任务ID和明确状态')
    raw, fields = response['raw_response'], response['response_fields']
    if response.get('adapter') == 'lovart_mcp':
        try:
            from .lovart_adapter import payload
        except ImportError:
            from lovart_adapter import payload
        checked_raw, _, _ = payload(raw)
        if checked_raw != raw: raise ValueError('MCP原文解析视图没有冻结保存')
        if job is not None:
            try:
                from .lovart_adapter import context_versions
            except ImportError:
                from lovart_adapter import context_versions
            context_versions(response, job)
    provider_status = pointer(raw, fields['status'])
    mapping = response.get('status_mapping')
    if mapping is not None:
        if mapping != PROVIDER_STATUS_MAP or mapping.get(provider_status) != response['status']:
            raise ValueError('供应商状态映射缺少明确原始状态或使用了未知映射')
    elif provider_status != response['status']:
        raise ValueError('标准化状态不在原始供应商响应指定字段中')
    if expected_task_id is not None and response['task_id'] != expected_task_id:
        raise ValueError('回查结果不是原供应商任务')
    if response.get('task_binding_kind') == 'request_context':
        context = response['request_context']
        if (expected_task_id is None or context.get('tool') not in {'mcp__lovart__get_task_status', 'mcp__lovart__get_task'}
                or context.get('arguments', {}).get('task_ids') != [expected_task_id]
                or not isinstance(context.get('call_id'), str) or not context['call_id']
                or not isinstance(context.get('request_sha256'), str)):
            raise ValueError('终态回查须绑定原任务的实际调用上下文，不伪造响应任务ID字段')
    elif pointer(raw, fields['task_id']) != response['task_id']:
        raise ValueError('标准化任务ID不在原始供应商响应指定字段中')
    return raw, fields


def preserve_response(job, response, expected_task_id=None):
    if response is None: return
    try:
        json.dumps(response, allow_nan=False)
        job['unvalidated_response'] = response
        validate_response(response, expected_task_id, job=job)
        job['provider_task_id'] = response['task_id']
    except Exception as exc:
        job['response_validation_error_type'] = type(exc).__name__
        if 'unvalidated_response' not in job:
            job['unvalidated_response'] = {'scope': 'unserializable_external_response', 'response_type': type(response).__name__}


def request_errors(contract):
    errors = []
    try:
        request, fields = contract['provider_request'], contract['provider_request_fields']
        if not isinstance(request, dict) or not request:
            raise ValueError('缺少实际原生请求')
        if pointer(request, fields['prompt']) != contract['prompt']:
            errors.append('原生请求提示词与已校验制作包不一致')
        edit_duration = money(contract['duration_sec'])
        generated_duration = money(contract.get('generation_duration_sec', contract['duration_sec']))
        if money(pointer(request, fields['duration'])) != generated_duration:
            errors.append('原生请求时长与明确的生成时长不一致')
        if generated_duration < edit_duration:
            errors.append('生成时长不足以覆盖原曲章节，不允许缩短或变速补齐')
        if generated_duration != edit_duration:
            tail = money(contract.get('generation_trim_tail_sec', '-1'))
            if (abs(tail-(generated_duration-edit_duration)) > money('0.000001')
                    or not str(contract.get('generation_duration_reason', '')).strip()):
                errors.append('模型时长取整须明确额外裁尾与理由，不能改变剪辑窗口或隐含烧钱')
            capabilities = contract['backend_capabilities']
            minimum, maximum = money(capabilities['min_duration_sec']), money(capabilities['max_duration_sec'])
            quantum = money(capabilities['duration_quantum_sec'])
            if quantum <= 0 or minimum < 0:
                raise ValueError('模型时长步长/下限无效')
            minimum_legal = (max(edit_duration, minimum)/quantum).to_integral_value(rounding=ROUND_CEILING)*quantum
            if generated_duration != minimum_legal:
                errors.append('额外生成时长并非已核验下限/步长所需，须先回到正式生成包设计，不能任意加秒')
            if generated_duration > maximum or generated_duration < minimum:
                errors.append('实际生成时长超出已核验的模型区间')
        if pointer(request, fields['resolution']) != contract['resolution']:
            errors.append('原生请求分辨率与提交契约不一致')
        if not contract.get('selected_model') or pointer(request, fields['model']) != contract['selected_model']:
            errors.append('原生请求未绑定用户所选模型')
        expected = [b['source'].get('url') or b['source'].get('asset_id') for b in contract['reference_bindings']]
        actual = [pointer(request, p) for p in fields['references']]
        if len(actual) != len(expected) or sorted(actual) != sorted(expected):
            errors.append('原生请求参考资产与已校验绑定不一致')
        json.dumps(request, allow_nan=False)
        def reject_secrets(value):
            if isinstance(value, dict):
                if any(str(k).lower() in {'api_key', 'authorization', 'access_token', 'secret_key'} for k in value):
                    raise ValueError('原生请求记录不能包含凭证；由外部传输层注入')
                for item in value.values(): reject_secrets(item)
            elif isinstance(value, list):
                for item in value: reject_secrets(item)
        reject_secrets(request)
    except (KeyError, ValueError, TypeError, IndexError) as exc:
        errors.append('原生请求无法核验: ' + str(exc))
    return errors


def upload_errors(project, contract):
    errors, versions = [], {}
    for binding in contract.get('reference_bindings', []):
        try:
            source = binding['source']
            path = (project/binding['upload_receipt_path']).resolve()
            path.relative_to(project)
            versions[path] = digest(path)
            receipt = load(path)
            local = Path(receipt['local_path'])
            versions[local] = digest(local)
            expected = str(source['sha256']).removeprefix('sha256:')
            if (receipt.get('status') not in {'uploaded', 'hosted_verified'} or receipt.get('project_id') != str(project)
                    or receipt.get('source_sha256', '').removeprefix('sha256:') != expected
                    or digest(local).removeprefix('sha256:') != expected):
                errors.append('上传回执与当前真实本地源不一致')
            locator = source.get('url') or source.get('asset_id')
            if receipt.get('locator') != locator:
                errors.append('提交的托管资产不是该源文件上传回执对应的资产')
            response = project/receipt['raw_response_path']
            response.resolve().relative_to(project)
            versions[response] = digest(response)
            if versions[response] != receipt.get('raw_response_sha256'):
                errors.append('原始上传响应已改变')
            elif pointer(load(response), receipt['locator_pointer']) != locator:
                errors.append('托管标识与原始上传响应不一致')
            if receipt.get('status') == 'hosted_verified':
                copy_path, http_path = Path(receipt['hosted_copy_path']), Path(receipt['http_evidence_path'])
                versions[copy_path], versions[http_path] = digest(copy_path), digest(http_path)
                if (versions[copy_path] != receipt['source_sha256']
                        or versions[http_path] != receipt['http_evidence_sha256']):
                    errors.append('托管复用的实际下载或HTTP证据已变化')
        except (OSError, KeyError, ValueError, TypeError, IndexError, AttributeError) as exc:
            errors.append('上传回执无法核验: ' + str(exc))
    return errors, versions


def _record_asset(project, local_path, *, locator, locator_pointer, raw_response, status, extra=None, extra_versions=None):
    """供实际外部上传适配器记录返回值；不把调用者提供的响应认证为供应商签名。"""
    project, local_path = Path(project).resolve(), Path(local_path).resolve()
    require_no_transaction(project)
    if not isinstance(locator, str) or not locator.strip() or not isinstance(raw_response, dict) or not raw_response:
        raise ValueError('须有实际托管标识和原始上传响应')
    if pointer(raw_response, locator_pointer) != locator:
        raise ValueError('托管标识不在实际上传响应的指定字段')
    run_id = uuid.uuid4().hex
    folder = project/'state/uploads'/run_id
    response_path, receipt_path = folder/'response.json', folder/'receipt.json'
    text = json.dumps(raw_response, ensure_ascii=False, indent=2, allow_nan=False)
    import hashlib
    source_sha = digest(local_path)
    receipt = {'schema_version': 1, 'status': status, 'project_id': str(project),
               'local_path': str(local_path), 'source_sha256': source_sha, 'locator': locator,
               'locator_pointer': locator_pointer,
               'raw_response_path': str(response_path), 'raw_response_sha256': 'sha256:'+hashlib.sha256(text.encode()).hexdigest(),
               'scope': 'external_adapter_supplied_response_not_provider_signature'}
    receipt.update(extra or {})
    atomic_batch({response_path: text, receipt_path: json.dumps(receipt, ensure_ascii=False, indent=2)},
                 expected={local_path: source_sha, response_path: None, receipt_path: None, **(extra_versions or {})})
    return receipt_path


def record_upload(project, local_path, *, locator, locator_pointer, raw_response):
    return _record_asset(project, local_path, locator=locator, locator_pointer=locator_pointer,
                         raw_response=raw_response, status='uploaded')


def verify_hosted(project, local_path, *, downloaded_path, http_evidence_path):
    """复用已有URL前比较实际下载字节，不伪造历史上传事实。"""
    local_path, downloaded_path, http_evidence_path = (Path(p).resolve() for p in
                                                     (local_path, downloaded_path, http_evidence_path))
    versions = {downloaded_path: digest(downloaded_path), http_evidence_path: digest(http_evidence_path)}
    http = json.loads(http_evidence_path.read_text(encoding='utf-8-sig'))
    if (http.get('method') != 'GET' or type(http.get('status_code')) is not int or http['status_code'] != 200
            or not isinstance(http.get('url'), str) or not http['url'].strip()
            or Path(http['downloaded_path']).resolve() != downloaded_path
            or http.get('downloaded_sha256') != versions[downloaded_path]
            or versions[downloaded_path] != digest(local_path)):
        raise ValueError('托管URL实际下载与本地源不一致或缺少HTTP证据，不能声明可复用')
    return _record_asset(project, local_path, locator=http['url'], locator_pointer='/url',
                         raw_response=http, status='hosted_verified',
                         extra={'hosted_copy_path': str(downloaded_path), 'http_evidence_path': str(http_evidence_path),
                                'http_evidence_sha256': versions[http_evidence_path],
                                'scope': 'current_hosted_bytes_verified_not_historical_upload_attestation'},
                         extra_versions=versions)


def job_path(project, job_id):
    if not isinstance(job_id, str) or len(job_id) != 32 or any(c not in '0123456789abcdef' for c in job_id):
        raise ValueError('任务ID无效')
    return Path(project).resolve()/'state/submission_jobs'/f'{job_id}.json'


def budget_event(project, job, action, cash='0', coins='0'):
    return ledger_record(Path(project)/'state/ledger.jsonl', {
        'event_id': f"{job['job_id']}:{action}", 'task_id': job['job_id'], 'action': action,
        'cash_cny': str(cash), 'coins': str(coins), 'duration_sec': job.get('generation_duration_sec', job['duration_sec']),
        'planned_edit_duration_sec': job['duration_sec'],
        'project_dir': str(project), 'shot_id': job['chapter_id'],
        'backend': job['selected_model'], 'provider_task_id': job.get('provider_task_id'),
        'submission_phase': job['submission_phase'],
    }, job['cash_limit_cny'])


def require_reconciled(project):
    for path in (Path(project)/'state/submission_jobs').glob('*.json'):
        if load(path).get('cost_status') == 'reconciliation_required':
            raise ValueError('有未核对的实际费用差异，禁止继续派发或预占新任务')


def _prepare(project, contract_path):
    project, contract_path = Path(project).resolve(), Path(contract_path).resolve()
    require_no_transaction(project)
    try:
        from .execution_policy import require_video_backend
    except ImportError:
        from execution_policy import require_video_backend
    policy_versions = require_video_backend(project)
    contract_bytes = contract_path.read_bytes()
    version = 'sha256:' + hashlib.sha256(contract_bytes).hexdigest()
    contract = json.loads(contract_bytes)
    repairs = repair_versions(project, contract)
    require_reconciled(project)
    limit = contract.get('backend_capabilities', {}).get('concurrency_limit')
    account = contract.get('backend_account_id')
    if type(limit) is not int or limit < 1 or not isinstance(account, str) or not account.strip():
        raise ValueError('须核验实际账号标识和正整数并发上限，不猜测默认值')
    active = [load(p) for p in (project/'state/submission_jobs').glob('*.json')]
    if sum(j.get('status') in {'prepared', 'dispatching', 'pending', 'polling', 'unknown'}
           and j.get('backend_account_id') == account for j in active) >= limit:
        raise ValueError('当前账号在本项目的任务达到并发上限；先回查在途任务')
    errors = validate_submission_contract(contract, project) + request_errors(contract)
    upload_failures, uploads = upload_errors(project, contract)
    errors += upload_failures
    if errors:
        raise ValueError('提交准备阻断: ' + '; '.join(errors))
    job_id = uuid.uuid4().hex
    archive = project / 'state/submission_jobs/contracts' / f'{job_id}.json'
    job = {'schema_version': 1, 'job_id': job_id, 'project_id': str(project), 'status': 'prepared',
           'contract_path': str(contract_path), 'contract_sha256': version,
           'request_archive_path': str(archive),
           'upload_versions': {str(k): v for k, v in uploads.items()},
           'repair_versions': {str(k): v for k, v in repairs.items()},
           **{key: contract[key] for key in ('chapter_id', 'selected_model', 'duration_sec',
                                            'submission_phase', 'cash_limit_cny', 'estimated_cash_cny', 'backend_account_id')},
           'provider_task_id': None, 'cost_status': 'reserved', 'artistic_approval': False}
    job['generation_duration_sec'] = str(contract.get('generation_duration_sec', contract['duration_sec']))
    budget_event(project, job, 'reserve', cash=job['estimated_cash_cny'])
    try:
        atomic_batch({job_path(project, job_id): json.dumps(job, ensure_ascii=False, indent=2), archive: contract_bytes},
                     expected={contract_path: version, **uploads, **repairs, **policy_versions,
                               job_path(project, job_id): None, archive: None})
    except Exception:
        # 派发尚未发生，撤销本次准备的预占；释放失败则保持账面预占并抛错。
        budget_event(project, job, 'release')
        raise
    return job


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('prepare', 'status', 'cancel-prepared'))
    parser.add_argument('project_dir', type=Path)
    parser.add_argument('--contract', type=Path)
    parser.add_argument('--job-id')
    args = parser.parse_args()
    try:
        if args.action == 'prepare':
            if args.contract is None:
                parser.error('prepare需要--contract')
            result = prepare(args.project_dir, args.contract)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.action == 'cancel-prepared':
            result = cancel_prepared(args.project_dir, args.job_id)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result.get('cost_status') == 'released_unspent' else 1
        path = job_path(args.project_dir, args.job_id)
        result = load(path)
        if result.get('project_id') != str(args.project_dir.resolve()):
            raise ValueError('作业项目不匹配')
        print(json.dumps({'operation': 'inspect_job_only', 'job_id': result['job_id'],
                          'provider_status': result['status'], 'provider_task_id': result.get('provider_task_id'),
                          'cost_status': result.get('cost_status'), 'artistic_approval': False}, ensure_ascii=False))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({'status': 'blocked', 'errors': [str(exc)]}, ensure_ascii=False))
        return 1


def prepare(project, contract_path):
    project = Path(project).resolve()
    if not project.is_dir():
        raise ValueError('项目目录不存在')
    with locked(project/'state/submission_jobs.index'):
        return _prepare(project, contract_path)


def poll(project, job_id, lookup):
    """只回查原供应商任务；unknown且没有任务ID时禁止猜测或重新生成。"""
    project = Path(project).resolve()
    require_no_transaction(project)
    try:
        from .execution_policy import require_video_backend
    except ImportError:
        from execution_policy import require_video_backend
    require_video_backend(project)
    path = job_path(project, job_id)
    version, job = digest(path), load(path)
    if (job.get('project_id') != str(project) or job.get('status') not in {'pending', 'unknown'}
            or not job.get('provider_task_id')):
        raise ValueError('没有可回查的原任务，或任务已经终态；禁止新建任务代替回查')
    task_id = job['provider_task_id']
    job['status'] = 'polling'
    atomic_batch({path: json.dumps(job, ensure_ascii=False, indent=2)}, expected={path: version})
    version = digest(path)
    response = None
    try:
        response = lookup(task_id)
        validate_response(response, task_id, job=job)
        if response.get('validation_errors'): raise ValueError('; '.join(response['validation_errors']))
        cost_status = job.get('cost_status')
        job.update(status=response['status'], response=response,
                   cost_status=cost_status if cost_status in {'settled', 'reconciliation_required'} else 'reserved_awaiting_actual_cost')
    except Exception as exc:
        preserve_response(job, response, task_id)
        cost_status = job.get('cost_status')
        job.update(status='unknown', error_type=type(exc).__name__,
                   cost_status=cost_status if cost_status in {'settled', 'reconciliation_required'} else 'reservation_retained')
    atomic_batch({path: json.dumps(job, ensure_ascii=False, indent=2, allow_nan=False)}, expected={path: version})
    return job


def settle(project, job_id, *, cash_cny, coins, raw_cost_response, cash_pointer, coins_pointer, task_pointer):
    """结算须绑定实际费用响应；不从估价或任务完成状态猜费用。"""
    project = Path(project).resolve()
    require_no_transaction(project)
    path = job_path(project, job_id)
    version, job = digest(path), load(path)
    if (job.get('project_id') != str(project) or job.get('status') not in {'pending', 'completed', 'failed', 'unknown'}
            or not job.get('provider_task_id')):
        raise ValueError('须有可绑定的供应商任务及真实费用；不把未知调用或估价当结算')
    if (money(cash_cny) < 0 or money(coins) < 0
            or pointer(raw_cost_response, task_pointer) != job.get('provider_task_id')
            or money(pointer(raw_cost_response, cash_pointer)) != money(cash_cny)
            or money(pointer(raw_cost_response, coins_pointer)) != money(coins)):
        raise ValueError('实际费用与原始费用响应不一致')
    if job.get('cost_status') == 'settled':
        if (money(job['actual_cost']['cash_cny']) != money(cash_cny)
                or money(job['actual_cost']['coins']) != money(coins)):
            raise ValueError('同一任务已结算，费用变更须走明确的账务核对或退款')
        return job
    if job.get('actual_cost') and (money(job['actual_cost']['cash_cny']) != money(cash_cny)
                                   or money(job['actual_cost']['coins']) != money(coins)):
        raise ValueError('不能覆盖已记录的费用差异，须有明确对账或退款证据')
    job.update(actual_cost={'cash_cny': str(cash_cny), 'coins': str(coins),
                            'raw_response': raw_cost_response, 'cash_pointer': cash_pointer,
                            'coins_pointer': coins_pointer, 'task_pointer': task_pointer})
    try:
        budget_event(project, job, 'settle', cash=cash_cny, coins=coins)
        job['cost_status'] = 'settled'
    except Exception as exc:
        # 已知费用不能被错误丢掉；保存差异，留待实际账务核对，禁止假报结清。
        job.update(cost_status='reconciliation_required', cost_error_type=type(exc).__name__)
    atomic_batch({path: json.dumps(job, ensure_ascii=False, indent=2, allow_nan=False)}, expected={path: version})
    return job


def require_dispatch_basis(project, job):
    """首次派发和外部桥接领取前共用同一实时检查。"""
    project = Path(project).resolve()
    require_no_transaction(project)
    try:
        from .execution_policy import require_video_backend
    except ImportError:
        from execution_policy import require_video_backend
    policy_versions = require_video_backend(project)
    require_reconciled(project)
    contract = load(job['contract_path'])
    archived_path = executed_contract_path(job)
    if load(archived_path) != contract:
        raise ValueError('准备后的请求归档与当前提交契约不一致')
    repairs = repair_versions(project, contract)
    if {str(k): v for k, v in repairs.items()} != job.get('repair_versions'):
        raise ValueError('准备后的返修状态已变化，禁止派发旧作业')
    if digest(job['contract_path']) != job['contract_sha256']:
        raise ValueError('准备后的提交契约已变化')
    errors = validate_submission_contract(contract, project) + request_errors(contract)
    failures, uploads = upload_errors(project, contract)
    if errors or failures or {str(k): v for k, v in uploads.items()} != job['upload_versions']:
        raise ValueError('派发前实际依据已失效: ' + '; '.join(errors+failures))
    ledger_path = project / 'state/ledger.jsonl'
    ledger_version = digest(ledger_path)
    rows = [json.loads(line) for line in ledger_path.read_text(encoding='utf8').splitlines() if line.strip()]
    events = [r for r in rows if r.get('task_id') == job['job_id']]
    if not events or events[-1]['action'] != 'reserve' or money(events[-1]['cash_cny']) != money(job['estimated_cash_cny']):
        raise ValueError('缺少该任务当前有效的预算预占')
    return contract, {Path(job['contract_path']): job['contract_sha256'], archived_path: job['contract_sha256'],
                      **uploads, **repairs, **policy_versions, ledger_path: ledger_version}


def dispatch(project, job_id, adapter):
    """adapter必须由用户选择的外部后端提供；结果未知保留预占，不自动再次调用。"""
    project = Path(project).resolve()
    path = job_path(project, job_id)
    version, job = digest(path), load(path)
    if job.get('project_id') != str(project) or job.get('status') != 'prepared':
        raise ValueError('只有当前prepared任务可首次派发；未知/在途任务须先回查原任务')
    contract, basis = require_dispatch_basis(project, job)
    job['status'] = 'dispatching'
    atomic_batch({path: json.dumps(job, ensure_ascii=False, indent=2)},
                 expected={path: version, **basis})
    version = digest(path)
    response = None
    try:
        response = adapter(contract['provider_request'])
        validate_response(response, job=job)
        if response.get('validation_errors'): raise ValueError('; '.join(response['validation_errors']))
        job.update(status=response['status'], provider_task_id=response['task_id'],
                   response=response, cost_status='reserved_awaiting_actual_cost')
    except Exception as exc:
        preserve_response(job, response)
        job.update(status='unknown', error_type=type(exc).__name__,
                   cost_status='reservation_retained',
                   recovery='先回查原始供应商调用；禁止把异常当作未提交而重试')
    atomic_batch({path: json.dumps(job, ensure_ascii=False, indent=2, allow_nan=False)}, expected={path: version})
    return job


def repair_versions(project, contract):
    try:
        from .repair_state import submission_errors, records
    except ImportError:
        from repair_state import submission_errors, records
    errors = submission_errors(project, contract)
    if errors: raise ValueError('; '.join(errors))
    return {path: digest(path) for path, row in records(project) if row.get('chapter_id') == contract.get('chapter_id')}


def executed_contract_path(job):
    """执行请求独立归档；后续编译不能改写旧任务的历史请求事实。"""
    path = Path(job.get('request_archive_path') or job['contract_path']).resolve()
    if digest(path) != job['contract_sha256']: raise ValueError('任务原始请求归档已改变')
    return path


def cancel_prepared(project, job_id):
    """仅释放未派发任务的本地预占；不是取消供应商任务或退款。"""
    project = Path(project).resolve()
    require_no_transaction(project)
    with locked(project / 'state/submission_jobs.index'):
        path = job_path(project, job_id)
        version, job = digest(path), load(path)
        if (job.get('project_id') != str(project) or job.get('provider_task_id') is not None
                or job.get('status') not in {'prepared', 'cancelled_before_dispatch'}):
            raise ValueError('只能撤销从未派发的本地准备，不能取消在途/未知供应商调用')
        ledger_path = project / 'state/ledger.jsonl'
        ledger_version = digest(ledger_path)
        rows = [json.loads(r) for r in ledger_path.read_text(encoding='utf8').splitlines() if r.strip()]
        history = [r for r in rows if r.get('task_id') == job_id]
        actions = [r['action'] for r in history]
        if actions not in [['reserve'], ['reserve', 'release']]:
            raise ValueError('没有可释放的原本地预占，不能伪称费用已撤销')
        if job.get('status') == 'cancelled_before_dispatch' and job.get('cost_status') == 'released_unspent':
            if actions != ['reserve', 'release']: raise ValueError('已释放任务的台账出现变化，须重新对账')
            return job
        job.update(status='cancelled_before_dispatch', cost_status='release_pending')
        atomic_batch({path: json.dumps(job, ensure_ascii=False, indent=2)}, expected={path: version, ledger_path: ledger_version})
        version = digest(path)
        try:
            budget_event(project, job, 'release')
            job['cost_status'] = 'released_unspent'
        except Exception as exc:
            job.update(cost_status='reconciliation_required', cost_error_type=type(exc).__name__)
        atomic_batch({path: json.dumps(job, ensure_ascii=False, indent=2)}, expected={path: version})
        return job


if __name__ == '__main__':
    raise SystemExit(main())
