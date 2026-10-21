"""真实《笼》工作流发现的动作传递反例；不作为艺术质量验收。"""
import unittest
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from scripts.gate_checks import check_prompt
from scripts.storyboard_build import build
from tests.test_visual_workflow import fixture


class ActionTransferTests(unittest.TestCase):
    def check_action(self, performance, moment):
        shot = {'current_action': '指尖压住不能移动的铁门把',
                'keyframe_moment': '门把停在最低处而门未开'}
        prompt = {'performance': performance,
                  'direction': {'scene_master': {'keyframe_moment': moment,
                                                'current_action': shot['current_action']}}}
        checks = check_prompt(prompt, {'duration_sec': 4, 'lines': []}, {}, False, shot)
        return next(row for row in checks if row.get('idx') == 12)

    def test_concrete_press_action_outside_old_wordlist_is_not_rejected(self):
        result = self.check_action('指尖压住不能移动的铁门把；指尖受力且门不动',
                                   '门把停在最低处而门未开')
        self.assertTrue(result['pass'])
        self.assertIn('不认证', result['evidence'])

    def test_wordlist_hit_cannot_hide_removed_action_or_changed_moment(self):
        for performance, moment in [('她轻轻抬起眼睛', '门把停在最低处而门未开'),
                                    ('指尖压住不能移动的铁门把', '改成向海走去')]:
            with self.subTest(performance=performance, moment=moment):
                self.assertFalse(self.check_action(performance, moment)['pass'])

    def test_keyframe_pack_preserves_states_props_and_reference_exclusions(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            fixture(project)
            source = project / 'state/story_plan.json'
            plan = json.loads(source.read_text(encoding='utf-8'))
            plan['shots'][0]['reference_exclusions'] = ['不能继承参考图后段已揭示的钥匙']
            source.write_text(json.dumps(plan, ensure_ascii=False), encoding='utf-8')
            storyboard = build(project)
            result = subprocess.run([sys.executable, '-m', 'scripts.prompt_build', str(project)],
                                    capture_output=True, encoding='utf-8')
            self.assertEqual(result.returncode, 0, result.stderr)
            pack = json.loads((project/'prompts/keyframes/SH001.json').read_text(encoding='utf-8'))
            shot = storyboard['shots'][0]
            self.assertEqual(pack['continuity'], {'before': shot['state_before'], 'after': shot['state_after']})
            self.assertEqual(pack['prop_state'], shot['props'])
            self.assertEqual(pack['reference_exclusions'], plan['shots'][0]['reference_exclusions'])

    def test_reference_exclusions_rejects_string_instead_of_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            fixture(project)
            source = project / 'state/story_plan.json'
            plan = json.loads(source.read_text(encoding='utf-8'))
            plan['shots'][0]['reference_exclusions'] = '不是列表'
            source.write_text(json.dumps(plan, ensure_ascii=False), encoding='utf-8')
            with self.assertRaisesRegex(ValueError, 'reference_exclusions'):
                build(project)
