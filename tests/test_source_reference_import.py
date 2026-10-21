import tempfile
import unittest
import zipfile
from pathlib import Path
from scripts.source_reference_import import pairs, moviechat, advene_package


class SourceReferenceImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def put(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')
        return path

    def corpus(self, scene='0 1\n'):
        self.put('data/metadata/video_type.csv', 'video_id,type\n1,movie\n')
        self.put('data/shot/1.txt', '0 0\n1 24\n')
        self.put('data/scene/1.txt', scene)

    def test_inclusive_single_frame_and_no_guessed_fps(self):
        self.corpus()
        report, evidence = moviechat(self.root)
        self.assertEqual(report['totals'], {'videos': 1, 'shots': 2, 'scenes': 1})
        self.assertEqual(report['by_video_type']['movie']['shot_lengths_quantiles']['p50'], 12.5)
        self.assertFalse(report['seconds_statistics_available'])
        self.assertIsNone(report['independent_film_count'])
        self.assertEqual(len(evidence), 3)

    def test_gap_and_overlap_rejected(self):
        for text in ('0 2\n4 5', '0 2\n2 5', '1 2', '0 -1'):
            with self.assertRaises(ValueError): pairs(self.put('pairs.txt', text))

    def test_scene_must_cover_all_shots(self):
        self.corpus('0 0\n')
        with self.assertRaises(ValueError): moviechat(self.root)

    def test_category_ids_cannot_be_duplicated(self):
        self.corpus()
        self.put('data/metadata/video_type.csv', 'video_id,type\n1,movie\n1,tvseries\n')
        with self.assertRaises(ValueError): moviechat(self.root)

    def package(self, xml):
        path = self.root / 'manual.azp'
        with zipfile.ZipFile(path, 'w') as archive: archive.writestr('content.xml', xml)
        return path

    def test_semantic_intervals_not_required_to_form_shot_partition(self):
        xml = '<package xmlns="http://experience.univ-lyon1.fr/advene/ns"><annotations>'
        xml += '<annotation type="#CameraMovement"><millisecond-fragment begin="100" end="900"/></annotation>'
        xml += '<annotation type="#FieldSize"><millisecond-fragment begin="200" end="600"/></annotation>'
        xml += '<annotation type="#Shot"><millisecond-fragment begin="5" end="5"/></annotation>'
        xml += '</annotations></package>'
        counts, invalid = advene_package(self.package(xml))
        self.assertEqual(dict(counts), {'CameraMovement': 1, 'FieldSize': 1})
        self.assertEqual(invalid, 1)

    def test_XML_entities_rejected(self):
        with self.assertRaises(ValueError):
            advene_package(self.package('<!DOCTYPE package [<!ENTITY x "unsafe">]><package/>'))
