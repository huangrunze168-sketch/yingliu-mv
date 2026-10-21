"""A06–A36 回归：构造真实本地媒体，测试正常路径与伪造反例，不调用生成后端。"""
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from test_visual_workflow import fixture, write, PNG_BYTES
from scripts.storyboard_build import build
from scripts.chapter_prompt_build import build as compile_chapters
from scripts.crew_pipeline import validate_crew_pipeline, validate_sequence_language
from scripts.gate_checks import check_timeline
from scripts.pipeline_contracts import (digest, timeline_errors, unique_ids, timing_authority,
                                       supported_transitions, atomic_batch, require_no_transaction,
                                       recover_transaction, REFERENCE_INTENTS)
from scripts.omni_reference import validate_omni_reference_blueprint
from scripts.submission_contract import validate_submission_contract
from scripts.director_review_gate import (dependency_snapshot, initial_review, approved,
                                         delivery_approved, DIMENSIONS, SEQUENCE_DIMENSIONS, review_plan)
from test_visual_workflow import register
from scripts.duration_fit import fit
from scripts.audio_master import resolve_master
from scripts.media_verify import verify_delivery, probe
from scripts.ledger import record, money
from scripts.gate_registry import REGISTRY
from scripts.film_grammar_corpus import build_profile, load_jsonl
from scripts.animatic_review import dependencies as animatic_dependencies, approved as animatic_approved, DIMENSIONS as ANIMATIC_DIMENSIONS
from scripts.delivery_state import check as check_delivery

ROOT = Path(__file__).resolve().parents[1]

def _seq_music():
    return {"tempo_class": "medium", "cues": [
        {"id": "downbeat", "time_sec": 0.0, "kind": "downbeat", "evidence": "起拍"},
        {"id": "accent", "time_sec": 2.0, "kind": "accent", "evidence": "重音"},
        {"id": "mid", "time_sec": 4.0, "kind": "accent", "evidence": "中段"},
    ]}, {"duration_sec": 8.0, "lines": []}



def command(script, *args):
    return subprocess.run([sys.executable, str(ROOT / 'scripts' / script), *map(str, args)],
                          capture_output=True, text=True, encoding='utf-8',
                          env={**os.environ, 'PYTHONIOENCODING': 'utf-8'})


def ffmpeg(*args):
    subprocess.run(['ffmpeg', '-y', '-v', 'error', *map(str, args)], check=True,
                   capture_output=True)


class ProductionContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        self.timeline, _, _, self.dev, self.style = fixture(self.project)
        self.sb = build(self.project)

    def generated_origin(self, source, contract_path=None):
        """真实本地媒体与完整提交链；供应商响应/HTTP为明确测试夹具。"""
        from scripts import submission_jobs, media_origin
        job = submission_jobs.prepare(self.project, contract_path or self.project / 'state/job_contract.json')
        raw = {'id': 'isolated-provider-fixture-' + job['job_id'], 'state': 'completed', 'url': 'https://fixture.invalid/video.mp4'}
        submission_jobs.dispatch(self.project, job['job_id'], lambda payload: {
            'task_id': raw['id'], 'status': raw['state'], 'raw_response': raw,
            'response_fields': {'task_id': '/id', 'status': '/state'}})
        submission_jobs.settle(self.project, job['job_id'], cash_cny='1', coins='0',
            raw_cost_response={'id': raw['id'], 'cash': '1', 'coins': '0', 'scope': 'isolated_cost_fixture'},
            cash_pointer='/cash', coins_pointer='/coins', task_pointer='/id')
        http = self.project / 'state' / f"test-download-http-{job['job_id']}.json"
        write(http, {'method': 'GET', 'status_code': 200, 'url': raw['url'],
                     'downloaded_path': str(source), 'downloaded_sha256': digest(source), 'scope': 'test-fixture'})
        receipt = media_origin.register_download(self.project, job['job_id'], downloaded_path=source,
                                                http_evidence_path=http, output_pointer='/url')
        return str(receipt.relative_to(self.project))

    def ready(self, max_duration=4):
        p = self.project
        write(p / 'state/execution_policy.json', {'project_id': str(p.resolve()), 'video_backend_access': True,
                                               'instruction': '仅为本地隔离测试模拟授权；不调用任何实际后端'})
        (p / 'input').mkdir()
        ffmpeg('-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=4',
               '-ac', '2', p / 'input/song.wav')
        self.timeline['master_audio'] = 'input/song.wav'
        self.timeline['master_audio_sha256'] = digest(p / 'input/song.wav')
        self.timeline.update(no_interlude=True, asr_engine='synthetic_fixture_only')
        (p/'input/lyrics.txt').write_text('\n'.join(line['text'] for line in self.timeline['lines']), encoding='utf8')
        write(p / 'state/timeline.json', self.timeline)
        from evidence_fixtures import write_timing_fixture
        write_timing_fixture(p, self.timeline)
        self.sb = build(p)
        (p / 'input/protagonist.png').write_bytes(PNG_BYTES)
        result = command('prompt_build.py', p)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for gate in ('gate5', 'gate6'):
            result = command('gate_checks.py', gate, p)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.sb = json.loads((p / 'state/storyboard.json').read_text(encoding='utf-8'))
        result = command('chapter_plan_build.py', p, '--max-duration', max_duration)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = command('chapter_audio_build.py', p)
        self.assertEqual(result.returncode, 0, result.stderr)
        image = p / 'frame.png'
        image.write_bytes(PNG_BYTES)
        register(p, 'M1', 'scene_master', 'gate', image, '测试静态结构，非真实人工批准')
        for shot in self.sb['shots']:
            register(p, shot['id'], 'keyframe', 'gate', image, '测试静态结构', 'M1')
        compile_chapters(p)
        plan = review_plan(p)
        cid = plan['production_policy']['prototype_chapter']
        chapter = next(c for c in plan['chapters'] if c['id'] == cid)
        package_path = p / f'prompts/chapters/{cid}.json'
        package = json.loads(package_path.read_text(encoding='utf-8'))
        bindings = copy.deepcopy(package['reference_bindings'])
        for index, binding in enumerate(bindings):
            binding['binding_status'] = 'uploaded'
            binding['source']['asset_id'] = f'mock-test-only-{index}'
        self.contract = {
            'project_id': str(p.resolve()), 'chapter_id': cid, 'submission_phase': 'prototype',
            'reference_mode': 'omni_reference', 'director_cut_status': 'approved',
            'source_package_sha256': digest(package_path), 'prompt': package['prompt'],
            'prompt_sha256': package['prompt_sha256'],
            'reference_bindings': bindings, 'duration_sec': chapter['duration_sec'], 'resolution': '480p',
            'estimated_cash_cny': '1', 'cash_limit_cny': '10',
            'backend_capabilities': {'verified': True, 'omni_reference': True,
                                     'multi_shot': True, 'timestamp_control': True,
                                     'reference_limits': {'image': 3, 'audio': 1, 'video': 0},
                                     'max_duration_sec': 4, 'resolutions': ['480p']},
            'edit_shots': [{'id': beat['shot_id'], 'start_sec': beat['start_sec'],
                            'end_sec': beat['end_sec'], 'cut_relation': beat['cut_relation']}
                           for beat in package['beats']],
        }
        self.package = package
        # 先对齐 sequence（可能改写 storyboard），再写门禁指纹与章节计划指纹
        from test_visual_workflow import write_sequence_stub
        from scripts.pipeline_contracts import digest as _digest
        write_sequence_stub(p, self.sb.get("shots") or [])
        self.sb = json.loads((p / "state/storyboard.json").read_text(encoding="utf-8"))
        plan_path = p / "state/chapter_plan.json"
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        plan["source_storyboard_fingerprint"] = _digest(p / "state/storyboard.json")
        write(plan_path, plan)
        # 正常路径必须实际运行门禁，不能手写成功执行回执。
        from scripts.workflow_execution import execute
        for step in ('gate1', 'gate5', 'crew_pipeline', 'crew_rehearsal', 'gate6'):
            receipt = execute(p, step)
            self.assertEqual(receipt['status'], 'succeeded', receipt.get('errors'))
        # 关5登记分镜指纹后按实际当前分镜更新依赖，避免沿用旧版本。
        self.sb = json.loads((p/'state/storyboard.json').read_text(encoding='utf8'))
        plan = json.loads(plan_path.read_text(encoding='utf8'))
        plan['source_storyboard_fingerprint'] = _digest(p/'state/storyboard.json')
        write(plan_path, plan)
        self.approve_animatic()
        from test_submission_jobs import job_contract_fixture
        _, self.contract = job_contract_fixture(p, self.contract)
        return package

    def approve_animatic(self):
        p = self.project
        ffmpeg('-f', 'lavfi', '-i', 'color=black:s=320x180:r=24:d=4',
               '-i', p / 'input/song.wav', '-c:v', 'libx264', '-c:a', 'alac', p / 'animatic.mp4')
        write(p / 'state/animatic_review.json', {
            'stage': 'animatic', 'project_id': str(p.resolve()), 'reviewed_media': 'animatic.mp4',
            'reviewed_media_sha256': digest(p / 'animatic.mp4'), 'decision': 'approve',
            'watched_full_duration': True, 'dependencies': animatic_dependencies(p),
            'checks': {d: {'status': 'pass', 'evidence': '测试夹具，不代表艺术批准'} for d in ANIMATIC_DIMENSIONS},
            'timecoded_findings': [{'dimension': d, 'start_sec': 0, 'end_sec': 4,
                                    'observation': '测试时间码'} for d in ANIMATIC_DIMENSIONS]})

    def make_review(self):
        p = self.project
        plan = json.loads((p / 'state/chapter_plan.json').read_text(encoding='utf-8'))
        duration = next(c['duration_sec'] for c in plan['chapters'] if c['id'] == plan['production_policy']['prototype_chapter'])
        ffmpeg('-f', 'lavfi', '-i', f'color=black:s=320x180:r=24:d={duration}',
               '-i', p / 'input/song.wav', '-t', duration, '-c:v', 'libx264', '-c:a', 'alac', p / 'review.mp4')
        review = initial_review(plan)
        review.update(reviewed_media='review.mp4', reviewed_media_sha256=digest(p / 'review.mp4'),
                      watched_full_duration=True, decision='approve', batch_generation_allowed=True,
                      dependencies=dependency_snapshot(p))
        review['technical_checks'].update(container_and_duration='pass', audio_alignment='pass')
        for field, names in (('director_checks', DIMENSIONS), ('sequence_checks', SEQUENCE_DIMENSIONS)):
            review[field] = {name: {'status': 'pass', 'evidence': '单元测试合成证据，不代表真实审美评价'}
                             for name in names}
        review['timecoded_findings'] = [{'start_sec': 0, 'end_sec': duration, 'dimension': 'editing',
                                        'observation': '测试时间码覆盖'}]
        review['crew_emotion_pull'] = {
            'source': 'unit-test',
            'segments': [{
                'start_sec': 0.0,
                'end_sec': float(duration),
                'verdict': 'KEEP',
                'reason': '单元测试：无情绪不合段',
            }],
        }
        write(p / 'state/director_review.json', review)
        return review

    def test_A06_required_dependencies_and_actual_files(self):
        self.ready()
        review = self.make_review()
        self.assertTrue(approved(self.project)[0])
        del review['dependencies'][str((self.project / 'state/storyboard.json').resolve())]
        write(self.project / 'state/director_review.json', review)
        self.assertFalse(approved(self.project)[0])
        review['dependencies'] = dependency_snapshot(self.project)
        write(self.project / 'state/director_review.json', review)
        (self.project / 'prompts/SH001.txt').write_text('替换提示词', encoding='utf-8')
        self.assertFalse(approved(self.project)[0])

    def test_workflow_sample_ignores_other_chapter_but_binds_public_lock(self):
        self.ready(max_duration=2)
        self.make_review()
        self.assertTrue(approved(self.project)[0])
        plan = review_plan(self.project)
        prototype = plan['production_policy']['prototype_chapter']
        other = next(c for c in plan['chapters'] if c['id'] != prototype)
        (self.project / f"prompts/chapters/{other['id']}.txt").write_text('修改无关章节', encoding='utf-8')
        (self.project / other['audio_asset']['local_path']).write_bytes(b'changed-unrelated-audio')
        sb = json.loads((self.project / 'state/storyboard.json').read_text(encoding='utf-8'))
        next(s for s in sb['shots'] if s['id'] in other['shot_ids'])['story_beat'] = '无关镜头的故事调整'
        write(self.project / 'state/storyboard.json', sb)
        self.assertTrue(approved(self.project)[0], approved(self.project)[1])
        development = json.loads((self.project / 'state/development.json').read_text(encoding='utf-8'))
        development['crew_bible']['lighting']['contrast_arc'] = '修改全片公共灯光锁'
        write(self.project / 'state/development.json', development)
        self.assertFalse(approved(self.project)[0])

    def test_workflow_animatic_required_before_first_submission(self):
        self.ready()
        self.assertEqual(validate_submission_contract(self.contract, self.project), [])
        path = self.project / 'state/animatic_review.json'
        record = json.loads(path.read_text(encoding='utf-8'))
        record['decision'] = 'pending'
        write(path, record)
        errors = validate_submission_contract(self.contract, self.project)
        self.assertTrue(any('剪辑预演' in error for error in errors))
        record['decision'] = 'approve'
        record['timecoded_findings'][0]['end_sec'] = 999
        write(path, record)
        self.assertFalse(animatic_approved(self.project)[0])
        self.assertNotIn('lip_sync', record['checks'])

    def test_workflow_animatic_real_media_and_design_changes_invalidate(self):
        self.ready()
        self.assertTrue(animatic_approved(self.project)[0])
        self.style['style_name'] = '新的全片视觉方案'
        write(self.project / 'state/style.json', self.style)
        self.assertFalse(animatic_approved(self.project)[0])
        self.style['style_name'] = '写实'
        write(self.project / 'state/style.json', self.style)
        self.assertTrue(animatic_approved(self.project)[0])
        ffmpeg('-f', 'lavfi', '-i', 'color=white:s=320x180:r=24:d=4',
               '-i', self.project / 'input/song.wav', '-c:v', 'libx264', '-c:a', 'alac', self.project / 'animatic.mp4')
        self.assertFalse(animatic_approved(self.project)[0])

    def test_animatic_refresh_requires_revocation_and_resets_all_approval_evidence(self):
        self.ready()
        record_path = self.project / 'state/animatic_review.json'
        before = digest(record_path)
        denied = command('animatic_review.py', 'refresh', self.project)
        self.assertNotEqual(denied.returncode, 0)
        self.assertEqual(digest(record_path), before)
        revoked = command('animatic_review.py', 'invalidate', self.project)
        self.assertEqual(revoked.returncode, 0, revoked.stderr)
        self.assertFalse(animatic_approved(self.project)[0])
        refreshed = command('animatic_review.py', 'refresh', self.project, '--media', 'animatic.mp4')
        self.assertEqual(refreshed.returncode, 0, refreshed.stderr)
        pending = json.loads(record_path.read_text(encoding='utf-8'))
        self.assertEqual(pending['decision'], 'pending')
        self.assertFalse(pending['watched_full_duration'])
        self.assertEqual(pending['timecoded_findings'], [])
        self.assertTrue(all(c['status'] == 'pending' and not c['evidence'] for c in pending['checks'].values()))
        self.assertTrue(list((self.project / 'state/review_history').glob('animatic-*.json')))
        self.assertFalse(animatic_approved(self.project)[0])

    def test_submission_rechecks_gate1_without_trusting_old_gate_report(self):
        self.ready()
        (self.project/'input/lyrics.txt').write_text('被替换的歌词', encoding='utf8')
        errors = validate_submission_contract(self.contract, self.project)
        self.assertTrue(any('歌词一行不能少' in e for e in errors), errors)

    def test_submission_job_preparation_uses_real_full_contract_and_actual_reservation(self):
        self.ready()
        from test_submission_jobs import job_contract_fixture, response
        from scripts.submission_jobs import prepare, dispatch
        path, contract = job_contract_fixture(self.project, self.contract)
        job = prepare(self.project, path)
        self.assertEqual(job['status'], 'prepared')
        seen = []
        def adapter(payload):
            seen.append(payload)
            return response()
        result = dispatch(self.project, job['job_id'], adapter)
        self.assertEqual(result['status'], 'pending')
        self.assertEqual(seen, [contract['provider_request']])

    def test_submission_rechecks_motion_package_without_trusting_old_gate5(self):
        self.ready()
        path = self.project/'prompts/motion/SH001.json'
        motion = json.loads(path.read_text(encoding='utf8'))
        del motion['crew']['lighting']
        write(path, motion)
        errors = validate_submission_contract(self.contract, self.project)
        self.assertTrue(any('关5' in e and '运动包' in e for e in errors), errors)

    def test_submission_rejects_stale_rehearsal_even_when_status_still_passes(self):
        self.ready()
        self.assertEqual(validate_submission_contract(self.contract, self.project), [])
        path = self.project/'gates/crew_rehearsal.json'
        report = json.loads(path.read_text(encoding='utf8'))
        report['input_fingerprints'][str(self.project.resolve()/'state/sequence_ref.txt')] = 'sha256:stale'
        write(path, report)
        errors = validate_submission_contract(self.contract, self.project)
        self.assertTrue(any('crew_rehearsal.json: 输入指纹过期' in e for e in errors), errors)

    def test_A07_A08_A09_A10_submission_positive_and_tampering(self):
        package = self.ready()
        self.assertEqual(self.contract['prompt_sha256'], package['prompt_sha256'])
        self.assertTrue(self.contract['prompt_sha256'].startswith('sha256:'))
        self.assertEqual(validate_submission_contract(self.contract, self.project), [])
        bare_hash = copy.deepcopy(self.contract)
        bare_hash['prompt_sha256'] = bare_hash['prompt_sha256'].removeprefix('sha256:')
        self.assertIn('提交提示词指纹不匹配',
                      validate_submission_contract(bare_hash, self.project))
        for key, value in [('project_id', 'other'), ('chapter_id', 'CH999'),
                           ('source_package_sha256', 'old'), ('edit_shots', []),
                           ('estimated_cash_cny', float('nan')), ('cash_limit_cny', '0'),
                           ('duration_sec', 999), ('resolution', 'unsupported')]:
            with self.subTest(key=key):
                changed = copy.deepcopy(self.contract)
                changed[key] = value
                self.assertTrue(validate_submission_contract(changed, self.project))
        changed = copy.deepcopy(self.contract)
        changed['reference_bindings'][0]['binding_status'] = 'planned'
        self.assertTrue(validate_submission_contract(changed, self.project))
        self.assertIn('blocking_start_state', REFERENCE_INTENTS)
        self.assertFalse(any('未知参考意图' in error for error in
                             validate_submission_contract(self.contract, self.project)))
        self.assertTrue(validate_submission_contract(self.contract))

    def test_A11_blueprint_finite_full_coverage_and_source(self):
        package = self.ready()
        blueprint = package['omni_reference']
        self.assertEqual(validate_omni_reference_blueprint(blueprint), [])
        for index, key, value in [(0, 'start_sec', 1), (0, 'end_sec', float('nan')),
                                  (1, 'end_sec', 3), (1, 'shot_id', 'SH001')]:
            changed = copy.deepcopy(blueprint)
            changed['timeline'][index][key] = value
            self.assertTrue(validate_omni_reference_blueprint(changed))
        changed = copy.deepcopy(blueprint)
        changed['reference_bindings'][0]['source'] = {'sha256': 'hash-only'}
        self.assertTrue(validate_omni_reference_blueprint(changed))

    def test_A07_approved_string_cannot_unlock_other_chapter(self):
        self.ready()
        result = command('chapter_plan_build.py', self.project, '--max-duration', 2)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(command('chapter_audio_build.py', self.project).returncode, 0)
        compile_chapters(self.project)
        plan = review_plan(self.project)
        prototype = plan['production_policy']['prototype_chapter']
        cid = next(c['id'] for c in plan['chapters'] if c['id'] != prototype)
        path = self.project / f'prompts/chapters/{cid}.json'
        package = json.loads(path.read_text(encoding='utf-8'))
        contract = copy.deepcopy(self.contract)
        contract.update(chapter_id=cid, duration_sec=2, source_package_sha256=digest(path),
                        prompt=package['prompt'], prompt_sha256=package['prompt_sha256'],
                        edit_shots=[{'id': b['shot_id'], 'start_sec': b['start_sec'],
                                     'end_sec': b['end_sec'], 'cut_relation': b['cut_relation']}
                                    for b in package['beats']], reference_bindings=copy.deepcopy(package['reference_bindings']))
        for index, binding in enumerate(contract['reference_bindings']):
            binding['binding_status'] = 'uploaded'
            binding['source']['asset_id'] = f'mock-test-only-{index}'
        errors = validate_submission_contract(contract, self.project)
        self.assertTrue(any('批量审片未通过' in error for error in errors), errors)

    def test_A12_cinematography_conflict(self):
        self.sb['shots'][0]['crew']['cinematography']['shot_size'] = 'close_up'
        self.assertTrue(any('景别' in e for e in validate_crew_pipeline(self.dev, self.sb, project=self.project)))
        self.sb['shots'][0]['crew']['cinematography']['shot_size'] = '全景'
        self.sb['shots'][0]['motion_design']['camera_motion'] = 'dolly'
        self.assertTrue(any('运镜' in e for e in validate_crew_pipeline(self.dev, self.sb, project=self.project)))

    def test_A13_selected_candidate_must_materialize(self):
        plan = json.loads((self.project / 'state/story_plan.json').read_text(encoding='utf-8'))
        plan['shots'][0]['coverage_plan'] = [{'id': 'C1', 'selection_status': 'selected',
                                            'materialized_shot_id': 'SH999', 'purpose': '补充反应',
                                            'keyframe_moment': plan['shots'][1]['keyframe_moment'],
                                            'window': {'start': 2.0, 'end': 4.0}}]
        write(self.project / 'state/story_plan.json', plan)
        with self.assertRaisesRegex(ValueError, 'coverage未引用'):
            build(self.project)
        plan['shots'][0]['coverage_plan'][0]['materialized_shot_id'] = 'SH002'
        plan['shots'][1]['coverage_source_id'] = 'C1'
        write(self.project / 'state/story_plan.json', plan)
        self.assertEqual(build(self.project)['shots'][1]['coverage_source_id'], 'C1')
        plan['shots'][0]['coverage_plan'][0]['window']['end'] = 3
        write(self.project / 'state/story_plan.json', plan)
        with self.assertRaisesRegex(ValueError, 'coverage未引用'):
            build(self.project)

    def test_A14_axis_alias_and_reset_evidence(self):
        self.dev['scenes'][0]['axes']['renamed'] = copy.deepcopy(self.dev['scenes'][0]['axes']['door_axis'])
        self.assertTrue(validate_crew_pipeline(self.dev, self.sb, project=self.project))
        self.sb['shots'][1]['crew']['editing']['axis_reset'] = True
        music, timeline = _seq_music()
        self.assertTrue(any('桥接' in e for e in validate_sequence_language(
            self.sb['shots'], self.dev['crew_bible']['editing'],
            music_structure=music, timeline=timeline, fps=24,
        )))

    def test_A15_cross_scene_and_discontinuous_time_not_direction_match(self):
        shots = copy.deepcopy(self.sb['shots'])
        shots[1]['scene_id'] = 'other'
        shots[1]['crew']['blocking']['entry_direction'] = 'right_to_left'
        music, timeline = _seq_music()
        errors = validate_sequence_language(
            shots, self.dev['crew_bible']['editing'],
            music_structure=music, timeline=timeline, fps=24,
        )
        self.assertFalse(any('入画方向' in e for e in errors), errors)
        shots[1]['scene_id'] = 'gate'
        shots[1]['crew']['editing']['time_relation'] = 'flashback'
        errors = validate_sequence_language(
            shots, self.dev['crew_bible']['editing'],
            music_structure=music, timeline=timeline, fps=24,
        )
        self.assertFalse(any('入画方向' in e for e in errors), errors)

    def test_A16_declared_coverage_must_be_fulfilled(self):
        self.dev['scenes'][0]['coverage_requirements'] = ['reaction']
        self.assertTrue(validate_crew_pipeline(self.dev, self.sb, project=self.project))
        self.dev['scenes'][0]['kind'] = 'performance'
        self.dev['scenes'][0]['coverage_requirements'] = ['establish', 'turn']
        self.assertEqual(validate_crew_pipeline(self.dev, self.sb, project=self.project), [])

    def test_A17_strict_hook_and_integer_threshold(self):
        self.sb['shots'][0]['crew']['editing']['hook'] = 'false'
        self.assertTrue(validate_crew_pipeline(self.dev, self.sb, project=self.project))
        self.sb['shots'][0]['crew']['editing']['hook'] = True
        for value in (1.5, True):
            self.dev['crew_bible']['editing']['pacing_policy']['max_consecutive_slow_shots'] = value
            self.assertTrue(validate_crew_pipeline(self.dev, self.sb, project=self.project))

    def test_A18_A19_lyrics_and_production_authority(self):
        self.assertTrue(next(c['pass'] for c in check_timeline(self.timeline, '第一句\n第二句', 4)
                             if c['item'] == '歌词一行不能少'))
        self.assertFalse(all(c['pass'] for c in check_timeline(self.timeline, '替换文本\n第二句', 4)))
        self.timeline['lines'][1]['index'] = 1
        self.assertFalse(all(c['pass'] for c in check_timeline(self.timeline, '第一句\n第二句', 4)))
        self.timeline['asr_engine'] = 'fallback'
        self.assertEqual(timing_authority(self.timeline), 'provisional')
        write(self.project / 'state/timeline.json', self.timeline)
        self.assertNotEqual(command('chapter_plan_build.py', self.project, '--max-duration', 4).returncode, 0)

    def test_A20_prototype_not_delivery_and_simulated_review_not_approval(self):
        self.ready()
        review = self.make_review()
        self.assertTrue(approved(self.project)[0])
        self.assertFalse(delivery_approved(self.project)[0])
        review['simulation_only'] = True
        write(self.project / 'state/director_review.json', review)
        self.assertFalse(approved(self.project)[0])

    def test_A21_render_cannot_skip_review(self):
        result = command('edit_assemble.py', self.project, '--render')
        self.assertNotEqual(result.returncode, 0)

    def test_chapter_admission_missing_mismatched_and_wrong_media_are_blocked(self):
        self.ready()
        review = self.make_review()
        clip = {'status': 'reviewed', 'evidence': '测试', 'chapter_id': 'CH01',
                'shot_ids': ['SH001', 'SH002'], 'frame_count': 96, 'fps': 24,
                'scene_id': 'gate', 'path': 'review.mp4', 'sha256': digest(self.project / 'review.mp4')}
        selections = [{'shot_id': f'SH00{i+1}', 'clip_id': 'C1', 'in_frame': i*48,
                       'out_frame': (i+1)*48, 'edit_reason': '测试', 'state_check': '测试'} for i in range(2)]
        write(self.project / 'state/generated_clips.json', {'clips': {'C1': clip}})
        write(self.project / 'state/edit_selection.json', {'selections': selections})
        result = command('edit_assemble.py', self.project, '--render')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('逐章审片未批准', result.stderr)
        self.assertFalse((self.project / 'clips/edit/SH001_fitted.mp4').exists())
        preview = command('edit_assemble.py', self.project, '--render', '--technical-preview')
        self.assertEqual(preview.returncode, 0, preview.stderr)
        write(self.project / 'state/reviews/CH01.json', review)
        clip['chapter_id'] = 'CH99'
        write(self.project / 'state/generated_clips.json', {'clips': {'C1': clip}})
        result = command('edit_assemble.py', self.project, '--render')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('归属不一致', result.stderr)
        clip['chapter_id'] = 'CH01'
        replacement = self.project / 'replacement.mp4'
        ffmpeg('-f', 'lavfi', '-i', 'color=white:s=320x180:r=24:d=4', '-c:v', 'libx264', replacement)
        clip.update(path='replacement.mp4', sha256=digest(replacement))
        write(self.project / 'state/generated_clips.json', {'clips': {'C1': clip}})
        result = command('edit_assemble.py', self.project, '--render')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('实际批准的媒体版本', result.stderr)
        clip.update(path='review.mp4', sha256=digest(self.project / 'review.mp4'))
        clip['shot_ids'] = 'SH001SH002'
        write(self.project / 'state/generated_clips.json', {'clips': {'C1': clip}})
        self.assertNotEqual(command('edit_assemble.py', self.project, '--render').returncode, 0)
        clip['shot_ids'] = ['SH001', 'SH002']
        clip['origin_receipt_path'] = self.generated_origin(self.project / 'review.mp4')
        write(self.project / 'state/generated_clips.json', {'clips': {'C1': clip}})
        from scripts.edit_assemble import build as assemble
        before = digest(self.project / 'state/edit_assembly.json')
        with patch('scripts.media_verify.is_cfr', return_value=False):
            with self.assertRaisesRegex(ValueError, 'CFR'):
                assemble(self.project, render=True)
        self.assertEqual(before, digest(self.project / 'state/edit_assembly.json'))

    def test_A22_A23_A28_actual_delivery_frames_and_audio_payload(self):
        self.ready()
        self.make_review()
        report = verify_delivery(self.project / 'review.mp4', self.project / 'input/song.wav', 4, 24, 320, 180)
        self.assertEqual(report['status'], 'pass')
        self.assertTrue(verify_delivery(self.project / 'review.mp4', self.project / 'input/song.wav',
                                        5, 30, 640, 360)['errors'])
        resolve_master(self.project, self.timeline)
        self.timeline.pop('master_audio_sha256')
        ffmpeg('-f', 'lavfi', '-i', 'sine=frequency=880:sample_rate=48000:duration=4',
               '-ac', '2', self.project / 'input/song.wav')
        with self.assertRaises(ValueError):
            resolve_master(self.project, self.timeline)

    def test_mp3_stream_copy_survives_cross_container_verification(self):
        master = self.project / 'master.mp3'
        picture = self.project / 'picture.mp4'
        delivery = self.project / 'delivery.mp4'
        ffmpeg('-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=2',
               '-ac', '2', '-c:a', 'libmp3lame', master)
        ffmpeg('-f', 'lavfi', '-i', 'color=black:s=160x90:r=24:d=2',
               '-frames:v', '48', '-r', '24', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', picture)
        ffmpeg('-i', picture, '-i', master, '-map', '0:v:0', '-map', '1:a:0',
               '-c:v', 'copy', '-c:a', 'copy', delivery)
        report = verify_delivery(delivery, master, 2, 24, 160, 90)
        self.assertEqual(report['status'], 'pass', report)
        self.assertEqual(report['hash_mode'], 'encoded_packets')
        self.assertEqual(report['master_audio_hash'], report['delivery_audio_hash'])

    def test_A24_source_fps_not_interpreted_as_target_fps(self):
        source, target = self.project / 'source.mp4', self.project / 'target.mp4'
        ffmpeg('-f', 'lavfi', '-i', 'color=black:s=160x90:r=30:d=2', '-c:v', 'libx264', source)
        result = fit(str(source), str(target), 2, 24)
        self.assertNotEqual(result.get('mode'), 'copy')
        self.assertEqual(int(probe(target)['streams'][0]['nb_read_frames']), 48)

    def test_A25_unsupported_transitions_blocked(self):
        for field, value in [('transition_type', 'dissolve'), ('audio_transition', 'J_cut'),
                             ('audio_transition', 'L_cut')]:
            self.sb['shots'][0]['crew']['editing'][field] = value
            self.assertTrue(supported_transitions(self.sb['shots']))

    def test_A26_A27_alignment_self_reference_not_formal_pass(self):
        source = self.project / 'analysis.wav'
        ffmpeg('-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=16000:duration=4', '-ac', '1', source)
        result = command('align_verify.py', source, self.project / 'state/storyboard.json',
                         self.project / 'state/timeline.json')
        report = json.loads(result.stdout)
        self.assertNotEqual(report['overall'], 'PASS')
        self.assertFalse(report['delivery_verified'])
        self.assertFalse(report['lip_sync_verified'])
        self.assertNotIn('clips_sum', report['check1_total_duration'])

    def test_A29_duplicate_or_malformed_ids(self):
        self.assertFalse(unique_ids([{'id': 'A'}, {'id': 'A'}]))
        self.assertFalse(unique_ids([{'id': []}]))
        self.assertTrue(timeline_errors([{'id': 'A', 'start_sec': 0, 'end_sec': 4},
                                         {'id': 'A', 'start_sec': 0, 'end_sec': 4}], 4))

    def test_A30_declared_inspection_not_actual_observation(self):
        image = self.project / 'frame.png'
        image.write_bytes(PNG_BYTES)
        asset = register(self.project, 'M1', 'scene_master', 'gate', image, '声明性证据')
        self.assertFalse(asset['agent_inspected'])
        self.assertGreater(asset['media_probe']['width'], 0)
        single_gif = self.project / 'single.gif'
        animated_gif = self.project / 'animated.gif'
        ffmpeg('-f', 'lavfi', '-i', 'color=red:s=16x16:r=1:d=1',
               '-frames:v', '1', single_gif)
        self.assertEqual(register(self.project, 'M2', 'scene_master', 'gate', single_gif,
                                  '单帧 GIF 测试')['status'], 'approved')
        ffmpeg('-f', 'lavfi', '-i', 'color=red:s=16x16:r=2:d=1', animated_gif)
        with self.assertRaises(ValueError):
            register(self.project, 'M3', 'scene_master', 'gate', animated_gif, '动态 GIF 测试')
        image.write_bytes(b'not-an-image')
        with self.assertRaises((ValueError, subprocess.CalledProcessError)):
            register(self.project, 'M4', 'scene_master', 'gate', image, '声明')

    def test_A31_batch_rollback_and_recovery(self):
        a, b = self.project / 'a.txt', self.project / 'b.txt'
        a.write_text('old-a'); b.write_text('old-b')
        real_replace = os.replace
        calls = []
        def broken(source, target):
            calls.append(target)
            if len(calls) == 2:
                raise OSError('测试中断')
            return real_replace(source, target)
        with patch('scripts.pipeline_contracts.os.replace', side_effect=broken):
            with self.assertRaises(OSError):
                atomic_batch({a: 'new-a', b: 'new-b'})
        self.assertEqual((a.read_text(), b.read_text()), ('old-a', 'old-b'))
        atomic_batch({a: 'new-a', b: 'new-b'})
        self.assertEqual((a.read_text(), b.read_text()), ('new-a', 'new-b'))
        journal = self.project / '.pipeline-transaction.json'
        write(journal, {'originals': {str(a): b'old-a'.hex()}, 'stages': {}})
        with self.assertRaises(ValueError):
            require_no_transaction(self.project)
        recover_transaction(journal)
        self.assertEqual(a.read_text(), 'old-a')

    def test_A32_decimal_idempotency_reservations_and_refunds(self):
        ledger = self.project / 'ledger.jsonl'
        def event(eid, tid, action, cash, **extra):
            return {'event_id': eid, 'task_id': tid, 'action': action, 'cash_cny': cash,
                    'coins': 0, 'duration_sec': 0, 'backend': 'test', **extra}
        for value in ('NaN', 'Infinity'):
            with self.assertRaises(ValueError): money(value)
        initial = event('E1', 'T1', 'reserve', '0.1')
        record(ledger, copy.deepcopy(initial), '0.3')
        with self.assertRaises(ValueError):
            record(ledger, event('B1', 'B1', 'reserve', '1'), '99')
        self.assertEqual(record(ledger, copy.deepcopy(initial), '0.3')['status'], 'idempotent')
        for field, value in [('backend', 'other'), ('shot_id', 'SH002'),
                             ('refund_of', 'T0'), ('note', 'changed')]:
            with self.subTest(idempotency_field=field):
                changed = copy.deepcopy(initial)
                changed[field] = value
                with self.assertRaises(ValueError):
                    record(ledger, changed, '0.3')
        record(ledger, event('E2', 'T1', 'settle', '0.1'), '0.3')
        with self.assertRaises(ValueError):
            record(ledger, event('E3', 'T1', 'fail_refund', '-0.2', refund_of='T1'), '0.3')
        record(ledger, event('E4', 'T1', 'fail_refund', '-0.1', refund_of='T1'), '0.3')
        def reserve(index):
            try:
                record(ledger, event(f'R{index}', f'RT{index}', 'reserve', '.2'), '.3')
                return True
            except ValueError: return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sum(pool.map(reserve, range(2))), 1)

    def test_A35_registry_matches_real_cli_and_external_phases(self):
        self.assertEqual(len({g['id'] for g in REGISTRY}), len(REGISTRY))
        self.assertTrue(any(g['owner'] == 'external_adapter' for g in REGISTRY))
        self.assertEqual({g['id'] for g in REGISTRY if g['entry'].startswith('gate_checks.py')},
                         {'1', '5', '6'})

    def test_A33_source_version_keys_kind_statistics_and_mapping(self):
        rows = [{'source_id': source, 'film_id': 'same-id', 'edition': edition,
                 'scene_id': 'S1', 'shot_index': index, 'duration_sec': duration,
                 'shot_size': '全景' if index == 0 else '近景', 'camera_movement': 'locked',
                 'scene_kind': 'narrative', 'rating': 9}
                for source, edition in [('a', 'v1'), ('a', 'v2'), ('b', 'v1')]
                for index, duration in enumerate((1, 3))]
        manifest = {'schema_version': 1, 'media_policy': 'annotations_only',
                    'sources': [{'id': source, 'title': '测试标注', 'url': 'https://example.test',
                                 'license_or_terms': 'test-only', 'access_scope': 'annotations'}
                                for source in ('a', 'b')]}
        profile = build_profile(rows, manifest, profile_id='test', min_films=4)
        self.assertEqual(profile['sample']['film_count'], 3)
        self.assertEqual(profile['sample']['scene_count'], 3)
        self.assertEqual(profile['status'], 'exploratory_only')
        self.assertEqual(profile['scene_kind_envelopes']['narrative']['p10'], 2)
        self.assertIn('full', profile['shot_size_frequency'])
        self.assertIn('static', profile['camera_movement_frequency'])

    def test_film_grammar_jsonl_rejects_boolean_numbers_and_float_indexes(self):
        corpus = self.project / 'corpus.jsonl'
        base = {'source_id': 'a', 'film_id': 'f', 'scene_id': 's', 'shot_index': 1,
                'duration_sec': 1, 'shot_size': '全景', 'camera_movement': 'locked'}
        for field, value in [('shot_index', True), ('duration_sec', True),
                             ('shot_index', 1.0)]:
            with self.subTest(field=field, value=value):
                row = {**base, field: value}
                corpus.write_text(json.dumps(row) + '\n', encoding='utf-8')
                with self.assertRaises(ValueError):
                    load_jsonl(corpus)

    def test_A22_A23_stitch_actual_30fps_and_short_clip_failure(self):
        self.ready()
        self.sb['fps'] = 30
        write(self.project / 'state/storyboard.json', self.sb)
        (self.project / 'clips').mkdir()
        clip = self.project / 'clips/CH01_fitted.mp4'
        ffmpeg('-f', 'lavfi', '-i', 'color=black:s=320x180:r=30:d=4', '-c:v', 'libx264', clip)
        result = command('stitch_chapters.py', self.project, '--allow-unreviewed-preview',
                         '--output-name', 'technical_preview.mp4', '--width', 320, '--height', 180)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        output = self.project / 'output/technical_preview.mp4'
        video = next(s for s in probe(output)['streams'] if s['codec_type'] == 'video')
        self.assertEqual(video['avg_frame_rate'], '30/1')
        self.assertEqual(int(video['nb_read_frames']), 120)
        ffmpeg('-f', 'lavfi', '-i', 'color=black:s=320x180:r=30:d=1', '-c:v', 'libx264', clip)
        result = command('stitch_chapters.py', self.project, '--allow-unreviewed-preview',
                         '--output-name', 'short_preview.mp4', '--width', 320, '--height', 180)
        self.assertNotEqual(result.returncode, 0)

    def test_A20_A21_full_formal_workflow_and_clip_dependency_invalidation(self):
        self.ready()
        prototype_review = self.make_review()
        clip = self.project / 'review.mp4'
        write(self.project / 'state/generated_clips.json', {'clips': {'C1': {
            'status': 'reviewed', 'evidence': '单元测试合成素材，不是美学批准',
            'chapter_id': 'CH01', 'shot_ids': ['SH001', 'SH002'],
            'frame_count': 96, 'fps': 24, 'scene_id': 'gate', 'path': 'review.mp4',
            'sha256': digest(clip), 'origin_receipt_path': self.generated_origin(clip)}}})
        write(self.project / 'state/edit_selection.json', {'selections': [
            {'shot_id': f'SH00{index+1}', 'clip_id': 'C1', 'in_frame': index*48,
             'out_frame': (index+1)*48, 'edit_reason': '测试按音乐切点', 'state_check': '测试状态承接'}
            for index in range(2)]})
        write(self.project / 'state/reviews/CH01.json', prototype_review)
        result = command('edit_assemble.py', self.project, '--render')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        result = command('stitch_chapters.py', self.project, '--allow-unreviewed-preview',
                         '--output-name', 'sequence_preview.mp4', '--width', 320, '--height', 180)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        write(self.project / 'state/reviews/CH01.json', prototype_review)
        sequence = copy.deepcopy(prototype_review)
        sequence['prototype_chapter'] = 'full_sequence'
        sequence['reviewed_media'] = 'output/sequence_preview.mp4'
        sequence['reviewed_media_sha256'] = digest(self.project / sequence['reviewed_media'])
        assembly_path = self.project / 'state/edit_assembly.json'
        assembly = json.loads(assembly_path.read_text(encoding='utf-8'))
        assembly['technical_preview'] = True
        write(assembly_path, assembly)
        with self.assertRaisesRegex(ValueError, '正式选段渲染'):
            dependency_snapshot(self.project, 'full_sequence')
        assembly['technical_preview'] = False
        write(assembly_path, assembly)
        sequence['dependencies'] = dependency_snapshot(self.project, 'full_sequence')
        write(self.project / 'state/reviews/full_sequence.json', sequence)
        self.assertTrue(delivery_approved(self.project)[0], delivery_approved(self.project)[1])
        frozen_hash = digest(self.project / 'state/edit_assembly.json')
        rerender = command('edit_assemble.py', self.project, '--render')
        self.assertNotEqual(rerender.returncode, 0)
        self.assertEqual(frozen_hash, digest(self.project / 'state/edit_assembly.json'))
        from scripts.repair_state import assess as assess_repairs
        assess_repairs(self.project)
        result = command('stitch_chapters.py', self.project, '--width', 320, '--height', 180)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(check_delivery(self.project, 'final_mv.mp4')['status'], 'technical_verified')
        from scripts.delivery_evidence import review_template
        pending = command('delivery_package.py', self.project)
        self.assertNotEqual(pending.returncode, 0)
        final_review = review_template(self.project, 'final_mv.mp4')
        final_review.update(observer='明确的单元测试观察夹具，不代表真实观看', watched_full_audio_video=True)
        for row in final_review['shots']:
            row.update(performance_status='pass', sound_action_status='pass', continuity_status='pass',
                       observation='单元测试本地媒体复核夹具',
                       mouth_status='pass' if row['lip_sync_required'] else 'not_applicable')
            if row['lip_sync_required']:
                a, b = row['window']['start'], row['window']['end']
                row['mouth_samples'] = [{'time_sec': a, 'observation': '测试开口观察'},
                                        {'time_sec': (a+b)/2, 'observation': '测试闭口观察'}]
        write(self.project / 'state/final_media_review.final_mv.mp4.json', final_review)
        result = command('delivery_package.py', self.project)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads((self.project / 'output/final_mv.verification.json').read_text(encoding='utf-8'))
        self.assertEqual(report['status'], 'pass')
        self.assertEqual(report['master_audio_hash'], report['delivery_audio_hash'])
        self.assertTrue(delivery_approved(self.project)[0])
        final_state = check_delivery(self.project, 'final_mv.mp4')
        self.assertEqual(final_state['status'], 'ready', final_state['errors'])
        self.assertFalse(final_state['user_received'])
        self.assertEqual(final_state['history'], ['rendering', 'exported', 'technical_verified', 'ready'])
        package_info = final_state['delivery_package']
        package_manifest = json.loads(Path(package_info['manifest_path']).read_text(encoding='utf8'))
        ledger = self.project / 'state/ledger.jsonl'
        ledger_bytes = ledger.read_bytes()
        financial_rows = [json.loads(r) for r in ledger.read_text(encoding='utf8').splitlines()]
        next(r for r in financial_rows if r['action'] == 'settle')['cash_cny'] = '0'
        ledger.write_text('\n'.join(json.dumps(r) for r in financial_rows)+'\n', encoding='utf8')
        self.assertFalse(check_delivery(self.project, 'final_mv.mp4')['ready_for_delivery'])
        ledger.write_bytes(ledger_bytes)
        self.assertTrue(check_delivery(self.project, 'final_mv.mp4')['ready_for_delivery'])
        bundle = Path(package_manifest['archive_path'])
        bundle_bytes = bundle.read_bytes()
        bundle.write_bytes(bundle_bytes + b'tampered')
        self.assertFalse(check_delivery(self.project, 'final_mv.mp4')['ready_for_delivery'])
        bundle.write_bytes(bundle_bytes)
        self.assertTrue(check_delivery(self.project, 'final_mv.mp4')['ready_for_delivery'])
        lrc_file = Path(package_manifest['artifact_root']) / 'lyrics.lrc'
        lrc_bytes = lrc_file.read_bytes()
        lrc_file.write_bytes(b'[00:00.00]wrong lyrics')
        self.assertFalse(check_delivery(self.project, 'final_mv.mp4')['ready_for_delivery'])
        lrc_file.write_bytes(lrc_bytes)
        self.assertTrue(check_delivery(self.project, 'final_mv.mp4')['ready_for_delivery'])
        assessment_path = self.project / 'state/repair_assessment.json'
        assessment_bytes = assessment_path.read_bytes()
        assessment_path.unlink()
        self.assertFalse(check_delivery(self.project, 'final_mv.mp4')['ready_for_delivery'])
        assessment_path.write_bytes(assessment_bytes)
        self.assertTrue(check_delivery(self.project, 'final_mv.mp4')['ready_for_delivery'])
        origins = json.loads((self.project / 'state/generated_clips.json').read_text(encoding='utf8'))['clips']
        origin_path = self.project / origins['C1']['origin_receipt_path']
        origin_bytes = origin_path.read_bytes()
        origin_path.unlink()
        self.assertFalse(check_delivery(self.project, 'final_mv.mp4')['ready_for_delivery'])
        origin_path.write_bytes(origin_bytes)
        self.assertTrue(check_delivery(self.project, 'final_mv.mp4')['ready_for_delivery'])
        receipt_path = self.project/'state/executions/gate1.json'
        receipt_bytes = receipt_path.read_bytes()
        receipt_path.unlink()
        self.assertFalse(check_delivery(self.project, 'final_mv.mp4')['ready_for_delivery'])
        receipt_path.write_bytes(receipt_bytes)
        self.assertTrue(check_delivery(self.project, 'final_mv.mp4')['ready_for_delivery'])
        altered_chapter_review = copy.deepcopy(prototype_review)
        altered_chapter_review['note'] = '章节审批版本发生变化'
        write(self.project / 'state/reviews/CH01.json', altered_chapter_review)
        self.assertFalse(delivery_approved(self.project)[0])
        self.assertFalse(check_delivery(self.project, 'final_mv.mp4')['ready_for_delivery'])
        write(self.project / 'state/reviews/CH01.json', prototype_review)
        result = command('director_review_gate.py', 'invalidate', self.project, '--chapter', 'full_sequence')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(check_delivery(self.project, 'final_mv.mp4')['ready_for_delivery'])
        result = command('director_review_gate.py', 'refresh', self.project, '--chapter', 'full_sequence')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(list((self.project / 'state/reviews/history').glob('*.json')))
        self.assertFalse(delivery_approved(self.project)[0])
        write(self.project / 'state/reviews/full_sequence.json', sequence)
        clip.write_bytes(b'changed-video')
        self.assertFalse(delivery_approved(self.project)[0])

    def test_repair_real_contract_new_task_and_chapter_reapproval(self):
        from scripts import repair_state
        from scripts.chapter_admission import require as admit
        self.ready()
        old_review = self.make_review()
        old = {'status': 'reviewed', 'evidence': '测试素材', 'chapter_id': 'CH01',
               'shot_ids': ['SH001', 'SH002'], 'frame_count': 96, 'fps': 24, 'scene_id': 'gate',
               'path': 'review.mp4', 'sha256': digest(self.project / 'review.mp4'),
               'origin_receipt_path': self.generated_origin(self.project / 'review.mp4')}
        write(self.project / 'state/generated_clips.json', {'clips': {'OLD': old}})
        write(self.project / 'state/reviews/CH01.json', old_review)
        failure = copy.deepcopy(old_review)
        failure.update(decision='reject', batch_generation_allowed=False)
        failure['director_checks']['editing']['status'] = 'fail'
        write(self.project / 'analysis/failed_review.json', failure)
        plan = {'dominant_failures': [{'classification': 'actual_drift', 'dimension': 'editing',
                                      'evidence': '测试时间码覆盖', 'cause': '测试模型未按正确切点执行'}],
                'structural_failure': False, 'action': 'surgical_repair', 'change_only': ['剪辑切点'],
                'preserve_exactly': ['身份与舞台'],
                'same_prompt_retry_reason': '测试：已批准切点正确，生成未遵循，按冻结条件重试'}
        write(self.project / 'analysis/repair_plan.json', plan)
        opened = repair_state.open_repair(self.project, clip_id='OLD', plan_path='analysis/repair_plan.json',
                                          failure_review_path='analysis/failed_review.json')
        self.assertFalse(approved(self.project)[0])
        with self.assertRaises(ValueError): repair_state.require_closed(self.project)
        self.assertTrue(validate_submission_contract(self.contract, self.project))
        contract = {**self.contract, 'repair_id': opened['repair_id'], 'repair_plan_sha256': opened['plan_sha256']}
        contract_path = self.project / 'state/repair_contract.json'
        write(contract_path, contract)
        source = self.project / 'replacement.mp4'
        ffmpeg('-f', 'lavfi', '-i', 'color=white:s=320x180:r=24:d=4',
               '-i', self.project / 'input/song.wav', '-t', 4, '-c:v', 'libx264', '-c:a', 'alac', source)
        new = {**old, 'path': source.name, 'sha256': digest(source),
               'origin_receipt_path': self.generated_origin(source, contract_path)}
        write(self.project / 'state/generated_clips.json', {'clips': {'OLD': old, 'NEW': new}})
        reviewed = copy.deepcopy(old_review)
        reviewed.update(reviewed_media=source.name, reviewed_media_sha256=digest(source),
                        dependencies=dependency_snapshot(self.project, 'CH01'))
        write(self.project / 'state/reviews/CH01.json', reviewed)
        write(self.project / 'state/director_review.json', reviewed)
        self.assertTrue(approved(self.project, 'CH01')[0], approved(self.project, 'CH01')[1])
        result = repair_state.close(self.project, opened['repair_id'], replacement_clip_id='NEW')
        self.assertEqual(result['status'], 'resolved')
        selections = [{'shot_id': f'SH00{i+1}', 'clip_id': 'NEW', 'in_frame': i*48,
                       'out_frame': (i+1)*48, 'edit_reason': '测试返修切点', 'state_check': '测试状态'} for i in range(2)]
        admit(self.project, self.sb, {'OLD': old, 'NEW': new}, selections)
        write(self.project / 'state/edit_selection.json', {'selections': selections})
        rendered = command('edit_assemble.py', self.project, '--render')
        self.assertEqual(rendered.returncode, 0, rendered.stdout + rendered.stderr)

    def test_local_repair_actual_transform_reapproval_and_formal_selection(self):
        from scripts import repair_state, local_transform
        self.ready()
        old_review = self.make_review()
        original = self.project / 'review.mp4'
        original_sha = digest(original)
        old = {'status': 'reviewed', 'evidence': '明确的本地媒体测试夹具', 'chapter_id': 'CH01',
               'shot_ids': ['SH001', 'SH002'], 'frame_count': 96, 'fps': 24, 'scene_id': 'gate',
               'path': original.name, 'sha256': original_sha, 'origin_receipt_path': self.generated_origin(original)}
        write(self.project / 'state/generated_clips.json', {'clips': {'OLD': old}})
        write(self.project / 'state/reviews/CH01.json', old_review)
        failure = copy.deepcopy(old_review)
        failure.update(decision='reject', batch_generation_allowed=False)
        failure['director_checks']['editing']['status'] = 'fail'
        write(self.project / 'analysis/failed-local-review.json', failure)
        plan = {'dominant_failures': [{'classification': 'actual_drift', 'dimension': 'editing',
                                      'evidence': '测试时间码覆盖', 'cause': '测试需调整数码构图'}],
                'structural_failure': False, 'action': 'surgical_repair', 'change_only': ['数码裁切构图'],
                'preserve_exactly': ['原透视、动作时序与声音'], 'permitted_local_operations': ['crop', 'scale']}
        write(self.project / 'analysis/local-repair-plan.json', plan)
        opened = repair_state.open_repair(self.project, clip_id='OLD', plan_path='analysis/local-repair-plan.json',
                                          failure_review_path='analysis/failed-local-review.json')
        transform_plan = {'source_clip_id': 'OLD', 'replacement_clip_id': 'LOCAL',
                          'repair_id': opened['repair_id'], 'repair_plan_sha256': opened['plan_sha256'],
                          'frame_ranges': [{'start_frame': 0, 'end_frame': 96}],
                          'crop': {'x': 0, 'y': 0, 'width': 256, 'height': 144},
                          'output_width': 320, 'output_height': 180, 'scale_method': 'lanczos',
                          'audio_policy': 'preserve_selected_source', 'reason': '测试裁切并如实标注放大'}
        write(self.project / 'analysis/transform-plan.json', transform_plan)
        clip = local_transform.run(self.project, 'analysis/transform-plan.json')
        self.assertEqual(clip['status'], 'pending')
        self.assertFalse(approved(self.project)[0])
        source = self.project / clip['path']
        reviewed = copy.deepcopy(old_review)
        reviewed.update(reviewed_media=clip['path'], reviewed_media_sha256=digest(source),
                        dependencies=dependency_snapshot(self.project, 'CH01'))
        write(self.project / 'state/reviews/CH01.json', reviewed)
        write(self.project / 'state/director_review.json', reviewed)
        self.assertTrue(approved(self.project, 'CH01')[0], approved(self.project, 'CH01')[1])
        repair_state.close(self.project, opened['repair_id'], replacement_clip_id='LOCAL')
        current_clip = json.loads((self.project / 'state/generated_clips.json').read_text(encoding='utf8'))['clips']['LOCAL']
        self.assertEqual(current_clip['status'], 'reviewed')
        selections = [{'shot_id': f'SH00{i+1}', 'clip_id': 'LOCAL', 'in_frame': i*48, 'out_frame': (i+1)*48,
                       'edit_reason': '测试修复后按音乐窗口选段', 'state_check': '测试原动作时序保留'} for i in range(2)]
        write(self.project / 'state/edit_selection.json', {'selections': selections})
        rendered = command('edit_assemble.py', self.project, '--render')
        self.assertEqual(rendered.returncode, 0, rendered.stdout + rendered.stderr)
        self.assertEqual(digest(original), original_sha)

    def test_mcp_broker_rechecks_real_preflight_before_host_claim(self):
        from scripts import mcp_broker as broker, submission_jobs as jobs
        import mcp_fixtures
        self.ready(max_duration=15)
        path, contract = mcp_fixtures.contract(self.project, self.contract)
        self.assertEqual(validate_submission_contract(contract, self.project), [])
        job = jobs.prepare(self.project, path)
        worker, box, cid = mcp_fixtures.start(lambda: broker.run_dispatch(self.project, job['job_id'], timeout_sec=30), self.project)
        receipt = self.project / 'state/executions/crew_rehearsal.json'
        old = receipt.read_bytes()
        receipt.unlink()
        with self.assertRaises(ValueError): broker.claim(self.project, cid)
        self.assertEqual(broker.load(broker.root_for(self.project, cid)/'call.json')['status'], 'awaiting_mcp')
        receipt.write_bytes(old)
        claimed = broker.claim(self.project, cid)
        self.assertEqual(claimed['arguments'], contract['provider_request'])
        mcp_fixtures.send(self.project, claimed, mcp_fixtures.result(contract))
        pending = mcp_fixtures.complete(worker, box)
        self.assertEqual(pending['status'], 'pending')
        worker, box, cid = mcp_fixtures.start(lambda: broker.run_poll(self.project, job['job_id'], timeout_sec=30), self.project)
        claimed = broker.claim(self.project, cid)
        mcp_fixtures.send(self.project, claimed, mcp_fixtures.result(contract, 'completed', ids=False))
        completed = mcp_fixtures.complete(worker, box)
        self.assertEqual(completed['status'], 'completed')
        self.assertEqual(completed['provider_task_id'], pending['provider_task_id'])
        self.assertEqual(completed['response']['task_binding_kind'], 'request_context')
        self.assertFalse((self.project / 'state/generated_clips.json').exists())
        self.assertFalse(check_delivery(self.project, 'final_mv.mp4')['ready_for_delivery'])

    def test_A34_documented_boundary_is_discoverable(self):
        self.assertTrue((ROOT / 'references/production-hardening.md').is_file())
        self.assertIn('references/production-hardening.md', (ROOT / 'SKILL.md').read_text(encoding='utf-8'))

    def test_A36_preview_generated_but_failed_verification_exits_nonzero(self):
        (self.project / 'input').mkdir()
        ffmpeg('-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=5',
               '-ac', '2', self.project / 'input/song.wav')
        result = command('render_preview.py', self.project, '--width', 160, '--height', 90)
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertTrue((self.project / 'output/mv_preview.mp4').is_file())
        report = json.loads((self.project / 'output/verification_report.json').read_text(encoding='utf-8'))
        self.assertEqual(report['overall'], 'FAIL')


if __name__ == '__main__':
    unittest.main()
