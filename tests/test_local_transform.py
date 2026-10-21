"""本地返修实际运行ffmpeg；失败观察/批准为明确隔离测试夹具。"""
import unittest
from unittest.mock import patch

import test_repair_state as fixtures
from test_production_contracts import ffmpeg
from scripts import local_transform as transform, media_origin, repair_state
from scripts.pipeline_contracts import digest

write = fixtures.write


class LocalTransformTests(unittest.TestCase):
    def setUp(self):
        fixtures.RepairStateTests.setUp(self)
        has_audio = self._testMethodName in {'test_selected_audio_is_preserved_exactly',
                                            'test_reordered_audio_is_also_selected_in_the_same_order',
                                            'test_aac_source_audio_is_preserved_as_decoded_pcm'}
        if has_audio:
            with_audio = self.project / 'with_audio.mp4'
            ffmpeg('-f', 'lavfi', '-i', 'color=black:s=160x90:r=24:d=0.25',
                   '-f', 'lavfi', '-i', 'sine=frequency=443:sample_rate=48000:duration=0.25',
                   '-ac', 2, '-c:v', 'libx264', '-c:a',
                   'aac' if self._testMethodName == 'test_aac_source_audio_is_preserved_as_decoded_pcm' else 'alac', with_audio)
            self.source.write_bytes(with_audio.read_bytes())
            self.clip['sha256'] = self.http['downloaded_sha256'] = digest(self.source)
            write(self.http_path, self.http)
            fixtures.media_fixtures.MediaOriginTests.register(self)
            self.failure['reviewed_media_sha256'] = self.clip['sha256']
            write(self.failure_path, self.failure)
        self.clip.update(frame_count=6, fps=24, scene_id='fixture')
        if self._testMethodName == 'test_retained_source_keeps_authorization_in_derived_chain':
            auth = self.project / 'authorization.txt'
            auth.write_text('明确测试授权夹具：允许保留并局部重剪', encoding='utf8')
            path = media_origin.register_retained(self.project, source_path=self.source, chapter_id='CH1',
                        shot_ids=['SH1'], authorization_path=auth, authorization_quote='允许保留并局部重剪',
                        observer='test-fixture', purpose='测试保留源本地修复')
            self.clip['origin_receipt_path'] = str(path.relative_to(self.project))
        write(self.registry, {'clips': {'OLD': self.clip}})
        self.plan['permitted_local_operations'] = ['crop', 'scale', 'frame_selection', 'reorder']
        write(self.plan_path, self.plan)
        self.opened = fixtures.RepairStateTests.open(self)
        self.transform_plan = {'source_clip_id': 'OLD', 'replacement_clip_id': 'LOCAL',
                               'repair_id': self.opened['repair_id'], 'repair_plan_sha256': self.opened['plan_sha256'],
                               'frame_ranges': [{'start_frame': 0, 'end_frame': 6}],
                               'crop': {'x': 0, 'y': 0, 'width': 128, 'height': 72},
                               'output_width': 160, 'output_height': 90, 'scale_method': 'lanczos',
                               'audio_policy': 'preserve_selected_source' if has_audio else 'source_has_no_audio',
                               'reason': '测试局部构图修复'}
        self.transform_path = self.project / 'transform-plan.json'
        write(self.transform_path, self.transform_plan)

    def run_transform(self):
        return transform.run(self.project, self.transform_path)

    def test_actual_crop_is_pending_and_has_real_process_and_source_chain(self):
        before = digest(self.source)
        clip = self.run_transform()
        self.assertEqual(digest(self.source), before)
        self.assertEqual(clip['status'], 'pending')
        self.assertEqual(clip['frame_count'], 6)
        record = transform.load(self.project / clip['origin_receipt_path'])
        receipt = transform.load(self.project / record['execution_path'])
        self.assertEqual(record['kind'], 'derived')
        self.assertTrue(record['compiled']['upscaled'])
        self.assertFalse(record['dynamic_verified'])
        self.assertTrue(all(p['process_started'] and p['pid'] > 0 and p['returncode'] == 0 for p in receipt['processes']))
        media_origin.require(self.project, clip)
        self.assertEqual(repair_state.load(self.project / 'state/reviews/CH1.json')['decision'], 'pending')

    def test_noop_and_duplicate_frames_and_bool_ranges_are_rejected(self):
        metadata = transform.probe(self.source)
        invalid = [
            {**self.transform_plan, 'crop': {'x': 0, 'y': 0, 'width': 160, 'height': 90}},
            {**self.transform_plan, 'frame_ranges': [{'start_frame': 0, 'end_frame': 4}, {'start_frame': 2, 'end_frame': 6}]},
            {**self.transform_plan, 'frame_ranges': [{'start_frame': False, 'end_frame': 6}]},
            {**self.transform_plan, 'filter_complex': 'arbitrary'},
            {**self.transform_plan, 'output_width': 158},
        ]
        for plan in invalid:
            with self.assertRaises(ValueError): transform.compile_plan(plan, metadata)

    def test_missing_permission_blocks_before_process(self):
        path = repair_state.path_for(self.project, self.opened['repair_id'])
        record = transform.load(path)
        record['plan']['permitted_local_operations'] = []
        write(path, record)
        with patch.object(transform, 'Popen') as process:
            with self.assertRaises(ValueError): self.run_transform()
            process.assert_not_called()

    def test_start_failure_preserves_original_and_creates_no_admitted_clip(self):
        with patch.object(transform, 'Popen', side_effect=FileNotFoundError('isolated launch failure')):
            with self.assertRaises(FileNotFoundError): self.run_transform()
        self.assertEqual(set(transform.load(self.registry)['clips']), {'OLD'})
        receipts = list((self.project / 'analysis/local_transform_runs').glob('*/execution.json'))
        self.assertEqual(len(receipts), 1)
        receipt = transform.load(receipts[0])
        self.assertEqual(receipt['status'], 'failed')
        self.assertFalse(receipt['processes'][0]['process_started'])

    def test_changed_recipe_or_parent_cannot_use_old_derived_media(self):
        clip = self.run_transform()
        record = transform.load(self.project / clip['origin_receipt_path'])
        plan = self.project / record['plan_path']
        data = plan.read_bytes()
        plan.write_bytes(data + b' ')
        with self.assertRaises(ValueError): media_origin.require(self.project, clip)
        plan.write_bytes(data)
        execution = self.project / record['execution_path']
        receipt_bytes = execution.read_bytes()
        execution.write_bytes(receipt_bytes + b' ')
        with self.assertRaises(ValueError): media_origin.require(self.project, clip)
        execution.write_bytes(receipt_bytes)
        self.source.write_bytes(self.source.read_bytes() + b'changed')
        with self.assertRaises(ValueError): media_origin.require(self.project, clip)

    def test_recursive_self_parent_is_rejected(self):
        clip = self.run_transform()
        path = self.project / clip['origin_receipt_path']
        record = transform.load(path)
        record['parent_clip'] = clip
        write(path, record)
        with self.assertRaisesRegex(ValueError, '循环'): media_origin.require(self.project, clip)

    def test_reordered_ranges_require_explicit_reason(self):
        plan = {**self.transform_plan, 'frame_ranges': [{'start_frame': 3, 'end_frame': 6}, {'start_frame': 0, 'end_frame': 3}]}
        with self.assertRaises(ValueError): transform.compile_plan(plan, transform.probe(self.source))
        plan['temporal_reordering_reason'] = '测试明确的非连续时间重排，仍需重审'
        compiled = transform.compile_plan(plan, transform.probe(self.source))
        self.assertIn('reorder', compiled['operations'])

    def test_new_review_is_required_before_closing_local_repair(self):
        clip = self.run_transform()
        with patch.object(repair_state, 'approved', return_value=(False, ['待审'])):
            with self.assertRaises(ValueError): repair_state.close(self.project, self.opened['repair_id'], replacement_clip_id='LOCAL')
        review = {'reviewed_media': clip['path'], 'reviewed_media_sha256': clip['sha256'],
                  'timecoded_findings': [{'dimension': 'camera_witness', 'observation': '测试局部修复后构图'}]}
        write(self.project / 'state/reviews/CH1.json', review)
        with patch.object(repair_state, 'approved', return_value=(True, [])):
            repair_state.close(self.project, self.opened['repair_id'], replacement_clip_id='LOCAL')
        admitted = transform.load(self.registry)['clips']['LOCAL']
        self.assertEqual(admitted['status'], 'reviewed')
        repair_state.require_closed(self.project)

    def test_selected_audio_is_preserved_exactly(self):
        bad = {**self.transform_plan, 'audio_policy': 'source_has_no_audio'}
        with self.assertRaises(ValueError): transform.compile_plan(bad, transform.probe(self.source))
        self.transform_plan['frame_ranges'] = [{'start_frame': 1, 'end_frame': 5}]
        write(self.transform_path, self.transform_plan)
        clip = self.run_transform()
        self.assertEqual(clip['frame_count'], 4)
        record = transform.load(self.project / clip['origin_receipt_path'])
        receipt = transform.load(self.project / record['execution_path'])
        self.assertEqual(len(receipt['processes']), 4)
        audio = receipt['audio_integrity']
        self.assertEqual(audio['source_selection_pcm'], audio['output_pcm'])
        media_origin.require(self.project, clip)

    def test_reordered_audio_is_also_selected_in_the_same_order(self):
        self.transform_plan.update(frame_ranges=[{'start_frame': 3, 'end_frame': 6}, {'start_frame': 0, 'end_frame': 3}],
                                   temporal_reordering_reason='测试非线性时间，声音跟随同一源帧映射')
        write(self.transform_path, self.transform_plan)
        clip = self.run_transform()
        self.assertEqual(clip['frame_count'], 6)
        media_origin.require(self.project, clip)

    def test_aac_source_audio_is_preserved_as_decoded_pcm(self):
        clip = self.run_transform()
        record = transform.load(self.project / clip['origin_receipt_path'])
        receipt = transform.load(self.project / record['execution_path'])
        self.assertEqual(receipt['audio_integrity']['source_selection_pcm'], receipt['audio_integrity']['output_pcm'])
        media_origin.require(self.project, clip)

    def test_multi_round_derived_chain_keeps_original_source(self):
        first = self.run_transform()
        review_path = self.project / 'state/reviews/CH1.json'
        write(review_path, {'reviewed_media': first['path'], 'reviewed_media_sha256': first['sha256'],
                            'timecoded_findings': [{'dimension': 'camera_witness', 'observation': '测试第一轮重审'}]})
        with patch.object(repair_state, 'approved', return_value=(True, [])):
            repair_state.close(self.project, self.opened['repair_id'], replacement_clip_id='LOCAL')
        self.failure.update(reviewed_media=first['path'], reviewed_media_sha256=first['sha256'])
        write(self.failure_path, self.failure)
        opened = fixtures.RepairStateTests.open(self, clip_id='LOCAL')
        self.transform_plan.update(source_clip_id='LOCAL', replacement_clip_id='LOCAL2',
                                   repair_id=opened['repair_id'], repair_plan_sha256=opened['plan_sha256'],
                                   crop={'x': 0, 'y': 0, 'width': 64, 'height': 36})
        write(self.transform_path, self.transform_plan)
        second = self.run_transform()
        versions = media_origin.require(self.project, second)
        self.assertIn(self.source, versions)
        self.assertTrue((self.project / first['path']).exists())

    def test_retained_source_keeps_authorization_in_derived_chain(self):
        clip = self.run_transform()
        versions = media_origin.require(self.project, clip)
        auth = self.project / 'authorization.txt'
        self.assertIn(auth, versions)
        self.assertNotIn(self.job_path, versions)
        auth.write_text('测试授权已撤销', encoding='utf8')
        with self.assertRaises(ValueError): media_origin.require(self.project, clip)
