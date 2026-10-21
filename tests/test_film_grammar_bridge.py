"""真实标注提取、叙事检索、连续性阻断及导演提示词交接。"""
import copy
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from scripts import film_grammar_bridge as bridge
from scripts.storyboard_build import build
from test_visual_workflow import fixture,write


class FilmBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.project=Path(self.tmp.name).resolve();fixture(self.project);build(self.project)
        self.repo=self.project/'ada';directory=self.repo/'annotations/manual-annotations_advene_azp_files';directory.mkdir(parents=True)
        self.package=directory/'fixture.azp'
        xml='''<package xmlns="http://experience.univ-lyon1.fr/advene/ns"><annotations>
        <annotation id="s1" type="#Shot"><millisecond-fragment begin="0" end="2000"/><content>1</content></annotation>
        <annotation id="s2" type="#Shot"><millisecond-fragment begin="2000" end="4000"/><content>2</content></annotation>
        <annotation id="f1" type="#FieldSize"><millisecond-fragment begin="0" end="2000"/><content>long shot</content></annotation>
        <annotation id="f2" type="#FieldSize"><millisecond-fragment begin="2000" end="4000"/><content>closeup</content></annotation>
        <annotation id="private" type="#DialogueText"><millisecond-fragment begin="0" end="4000"/><content>DO NOT COPY DIALOGUE</content></annotation>
        </annotations></package>'''
        with zipfile.ZipFile(self.package,'w') as z:z.writestr('content.xml',xml)
        self.index=self.project/'index.json';bridge.index(self.repo,self.index)

    def test_workflow_requires_structure_only_when_enabled(self):
        from unittest.mock import patch
        from scripts import workflow_run
        self.assertEqual(bridge.require(self.project), {})
        with self.assertRaisesRegex(ValueError, '尚未选择镜头组织方案'):
            bridge.require(self.project, required=True)
        with patch.object(workflow_run.workflow_audit, 'audit', return_value={'checks': []}):
            self.assertEqual(workflow_run.check_step(self.project, 4, {}), [])
            errors=workflow_run.check_step(self.project, 4, {'creative_workflow': {'film_grammar_required': True}})
            self.assertTrue(any('电影镜头组织交接未完成' in error for error in errors))

    def test_project_policy_blocks_direct_compilation_without_structure(self):
        from scripts import workflow_audit
        write(self.project/'state/creative_workflow.json', {'film_grammar_required': True})
        with self.assertRaisesRegex(ValueError, '尚未选择镜头组织方案'):
            bridge.require(self.project)
        report=workflow_audit.audit(self.project, phase='before-generation')
        check=next(row for row in report['checks'] if row['id']=='film_organization')
        self.assertEqual(check['status'], 'blocked')
        script=Path(bridge.__file__).with_name('prompt_build.py')
        result=subprocess.run([sys.executable,str(script),str(self.project)],capture_output=True,text=True,encoding='utf8')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('尚未选择镜头组织方案',result.stdout+result.stderr)

    def test_invalid_project_policy_does_not_disable_requirement(self):
        write(self.project/'state/creative_workflow.json', {'film_grammar_required': 'false'})
        with self.assertRaisesRegex(ValueError, '策略格式无效'):
            bridge.require(self.project)

    def test_structure_precedes_director_and_changes_require_rebinding(self):
        from scripts import organization_workflow as workflow
        _,old=self.draft()
        rows=old['shots']
        for row in rows:
            row['source_id']=row['source_evidence']['id'];row['axis_side']='south'
        rows[1].update(incoming_relation='ellipsis',transition_reason='隔离测试显式跳时')
        vendor=Path(bridge.__file__).resolve().parents[1]/'vendor/zy-cinematic-realism'
        source=vendor/'SKILL.md'
        beats=self.project/'beats.json'
        write(beats,{'method':'zy_story_beats','zy_sources':[{'path':'SKILL.md','sha256':bridge.digest(source)}],'shots':rows})
        saved={name:(self.project/'state'/name).read_bytes() for name in ('story_plan.json','visual_edit_plan.json','sequence_direction.json','development.json')}
        for name in saved:(self.project/'state'/name).unlink()
        draft=self.project/'early.json'
        plan=workflow.propose(self.project,beats,self.index,draft)
        self.assertFalse(any(str(self.project/'state'/name) in plan['inputs'] for name in saved))
        plan.update(decision='accept_structure',reviewer='隔离夹具');write(draft,plan)
        broken=copy.deepcopy(plan);broken['shots'][0]['axis_side']='pending';write(draft,broken)
        with self.assertRaisesRegex(ValueError,'轴侧'):workflow.select(self.project,draft)
        write(draft,plan)
        selected=workflow.select(self.project,draft)
        with self.assertRaises((ValueError,FileNotFoundError)):bridge.require(self.project)
        for name,data in saved.items():(self.project/'state'/name).write_bytes(data)
        legacy=self.project/'legacy.json';old.update(decision='accept_structure',reviewer='隔离测试');write(legacy,old)
        with self.assertRaisesRegex(ValueError,'前置组织结构'):bridge.adopt(self.project,legacy)
        with self.assertRaisesRegex(ValueError,'绑定'):workflow.bind(self.project)
        development=bridge.load(self.project/'state/development.json')
        development['organization_structure']=selected['binding'];write(self.project/'state/development.json',development)
        workflow.bind(self.project)
        sb=build(self.project);self.assertTrue(bridge.require(self.project,sb))
        development['execution_note']='改变导演执行';write(self.project/'state/development.json',development)
        with self.assertRaisesRegex(ValueError,'重新绑定'):bridge.require(self.project,sb)
        workflow.bind(self.project);sb=build(self.project)
        self.assertTrue(bridge.require(self.project,sb))
        write(self.project/'state/organization_structure.json',{'changed':True})
        with self.assertRaises(ValueError):bridge.require(self.project,sb)

    def draft(self):
        path=self.project/'draft.json';value=bridge.propose(self.project,self.index,path)
        return path,value

    def test_index_reads_real_ids_excludes_dialogue_and_marks_no_media_view(self):
        r=bridge.load(self.index);p=r['passages'][0]
        self.assertEqual([r['id'] for r in p['annotations']],['s1','s2','f1','f2'])
        self.assertNotIn('DO NOT COPY',self.index.read_text(encoding='utf8'))
        self.assertFalse(p['media_observed']);self.assertEqual(p['mean_shot_sec'],2)

    def test_proposal_preserves_music_and_story_and_never_auto_accepts(self):
        before=(self.project/'state/story_plan.json').read_bytes();path,draft=self.draft()
        self.assertEqual(draft['decision'],'pending');self.assertFalse(draft['artistic_approval'])
        self.assertEqual(before,(self.project/'state/story_plan.json').read_bytes())
        self.assertEqual(len(draft['shots']),2)
        with self.assertRaises(ValueError):bridge.adopt(self.project,path)

    def test_changed_corpus_and_fake_source_cannot_be_adopted(self):
        path,draft=self.draft();draft.update(decision='accept_structure',reviewer='隔离测试，不是影片批准')
        draft['shots'][0]['source_evidence']['id']='invented';write(path,draft)
        with self.assertRaisesRegex(ValueError,'来源段落'):bridge.adopt(self.project,path)

    def test_identity_switch_is_not_same_actor_teleport_and_axis_bridge_required(self):
        _,draft=self.draft();rows=draft['shots']
        for row in rows:row.update(axis_side='south',action_start='held',action_end='held',start_positions={'A':[0,0]},end_positions={'A':[0,0]})
        rows[1]['start_positions']={'B':[2,0]};rows[1]['end_positions']={'B':[2,0]}
        self.assertFalse(bridge.continuity_errors(rows))
        rows[1]['start_positions']={'A':[2,0]}
        self.assertTrue(any('位置跳变' in e for e in bridge.continuity_errors(rows)))
        rows[1]['start_positions']={'B':[2,0]};rows[1]['axis_side']='north'
        self.assertTrue(any('越轴' in e for e in bridge.continuity_errors(rows)))
        rows[1]['axis_bridge']='中性机位明确建立新轴线'
        self.assertFalse(bridge.continuity_errors(rows))

    def test_explicit_narrative_beats_select_method_without_moving_cuts(self):
        beats=self.project/'beats.json';write(beats,{'beats':[{'id':'exchange','story_goal':'交锋以后观察对手反应','method':'exchange','shot_ids':['SH001','SH002']}]})
        draft=bridge.propose(self.project,self.index,self.project/'beats-draft.json',beats_path=beats)
        self.assertEqual({s['method'] for s in draft['shots']},{'exchange'})
        self.assertNotEqual(draft['shots'][0]['editorial_role_proposal'],draft['shots'][1]['editorial_role_proposal'])
        self.assertEqual(draft['shots'][0]['music_refs']['start'],'song:start')

    def test_changed_actual_annotation_blocks_stale_proposal(self):
        path,draft=self.draft();draft.update(decision='accept_structure',reviewer='test-only')
        for row in draft['shots']:row['axis_side']='south'
        draft['shots'][1].update(incoming_relation='ellipsis',transition_reason='显式测试跳时')
        write(path,draft)
        with zipfile.ZipFile(self.package,'a') as z:z.writestr('changed.txt','version changed')
        with self.assertRaisesRegex(ValueError,'依赖已过期'):bridge.adopt(self.project,path)

    def test_narrative_beats_cannot_drop_or_repeat_shots(self):
        beats=self.project/'beats.json';write(beats,{'beats':[{'id':'bad','story_goal':'测试','shot_ids':['SH001','SH001']}]})
        with self.assertRaisesRegex(ValueError,'覆盖全部镜头'):bridge.propose(self.project,self.index,self.project/'invalid.json',beats_path=beats)
        self.assertFalse((self.project/'invalid.json').exists())

    def test_pending_preview_compiles_without_adopting_story_or_allowing_dispatch(self):
        script=Path(bridge.__file__).with_name('prompt_build.py')
        result=subprocess.run([sys.executable,str(script),str(self.project)],capture_output=True,text=True,encoding='utf8')
        self.assertEqual(result.returncode,0,result.stderr)
        path,draft=self.draft()
        for row in draft['shots']:row['axis_side']='south'
        draft['shots'][1].update(incoming_relation='ellipsis',transition_reason='隔离测试显式跳时')
        write(path,draft);before=(self.project/'state/story_plan.json').read_bytes()
        handoff=bridge.preview(self.project,path,self.project/'handoff.json')
        self.assertFalse(handoff['dispatch_allowed']);self.assertEqual(handoff['decision'],'pending')
        self.assertIn('镜头组织',handoff['packets'][0]['prompt'])
        self.assertEqual(before,(self.project/'state/story_plan.json').read_bytes())

    def test_adopt_rebuild_and_prompt_compilation_preserve_structure(self):
        path,draft=self.draft()
        # 仅测试结构交接；不创建真实观察或影片质量结论。
        draft.update(decision='accept_structure',reviewer='test-only')
        for row in draft['shots']:row['axis_side']='south'
        if bridge.continuity_errors(draft['shots']):
            draft['shots'][1].update(incoming_relation='ellipsis',transition_reason='测试显式跳时，动作状态来自原故事')
        write(path,draft)
        result=bridge.adopt(self.project,path);self.assertFalse(result['artistic_approval'])
        sb=build(self.project);self.assertIn('film_grammar',sb['shots'][0])
        self.assertTrue(bridge.require(self.project,sb))
        script=Path(bridge.__file__).with_name('prompt_build.py')
        r=subprocess.run([sys.executable,str(script),str(self.project)],capture_output=True,text=True,encoding='utf8')
        self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        prompt=bridge.load(self.project/'prompts/SH001.json')
        self.assertEqual(prompt['direction']['film_grammar']['shot_id'],'SH001')
        motion=bridge.load(self.project/'prompts/motion/SH001.json')
        self.assertEqual(motion['film_grammar'],prompt['direction']['film_grammar'])
        from scripts.chapter_prompt_build import compile_chapter
        chapter={'id':'CH01','window':{'start':0,'end':4},'shot_deltas':[{'id':'SH001','cut_relation':'action_to_reaction'}],'continuity_lock':{}}
        prompts={sid:bridge.load(self.project/f'prompts/{sid}.json') for sid in ('SH001','SH002')}
        # 编译函数使用章节shot_ids查询已有提示词。
        chapter['shot_ids']=['SH001','SH002']
        packet=compile_chapter(chapter,prompts,'16:9',False)
        self.assertIn('镜头组织',packet['prompt'])
        self.assertEqual(packet['beats'][0]['film_grammar']['shot_id'],'SH001')
        from scripts.omni_reference import build_omni_reference_blueprint
        chapter.update(duration_sec=4,audio_asset={'local_path':'audio.wav','sha256':'sha256:'+'1'*64})
        blueprint=build_omni_reference_blueprint(chapter,packet['beats'],protagonist_source=None,
                    keyframe_sources={sid:{'local_path':sid+'.png','sha256':'sha256:'+'2'*64} for sid in chapter['shot_ids']})
        self.assertEqual(blueprint['timeline'][0]['film_grammar'],packet['beats'][0]['film_grammar'])


if __name__=='__main__':unittest.main()
