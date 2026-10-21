"""八项补缺的真实媒体、签名、断点恢复与未知作业反例。"""
import base64
import copy
import json
import os
import subprocess
import tempfile
import unittest
import shutil
import threading
import sys
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch
from scripts import batch_scheduler, workflow_run, release_evidence, corpus_validation, lip_measure, sound_mix
from scripts.pipeline_contracts import digest
from scripts.signed_evidence import canonical, verify


def write(path, value):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False),encoding='utf8')


class GapTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve()
        (self.root/'state').mkdir()
        (self.root/'output').mkdir()

    def signer(self):
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
        key=Ed25519PrivateKey.generate()
        public=base64.b64encode(key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw)).decode()
        store=self.root/'trusted-reviewers.json'
        write(store,{'reviewers':{'test-only':{'public_key':public,'purposes':['lip_measure','film_corpus']}}})
        self.enterContext(patch.dict(os.environ,{'MV_REVIEW_TRUST_STORE':str(store)}))
        return lambda payload:{'key_id':'test-only','payload':payload,'signature':base64.b64encode(key.sign(canonical(payload))).decode()}

    def media(self):
        source=self.root/'output/source.mp4'
        master=self.root/'audio.wav'
        subprocess.run(['ffmpeg','-nostdin','-v','error','-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=1',
                        '-c:a','pcm_s24le',str(master)],capture_output=True,check=True)
        subprocess.run(['ffmpeg','-nostdin','-v','error','-f','lavfi','-i','color=c=blue:s=64x64:r=24:d=1','-i',str(master),
                        '-c:v','libx264','-pix_fmt','yuv420p','-c:a','alac',str(source)],capture_output=True,check=True)
        return source,master

    def test_signature_wrong_purpose_revocation_and_tampering_fail(self):
        sign=self.signer()
        envelope=sign({'purpose':'film_corpus','test_fixture':True})
        self.assertEqual(verify(envelope,'film_corpus')['test_fixture'],True)
        with self.assertRaises(ValueError): verify(envelope,'lip_measure')
        envelope['payload']['test_fixture']=False
        with self.assertRaises(Exception): verify(envelope,'film_corpus')
        store=self.root/'trusted-reviewers.json'
        value=json.loads(store.read_text(encoding='utf8'));value['reviewers']['test-only']['revoked']=True;write(store,value)
        with self.assertRaises(ValueError): verify(sign({'purpose':'film_corpus'}),'film_corpus')

    def job(self, name, status, task=None):
        job={'job_id':name,'chapter_id':name[:4],'project_id':str(self.root),'status':status,'provider_task_id':task}
        write(batch_scheduler.jobs.job_path(self.root,name),job)
        return job

    def enable(self):
        write(self.root/'state/execution_policy.json',{'project_id':str(self.root),'video_backend_access':True,'instruction':'仅测试夹具，无外部调用'})

    def test_backend_disabled_never_invokes_queue_callbacks(self):
        job=self.job('a'*32,'prepared')
        batch_scheduler.init(self.root,[job['job_id']])
        with self.assertRaises(ValueError): batch_scheduler.tick(self.root,dispatch=lambda *a:self.fail('禁止派发'),poll=lambda *a:self.fail('禁止回查'))

    def test_unknown_no_task_never_redispatches_and_backoff_does_not_slide(self):
        self.enable()
        a=self.job('a'*32,'unknown')
        b=self.job('b'*32,'pending','original-task')
        batch_scheduler.init(self.root,[a['job_id'],b['job_id']],concurrency=2)
        calls=[]
        def lookup(project,job_id):
            calls.append(job_id)
            return json.loads(batch_scheduler.jobs.job_path(project,job_id).read_text(encoding='utf8'))
        first=batch_scheduler.tick(self.root,dispatch=lambda *a:self.fail('未知任务不得重派'),poll=lookup,now=100)
        due=first['jobs'][1]['next_poll']
        second=batch_scheduler.tick(self.root,dispatch=lambda *a:self.fail('不得重派'),poll=lookup,now=101)
        self.assertEqual(len(calls),1)
        self.assertEqual(second['jobs'][1]['next_poll'],due)
        self.assertEqual(second['status'],'needs_attention')

    def test_prepared_queue_dispatches_once_then_recovers_completed_status(self):
        self.enable()
        job=self.job('c'*32,'prepared')
        batch_scheduler.init(self.root,[job['job_id']])
        calls=[]
        def dispatch(project,job_id):
            calls.append(job_id)
            job['status']='completed';job['provider_task_id']='original'
            write(batch_scheduler.jobs.job_path(project,job_id),job)
            return job
        batch_scheduler.tick(self.root,dispatch=dispatch,poll=lambda *a:self.fail('已完成不回查'),now=1)
        batch_scheduler.tick(self.root,dispatch=dispatch,poll=lambda *a:self.fail('已完成不回查'),now=2)
        self.assertEqual(len(calls),1)
        self.assertEqual(batch_scheduler.require_completed(self.root)['status'],'completed')

    def test_queue_runs_network_callbacks_concurrently(self):
        self.enable()
        records=[self.job(c*32,'prepared') for c in ('e','f')]
        batch_scheduler.init(self.root,[r['job_id'] for r in records],concurrency=2)
        barrier=threading.Barrier(2)
        def dispatch(project,job_id):
            barrier.wait(timeout=3)
            path=batch_scheduler.jobs.job_path(project,job_id)
            job=json.loads(path.read_text(encoding='utf8'));job['status']='completed';write(path,job)
            return job
        result=batch_scheduler.tick(self.root,dispatch=dispatch,poll=lambda *a:self.fail('首次派发不回查'))
        self.assertEqual(result['status'],'completed',result)

    def plan(self):
        stages=[]
        for i in range(1,13):
            path=f'state/stage{i}.json';write(self.root/path,{'stage':i})
            stages.append({'step':i,'commands':[],'inputs':[],'outputs':[path]})
        plan={'project_id':str(self.root),'steps':stages}
        path=self.root/'plan.json';write(path,plan)
        return path,plan

    def approve_pending(self, plan_path, state):
        from scripts import workflow_approval
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
        if not hasattr(self, '_workflow_key'):
            self._workflow_key = Ed25519PrivateKey.generate()
            trust_dir = tempfile.TemporaryDirectory()
            self.addCleanup(trust_dir.cleanup)
            trust = Path(trust_dir.name) / 'reviewers.json'
            public = base64.b64encode(self._workflow_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)).decode()
            write(trust, {'reviewers': {'workflow-test': {'public_key': public, 'purposes': ['workflow_step']}}})
            self.enterContext(patch.dict(os.environ, {'MV_REVIEW_TRUST_STORE': str(trust),
                              'MV_WORKFLOW_TRUST_STORE_SHA256': digest(trust)}))
        payload = workflow_approval.request(self.root, plan_path, state['waiting_step'])
        payload.update(decision='approve', observer='隔离测试复核者', evidence='仅测试签名，不是实际项目批准')
        envelope = {'key_id': 'workflow-test', 'payload': payload,
                    'signature': base64.b64encode(self._workflow_key.sign(canonical(payload))).decode()}
        write(workflow_approval.approval_path(self.root, state['waiting_step']), envelope)

    def run_with_test_approvals(self, path):
        for _ in range(13):
            result = workflow_run.run(self.root, path)
            if result['status'] != 'waiting_approval':
                return result
            self.approve_pending(path, result)
        self.fail('十二步测试批准后仍未完成')

    def test_creative_policy_persists_before_steps_and_check_is_read_only(self):
        path,plan=self.plan()
        plan['creative_workflow']={'film_grammar_required':True};write(path,plan)
        policy=self.root/'state/creative_workflow.json'
        workflow_run.run(self.root,path,execute=False)
        self.assertFalse(policy.exists())
        with patch.object(workflow_run,'check_step',return_value=['待补材料']):
            workflow_run.run(self.root,path)
        self.assertTrue(json.loads(policy.read_text(encoding='utf8'))['film_grammar_required'])
        plan.pop('creative_workflow');write(path,plan)
        with patch.object(workflow_run,'check_step',return_value=['待补材料']):
            workflow_run.run(self.root,path)
        self.assertTrue(json.loads(policy.read_text(encoding='utf8'))['film_grammar_required'])

    def test_twelve_step_resume_invalidates_downstream_and_waits_for_human(self):
        path,plan=self.plan()
        with patch.object(workflow_run,'check_step',return_value=[]):
            result=self.run_with_test_approvals(path)
            self.assertEqual(result['status'],'completed');self.assertEqual(len(result['steps']),12)
            (self.root/'state/stage3.json').unlink()
            result=workflow_run.run(self.root,path)
            self.assertEqual(result['status'],'waiting_review')
            self.assertEqual([r['step'] for r in result['steps']],[1,2,3])

    def test_twelve_step_rejects_arbitrary_commands_before_writes(self):
        path,plan=self.plan();plan['steps'][0]['commands']=[['untrusted.py']];write(path,plan)
        with self.assertRaises(ValueError): workflow_run.run(self.root,path)
        self.assertFalse((self.root/'state/workflow_run.json').exists())

    def test_later_declared_output_update_keeps_history_and_resume_valid(self):
        path,plan=self.plan()
        plan['steps'][1]['outputs'].append('state/stage1.json')
        plan['steps'][1]['commands']=[['music_capture.py','{project}']];write(path,plan)
        original=digest(self.root/'state/stage1.json')
        def invoke(*args,**kwargs):
            write(self.root/'state/stage1.json',{'updated_by':2})
            return SimpleNamespace(returncode=0,stdout='actual test process boundary',stderr='')
        with patch.object(workflow_run,'check_step',return_value=[]),patch.object(workflow_run.subprocess,'run',side_effect=invoke) as process:
            state=self.run_with_test_approvals(path)
            self.assertEqual(state['status'],'completed')
            self.assertEqual(state['steps'][0]['outputs'][str(self.root/'state/stage1.json')],original)
            self.assertEqual(workflow_run.inspect(self.root,path)['status'],'completed')
            self.assertEqual(workflow_run.run(self.root,path)['status'],'completed')
            self.assertEqual(process.call_count,1)

    def test_twelve_step_interrupted_command_cannot_be_retried_on_repeated_resume(self):
        path,plan=self.plan()
        plan['steps'][0]['commands']=[['workflow_execution.py','--help']]
        write(path,plan)
        with self.assertRaises(ValueError): workflow_run.run(self.root,path)
        plan['steps'][0]['commands']=[['workflow_execution.py','run','{project}','--step','gate1']];write(path,plan)
        write(self.root/'state/workflow_run.json',{'plan_sha256':digest(path),'steps':[{'step':1,'status':'running'}]})
        with patch.object(workflow_run,'check_step',return_value=['待核']),patch.object(workflow_run.subprocess,'run',side_effect=AssertionError('中断不可重派')):
            first=workflow_run.run(self.root,path);second=workflow_run.run(self.root,path)
            self.assertTrue(first['steps'][0]['recovery_required']);self.assertEqual(second['status'],'recovery_required')
            self.assertEqual(first['steps'][0], second['steps'][0])

    def test_release_requires_current_real_regression_and_commit(self):
        versions=release_evidence.tree_versions()
        write(self.root/'state/regression_execution.json',{'project_id':str(self.root),'status':'passed','returncode':0,'versions':versions,'commit':'c'})
        review={'project_id':str(self.root),'regression_sha256':digest(self.root/'state/regression_execution.json'),
                'findings':['测试发现'],'changes':['具体修改'],'remaining_limits':['真实影片待审'],'commit':'c','publication':{'kind':'local','commit':'c'}}
        write(self.root/'state/retrospective.json',review)
        with patch.object(release_evidence,'git',side_effect=lambda *args: 'c' if args[0]=='rev-parse' else ''):
            self.assertEqual(len(release_evidence.require(self.root)),2)
            with patch.object(release_evidence,'tree_versions',return_value={}):
                with self.assertRaises(ValueError): release_evidence.require(self.root)
        with patch.object(release_evidence,'git',side_effect=lambda *args: ' M scripts/test.py' if args[0]=='status' else 'c'):
            with self.assertRaises(ValueError): release_evidence.require(self.root)

    def test_queue_recovers_prepared_job_before_queue_binding_without_new_reservation(self):
        self.enable()
        contract=self.root/'contract.json'
        write(contract,{'chapter_id':'CH01','backend_account_id':'test','backend_capabilities':{'concurrency_limit':1}})
        batch_scheduler.init(self.root,[],contracts=['contract.json'])
        job=self.job('d'*32,'unknown')
        job.update(chapter_id='CH01',contract_sha256=digest(contract));write(batch_scheduler.jobs.job_path(self.root,job['job_id']),job)
        with patch.object(batch_scheduler.jobs,'prepare',side_effect=AssertionError('不能重复预占')):
            queue=batch_scheduler.tick(self.root,dispatch=lambda *a:self.fail('不能重派'),poll=lambda *a:self.fail('无任务ID'),now=1)
        self.assertEqual(queue['jobs'][0]['job_id'],job['job_id'])

    def test_check_never_runs_or_claims_unexecuted_command_complete(self):
        path,plan=self.plan()
        plan['steps'][0]['commands']=[['workflow_execution.py','run','{project}','--step','gate1']];write(path,plan)
        with patch.object(workflow_run,'check_step',return_value=[]),patch.object(workflow_run.subprocess,'run',side_effect=AssertionError('check不能执行')):
            result=workflow_run.run(self.root,path,execute=False)
        self.assertEqual(result['status'],'waiting_execution')
        self.assertFalse((self.root/'state/workflow_run.json').exists())

    @unittest.skipUnless(shutil.which('node'),'需要Node验证实际工作台JavaScript交互')
    def test_workbench_sync_play_seek_mute_and_offset(self):
        from scripts.review_workbench import HTML
        session={'session_id':'test','timeline':{'lines':[],'duration_sec':10},'assets':{'audio':{'file':'a'},'candidate':{'file':'c'}},'design_drafts':{'style':{}}}
        setup=r'''
const assert=require('node:assert/strict');
class Element{
 constructor(){this.value='';this.checked=false;this.children=[];this.events={};this.currentTime=0;this.duration=10;this.paused=true;this.muted=false;this.volume=1;this.playbackRate=1;}
 addEventListener(k,fn){(this.events[k]??=[]).push(fn);} emit(k){for(const f of this.events[k]||[])f({type:k,target:this});}
 append(...children){this.children.push(...children);if(!this.value&&children[0]?.value)this.value=children[0].value;}
 replaceChildren(){this.children=[];} play(){if(this.paused){this.paused=false;this.emit('play');}return Promise.resolve();}
 pause(){if(!this.paused){this.paused=true;this.emit('pause');}} click(){if(this.onclick)this.onclick();}
}
const elements={};const document={getElementById(id){return elements[id]??=(new Element());},createElement(){return new Element();}};
document.getElementById('session').textContent=JSON.stringify(SESSION);
document.getElementById('syncOffset').value='0';document.getElementById('syncListen').value='candidate';
const window={addEventListener(){}};
'''.replace('SESSION',json.dumps(session))
        script=HTML.split('<script>')[1].split('</script>')[0]
        checks=r'''
$('syncEnabled').checked=true;$('syncOffset').value='0.5';$('syncEnabled').onchange();
assert.equal(players.candidate.currentTime,.5);players.audio.play();assert.equal(players.candidate.paused,false);
assert.equal(players.audio.muted,true);assert.equal(players.candidate.muted,false);
players.audio.currentTime=.3;players.audio.emit('timeupdate');assert.equal(players.candidate.currentTime,.8);
players.candidate.currentTime=.9;players.candidate.emit('seeking');assert.equal(players.audio.currentTime,.4);
$('syncListen').value='audio';$('syncListen').onchange();assert.equal(players.candidate.muted,true);assert.equal(players.audio.muted,false);
players.audio.pause();assert.equal(players.candidate.paused,true);
$('syncAlign').onclick();assert.equal(D.playback_sync.candidate_offset_sec,.5);
console.log('sync controls passed');
'''
        path=self.root/'sync-test.js';path.write_text(setup+script+checks,encoding='utf8')
        result=subprocess.run(['node',str(path)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_corpus_signed_semantic_mapping_and_duplicate_film_rejection(self):
        sign=self.signer()
        profile={'schema_version':1,'profile_id':'test','sample':{'film_count':1},'selection':{'minimum_films':1},
                 'scene_mean_duration_sec':{'p10':.5,'p90':1.5},'scene_kind_envelopes':{'performance':{'p10':.5,'p90':1.5}}}
        shot={'id':'SH001','scene_id':'hall','window':{'start':0,'end':1},'scene_kind':'performance'}
        write(self.root/'profile.json',profile);write(self.root/'annotations.json',{'fixture':True})
        payload={'purpose':'film_corpus','profile_path':'profile.json','profile_sha256':digest(self.root/'profile.json'),
                 'artifacts':{'annotations.json':digest(self.root/'annotations.json')},'annotation_review':'pass','review_method':'独立测试夹具',
                 'films':[{'canonical_film_id':'test-film','edition':'test-edition'}],
                 'semantic_mapping':[{**shot,'shot_id':'SH001','decision':'applicable','rationale':'仅测试覆盖','reference_annotation':'annotations.json#0'}]}
        cert=self.root/'certificate.json';write(cert,sign(payload))
        self.assertEqual(len(corpus_validation.require(self.root,profile,{'shots':[shot]},'certificate.json')),3)
        from scripts.film_grammar_corpus import audit_storyboard
        self.assertFalse(audit_storyboard(profile,{'shots':[shot]},mode='enforce_envelope',project=self.root,certificate_path='certificate.json')['errors'])
        write(self.root/'state/storyboard.json',{'shots':[shot]})
        command=[sys.executable,str(Path(corpus_validation.__file__).with_name('film_grammar_corpus.py')),'audit',str(self.root),str(self.root/'profile.json'),
                 '--mode','enforce_envelope','--certificate','certificate.json']
        result=subprocess.run(command,capture_output=True,text=True,encoding='utf8')
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        payload['films']*=2;write(cert,sign(payload))
        with self.assertRaises(ValueError): corpus_validation.require(self.root,profile,{'shots':[shot]},'certificate.json')
        result=subprocess.run(command,capture_output=True,text=True,encoding='utf8')
        self.assertEqual(result.returncode,1,result.stdout+result.stderr)

    def test_batch_run_continues_to_completion_and_stops_on_unknown(self):
        with patch.object(batch_scheduler,'tick',side_effect=[{'status':'pending','jobs':[]},{'status':'completed','jobs':[]}]) as tick,patch.object(batch_scheduler.time,'sleep'):
            self.assertEqual(batch_scheduler.run(self.root)['status'],'completed');self.assertEqual(tick.call_count,2)
        with patch.object(batch_scheduler,'tick',return_value={'status':'needs_attention','jobs':[]}) as tick:
            self.assertEqual(batch_scheduler.run(self.root)['status'],'needs_attention');self.assertEqual(tick.call_count,1)

    def test_real_j_cut_mix_preserves_master_and_detects_changed_plan(self):
        source,master=self.media()
        shots=[{'id':f'SH00{i+1}','window':{'start':i*.5,'end':(i+1)*.5},'crew':{'editing':{'audio_transition':'J_cut' if i==0 else 'straight',**({'audio_transition_duration_frames':2} if i==0 else {})}}} for i in range(2)]
        write(self.root/'state/storyboard.json',{'fps':24,'shots':shots})
        entries=[{'shot_id':s['id'],'frames':12,'audio_path':'audio.wav','audio_sha256':digest(master),'audio_in_frame':4,'role':'diegetic','authorization':'合成测试声音',
                  'audio_transition':{'type':'J_cut' if i==0 else 'straight','duration_frames':2 if i==0 else 0}} for i,s in enumerate(shots)]
        plan={'fps':24,'entries':entries,'master_gain':.8,'stem_gain':.2}
        write(self.root/'state/sound_edit_plan.json',plan)
        output=self.root/'output/mixed.mp4'
        directory=self.root/'render';directory.mkdir()
        sound_mix.mux(self.root,source,master,output,directory)
        from scripts.media_verify import verify_delivery
        report=verify_delivery(output,master,1,24,64,64,project=self.root)
        self.assertEqual(report['status'],'pass',report)
        plan['stem_gain']=.1;write(self.root/'state/sound_edit_plan.json',plan)
        with self.assertRaises(ValueError): sound_mix.verify(self.root,output,master)

    def test_dense_lip_frames_signed_events_and_mutation_rejected(self):
        sign=self.signer();source,master=self.media()
        write(self.root/'state/storyboard.json',{'fps':24,'shots':[{'id':'SH001','window':{'start':0,'end':1},'lyric_lines':[0],'lip_sync':True}]})
        write(self.root/'state/timeline.json',{'master_audio':'audio.wav'})
        plan=self.root/'roi.json';write(plan,{'shots':[{'shot_id':'SH001','roi':[0,0,32,16]}]})
        record=lip_measure.prepare(self.root,source,plan)
        self.assertEqual(len(record['shots'][0]['frames']),24)
        payload={'purpose':'lip_measure','measurement_sha256':digest(self.root/'state/lip_measurement.json'),
                 'media_sha256':digest(source),'full_audio_video_review':True,'tolerance_ms':80,
                 'shots':[{'shot_id':'SH001','phonetic_match':'pass','observation':'合成媒体签名测试，不是实际唇形质量评价',
                           'events':[{'video_frame':i,'audio_time_sec':i/24,'phoneme':'test','lyric_line':0} for i in (2,20)]}]}
        cert=self.root/'state/lip_measure_review.json';write(cert,sign(payload))
        self.assertEqual(lip_measure.require(self.root)['max_abs_lag_ms'],0)
        payload['shots'][0]['events'][0]['audio_time_sec']=.5;write(cert,sign(payload))
        with self.assertRaises(ValueError): lip_measure.require(self.root)
        payload['shots'][0]['events'][0]['audio_time_sec']=2/24;write(cert,sign(payload))
        frame=self.root/next(iter(record['shots'][0]['frames']));frame.write_bytes(b'changed')
        with self.assertRaises(ValueError): lip_measure.require(self.root)


if __name__=='__main__': unittest.main()
