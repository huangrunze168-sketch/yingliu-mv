"""用户显式选择的Lovart H3适配；保留MCP原文，不默认模型、不猜任务ID。"""
import json
import hashlib
from pathlib import Path

try:
    from .pipeline_contracts import digest
    from .submission_jobs import PROVIDER_STATUS_MAP
except ImportError:
    from pipeline_contracts import digest
    from submission_jobs import PROVIDER_STATUS_MAP


def payload(raw):
    """structuredContent或实际text JSON；解析视图不冒充原始供应商字段。"""
    if isinstance(raw, dict) and isinstance(raw.get('structuredContent'), dict):
        return raw, raw['structuredContent'], '/structuredContent'
    if isinstance(raw, dict) and raw.get('mcp_result') is not None:
        original = raw['mcp_result']
        text = [r['text'] for r in original.get('content', []) if r.get('type') == 'text']
        if len(text) != 1 or json.loads(text[0]) != raw.get('decoded_text_result'):
            raise ValueError('MCP文本解析视图与原文不一致')
        return raw, raw['decoded_text_result'], '/decoded_text_result'
    if isinstance(raw, dict):
        text = [r['text'] for r in raw.get('content', []) if r.get('type') == 'text']
        if len(text) == 1:
            decoded = json.loads(text[0])
            if isinstance(decoded, dict):
                envelope = {'mcp_result': raw, 'decoded_text_result': decoded}
                return envelope, decoded, '/decoded_text_result'
    raise ValueError('MCP返回缺少可核验的结构化结果，保留原文待回查')


def context_versions(response, job):
    context = response['call_context']
    project = Path(job['project_id']).resolve()
    path = (project / 'state/mcp_calls' / context['call_id'] / 'call.json').resolve()
    if Path(context['call_record_path']).resolve() != path or path.parent.parent != project / 'state/mcp_calls':
        raise ValueError('MCP执行上下文属于其他本地项目或路径')
    raw_path = (path.parent / 'tool_result.json').resolve()
    if Path(context['tool_result_path']).resolve() != raw_path:
        raise ValueError('MCP原文路径与调用不一致')
    response_path = path.parent / 'response.json'
    versions = {path: digest(path), raw_path: digest(raw_path), response_path: digest(response_path)}
    if versions[path] != context['call_record_sha256'] or versions[raw_path] != context['tool_result_sha256']:
        raise ValueError('实际MCP调用记录或原文已改变')
    call, original = json.loads(path.read_text(encoding='utf8')), json.loads(raw_path.read_text(encoding='utf8'))
    packet = json.loads(response_path.read_text(encoding='utf8'))
    raw = response['raw_response'].get('mcp_result', response['raw_response'])
    if (call.get('status') != 'consumed' or call.get('invocation_claimed') is not True
            or call.get('project_id') != str(project) or call.get('job_id') != job['job_id']
            or call.get('tool') != context['tool'] or call.get('arguments') != context['arguments']
            or call.get('request_sha256') != context['request_sha256'] or original != raw
            or call.get('tool_result_sha256') != versions[raw_path]
            or call.get('response_sha256') != versions[response_path]
            or packet.get('raw_result') != raw or packet.get('nonce') != call['nonce']
            or packet.get('call_id') != call['call_id'] or packet.get('tool') != call['tool']):
        raise ValueError('标准化结果与实际MCP交接原文/参数不一致')
    canonical = json.dumps(context['arguments'], ensure_ascii=False, sort_keys=True, allow_nan=False).encode('utf8')
    if context['request_sha256'] != 'sha256:' + hashlib.sha256(canonical).hexdigest():
        raise ValueError('MCP原始调用参数指纹不一致')
    try:
        from .submission_jobs import executed_contract_path
    except ImportError:
        from submission_jobs import executed_contract_path
    contract = json.loads(executed_contract_path(job).read_text(encoding='utf8'))
    if context['tool'] == 'mcp__lovart__generate_video':
        expected = contract['provider_request']
    elif context['tool'] == 'mcp__lovart__get_task_status':
        expected = {'task_ids': [job['provider_task_id']], 'project_id': contract['provider_adapter']['provider_project_id']}
    else: raise ValueError('视频结果不能来自其他工具')
    if context['arguments'] != expected: raise ValueError('实际工具调用不是该作业的原始请求或原任务回查')
    return versions


