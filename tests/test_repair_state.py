"""返修状态机使用真实本地视频；批准查询隔离，完整批准链另有端到端测试。"""
import copy
from unittest.mock import patch
import unittest

import test_media_origin as media_fixtures
from test_production_contracts import ffmpeg
from scripts import repair_state as repair, media_origin as origin
from scripts.pipeline_contracts import digest

write = media_fixtures.write


class RepairStateTests(unittest.TestCase):
    def setUp(self):
        media_fixtures.MediaOriginTests.setUp(self)
        media_fixtures.MediaOriginTests.register(self)
        self.registry = self.project / 'state/generated_clips.json'
        write(self.registry, {'clips': {'OLD': self.clip}})
        write(self.project / 'state/chapter_plan.json', {'production_policy': {'prototype_chapter': 'CH1'},
                                                       'chapters': [{'id': 'CH1', 'shot_ids': ['SH1']}]})
        self.plan_path = self.project / 'repair-plan.json'
        self.failure_path = self.project / 'failure-review.json'
        self.failure = {'decision': 'reject', 'reviewed_media': 'clip.mp4',
                        'reviewed_media_sha256': self.clip['sha256'], 'watched_full_duration': True,
                        'timecoded_findings': [{'start_sec': 0, 'end_sec': .2,
                                               'dimension': 'camera_witness', 'observation': '测试：机位没有变化'}]}
        self.plan = {'dominant_failures': [{'classification': 'actual_drift', 'dimension': 'camera_witness',
                                          'evidence': '测试：机位没有变化', 'cause': '测试：动作提示未进入视频请求'}],
                     'structural_failure': False, 'action': 'surgical_repair',
                     'change_only': ['摄影'], 'preserve_exactly': ['人物与服装']}
        write(self.failure_path, self.failure)
        write(self.plan_path, self.plan)
        for path in ('state/director_review.json', 'state/reviews/CH1.json', 'state/reviews/full_sequence.json'):
            write(self.project / path, {'decision': 'approve', 'watched_full_duration': True})

    def open(self, clip_id='OLD'):
        return repair.open_repair(self.project, clip_id=clip_id, plan_path=self.plan_path,
                                  failure_review_path=self.failure_path)

    def replacement(self, record, *, suffix='b', repair_id=None):
        source = self.project / f'new-{suffix}.mp4'
        ffmpeg('-f', 'lavfi', '-i', f'color=white:s=160x90:r=24:d=0.{25 if suffix == "b" else 5}',
               '-c:v', 'libx264', source)
        contract_path = self.project / f'new-contract-{suffix}.json'
        write(contract_path, {'edit_shots': [{'id': 'SH1'}],
                              'repair_id': repair_id or record['repair_id'], 'repair_plan_sha256': record['plan_sha256']})
        job = copy.deepcopy(self.job)
        job.update(job_id=suffix*32, provider_task_id=f'new-task-{suffix}', contract_path=str(contract_path),
                   contract_sha256=digest(contract_path))
        job['response']['task_id'] = job['response']['raw_response']['id'] = job['provider_task_id']
        write(origin.job_path(self.project, job['job_id']), job)
        http = self.project / f'http-{suffix}.json'
        write(http, {**self.http, 'downloaded_path': str(source), 'downloaded_sha256': digest(source)})
        receipt = origin.register_download(self.project, job['job_id'], downloaded_path=source,
                                           http_evidence_path=http, output_pointer='/url')
        clip = {**self.clip, 'path': source.name, 'sha256': digest(source),
                'origin_receipt_path': str(receipt.relative_to(self.project))}
        registry = repair.load(self.registry)
        registry['clips'][f'NEW-{suffix}'] = clip
        write(self.registry, registry)
        review = {'decision': 'approve', 'reviewed_media': source.name,
                  'reviewed_media_sha256': clip['sha256'],
                  'timecoded_findings': [{'dimension': 'camera_witness', 'observation': '测试：新镜头已核对',
                                         'start_sec': 0, 'end_sec': .2}]}
        write(self.project / 'state/reviews/CH1.json', review)
        return f'NEW-{suffix}', clip

    def test_open_freezes_evidence_and_revokes_all_related_approvals(self):
        record = self.open()
        self.assertEqual(record['status'], 'open')
        self.assertFalse(record['artistic_approval'])
        for path in ('state/director_review.json', 'state/reviews/CH1.json', 'state/reviews/full_sequence.json'):
            value = repair.load(self.project / path)
            self.assertEqual(value['decision'], 'pending')
            self.assertFalse(value['watched_full_duration'])
        self.assertEqual(len(list((self.project / 'state/reviews/history').glob('*.json'))), 2)
        write(self.plan_path, {'changed': True})
        self.assertEqual(repair.load(repair.path_for(self.project, record['repair_id']))['plan'], self.plan)
        with self.assertRaises(ValueError): repair.require_closed(self.project)

    def test_duplicate_open_cannot_hide_unresolved_repair(self):
        self.open()
        with self.assertRaises(ValueError): self.open()
        self.assertEqual(len(repair.records(self.project)), 1)

    def test_false_or_wrong_media_failure_review_is_rejected_without_revocation(self):
        for key, value in [('watched_full_duration', False), ('reviewed_media_sha256', 'wrong')]:
            changed = {**self.failure, key: value}
            write(self.failure_path, changed)
            with self.assertRaises(ValueError): self.open()
            self.assertEqual(repair.load(self.project / 'state/reviews/CH1.json')['decision'], 'approve')
        self.assertFalse(repair.records(self.project))

    def test_out_of_range_or_unmatched_observation_is_rejected(self):
        self.failure['timecoded_findings'][0]['end_sec'] = 99
        write(self.failure_path, self.failure)
        with self.assertRaises(ValueError): self.open()
        self.failure['timecoded_findings'][0]['end_sec'] = .2
        self.failure['timecoded_findings'][0]['observation'] = '不匹配计划的观察'
        write(self.failure_path, self.failure)
        with self.assertRaises(ValueError): self.open()

    def test_open_repair_requires_explicit_submission_plan_binding(self):
        record = self.open()
        contract = {'chapter_id': 'CH1'}
        self.assertTrue(repair.submission_errors(self.project, contract))
        contract.update(repair_id=record['repair_id'], repair_plan_sha256=record['plan_sha256'])
        self.assertEqual(repair.submission_errors(self.project, contract), [])
        contract['repair_plan_sha256'] = 'wrong'
        self.assertTrue(repair.submission_errors(self.project, contract))

    def test_old_media_cannot_close_repair(self):
        record = self.open()
        with self.assertRaises(ValueError): repair.close(self.project, record['repair_id'], replacement_clip_id='OLD')

    def test_new_media_requires_correct_repair_task_and_actual_approval_query(self):
        record = self.open()
        cid, _ = self.replacement(record, repair_id='wrong')
        with self.assertRaisesRegex(ValueError, '冻结计划'):
            repair.close(self.project, record['repair_id'], replacement_clip_id=cid)
        cid, _ = self.replacement(record, suffix='c')
        with patch.object(repair, 'approved', return_value=(False, ['未通过'])):
            with self.assertRaisesRegex(ValueError, '重审'):
                repair.close(self.project, record['repair_id'], replacement_clip_id=cid)

    def test_closed_repair_binds_new_review_and_invalidates_on_change(self):
        record = self.open()
        cid, _ = self.replacement(record)
        with patch.object(repair, 'approved', return_value=(True, [])):
            result = repair.close(self.project, record['repair_id'], replacement_clip_id=cid)
        self.assertEqual(result['status'], 'resolved')
        repair.require_closed(self.project)
        write(self.project / 'state/reviews/CH1.json', {'decision': 'pending'})
        with self.assertRaises(ValueError): repair.require_closed(self.project)

    def test_new_repair_can_supersede_prior_resolved_round_without_destroying_history(self):
        first = self.open()
        cid, clip = self.replacement(first)
        with patch.object(repair, 'approved', return_value=(True, [])):
            repair.close(self.project, first['repair_id'], replacement_clip_id=cid)
        self.failure.update(reviewed_media=clip['path'], reviewed_media_sha256=clip['sha256'])
        write(self.failure_path, self.failure)
        second = self.open(cid)
        cid2, _ = self.replacement(second, suffix='c')
        with patch.object(repair, 'approved', return_value=(True, [])):
            repair.close(self.project, second['repair_id'], replacement_clip_id=cid2)
        repair.require_closed(self.project)
        self.assertEqual(len(repair.records(self.project)), 2)

    def test_no_repair_assessment_requires_current_full_approval_and_is_version_bound(self):
        write(self.project / 'state/edit_selection.json', {'selections': []})
        with self.assertRaises(FileNotFoundError): repair.require_assessed(self.project)
        with patch.object(repair, 'delivery_approved', return_value=(False, ['尚未审片'])):
            with self.assertRaises(ValueError): repair.assess(self.project)
        with patch.object(repair, 'delivery_approved', return_value=(True, [])):
            result = repair.assess(self.project)
            self.assertEqual(result['assessment'], 'no_registered_repairs')
            self.assertFalse(result['artistic_approval'])
            repair.require_assessed(self.project)
            write(self.project / 'state/edit_selection.json', {'selections': ['changed']})
            with self.assertRaises(ValueError): repair.require_assessed(self.project)

    def test_unchanged_failed_prompt_needs_explicit_frozen_retry_reason(self):
        record = self.open()
        path = repair.path_for(self.project, record['repair_id'])
        value = repair.load(path)
        value['failed_prompt'] = 'same prompt'
        write(path, value)
        contract = {'chapter_id': 'CH1', 'repair_id': record['repair_id'],
                    'repair_plan_sha256': record['plan_sha256'], 'prompt': 'same prompt'}
        self.assertTrue(repair.submission_errors(self.project, contract))
        contract['prompt'] = 'updated approved prompt'
        self.assertEqual(repair.submission_errors(self.project, contract), [])

    def test_deleted_failure_media_cannot_be_hidden_by_new_approval(self):
        record = self.open()
        cid, _ = self.replacement(record)
        self.source.unlink()
        with patch.object(repair, 'approved', return_value=(True, [])):
            with self.assertRaises(FileNotFoundError):
                repair.close(self.project, record['repair_id'], replacement_clip_id=cid)
