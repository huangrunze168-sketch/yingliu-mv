"""离线核对登记作业、实付声明与台账；估价/预占不等于实付，不查询后端。"""
import json
from pathlib import Path
from decimal import Decimal

try:
    from .submission_jobs import job_path, pointer
    from .pipeline_contracts import digest
    from .ledger import money
except ImportError:
    from submission_jobs import job_path, pointer
    from pipeline_contracts import digest
    from ledger import money


def audit(project):
    project = Path(project).resolve()
    ledger_path = project / 'state/ledger.jsonl'
    versions = {}
    rows = []
    if ledger_path.is_file():
        versions[ledger_path] = digest(ledger_path)
        rows = [json.loads(line) for line in ledger_path.read_text(encoding='utf8').splitlines() if line.strip()]
    events, ids = {}, set()
    for row in rows:
        if not row.get('event_id') or row['event_id'] in ids: raise ValueError('费用台账事件ID缺失或重复')
        ids.add(row['event_id'])
        events.setdefault(row['task_id'], []).append(row)
    records = []
    total_cash, total_coins = Decimal(0), Decimal(0)
    caps = {money(r['cash_limit_cny']) for r in rows}
    if len(caps) > 1 or any(cap < 0 for cap in caps): raise ValueError('费用台账全项目现金封顶不一致')
    def released(history):
        return (len(history) == 2 and [r['action'] for r in history] == ['reserve', 'release']
                and all(r.get('provider_task_id') is None for r in history)
                and money(history[0]['cash_cny']) >= 0 and money(history[0]['coins']) == 0
                and money(history[1]['cash_cny']) == 0 and money(history[1]['coins']) == 0)
    paths = sorted((project / 'state/submission_jobs').glob('*.json'))
    for path in paths:
        versions[path] = digest(path)
        job = json.loads(path.read_text(encoding='utf8'))
        if job.get('project_id') != str(project) or job_path(project, job.get('job_id')) != path.resolve():
            raise ValueError('费用作业项目/ID/路径不一致')
        tid = job['job_id']
        history = events.pop(tid, [])
        if not history or history[0].get('action') != 'reserve': raise ValueError('登记作业缺少原预算预占事实')
        if job.get('status') == 'cancelled_before_dispatch':
            if job.get('cost_status') != 'released_unspent' or not released(history):
                raise ValueError('未派发撤销仍缺少完整预占释放事实')
            records.append({'job_id': tid, 'status': 'unspent_preparation_released',
                            'scope': 'local_reservation_release_not_provider_refund'})
            continue
        if job.get('cost_status') != 'settled' or not job.get('provider_task_id'):
            raise ValueError(f'{tid} 费用仍未核对，不能将预估、预占或完成状态当实付')
        actual = job['actual_cost']
        raw = actual['raw_response']
        cash, coins = money(actual['cash_cny']), money(actual['coins'])
        if (cash < 0 or coins < 0 or pointer(raw, actual['task_pointer']) != job['provider_task_id']
                or money(pointer(raw, actual['cash_pointer'])) != cash
                or money(pointer(raw, actual['coins_pointer'])) != coins):
            raise ValueError('实付字段与记录的原费用响应不匹配')
        settlements = [r for r in history if r['action'] == 'settle']
        if (len(settlements) != 1 or money(settlements[0]['cash_cny']) != cash
                or money(settlements[0]['coins']) != coins
                or settlements[0].get('provider_task_id') != job['provider_task_id']):
            raise ValueError('作业实付与原任务的实际settle事件不匹配')
        refunds_cash, refunds_coins = Decimal(0), Decimal(0)
        for row in history:
            if row['action'] in {'reserve', 'settle'}: continue
            if (row['action'] != 'fail_refund' or row.get('refund_of') != tid
                    or money(row['cash_cny']) > 0 or money(row['coins']) > 0):
                raise ValueError('费用链包含未关联退款或未知账务变更')
            evidence = row.get('refund_evidence') or {}
            refund_raw = evidence.get('raw_response')
            if (not isinstance(refund_raw, dict)
                    or pointer(refund_raw, evidence['task_pointer']) != job['provider_task_id']
                    or money(pointer(refund_raw, evidence['cash_pointer'])) != money(row['cash_cny'])
                    or money(pointer(refund_raw, evidence['coins_pointer'])) != money(row['coins'])):
                raise ValueError('退款缺少对应任务的原始费用证据，不用负数声明自动冲减实付')
            refunds_cash += money(row['cash_cny']); refunds_coins += money(row['coins'])
        if cash+refunds_cash < 0 or coins+refunds_coins < 0: raise ValueError('关联退款超过记录实付')
        if len({money(r['cash_limit_cny']) for r in history}) != 1: raise ValueError('作业费用封顶记录不一致')
        total_cash += cash+refunds_cash; total_coins += coins+refunds_coins
        records.append({'job_id': tid, 'provider_task_id': job['provider_task_id'],
                        'provider_status': job['status'], 'recorded_gross_cash_cny': str(cash),
                        'recorded_gross_coins': str(coins), 'recorded_refund_cash_cny': str(refunds_cash),
                        'recorded_refund_coins': str(refunds_coins)})
    for tid, history in events.items():
        if not released(history): raise ValueError('台账存在未关联作业的历史事件，不能认证成本已完整核对')
        records.append({'job_id': tid, 'status': 'unpublished_preparation_released',
                        'scope': 'local_preparation_rollback_not_provider_refund'})
    if caps and total_cash > next(iter(caps)): raise ValueError('记录的项目净实付超过锁定现金封顶')
    if any(digest(p) != v for p, v in versions.items()): raise ValueError('费用核对期间记录已改变')
    return {'schema_version': 1, 'project_id': str(project),
            'status': 'registered_jobs_reconciled' if records else 'no_registered_provider_jobs',
            'scope': 'recorded_response_and_ledger_consistency_not_provider_signed_billing',
            'historical_unregistered_costs_verified': False, 'artistic_approval': False,
            'jobs': records, 'recorded_net_cash_cny': str(total_cash), 'recorded_net_coins': str(total_coins)}, versions
