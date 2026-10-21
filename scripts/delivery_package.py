"""实际双版本、权威歌词及验收证据打包；包完整性与艺术判断分别说明。"""
import json
import hashlib
import subprocess
import tempfile
import uuid
import wave
import zipfile
from pathlib import Path

try:
    from .pipeline_contracts import digest, atomic_batch, require_no_transaction
    from . import delivery_evidence
    from .media_verify import probe
except ImportError:
    from pipeline_contracts import digest, atomic_batch, require_no_transaction
    import delivery_evidence
    from media_verify import probe


def load(path):
    return json.loads(Path(path).read_text(encoding='utf8'))


def sha(data):
    return 'sha256:' + hashlib.sha256(data).hexdigest()


def manifest_path(project, output_name):
    try:
        from .delivery_state import record_path
    except ImportError:
        from delivery_state import record_path
    record_path(project, output_name)
    return Path(project).resolve() / 'state/delivery_packages' / f'{output_name}.json'


def video_payload(path):
    result = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_packets',
                             '-show_entries', 'packet=data_hash', '-show_data_hash', 'sha256', '-of', 'json', str(path)],
                            capture_output=True, text=True, check=True)
    rows = json.loads(result.stdout)['packets']
    if not rows or any(not r.get('data_hash') for r in rows): raise ValueError('画面载荷不可核验')
    return sha(json.dumps([r['data_hash'] for r in rows]).encode())


def embedded_audio(project, master_video, target, directory):
    """从正式选段原素材取对应音频，不用母带替代缺失的原生音轨。"""
    assembly = load(project / 'state/edit_assembly.json')
    rate, channels, samples, commands = 48000, 2, [], []
    for entry in assembly['entries']:
        source = (project / entry['source_path']).resolve()
        if digest(source) != entry['source_sha256']: raise ValueError('内嵌声音来源已改变')
        if not any(s['codec_type'] == 'audio' for s in probe(source)['streams']):
            raise ValueError('原生成素材无内嵌音轨，不能用母带伪造第二版本')
        start = round(entry['in_frame'] / entry['fps'] * rate)
        count = round(entry['frames'] / entry['fps'] * rate)
        cmd = ['ffmpeg', '-v', 'error', '-i', str(source), '-map', '0:a:0', '-ac', str(channels),
               '-af', f'aresample={rate},atrim=start_sample={start}:end_sample={start+count},asetpts=PTS-STARTPTS',
               '-ar', str(rate), '-c:a', 'pcm_s16le', '-f', 's16le', '-']
        result = subprocess.run(cmd, capture_output=True)
        if result.returncode or len(result.stdout) != count * channels * 2:
            raise ValueError('原生音轨未覆盖正式选段，不补静音、不变速掩盖缺失')
        samples.append(result.stdout)
        commands.append({'command': cmd, 'returncode': result.returncode,
                         'pcm_sha256': sha(result.stdout), 'samples': count, 'shot_id': entry['shot_id']})
    audio = directory / 'embedded.wav'
    with wave.open(str(audio), 'wb') as handle:
        handle.setnchannels(channels); handle.setsampwidth(2); handle.setframerate(rate)
        handle.writeframes(b''.join(samples))
    cmd = ['ffmpeg', '-y', '-v', 'error', '-i', str(master_video), '-i', str(audio), '-map', '0:v:0',
           '-map', '1:a:0', '-c:v', 'copy', '-c:a', 'alac', str(target)]
    subprocess.run(cmd, capture_output=True, check=True)
    if video_payload(master_video) != video_payload(target): raise ValueError('双版本的画面载荷不一致')
    return {'scope': 'selected_original_embedded_audio_diagnostic_not_master_replacement',
            'sample_rate': rate, 'channels': channels, 'commands': commands, 'mux_command': cmd,
            'picture_payload_sha256': video_payload(target), 'lip_sync_verified': False}


