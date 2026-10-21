"""持久章节队列；单次tick并发调度，指数回查退避，未知不重新提交。"""
import argparse
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
try:
    from .pipeline_contracts import digest, atomic_batch
    from .execution_policy import require_video_backend
    from . import submission_jobs as jobs
except ImportError:
    from pipeline_contracts import digest, atomic_batch
    from execution_policy import require_video_backend
    import submission_jobs as jobs


def load(path):
    return json.loads(Path(path).read_text(encoding='utf8'))


def init(project, job_ids, *, concurrency=1, poll_seconds=15, contracts=()):
    project = Path(project).resolve()
    if type(concurrency) is not int or not 1 <= concurrency <= 8 or type(poll_seconds) is not int or poll_seconds < 1:
        raise ValueError('并发须为1至8的整数，回查间隔须为正整数秒')
    if (not job_ids and not contracts) or len(set(job_ids)) != len(job_ids): raise ValueError('队列为空或作业重复')
    records = [load(jobs.job_path(project, name)) for name in job_ids]
    if any(r.get('project_id') != str(project) for r in records): raise ValueError('队列包含其他项目的作业')
    pending = []
    for name in contracts:
        contract_path = (project/name).resolve()
        contract_path.relative_to(project)
        contract = load(contract_path)
        pending.append({'chapter_id':contract['chapter_id'],'contract_path':str(contract_path),
                        'contract_sha256':digest(contract_path),'status':'queued','next_poll':0,'attempts':0})
    chapters = [r['chapter_id'] for r in records] + [r['chapter_id'] for r in pending]
    if len(set(chapters)) != len(chapters): raise ValueError('同一章节只能有一个当前作业')
    path = project/'state/batch_queue.json'
    queue = {'schema_version': 1, 'project_id': str(project), 'concurrency': concurrency,
             'poll_seconds': poll_seconds, 'status': 'queued',
             'jobs': [{'job_id': r['job_id'], 'chapter_id': r['chapter_id'], 'next_poll': 0, 'attempts': 0} for r in records] + pending}
    atomic_batch({path: json.dumps(queue, ensure_ascii=False, indent=2)}, expected={path: None})
    return queue


def tick(project, *, dispatch=None, poll=None, now=None):
    project = Path(project).resolve()
    require_video_backend(project)
    now = time.time() if now is None else now
    path = project/'state/batch_queue.json'
    lock = project/'.batch-scheduler.lock'
    fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        os.write(fd, str(os.getpid()).encode())
        version = digest(path)
        queue = load(path)
        if queue['project_id'] != str(project): raise ValueError('队列项目不符')
        limit, delay = queue['concurrency'], queue['poll_seconds']
        if type(limit) is not int or not 1 <= limit <= 8 or type(delay) is not int or delay < 1:
            raise ValueError('队列调度参数无效')
        if dispatch is None or poll is None:
            try:
                from .mcp_broker import run_dispatch, run_poll
            except ImportError:
                from mcp_broker import run_dispatch, run_poll
            dispatch, poll = dispatch or run_dispatch, poll or run_poll
        # 仅为可用并发槽准备下一批，避免一次性预占所有章节而撞供应商上限。
        active = sum(load(jobs.job_path(project,r['job_id']))['status'] not in {'completed','failed','cancelled_before_dispatch'}
                     for r in queue['jobs'] if r.get('job_id'))
        for row in queue['jobs']:
            if row.get('job_id'): continue
            try:
                # 预占成功、队列发布前崩溃时，从不可变契约恢复原作业，不创建替代任务。
                recovered = [j for j in (load(p) for p in (project/'state/submission_jobs').glob('*.json'))
                             if j.get('contract_sha256') == row['contract_sha256'] and j.get('chapter_id') == row['chapter_id']
                             and j.get('project_id') == str(project)]
                if len(recovered)>1: raise ValueError('同章契约对应多个作业，须人工核查原调用')
                if recovered:
                    row.update(job_id=recovered[0]['job_id'],status=recovered[0]['status'])
                    active += recovered[0]['status'] not in {'completed','failed','cancelled_before_dispatch'}
                    continue
                if active >= limit: continue
                contract = load(row['contract_path'])
                if digest(row['contract_path']) != row['contract_sha256']: raise ValueError('排队期间提交契约已变化')
                account_limit = contract['backend_capabilities']['concurrency_limit']
                account_active = sum(j.get('backend_account_id') == contract['backend_account_id']
                                     and j.get('status') in {'prepared','dispatching','pending','polling','unknown'}
                                     for j in (load(p) for p in (project/'state/submission_jobs').glob('*.json')))
                if account_active >= account_limit: continue
                job = jobs.prepare(project, row['contract_path'])
                row.update(job_id=job['job_id'], status=job['status'])
                row.pop('error',None)
                active += 1
                # 预占后立即保存队列绑定；崩溃恢复不会创建第二个同章作业。
                atomic_batch({path: json.dumps(queue, ensure_ascii=False, indent=2)}, expected={path:version})
                version = digest(path)
            except Exception as exc:
                row['error'] = str(exc)
        def work(row):
            if not row.get('job_id'): return row
            try:
                job = load(jobs.job_path(project, row['job_id']))
                if job.get('project_id') != str(project) or job.get('chapter_id') != row['chapter_id']:
                    raise ValueError('作业与队列章节不符')
                status = job['status']
                invoked = False
                if status == 'prepared':
                    require_video_backend(project)
                    job = dispatch(project, row['job_id'])
                    invoked = True
                elif status in {'pending', 'unknown'} and job.get('provider_task_id') and now >= row['next_poll']:
                    require_video_backend(project)
                    job = poll(project, row['job_id'])
                    row['attempts'] += 1
                    invoked = True
                row['status'] = job['status']
                if job['status'] in {'unknown', 'dispatching', 'polling'} and not job.get('provider_task_id'):
                    row['requires_action'] = '核查原调用回执并recover，禁止重新派发'
                elif job['status'] in {'dispatching', 'polling'}:
                    row['requires_action'] = '中断调用须先recover，不自动重入'
                else:
                    row.pop('requires_action', None)
                if invoked:
                    row['next_poll'] = now + delay * 2**min(row['attempts'], 6)
                row.pop('error', None)
            except Exception as exc:
                # 作业文件是真实依据；异常不重派，下一tick先重新读取作业。
                row['error'] = str(exc)
            return row
        with ThreadPoolExecutor(max_workers=limit) as pool:
            queue['jobs'] = list(pool.map(work, queue['jobs']))
        queue['status'] = ('completed' if all(r.get('status') == 'completed' and not r.get('error') for r in queue['jobs'])
                           else 'needs_attention' if any(r.get('error') or r.get('requires_action') or r.get('status') in {'failed','cancelled_before_dispatch'} for r in queue['jobs'])
                           else 'pending')
        atomic_batch({path: json.dumps(queue, ensure_ascii=False, indent=2)}, expected={path: version})
        return queue
    finally:
        os.close(fd)
        lock.unlink(missing_ok=True)


