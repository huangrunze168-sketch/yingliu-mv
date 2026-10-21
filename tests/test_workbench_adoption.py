"""真实结构编译/原子采纳、审批撤销、过期与失败保留；夹具不认证实际审片。"""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from scripts.review_workbench import build, import_draft
from scripts.workbench_adoption import adopt, DESIGNS
from test_visual_workflow import fixture, write


class WorkbenchAdoptionTests(unittest.TestCase):
    def test_legacy_export_does_not_silently_delete_existing_interlude(self):
        timeline_path = self.project/'state/timeline.json'
        value = json.loads(timeline_path.read_text(encoding='utf8'))
        value['lines'][0]['end'] = 1.5
        value['lines'].insert(1, {'index':3,'type':'interlude','text':'','start':1.5,'end':2,
                                  'observation':'隔离测试既有间奏'})
        write(timeline_path,value)
        self.bundle = self.root/'new-bundle'
        build(self.project,self.bundle)
        session = json.loads((self.bundle/'session.json').read_text(encoding='utf8'))
        self.payload.update(session_id=session['session_id'],lines=session['timeline']['lines'])
        for row in self.payload['lines']:
            row['observation']='隔离测试候选，不认证听辨'
        result=adopt(self.project,self.bundle,self.imported(),scope='timing')
        updated=json.loads(timeline_path.read_text(encoding='utf8'))
        gaps=[r for r in updated['lines'] if r['type']=='interlude']
        self.assertEqual([(r['start'],r['end']) for r in gaps],[(1.5,2)])
        self.assertFalse(result['production_allowed'])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root/'project'
        fixture(self.project)
        (self.project/'input').mkdir(exist_ok=True)
        (self.project/'input/song.wav').write_bytes(b'explicit-isolated-audio-fixture-not-decoded')
        (self.project/'input/lyrics.txt').write_text('第一句\n第二句', encoding='utf-8')
        path = self.project/'state/timeline.json'
        self.timeline = json.loads(path.read_text(encoding='utf-8'))
        self.timeline['master_audio'] = 'input/song.wav'
        for row in self.timeline['lines']:
            row['speaker'] = '测试演唱者'
        write(path, self.timeline)
        write(self.project/'state/timing_review.json', {'decision': 'pending', 'lines': self.timeline['lines']})
        self.bundle = self.root/'bundle'
        build(self.project, self.bundle)
        self.session = json.loads((self.bundle/'session.json').read_text(encoding='utf-8'))
        self.payload = {'session_id': self.session['session_id'], 'decision': 'pending',
                        'lines': copy.deepcopy(self.session['timeline']['lines']),
                        'design_drafts': copy.deepcopy(self.session['design_drafts'])}

    def imported(self):
        path = self.root/'draft.json'
        path.write_text(json.dumps(self.payload), encoding='utf-8')
        return import_draft(self.project, self.bundle, path)

    def test_real_design_compile_and_approval_invalidation(self):
        self.payload['design_drafts']['development']['treatment']['concept'] = '显式隔离测试的新设计'
        record = self.imported()
        review = self.project/'state/director_review.json'
        write(review, {'decision': 'approve', 'watched_full_duration': True, 'batch_generation_allowed': True})
        before = review.read_bytes()
        result = adopt(self.project, self.bundle, record, scope='design')
        self.assertEqual(result['status'], 'adopted_pending_review')
        self.assertFalse(result['production_allowed'])
        self.assertEqual(json.loads(review.read_text(encoding='utf-8'))['decision'], 'pending')
        archive = self.project/'state/adoption_history'/result['adoption_id']/'state/director_review.json'
        self.assertEqual(archive.read_bytes(), before)
        sb = json.loads((self.project/'state/storyboard.json').read_text(encoding='utf-8'))
        self.assertEqual(len(sb['shots']), 2)
        edit = json.loads((self.project/'state/visual_edit_plan.json').read_text(encoding='utf-8'))
        self.assertFalse(edit['director_cut_approved'])

    def test_invalid_partial_design_cannot_publish_or_revoke(self):
        self.payload['design_drafts'].pop('visual_edit_plan')
        record = self.imported()
        before = {p:p.read_bytes() for p in (self.project/'state').glob('*.json')}
        with self.assertRaisesRegex(ValueError, '六份设计'):
            adopt(self.project, self.bundle, record, scope='design')
        self.assertEqual(before, {p:p.read_bytes() for p in before})

    def test_failed_creative_contract_preserves_old_files(self):
        self.payload['design_drafts']['story_plan']['shots'][0]['current_action'] = ''
        record = self.imported()
        before = (self.project/'state/story_plan.json').read_bytes()
        with self.assertRaises(ValueError):
            adopt(self.project, self.bundle, record, scope='design')
        self.assertEqual(before, (self.project/'state/story_plan.json').read_bytes())
        self.assertFalse((self.project/'state/adoptions').exists())

    def test_timing_correction_is_pending_not_self_approved(self):
        for row in self.payload['lines']:
            row['observation'] = '隔离测试边界记录，不认证实际听辨'
        self.payload['lines'][0]['end'] = 2.5
        self.payload['lines'][1]['start'] = 3
        self.payload['interludes'] = [{'start': 2.5, 'end': 3, 'observation': '隔离间奏区间'}]
        record = self.imported()
        result = adopt(self.project, self.bundle, record, scope='timing')
        timeline = json.loads((self.project/'state/timeline.json').read_text(encoding='utf-8'))
        self.assertEqual(len(timeline['lines']), 3)
        self.assertFalse(timeline['production_timing_verified'])
        self.assertTrue(timeline['pending_review'])
        self.assertNotIn([2.5, 3], timeline['unclassified_audio_ranges'])
        self.assertEqual(result['status'], 'adopted_pending_review')
        review = json.loads((self.project/'state/timing_review.json').read_text(encoding='utf-8'))
        self.assertFalse(review['reviewed_full_audio'])
        self.assertEqual(review['decision'], 'pending')

    def test_overlap_and_unobserved_rows_cannot_be_adopted(self):
        record = self.imported()
        with self.assertRaisesRegex(ValueError, '边界复核说明'):
            adopt(self.project, self.bundle, record, scope='timing')
        for row in self.payload['lines']:
            row['observation'] = '隔离说明'
        self.payload['lines'][1]['start'] = 1
        record = self.imported()
        with self.assertRaisesRegex(ValueError, '重叠'):
            adopt(self.project, self.bundle, record, scope='timing')

    def test_changed_source_rejects_old_record(self):
        record = self.imported()
        (self.project/'input/lyrics.txt').write_text('替换原词', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '过期'):
            adopt(self.project, self.bundle, record, scope='design')


if __name__ == '__main__':
    unittest.main()