def validate_request(project, contract):
    project = Path(project).resolve()
    adapter = contract['provider_adapter']
    if adapter.get('kind') != 'lovart_mcp' or contract.get('selected_model') != 'minimax/minimax-h3':
        raise ValueError('此适配器仅执行用户明确选择的Lovart MiniMax-H3，不使用默认模型')
    schema_path = (project / adapter['schema_response_path']).resolve()
    schema_path.relative_to(project)
    if digest(schema_path) != adapter['schema_response_sha256']:
        raise ValueError('实际模型schema回执已改变')
    schema_call_path = (project / adapter['schema_call_record_path']).resolve()
    schema_call_path.relative_to(project)
    schema_call = json.loads(schema_call_path.read_text(encoding='utf8'))
    response_path = schema_call_path.parent / 'response.json'
    schema_packet = json.loads(response_path.read_text(encoding='utf8'))
    if (digest(schema_call_path) != adapter['schema_call_record_sha256']
            or schema_call.get('project_id') != str(project) or schema_call.get('status') != 'consumed'
            or schema_call.get('invocation_claimed') is not True or schema_call.get('mode') != 'describe'
            or schema_call.get('tool') != 'mcp__lovart__describe_model'
            or schema_call.get('arguments') != {'model': contract['selected_model']}
            or Path(schema_call.get('tool_result_path', '')).resolve() != schema_path
            or schema_call.get('tool_result_sha256') != digest(schema_path)
            or schema_call.get('response_sha256') != digest(response_path)
            or schema_packet.get('raw_result') != json.loads(schema_path.read_text(encoding='utf8'))):
        raise ValueError('schema缺少同项目实际只读MCP交接回执')
    _, descriptor, _ = payload(json.loads(schema_path.read_text(encoding='utf8')))
    if descriptor.get('model') != contract['selected_model']: raise ValueError('schema不是所选模型')
    schema = descriptor['schema']
    if 'reference-to-video' not in {m['mode'] for m in schema['x-capabilities']['modes']}:
        raise ValueError('实际schema没有全能参考模式')
    request = contract['provider_request']
    required = {'project_id', 'model', 'prompt', 'duration', 'aspect_ratio', 'params'}
    if set(request) != required or request['model'] != contract['selected_model']:
        raise ValueError('MCP请求必须显式绑定项目、模型和全能参考；不接受默认或首尾帧降级')
    if (not isinstance(adapter.get('provider_project_id'), str) or not adapter['provider_project_id']
            or request['project_id'] != adapter['provider_project_id']):
        raise ValueError('须使用同一实际Lovart项目ID，不能隐式新建项目')
    properties = schema['properties']
    if type(request['duration']) is not int or request['duration'] not in properties['duration']['enum']:
        raise ValueError('生成时长不在实际H3 schema整数枚举内')
    style = json.loads((project / 'state/style.json').read_text(encoding='utf8'))
    if request['aspect_ratio'] != style['aspect_ratio'] or request['aspect_ratio'] not in properties['ratio']['enum']:
        raise ValueError('实际请求比例与当前风格锁或schema不一致')
    params = request['params']
    if (not isinstance(params, dict) or 'resolution' not in params
            or set(params) - {'resolution', 'image_list', 'audio_list', 'video_list'}
            or params['resolution'] not in properties['resolution']['enum']):
        raise ValueError('H3原生参数缺分辨率或包含未经支持的字段/混合模式')
    locators, pointers = [], []
    for modality in ('image', 'audio', 'video'):
        key = modality + '_list'
        rows = params.get(key, [])
        if not isinstance(rows, list) or len(rows) > properties[key]['maxItems']:
            raise ValueError('实际全能参考列表类型或数量不符合schema')
        for index, row in enumerate(rows):
            url_key = modality + '_url'
            if (not isinstance(row, dict) or set(row) - {url_key, 'type', 'role'}
                    or not isinstance(row.get(url_key), dict) or set(row[url_key]) != {'url'}
                    or not isinstance(row[url_key]['url'], str) or not row[url_key]['url']
                    or any(not isinstance(row[k], str) or not row[k] for k in ('type', 'role') if k in row)):
                raise ValueError('参考条目须使用实际schema的同模态URL字段')
            locators.append((modality, row[url_key]['url']))
            pointers.append(f'/params/{key}/{index}/{url_key}/url')
    expected = [(b['modality'], b['source'].get('url') or b['source'].get('asset_id')) for b in contract['reference_bindings']]
    if not locators or sorted(locators) != sorted(expected) or set(pointers) != set(contract['provider_request_fields']['references']):
        raise ValueError('真正传入H3列表的参考与绑定/原生字段位置不一致')
    fields = contract['provider_request_fields']
    if any(fields.get(k) != v for k, v in {'model': '/model', 'prompt': '/prompt', 'duration': '/duration',
                                         'resolution': '/params/resolution'}.items()):
        raise ValueError('MCP核心字段指针不能指向未实际使用的占位字段')
    return {schema_path: digest(schema_path), schema_call_path: digest(schema_call_path), response_path: digest(response_path)}


