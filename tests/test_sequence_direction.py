import copy
import json
import tempfile
import unittest
from pathlib import Path
from scripts.sequence_direction import validate, require
from scripts.pipeline_contracts import digest


class SequenceDirectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        source = self.root/'reference.txt'; source.write_text('真实方法文件',encoding='utf-8')
        self.plan = {'duration_sec':4,'artistic_approval':False,
            'sources':{'ref':{'path':str(source),'sha256':digest(source),'scope':'annotation_only','media_observed':False}},
            'shots':[{'id':'S1','window':[0,4],'camera_xyz':[1,2,1.5],
                      'actor_start_xy':[0,0],'actor_end_xy':[1,0],'reference_ids':['ref'],'shot_size':'近景',
                      **{k:'具体设计提案' for k in ('action','audience_gain','camera_reason','cut_reason','lighting','art','performance')}}]}

    def test_valid_design_is_not_art_approval(self):
        self.assertEqual(validate(self.plan),[])
        self.plan['artistic_approval']=True
        self.assertTrue(validate(self.plan))

    def test_changed_source_blocks(self):
        Path(self.plan['sources']['ref']['path']).write_text('修改',encoding='utf-8')
        self.assertTrue(validate(self.plan))

    def test_annotation_cannot_claim_media_observed(self):
        self.plan['sources']['ref']['media_observed']=True
        self.assertTrue(validate(self.plan))

    def test_nonfinite_coordinate_and_timing_block(self):
        for field in ('window','camera_xyz'):
            plan=copy.deepcopy(self.plan);plan['shots'][0][field][0]=float('nan')
            self.assertTrue(validate(plan))

    def test_formal_storyboard_must_keep_execution(self):
        (self.root/'state').mkdir()
        (self.root/'state/sequence_direction.json').write_text(json.dumps(self.plan),encoding='utf-8')
        formal={'id':'S1','window':{'start':0,'end':4},'sequence_direction':self.plan['shots'][0],
                'current_action':'具体设计提案','crew':{'cinematography':{'position_xy':[1,2],'shot_size':'近景'}}}
        path=self.root/'state/storyboard.json'
        path.write_text(json.dumps({'shots':[formal]}),encoding='utf-8')
        self.assertEqual(require(self.root),self.plan)
        del formal['sequence_direction']
        path.write_text(json.dumps({'shots':[formal]}),encoding='utf-8')
        with self.assertRaises(ValueError):require(self.root)