def build(project, output_name='final_mv.mp4', *, review_path=None):
    project = Path(project).resolve()
    require_no_transaction(project)
    try:
        from . import delivery_state
    except ImportError:
        import delivery_state
    record = delivery_state.read_record(project, output_name)
    if record.get('status') != 'technical_verified':
        raise ValueError('先实际输出并完成技术验收；不从预览或旧ready直接打包')
    current = delivery_state.check(project, output_name)
    if current['errors']: raise ValueError('打包依据已失效: ' + '; '.join(current['errors']))
    delivery_state.require_current(project)
    versions = {Path(p): s for p, s in record['dependencies'].items()}
    state_path = delivery_state.record_path(project, output_name)
    versions[state_path] = record['_revision']
    master = project / 'output' / output_name
    versions[master] = record['output_sha256']
    review_path = (project / (review_path or f'state/final_media_review.{output_name}.json')).resolve()
    review_path.relative_to(project)
    if not review_path.is_file():
        template = delivery_evidence.review_template(project, output_name)
        atomic_batch({review_path: json.dumps(template, ensure_ascii=False, indent=2)}, expected={review_path: None})
        raise ValueError('已建立待核最终媒体复核模板；实际完整有声观看后填写，不自动批准')
    versions[review_path] = digest(review_path)
    review = delivery_evidence.require_review(project, output_name, review_path)
    try:
        from .production_costs import audit as audit_costs
    except ImportError:
        from production_costs import audit as audit_costs
    costs, cost_versions = audit_costs(project)
    versions.update(cost_versions)
    lyrics = delivery_evidence.lrc(project)
    verification = project / 'output' / Path(output_name).with_suffix('.verification.json')
    versions[verification] = record['verification_sha256']
    sources = {'master.mp4': master, 'storyboard.json': project / 'state/storyboard.json',
               'timeline.json': project / 'state/timeline.json', 'lyrics.txt': project / 'input/lyrics.txt',
               'final_media_review.json': review_path, 'verification.json': verification,
               'repair_assessment.json': project / 'state/repair_assessment.json'}
    for p in sources.values(): versions.setdefault(p, digest(p))
    extra_members = set()
    if review.get('independent_lip_measurement'):
        measurement = review['independent_lip_measurement']
        versions.update({Path(p):v for p,v in measurement['versions'].items()})
        sources['lip_measurement.json'] = project/'state/lip_measurement.json'
        sources['lip_measure_review.json'] = project/'state/lip_measure_review.json'
        extra_members.update(('lip_measurement.json', 'lip_measure_review.json'))
    if (project/'state/sound_edit_plan.json').is_file():
        sources['sound_edit_plan.json'] = project/'state/sound_edit_plan.json'
        extra_members.add('sound_edit_plan.json')
    for p in sources.values(): versions.setdefault(p, digest(p))
    script_paths = [Path(__file__).resolve(), Path(delivery_evidence.__file__).resolve(),
                    Path(__file__).with_name('align_verify.py').resolve()]
    versions.update({p: digest(p) for p in script_paths})
    generation = uuid.uuid4().hex
    root = project / 'output/packages' / record['export_id'] / generation
    execution = project / 'analysis/delivery_alignment_runs' / generation
    report = delivery_evidence.run_alignment(project, output_name, execution)
    sources.update({'alignment.json': execution / 'alignment.json',
                    'alignment_execution.json': execution / 'alignment_execution.json'})
    for p in sources.values(): versions.setdefault(p, digest(p))
    with tempfile.TemporaryDirectory(dir=project / 'output') as temporary:
        directory = Path(temporary)
        embedded = directory / 'embedded_audio.mp4'
        audio_report = embedded_audio(project, master, embedded, directory)
        contents = {name: p.read_bytes() for name, p in sources.items()}
        contents.update({'embedded_audio.mp4': embedded.read_bytes(), 'lyrics.lrc': lyrics.encode('utf8'),
                         'cost_audit.json': json.dumps(costs, ensure_ascii=False, indent=2).encode('utf8'),
                         'embedded_audio_execution.json': json.dumps(audio_report, ensure_ascii=False, indent=2).encode('utf8')})
        manifest = {'schema_version': 1, 'project_id': str(project), 'output_name': output_name,
                    'export_id': record['export_id'], 'specification_sha256': record['specification_sha256'],
                    'output_sha256': record['output_sha256'], 'artifact_root': str(root),
                    'dependencies': {str(p): s for p, s in versions.items() if p != state_path},
                    'members': {name: sha(value) for name, value in contents.items()},
                    'optional_members': sorted(extra_members),
                    'audio_alignment': 'actual_candidate_audio_bound_to_video',
                    'lip_sync_verified': False, 'lip_sync_review_scope': review['scope'],
                    'artistic_approval': False, 'user_received': False}
        archive = directory / 'delivery.zip'
        manifest['archive_path'] = str(root / 'delivery.zip')
        packed_manifest = json.dumps(manifest, ensure_ascii=False, indent=2).encode('utf8')
        with zipfile.ZipFile(archive, 'w', compression=zipfile.ZIP_STORED) as handle:
            for name, data in contents.items(): handle.writestr(name, data)
            handle.writestr('manifest.json', packed_manifest)
        manifest['archive_sha256'] = digest(archive)
        manifest['packed_manifest_sha256'] = sha(packed_manifest)
        path = manifest_path(project, output_name)
        updates = {root / name: data for name, data in contents.items()}
        updates[root / 'manifest.json'] = packed_manifest
        updates[root / 'delivery.zip'] = archive.read_bytes()
        updates[path] = json.dumps(manifest, ensure_ascii=False, indent=2)
        expected = {**versions, path: digest(path) if path.exists() else None}
        expected.update({p: None for p in updates if p != path})
        atomic_batch(updates, expected=expected)
    return manifest


