"""逐帧口部审查材料与独立签名的声画事件测量；不把音轨相关性当唇形识别。"""
import argparse
import json
import statistics
import subprocess
import uuid
from pathlib import Path
try:
    from .pipeline_contracts import digest, atomic_batch, number
    from .signed_evidence import verify
    from .media_verify import probe, ratio, is_cfr
except ImportError:
    from pipeline_contracts import digest, atomic_batch, number
    from signed_evidence import verify
    from media_verify import probe, ratio, is_cfr


def encode(value):
    return json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)


def prepare(project, video, plan_path):
    project, video, plan_path = Path(project).resolve(), Path(video).resolve(), Path(plan_path).resolve()
    video.relative_to(project)
    plan_path.relative_to(project)
    sb_path = project/'state/storyboard.json'
    timeline_path = project/'state/timeline.json'
    versions = {p:digest(p) for p in (video, plan_path, sb_path, timeline_path)}
    sb = json.loads(sb_path.read_text(encoding='utf8'))
    plan = json.loads(plan_path.read_text(encoding='utf8'))
    shots = [s for s in sb['shots'] if s['lip_sync']]
    if not shots or [r['shot_id'] for r in plan['shots']] != [s['id'] for s in shots]:
        raise ValueError('口部ROI计划须覆盖全部口型镜头，顺序一致')
    fps = number(sb['fps'])
    stream = next(s for s in probe(video)['streams'] if s['codec_type'] == 'video')
    if abs(ratio(stream['avg_frame_rate'])-fps) > 1e-6 or not is_cfr(video, fps):
        raise ValueError('逐帧口型测量要求与分镜同帧率的CFR媒体')
    run_id = uuid.uuid4().hex
    root = project/'analysis/lip_measure'/run_id
    root.mkdir(parents=True)
    record = {'schema_version': 1, 'project_id': str(project), 'media_path': str(video),
              'media_sha256': versions[video], 'fps': fps, 'inputs': {str(p):v for p,v in versions.items()},
              'status': 'preparing', 'shots': [], 'lip_sync_verified': False,
              'scope': 'dense_frames_and_original_audio_for_independent_human_phonetic_review'}
    path = project/'state/lip_measurement.json'
    revision = digest(path) if path.is_file() else None
    for shot, row in zip(shots, plan['shots']):
        roi = row['roi']
        if not isinstance(roi,list) or len(roi) != 4 or any(type(v) is not int for v in roi): raise ValueError('ROI须为x/y/width/height整数列表')
        x,y,w,h = roi
        if x<0 or y<0 or w<=0 or h<=0 or x+w>stream['width'] or y+h>stream['height']:
            raise ValueError('口部ROI越出画面')
        start, end = [round(number(shot['window'][k])*fps) for k in ('start','end')]
        directory = root/shot['id']
        # 镜头ID不得成为路径穿越入口。
        directory.resolve().relative_to(root)
        directory.mkdir()
        commands = [
            ['ffmpeg','-nostdin','-v','error','-i',str(video),'-vf',f'trim=start_frame={start}:end_frame={end},setpts=PTS-STARTPTS,crop={w}:{h}:{x}:{y}',
             '-fps_mode','passthrough','-start_number',str(start),str(directory/'%08d.png')],
            ['ffmpeg','-nostdin','-v','error','-i',str(video),'-map','0:a:0','-af',f'atrim=start={start/fps}:end={end/fps},asetpts=PTS-STARTPTS',
             '-c:a','pcm_s24le',str(directory/'audio.wav')]]
        for command in commands:
            result = subprocess.run(command, capture_output=True)
            if result.returncode: raise ValueError('口型材料实际解码失败: '+result.stderr.decode('utf8',errors='replace'))
        frames = sorted(directory.glob('*.png'))
        if [int(p.stem) for p in frames] != list(range(start,end)):
            raise ValueError('口部图像没有逐帧完整覆盖')
        record['shots'].append({'shot_id':shot['id'],'start_frame':start,'end_frame':end,'lyric_lines':shot.get('lyric_lines',[]),
                                'roi':roi,'commands':commands,'frames':{str(p.relative_to(project)):digest(p) for p in frames},
                                'audio_path':str((directory/'audio.wav').relative_to(project)), 'audio_sha256':digest(directory/'audio.wav')})
    record['status'] = 'awaiting_independent_review'
    atomic_batch({path:encode(record)},expected={path:revision, **versions})
    return record


