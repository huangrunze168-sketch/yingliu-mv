"""隔离供应商作业状态机与真实本地预算台账；不调用付费后端。"""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import submission_jobs as jobs
from scripts.pipeline_contracts import digest
from scripts.ledger import record as ledger_record


def job_contract_fixture(project, contract):
    """扩充已有合法契约，用显式测试原生字段与上传响应验证交接。"""
    project = Path(project).resolve()
    contract = copy.deepcopy(contract)
    contract.update(selected_model='explicit-test-model', backend_account_id='isolated-test-account')
    contract.setdefault('backend_capabilities', {})['concurrency_limit'] = 2
    locators = []
    for binding in contract['reference_bindings']:
        source = binding['source']
        locator = source.get('url') or source['asset_id']
        path = jobs.record_upload(project, project/source['local_path'], locator=locator,
                                  locator_pointer='/location', raw_response={'location': locator, 'scope': 'test-fixture'})
        binding['upload_receipt_path'] = path.relative_to(project).as_posix()
        locators.append(locator)
    contract['provider_request'] = {'engine': contract['selected_model'], 'text': contract['prompt'],
                                    'seconds': contract['duration_sec'], 'size': contract['resolution'], 'assets': locators}
    contract['provider_request_fields'] = {'model': '/engine', 'prompt': '/text', 'duration': '/seconds',
                                         'resolution': '/size', 'references': [f'/assets/{i}' for i in range(len(locators))]}
    path = project/'state/job_contract.json'
    path.write_text(json.dumps(contract, ensure_ascii=False, indent=2), encoding='utf8')
    return path, contract


def response(status='pending', task_id='provider-fixture-task'):
    return {'status': status, 'task_id': task_id,
            'raw_response': {'state': status, 'id': task_id},
            'response_fields': {'status': '/state', 'task_id': '/id'}}


class SubmissionJobTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name).resolve()
        (self.project/'input').mkdir()
        (self.project/'state').mkdir()
        (self.project/'state/execution_policy.json').write_text(json.dumps({'project_id': str(self.project),
            'video_backend_access': True, 'instruction': '仅为本地隔离测试模拟授权；不调用任何实际后端'}), encoding='utf8')
        source = self.project/'input/source.bin'
        source.write_bytes(b'isolated source bytes')
        contract = {'project_id': str(self.project), 'chapter_id': 'CH01', 'submission_phase': 'prototype',
                    'prompt': 'isolated contract fixture', 'duration_sec': 5, 'resolution': 'test-resolution',
                    'cash_limit_cny': '5', 'estimated_cash_cny': '2',
                    'reference_bindings': [{'source': {'local_path': 'input/source.bin',
                                                      'sha256': digest(source), 'asset_id': 'uploaded-fixture'}}]}
        self.contract_path, self.contract = job_contract_fixture(self.project, contract)
        # 本组隔离作业状态机；完整制作包和前置门禁由ProductionContracts另测。
        patcher = patch.object(jobs, 'validate_submission_contract', return_value=[])
        patcher.start(); self.addCleanup(patcher.stop)

    def save_contract(self):
        self.contract_path.write_text(json.dumps(self.contract), encoding='utf8')

    def ledger(self):
        return [json.loads(s) for s in (self.project/'state/ledger.jsonl').read_text(encoding='utf8').splitlines()]

    def test_prepare_reserves_before_adapter_and_dispatches_exact_payload(self):
        job = jobs.prepare(self.project, self.contract_path)
        self.assertEqual(self.ledger()[-1]['action'], 'reserve')
        seen = []
        def adapter(payload):
            self.assertEqual(self.ledger()[-1]['action'], 'reserve')
            seen.append(payload)
            return response()
        result = jobs.dispatch(self.project, job['job_id'], adapter)
        self.assertEqual(seen, [self.contract['provider_request']])
        self.assertEqual(result['status'], 'pending')
        self.assertEqual(result['provider_task_id'], 'provider-fixture-task')

    def test_unknown_result_keeps_reservation_and_forbids_duplicate_dispatch(self):
        job = jobs.prepare(self.project, self.contract_path)
        calls = []
        def lost(payload):
            calls.append(payload)
            raise TimeoutError('test timeout after possible acceptance')
        result = jobs.dispatch(self.project, job['job_id'], lost)
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(self.ledger()[-1]['action'], 'reserve')
        with self.assertRaises(ValueError): jobs.dispatch(self.project, job['job_id'], lost)
        self.assertEqual(len(calls), 1)

    def test_new_repair_after_preparation_blocks_old_dispatch(self):
        job = jobs.prepare(self.project, self.contract_path)
        rid = 'a' * 32
        path = self.project / 'state/repairs' / f'{rid}.json'
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({'repair_id': rid, 'project_id': str(self.project),
                                    'chapter_id': 'CH01', 'status': 'open', 'plan_sha256': 'fixture-plan'}), encoding='utf8')
        calls = []
        with self.assertRaises(ValueError): jobs.dispatch(self.project, job['job_id'], lambda payload: calls.append(payload))
        self.assertEqual(calls, [])
        self.assertEqual(jobs.load(jobs.job_path(self.project, job['job_id']))['status'], 'prepared')

    def test_repair_record_change_after_preparation_blocks_dispatch(self):
        rid = 'a' * 32
        path = self.project / 'state/repairs' / f'{rid}.json'
        path.parent.mkdir(parents=True)
        row = {'repair_id': rid, 'project_id': str(self.project), 'chapter_id': 'CH01',
               'status': 'open', 'plan_sha256': 'fixture-plan'}
        path.write_text(json.dumps(row), encoding='utf8')
        self.contract.update(repair_id=rid, repair_plan_sha256='fixture-plan')
        self.save_contract()
        job = jobs.prepare(self.project, self.contract_path)
        row['note'] = '返修依据在准备后改变'
        path.write_text(json.dumps(row), encoding='utf8')
        with self.assertRaisesRegex(ValueError, '返修状态'):
            jobs.dispatch(self.project, job['job_id'], lambda payload: response())

    def test_stale_native_payload_blocks_before_budget(self):
        self.contract['provider_request']['text'] = 'unapproved replacement'
        self.save_contract()
        with self.assertRaisesRegex(ValueError, '原生请求提示词'):
            jobs.prepare(self.project, self.contract_path)
        self.assertFalse((self.project/'state/ledger.jsonl').exists())

    def test_missing_uploaded_reference_in_native_payload_blocks(self):
        self.contract['provider_request_fields']['references'] = []
        self.save_contract()
        with self.assertRaisesRegex(ValueError, '参考资产'):
            jobs.prepare(self.project, self.contract_path)

    def test_integer_generation_duration_can_cover_fractional_edit_without_retiming(self):
        self.contract.update(duration_sec=5.4, generation_duration_sec=6,
                             generation_trim_tail_sec=.6, generation_duration_reason='测试模型只接受整数秒')
        self.contract['backend_capabilities']['max_duration_sec'] = 10
        self.contract['backend_capabilities'].update(min_duration_sec=1, duration_quantum_sec=1)
        self.contract['provider_request']['seconds'] = 6
        self.save_contract()
        job = jobs.prepare(self.project, self.contract_path)
        self.assertEqual(job['duration_sec'], 5.4)
        self.assertEqual(job['generation_duration_sec'], '6')
        self.assertEqual(self.ledger()[0]['duration_sec'], '6')
        self.assertEqual(self.ledger()[0]['planned_edit_duration_sec'], 5.4)

    def test_nonintegral_minimum_is_rounded_to_valid_quantum(self):
        self.contract.update(duration_sec=2, generation_duration_sec=4,
                             generation_trim_tail_sec=2, generation_duration_reason='满足模型最小时长及步长')
        self.contract['backend_capabilities'].update(min_duration_sec=3, max_duration_sec=10, duration_quantum_sec=2)
        self.contract['provider_request']['seconds'] = 4
        self.assertEqual(jobs.request_errors(self.contract), [])
        self.contract.update(generation_duration_sec=3, generation_trim_tail_sec=1)
        self.contract['provider_request']['seconds'] = 3
        self.assertTrue(jobs.request_errors(self.contract))

    def test_extra_generation_seconds_without_explicit_tail_are_blocked(self):
        self.contract.update(duration_sec=5.4, generation_duration_sec=6)
        self.contract['provider_request']['seconds'] = 6
        self.contract['backend_capabilities']['max_duration_sec'] = 10
        self.contract['backend_capabilities'].update(min_duration_sec=1, duration_quantum_sec=1)
        self.save_contract()
        with self.assertRaisesRegex(ValueError, '额外裁尾'):
            jobs.prepare(self.project, self.contract_path)

    def test_wrong_upload_locator_is_rejected(self):
        with self.assertRaises(ValueError):
            jobs.record_upload(self.project, self.project/'input/source.bin', locator='invented',
                               locator_pointer='/url', raw_response={'url': 'actual-fixture'})

    def test_hosted_reuse_requires_matching_download_and_current_http_evidence(self):
        original = self.project/'input/source.bin'
        downloaded, http_path = self.project/'download.bin', self.project/'http.json'
        downloaded.write_bytes(original.read_bytes())
        url = 'https://fixture.invalid/hosted'
        http_path.write_text(json.dumps({'url': url, 'method': 'GET', 'status_code': 200,
                                        'downloaded_path': str(downloaded), 'downloaded_sha256': digest(downloaded)}), encoding='utf8')
        path = jobs.verify_hosted(self.project, original, downloaded_path=downloaded, http_evidence_path=http_path)
        receipt = json.loads(path.read_text(encoding='utf8'))
        self.assertEqual(receipt['status'], 'hosted_verified')
        binding = {'source': {'url': url, 'sha256': digest(original)},
                   'upload_receipt_path': str(path)}
        self.assertEqual(jobs.upload_errors(self.project, {'reference_bindings': [binding]})[0], [])
        downloaded.write_bytes(b'changed remote copy')
        self.assertTrue(jobs.upload_errors(self.project, {'reference_bindings': [binding]})[0])

    def test_changed_local_source_after_prepare_blocks_adapter(self):
        job = jobs.prepare(self.project, self.contract_path)
        (self.project/'input/source.bin').write_bytes(b'changed source')
        with self.assertRaises(ValueError):
            jobs.dispatch(self.project, job['job_id'], lambda _: self.fail('不得调用供应商'))

    def test_poll_reuses_original_provider_task_id(self):
        job = jobs.prepare(self.project, self.contract_path)
        jobs.dispatch(self.project, job['job_id'], lambda _: response())
        seen = []
        def lookup(task_id):
            seen.append(task_id)
            return response('completed', task_id)
        result = jobs.poll(self.project, job['job_id'], lookup)
        self.assertEqual(seen, ['provider-fixture-task'])
        self.assertEqual(result['status'], 'completed')
        with self.assertRaises(ValueError): jobs.poll(self.project, job['job_id'], lookup)

    def test_poll_mismatched_task_stays_unknown_and_reserved(self):
        job = jobs.prepare(self.project, self.contract_path)
        jobs.dispatch(self.project, job['job_id'], lambda _: response())
        result = jobs.poll(self.project, job['job_id'], lambda _: response('completed', 'other-task'))
        self.assertEqual(result['status'], 'unknown')
        self.assertEqual(self.ledger()[-1]['action'], 'reserve')

    def test_settlement_needs_actual_task_bound_cost_and_is_idempotent(self):
        job = jobs.prepare(self.project, self.contract_path)
        jobs.dispatch(self.project, job['job_id'], lambda _: response('completed'))
        args = dict(cash_cny='1', coins='3', raw_cost_response={'id': 'provider-fixture-task', 'cash': 1, 'coins': 3},
                    cash_pointer='/cash', coins_pointer='/coins', task_pointer='/id')
        result = jobs.settle(self.project, job['job_id'], **args)
        self.assertEqual(result['cost_status'], 'settled')
        jobs.settle(self.project, job['job_id'], **args)
        self.assertEqual([r['action'] for r in self.ledger()], ['reserve', 'settle'])

    def test_other_task_bill_cannot_settle(self):
        job = jobs.prepare(self.project, self.contract_path)
        jobs.dispatch(self.project, job['job_id'], lambda _: response('completed'))
        with self.assertRaises(ValueError):
            jobs.settle(self.project, job['job_id'], cash_cny='1', coins='0',
                        raw_cost_response={'id': 'different-task', 'cash': 1, 'coins': 0},
                        cash_pointer='/cash', coins_pointer='/coins', task_pointer='/id')
        self.assertEqual(self.ledger()[-1]['action'], 'reserve')

    def test_inflight_actual_charge_does_not_claim_video_complete_or_lose_cost_on_poll(self):
        job = jobs.prepare(self.project, self.contract_path)
        jobs.dispatch(self.project, job['job_id'], lambda _: response('pending'))
        result = jobs.settle(self.project, job['job_id'], cash_cny='1', coins='0',
                             raw_cost_response={'id': 'provider-fixture-task', 'cash': 1, 'coins': 0},
                             cash_pointer='/cash', coins_pointer='/coins', task_pointer='/id')
        self.assertEqual(result['status'], 'pending')
        self.assertEqual(result['cost_status'], 'settled')
        result = jobs.poll(self.project, job['job_id'], lambda _: response('completed'))
        self.assertEqual(result['cost_status'], 'settled')

    def test_actual_cost_can_close_after_review_revocation_but_new_batch_is_blocked(self):
        ledger = self.project/'state/ledger.jsonl'
        base = {'task_id': 'test-task', 'cash_cny': '2', 'coins': '0', 'duration_sec': '5',
                'shot_id': 'CH01', 'backend': 'fixture'}
        ledger_record(ledger, {**base, 'event_id': 'reserve-event', 'action': 'reserve'}, '5')
        # 现有任务实际收费，审批已无效时也必须如实记账；新预占仍不得通过。
        ledger_record(ledger, {**base, 'event_id': 'settle-event', 'action': 'settle',
                               'submission_phase': 'batch', 'project_dir': str(self.project)}, '5')
        with self.assertRaises(ValueError):
            ledger_record(ledger, {**base, 'task_id': 'new-task', 'event_id': 'new-reserve',
                                   'action': 'reserve', 'submission_phase': 'batch',
                                   'project_dir': str(self.project)}, '5')

    def test_concurrency_and_budget_prevent_extra_preparation(self):
        jobs.prepare(self.project, self.contract_path)
        jobs.prepare(self.project, self.contract_path)
        with self.assertRaisesRegex(ValueError, '并发上限'):
            jobs.prepare(self.project, self.contract_path)

    def test_over_budget_actual_cost_is_preserved_and_blocks_new_jobs(self):
        job = jobs.prepare(self.project, self.contract_path)
        jobs.dispatch(self.project, job['job_id'], lambda _: response('completed'))
        result = jobs.settle(self.project, job['job_id'], cash_cny='9', coins='0',
                             raw_cost_response={'id': 'provider-fixture-task', 'cash': 9, 'coins': 0},
                             cash_pointer='/cash', coins_pointer='/coins', task_pointer='/id')
        self.assertEqual(result['cost_status'], 'reconciliation_required')
        self.assertEqual(result['actual_cost']['cash_cny'], '9')
        with self.assertRaisesRegex(ValueError, '实际费用差异'):
            jobs.prepare(self.project, self.contract_path)


if __name__ == '__main__':
    unittest.main()
