"""禁止后端时在预占/派发/回查之前阻断；不阻止本地证据读取和旧费用记录。"""
import json
import unittest
from unittest.mock import patch

import test_submission_jobs as fixtures
from scripts import submission_jobs as jobs, mcp_broker as broker
from scripts.execution_policy import require_video_backend


class ExecutionPolicyTests(unittest.TestCase):
    def setUp(self):
        fixtures.SubmissionJobTests.setUp(self)
        self.policy = self.project / 'state/execution_policy.json'

    def disable(self):
        self.policy.write_text(json.dumps({'project_id': str(self.project), 'video_backend_access': False,
                                          'instruction': '用户禁止使用生成视频后端'}), encoding='utf8')

    def test_default_closed_and_non_boolean_allowance_are_rejected(self):
        self.policy.unlink()
        with self.assertRaises(ValueError): require_video_backend(self.project)
        self.policy.write_text(json.dumps({'project_id': str(self.project), 'video_backend_access': 'true',
                                          'instruction': '测试不可信字符串开关'}), encoding='utf8')
        with self.assertRaises(ValueError): require_video_backend(self.project)

    def test_disabled_preparation_cannot_reserve_money(self):
        self.disable()
        with self.assertRaisesRegex(ValueError, '禁止'): jobs.prepare(self.project, self.contract_path)
        self.assertFalse((self.project / 'state/ledger.jsonl').exists())

    def test_disable_after_prepare_prevents_callback_and_retains_prepared_fact(self):
        job = jobs.prepare(self.project, self.contract_path)
        self.disable()
        calls = []
        with self.assertRaises(ValueError): jobs.dispatch(self.project, job['job_id'], lambda body: calls.append(body))
        self.assertEqual(calls, [])
        self.assertEqual(jobs.load(jobs.job_path(self.project, job['job_id']))['status'], 'prepared')

    def test_disabled_query_cannot_invoke_lookup_or_mutate_provider_status(self):
        job = jobs.prepare(self.project, self.contract_path)
        job = jobs.dispatch(self.project, job['job_id'], lambda body: fixtures.response())
        self.disable()
        calls = []
        with self.assertRaises(ValueError): jobs.poll(self.project, job['job_id'], lambda task: calls.append(task))
        self.assertEqual(calls, [])
        self.assertEqual(jobs.load(jobs.job_path(self.project, job['job_id']))['status'], 'pending')
        # 允许离线登记既有真实费用；测试费用是明确合成夹具，不触发后端。
        settled = jobs.settle(self.project, job['job_id'], cash_cny='1', coins='0',
                  raw_cost_response={'id': job['provider_task_id'], 'cash': '1', 'coins': '0'},
                  cash_pointer='/cash', coins_pointer='/coins', task_pointer='/id')
        self.assertEqual(settled['cost_status'], 'settled')

    def test_disabled_backend_also_blocks_readonly_broker_before_handoff(self):
        self.disable()
        with self.assertRaises(ValueError):
            broker.exchange(self.project, 'mcp__lovart__describe_model', {'model': 'minimax/minimax-h3'}, mode='describe')
        self.assertFalse((self.project / 'state/mcp_calls').exists())
