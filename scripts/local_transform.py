"""固定本地裁切/缩放/按帧重剪执行器；保留来源，不变速、不自动批准。"""
import json
import subprocess
from subprocess import Popen
import hashlib
import uuid
from pathlib import Path
from fractions import Fraction
from datetime import datetime, timezone

try:
    from .pipeline_contracts import digest, atomic_batch, require_no_transaction
    from .media_verify import probe, is_cfr
    from .repair_state import path_for
    from .ledger import locked
except ImportError:
    from pipeline_contracts import digest, atomic_batch, require_no_transaction
    from media_verify import probe, is_cfr
    from repair_state import path_for
    from ledger import locked


def load(path):
    return json.loads(Path(path).read_text(encoding='utf8'))


def origin_module():
    try:
        from . import media_origin
    except ImportError:
        import media_origin
    return media_origin


def compile_plan(plan, metadata):
    required = {'source_clip_id', 'replacement_clip_id', 'repair_id', 'repair_plan_sha256',
                'frame_ranges', 'output_width', 'output_height', 'scale_method', 'audio_policy', 'reason'}
    if not isinstance(plan, dict) or required - set(plan) or set(plan) - required - {'crop', 'temporal_reordering_reason', 'aspect_change_reason'}:
        raise ValueError('本地变换计划字段缺失或存在未支持操作，不接受任意滤镜/命令')
    if any(not isinstance(plan[k], str) or not plan[k].strip() for k in
           ('source_clip_id', 'replacement_clip_id', 'reason')):
        raise ValueError('须明确源素材、新素材ID和变换原因')
    if plan['source_clip_id'] == plan['replacement_clip_id']: raise ValueError('不能覆盖原素材')
    video = next(s for s in metadata['streams'] if s['codec_type'] == 'video')
    fps = Fraction(video['avg_frame_rate'])
    total = int(video['nb_read_frames'])
    ranges = plan['frame_ranges']
    if not isinstance(ranges, list) or not ranges: raise ValueError('须明确逐段源帧范围')
    intervals, count = [], 0
    for item in ranges:
        if not isinstance(item, dict) or set(item) != {'start_frame', 'end_frame'}:
            raise ValueError('源帧范围必须明确开始/结束帧')
        a, b = item['start_frame'], item['end_frame']
        if type(a) is not int or type(b) is not int or not 0 <= a < b <= total:
            raise ValueError('帧范围须为源视频内的正向整数帧，拒绝布尔值')
        if any(a < d and b > c for c, d in intervals):
            raise ValueError('禁止重复源帧、循环或克隆画面掩盖素材不足')
        intervals.append((a, b)); count += b-a
    reordered = any(a < previous[1] for previous, (a, _) in zip(intervals, intervals[1:]))
    if reordered and (not isinstance(plan.get('temporal_reordering_reason'), str) or not plan['temporal_reordering_reason'].strip()):
        raise ValueError('重排源时间须明确说明原因，并在新章审片检查动作/状态连续性')
    width, height = video['width'], video['height']
    crop = plan.get('crop', {'x': 0, 'y': 0, 'width': width, 'height': height})
    if (not isinstance(crop, dict) or set(crop) != {'x', 'y', 'width', 'height'}
            or any(type(v) is not int for v in crop.values())
            or crop['x'] < 0 or crop['y'] < 0 or crop['width'] <= 0 or crop['height'] <= 0
            or crop['x']+crop['width'] > width or crop['y']+crop['height'] > height
            or any(crop[k] % 2 for k in crop)):
        raise ValueError('裁切须为画面内明确偶数像素矩形，不静默取整')
    w, h = plan['output_width'], plan['output_height']
    if any(type(v) is not int or v <= 0 or v % 2 for v in (w, h)):
        raise ValueError('输出画布须为正偶数像素')
    if plan['scale_method'] not in {'lanczos', 'bicubic'}: raise ValueError('须明确支持的缩放算法')
    audio = next((s for s in metadata['streams'] if s['codec_type'] == 'audio'), None)
    if plan['audio_policy'] != ('preserve_selected_source' if audio else 'source_has_no_audio'):
        raise ValueError('须如实保留源选段音轨，不默认静音或拿母带替换')
    merged = []
    for a, b in intervals:
        if merged and merged[-1][1] == a: merged[-1] = (merged[-1][0], b)
        else: merged.append((a, b))
    operations = set()
    if merged != [(0, total)]: operations.add('frame_selection')
    if reordered: operations.add('reorder')
    if crop != {'x': 0, 'y': 0, 'width': width, 'height': height}: operations.add('crop')
    if (w, h) != (crop['width'], crop['height']): operations.add('scale')
    stretched = w*crop['height'] != h*crop['width']
    if stretched:
        if not isinstance(plan.get('aspect_change_reason'), str) or not plan['aspect_change_reason'].strip():
            raise ValueError('禁止静默拉伸画面比例；须声明原因及aspect_stretch许可')
        operations.add('aspect_stretch')
    if not operations: raise ValueError('没有有效变换，不能仅重编码原失败视频冒充修复')
    filters = [f'[0:v:0]split={len(intervals)}' + ''.join(f'[vs{i}]' for i in range(len(intervals)))]
    if audio: filters.append(f'[0:a:0]asplit={len(intervals)}' + ''.join(f'[as{i}]' for i in range(len(intervals))))
    for i, (a, b) in enumerate(intervals):
        filters.append(f'[vs{i}]trim=start_frame={a}:end_frame={b},setpts=PTS-STARTPTS,'
                       f'crop={crop["width"]}:{crop["height"]}:{crop["x"]}:{crop["y"]},'
                       f'scale={w}:{h}:flags={plan["scale_method"]},setsar=1,format=yuv420p[v{i}]')
        if audio:
            sr = int(audio['sample_rate'])
            start, end = round(Fraction(a)*sr/fps), round(Fraction(b)*sr/fps)
            filters.append(f'[as{i}]atrim=start_sample={start}:end_sample={end},asetpts=PTS-STARTPTS[a{i}]')
    labels = ''.join(f'[v{i}]' + (f'[a{i}]' if audio else '') for i in range(len(intervals)))
    filters.append(f'{labels}concat=n={len(intervals)}:v=1:a={int(audio is not None)}[v]' + ('[a]' if audio else ''))
    return {'filter_complex': ';'.join(filters), 'frame_count': count, 'fps': float(fps), 'fps_ratio': str(fps),
            'operations': sorted(operations), 'has_audio': audio is not None,
            'source_width': width, 'source_height': height, 'output_width': w, 'output_height': h,
            'upscaled': w > crop['width'] or h > crop['height'], 'source_frame_ranges': ranges,
            'aspect_stretched': stretched,
            'audio_sample_rate': int(audio['sample_rate']) if audio else None,
            'audio_channels': audio['channels'] if audio else None}


