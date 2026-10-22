"""只读依赖检查；可选合成媒体自检，不安装软件或调用外部服务。"""
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def execute(args):
    result = subprocess.run(args, capture_output=True, text=True, encoding='utf8', errors='replace', timeout=30)
    if result.returncode:
        raise ValueError((result.stderr or result.stdout)[-2000:])
    return result.stdout


def names(output):
    result = set()
    for line in output.splitlines():
        parts = line.split()
        if len(parts) >= 2 and re.fullmatch(r'[A-Z.]{1,6}', parts[0]):
            result.update(parts[1].split(','))
    return result


def doctor(project=None, smoke=False):
    manifest = json.loads((ROOT / 'dependencies.json').read_text(encoding='utf8'))
    errors = []
    package_missing = [p for p in manifest['bundled_files'] if not (ROOT / p).is_file()]
    if package_missing:
        errors.append('分享包缺少文件：' + ', '.join(package_missing))
    python_ok = sys.version_info >= (3, 10)
    if not python_ok:
        errors.append('Python 版本低于 3.10')
    binaries = {}
    capabilities = {}
    for name in ('ffmpeg', 'ffprobe'):
        path = shutil.which(name)
        row = {'path': path, 'available': False}
        if path:
            try:
                row.update(available=True, version=execute([path, '-version']).splitlines()[0])
            except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
                row.update(available=False, issue=str(exc))
        if not row['available']:
            errors.append(name + ' 缺失或无法启动；安装后确认其 bin 目录位于 PATH')
        binaries[name] = row
    if binaries['ffmpeg']['available']:
        for category, required in manifest['ffmpeg_capabilities'].items():
            try:
                available = names(execute([binaries['ffmpeg']['path'], '-hide_banner', '-' + category]))
                missing = [x for x in required if x not in available]
                capabilities[category] = {'required': required, 'missing': missing}
                if missing:
                    errors.append('FFmpeg 缺少 ' + category + '：' + ', '.join(missing))
            except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
                errors.append('无法检查 FFmpeg ' + category + '：' + str(exc))
    smoke_result = {'status': 'not_run', 'hint': '使用 doctor --smoke 执行实际合成、编码、抽帧和探测'}
    if smoke and not errors:
        try:
            smoke_result = smoke_media(binaries)
        except (OSError, ValueError, KeyError, subprocess.TimeoutExpired) as exc:
            smoke_result = {'status': 'failed', 'issue': str(exc)}
            errors.append('合成媒体自检失败：' + str(exc))
    elif smoke:
        smoke_result = {'status': 'skipped', 'issue': '先修复上面列出的本地依赖'}
    external = {key: {'status': 'unverified', 'required_for': value}
                for key, value in manifest['external_capabilities'].items()}
    project_result = None
    if project is not None:
        try:
            cfg = json.loads((Path(project) / 'project.json').read_text(encoding='utf-8-sig'))
            project_result = {'agent_capabilities_declared': cfg.get('agent_capabilities', {}),
                              'backend_declared': cfg.get('backend'),
                              'note': '配置仅为声明，CLI 无法探测宿主 Agent 的真实工具或供应商凭证'}
        except (OSError, ValueError) as exc:
            project_result = {'issue': str(exc)}
            errors.append('项目配置不可读：' + str(exc))
    return {'local_ready': not errors, 'python': {'version': sys.version.split()[0], 'compatible': python_ok},
            'python_third_party_runtime': [], 'package_missing': package_missing, 'binaries': binaries,
            'capabilities': capabilities, 'smoke': smoke_result, 'external': external,
            'project': project_result, 'issues': errors,
            'note': 'local_ready 仅代表本地工具检查通过；外部能力须由 Agent/用户实际验证，不使用 Qwen'}


def smoke_media(binaries):
    # 清理对象固定在本工作流目录内；不碰原工作流或项目媒体。
    with tempfile.TemporaryDirectory(prefix='.dependency-check-', dir=ROOT) as temp:
        folder = Path(temp).resolve()
        folder.relative_to(ROOT)
        ffmpeg, ffprobe = binaries['ffmpeg']['path'], binaries['ffprobe']['path']
        clip = folder / 'test.mp4'
        execute([ffmpeg, '-v', 'error', '-nostdin', '-y', '-f', 'lavfi', '-i', 'color=c=blue:s=160x90:r=24:d=0.5',
                 '-f', 'lavfi', '-i', 'sine=frequency=440:duration=0.5', '-t', '0.5',
                 '-vf', 'scale=160:90,pad=160:90:0:0,setsar=1', '-c:v', 'libx264',
                 '-pix_fmt', 'yuv420p', '-c:a', 'alac', str(clip)])
        info = json.loads(execute([ffprobe, '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(clip)]))
        if not {'video', 'audio'} <= {s['codec_type'] for s in info['streams']}:
            raise ValueError('合成文件缺少视频或音频轨')
        for suffix in ('jpg', 'png'):
            image = folder / ('frame.' + suffix)
            execute([ffmpeg, '-v', 'error', '-nostdin', '-y', '-i', str(clip), '-frames:v', '1', str(image)])
            if not image.is_file() or image.stat().st_size == 0:
                raise ValueError('抽帧失败：' + suffix)
        listing = folder / 'concat.txt'
        listing.write_text("file 'test.mp4'\n", encoding='utf8')
        joined = folder / 'joined.mp4'
        execute([ffmpeg, '-v', 'error', '-nostdin', '-y', '-f', 'concat', '-safe', '1',
                 '-i', str(listing), '-c', 'copy', str(joined)])
        return {'status': 'passed', 'checks': ['libx264', 'ALAC', 'MP4', 'scale/pad/setsar', 'JPEG/PNG', 'concat', 'FFprobe'],
                'external_calls': False}
