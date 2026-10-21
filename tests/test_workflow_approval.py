"""十二步批准锁：仅隔离夹具签名，不接触真实项目或私钥。"""
import base64
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from scripts import workflow_run, workflow_approval
from scripts.pipeline_contracts import digest
from scripts.signed_evidence import canonical


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf8')


class WorkflowApprovalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.project = self.root / 'project'
        self.project.mkdir()
        self.key = Ed25519PrivateKey.generate()
        self.trust = self.root / 'external-trust.json'
        public = base64.b64encode(self.key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
        write(self.trust, {'reviewers': {'test-reviewer': {'public_key': public, 'purposes': ['workflow_step']}}})
        self.enterContext(patch.dict(os.environ, {'MV_REVIEW_TRUST_STORE': str(self.trust),
                          'MV_WORKFLOW_TRUST_STORE_SHA256': digest(self.trust)}))
        self.plan = self.project / 'plan.json'
        stages = []
        for step in range(1, 13):
            name = f'state/result{step}.json'
            write(self.project / name, {'step': step})
            stages.append({'step': step, 'inputs': [], 'outputs': [name], 'commands': []})
        write(self.plan, {'project_id': str(self.project), 'steps': stages})
        self.enterContext(patch.object(workflow_run, 'check_step', return_value=[]))

    def run_flow(self):
        return workflow_run.run(self.project, self.plan)

    def sign(self, payload, key=None):
        return {'key_id': 'test-reviewer', 'payload': payload,
                'signature': base64.b64encode((key or self.key).sign(canonical(payload))).decode()}

    def approve(self, step, **overrides):
        payload = workflow_approval.request(self.project, self.plan, step)
        payload.update(decision='approve', observer='测试验收者', evidence='隔离测试，不代表实际听看', **overrides)
        value = self.sign(payload)
        write(workflow_approval.approval_path(self.project, step), value)
        return value

    def test_every_step_waits_and_last_step_requires_approval(self):
        for step in range(1, 13):
            state = self.run_flow()
            self.assertEqual(state['status'], 'waiting_approval')
            self.assertEqual(state['waiting_step'], step)
            self.assertEqual(len(state['steps']), step)
            self.assertEqual(workflow_run.inspect(self.project, self.plan)['status'], 'waiting_approval')
            self.approve(step)
        self.assertEqual(self.run_flow()['status'], 'completed')
        self.assertEqual(workflow_run.inspect(self.project, self.plan)['status'], 'completed')

    def test_repeated_resume_does_not_start_next_command_without_signature(self):
        plan = json.loads(self.plan.read_text(encoding='utf8'))
        plan['steps'][1]['commands'] = [['music_capture.py', '{project}']]
        write(self.plan, plan)
        with patch.object(workflow_run.subprocess, 'run', side_effect=AssertionError('未批准不能执行下一步')):
            for _ in range(3):
                state = self.run_flow()
                self.assertEqual(state['waiting_step'], 1)
                self.assertEqual(len(state['steps']), 1)

    def test_plain_approved_flag_cannot_unlock(self):
        self.run_flow()
        write(workflow_approval.approval_path(self.project, 1), {'decision': 'approve', 'approved': True})
        self.assertEqual(self.run_flow()['waiting_step'], 1)

    def test_attacker_key_cannot_unlock(self):
        self.run_flow()
        payload = workflow_approval.request(self.project, self.plan, 1)
        payload.update(decision='approve', observer='冒名者', evidence='伪造')
        write(workflow_approval.approval_path(self.project, 1), self.sign(payload, Ed25519PrivateKey.generate()))
        self.assertEqual(self.run_flow()['waiting_step'], 1)

    def test_export_future_step_is_denied(self):
        self.run_flow()
        with self.assertRaises(ValueError):
            workflow_approval.request(self.project, self.plan, 2)

    def test_payload_tamper_fails(self):
        self.run_flow()
        value = self.approve(1)
        value['payload']['step'] = 2
        write(workflow_approval.approval_path(self.project, 1), value)
        self.assertEqual(self.run_flow()['waiting_step'], 1)

    def test_missing_trust_pin_is_closed(self):
        self.run_flow()
        self.approve(1)
        with patch.dict(os.environ, {'MV_WORKFLOW_TRUST_STORE_SHA256': ''}):
            self.assertEqual(self.run_flow()['waiting_step'], 1)

    def test_trust_store_replacement_is_closed(self):
        self.run_flow()
        self.approve(1)
        value = json.loads(self.trust.read_text(encoding='utf8'))
        value['extra'] = '替换'
        write(self.trust, value)
        self.assertEqual(self.run_flow()['waiting_step'], 1)

    def test_project_local_trust_store_is_rejected(self):
        self.run_flow()
        self.approve(1)
        local = self.project / 'trust.json'
        local.write_bytes(self.trust.read_bytes())
        with patch.dict(os.environ, {'MV_REVIEW_TRUST_STORE': str(local),
                                   'MV_WORKFLOW_TRUST_STORE_SHA256': digest(local)}):
            self.assertEqual(self.run_flow()['waiting_step'], 1)

    def test_revoked_or_wrong_purpose_key_is_rejected(self):
        self.run_flow()
        self.approve(1)
        original = json.loads(self.trust.read_text(encoding='utf8'))
        for update in ({'revoked': True}, {'purposes': ['lip_measure']}):
            with self.subTest(update=update):
                value = copy.deepcopy(original)
                value['reviewers']['test-reviewer'].update(update)
                write(self.trust, value)
                with patch.dict(os.environ, {'MV_WORKFLOW_TRUST_STORE_SHA256': digest(self.trust)}):
                    self.assertEqual(self.run_flow()['waiting_step'], 1)

    def test_valid_pending_or_empty_observer_does_not_unlock(self):
        self.run_flow()
        payload = workflow_approval.request(self.project, self.plan, 1)
        for decision, observer, evidence in [('pending', '测试', '证据'), ('approve', '', '证据'), ('approve', '测试', '')]:
            payload.update(decision=decision, observer=observer, evidence=evidence)
            write(workflow_approval.approval_path(self.project, 1), self.sign(payload))
            self.assertEqual(self.run_flow()['waiting_step'], 1)

    def test_output_change_requires_new_run_and_new_approval(self):
        self.run_flow()
        self.approve(1)
        self.run_flow()
        write(self.project / 'state/result1.json', {'changed': True})
        state = self.run_flow()
        self.assertEqual(state['waiting_step'], 1)
        self.assertEqual(len(state['steps']), 1)
        self.assertEqual(self.run_flow()['waiting_step'], 1)

    def test_plan_change_rejects_old_signature(self):
        self.run_flow()
        self.approve(1)
        plan = json.loads(self.plan.read_text(encoding='utf8'))
        plan['note'] = '新版本'
        write(self.plan, plan)
        self.assertEqual(self.run_flow()['waiting_step'], 1)
        self.assertEqual(self.run_flow()['waiting_step'], 1)

    def test_execution_record_tamper_is_not_approval(self):
        self.run_flow()
        self.approve(1)
        path = self.project / 'state/workflow_run.json'
        state = json.loads(path.read_text(encoding='utf8'))
        state['steps'][0]['processes'].append({'returncode': 0, 'stdout': '伪造执行'})
        state['status'] = 'completed'
        write(path, state)
        self.assertEqual(self.run_flow()['waiting_step'], 1)

    def test_changed_predecessor_signature_invalidates_downstream(self):
        self.run_flow(); self.approve(1)
        self.run_flow(); self.approve(2)
        first_path = workflow_approval.approval_path(self.project, 1)
        first = json.loads(first_path.read_text(encoding='utf8'))
        first['payload']['evidence'] = '独立复核者重新签发前序批准'
        write(first_path, self.sign(first['payload']))
        self.assertEqual(self.run_flow()['waiting_step'], 2)

    def test_cross_project_replay_is_denied(self):
        state = self.run_flow()
        value = self.approve(1)
        other = self.root / 'other-project'
        path = workflow_approval.approval_path(other, 1)
        write(path, value)
        with self.assertRaises(ValueError):
            workflow_approval.require(other, digest(self.plan), state['steps'][0], None)

    def test_future_unsigned_writer_cannot_cover_changed_approved_output(self):
        self.run_flow(); self.approve(1)
        state_path = self.project / 'state/workflow_run.json'
        state = json.loads(state_path.read_text(encoding='utf8'))
        output = self.project / 'state/result1.json'
        write(output, {'tampered': True})
        state['steps'].append({'step': 12, 'status': 'completed', 'outputs': {str(output): digest(output)}})
        write(state_path, state)
        plan = json.loads(self.plan.read_text(encoding='utf8'))
        # 修改计划会使签名失效，故不修改计划；检查步骤2不得被执行或机检。
        calls = []
        with patch.object(workflow_run, 'check_step', side_effect=lambda project, index, plan: calls.append(index) or []):
            result = self.run_flow()
        self.assertEqual(result['waiting_step'], 1)
        self.assertNotIn(2, calls)

    def test_shared_output_waits_for_owner_signature_without_rerun(self):
        from types import SimpleNamespace
        plan = json.loads(self.plan.read_text(encoding='utf8'))
        plan['steps'][1]['outputs'].append('state/result1.json')
        plan['steps'][1]['commands'] = [['music_capture.py', '{project}']]
        write(self.plan, plan)
        self.run_flow(); self.approve(1)
        def invoke(*args, **kwargs):
            write(self.project / 'state/result1.json', {'changed_by': 2})
            return SimpleNamespace(returncode=0, stdout='隔离命令边界', stderr='')
        with patch.object(workflow_run.subprocess, 'run', side_effect=invoke) as process:
            state = self.run_flow()
            self.assertEqual(state['waiting_step'], 2)
            self.assertEqual(self.run_flow()['waiting_step'], 2)
            self.approve(2)
            self.assertEqual(self.run_flow()['waiting_step'], 3)
            self.assertEqual(process.call_count, 1)

    def test_shared_output_failed_writer_preserves_all_execution_evidence(self):
        from types import SimpleNamespace
        plan = json.loads(self.plan.read_text(encoding='utf8'))
        plan['steps'][1]['outputs'].append('state/result1.json')
        plan['steps'][1]['commands'] = [['music_capture.py', '{project}']]
        write(self.plan, plan)
        self.run_flow(); self.approve(1)
        def invoke(*args, **kwargs):
            write(self.project / 'state/result1.json', {'partial_write': True})
            return SimpleNamespace(returncode=1, stdout='部分写入', stderr='模拟失败')
        with patch.object(workflow_run.subprocess, 'run', side_effect=invoke):
            state = self.run_flow()
        original = copy.deepcopy(state['steps'])
        with patch.object(workflow_run.subprocess, 'run', side_effect=AssertionError('禁止重派任何步骤')):
            result = self.run_flow()
        self.assertEqual(result['status'], 'recovery_required')
        self.assertEqual(result['steps'], original)
        self.assertIn('code', result['steps'][1])
        self.assertTrue(result['steps'][1]['processes'])

    def test_interrupted_command_does_not_become_new_completed_record(self):
        state = self.run_flow()
        path = self.project / 'state/workflow_run.json'
        row = state['steps'][0]
        row['status'] = 'running'
        row['active_command'] = ['模拟未完成命令']
        row['inputs'] = {'old_input': 'old_sha'}
        original = copy.deepcopy(row)
        write(path, state)
        with patch.object(workflow_run.subprocess, 'run', side_effect=AssertionError('中断不能重派')):
            result = self.run_flow()
        self.assertEqual(result['status'], 'recovery_required')
        original['recovery_required'] = True
        self.assertEqual(result['steps'][0], original)
        with self.assertRaises(ValueError):
            workflow_approval.request(self.project, self.plan, 1)

    def test_request_refuses_unfinished_transaction(self):
        self.run_flow()
        write(self.project / '.pipeline-transaction.json', {'test': True})
        with self.assertRaises(ValueError):
            workflow_approval.request(self.project, self.plan, 1)

    def test_cross_step_gate_and_run_required_are_rejected_before_execution(self):
        original = json.loads(self.plan.read_text(encoding='utf8'))
        for command in ([['workflow_execution.py', 'run', '{project}', '--step', 'gate6']],
                        [['workflow_execution.py', 'run-required', '{project}']]):
            with self.subTest(command=command):
                plan = copy.deepcopy(original)
                plan['steps'][0]['commands'] = command
                write(self.plan, plan)
                with patch.object(workflow_run.subprocess, 'run', side_effect=AssertionError('越级命令不得执行')):
                    with self.assertRaises(ValueError):
                        self.run_flow()
        self.assertFalse((self.project / 'state/workflow_run.json').exists())

    def test_design_adoption_cannot_hide_in_timing_or_style_step(self):
        original = json.loads(self.plan.read_text(encoding='utf8'))
        for stage in (0, 1):
            plan = copy.deepcopy(original)
            plan['steps'][stage]['commands'] = [['workbench_adoption.py', 'design', '{project}', 'bundle', 'imported']]
            write(self.plan, plan)
            with self.assertRaises(ValueError):
                self.run_flow()
        self.assertFalse((self.project / 'state/workflow_run.json').exists())

    def test_cli_request_is_pending_and_does_not_advance(self):
        self.run_flow()
        # 实际CLI无测试机检替身，故这里只验证缺有效记录的请求不会写输出。
        out = self.root / 'request.json'
        result = subprocess.run([sys.executable, str(workflow_run.SCRIPTS / 'workflow_approval.py'),
                                 'request', str(self.project), '--plan', str(self.plan),
                                 '--step', '2', '--output', str(out)], capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(out.exists())
        state = json.loads((self.project / 'state/workflow_run.json').read_text(encoding='utf8'))
        self.assertEqual(len(state['steps']), 1)


if __name__ == '__main__':
    unittest.main()
