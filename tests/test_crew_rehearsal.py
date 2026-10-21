import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from test_visual_workflow import fixture
from scripts.storyboard_build import build as storyboard_build
from scripts.crew_rehearsal import analyze, render_html, build
from scripts.pipeline_contracts import RevisionConflict


class CrewRehearsalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        _, _, _, self.dev, _ = fixture(self.project)
        self.sb = storyboard_build(self.project)

    def test_valid_design_does_not_auto_approve_or_apply_style(self):
        report = analyze(self.dev, self.sb)
        self.assertFalse(report['artistic_approval'])
        self.assertFalse(report['director_stylization']['applied'])
        self.assertTrue(report['dynamic_review_required'])
        self.assertTrue(all(not c['watched'] for c in report['cuts']))

    def test_legacy_missing_crew_rework_before_style(self):
        for shot in self.sb['shots']: shot.pop('crew')
        report = analyze(self.dev, self.sb)
        self.assertEqual(report['status'], 'structural_rework_required')
        self.assertFalse(report['director_stylization']['eligible_for_routing'])
        self.assertEqual({t['owner'] for t in report['department_rework']},
                         {'screenwriting', 'directing', 'production_design', 'lighting',
                          'character_design', 'blocking', 'cinematography', 'editing'})

    def test_cross_scene_and_time_jump_do_not_require_continuous_actor_path(self):
        self.sb['shots'][1]['scene_id'] = 'elsewhere'
        self.assertFalse(analyze(self.dev, self.sb)['cuts'][0]['continuous_actor_path_required'])
        self.sb['shots'][1]['scene_id'] = self.sb['shots'][0]['scene_id']
        self.sb['shots'][1]['crew']['editing']['time_relation'] = 'flashback'
        self.assertFalse(analyze(self.dev, self.sb)['cuts'][0]['continuous_actor_path_required'])

    def test_repeated_information_is_proposal_not_automatic_rewrite(self):
        self.sb['shots'][1]['crew']['screenwriting']['audience_gain'] = self.sb['shots'][0]['crew']['screenwriting']['audience_gain']
        before = copy.deepcopy(self.sb)
        report = analyze(self.dev, self.sb)
        self.assertTrue(any(t.get('heuristic_only') for t in report['department_rework']))
        self.assertEqual(self.sb, before)

    def test_html_escapes_design_text(self):
        self.sb['shots'][0]['visual_center'] = '<script>alert(1)</script>'
        page = render_html(analyze(self.dev, self.sb))
        self.assertNotIn('<script>', page)
        self.assertIn('&lt;script&gt;', page)

    def test_duplicate_id_rejected_before_report(self):
        self.sb['shots'][1]['id'] = self.sb['shots'][0]['id']
        with self.assertRaises(ValueError): analyze(self.dev, self.sb)

    def test_report_build_has_sources_and_cannot_publish_old_snapshot(self):
        report = build(self.project)
        self.assertIn('input_fingerprints', report)
        self.assertGreater(len(report['director_library']['directors']), 10)
        output = self.project/'gates/crew_rehearsal.json'
        previous = output.read_bytes()
        with patch('scripts.crew_rehearsal.atomic_batch', side_effect=RevisionConflict('输入变化')):
            with self.assertRaises(RevisionConflict): build(self.project)
        self.assertEqual(output.read_bytes(), previous)
