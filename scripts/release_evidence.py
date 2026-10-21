"""实际回归、Git版本与复盘绑定；仅核验发布证据，不自动推送。"""
import argparse
import json
import subprocess
import sys
from pathlib import Path
from datetime import datetime, timezone
try:
    from .pipeline_contracts import digest, atomic_batch
except ImportError:
    from pipeline_contracts import digest, atomic_batch

ROOT = Path(__file__).resolve().parent.parent


def tree_versions():
    return {str(p.relative_to(ROOT)): digest(p) for folder in ('scripts', 'tests')
            for p in sorted((ROOT/folder).glob('*')) if p.suffix in ('.py', '.js')}


def git(*args):
    return subprocess.run(['git', '-C', str(ROOT), *args], capture_output=True, text=True, check=True).stdout.strip()


def capture(project):
    project = Path(project).resolve()
    if git('status', '--porcelain', '--', 'scripts', 'tests'):
        raise ValueError('发布回归要求scripts/tests已提交；工作区验证请直接运行unittest')
    before = tree_versions()
    result = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'],
                            cwd=ROOT, capture_output=True, text=True, encoding='utf8', errors='replace')
    report = {'schema_version': 1, 'project_id': str(project), 'returncode': result.returncode,
              'command': [sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-v'],
              'stdout': result.stdout, 'stderr': result.stderr, 'versions': before,
              'status': 'passed' if result.returncode == 0 and before == tree_versions() else 'failed',
              'finished_at': datetime.now(timezone.utc).isoformat(), 'commit': git('rev-parse', 'HEAD')}
    path = project/'state/regression_execution.json'
    atomic_batch({path: json.dumps(report, ensure_ascii=False, indent=2)},
                 expected={path: digest(path) if path.is_file() else None,
                           **{ROOT/p: v for p, v in before.items()}})
    return report


def require(project):
    project = Path(project).resolve()
    path = project/'state/retrospective.json'
    version = digest(path)
    review = json.loads(path.read_text(encoding='utf8'))
    regression_path = project/'state/regression_execution.json'
    regression_version = digest(regression_path)
    regression = json.loads(regression_path.read_text(encoding='utf8'))
    if (review.get('project_id') != str(project) or regression.get('project_id') != str(project)
            or regression.get('status') != 'passed' or type(regression.get('returncode')) is not int or regression['returncode'] != 0
            or regression.get('versions') != tree_versions()
            or review.get('regression_sha256') != regression_version):
        raise ValueError('复盘缺少当前源码实际回归证据')
    for field in ('findings', 'changes', 'remaining_limits'):
        if not isinstance(review.get(field), list) or not review[field] or any(not isinstance(v, str) or not v.strip() for v in review[field]):
            raise ValueError('复盘须填写具体条目: '+field)
    commit = git('rev-parse', 'HEAD')
    if git('status', '--porcelain', '--', 'scripts', 'tests'):
        raise ValueError('当前源码/测试尚未提交，不能声明该Git版本已完成发布验收')
    if review.get('commit') != commit or regression.get('commit') != commit:
        raise ValueError('复盘/回归不是当前Git版本')
    # 本地交付允许不推送；远端发布必须提供实际远端ref供只读核验。
    publication = review['publication']
    if publication['kind'] == 'local':
        if publication.get('commit') != commit: raise ValueError('本地版本不一致')
    elif publication['kind'] == 'remote':
        ref = publication['ref']
        if not isinstance(ref, str) or not ref.startswith(('refs/heads/', 'refs/tags/')):
            raise ValueError('远端发布须使用完整ref')
        if publication['remote'] not in git('remote').splitlines():
            raise ValueError('发布目标须为仓库已配置的远端名称')
        rows = git('ls-remote', '--', publication['remote'], ref).splitlines()
        if not any(row.split()[0] == commit for row in rows): raise ValueError('远端版本未发布到目标ref')
    else:
        raise ValueError('发布方式须为local或remote')
    if digest(path) != version or digest(regression_path) != regression_version:
        raise ValueError('复盘核验期间依据变化')
    return {path: version, regression_path: regression_version}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('test', 'check'))
    parser.add_argument('project', type=Path)
    args = parser.parse_args()
    try:
        result = capture(args.project) if args.action == 'test' else {'status': 'checked', 'versions': {str(p):v for p,v in require(args.project).items()}}
        print(json.dumps(result, ensure_ascii=False))
        raise SystemExit(1 if result.get('status') == 'failed' else 0)
    except Exception as exc:
        print(json.dumps({'status': 'blocked', 'error': str(exc)}, ensure_ascii=False))
        raise SystemExit(1)
