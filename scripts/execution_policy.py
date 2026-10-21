"""本项目外部视频后端开关；默认关闭，不把本地验证或历史回执当新授权。"""
import json
from pathlib import Path

try:
    from .pipeline_contracts import digest
except ImportError:
    from pipeline_contracts import digest


def require_video_backend(project):
    project = Path(project).resolve()
    path = project / 'state/execution_policy.json'
    if not path.is_file(): raise ValueError('未明确允许使用外部视频后端；默认禁止调用')
    version = digest(path)
    policy = json.loads(path.read_text(encoding='utf8'))
    if (not isinstance(policy, dict) or policy.get('project_id') != str(project) or policy.get('video_backend_access') is not True
            or not isinstance(policy.get('instruction'), str) or not policy['instruction'].strip()):
        raise ValueError('用户/项目执行约束禁止使用生成视频后端；仅可本地分析、测试及处理既有素材')
    if digest(path) != version: raise ValueError('后端执行约束在检查期间改变')
    return {path: version}
