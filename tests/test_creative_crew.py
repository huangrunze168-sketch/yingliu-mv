import copy
import tempfile
import unittest
from pathlib import Path
from test_visual_workflow import fixture, ROOT
from scripts.storyboard_build import build
from scripts.crew_execution import execution_lines, scene_execution, cut_rehearsal
from scripts.director_catalog import build_catalog, AXES


class CreativeCrewTests(unittest.TestCase):
    def test_all_departments_compile_actual_values_without_mutation(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            fixture(project)
            shot = build(project)['shots'][0]
            original = copy.deepcopy(shot['crew'])
            lines = execution_lines(shot['crew'])
            self.assertEqual(len(lines), 8)
            self.assertTrue(any(shot['crew']['lighting']['source_used'] in line for line in lines))
            self.assertTrue(any(shot['crew']['editing']['cut_motivation'] in line for line in lines))
            self.assertEqual(shot['crew'], original)

    def test_scene_prompt_actually_contains_art_and_lighting_facts(self):
        master = {'master_prompt': '已锁场景', 'art_direction': {'hero_prop': '折断的木梳'},
                  'lighting_plan': {'primary_source': '门外阴天散射光'}}
        result = scene_execution(master)
        self.assertIn('折断的木梳', result)
        self.assertIn('门外阴天散射光', result)
        self.assertTrue(result.startswith(master['master_prompt']))

    def test_cut_handoff_preserves_cross_scene_facts_and_never_approves(self):
        with tempfile.TemporaryDirectory() as folder:
            project = Path(folder)
            fixture(project)
            shots = build(project)['shots']
            shots[1]['scene_id'] = 'another-place'
            cuts = cut_rehearsal(shots)
            self.assertEqual(len(cuts), len(shots)-1)
            self.assertFalse(cuts[0]['same_scene'])
            self.assertFalse(cuts[0]['watch_verified'])
            self.assertEqual(cuts[0]['attention_transfer']['to'], shots[1]['visual_center'])

    def test_real_catalog_discovers_whole_index_not_three_presets(self):
        catalog = build_catalog(ROOT/'vendor/zy-cinematic-realism/references/directors')
        self.assertGreater(len(catalog['directors']), 10)
        aliases = [alias for d in catalog['directors'] for alias in d['aliases']]
        self.assertIn('王家卫', aliases)
        self.assertIn('胡金铨', aliases)
        self.assertIn('侯孝贤', aliases)
        self.assertFalse(catalog['style_quality_verified'])

    def catalog_folder(self, path, file='new-director.md'):
        (path/'index.md').write_text(f'- 新导演 / New Director\n  → [New Director]({file})\n', encoding='utf-8')

    def test_new_index_entry_is_discovered_without_hardcoded_name(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            self.catalog_folder(path)
            (path/'new-director.md').write_text('# New Director\n'+ '\n'.join('## '+a for a in AXES), encoding='utf-8')
            self.assertEqual(build_catalog(path)['directors'][0]['name'], 'New Director')

    def test_incomplete_or_missing_director_not_fabricated(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)
            self.catalog_folder(path)
            with self.assertRaises(FileNotFoundError): build_catalog(path)
            (path/'new-director.md').write_text('# Incomplete', encoding='utf-8')
            with self.assertRaises(ValueError): build_catalog(path)
