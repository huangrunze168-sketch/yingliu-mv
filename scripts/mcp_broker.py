"""MCP宿主与本地作业的实际交接；不持有凭证，不内置网络生成请求。"""
import base64
import ctypes
import hashlib
import json
import os
from pathlib import Path
import time
import uuid

try:
    from . import submission_jobs as jobs, lovart_adapter
    from .pipeline_contracts import atomic_batch, digest, require_no_transaction
    from .ledger import locked
    from .execution_policy import require_video_backend
except ImportError:
    import submission_jobs as jobs
    import lovart_adapter
    from pipeline_contracts import atomic_batch, digest, require_no_transaction
    from ledger import locked
    from execution_policy import require_video_backend


def encode(value): return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
def fingerprint(value): return 'sha256:' + hashlib.sha256(encode(value).encode('utf8')).hexdigest()
def load(path): return json.loads(Path(path).read_text(encoding='utf8'))


def process_token(pid):
    """读取实际进程创建标识；不以心跳文件或锁文件代替存活证据。"""
    if type(pid) is not int or pid <= 0: return None
    if os.name == 'nt':
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)]*4
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            error = ctypes.get_last_error()
            if error in {87, 1168}: return None
            if error == 5: raise PermissionError('无权读取原桥接进程，不能据此认定其停止')
            raise OSError(error, '无法观察原桥接进程')
        try:
            code = wintypes.DWORD()
            times = [wintypes.FILETIME() for _ in range(4)]
            if not kernel.GetExitCodeProcess(handle, ctypes.byref(code)):
                raise OSError(ctypes.get_last_error(), '无法读取原进程退出状态')
            if code.value != 259: return None
            if not kernel.GetProcessTimes(handle, *[ctypes.byref(t) for t in times]):
                raise OSError(ctypes.get_last_error(), '无法读取原进程创建标识')
            return str((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime)
        finally: kernel.CloseHandle(handle)
    try:
        text = Path(f'/proc/{pid}/stat').read_text()
        rest = text[text.rfind(')')+2:].split()
        return rest[19] if rest[0] not in {'Z', 'X'} else None
    except FileNotFoundError: return None


def root_for(project, call_id):
    if not isinstance(call_id, str) or len(call_id) != 32 or any(c not in '0123456789abcdef' for c in call_id):
        raise ValueError('MCP交接ID无效')
    project = Path(project).resolve()
    root = (project / 'state/mcp_calls' / call_id).resolve()
    root.relative_to(project)
    return root


def _record(project, call_id):
    root = root_for(project, call_id)
    path = root / 'call.json'
    version, call = digest(path), load(path)
    if (call.get('call_id') != call_id or call.get('project_id') != str(Path(project).resolve())
            or fingerprint(call['arguments']) != call.get('request_sha256')):
        raise ValueError('MCP交接项目、ID或实际参数已改变')
    return root, path, version, call


def exchange(project, tool, arguments, *, mode, job_id=None, timeout_sec=600, basis=None):
    project = Path(project).resolve()
    policy_versions = require_video_backend(project)
    if not 1 <= timeout_sec <= 3600: raise ValueError('交接等待须为1–3600秒')
    call_id = uuid.uuid4().hex
    root = root_for(project, call_id)
    path = root / 'call.json'
    token = process_token(os.getpid())
    if token is None: raise ValueError('无法验证交接进程身份，不启动MCP调用')
    call = {'schema_version': 1, 'call_id': call_id, 'project_id': str(project), 'job_id': job_id,
            'mode': mode, 'tool': tool, 'arguments': arguments, 'request_sha256': fingerprint(arguments),
            'nonce': uuid.uuid4().hex, 'pid': os.getpid(), 'process_token': token,
            'status': 'awaiting_mcp', 'chunks': 0, 'bytes_received': 0,
            'artistic_approval': False, 'scope': 'actual_host_handoff_not_provider_signed_attestation'}
    atomic_batch({path: encode(call)}, expected={path: None, **(basis or {}), **policy_versions})
    print(encode({'status': 'awaiting_mcp', 'call_id': call_id, 'request_path': str(path)}), flush=True)
    deadline = time.monotonic() + timeout_sec
    while True:
        with locked(root / 'exchange.index'):
            _, _, version, current = _record(project, call_id)
            response = root / 'response.json'
            if response.is_file():
                packet = load(response)
                if any(packet.get(k) != current[k] for k in ('call_id', 'nonce', 'request_sha256', 'tool')):
                    raise ValueError('实际MCP返回与已领取调用不一致')
                raw_path = root / 'tool_result.json'
                raw = packet.get('raw_result')
                current.update(status='consumed', tool_result_path=str(raw_path), tool_result_sha256=fingerprint(raw),
                               response_sha256=digest(response))
                atomic_batch({raw_path: encode(raw), path: encode(current)},
                             expected={path: version, response: digest(response), raw_path: None})
                context = {'tool': tool, 'arguments': arguments, 'call_id': call_id,
                           'request_sha256': call['request_sha256'], 'call_record_path': str(path),
                           'tool_result_path': str(raw_path), 'call_record_sha256': digest(path),
                           'tool_result_sha256': digest(raw_path)}
                if packet.get('transport_error'):
                    raise RuntimeError('实际MCP调用抛错；原始交接与错误已保留，禁止自动重提')
                return raw, context
            if time.monotonic() >= deadline:
                current.update(status='timed_out_unknown')
                atomic_batch({path: encode(current)}, expected={path: version})
                raise TimeoutError('MCP交接超时，结果未知；保留预占及交接，不重复生成')
        time.sleep(.1)


def claim(project, call_id):
    root, path, version, call = _record(project, call_id)
    if call['status'] != 'awaiting_mcp' or process_token(call['pid']) != call['process_token']:
        raise ValueError('交接未等待或原进程已停止，不能凭旧文件发起MCP调用')
    expected = {path: version, **require_video_backend(project)}
    # 较慢的生产检查在短交接锁外执行，最终CAS拒绝过期/并发领取。
    if call['mode'] == 'dispatch':
        jp = jobs.job_path(project, call['job_id'])
        expected[jp] = digest(jp)
        job = load(jp)
        if job.get('status') != 'dispatching' or job.get('project_id') != str(Path(project).resolve()):
            raise ValueError('只有真实派发中的原作业可领取')
        contract, basis = jobs.require_dispatch_basis(project, job)
        expected.update(basis)
        expected.update(lovart_adapter.validate_request(project, contract))
        if call['tool'] != 'mcp__lovart__generate_video' or call['arguments'] != contract['provider_request']:
            raise ValueError('领取参数与当前批准的原生请求不同')
    elif call['mode'] == 'poll':
        jp = jobs.job_path(project, call['job_id'])
        expected[jp] = digest(jp)
        job = load(jp)
        contract = load(jobs.executed_contract_path(job))
        if (job.get('status') != 'polling' or job.get('project_id') != str(Path(project).resolve())
                or call['tool'] != 'mcp__lovart__get_task_status'
                or call['arguments'] != {'task_ids': [job['provider_task_id']],
                                         'project_id': contract['provider_adapter']['provider_project_id']}):
            raise ValueError('回查交接不对应原任务/项目')
    elif call['mode'] == 'describe':
        if call['tool'] != 'mcp__lovart__describe_model' or set(call['arguments']) != {'model'}:
            raise ValueError('只读交接不能调用生成工具')
    else: raise ValueError('未知交接模式')
    with locked(root / 'exchange.index'):
        if process_token(call['pid']) != call['process_token']:
            raise ValueError('领取前原等待进程已停止')
        call.update(status='invocation_claimed', invocation_claimed=True)
        atomic_batch({path: encode(call)}, expected=expected)
        return {k: call[k] for k in ('call_id', 'nonce', 'request_sha256', 'tool', 'arguments')}


def put_chunk(project, call_id, nonce, index, data_base64):
    root = root_for(project, call_id)
    data = base64.b64decode(data_base64, validate=True)
    if len(data) > 12000: raise ValueError('交接单块过大')
    with locked(root / 'exchange.index'):
        _, path, version, call = _record(project, call_id)
        if (call['nonce'] != nonce or call.get('invocation_claimed') is not True
                or call['status'] not in {'invocation_claimed', 'timed_out_unknown'}
                or type(index) is not int or index != call['chunks']):
            raise ValueError('交接返回nonce、状态或分块顺序不一致')
        partial = root / 'response.partial'
        old = partial.read_bytes() if partial.exists() else b''
        if len(old) != call['bytes_received'] or len(old)+len(data) > 64*1024*1024:
            raise ValueError('交接分块长度不一致或超出限制')
        call.update(chunks=index+1, bytes_received=len(old)+len(data))
        atomic_batch({partial: old+data, path: encode(call)},
                     expected={path: version, partial: digest(partial) if partial.exists() else None})


def receive(project, call_id, nonce):
    root = root_for(project, call_id)
    with locked(root / 'exchange.index'):
        _, path, version, call = _record(project, call_id)
        if (call['nonce'] != nonce or call.get('invocation_claimed') is not True
                or call['status'] not in {'invocation_claimed', 'timed_out_unknown'}):
            raise ValueError('调用没有可接收的实际结果，不能覆盖或重复返回')
        partial = root / 'response.partial'
        data = partial.read_bytes()
        packet = json.loads(data)
        if any(packet.get(k) != call[k] for k in ('call_id', 'nonce', 'request_sha256', 'tool')):
            raise ValueError('实际MCP返回与已领取调用不一致')
        if set(packet) - {'call_id', 'nonce', 'request_sha256', 'tool', 'raw_result', 'transport_error'}:
            raise ValueError('交接响应含未知字段')
        late = call['status'] == 'timed_out_unknown'
        call.update(status='late_response_saved' if late else 'response_received')
        response = root / 'response.json'
        atomic_batch({response: data, path: encode(call)}, expected={path: version, partial: digest(partial), response: None})
        return {'status': call['status'], 'scope': 'response_saved_not_generation_or_art_approval'}


def run_dispatch(project, job_id, *, timeout_sec=600):
    project = Path(project).resolve()
    job = load(jobs.job_path(project, job_id))
    contract = load(jobs.executed_contract_path(job))
    schema_versions = lovart_adapter.validate_request(project, contract)
    def invoke(arguments):
        raw, context = exchange(project, 'mcp__lovart__generate_video', arguments,
                                mode='dispatch', job_id=job_id, timeout_sec=timeout_sec, basis=schema_versions)
        try: return lovart_adapter.normalize(raw, contract, context=context)
        except Exception as exc:
            return {'raw_response': raw, 'call_context': context, 'normalization_error': str(exc)}
    return jobs.dispatch(project, job_id, invoke)


def run_poll(project, job_id, *, timeout_sec=600):
    project = Path(project).resolve()
    require_video_backend(project)
    job = load(jobs.job_path(project, job_id))
    contract = load(jobs.executed_contract_path(job))
    def lookup(task_id):
        raw, context = exchange(project, 'mcp__lovart__get_task_status',
                                {'task_ids': [task_id], 'project_id': contract['provider_adapter']['provider_project_id']},
                                mode='poll', job_id=job_id, timeout_sec=timeout_sec)
        try: return lovart_adapter.normalize(raw, contract, context=context, original_task_id=task_id)
        except Exception as exc:
            return {'raw_response': raw, 'call_context': context, 'normalization_error': str(exc)}
    return jobs.poll(project, job_id, lookup)


def recover(project, call_id):
    """只消费原调用已保存结果；须确认原进程结束，绝不再次调用供应商。"""
    project = Path(project).resolve()
    root = root_for(project, call_id)
    with locked(root / 'exchange.index'):
        _, path, version, call = _record(project, call_id)
        if process_token(call['pid']) == call['process_token']:
            raise ValueError('原等待进程仍在运行，继续观察原进程，不启动恢复或重提')
        if call.get('mode') not in {'dispatch', 'poll'} or call.get('invocation_claimed') is not True:
            raise ValueError('不是已领取的原视频调用，不能补造供应商执行')
        response_path = root / 'response.json'
        packet = load(response_path)
        if (any(packet.get(k) != call[k] for k in ('call_id', 'nonce', 'request_sha256', 'tool'))
                or packet.get('transport_error')):
            raise ValueError('原调用返回未可靠关联或仍是传输错误，不能自动重提')
        job_path = jobs.job_path(project, call['job_id'])
        job_version, job = digest(job_path), load(job_path)
        if job.get('project_id') != str(project): raise ValueError('原作业本地项目不一致')
        if (job.get('response') or {}).get('call_context', {}).get('call_id') == call_id:
            return job
        if job.get('status') not in {'unknown', 'dispatching', 'polling'}:
            raise ValueError('作业已被其他结果更新，不能用旧返回覆盖')
        raw = packet.get('raw_result')
        raw_path = root / 'tool_result.json'
        call.update(status='consumed', recovered_after_process_end=True, tool_result_path=str(raw_path),
                    tool_result_sha256=fingerprint(raw), response_sha256=digest(response_path))
        atomic_batch({raw_path: encode(raw), path: encode(call)},
                     expected={path: version, response_path: digest(response_path),
                               raw_path: digest(raw_path) if raw_path.exists() else None})
        context = {'tool': call['tool'], 'arguments': call['arguments'], 'call_id': call_id,
                   'request_sha256': call['request_sha256'], 'call_record_path': str(path),
                   'call_record_sha256': digest(path), 'tool_result_path': str(raw_path),
                   'tool_result_sha256': digest(raw_path)}
        contract = load(jobs.executed_contract_path(job))
        normalized = None
        try:
            normalized = lovart_adapter.normalize(raw, contract, context=context,
                original_task_id=job.get('provider_task_id') if call['mode'] == 'poll' else None)
            jobs.validate_response(normalized, job.get('provider_task_id') if call['mode'] == 'poll' else None, job=job)
            if normalized.get('validation_errors'): raise ValueError('; '.join(normalized['validation_errors']))
            old_cost = job.get('cost_status')
            job.update(status=normalized['status'], provider_task_id=normalized['task_id'], response=normalized,
                       recovery_source='preserved_original_mcp_result_without_new_call',
                       cost_status=old_cost if old_cost in {'settled', 'reconciliation_required'} else 'reserved_awaiting_actual_cost')
        except Exception as exc:
            jobs.preserve_response(job, normalized, job.get('provider_task_id') if call['mode'] == 'poll' else None)
            job.update(status='unknown', recovery_error_type=type(exc).__name__)
        atomic_batch({job_path: encode(job)}, expected={job_path: job_version})
        return job


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('describe', 'dispatch', 'poll', 'claim', 'chunk', 'receive', 'recover'))
    parser.add_argument('project_dir', type=Path)
    parser.add_argument('--call-id'); parser.add_argument('--job-id'); parser.add_argument('--nonce')
    parser.add_argument('--model'); parser.add_argument('--chunk-base64'); parser.add_argument('--index', type=int)
    parser.add_argument('--timeout-sec', type=int, default=600)
    args = parser.parse_args()
    try:
        project = args.project_dir.resolve()
        if not project.is_dir(): raise ValueError('项目目录不存在')
        if args.action == 'claim': result = claim(project, args.call_id)
        elif args.action == 'chunk':
            put_chunk(project, args.call_id, args.nonce, args.index, args.chunk_base64)
            result = {'status': 'chunk_saved'}
        elif args.action == 'receive': result = receive(project, args.call_id, args.nonce)
        elif args.action == 'dispatch': result = run_dispatch(project, args.job_id, timeout_sec=args.timeout_sec)
        elif args.action == 'poll': result = run_poll(project, args.job_id, timeout_sec=args.timeout_sec)
        elif args.action == 'recover': result = recover(project, args.call_id)
        else:
            if not args.model: raise ValueError('describe须显式--model')
            raw, context = exchange(project, 'mcp__lovart__describe_model', {'model': args.model},
                                    mode='describe', timeout_sec=args.timeout_sec)
            _, value, _ = lovart_adapter.payload(raw)
            if value.get('model') != args.model or not isinstance(value.get('schema'), dict):
                raise ValueError('实际schema返回不匹配请求模型')
            result = {'status': 'schema_received', 'context': context, 'artistic_approval': False}
        print(encode(result), flush=True)
        return 1 if result.get('status') in {'unknown', 'failed'} else 0
    except Exception as exc:
        print(encode({'status': 'blocked', 'errors': [str(exc)]}), flush=True)
        return 1


if __name__ == '__main__': raise SystemExit(main())