def require_current(project, record):
    project = Path(project).resolve()
    path = manifest_path(project, record['output_name'])
    version = digest(path)
    manifest = load(path)
    for key in ('project_id', 'output_name', 'export_id', 'output_sha256', 'specification_sha256'):
        if manifest.get(key) != record.get(key): raise ValueError('交付包绑定旧导出: ' + key)
    expected = {Path(p): s for p, s in manifest['dependencies'].items()}
    root = Path(manifest['artifact_root']).resolve()
    root.relative_to(project / 'output/packages')
    required = {'master.mp4', 'embedded_audio.mp4', 'storyboard.json', 'timeline.json', 'lyrics.txt', 'lyrics.lrc',
                'final_media_review.json', 'verification.json', 'repair_assessment.json',
                'alignment.json', 'alignment_execution.json', 'embedded_audio_execution.json', 'cost_audit.json'}
    extras = set(manifest.get('optional_members', []))
    expected_extras = set()
    if (project/'state/lip_measurement.json').is_file(): expected_extras.update(('lip_measurement.json','lip_measure_review.json'))
    if (project/'state/sound_edit_plan.json').is_file(): expected_extras.add('sound_edit_plan.json')
    if extras != expected_extras: raise ValueError('交付包独立口型或混音材料覆盖不一致')
    required.update(extras)
    if set(manifest.get('members', {})) != required: raise ValueError('交付包缺少必需产物')
    if manifest['members']['master.mp4'] != record['output_sha256']:
        raise ValueError('包内母带版不是当前实际输出')
    for name, value in manifest['members'].items():
        target = root / name
        if digest(target) != value: raise ValueError('交付包产物已改变: ' + name)
        expected[target] = value
    if (root / 'lyrics.lrc').read_text(encoding='utf8') != delivery_evidence.lrc(project):
        raise ValueError('交付LRC与当前权威时间不一致')
    delivery_evidence.require_review(project, record['output_name'], root / 'final_media_review.json')
    try:
        from .production_costs import audit as audit_costs
    except ImportError:
        from production_costs import audit as audit_costs
    costs, cost_versions = audit_costs(project)
    if costs != load(root / 'cost_audit.json'): raise ValueError('交付成本汇总与当前作业/费用响应/台账不一致')
    expected.update(cost_versions)
    alignment = load(root / 'alignment.json')
    if (alignment.get('overall') != 'PASS' or alignment.get('candidate_video_sha256') != record['output_sha256']
            or alignment.get('candidate_audio_bound_to_video') is not True or alignment.get('lip_sync_verified') is not False):
        raise ValueError('包内对齐报告不是当前成片验证')
    shots = load(project / 'state/storyboard.json')['shots']
    if ([r.get('shot_id') for r in alignment.get('check2_waveform_lag', [])] != [s['id'] for s in shots]
            or any(r.get('pass') is not True for r in alignment['check2_waveform_lag'])
            or alignment.get('check1_total_duration', {}).get('pass') is not True):
        raise ValueError('对齐报告没有逐镜覆盖当前分镜')
    receipt = load(root / 'alignment_execution.json')
    script = Path(delivery_evidence.__file__).with_name('align_verify.py').resolve()
    if (receipt.get('status') != 'completed' or len(receipt.get('commands', [])) != 3
            or len(receipt.get('process_outputs', [])) != 3
            or any(type(p.get('returncode')) is not int or p['returncode'] != 0 for p in receipt['process_outputs'])
            or receipt.get('report_sha256') != manifest['members']['alignment.json']
            or receipt.get('input_versions', {}).get(str(project / 'output' / record['output_name'])) != record['output_sha256']
            or receipt.get('input_versions', {}).get(str(script)) != digest(script)
            or receipt.get('lip_sync_verified') is not False):
        raise ValueError('包内对齐缺少当前实际执行回执')
    archive = Path(manifest['archive_path']).resolve()
    if archive != root / 'delivery.zip' or digest(archive) != manifest['archive_sha256']:
        raise ValueError('交付ZIP缺失或已改变')
    expected[archive] = manifest['archive_sha256']
    packed = (root / 'manifest.json').read_bytes()
    if sha(packed) != manifest['packed_manifest_sha256']: raise ValueError('包内清单已改变')
    declared = json.loads(packed)
    if declared != {k: v for k, v in manifest.items() if k not in {'archive_sha256', 'packed_manifest_sha256'}}:
        raise ValueError('包内外清单不一致')
    expected[root / 'manifest.json'] = sha(packed)
    with zipfile.ZipFile(archive) as handle:
        names = list(handle.namelist())
        if len(names) != len(set(names)) or set(names) != required | {'manifest.json'}:
            raise ValueError('ZIP条目缺失、重复或存在额外文件')
        for name, value in manifest['members'].items():
            if sha(handle.read(name)) != value: raise ValueError('ZIP内实际产物不一致')
        if handle.read('manifest.json') != packed: raise ValueError('ZIP内清单不一致')
    if any(digest(p) != s for p, s in expected.items()) or digest(path) != version:
        raise ValueError('交付包检查期间产物或依据已改变')
    return {'manifest_path': str(path), 'manifest_sha256': version, 'archive_path': str(archive),
            'archive_sha256': manifest['archive_sha256'],
            'verified_versions': {str(p): s for p, s in {**expected, path: version}.items()}}


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project_dir', type=Path)
    parser.add_argument('--output-name', default='final_mv.mp4')
    parser.add_argument('--review', type=Path)
    args = parser.parse_args()
    try:
        result = build(args.project_dir, args.output_name, review_path=args.review)
        try:
            from .delivery_state import promote, read_record
        except ImportError:
            from delivery_state import promote, read_record
        promote(args.project_dir, read_record(args.project_dir, args.output_name))
        print(json.dumps({'status': 'ready', 'archive': result['archive_path'], 'user_received': False}, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({'status': 'blocked', 'errors': [str(exc)]}, ensure_ascii=False))
        return 1


if __name__ == '__main__': raise SystemExit(main())
