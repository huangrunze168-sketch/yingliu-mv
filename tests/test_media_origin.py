"""来源契约反例；隔离供应商夹具，实际ffprobe读取本地测试视频。"""
import json
from pathlib import Path
import tempfile
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

from scripts import media_origin as origin
from scripts.pipeline_contracts import digest
from test_production_contracts import ffmpeg


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf8')


class MediaOriginTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name).resolve()
        self.source = self.project / 'clip.mp4'
        ffmpeg('-f', 'lavfi', '-i', 'color=black:s=160x90:r=24:d=0.25', '-c:v', 'libx264', self.source)
        self.contract = self.project / 'state/contract.json'
        write(self.contract, {'edit_shots': [{'id': 'SH1'}]})
        self.job_id = 'a' * 32
        self.job_path = origin.job_path(self.project, self.job_id)
        self.job = {'job_id': self.job_id, 'project_id': str(self.project), 'chapter_id': 'CH1',
                    'status': 'completed', 'provider_task_id': 'fixture-task',
                    'contract_path': str(self.contract), 'contract_sha256': digest(self.contract),
                    'response': {'task_id': 'fixture-task', 'status': 'completed',
                                 'raw_response': {'id': 'fixture-task', 'state': 'completed',
                                                  'url': 'https://fixture.invalid/video'},
                                 'response_fields': {'task_id': '/id', 'status': '/state'}}}
        write(self.job_path, self.job)
        self.http_path = self.project / 'http.json'
        self.http = {'method': 'GET', 'status_code': 200, 'url': 'https://fixture.invalid/video',
                     'downloaded_path': str(self.source), 'downloaded_sha256': digest(self.source)}
        write(self.http_path, self.http)
        self.clip = {'path': 'clip.mp4', 'sha256': digest(self.source), 'chapter_id': 'CH1', 'shot_ids': ['SH1']}

    def register(self):
        receipt = origin.register_download(self.project, self.job_id, downloaded_path=self.source,
                                           http_evidence_path=self.http_path, output_pointer='/url')
        self.clip['origin_receipt_path'] = str(receipt.relative_to(self.project))
        return receipt

    def test_generated_actual_media_and_versions_without_artistic_approval(self):
        path = self.register()
        versions = origin.require(self.project, self.clip)
        self.assertIn(self.source, versions)
        self.assertIn(self.job_path, versions)
        self.assertFalse(origin.load(path)['dynamic_verified'])
        self.assertFalse(origin.load(path)['artistic_approval'])

    def test_pending_task_cannot_be_download_approved(self):
        self.job['status'] = 'pending'
        write(self.job_path, self.job)
        with self.assertRaises(ValueError): self.register()

    def test_wrong_provider_id_or_url_cannot_bind_download(self):
        self.job['response']['raw_response']['id'] = 'other-task'
        write(self.job_path, self.job)
        with self.assertRaises(ValueError): self.register()
        self.job['response']['raw_response']['id'] = 'fixture-task'
        write(self.job_path, self.job)
        self.http['url'] = 'https://fixture.invalid/other'
        write(self.http_path, self.http)
        with self.assertRaisesRegex(ValueError, 'HTTP'): self.register()

    def test_changed_file_and_http_after_registration_are_blocked(self):
        self.register()
        self.http['status_code'] = 404
        write(self.http_path, self.http)
        with self.assertRaises(ValueError): origin.require(self.project, self.clip)
        self.http['status_code'] = 200
        write(self.http_path, self.http)
        original = self.source.read_bytes()
        self.source.write_bytes(original + b'changed')
        with self.assertRaises(ValueError): origin.require(self.project, self.clip)

    def test_later_cost_settlement_does_not_change_media_identity(self):
        self.register()
        self.job.update(cost_status='settled', actual_cost={'cash_cny': '2'})
        write(self.job_path, self.job)
        origin.require(self.project, self.clip)

    def test_forged_chapter_or_shot_ownership_is_blocked(self):
        path = self.register()
        record = origin.load(path)
        record['chapter_id'] = self.clip['chapter_id'] = 'CH2'
        write(path, record)
        with self.assertRaises(ValueError): origin.require(self.project, self.clip)
        record['chapter_id'] = self.clip['chapter_id'] = 'CH1'
        record['shot_ids'] = self.clip['shot_ids'] = ['SH2']
        write(path, record)
        with self.assertRaises(ValueError): origin.require(self.project, self.clip)

    def test_missing_origin_cannot_enter_formal_selection(self):
        with self.assertRaisesRegex(ValueError, '来源登记'): origin.require(self.project, self.clip)

    def test_retained_requires_source_authorization_and_remains_unreviewed(self):
        auth = self.project / 'user-request.txt'
        auth.write_text('测试授权夹具：允许保留DJ片段', encoding='utf8')
        kwargs = dict(source_path=self.source, chapter_id='CH1', shot_ids=['SH1'],
                      authorization_path=auth, authorization_quote='允许保留DJ片段',
                      observer='test-fixture', purpose='保留DJ搓碟')
        path = origin.register_retained(self.project, **kwargs)
        self.clip['origin_receipt_path'] = str(path.relative_to(self.project))
        origin.require(self.project, self.clip)
        self.assertFalse(origin.load(path)['artistic_approval'])
        auth.write_text('已撤销该授权', encoding='utf8')
        with self.assertRaises(ValueError): origin.require(self.project, self.clip)
        kwargs['authorization_quote'] = '不存在的授权'
        with self.assertRaises(ValueError): origin.register_retained(self.project, **kwargs)

    def test_non_video_download_is_not_usable_material(self):
        self.source.write_text('not a video', encoding='utf8')
        self.http['downloaded_sha256'] = digest(self.source)
        write(self.http_path, self.http)
        with self.assertRaises(Exception): self.register()
        self.assertFalse((self.project / 'state/media_origins').exists())

    def test_actual_http_download_registers_identical_bytes(self):
        payload = self.source.read_bytes()
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(payload)
            def log_message(self, *args): pass
        server = HTTPServer(('127.0.0.1', 0), Handler)
        worker = Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            self.job['response']['raw_response']['url'] = f'http://127.0.0.1:{server.server_port}/video'
            write(self.job_path, self.job)
            receipt_path = origin.download(self.project, self.job_id, output_pointer='/url')
            receipt = origin.load(receipt_path)
            downloaded = self.project / receipt['path']
            self.assertEqual(downloaded.read_bytes(), payload)
            clip = {**self.clip, 'path': receipt['path'], 'origin_receipt_path': str(receipt_path.relative_to(self.project))}
            origin.require(self.project, clip)
            self.assertEqual(origin.load(self.project / receipt['http_evidence_path'])['status'], 'downloaded')
        finally:
            server.shutdown()
            worker.join(timeout=5)
            server.server_close()