def require_completed(project):
    project = Path(project).resolve()
    queue = load(project/'state/batch_queue.json')
    if queue.get('project_id') != str(project) or queue.get('status') != 'completed' or not queue.get('jobs'):
        raise ValueError('批量队列尚未完成')
    chapters = [row['chapter_id'] for row in queue['jobs']]
    if len(set(chapters)) != len(chapters): raise ValueError('完成队列存在重复章节')
    plan_path = project/'state/chapter_plan.json'
    if plan_path.is_file() and set(chapters) != {r['id'] for r in load(plan_path)['chapters']}:
        raise ValueError('完成队列没有覆盖当前全部章节（包括已有代表样片作业）')
    for row in queue['jobs']:
        if not row.get('job_id'): raise ValueError('队列仍有未准备章节')
        job = load(jobs.job_path(project, row['job_id']))
        if job.get('project_id') != str(project) or job.get('chapter_id') != row['chapter_id'] or job['status'] != 'completed':
            raise ValueError('批量队列与真实作业状态不符')
    return queue


def run(project, *, timeout_seconds=300, dispatch=None, poll=None):
    """可持续运行的队列入口；超时保留pending供下一次resume，不取消或重派任务。"""
    if type(timeout_seconds) is not int or timeout_seconds < 1:
        raise ValueError('运行时限须为正整数秒')
    deadline = time.monotonic()+timeout_seconds
    while True:
        queue = tick(project, dispatch=dispatch, poll=poll)
        if queue['status'] in {'completed','needs_attention'} or time.monotonic() >= deadline:
            return queue
        due = min((r.get('next_poll',time.time()) for r in queue['jobs'] if r.get('status') in {'pending','unknown'}),default=time.time()+1)
        time.sleep(max(.05,min(15,due-time.time(),max(.05,deadline-time.monotonic()))))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('init', 'tick', 'run', 'resume', 'check'))
    parser.add_argument('project', type=Path)
    parser.add_argument('--job-id', action='append', default=[])
    parser.add_argument('--concurrency', type=int, default=1)
    parser.add_argument('--contract', action='append', default=[])
    parser.add_argument('--timeout-seconds',type=int,default=300)
    args = parser.parse_args()
    result = (init(args.project, args.job_id, concurrency=args.concurrency, contracts=args.contract) if args.action == 'init'
              else tick(args.project) if args.action == 'tick'
              else run(args.project,timeout_seconds=args.timeout_seconds) if args.action in {'run','resume'}
              else require_completed(args.project))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result['status']=='completed' or args.action=='init' else 1)
