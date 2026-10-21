"""十二步独立签名批准锁；只导出待审请求，不持有或生成批准私钥。

安全前提：启动环境、信任目录及代码须在执行 Agent 权限之外保护。
本模块约束正式编排，不是操作系统沙箱，不能阻止同权限者改代码或绕行执行。
"""
import argparse
import hashlib
import json
import os
from pathlib import Path

try:
    from .pipeline_contracts import digest
    from .signed_evidence import canonical, verify
except ImportError:
    from pipeline_contracts import digest
    from signed_evidence import canonical, verify

PURPOSE = 'workflow_step'


def approval_path(project, step):
    if type(step) is not int or not 1 <= step <= 12:
        raise ValueError('批准步骤必须为1至12的整数')
    return Path(project) / 'state/workflow_approvals' / f'step{step:02d}.json'


def value_digest(value):
    return 'sha256:' + hashlib.sha256(canonical(value)).hexdigest()


def context(project, plan_sha, row, previous):
    if (row.get('status') != 'completed' or not row.get('run_id')
            or not isinstance(row.get('inputs'), dict)
            or not isinstance(row.get('outputs'), dict) or not row['outputs']
            or not isinstance(row.get('code'), dict) or not row['code']):
        raise ValueError('只有具有完整执行版本的已完成步骤才能申请批准')
    # 整条实际执行记录进入签名，包含输入、输出、代码、命令日志和本次run_id。
    return {'purpose': PURPOSE, 'project_id': str(Path(project).resolve()),
            'plan_sha256': plan_sha, 'step': row['step'], 'run_id': row['run_id'],
            'previous_approval_sha256': previous,
            'execution_sha256': value_digest(row), 'execution': row}


def trusted_verify(project, envelope):
    path = os.environ.get('MV_REVIEW_TRUST_STORE')
    pinned = os.environ.get('MV_WORKFLOW_TRUST_STORE_SHA256')
    if not path or not pinned:
        raise ValueError('逐步批准须由启动者配置外部信任目录及MV_WORKFLOW_TRUST_STORE_SHA256固定摘要')
    trust = Path(path).resolve()
    if trust == Path(project).resolve() or Path(project).resolve() in trust.parents:
        raise ValueError('工作流批准信任目录不得放在待批准项目内')
    if digest(trust) != pinned:
        raise ValueError('外部信任目录与启动者固定摘要不一致')
    try:
        payload = verify(envelope, PURPOSE)
    except Exception as exc:
        raise ValueError('工作流批准签名无效或验收者无权批准') from exc
    if digest(trust) != pinned:
        raise ValueError('批准核验期间外部信任目录改变')
    return payload


def require(project, plan_sha, row, previous):
    expected = context(project, plan_sha, row, previous)
    path = approval_path(project, row['step'])
    try:
        envelope = json.loads(path.read_text(encoding='utf8'))
    except (OSError, ValueError) as exc:
        raise ValueError(f'第{row["step"]}步等待独立人工签名批准') from exc
    payload = trusted_verify(project, envelope)
    if any(payload.get(k) != v for k, v in expected.items()):
        raise ValueError(f'第{row["step"]}步批准与当前项目、计划、执行版本或前序批准不一致')
    if (payload.get('decision') != 'approve'
            or not isinstance(payload.get('observer'), str) or not payload['observer'].strip()
            or not isinstance(payload.get('evidence'), str) or not payload['evidence'].strip()):
        raise ValueError('须由独立复核者明确approve并填写审查者和具体证据')
    return value_digest(envelope)


def request(project, plan_path, step):
    """只读生成待签内容；上游未批准或当前执行过期时不发后续请求。"""
    project, plan_path = Path(project).resolve(), Path(plan_path).resolve()
    try:
        from . import workflow_run
    except ImportError:
        import workflow_run
    report = workflow_run.inspect(project, plan_path)
    if report['status'] != 'waiting_approval' or report.get('waiting_step') != step:
        raise ValueError('只能为当前有效、前序已批准且等待批准的步骤导出请求')
    state = json.loads((project / 'state/workflow_run.json').read_text(encoding='utf8'))
    previous = None
    for row in state['steps']:
        if row['step'] == step:
            payload = context(project, digest(plan_path), row, previous)
            payload.update(decision='pending', observer='', evidence='')
            return payload
        previous = require(project, digest(plan_path), row, previous)
    raise ValueError('没有当前步骤的实际执行记录')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('request',))
    parser.add_argument('project', type=Path)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--step', type=int, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    value = request(args.project, args.plan, args.step)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open('x', encoding='utf8') as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
    print('已导出pending待审请求；只有外部复核者签名可批准，导出不解锁下一步')
