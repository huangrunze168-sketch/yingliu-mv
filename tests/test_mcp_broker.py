"""真实本地交接进程/台账与明确合成MCP回执；不调用付费工具。"""
import copy
import json
import subprocess
import sys
from pathlib import Path
import unittest

import test_submission_jobs as job_fixtures
import mcp_fixtures as fixture
from scripts import mcp_broker as broker, lovart_adapter as adapter, submission_jobs as jobs, media_origin
from scripts.pipeline_contracts import digest


class McpBrokerTests(unittest.TestCase):
    def setUp(self):
        job_fixtures.SubmissionJobTests.setUp(self)
        self.path, self.contract = fixture.contract(self.project, self.contract)

    def launch(self, raw=None):
        job = jobs.prepare(self.project, self.path)
        worker, box, cid = fixture.start(lambda: broker.run_dispatch(self.project, job['job_id'], timeout_sec=15), self.project)
        claimed = broker.claim(self.project, cid)
        fixture.send(self.project, claimed, raw or fixture.result(self.contract))
        return fixture.complete(worker, box), cid

    def test_real_handoff_preserves_exact_native_request_and_reservation(self):
        adapter.validate_request(self.project, self.contract)
        self.assertEqual(jobs.request_errors(self.contract), [])
        job, cid = self.launch()
        self.assertEqual(job['status'], 'pending')
        call = broker.load(broker.root_for(self.project, cid) / 'call.json')
        self.assertEqual(call['arguments'], self.contract['provider_request'])
        self.assertEqual(call['status'], 'consumed')
        self.assertTrue(call['invocation_claimed'])
        ledger = [json.loads(r) for r in (self.project/'state/ledger.jsonl').read_text().splitlines()]
        self.assertEqual(ledger[-1]['action'], 'reserve')
        with self.assertRaises(ValueError): broker.claim(self.project, cid)

    def test_terminal_query_without_id_uses_actual_request_context(self):
        job, _ = self.launch()
        # 后续编译改原契约不应改写已执行任务的历史请求，回查使用独立归档。
        self.path.write_text('{}', encoding='utf8')
        worker, box, cid = fixture.start(lambda: broker.run_poll(self.project, job['job_id'], timeout_sec=15), self.project)
        claimed = broker.claim(self.project, cid)
        self.assertEqual(claimed['arguments']['task_ids'], [job['provider_task_id']])
        fixture.send(self.project, claimed, fixture.result(self.contract, 'completed', ids=False))
        completed = fixture.complete(worker, box)
        self.assertEqual(completed['status'], 'completed')
        self.assertEqual(completed['provider_task_id'], job['provider_task_id'])
        self.assertEqual(completed['response']['task_binding_kind'], 'request_context')
        self.assertNotIn('task_ids', completed['response']['raw_response']['structuredContent'])
        identity = media_origin.completion(completed)
        self.assertEqual(Path(identity['contract_path']), jobs.executed_contract_path(job))

    def test_wrong_effective_model_keeps_id_and_raw_but_not_success(self):
        raw = fixture.result(self.contract)
        raw['structuredContent']['model'] = 'other-model'
        job, _ = self.launch(raw)
        self.assertEqual(job['status'], 'unknown')
        self.assertEqual(job['provider_task_id'], 'provider-fixture-task')
        self.assertEqual(job['unvalidated_response']['raw_response'], raw)
        with self.assertRaises(ValueError): broker.run_dispatch(self.project, job['job_id'])

    def test_moderated_state_maps_to_failed_without_changing_native_raw(self):
        raw = fixture.result(self.contract, 'moderated')
        job, _ = self.launch(raw)
        self.assertEqual(job['status'], 'failed')
        self.assertEqual(job['response']['raw_response']['structuredContent']['status'], 'moderated')
        self.assertEqual(job['cost_status'], 'reserved_awaiting_actual_cost')

    def test_plain_text_result_is_parsed_but_original_is_preserved(self):
        value = fixture.result(self.contract)['structuredContent']
        raw = {'content': [{'type': 'text', 'text': json.dumps(value, ensure_ascii=False)}]}
        job, _ = self.launch(raw)
        self.assertEqual(job['status'], 'pending')
        self.assertEqual(job['response']['raw_response']['mcp_result'], raw)
        tampered = copy.deepcopy(job['response'])
        tampered['raw_response']['decoded_text_result']['status'] = 'completed'
        with self.assertRaises(ValueError): jobs.validate_response(tampered, job=job)

    def test_sync_completion_without_id_is_unknown_not_invented_identity(self):
        raw = fixture.result(self.contract, 'completed', ids=False)
        job, cid = self.launch(raw)
        self.assertEqual(job['status'], 'unknown')
        self.assertIsNone(job['provider_task_id'])
        self.assertEqual(job['unvalidated_response']['raw_response'], raw)
        self.assertEqual(broker.load(broker.root_for(self.project, cid)/'tool_result.json'), raw)

    def test_multiple_ids_are_not_silently_reduced_to_one(self):
        raw = fixture.result(self.contract)
        raw['structuredContent']['task_ids'].append('another-fixture-task')
        job, _ = self.launch(raw)
        self.assertEqual(job['status'], 'unknown')
        self.assertEqual(job['unvalidated_response']['raw_response'], raw)

    def test_reference_pointer_must_be_a_real_h3_reference_field(self):
        broken = copy.deepcopy(self.contract)
        broken['provider_request_fields']['references'] = ['/unused']
        with self.assertRaises(ValueError): adapter.validate_request(self.project, broken)
        broken = copy.deepcopy(self.contract)
        broken['provider_request']['first_frame'] = 'https://a.lovart.ai/fixture.png'
        with self.assertRaises(ValueError): adapter.validate_request(self.project, broken)

    def test_schema_call_and_transport_record_replacement_are_rejected(self):
        call_path = self.project / self.contract['provider_adapter']['schema_call_record_path']
        original = call_path.read_bytes()
        call_path.write_bytes(original + b' ')
        with self.assertRaises(ValueError): adapter.validate_request(self.project, self.contract)
        call_path.write_bytes(original)
        job, cid = self.launch()
        result_path = broker.root_for(self.project, cid)/'tool_result.json'
        result_path.write_bytes(result_path.read_bytes() + b' ')
        with self.assertRaises(ValueError): jobs.validate_response(job['response'], job=job)

    def test_actual_process_end_is_required_before_late_result_recovery(self):
        job = jobs.prepare(self.project, self.path)
        code = """import sys,json
from unittest.mock import patch
from scripts import mcp_broker
with patch('scripts.submission_jobs.validate_submission_contract',return_value=[]):
    result=mcp_broker.run_dispatch(sys.argv[1],sys.argv[2],timeout_sec=3)
    print(json.dumps({'status':result['status']}),flush=True)
"""
        child = subprocess.Popen([sys.executable, '-X', 'utf8', '-u', '-c', code, str(self.project), job['job_id']],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf8')
        self.addCleanup(lambda: child.kill() if child.poll() is None else None)
        first = json.loads(child.stdout.readline())
        cid = first['call_id']
        claimed = broker.claim(self.project, cid)
        with self.assertRaisesRegex(ValueError, '仍在运行'): broker.recover(self.project, cid)
        out, err = child.communicate(timeout=15)
        self.assertEqual(child.returncode, 0, err)
        self.assertIn('unknown', out)
        fixture.send(self.project, claimed, fixture.result(self.contract))
        restored = broker.recover(self.project, cid)
        self.assertEqual(restored['status'], 'pending')
        self.assertEqual(restored['provider_task_id'], 'provider-fixture-task')
        self.assertEqual(broker.recover(self.project, cid)['provider_task_id'], restored['provider_task_id'])
