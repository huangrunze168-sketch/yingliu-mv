"""实际缺证据、旧报告、自动补设计及生产入口回归。"""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from test_visual_workflow import fixture, write, PNG_BYTES
from scripts.visual_assets import register as register_pending, approve_static, asset_errors
from scripts import workflow_audit
from scripts.storyboard_build import build
from scripts.sequence_direction import align_to_storyboard
from scripts.crew_rehearsal import current_report_errors
from scripts.pipeline_contracts import digest


class WorkflowAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name).resolve()

    def test_exported_movie_and_self_declared_status_cannot_pass(self):
        (self.project/'final.mp4').write_bytes(b'not valid media')
        write(self.project/'execution_status.json', {'status': 'ready', 'formal_approval': True})
        before = {str(p): p.read_bytes() for p in self.project.rglob('*') if p.is_file()}
        report = workflow_audit.audit(self.project)
        self.assertFalse(report['current_phase_checks_pass'])
        self.assertFalse(report['all_steps_completed'])
        self.assertEqual(len(report['steps']), 12)
        for number in (1, 4, 5, 6):
            self.assertIn('execution_receipts', report['steps'][number-1]['checks'])
            self.assertEqual(report['steps'][number-1]['status'], 'blocked')
        self.assertTrue(any(c['id'] == 'animatic' and c['status'] == 'blocked' for c in report['checks']))
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.project.rglob('*') if p.is_file()})

    def test_registration_never_approves_an_image(self):
        fixture(self.project)
        build(self.project)
        image = self.project/'frame.png'
        image.write_bytes(PNG_BYTES)
        record = register_pending(self.project, 'M1', 'scene_master', 'gate', image, '只登记文件')
        self.assertEqual(record['status'], 'pending')
        self.assertFalse(record['agent_inspected'])
        self.assertTrue(asset_errors(self.project, record, {'M1': record}))
        with self.assertRaisesRegex(ValueError, '版本不匹配'):
            approve_static(self.project, 'M1', observed_sha256='sha256:wrong',
                           observer='test-only', evidence='隔离测试')
        approved = approve_static(self.project, 'M1', observed_sha256=record['sha256'],
                                  observer='test-only', evidence='隔离静帧审查声明')
        self.assertEqual(asset_errors(self.project, approved, {'M1': approved}), [])
        self.assertFalse(approved['dynamic_verified'])
        self.assertFalse(approved['agent_inspected'])

    def test_legacy_approved_string_without_static_review_cannot_pass(self):
        fixture(self.project)
        build(self.project)
        image = self.project/'frame.png'
        image.write_bytes(PNG_BYTES)
        record = register_pending(self.project, 'M1', 'scene_master', 'gate', image, '登记')
        record.update(status='approved', review_evidence='伪造旧通过字符串')
        self.assertTrue(any('独立静帧审查' in e for e in asset_errors(self.project, record, {'M1': record})))

    def test_generation_does_not_require_nonexistent_prototype_but_requires_submission(self):
        report = workflow_audit.audit(self.project, phase='before-generation')
        checks = {c['id']: c for c in report['checks']}
        self.assertEqual(checks['prototype']['status'], 'not_due')
        self.assertEqual(checks['submission']['status'], 'blocked')

    def test_rebuild_failure_preserves_both_previous_outputs(self):
        fixture(self.project)
        build(self.project)
        paths = [self.project/'state'/f'{name}.json' for name in ('sequence_direction', 'storyboard')]
        before = {p: p.read_bytes() for p in paths}
        path = self.project/'state/story_plan.json'
        story = json.loads(path.read_text(encoding='utf8'))
        story['shots'][0]['current_action'] = '未经导演序列批准的新动作'
        write(path, story)
        with self.assertRaisesRegex(ValueError, '禁止自动改写'):
            build(self.project)
        self.assertEqual(before, {p: p.read_bytes() for p in paths})

    def test_new_shot_cannot_get_invented_design_or_first_reference(self):
        fixture(self.project)
        sb = build(self.project)
        plan = json.loads((self.project/'state/sequence_direction.json').read_text(encoding='utf8'))
        sb['shots'].append({**copy.deepcopy(sb['shots'][-1]), 'id': 'NEW'})
        with self.assertRaisesRegex(ValueError, '镜头集合'):
            align_to_storyboard(plan, sb)

    def test_timing_snap_preserves_creative_facts(self):
        fixture(self.project)
        sb = build(self.project)
        plan = json.loads((self.project/'state/sequence_direction.json').read_text(encoding='utf8'))
        old = copy.deepcopy(plan)
        sb['shots'][0]['window']['end'] = 1.5
        sb['shots'][1]['window']['start'] = 1.5
        updated = align_to_storyboard(plan, sb)
        for previous, current in zip(old['shots'], updated['shots']):
            self.assertEqual({k: v for k, v in previous.items() if k != 'window'},
                             {k: v for k, v in current.items() if k != 'window'})
        self.assertEqual(plan, old)

    def test_report_status_without_fingerprints_is_blocked(self):
        write(self.project/'gates/crew_pipeline.json', {'status': 'structural_pass'})
        write(self.project/'gates/crew_rehearsal.json', {'status': 'design_ready_for_rehearsal'})
        self.assertTrue(any('指纹' in e for e in current_report_errors(self.project)))

    def test_report_current_then_changed_reference_is_blocked(self):
        fixture(self.project)
        build(self.project)
        paths = {name: self.project/'state'/f'{name}.json' for name in ('development', 'storyboard')}
        write(self.project/'gates/crew_pipeline.json', {
            'status': 'structural_pass', 'source_fingerprints': {k: digest(p) for k, p in paths.items()}})
        files = list(paths.values()) + [self.project/'state/sequence_direction.json', self.project/'state/sequence_ref.txt']
        write(self.project/'gates/crew_rehearsal.json', {
            'status': 'design_ready_for_rehearsal', 'input_fingerprints': {str(p): digest(p) for p in files}})
        self.assertEqual(current_report_errors(self.project), [])
        (self.project/'state/sequence_ref.txt').write_text('来源被替换', encoding='utf8')
        self.assertTrue(any('输入指纹过期' in e for e in current_report_errors(self.project)))


if __name__ == '__main__':
    unittest.main()