def command(source, target, compiled):
    result = ['ffmpeg', '-y', '-v', 'error', '-i', str(source), '-filter_complex', compiled['filter_complex'], '-map', '[v]']
    if compiled['has_audio']:
        result += ['-map', '[a]', '-c:a', 'alac', '-ar', str(compiled['audio_sample_rate']), '-ac', str(compiled['audio_channels'])]
    else: result += ['-an']
    return result + ['-c:v', 'libx264', '-crf', '18', '-pix_fmt', 'yuv420p', '-fps_mode', 'passthrough', str(target)]


def audio_commands(source, target, compiled):
    if not compiled['has_audio']: return []
    ranges = compiled['source_frame_ranges']
    rate = compiled['audio_sample_rate']
    fps = Fraction(compiled['fps_ratio'])
    filters = [f'[0:a:0]asplit={len(ranges)}' + ''.join(f'[s{i}]' for i in range(len(ranges)))]
    for i, item in enumerate(ranges):
        start, end = round(Fraction(item['start_frame'])*rate/fps), round(Fraction(item['end_frame'])*rate/fps)
        filters.append(f'[s{i}]atrim=start_sample={start}:end_sample={end},asetpts=PTS-STARTPTS[a{i}]')
    filters.append(''.join(f'[a{i}]' for i in range(len(ranges))) + f'concat=n={len(ranges)}:v=0:a=1[a]')
    suffix = ['-c:a', 'pcm_s24le', '-f', 'hash', '-hash', 'sha256', '-']
    return [['ffmpeg', '-v', 'error', '-i', str(source), '-filter_complex', ';'.join(filters), '-map', '[a]', *suffix],
            ['ffmpeg', '-v', 'error', '-i', str(target), '-map', '0:a:0', *suffix]]


