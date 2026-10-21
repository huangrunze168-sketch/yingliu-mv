"""实际本地播放交付、范围请求和待核隔离；不测试伪造观看结论。"""
import functools
import http.client
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import tempfile
import threading
import unittest

from scripts.review_workbench import build, import_draft, MediaHandler
from scripts.pipeline_contracts import digest


class ReviewWorkbenchTests(unittest.TestCase):
    def test_rebuild_restores_only_current_bound_observations(self):
        review_path=self.project/'state/timing_review.json'
        review={'timeline_sha256':digest(self.path),'lines':[
            {**self.timeline['lines'][0],'observation':'隔离机器候选说明，非听辨批准'}]}
        review_path.write_text(json.dumps(review),encoding='utf8')
        build(self.project,self.bundle)
        session=json.loads((self.bundle/'session.json').read_text(encoding='utf8'))
        self.assertEqual(session['timeline']['lines'][0]['observation'],'隔离机器候选说明，非听辨批准')
        path,_=self.draft();review['lines'][0]['observation']='改动'
        review_path.write_text(json.dumps(review),encoding='utf8')
        with self.assertRaisesRegex(ValueError,'依据已变化'):import_draft(self.project,self.bundle,path)
        review['timeline_sha256']='sha256:old';review_path.write_text(json.dumps(review),encoding='utf8')
        other=self.root/'other-bundle';build(self.project,other)
        self.assertNotIn('observation',json.loads((other/'session.json').read_text(encoding='utf8'))['timeline']['lines'][0])

    def test_overlapping_interludes_rejected_before_import(self):
        build(self.project,self.bundle)
        path,draft=self.draft()
        draft['interludes']=[dict(start=0,end=1,observation='隔离区间一'),
                            dict(start=.5,end=1.5,observation='隔离区间二')]
        path.write_text(json.dumps(draft),encoding='utf8')
        with self.assertRaisesRegex(ValueError,'重叠'):
            import_draft(self.project,self.bundle,path)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root/'project'
        (self.project/'state').mkdir(parents=True)
        (self.project/'input').mkdir()
        (self.project/'input/song.mp3').write_bytes(bytes(range(256)))
        self.timeline = {'master_audio': 'input/song.mp3', 'duration_sec': 2,
                         'production_timing_verified': False,
                         'lines': [{'index': 1, 'type': 'vocal', 'text': '</script><script>alert(1)</script>',
                                    'start': 0, 'end': 2, 'speaker': '周瑜'}]}
        self.path = self.project/'state/timeline.json'
        self.path.write_text(json.dumps(self.timeline), encoding='utf-8')
        self.bundle = self.root/'workbench'

    def draft(self):
        session = json.loads((self.bundle/'session.json').read_text(encoding='utf-8'))
        draft = {'session_id': session['session_id'], 'decision': 'pending',
                 'lines': session['timeline']['lines'], 'findings': []}
        path = self.root/'draft.json'
        path.write_text(json.dumps(draft), encoding='utf-8')
        return path, draft

    def test_build_binds_actual_media_and_does_not_create_formal_design(self):
        media = self.root/'candidate.mp4'
        media.write_bytes(b'isolated-nonplayable-fixture')
        before = self.path.read_bytes()
        result = build(self.project, self.bundle, media=media)
        self.assertEqual(result['decision'], 'pending')
        self.assertEqual((self.bundle/'candidate.mp4').read_bytes(), media.read_bytes())
        self.assertEqual(self.path.read_bytes(), before)
        self.assertFalse((self.project/'state/development.json').exists())
        self.assertFalse((self.project/'state/director_review.json').exists())
        html = (self.bundle/'index.html').read_text(encoding='utf-8')
        self.assertNotIn('</script><script>alert(1)</script>', html)
        self.assertIn('\\u003c/script>', html)
        with self.assertRaisesRegex(ValueError, '已存在'):
            build(self.project, self.bundle)

    def test_import_stays_pending_and_preserves_official_timeline(self):
        build(self.project, self.bundle)
        path, draft = self.draft()
        before = self.path.read_bytes()
        draft['lines'][0]['start'] = .25
        draft['lines'][0]['observation'] = '隔离测试观察，不认证听看'
        path.write_text(json.dumps(draft), encoding='utf-8')
        target = import_draft(self.project, self.bundle, path)
        record = json.loads(target.read_text(encoding='utf-8'))
        self.assertEqual(record['decision'], 'pending')
        self.assertFalse(record['dynamic_verified'])
        self.assertFalse(record['agent_inspected'])
        self.assertEqual(record['payload']['lines'][0]['start'], .25)
        self.assertEqual(before, self.path.read_bytes())

    def test_old_media_and_autoapproval_are_rejected(self):
        build(self.project, self.bundle)
        path, draft = self.draft()
        draft['decision'] = 'approve'
        path.write_text(json.dumps(draft), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '不能在此批准'):
            import_draft(self.project, self.bundle, path)
        draft['decision'] = 'pending'
        path.write_text(json.dumps(draft), encoding='utf-8')
        (self.bundle/'audio.mp3').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, '播放器媒体已变化'):
            import_draft(self.project, self.bundle, path)

    def test_changed_source_or_missing_design_appearing_invalidates_session(self):
        build(self.project, self.bundle)
        path, _ = self.draft()
        (self.project/'state/development.json').write_text('{}', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '依据已变化'):
            import_draft(self.project, self.bundle, path)

    def test_out_of_range_and_wrong_session_are_rejected(self):
        build(self.project, self.bundle)
        path, draft = self.draft()
        draft['lines'][0]['end'] = 3
        path.write_text(json.dumps(draft), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '超出原曲'):
            import_draft(self.project, self.bundle, path)
        draft['session_id'] = 'other'
        path.write_text(json.dumps(draft), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '不匹配'):
            import_draft(self.project, self.bundle, path)

    def test_real_http_range_seek_and_no_directory_listing_or_write(self):
        build(self.project, self.bundle)
        class QuietHandler(MediaHandler):
            def log_message(self, *args):
                pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(QuietHandler, directory=str(self.bundle)))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        conn = http.client.HTTPConnection('127.0.0.1', server.server_port)
        self.addCleanup(conn.close)
        for header, expected in [('bytes=20-29', bytes(range(20, 30))), ('bytes=-4', bytes(range(252, 256))),
                                 ('bytes=254-', bytes(range(254, 256)))]:
            conn.request('GET', '/audio.mp3', headers={'Range': header})
            response = conn.getresponse()
            self.assertEqual(response.status, 206)
            self.assertEqual(response.read(), expected)
        conn.request('GET', '/audio.mp3', headers={'Range': 'bytes=999-1000'})
        response = conn.getresponse()
        self.assertEqual(response.status, 416)
        response.read()
        conn.request('POST', '/', body=b'approve')
        response = conn.getresponse()
        self.assertEqual(response.status, 501)
        response.read()


if __name__ == '__main__':
    unittest.main()