def normalize(raw_result, contract, *, context, original_task_id=None):
    raw, value, prefix = payload(raw_result)
    provider_status = value.get('status')
    status = PROVIDER_STATUS_MAP.get(provider_status)
    if status is None: raise ValueError('未知供应商状态，不能猜完成或自动重试')
    ids = value.get('task_ids') or []
    fields = {'status': prefix + '/status'}
    result = {'adapter': 'lovart_mcp', 'status': status, 'raw_response': raw,
              'response_fields': fields, 'status_mapping': dict(PROVIDER_STATUS_MAP), 'validation_errors': []}
    original = raw.get('mcp_result', raw)
    if original.get('isError') is True: result['validation_errors'].append('MCP工具标记了错误，不能据此认定请求成功')
    if ids:
        if len(ids) != 1 or not isinstance(ids[0], str) or not ids[0]:
            raise ValueError('单作业返回多个或无效任务ID，须保留原文并核对，不能丢弃其他任务')
        result['task_id'] = ids[0]
        fields['task_id'] = prefix + '/task_ids/0'
        if original_task_id is not None and ids[0] != original_task_id:
            result['validation_errors'].append('返回任务ID与原回查任务不一致')
    elif original_task_id is not None:
        result.update(task_id=original_task_id, task_binding_kind='request_context', request_context=context)
    else:
        raise ValueError('首次生成返回没有可回查任务ID；不伪造ID，保留原始终态/拒绝结果待核对')
    adapter = contract['provider_adapter']
    if value.get('project_id') != adapter['provider_project_id']:
        result['validation_errors'].append('MCP响应项目ID缺失或不匹配')
    if provider_status in {'pending', 'completed'}:
        request = contract['provider_request']
        if value.get('model') != request['model']: result['validation_errors'].append('响应模型缺失或不匹配')
        spec = value.get('spec') or {}
        if any(spec.get(k) != v for k, v in {'aspect_ratio': request['aspect_ratio'],
                                           'duration_seconds': request['duration'],
                                           'resolution': request['params']['resolution']}.items()):
            result['validation_errors'].append('供应商实际规格缺失或与原请求不同')
    if provider_status == 'completed' and not any(a.get('type') == 'video' and isinstance(a.get('url'), str)
                                                for a in value.get('artifacts', []) or []):
        result['validation_errors'].append('供应商完成结果缺少实际视频产物')
    result['call_context'] = context
    return result
