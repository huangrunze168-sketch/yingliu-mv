"""独立交付状态的真实媒体及版本失效回归。"""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from test_production_contracts import ffmpeg, ROOT
from scripts import delivery_state as delivery
from scripts.pipeline_contracts import digest
from concurrent.futures import ThreadPoolExecutor


class DeliveryStateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.temporary.cleanup)
        self.project = Path(self.temporary.name)
        (self.project / 'state').mkdir()
        (self.project / 'output').mkdir()
        self.source = self.project / 'source.txt'
        self.source.write_text('审批依赖版本1', encoding='utf-8')
        self.master = self.project / 'song.wav'
        self.output = self.project / 'output/final_mv.mp4'
        ffmpeg('-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=1',
               '-ac', '2', self.master)
        ffmpeg('-f', 'lavfi', '-i', 'color=black:s=160x90:r=24:d=1',
               '-i', self.master, '-c:v', 'libx264', '-c:a', 'alac', self.output)
        self.specification = {'master_audio': str(self.master), 'duration_sec': 1,
                              'fps': 24, 'width': 160, 'height': 90}
        for name, data in [('timeline', {'duration_sec': 1, 'master_audio': str(self.master)}),
                           ('storyboard', {'fps': 24}), ('style', {'aspect_ratio': '16:9'})]:
            (self.project / 'state' / f'{name}.json').write_text(json.dumps(data), encoding='utf-8')
        for target, kwargs in [
            # 本组隔离交付状态机；真实生产前置链由ProductionContracts端到端测试。
            ('require_current', {'return_value': None}),
            ('require_package', {'return_value': {'scope': 'isolated_state_machine_fixture'}}),
            ('dependencies', {'side_effect': lambda project: {str(self.source): digest(self.source)}}),
            ('approved', {'return_value': (True, [])}),
            ('delivery_approved', {'return_value': (True, [])}),
            ('animatic_approved', {'return_value': (True, [])}),
        ]:
            patcher = patch.object(delivery, target, **kwargs)
            patcher.start()
            self.addCleanup(patcher.stop)

    def ready(self):
        record = delivery.begin(self.project, 'final_mv.mp4', self.specification)
        self.assertEqual(record['status'], 'rendering')
        delivery.register_output(self.project, record)
        self.assertEqual(delivery.check(self.project, 'final_mv.mp4')['status'], 'exported')
        self.assertFalse(delivery.check(self.project, 'final_mv.mp4')['ready_for_delivery'])
        delivery.finish(self.project, record)
        return record

    def test_three_stages_not_user_receipt(self):
        record = self.ready()
        self.assertEqual(record['history'], ['rendering', 'exported', 'technical_verified', 'ready'])
        result = delivery.require_ready(self.project)
        self.assertEqual(result['status'], 'ready')
        self.assertFalse(result['user_received'])
        self.assertTrue(result['output_sha256'])
        self.assertTrue(result['verification_sha256'])

    def test_missing_package_preserves_technical_state_without_ready(self):
        record = delivery.begin(self.project, 'final_mv.mp4', self.specification)
        delivery.register_output(self.project, record)
        with patch.object(delivery, 'require_package', side_effect=ValueError('缺少实际交付包')):
            with self.assertRaisesRegex(ValueError, '完整交付包'):
                delivery.finish(self.project, record)
        actual = delivery.read_record(self.project, 'final_mv.mp4')
        self.assertEqual(actual['status'], 'technical_verified')
        self.assertNotIn('ready', actual['history'])

    def test_output_changed_or_deleted_invalidates_ready(self):
        self.ready()
        self.output.write_bytes(b'changed-output')
        self.assertEqual(delivery.check(self.project, 'final_mv.mp4')['status'], 'invalidated')
        with self.assertRaises(ValueError): delivery.require_ready(self.project)
        stored = delivery.load(delivery.record_path(self.project, 'final_mv.mp4'))
        self.assertEqual(stored['status'], 'invalidated')
        self.output.unlink()
        self.assertFalse(delivery.check(self.project, 'final_mv.mp4')['ready_for_delivery'])

    def test_dependency_and_approval_changes_invalidate(self):
        self.ready()
        self.source.write_text('审批依赖版本2', encoding='utf-8')
        self.assertFalse(delivery.check(self.project, 'final_mv.mp4')['ready_for_delivery'])
        self.source.write_text('审批依赖版本1', encoding='utf-8')
        with patch.object(delivery, 'delivery_approved', return_value=(False, ['章节批准已撤销'])):
            self.assertEqual(delivery.check(self.project, 'final_mv.mp4')['status'], 'invalidated')

    def test_report_tampering_invalidates(self):
        self.ready()
        (self.project / 'output/final_mv.verification.json').write_text('{}')
        self.assertEqual(delivery.check(self.project, 'final_mv.mp4')['status'], 'invalidated')

    def test_technical_pass_without_approval_is_not_ready(self):
        record = delivery.begin(self.project, 'final_mv.mp4', self.specification)
        delivery.register_output(self.project, record)
        with patch.object(delivery, 'delivery_approved', return_value=(False, ['尚未批准'])):
            with self.assertRaises(ValueError): delivery.finish(self.project, record)
        self.assertIn('technical_verified', record['history'])
        self.assertNotIn('ready', record['history'])
        self.assertEqual(record['status'], 'technical_verified')
        self.assertFalse(delivery.check(self.project, 'final_mv.mp4')['ready_for_delivery'])

    def test_reexport_cancels_old_ready_and_render_failure_persists(self):
        self.ready()
        record = delivery.begin(self.project, 'final_mv.mp4', self.specification)
        self.assertFalse(delivery.check(self.project, 'final_mv.mp4')['ready_for_delivery'])
        with self.assertRaises(RuntimeError):
            with delivery.rendering_guard(self.project, record): raise RuntimeError('测试渲染中断')
        self.assertEqual(delivery.check(self.project, 'final_mv.mp4')['status'], 'failed')

    def test_failed_media_verification_cannot_be_ready(self):
        ffmpeg('-f', 'lavfi', '-i', 'color=black:s=160x90:r=24:d=2',
               '-i', self.master, '-c:v', 'libx264', '-c:a', 'alac', self.output)
        record = delivery.begin(self.project, 'final_mv.mp4', self.specification)
        delivery.register_output(self.project, record)
        with self.assertRaises(ValueError): delivery.finish(self.project, record)
        self.assertEqual(record['status'], 'failed')
        self.assertNotIn('technical_verified', record['history'])

    def test_missing_delivery_record_and_path_escape_are_blocked(self):
        result = delivery.check(self.project, 'final_mv.mp4')
        self.assertFalse(result['ready_for_delivery'])
        self.assertTrue(result['errors'])
        with self.assertRaises(ValueError):
            delivery.record_path(self.project, '../outside.mp4')

    def test_media_probe_failure_is_failed_not_exported_or_ready(self):
        record = delivery.begin(self.project, 'final_mv.mp4', self.specification)
        self.output.write_bytes(b'corrupt-file')
        delivery.register_output(self.project, record)
        with self.assertRaises(Exception): delivery.finish(self.project, record)
        self.assertEqual(record['status'], 'failed')
        self.assertFalse(delivery.check(self.project, 'final_mv.mp4')['ready_for_delivery'])

    def test_stale_export_cannot_overwrite_newer_failed_state_or_media(self):
        stale = delivery.begin(self.project, 'final_mv.mp4', self.specification)
        newer = delivery.begin(self.project, 'final_mv.mp4', self.specification)
        self.assertNotEqual(stale['export_id'], newer['export_id'])
        delivery.fail(self.project, newer, '新任务失败')
        state_before = digest(delivery.record_path(self.project, 'final_mv.mp4'))
        output_before = digest(self.output)
        staged = self.project / 'output/stale.mp4'
        staged.write_bytes(b'old-task-media')
        with self.assertRaisesRegex(ValueError, '版本已改变'):
            delivery.register_output(self.project, stale, staged)
        self.assertTrue(staged.exists())
        self.assertEqual(digest(self.output), output_before)
        self.assertEqual(digest(delivery.record_path(self.project, 'final_mv.mp4')), state_before)
        with self.assertRaises(ValueError): delivery.finish(self.project, stale)
        with self.assertRaises(ValueError): delivery.fail(self.project, stale, '旧任务失败回调')

    def test_stale_technical_validation_cannot_replace_new_report(self):
        old = delivery.begin(self.project, 'final_mv.mp4', self.specification)
        delivery.register_output(self.project, old)
        newer = delivery.begin(self.project, 'final_mv.mp4', self.specification)
        delivery.register_output(self.project, newer)
        delivery.finish(self.project, newer)
        report = self.project / 'output/final_mv.verification.json'
        before = digest(report)
        with self.assertRaises(ValueError): delivery.finish(self.project, old)
        self.assertEqual(digest(report), before)
        self.assertTrue(delivery.check(self.project, 'final_mv.mp4')['ready_for_delivery'])

    def test_specification_changes_and_forged_spec_digest_are_blocked(self):
        record = self.ready()
        record['specification']['width'] = 320
        record['specification']['height'] = 180
        delivery.save(self.project, record)
        self.assertFalse(delivery.check(self.project, 'final_mv.mp4')['ready_for_delivery'])
        record['specification_sha256'] = delivery.specification_hash(record['specification'])
        delivery.save(self.project, record)
        self.assertFalse(delivery.check(self.project, 'final_mv.mp4')['ready_for_delivery'])

    def test_input_specification_is_copied_and_authority_checked(self):
        record = delivery.begin(self.project, 'final_mv.mp4', self.specification)
        self.specification['width'] = 999
        self.assertEqual(record['specification']['width'], 160)
        for field, value in [('fps', 30), ('duration_sec', 2), ('width', True)]:
            spec = dict(record['specification'])
            spec[field] = value
            with self.assertRaises(ValueError): delivery.begin(self.project, 'final_mv.mp4', spec)

    def test_atomic_revision_compare_allows_only_one_writer(self):
        record = delivery.begin(self.project, 'final_mv.mp4', self.specification)
        import copy
        candidates = [copy.deepcopy(record), copy.deepcopy(record)]
        def update(item):
            item['errors'] = ['并发测试']
            try:
                delivery.save(self.project, item)
                return True
            except (ValueError, RuntimeError): return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sum(pool.map(update, candidates)), 1)

    def test_old_failed_check_cannot_invalidate_new_task(self):
        self.ready()
        entered = [False]
        def racing(project):
            if not entered[0]:
                entered[0] = True
                delivery.begin(project, 'final_mv.mp4', self.specification)
                raise OSError('旧检查失败')
            return {str(self.source): digest(self.source)}
        with patch.object(delivery, 'dependencies', side_effect=racing):
            result = delivery.check(self.project, 'final_mv.mp4', persist=True)
        self.assertEqual(result['status'], 'superseded')
        self.assertFalse(result['ready_for_delivery'])
        self.assertEqual(delivery.read_record(self.project, 'final_mv.mp4')['status'], 'rendering')

    def test_successful_old_check_does_not_report_new_task_as_ready(self):
        self.ready()
        entered = [False]
        def racing(project):
            if not entered[0]:
                entered[0] = True
                delivery.begin(project, 'final_mv.mp4', self.specification)
            return {str(self.source): digest(self.source)}
        with patch.object(delivery, 'dependencies', side_effect=racing):
            result = delivery.check(self.project, 'final_mv.mp4', persist=True)
        self.assertEqual(result['status'], 'superseded')
        self.assertFalse(result['ready_for_delivery'])
        self.assertEqual(delivery.read_record(self.project, 'final_mv.mp4')['status'], 'rendering')

    def test_late_revision_conflict_during_invalidation_preserves_new_state(self):
        self.ready()
        self.source.write_text('导致旧版本失效', encoding='utf-8')
        actual_save = delivery.save
        entered = [False]
        def racing_save(project, record, *args, **kwargs):
            if not entered[0]:
                entered[0] = True
                delivery.begin(project, 'final_mv.mp4', self.specification)
            return actual_save(project, record, *args, **kwargs)
        with patch.object(delivery, 'save', side_effect=racing_save):
            result = delivery.check(self.project, 'final_mv.mp4', persist=True)
        self.assertEqual(result['status'], 'superseded')
        self.assertEqual(delivery.read_record(self.project, 'final_mv.mp4')['status'], 'rendering')

    def test_mismatched_record_does_not_invalidate_another_output(self):
        self.ready()
        other_media = self.project / 'output/other.mp4'
        other_media.write_bytes(self.output.read_bytes())
        other = delivery.begin(self.project, 'other.mp4', self.specification)
        delivery.register_output(self.project, other)
        delivery.finish(self.project, other)
        other_path = delivery.record_path(self.project, 'other.mp4')
        before = digest(other_path)
        delivery.record_path(self.project, 'final_mv.mp4').write_bytes(other_path.read_bytes())
        result = delivery.check(self.project, 'final_mv.mp4', persist=True)
        self.assertFalse(result['ready_for_delivery'])
        self.assertEqual(digest(other_path), before)
        self.assertTrue(delivery.check(self.project, 'other.mp4')['ready_for_delivery'])


if __name__ == '__main__': unittest.main()
