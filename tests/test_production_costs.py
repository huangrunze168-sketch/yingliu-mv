"""本地实付字段/台账核验，不查询后端，不把测试费用当真实账单。"""
import json
import unittest

import test_submission_jobs as fixtures
from scripts import submission_jobs as jobs, production_costs as costs
from scripts.ledger import record


class ProductionCostTests(unittest.TestCase):
    def setUp(self): fixtures.SubmissionJobTests.setUp(self)

    def completed(self):
        job = jobs.prepare(self.project, self.contract_path)
        return jobs.dispatch(self.project, job['job_id'], lambda body: fixtures.response('completed'))

    def settled(self):
        job = self.completed()
        return jobs.settle(self.project, job['job_id'], cash_cny='1', coins='2',
            raw_cost_response={'id': job['provider_task_id'], 'cash': '1', 'coins': '2', 'scope': 'isolated_fixture'},
            cash_pointer='/cash', coins_pointer='/coins', task_pointer='/id')

    def test_empty_registry_does_not_claim_historical_costs_verified(self):
        result, _ = costs.audit(self.project)
        self.assertEqual(result['status'], 'no_registered_provider_jobs')
        self.assertFalse(result['historical_unregistered_costs_verified'])

    def test_estimate_or_completion_cannot_replace_real_cost_fields(self):
        self.completed()
        with self.assertRaisesRegex(ValueError, '费用仍未核对'): costs.audit(self.project)

    def test_settled_record_matches_raw_and_ledger_without_backend(self):
        job = self.settled()
        result, versions = costs.audit(self.project)
        self.assertEqual(result['recorded_net_cash_cny'], '1')
        self.assertEqual(result['recorded_net_coins'], '2')
        self.assertIn(jobs.job_path(self.project, job['job_id']), versions)
        self.assertIn(self.project / 'state/ledger.jsonl', versions)

    def test_changed_raw_fee_cannot_keep_settled_qualification(self):
        job = self.settled()
        job['actual_cost']['raw_response']['cash'] = '0'
        jobs.job_path(self.project, job['job_id']).write_text(json.dumps(job), encoding='utf8')
        with self.assertRaises(ValueError): costs.audit(self.project)

    def test_cancel_only_unsubmitted_reservation_and_can_run_when_backend_disabled(self):
        job = jobs.prepare(self.project, self.contract_path)
        policy = self.project / 'state/execution_policy.json'
        policy.write_text(json.dumps({'project_id': str(self.project), 'video_backend_access': False,
                                      'instruction': '测试禁止实际后端'}), encoding='utf8')
        released = jobs.cancel_prepared(self.project, job['job_id'])
        self.assertEqual(released['cost_status'], 'released_unspent')
        self.assertEqual(jobs.cancel_prepared(self.project, job['job_id'])['status'], 'cancelled_before_dispatch')
        result, _ = costs.audit(self.project)
        self.assertEqual(result['jobs'][0]['scope'], 'local_reservation_release_not_provider_refund')
        with self.assertRaises(ValueError): jobs.dispatch(self.project, job['job_id'], lambda body: fixtures.response())

    def test_inflight_provider_task_cannot_be_cancelled_as_unspent(self):
        job = jobs.prepare(self.project, self.contract_path)
        job = jobs.dispatch(self.project, job['job_id'], lambda body: fixtures.response())
        with self.assertRaises(ValueError): jobs.cancel_prepared(self.project, job['job_id'])
        self.assertEqual(jobs.load(jobs.job_path(self.project, job['job_id']))['status'], 'pending')

    def test_refund_without_raw_evidence_cannot_reduce_cost(self):
        job = self.settled()
        entry = {'event_id': 'fixture-refund', 'task_id': job['job_id'], 'action': 'fail_refund',
                 'refund_of': job['job_id'], 'cash_cny': '-0.5', 'coins': '0', 'duration_sec': 0,
                 'provider_task_id': job['provider_task_id']}
        record(self.project / 'state/ledger.jsonl', entry, job['cash_limit_cny'])
        with self.assertRaises(ValueError): costs.audit(self.project)

    def test_unpublished_reservation_release_is_not_orphan_provider_charge(self):
        job = jobs.prepare(self.project, self.contract_path)
        jobs.cancel_prepared(self.project, job['job_id'])
        jobs.job_path(self.project, job['job_id']).unlink()
        result, _ = costs.audit(self.project)
        self.assertEqual(result['jobs'][0]['status'], 'unpublished_preparation_released')

    def test_lost_paid_job_cannot_be_ignored(self):
        job = self.settled()
        jobs.job_path(self.project, job['job_id']).unlink()
        with self.assertRaisesRegex(ValueError, '未关联作业'): costs.audit(self.project)

    def test_coin_only_refund_needs_evidence_and_cannot_exceed_recorded_spend(self):
        job = self.settled()
        def refund(eid, amount):
            return {'event_id': eid, 'task_id': job['job_id'], 'action': 'fail_refund',
                    'refund_of': job['job_id'], 'cash_cny': '0', 'coins': amount, 'duration_sec': 0,
                    'provider_task_id': job['provider_task_id'],
                    'refund_evidence': {'raw_response': {'id': job['provider_task_id'], 'cash': '0', 'coins': amount,
                                                        'scope': 'isolated_refund_fixture'},
                                        'task_pointer': '/id', 'cash_pointer': '/cash', 'coins_pointer': '/coins'}}
        ledger = self.project / 'state/ledger.jsonl'
        with self.assertRaises(ValueError): record(ledger, refund('too-much', '-3'), job['cash_limit_cny'])
        record(ledger, refund('coin-refund', '-1'), job['cash_limit_cny'])
        result, _ = costs.audit(self.project)
        self.assertEqual(result['recorded_net_cash_cny'], '1')
        self.assertEqual(result['recorded_net_coins'], '1')

    def test_coin_charge_requires_reservation_even_when_cash_is_zero(self):
        event = {'event_id': 'unreserved', 'task_id': 'unreserved', 'action': 'settle',
                 'cash_cny': '0', 'coins': '1', 'duration_sec': 0}
        with self.assertRaises(ValueError): record(self.project / 'unreserved.jsonl', event, '5')
