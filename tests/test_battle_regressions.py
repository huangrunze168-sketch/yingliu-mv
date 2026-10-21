"""设备名误判、多人物参考碰撞与伪机器时间来源的回归。"""
import unittest
from scripts.gate_checks import nonlip_singing_marker
from scripts.omni_reference import build_omni_reference_blueprint
import test_workbench_adoption as adoption_tests


class BattleRegressions(unittest.TestCase):
    def test_equipment_and_negation_do_not_hide_active_singing(self):
        for text in ('DJ推唱盘，唱针接触唱片', '对手不跟唱', '禁止开口唱'):
            self.assertIsNone(nonlip_singing_marker(text), text)
        for text in ('不要停止唱歌', '不跟唱，然后开口唱', '主唱继续演唱'):
            self.assertIsNotNone(nonlip_singing_marker(text), text)

    def test_two_identities_do_not_collide_with_keyframe(self):
        beat = dict(shot_id='SH001', keyframe_handle='@图片 3', start_sec=0, end_sec=2,
                    story_change='反攻', current_action='抬麦', camera_witness='南侧近景')
        result = build_omni_reference_blueprint(
            {'id':'C1','window':{'start':0,'end':2}}, [beat], protagonist_source=None,
            character_sources=[{'character':name,'source':{'local_path':name+'.png'}}
                               for name in ('诸葛亮','周瑜')],
            keyframe_sources={'SH001':{'local_path':'keyframe.png'}})
        self.assertEqual([s['subject_id'] for s in result['subject_definitions']], ['诸葛亮','周瑜'])
        handles = [r['handle'] for r in result['reference_bindings']]
        self.assertEqual(len(handles), len(set(handles)))
        self.assertEqual(result['timeline'][0]['reference_handles'][:2], ['@图片 1','@图片 2'])


class MachineTimingRegressions(unittest.TestCase):
    def test_missing_machine_job_cannot_change_timeline(self):
        from scripts.workbench_adoption import adopt
        fixture = adoption_tests.WorkbenchAdoptionTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        for row in fixture.payload['lines']:
            row['observation'] = '未获批准的机器候选'
        fixture.payload['machine_timing_source'] = {'job_path':'state/forced_alignment_jobs/missing.json'}
        record = fixture.imported()
        timeline = fixture.project/'state/timeline.json'
        before = timeline.read_bytes()
        with self.assertRaises((ValueError, OSError)):
            adopt(fixture.project, fixture.bundle, record, scope='timing')
        self.assertEqual(timeline.read_bytes(), before)