def run(project, plan_path):
    project = Path(project).resolve()
    with locked(project / 'state/local_transform.index'):
        return _run(project, plan_path)


def _run(project, plan_path):
    require_no_transaction(project)
    plan_path = origin_module().local(project, plan_path)
    original_plan = plan_path.read_bytes()
    plan = json.loads(original_plan)
    registry_path = project / 'state/generated_clips.json'
    repair_path = path_for(project, plan['repair_id'])
    registry_bytes, repair_bytes = registry_path.read_bytes(), repair_path.read_bytes()
    versions = {p: 'sha256:' + hashlib.sha256(data).hexdigest() for p, data in
                ((plan_path, original_plan), (registry_path, registry_bytes), (repair_path, repair_bytes))}
    registry, repair = json.loads(registry_bytes), json.loads(repair_bytes)
    parent = registry['clips'][plan['source_clip_id']]
    if (repair.get('project_id') != str(project) or repair.get('status') != 'open'
            or repair.get('failed_clip_id') != plan['source_clip_id'] or repair.get('failed_clip') != parent
            or plan.get('repair_plan_sha256') != repair['plan_sha256']
            or repair['plan'].get('structural_failure') is True):
        raise ValueError('本地变换须绑定当前开放的局部返修和原失败素材，不代替结构重建')
    if plan['replacement_clip_id'] in registry['clips']: raise ValueError('目标素材已存在，禁止覆盖')
    origin_module().require(project, parent, versions)
    source = origin_module().local(project, parent['path'])
    metadata = probe(source)
    compiled = compile_plan(plan, metadata)
    if (type(parent.get('frame_count')) is not int or isinstance(parent.get('fps'), bool)
            or abs(compiled['fps']-float(parent['fps'])) > 1e-6
            or int(next(s for s in metadata['streams'] if s['codec_type'] == 'video')['nb_read_frames']) != parent['frame_count']
            or not is_cfr(source, compiled['fps'])):
        raise ValueError('本地变换须消费已实测帧数和CFR，不以平均帧率补帧或变速')
    allowed = repair['plan'].get('permitted_local_operations')
    if (not isinstance(allowed, list) or any(not isinstance(op, str) for op in allowed)
            or not set(allowed) <= {'crop', 'scale', 'frame_selection', 'reorder', 'aspect_stretch'}
            or not set(compiled['operations']) <= set(allowed)):
        raise ValueError('实际变换超出冻结返修计划permitted_local_operations')
    run_id = uuid.uuid4().hex
    root = project / 'analysis/local_transform_runs' / run_id
    root.mkdir(parents=True, exist_ok=False)
    frozen = root / 'plan.json'
    frozen.write_bytes(original_plan)
    frozen_repair = root / 'repair.json'
    frozen_repair.write_bytes(repair_bytes)
    target, receipt_path = root / 'result.mp4', root / 'execution.json'
    code_versions = {str(Path(__file__).resolve()): digest(__file__)}
    receipt = {'run_id': run_id, 'project_id': str(project), 'status': 'running', 'processes': [],
               'repair_id': plan['repair_id'], 'repair_plan_sha256': repair['plan_sha256'],
               'source_clip': parent, 'source_probe': metadata, 'compiled': compiled,
               'plan_sha256': digest(frozen), 'code_versions': code_versions,
               'repair_snapshot_sha256': digest(frozen_repair),
               'input_versions': {str(p): v for p, v in versions.items()},
               'output_path': str(target), 'artistic_approval': False, 'dynamic_verified': False}
    def save(): receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf8')
    def execute(cmd):
        entry = {'command': cmd, 'started_at': datetime.now(timezone.utc).isoformat(), 'process_started': False}
        receipt['processes'].append(entry); save()
        process = None
        try:
            process = Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            entry.update(process_started=True, pid=process.pid); save()
            out, err = process.communicate()
            entry.update(returncode=process.returncode, stdout=out, stderr=err)
            if process.returncode: raise ValueError('本地变换/解码命令失败')
        except BaseException as exc:
            entry['error_type'] = type(exc).__name__
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    out, err = process.communicate(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    out, err = process.communicate(timeout=10)
                entry.update(returncode=process.returncode, stdout=out, stderr=err, interrupted_process_stopped=True)
            raise
        finally:
            entry['completed_at'] = datetime.now(timezone.utc).isoformat(); save()
    save()
    try:
        execute(command(source, target, compiled))
        execute(['ffmpeg', '-v', 'error', '-i', str(target), '-map', '0:v:0', '-map', '0:a?', '-f', 'null', '-'])
        for cmd in audio_commands(source, target, compiled): execute(cmd)
        if compiled['has_audio']:
            hashes = [p['stdout'].strip() for p in receipt['processes'][-2:]]
            if not hashes[0].startswith('SHA256=') or hashes[0] != hashes[1]:
                raise ValueError('实际选段音轨与衍生结果不同，不能静默改变或补声音')
            receipt['audio_integrity'] = {'source_selection_pcm': hashes[0], 'output_pcm': hashes[1],
                                          'scope': 'decoded_pcm_s24le_selected_source_exact_match'}
        output = probe(target)
        video = next(s for s in output['streams'] if s['codec_type'] == 'video')
        audio = next((s for s in output['streams'] if s['codec_type'] == 'audio'), None)
        if (int(video['nb_read_frames']) != compiled['frame_count'] or not is_cfr(target, compiled['fps'])
                or (video['width'], video['height']) != (compiled['output_width'], compiled['output_height'])
                or (audio is not None) != compiled['has_audio']
                or (audio and (int(audio['sample_rate']), audio['channels']) !=
                    (compiled['audio_sample_rate'], compiled['audio_channels']))):
            raise ValueError('实际变换帧数/画布/帧率/声音不符合冻结计划')
        receipt.update(status='completed', registration_status='registered_pending_review',
                       completed_at=datetime.now(timezone.utc).isoformat(), output_sha256=digest(target), output_probe=output)
        receipt_payload = json.dumps(receipt, ensure_ascii=False, indent=2)
        oid = project / 'state/media_origins' / f'{uuid.uuid4().hex}.json'
        record = {'schema_version': 1, 'kind': 'derived', 'project_id': str(project),
                  'chapter_id': parent['chapter_id'], 'shot_ids': parent['shot_ids'],
                  'path': str(target.relative_to(project)), 'sha256': receipt['output_sha256'],
                  'parent_clip': parent, 'plan_path': str(frozen.relative_to(project)), 'plan_sha256': receipt['plan_sha256'],
                  'repair_snapshot_path': str(frozen_repair.relative_to(project)), 'repair_snapshot_sha256': digest(frozen_repair),
                  'execution_path': str(receipt_path.relative_to(project)), 'compiled': compiled,
                  'execution_sha256': 'sha256:' + hashlib.sha256(receipt_payload.encode('utf8')).hexdigest(),
                  'repair_id': plan['repair_id'], 'repair_plan_sha256': repair['plan_sha256'],
                  'scope': 'executed_local_transform_not_dynamic_review', 'artistic_approval': False, 'dynamic_verified': False}
        clip = {**parent, 'path': record['path'], 'sha256': record['sha256'], 'frame_count': compiled['frame_count'],
                'fps': compiled['fps'], 'origin_receipt_path': str(oid.relative_to(project)), 'status': 'pending', 'evidence': ''}
        registry['clips'][plan['replacement_clip_id']] = clip
        versions[receipt_path] = digest(receipt_path)
        atomic_batch({oid: json.dumps(record, ensure_ascii=False, indent=2),
                      registry_path: json.dumps(registry, ensure_ascii=False, indent=2),
                      receipt_path: receipt_payload},
                     expected={**versions, **{Path(p): v for p, v in code_versions.items()},
                               frozen: receipt['plan_sha256'], frozen_repair: receipt['repair_snapshot_sha256'],
                               target: receipt['output_sha256'], oid: None})
        return clip
    except BaseException as exc:
        receipt.update(status='failed', registration_status='failed_unregistered', error_type=type(exc).__name__,
                       completed_at=datetime.now(timezone.utc).isoformat())
        save()
        raise


def require_origin(project, clip, record, versions, seen):
    parent = record['parent_clip']
    origin_module().require(project, parent, versions, _seen=seen)
    frozen = origin_module().local(project, record['plan_path'])
    execution = origin_module().local(project, record['execution_path'])
    frozen_repair = origin_module().local(project, record['repair_snapshot_path'])
    versions.update({frozen: digest(frozen), execution: digest(execution), frozen_repair: digest(frozen_repair)})
    receipt, plan = load(execution), load(frozen)
    repair = load(frozen_repair)
    compiled = compile_plan(plan, receipt['source_probe'])
    source, target = origin_module().local(project, parent['path']), origin_module().local(project, clip['path'])
    if (versions[execution] != record.get('execution_sha256')
            or versions[frozen] != record['plan_sha256'] or receipt.get('plan_sha256') != record['plan_sha256']
            or receipt.get('project_id') != str(project) or receipt.get('status') != 'completed'
            or receipt.get('registration_status') != 'registered_pending_review'
            or receipt.get('source_clip') != parent or receipt.get('output_sha256') != clip['sha256']
            or versions[frozen_repair] != record.get('repair_snapshot_sha256')
            or receipt.get('repair_snapshot_sha256') != versions[frozen_repair]
            or repair.get('project_id') != str(project) or repair.get('status') != 'open'
            or repair.get('failed_clip') != parent or repair.get('failed_clip_id') != plan['source_clip_id']
            or repair.get('repair_id') != plan['repair_id'] or repair.get('plan_sha256') != plan['repair_plan_sha256']
            or repair['plan'].get('structural_failure') is True
            or not set(compiled['operations']) <= set(repair['plan'].get('permitted_local_operations', []))
            or Path(receipt.get('output_path', '')).resolve() != target
            or record.get('repair_id') != plan['repair_id'] or receipt.get('repair_id') != plan['repair_id']
            or record.get('repair_plan_sha256') != plan['repair_plan_sha256']
            or receipt.get('repair_plan_sha256') != plan['repair_plan_sha256']
            or record.get('chapter_id') != parent['chapter_id'] or record.get('shot_ids') != parent['shot_ids']
            or compiled != record.get('compiled') or compiled != receipt.get('compiled')
            or clip.get('frame_count') != compiled['frame_count'] or float(clip.get('fps', 0)) != compiled['fps']):
        raise ValueError('衍生素材缺少对应冻结计划和实际执行结果')
    expected_commands = [command(source, target, compiled),
                         ['ffmpeg', '-v', 'error', '-i', str(target), '-map', '0:v:0', '-map', '0:a?', '-f', 'null', '-'],
                         *audio_commands(source, target, compiled)]
    processes = receipt.get('processes', [])
    if (len(processes) != len(expected_commands) or [p.get('command') for p in processes] != expected_commands
            or any(p.get('process_started') is not True or type(p.get('pid')) is not int
                   or type(p.get('returncode')) is not int or p['returncode'] != 0 or not p.get('completed_at') for p in processes)):
        raise ValueError('衍生素材执行命令或真实退出结果不一致')
    if compiled['has_audio']:
        hashes = [p.get('stdout', '').strip() for p in processes[-2:]]
        audio = receipt.get('audio_integrity', {})
        if (not hashes[0].startswith('SHA256=') or hashes[0] != hashes[1]
                or audio.get('source_selection_pcm') != hashes[0] or audio.get('output_pcm') != hashes[1]):
            raise ValueError('衍生素材声音保留回执不一致')
    expected_codes = {str(Path(__file__).resolve()): digest(__file__)}
    if receipt.get('code_versions') != expected_codes: raise ValueError('变换执行器版本已改变，须重新核验')
    versions.update({Path(p): v for p, v in expected_codes.items()})


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project_dir', type=Path)
    parser.add_argument('--plan', type=Path, required=True)
    args = parser.parse_args()
    try:
        clip = run(args.project_dir, args.plan)
        print(json.dumps({'status': 'pending_review', 'clip': clip, 'artistic_approval': False}, ensure_ascii=False))
        return 0
    except Exception as exc:
        print(json.dumps({'status': 'blocked', 'errors': [str(exc)]}, ensure_ascii=False))
        return 1


if __name__ == '__main__': raise SystemExit(main())
