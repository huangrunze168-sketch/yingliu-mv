"""实际进程执行、失败撤销、版本变化与日志替换反例。"""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import wave

from scripts.workflow_execution import execute, receipt_errors, run_required, RUN_ORDER
from evidence_fixtures import write_timing_fixture


class WorkflowExecutionTests(unittest.TestCase):
    def test_required_run_stops_at_first_real_failure(self):
        self.timeline['production_timing_verified'] = False
        self.save()
        result = run_required(self.project)
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['failed_step'], 'gate1')
        self.assertEqual(result['not_run'], list(RUN_ORDER[1:]))
        self.assertEqual(len(result['records']), 1)
        self.assertFalse(result['all_steps_completed'])
        self.assertFalse((self.project/'state/executions/gate5.json').exists())

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name).resolve()
        (self.project/'input').mkdir()
        (self.project/'state').mkdir()
        audio = self.project/'input/song.wav'
        with wave.open(str(audio), 'wb') as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(8000)
            handle.writeframes(b'\0\0'*16000)
        (self.project/'input/lyrics.txt').write_text('隔离测试歌词', encoding='utf8')
        self.timeline = {'duration_sec': 2, 'source_audio': 'input/song.wav',
                         'master_audio': 'input/song.wav', 'asr_engine': 'isolated-test-fixture',
                         'production_timing_verified': True, 'pending_review': [], 'no_interlude': True,
                         'lines': [{'index': 1, 'type': 'vocal', 'text': '隔离测试歌词',
                                    'start': 0, 'end': 2, 'confidence': 1}]}
        self.save()

    def save(self):
        (self.project/'state/timeline.json').write_text(json.dumps(self.timeline, ensure_ascii=False), encoding='utf8')
        write_timing_fixture(self.project, self.timeline)

    def test_actual_process_receipt_passes_then_input_change_blocks(self):
        record = execute(self.project, 'gate1')
        self.assertEqual(record['status'], 'succeeded', record)
        self.assertEqual(receipt_errors(self.project, ('gate1',)), [])
        self.timeline['asr_engine'] = 'changed-fixture'
        self.save()
        self.assertTrue(any('依据已变化' in e for e in receipt_errors(self.project, ('gate1',))))

    def test_failed_rerun_replaces_previous_success_and_keeps_history(self):
        self.assertEqual(execute(self.project, 'gate1')['status'], 'succeeded')
        (self.project/'input/lyrics.txt').write_text('不匹配的歌词', encoding='utf8')
        record = execute(self.project, 'gate1')
        self.assertEqual(record['status'], 'failed')
        self.assertTrue(receipt_errors(self.project, ('gate1',)))
        archived = list((self.project/'state/executions/history').glob('gate1-*.json'))
        self.assertEqual(len(archived), 1)
        self.assertEqual(json.loads(archived[0].read_text(encoding='utf8'))['status'], 'succeeded')

    def test_provisional_gate1_default_refuses_production(self):
        self.timeline['production_timing_verified'] = False
        self.save()
        record = execute(self.project, 'gate1')
        self.assertEqual(record['returncode'], 1)
        self.assertEqual(record['status'], 'failed')
        self.assertTrue(any('临时时间' in e for e in record['errors']))

    def test_diagnostic_exit_zero_is_explicit_and_not_production(self):
        self.timeline['production_timing_verified'] = False
        self.save()
        import sys
        from scripts.workflow_execution import SCRIPTS
        result = subprocess.run([sys.executable, str(SCRIPTS/'gate_checks.py'), 'gate1',
                                 str(self.project), '--diagnostic'], capture_output=True, text=True, encoding='utf8')
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertTrue(report['diagnostic_only'])
        self.assertFalse(report['production_allowed'])
        self.assertTrue(receipt_errors(self.project, ('gate1',)))

    def test_process_log_replacement_blocks_receipt(self):
        record = execute(self.project, 'gate1')
        Path(record['log_path']).write_text('{}', encoding='utf8')
        self.assertTrue(any('日志已变化' in e for e in receipt_errors(self.project, ('gate1',))))

    def test_report_replacement_blocks_receipt(self):
        execute(self.project, 'gate1')
        (self.project/'gates/gate1.json').write_text('{"summary":{"pass":true}}', encoding='utf8')
        self.assertTrue(any('报告被替换' in e for e in receipt_errors(self.project, ('gate1',))))

    def test_input_change_during_real_process_blocks_success(self):
        real = subprocess.run
        def changed(*args, **kwargs):
            result = real(*args, **kwargs)
            self.timeline['asr_engine'] = 'changed-during-process'
            self.save()
            return result
        with patch('scripts.workflow_execution.subprocess.run', side_effect=changed):
            record = execute(self.project, 'gate1')
        self.assertEqual(record['status'], 'failed')
        self.assertTrue(any('执行期间' in e for e in record['errors']))

    def test_corrupt_report_after_process_preserves_actual_output(self):
        real = subprocess.run
        def corrupt(*args, **kwargs):
            result = real(*args, **kwargs)
            (self.project/'gates/gate1.json').write_text('{broken', encoding='utf8')
            return result
        with patch('scripts.workflow_execution.subprocess.run', side_effect=corrupt):
            record = execute(self.project, 'gate1')
        self.assertEqual(record['status'], 'failed')
        self.assertTrue(record['process_completed'])
        self.assertEqual(record['returncode'], 0)
        output = json.loads(Path(record['log_path']).read_text(encoding='utf8'))
        self.assertIn('production_allowed', output['stdout'])
        self.assertTrue(receipt_errors(self.project, ('gate1',)))
        self.assertFalse((self.project/'.workflow-execution.lock').exists())

    def test_arbitrary_command_is_rejected_without_receipt(self):
        with self.assertRaises(ValueError):
            execute(self.project, 'echo pass')
        self.assertFalse((self.project/'state/executions').exists())

    def test_old_pass_report_without_execution_receipt_is_blocked(self):
        (self.project/'gates').mkdir()
        (self.project/'gates/gate1.json').write_text('{"summary":{"pass":true}}', encoding='utf8')
        self.assertTrue(receipt_errors(self.project, ('gate1',)))

    def test_crew_commands_need_actual_execution_and_current_reports(self):
        from test_visual_workflow import fixture
        from scripts.storyboard_build import build
        fixture(self.project)
        build(self.project)
        for step in ('crew_pipeline', 'crew_rehearsal'):
            record = execute(self.project, step)
            self.assertEqual(record['status'], 'succeeded', record.get('errors'))
            self.assertEqual(receipt_errors(self.project, (step,)), [])
            report = self.project/'gates'/f'{step}.json'
            report.write_text('{"status":"structural_pass"}', encoding='utf8')
            self.assertTrue(any('报告被替换' in e for e in receipt_errors(self.project, (step,))))


if __name__ == '__main__':
    unittest.main()
