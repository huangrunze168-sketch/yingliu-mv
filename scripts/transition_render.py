"""后端无关的本地转场：固定音乐时间窗的叠化，以及独立同期声音轨J/L剪辑。"""
import os
import subprocess
import tempfile
from pathlib import Path
try:
    from .media_verify import probe, ratio, is_cfr
    from .pipeline_contracts import digest, number
except ImportError:
    from media_verify import probe, ratio, is_cfr
    from pipeline_contracts import digest, number


def join_picture(project, entries, output, fps, width, height):
    """叠化从切点开始，使用前镜真实后延素材，不缩短音乐时间轴。"""
    fps = number(fps)
    if fps <= 0 or not entries: raise ValueError('转场须有正帧率和镜头')
    command = ['ffmpeg', '-y', '-v', 'error', '-filter_complex_threads', '1']
    graph, total, label = [], 0, 'v0'
    for i, entry in enumerate(entries):
        command += ['-i', str(Path(project) / entry['target_path'])]
        graph.append(f'[{i}:v]fps={fps},scale={width}:{height}:force_original_aspect_ratio=increase,'
                     f'crop={width}:{height},setsar=1,settb=AVTB,setpts=PTS-STARTPTS[v{i}]')
        if i:
            previous = entries[i-1]
            transition = previous.get('video_transition', {'type': 'hard_cut'})
            if transition['type'] == 'dissolve':
                frames = transition['duration_frames']
                if type(frames) is not int or frames <= 0 or previous.get('postroll_frames') != frames:
                    raise ValueError('叠化缺少真实后延帧')
                graph.append(f'[{label}][v{i}]xfade=transition=fade:duration={frames/fps}:'
                             f'offset={total/fps}[joined{i}]')
            elif transition['type'] in {'hard_cut', 'match_cut'}:
                graph.append(f'[{label}][v{i}]concat=n=2:v=1:a=0[joined{i}]')
            else:
                raise ValueError('未知画面转场')
            label = f'joined{i}'
        total += entry['frames']
    command += ['-filter_complex', ';'.join(graph), '-map', f'[{label}]', '-an',
                '-frames:v', str(total), '-r', str(fps), '-c:v', 'libx264', '-crf', '18',
                '-pix_fmt', 'yuv420p', str(output)]
    subprocess.run(command, check=True, capture_output=True)
    video = next(s for s in probe(output)['streams'] if s['codec_type'] == 'video')
    if int(video['nb_read_frames']) != total or abs(ratio(video['avg_frame_rate'])-fps)>1e-6 or not is_cfr(output, fps):
        raise ValueError('转场输出帧数/帧率不符合音乐窗口')


def render_diegetic_audio(entries, output, fps, sample_rate=48000, channels=2):
    """独立同期声/音效轨；绝不移动、覆盖或冒充用户音乐母带。

    每条entry有frames、audio_path、audio_sha256、audio_in_frame，出镜边界可有
    audio_transition={type:J_cut|L_cut|straight,duration_frames:N}。
    J_cut让下一镜声音提前N帧；L_cut让上一镜声音延续N帧。须有真实前/后延声音。
    """
    fps = number(fps)
    if fps <= 0 or not entries: raise ValueError('声音剪辑须有正帧率和镜头')
    if type(sample_rate) is not int or sample_rate <= 0 or type(channels) is not int or channels not in (1, 2):
        raise ValueError('声音轨规格无效')
    output = Path(output).resolve()
    if output.exists(): raise FileExistsError('禁止覆盖已存在声音轨')
    command = ['ffmpeg', '-v', 'error', '-filter_complex_threads', '1']
    graph, sources, total, shift = [], {}, 0, 0
    for i, entry in enumerate(entries):
        frames, start = entry['frames'], entry['audio_in_frame']
        if type(frames) is not int or frames <= 0 or type(start) is not int or start < 0:
            raise ValueError('声音窗口须为整数帧')
        transition = entry.get('audio_transition', {'type': 'straight'})
        kind = transition.get('type')
        duration = transition.get('duration_frames', 0)
        if type(duration) is not int or duration < 0 or kind not in {'straight', 'J_cut', 'L_cut'}:
            raise ValueError('声音转场类型或帧数无效')
        if kind != 'straight' and (duration <= 0 or i == len(entries)-1):
            raise ValueError('J/L剪辑须有正时长和下一镜')
        if kind == 'straight' and duration != 0:
            raise ValueError('straight不能声明非零声音偏移')
        if kind == 'J_cut' and duration >= frames:
            raise ValueError('J_cut不能提前越过当前镜头起点')
        if kind == 'L_cut' and duration >= entries[i+1]['frames']:
            raise ValueError('L_cut不能延续越过下一镜末尾')
        outgoing = -duration if kind == 'J_cut' else duration if kind == 'L_cut' else 0
        left, right = start + shift, start + frames + outgoing
        if left < 0 or right <= left: raise ValueError('缺少声音前延/窗口被转场耗尽')
        path = Path(entry['audio_path']).resolve()
        actual = digest(path)
        if actual != entry['audio_sha256']: raise ValueError('独立声音素材指纹不匹配')
        audio = next(s for s in probe(path)['streams'] if s['codec_type'] == 'audio')
        if right/fps > float(audio.get('duration', probe(path)['format']['duration']))+1e-6:
            raise ValueError('缺少真实声音后延素材')
        sources[path] = actual
        command += ['-i', str(path)]
        graph.append(f'[{i}:a]atrim=start={left/fps}:end={right/fps},asetpts=PTS-STARTPTS,'
                     f'aresample={sample_rate},aformat=sample_fmts=s32:channel_layouts='
                     f'{"mono" if channels == 1 else "stereo"}[a{i}]')
        total += frames
        shift = outgoing
    graph.append(''.join(f'[a{i}]' for i in range(len(entries))) + f'concat=n={len(entries)}:v=0:a=1[out]')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent) as folder:
        staged = Path(folder) / 'diegetic.wav'
        subprocess.run(command + ['-filter_complex', ';'.join(graph), '-map', '[out]',
                                  '-c:a', 'pcm_s24le', str(staged)], check=True, capture_output=True)
        data = probe(staged)
        if abs(float(data['format']['duration']) - total/fps) > 1/sample_rate+1e-6:
            raise ValueError('声音剪辑改变总时间轴')
        if any(digest(path) != sha for path, sha in sources.items()):
            raise ValueError('声音素材在渲染期间改变')
        # 独占创建，不能因并发任务而覆盖另一份成功声音轨。
        os.link(staged, output)
    return {'status': 'rendered', 'scope': 'diegetic_stem_only', 'path': str(output),
            'sha256': digest(output), 'duration_sec': total/fps,
            'master_audio_modified': False}


if __name__ == '__main__':
    import argparse
    import json
    parser = argparse.ArgumentParser(description='渲染独立同期声J/L轨，不改变音乐母带')
    parser.add_argument('audio_plan', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    plan = json.loads(args.audio_plan.read_text(encoding='utf-8'))
    print(json.dumps(render_diegetic_audio(plan['entries'], args.output, plan['fps'],
                     plan.get('sample_rate', 48000), plan.get('channels', 2)), ensure_ascii=False))