def require(project, certificate_path=None):
    project = Path(project).resolve()
    path = project/'state/lip_measurement.json'
    versions = {path:digest(path)}
    record = json.loads(path.read_text(encoding='utf8'))
    certificate = (project/(certificate_path or 'state/lip_measure_review.json')).resolve()
    certificate.relative_to(project)
    versions[certificate] = digest(certificate)
    payload = verify(json.loads(certificate.read_text(encoding='utf8')), 'lip_measure')
    if (record.get('project_id') != str(project) or payload.get('measurement_sha256') != versions[path]
            or payload.get('media_sha256') != record['media_sha256'] or payload.get('full_audio_video_review') is not True):
        raise ValueError('独立口型复核未绑定当前逐帧材料和完整有声媒体')
    versions.update({Path(p):v for p,v in record['inputs'].items()})
    fps = number(record['fps'])
    tolerance = number(payload['tolerance_ms'])
    if not 0 < tolerance <= 100: raise ValueError('口型事件容差须为0至100毫秒，不能放宽掩盖偏移')
    if [r['shot_id'] for r in payload['shots']] != [r['shot_id'] for r in record['shots']]:
        raise ValueError('独立口型复核未覆盖全部口型镜头')
    measurements = []
    for row, source in zip(payload['shots'],record['shots']):
        for name,sha in source['frames'].items(): versions[(project/name).resolve()] = sha
        versions[(project/source['audio_path']).resolve()] = source['audio_sha256']
        if row.get('phonetic_match') != 'pass' or not str(row.get('observation','')).strip():
            raise ValueError('独立逐帧唇形/音素复核未通过')
        events = row['events']
        if len(events)<2 or len({e['video_frame'] for e in events}) != len(events):
            raise ValueError('每个口型镜头至少需要两个不同的实测声画事件')
        lines = set()
        for event in events:
            frame = event['video_frame']
            audio_time = number(event['audio_time_sec'])
            if (type(frame) is not int or not source['start_frame'] <= frame < source['end_frame']
                    or not source['start_frame']/fps <= audio_time < source['end_frame']/fps
                    or not str(event.get('phoneme','')).strip()
                    or event.get('lyric_line') not in source['lyric_lines']):
                raise ValueError('声画事件缺少实际帧/音素/歌词归属或超出镜头')
            lines.add(event['lyric_line'])
            measurements.append(1000*(frame/fps-audio_time))
        if lines != set(source['lyric_lines']): raise ValueError('声画事件没有覆盖本镜全部演唱歌词')
    if not measurements or max(abs(v) for v in measurements)>tolerance:
        raise ValueError('实测嘴型与音素事件偏移超过验收容差')
    if any(digest(p)!=v for p,v in versions.items()): raise ValueError('口型测量源、图片或复核材料已改变')
    return {'status':'pass','scope':'independent_signed_human_phonetic_and_frame_event_measurement',
            'automatic_phoneme_recognition':False,'max_abs_lag_ms':max(abs(v) for v in measurements),
            'median_lag_ms':statistics.median(measurements),'events':len(measurements),
            'versions':{str(p):v for p,v in versions.items()}}


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('prepare','check'))
    parser.add_argument('project',type=Path)
    parser.add_argument('--video',type=Path)
    parser.add_argument('--plan',type=Path)
    args=parser.parse_args()
    if args.action=='prepare' and (args.video is None or args.plan is None): parser.error('prepare需要--video和--plan')
    result=prepare(args.project,args.video,args.plan) if args.action=='prepare' else require(args.project)
    print(encode(result))
