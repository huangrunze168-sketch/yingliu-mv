import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from test_visual_workflow import fixture, write
from test_production_contracts import command
from scripts.storyboard_build import build
from scripts import prompt_build
from scripts.director_catalog import resolve_director
from scripts.visual_development import validate, VENDOR, fingerprint
from scripts.pipeline_contracts import RevisionConflict
from scripts.crew_execution import validate_motion_package


class WorkflowEntrypoints(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        _, _, _, self.dev, self.style = fixture(self.project)
        self.sb = build(self.project)

    def compile(self):
        with patch.object(sys, 'argv', ['prompt_build.py', str(self.project)]), contextlib.redirect_stdout(io.StringIO()):
            prompt_build.main()

    def test_prompt_entry_automatically_builds_crew_desk(self):
        self.compile()
        report = json.loads((self.project/'gates/crew_rehearsal.json').read_text(encoding='utf-8'))
        self.assertFalse(report['artistic_approval'])
        self.assertTrue((self.project/'gates/crew_rehearsal.html').is_file())
        self.assertTrue((self.project/'prompts/SH001.json').is_file())

    def test_missing_department_blocks_prompt_and_keeps_diagnostic(self):
        self.sb['shots'][0]['crew'].pop('lighting')
        write(self.project/'state/storyboard.json', self.sb)
        with self.assertRaisesRegex(ValueError, '基础剧组设计'): self.compile()
        self.assertTrue((self.project/'gates/crew_rehearsal.json').is_file())
        self.assertFalse((self.project/'prompts/SH001.json').exists())

    def test_changed_input_during_compile_preserves_previous_prompt(self):
        self.compile()
        path = self.project/'prompts/SH001.json'
        original = path.read_bytes()
        real = prompt_build.require_development
        def changed(*args):
            result = real(*args)
            write(self.project/'state/style.json', {**self.style, 'changed_during_compile': True})
            return result
        with patch.object(prompt_build, 'require_development', side_effect=changed):
            with self.assertRaises(RevisionConflict): self.compile()
        self.assertEqual(path.read_bytes(), original)

    def director(self, filename):
        self.style['director_ref'] = {'name': '王家卫', 'intensity': '明确'}
        self.dev['director'] = {'name': '王家卫', 'strength': '明确', 'active_axes': ['composition_space'],
            'rationale': '分离人物关系', 'source_file': f'references/directors/{filename}',
            'source_sha256': fingerprint(VENDOR/'references/directors'/filename)}
        for shot in self.sb['shots']:
            shot['director_decisions'] = {'composition_space': {'instruction': '保留既有空间',
                                                              'reason': '测试', 'observable_check': '测试'}}

    def test_name_cannot_borrow_another_real_director_file(self):
        self.director('christopher-nolan.md')
        errors = validate(self.dev, self.sb, self.style, self.project)
        self.assertTrue(any('实际方法文件不匹配' in e for e in errors))

    def test_actual_director_file_matches_route(self):
        self.director('wong-kar-wai.md')
        errors = validate(self.dev, self.sb, self.style, self.project)
        self.assertFalse(any('索引路由' in e or '实际方法文件不匹配' in e for e in errors))

    def test_alias_and_unknown_director_resolution(self):
        folder = VENDOR/'references/directors'
        self.assertEqual(resolve_director('WKW', folder)['file'], 'wong-kar-wai.md')
        with self.assertRaises(ValueError): resolve_director('不存在的导演', folder)

    def motion(self):
        self.compile()
        path = self.project/'prompts/SH001.json'
        prompt = json.loads(path.read_text(encoding='utf-8'))
        motion = json.loads((self.project/'prompts/motion/SH001.json').read_text(encoding='utf-8'))
        return path, prompt, motion

    def test_motion_preserves_all_eight_departments(self):
        path, prompt, motion = self.motion()
        self.assertEqual(motion['crew'], self.sb['shots'][0]['crew'])
        self.assertEqual(len(motion['crew_execution']), 8)
        self.assertEqual(validate_motion_package(prompt, motion, fingerprint(path)), [])

    def test_motion_lighting_removed_is_blocked(self):
        path, prompt, motion = self.motion()
        del motion['crew']['lighting']
        self.assertTrue(validate_motion_package(prompt, motion, fingerprint(path)))

    def test_motion_changed_director_or_continuity_is_blocked(self):
        path, prompt, motion = self.motion()
        motion['director_method'] = {'composition_space': '任意新机位'}
        motion['continuity'] = {'before': {}, 'after': {}}
        errors = validate_motion_package(prompt, motion, fingerprint(path))
        self.assertTrue(any('director_method' in e for e in errors))
        self.assertTrue(any('continuity' in e for e in errors))

    def test_gate5_actual_entry_rejects_stripped_motion(self):
        _, _, motion = self.motion()
        result = command('gate_checks.py', 'gate5', self.project)
        self.assertEqual(result.returncode, 0, result.stderr)
        del motion['crew']['lighting']
        write(self.project/'prompts/motion/SH001.json', motion)
        result = command('gate_checks.py', 'gate5', self.project)
        self.assertEqual(result.returncode, 1, result.stderr)
        report = json.loads((self.project/'gates/gate5.json').read_text(encoding='utf-8'))
        self.assertFalse(report['summary']['pass'])
