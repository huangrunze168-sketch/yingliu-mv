"""隔离契约夹具验证来源、范围与失效；不把合成数据当真实推理。"""
import json
from pathlib import Path
import tempfile
import unittest
import uuid

from scripts.alignment_evidence import require
from scripts.pipeline_contracts import digest, atomic_batch
from scripts.timing_evidence import errors
from scripts.lyrics_canonical import parse
from evidence_fixtures import write_timing_fixture


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value,ensure_ascii=False),encoding='utf8')


class AlignmentEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.p=Path(self.temp.name).resolve()
        (self.p/'input').mkdir()
        (self.p/'input/song.wav').write_bytes(b'explicit-isolated-contract-fixture')
        lyrics=self.p/'input/lyrics.txt';lyrics.write_text('[周瑜]\n第一句\n第二句',encoding='utf8')
        self.t={'master_audio':'input/song.wav','duration_sec':4,'lines':[
            dict(index=i,type='vocal',speaker='周瑜',text=text,start=a,end=b)
            for i,text,a,b in [(1,'第一句',0,2),(2,'第二句',2,4)]]}
        _,self.review=write_timing_fixture(self.p,self.t)
        self.rid=uuid.uuid4().hex
        self.jp=self.p/'state/forced_alignment_jobs'/(self.rid+'.json')
        self.rp=self.p/'analysis/forced_alignment'/self.rid/'alignment.json'
        self.result={'run_id':self.rid,'duration_sec':4,'source_audio_sha256':digest(self.p/'input/song.wav'),
            'words':[dict(word=w,start=i,end=i+1,score=.8) for i,w in enumerate(('di','yi','di','er'))],
            'lines':[{**row,'word_start_index':i*2,'word_end_index':i*2+2,
                      'start_candidate':i*2,'end_candidate':i*2+2}
                     for i,row in enumerate(parse(lyrics.read_text(encoding='utf8')))]}
        self.job={'run_id':self.rid,'project_id':str(self.p),'status':'completed_alignment_unreviewed',
            'model':'torchaudio.pipelines.MMS_FA','engine_versions':{'torchaudio':'fixture'},
            'decode':{'returncode':0},'result_path':str(self.rp),'lines':2,
            'romanized_words':['di','yi','di','er'],
            'inputs':{str(p):digest(p) for p in (lyrics,self.p/'input/song.wav',self.p/'state/timeline.json')}}
        self.persist()
        self.t['machine_candidate_source']={'job_path':str(self.jp),'run_id':self.rid,'result_path':str(self.rp)}
        write(self.p/'state/timeline.json',self.t)
        self.bind_review()

    def persist(self):
        write(self.rp,self.result);self.job['result_sha256']=digest(self.rp);write(self.jp,self.job)

    def bind_review(self):
        self.review.update(boundary_source='forced_alignment',alignment_run_id=self.rid,
            alignment_result_sha256=self.job['result_sha256'],timeline_sha256=digest(self.p/'state/timeline.json'))
        for i,row in enumerate(self.review['lines']):row['forced_alignment_word_range']=[i*2,i*2+1]
        write(self.p/'state/timing_review.json',self.review)

    def test_current_alignment_and_explicit_review_contract(self):
        self.assertEqual(errors(self.p,self.t),[])
        self.review['decision']='pending';write(self.p/'state/timing_review.json',self.review)
        self.assertTrue(any('复核声明' in e for e in errors(self.p,self.t)))

    def test_result_replacement_or_changed_lyrics_blocks(self):
        self.rp.write_text('{}',encoding='utf8')
        with self.assertRaisesRegex(ValueError,'结果已改变'):require(self.p,self.t)
        self.persist();(self.p/'input/lyrics.txt').write_text('改词',encoding='utf8')
        with self.assertRaisesRegex(ValueError,'歌词已改变'):require(self.p,self.t)

    def test_wrong_project_and_failed_execution_block(self):
        self.job['project_id']=str(self.p/'other');write(self.jp,self.job)
        with self.assertRaises(ValueError):require(self.p,self.t)
        self.job['project_id']=str(self.p);self.job['decode']['returncode']=True;write(self.jp,self.job)
        with self.assertRaises(ValueError):require(self.p,self.t)

    def test_rehashed_wrong_line_or_partial_words_still_block(self):
        self.result['lines'][0]['text']='篡改歌词';self.persist()
        with self.assertRaisesRegex(ValueError,'身份'):require(self.p,self.t)
        self.result['lines'][0]['text']='第一句';self.result['lines'][0]['word_end_index']=1;self.persist()
        with self.assertRaises(ValueError):require(self.p,self.t)

    def test_review_cannot_bind_old_result_or_partial_word_range(self):
        self.review['alignment_result_sha256']='sha256:old';write(self.p/'state/timing_review.json',self.review)
        self.assertTrue(any('未绑定' in e for e in errors(self.p,self.t)))
        self.bind_review();self.review['lines'][0]['forced_alignment_word_range']=[0,0]
        write(self.p/'state/timing_review.json',self.review)
        self.assertTrue(any('完整词范围' in e for e in errors(self.p,self.t)))


class AtomicRetirementTests(unittest.TestCase):
    def test_real_prompt_compile_archives_retired_packages(self):
        import subprocess
        import sys
        from test_visual_workflow import fixture, build, write_sequence_stub
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);fixture(root)
            storyboard=build(root);write_sequence_stub(root,storyboard['shots'])
            old=[]
            for directory in ('prompts','prompts/keyframes','prompts/motion'):
                file=root/directory/'SH999.json';file.parent.mkdir(parents=True,exist_ok=True)
                file.write_bytes(b'isolated retired package');old.append(file)
            script=Path(__file__).resolve().parents[1]/'scripts/prompt_build.py'
            result=subprocess.run([sys.executable,'-X','utf8',str(script),str(root)],capture_output=True,text=True,encoding='utf8')
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            self.assertTrue(all(not file.exists() for file in old))
            archives=list((root/'state/prompt_history').rglob('SH999.json'))
            self.assertEqual(len(archives),3)
            self.assertTrue(all(file.read_bytes()==b'isolated retired package' for file in archives))
            self.assertTrue((root/'prompts/SH001.json').exists())

    def test_archive_and_delete_rollback_together(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);old=root/'SH002.json';old.write_bytes(b'old')
            archive=root/'history/SH002.json'
            def fail():raise ValueError('显式失败')
            with self.assertRaises(ValueError):
                atomic_batch({archive:b'old'},expected={old:digest(old),archive:None},deletions=[old],before_commit=fail)
            self.assertEqual(old.read_bytes(),b'old');self.assertFalse(archive.exists())
            atomic_batch({archive:b'old'},expected={old:digest(old),archive:None},deletions=[old])
            self.assertFalse(old.exists());self.assertEqual(archive.read_bytes(),b'old')

    def test_unversioned_delete_and_write_delete_overlap_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'file';path.write_bytes(b'keep')
            with self.assertRaises(ValueError):atomic_batch({},deletions=[path])
            with self.assertRaises(ValueError):atomic_batch({path:b'replace'},expected={path:digest(path)},deletions=[path])
            self.assertEqual(path.read_bytes(),b'keep')
