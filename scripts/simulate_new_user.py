#!/usr/bin/env python3
"""从分享 ZIP 模拟安装并离线出片；所有审查/确认是隔离夹具，不是真实验收。"""
import argparse
import hashlib
import json
import subprocess
import sys
import time
import zipfile
from pathlib import Path


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf8')


def load(path):
    return json.loads(path.read_text(encoding='utf8'))


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Simulation:
    def __init__(self, archive, output):
        self.archive = archive.resolve()
        self.output = output.resolve()
        self.output.mkdir(parents=True, exist_ok=False)
        self.cwd = self.output / '新用户工作目录'
        self.cwd.mkdir()
        self.install = self.output / '新用户主目录/.codex/skills'
        self.install.mkdir(parents=True)
        self.steps = []
        self.project = self.output / '项目/离线音乐 MV'

    def command(self, args, expected=0, label=''):
        started = time.monotonic()
        row = subprocess.run(list(map(str, args)), cwd=self.cwd, capture_output=True,
                             text=True, encoding='utf8', errors='replace', timeout=120)
        record = {'step': len(self.steps) + 1, 'label': label, 'command': list(map(str, args)),
                  'cwd': str(self.cwd), 'exit': row.returncode, 'expected_exit': expected,
                  'seconds': round(time.monotonic() - started, 3), 'stdout': row.stdout, 'stderr': row.stderr}
        self.steps.append(record)
        save(self.output / 'command-log.json', self.steps)
        if row.returncode != expected:
            raise ValueError(f'{label} 退出码不符：{row.returncode}，详见 command-log.json\n{row.stdout}\n{row.stderr}')
        return row.stdout

    def cli(self, command, *args, expected=0):
        prefix = [sys.executable, '-X', 'utf8', self.skill / 'scripts/mv.py', command]
        if command != 'doctor':
            prefix.append(self.project)
        result = self.command([*prefix, *args], expected, label=command)
        return json.loads(result)

    def approve(self, stage):
        current = self.cli('status')
        assert current['stage'] == stage and current['status'] == 'waiting_approval', current
        return self.cli('approve', '--reviewer', 'SIMULATION_FIXTURE',
                        '--evidence', f'隔离模拟 {stage}；非用户真实批准、非实际观看')

    def observation(self, media, scope='audiovisual', manifest=None):
        record = {'media': media, 'scope': scope, 'observer': 'SIMULATION_FIXTURE', 'simulation': True,
                  'evidence': '隔离流程夹具，不声明人或 Agent 真实完整听看；技术信息另行测量',
                  'findings': [], 'unverified': [], 'references': []}
        if manifest:
            record['frame_manifest'] = manifest
        draft = 'state/drafts/observation.json'
        save(self.project / draft, record)
        return self.cli('observe', '--record', draft)['observation']

    def reviewed(self, media, scope='audiovisual'):
        return {'media': media, 'observations': [self.observation(media, scope)], 'decision': 'approve',
                'evidence': '隔离测试模拟审查，不是真实观看或艺术验收', 'unverified': [], 'simulation': True}

    def stage_review(self, stage, media, filename):
        ref = self.observation(media)
        self.cli('review-init', '--stage', stage, '--media', media, '--observations', ref)
        assert self.cli('status')['status'] == 'blocked'
        item = load(self.project / filename)
        item.update(decision='approve', evidence='隔离测试夹具；非真实观看', unverified=[])
        save(self.project / filename, item)

    def run(self):
        with zipfile.ZipFile(self.archive) as package:
            if package.testzip():
                raise ValueError('ZIP 校验失败')
            for name in package.namelist():
                (self.install / name).resolve().relative_to(self.install.resolve())
                if not name.startswith('yingliu-mv/'):
                    raise ValueError('ZIP 目录名不是 yingliu-mv')
            package.extractall(self.install)
        self.skill = self.install / 'yingliu-mv'
        environment = self.cli('doctor', '--smoke')
        assert environment['local_ready'] and environment['smoke']['status'] == 'passed'
        self.cli('init', '--simulation')
        # 同目录重装/初始化不能覆盖项目。
        self.cli('init', '--simulation', expected=1)
        self.cli('pack')
        self.cli('render', '--mode', 'final', expected=1)
        cfg = load(self.project / 'project.json')
        cfg.update(width=640, height=360, fps=24, backend=None)
        save(self.project / 'project.json', cfg)
        self.command(['ffmpeg', '-v', 'error', '-nostdin', '-y', '-f', 'lavfi', '-i',
                      'sine=frequency=440:sample_rate=48000:duration=8', '-ac', '2', self.project / cfg['audio']], label='合成测试母带')
        (self.project / cfg['lyrics']).write_text('[instrumental] 合成音频；无人声，无真实歌词\n', encoding='utf8')
        self.approve('prepare')
        self.cli('pack')
        self.cli('compile', expected=1)
        evidence = {'kind': 'synthetic_timing', 'simulation': True, 'duration': 8,
                    'note': 'FFmpeg 合成信号固定八秒；非 ASR、非真实歌词对齐'}
        save(self.project / 'input/timing_evidence.json', evidence)
        save(self.project / 'state/timeline.json', {'duration': 8, 'verified': True,
            'evidence_path': 'input/timing_evidence.json', 'segments': [{'start': 0, 'end': 8, 'text': '[instrumental]'}]})
        save(self.project / 'state/concept.json', {'treatment': '彩色测试场景随时间推进；仅验证工作流',
            'visual_direction': '无人物、无演唱、动态测试图', 'music_sections': [{'start': 0, 'end': 8, 'type': '合成器信号'}]})
        self.approve('concept')
        # 六个非整帧时长镜头，同时覆盖横竖画幅、异帧率素材和有空格路径。
        boundaries = [0, 1.37, 2.51, 4.02, 5.31, 6.79, 8]
        shots = []
        for i in range(6):
            media = f'clips/本地镜头 {i+1}.mp4'
            picture = f'input/关键帧 {i+1}.png'
            source_size = '360x640' if i % 2 else '640x360'
            fps = 30 if i % 2 else 24
            source = f'testsrc2=size={source_size}:rate={fps}:duration=3'
            self.command(['ffmpeg', '-v', 'error', '-nostdin', '-y', '-f', 'lavfi', '-i', source,
                          '-vf', f'hue=h={i*45}', '-an', '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                          self.project / media], label=f'本地合成镜头{i+1}')
            self.command(['ffmpeg', '-v', 'error', '-nostdin', '-y', '-ss', '0.2', '-i', self.project / media,
                          '-frames:v', '1', self.project / picture], label=f'提取关键帧{i+1}')
            shots.append({'id': f'SH{i+1:03}', 'scene': 'TEST', 'start': boundaries[i], 'end': boundaries[i+1],
                          'image': picture, 'action': '测试图运动', 'camera': '固定机位', 'cut_reason': '测试分段切点', 'lip_sync': False})
        save(self.project / 'state/design.json', {'global': {'style': '离线合成测试'}, 'scenes': {'TEST': {}}, 'shots': shots})
        self.cli('compile')
        self.cli('pack', '--shot', 'SH003')
        self.cli('render', '--mode', 'preview')
        self.stage_review('storyboard', 'output/animatic_preview.mp4', 'state/animatic_review.json')
        self.approve('storyboard')
        frame = self.cli('frames', '--media', 'clips/本地镜头 1.mp4', '--count', '4')
        sampled = self.observation('clips/本地镜头 1.mp4', 'sampled_frames', frame['manifest'])
        self.cli('observations', '--media', 'clips/本地镜头 1.mp4')
        # 抽帧不能作为动态样片通过。
        save(self.project / 'state/pilot_review.json', {'media': 'clips/本地镜头 1.mp4', 'observations': [sampled],
            'decision': 'approve', 'evidence': '故意反例', 'unverified': [], 'simulation': True})
        assert self.cli('status')['status'] == 'blocked'
        save(self.project / 'state/pilot_review.json', self.reviewed('clips/本地镜头 1.mp4'))
        self.approve('pilot')
        self.cli('pack')
        clips, reviews = [], []
        for i in range(6):
            media = f'clips/本地镜头 {i+1}.mp4'
            receipt = f'state/origins/SH{i+1:03}.json'
            save(self.project / receipt, {'origin': 'ffmpeg_lavfi', 'simulation': True,
                'media': media, 'cost': 0, 'currency': 'CNY', 'source_sha256': digest(self.project / media)})
            clips.append({'media': media, 'in': 0.21, 'duration': boundaries[i+1] - boundaries[i], 'receipt': receipt})
            reviews.append(self.reviewed(media))
        save(self.project / 'state/edit.json', {'clips': clips})
        save(self.project / 'state/clips_review.json', {'reviews': reviews})
        # 选段超界须在批准前被发现。
        bad = json.loads(json.dumps(clips))
        bad[0]['in'] = 99
        save(self.project / 'state/edit.json', {'clips': bad})
        assert self.cli('status')['status'] == 'blocked'
        save(self.project / 'state/edit.json', {'clips': clips})
        self.approve('production')
        self.cli('render', '--mode', 'final')
        self.stage_review('delivery', 'output/final_mv.mp4', 'state/final_review.json')
        final_state = self.approve('delivery')
        assert final_state['status'] == 'simulation_completed', final_state
        self.cli('report')
        report = load(self.project / 'output/delivery_report.json')
        assert report['production_acceptance'] is False and report['simulation'] is True
        final = self.project / 'output/final_mv.mp4'
        probe = json.loads(self.command(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', final], label='独立探测成片'))
        video = next(s for s in probe['streams'] if s['codec_type'] == 'video')
        assert video['width'] == 640 and video['height'] == 360 and int(video['nb_frames']) == 192, video
        self.command(['ffmpeg', '-v', 'error', '-nostdin', '-i', final, '-f', 'null', '-'], label='全片完整解码')
        audio_hashes = []
        for source in (self.project / cfg['audio'], final):
            audio_hashes.append(self.command(['ffmpeg', '-v', 'error', '-nostdin', '-i', source, '-map', '0:a:0',
                '-c:a', 'pcm_s16le', '-f', 'hash', '-hash', 'sha256', '-'], label='解码音频哈希').strip())
        assert audio_hashes[0] == audio_hashes[1], audio_hashes
        # 清空一张预演图，旧预演与后续批准须失效；原字节恢复后可恢复状态。
        image = self.project / shots[2]['image']
        original = image.read_bytes()
        image.write_bytes(b'changed')
        assert self.cli('status')['stage'] == 'storyboard'
        image.write_bytes(original)
        assert self.cli('status')['status'] == 'simulation_completed'
        result = {'status': 'passed', 'simulation': True, 'backend_calls': 0, 'archive': str(self.archive),
                  'archive_sha256': digest(self.archive), 'project': str(self.project), 'media': str(final),
                  'media_sha256': digest(final), 'seconds': float(probe['format']['duration']), 'video_frames': 192,
                  'audio_decoded_matches_master': True, 'cli_and_tool_commands': len(self.steps),
                  'checks': ['独立目录解压安装', '非安装目录调用CLI', '依赖实测', '六阶段模拟', '相对观察路径',
                             '非整帧音乐边界', '横竖/异帧率素材', '抽帧不能替代动态审查', '选段越界提前阻断',
                             '旧预演失效', '成片解码', '母带逐样本哈希一致', '模拟不冒充正式验收']}
        save(self.output / 'simulation-result.json', result)
        return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True, help='新的、尚不存在的演练目录')
    args = parser.parse_args()
    try:
        print(json.dumps(Simulation(args.archive, args.output).run(), ensure_ascii=False, indent=2))
    except (OSError, ValueError, AssertionError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({'status': 'failed', 'issue': str(exc)}, ensure_ascii=False))
        sys.exit(1)
