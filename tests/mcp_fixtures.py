"""明确的MCP合成回执；交接/本地进程真实执行，不调用付费后端。"""
import base64
import io
import json
import time
from contextlib import redirect_stdout
from pathlib import Path
from threading import Thread

from scripts import mcp_broker as broker, submission_jobs as jobs
from scripts.pipeline_contracts import digest


def start(operation, project):
    before = set((Path(project) / 'state/mcp_calls').glob('*/call.json'))
    box = {}
    def execute():
        with redirect_stdout(io.StringIO()):
            try: box['result'] = operation()
            except BaseException as exc: box['error'] = exc
    worker = Thread(target=execute, daemon=True)
    worker.start()
    deadline = time.monotonic()+20
    while time.monotonic() < deadline:
        paths = set((Path(project) / 'state/mcp_calls').glob('*/call.json')) - before
        if paths: return worker, box, next(iter(paths)).parent.name
        if not worker.is_alive(): raise box.get('error', RuntimeError('没有创建实际交接'))
        time.sleep(.02)
    raise TimeoutError('测试交接未建立')


def send(project, claimed, raw):
    packet = {k: claimed[k] for k in ('call_id', 'nonce', 'request_sha256', 'tool')}
    packet.update(raw_result=raw, transport_error=None)
    data = json.dumps(packet, ensure_ascii=False).encode('utf8')
    for index, offset in enumerate(range(0, len(data), 4500)):
        broker.put_chunk(project, claimed['call_id'], claimed['nonce'], index,
                         base64.b64encode(data[offset:offset+4500]).decode('ascii'))
    return broker.receive(project, claimed['call_id'], claimed['nonce'])


def complete(worker, box):
    worker.join(timeout=20)
    if worker.is_alive(): raise TimeoutError('测试只观察原交接，未重启')
    if 'error' in box: raise box['error']
    return box['result']


def model_schema():
    return {'structuredContent': {'model': 'minimax/minimax-h3', 'scope': 'isolated_test_fixture_not_provider',
            'schema': {'properties': {'duration': {'enum': list(range(5, 16))}, 'ratio': {'enum': ['16:9']},
                                    'resolution': {'enum': ['768P', '2K']},
                                    **{m+'_list': {'maxItems': n} for m, n in [('image', 9), ('audio', 3), ('video', 3)]}},
                       'x-capabilities': {'modes': [{'mode': 'reference-to-video'}]}}}}


def contract(project, original):
    project = Path(project).resolve()
    worker, box, call_id = start(lambda: broker.exchange(project, 'mcp__lovart__describe_model',
                                    {'model': 'minimax/minimax-h3'}, mode='describe', timeout_sec=15), project)
    claimed = broker.claim(project, call_id)
    send(project, claimed, model_schema())
    _, context = complete(worker, box)
    value = json.loads(json.dumps(original))
    value.update(selected_model='minimax/minimax-h3', resolution='768P')
    if value['duration_sec'] < 5:
        value.update(generation_duration_sec=5, generation_trim_tail_sec=5-value['duration_sec'],
                     generation_duration_reason='测试H3最短5秒，音乐窗口不缩短')
    value.setdefault('backend_capabilities', {}).update(min_duration_sec=5, max_duration_sec=15,
                                                       duration_quantum_sec=1, resolutions=['768P', '2K'])
    params = {'resolution': '768P'}
    pointers = []
    for index, binding in enumerate(value['reference_bindings']):
        modality = binding.setdefault('modality', 'image')
        url = f'https://a.lovart.ai/isolated-fixture-{index}.{modality}'
        binding['source']['url'] = url
        receipt = jobs.record_upload(project, project/binding['source']['local_path'], locator=url,
                                     locator_pointer='/url', raw_response={'url': url, 'scope': 'isolated_test_fixture'})
        binding['upload_receipt_path'] = str(receipt.relative_to(project))
        rows = params.setdefault(modality+'_list', [])
        pointers.append(f'/params/{modality}_list/{len(rows)}/{modality}_url/url')
        rows.append({modality+'_url': {'url': url}})
    value['provider_adapter'] = {'kind': 'lovart_mcp', 'provider_project_id': 'isolated-provider-project',
                    'schema_response_path': str(Path(context['tool_result_path']).relative_to(project)),
                    'schema_response_sha256': context['tool_result_sha256'],
                    'schema_call_record_path': str(Path(context['call_record_path']).relative_to(project)),
                    'schema_call_record_sha256': context['call_record_sha256']}
    value['provider_request'] = {'model': value['selected_model'], 'project_id': 'isolated-provider-project',
                                 'prompt': value['prompt'], 'duration': value.get('generation_duration_sec', value['duration_sec']),
                                 'aspect_ratio': '16:9', 'params': params}
    value['provider_request_fields'] = {'model': '/model', 'prompt': '/prompt', 'duration': '/duration',
                                        'resolution': '/params/resolution', 'references': pointers}
    style = project / 'state/style.json'
    if not style.exists(): style.write_text(json.dumps({'aspect_ratio': '16:9'}), encoding='utf8')
    path = project / 'state/mcp_contract.json'
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf8')
    return path, value


def result(contract, status='pending', *, ids=True, task='provider-fixture-task'):
    request = contract['provider_request']
    value = {'status': status, 'project_id': request['project_id'], 'model': request['model'],
             'spec': {'aspect_ratio': request['aspect_ratio'], 'duration_seconds': request['duration'],
                      'resolution': request['params']['resolution']}, 'scope': 'isolated_test_fixture'}
    if ids: value['task_ids'] = [task]
    if status == 'completed': value['artifacts'] = [{'type': 'video', 'url': 'https://a.lovart.ai/isolated-result.mp4'}]
    return {'structuredContent': value}
