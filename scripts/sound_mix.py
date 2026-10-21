"""正式J/L声音轨：母带原轨保留，独立同期声另混为可选择的无损音轨。"""
import json
import subprocess
import tempfile
from pathlib import Path
try:
    from .pipeline_contracts import digest, number
    from .transition_render import render_diegetic_audio
    from .media_verify import probe
    from .audio_master import master_encoding_args
except ImportError:
    from pipeline_contracts import digest, number
    from transition_render import render_diegetic_audio
    from media_verify import probe
    from audio_master import master_encoding_args


def require_plan(project, storyboard=None):
    project = Path(project).resolve()
    storyboard = storyboard or json.loads((project/'state/storyboard.json').read_text(encoding='utf8'))
    required = any(s.get('crew', {}).get('editing', {}).get('audio_transition') in {'J_cut', 'L_cut'} for s in storyboard.get('shots', []))
    path = project/'state/sound_edit_plan.json'
    if not path.is_file():
        if required: raise ValueError('J/L剪辑缺少sound_edit_plan.json及实际同期声')
        return None, {}
    versions = {path: digest(path)}
    plan = json.loads(path.read_text(encoding='utf8'))
    fps = number(storyboard['fps'])
    if number(plan['fps']) != fps or len(plan.get('entries', [])) != len(storyboard['shots']):
        raise ValueError('声音轨必须逐镜覆盖当前分镜与帧率')
    for i, (row, shot) in enumerate(zip(plan['entries'], storyboard['shots'])):
        editing = shot.get('crew', {}).get('editing', {})
        kind = editing.get('audio_transition', 'straight')
        kind = 'straight' if kind == 'music_hit' else kind
        frames = round((shot['window']['end']-shot['window']['start'])*fps)
        if row.get('shot_id') != shot['id'] or type(row.get('frames')) is not int or row['frames'] != frames:
            raise ValueError('声音轨镜头或帧窗口不一致')
        transition = row.get('audio_transition', {'type': 'straight', 'duration_frames': 0})
        if transition.get('type') != kind or transition.get('duration_frames', 0) != editing.get('audio_transition_duration_frames', 0):
            raise ValueError('声音转场与导演剪辑设计不一致')
        source = (project/row['audio_path']).resolve()
        source.relative_to(project)
        if row.get('role') != 'diegetic' or not isinstance(row.get('authorization'), str) or not row['authorization'].strip():
            raise ValueError('独立声音须标记diegetic并记录来源授权')
        if digest(source) != row.get('audio_sha256'): raise ValueError('同期声源版本已变化')
        versions[source] = row['audio_sha256']
    for key in ('master_gain', 'stem_gain'):
        if not 0 <= number(plan[key]) <= 1: raise ValueError('混音增益须在0至1之间')
    if number(plan['master_gain']) + number(plan['stem_gain']) > 1:
        raise ValueError('增益和不得超过1，避免自动限幅改变混音')
    return plan, versions


def render(project, master, plan, directory):
    directory = Path(directory)
    stem, mixed = directory/'diegetic.wav', directory/'mixed.wav'
    entries = [{**row, 'audio_path': str((Path(project)/row['audio_path']).resolve())} for row in plan['entries']]
    render_diegetic_audio(entries, stem, plan['fps'])
    duration = sum(r['frames'] for r in entries)/number(plan['fps'])
    cmd = ['ffmpeg', '-nostdin', '-v', 'error', '-i', str(master), '-i', str(stem), '-filter_complex',
           f"[0:a:0]aresample=48000,aformat=channel_layouts=stereo,atrim=duration={duration},asetpts=PTS-STARTPTS,volume={number(plan['master_gain'])}[m];"
           f"[1:a:0]volume={number(plan['stem_gain'])}[s];[m][s]amix=inputs=2:duration=first:normalize=0[out]",
           '-map', '[out]', '-c:a', 'pcm_s24le', str(mixed)]
    subprocess.run(cmd, check=True, capture_output=True)
    return mixed


def pcm_hash(path, stream):
    result = subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(path), '-map', f'0:a:{stream}',
                             '-c:a', 'pcm_s24le', '-f', 'hash', '-hash', 'sha256', '-'],
                            check=True, capture_output=True, text=True)
    return result.stdout.strip()


def mux(project, picture, master, output, directory):
    plan, versions = require_plan(project)
    if plan is None: raise ValueError('未声明声音剪辑计划')
    mixed = render(project, master, plan, directory)
    args = master_encoding_args(master)
    # 对每轨单独指定编码，母带仍为第一轨，既有母带载荷核验继续有效。
    codec = args[args.index('-c:a')+1]
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-i', str(picture), '-i', str(master), '-i', str(mixed),
                    '-map', '0:v:0', '-map', '1:a:0', '-map', '2:a:0', '-c:v', 'copy', '-c:a:0', codec,
                    '-c:a:1', 'alac', '-metadata:s:a:0', 'title=原始音乐母带',
                    '-metadata:s:a:1', 'title=J/L同期声混音', '-disposition:a:0', 'default',
                    '-disposition:a:1', '0', str(output)], check=True, capture_output=True)
    if pcm_hash(output, 1) != pcm_hash(mixed, 0): raise ValueError('混音轨实际载荷不一致')
    if any(digest(p) != sha for p, sha in versions.items()): raise ValueError('混音期间输入变化')


def verify(project, output, master):
    plan, versions = require_plan(project)
    if plan is None: return None
    if len([s for s in probe(output)['streams'] if s['codec_type'] == 'audio']) != 2:
        raise ValueError('正式J/L交付必须同时包含原始母带和独立混音轨')
    with tempfile.TemporaryDirectory(dir=Path(project)/'output') as temporary:
        mixed = render(project, master, plan, temporary)
        expected, actual = pcm_hash(mixed, 0), pcm_hash(output, 1)
    if expected != actual or any(digest(p) != sha for p, sha in versions.items()):
        raise ValueError('正式混音载荷或来源不一致')
    return {'status': 'pass', 'mixed_pcm_sha256': actual, 'master_preserved_separately': True,
            'versions': {str(p): sha for p, sha in versions.items()}}
