"""运行固定门禁并保存实际进程回执；记录执行，不认证人工观察或阻止文件所有者伪造。"""
import argparse
from datetime import datetime, timezone
import json
import hashlib
import os
import re
from pathlib import Path
import subprocess
import sys
import uuid

try:
    from .pipeline_contracts import atomic_batch, digest, require_no_transaction
except ImportError:
    from pipeline_contracts import atomic_batch, digest, require_no_transaction

COMMANDS = {
    'gate1': ('gate_checks.py', ('gate1',), 'gate1', None),
    'gate5': ('gate_checks.py', ('gate5',), 'gate5', None),
    'gate6': ('gate_checks.py', ('gate6',), 'gate6', None),
    'crew_pipeline': ('crew_pipeline.py', (), 'crew_pipeline', 'structural_pass'),
    'crew_rehearsal': ('crew_rehearsal.py', (), 'crew_rehearsal', 'design_ready_for_rehearsal'),
}
STEPS = tuple(COMMANDS)
RUN_ORDER = ('gate1', 'gate5', 'crew_pipeline', 'crew_rehearsal', 'gate6')
SCRIPTS = Path(__file__).resolve().parent


def snapshot(paths):
    return {str(Path(p).resolve()): digest(p) if Path(p).is_file() else None for p in paths}


def inputs(project, step):
    paths = [project/'state/timeline.json']
    if step == 'gate1':
        paths.append(project/'input/lyrics.txt')
        timeline = json.loads(paths[0].read_text(encoding='utf8'))
        for name in ('source_audio', 'master_audio'):
            if timeline.get(name):
                paths.append(project/timeline[name])
        try:
            from .timing_evidence import source_paths, alignment_versions
        except ImportError:
            from timing_evidence import source_paths, alignment_versions
        evidence_paths = source_paths(project, timeline)
        paths += evidence_paths
        paths += list(alignment_versions(project, timeline))
        if evidence_paths and evidence_paths[0].is_file():
            record = json.loads(evidence_paths[0].read_text(encoding='utf8'))
            if record.get('raw_path'):
                paths.append(Path(record['raw_path']))
            paths += [Path(p) for p in record.get('inputs', {})]
        try:
            from .gate_checks import resolve_source_audio
        except ImportError:
            from gate_checks import resolve_source_audio
        audio = resolve_source_audio(project, timeline)
        if audio is not None:
            paths.append(audio)
    elif step == 'gate6':
        paths.append(project/'state/storyboard.json')
    else:
        paths += [project/'state'/f'{name}.json' for name in (
            'storyboard', 'development', 'style', 'story_plan', 'visual_edit_plan',
            'music_structure', 'sequence_direction')]
        paths += list((project/'prompts').glob('SH*.json'))
        paths += list((project/'prompts/motion').glob('SH*.json'))
        sequence = json.loads((project/'state/sequence_direction.json').read_text(encoding='utf8'))
        paths += [project/v['path'] for v in sequence['sources'].values()]
    return snapshot(paths)


def code_versions():
    return snapshot(SCRIPTS.glob('*.py'))


def now():
    return datetime.now(timezone.utc).isoformat()


def execute(project, step):
    if step not in STEPS:
        raise ValueError('只允许运行登记的门禁，不接受任意命令')
    project = Path(project).resolve()
    if not project.is_dir():
        raise ValueError('项目目录不存在')
    require_no_transaction(project)
    lock = project/'.workflow-execution.lock'
    fd = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    run_id = uuid.uuid4().hex
    path = project/'state/executions'/f'{step}.json'
    record = {'schema_version': 1, 'project_id': str(project), 'step': step,
              'run_id': run_id, 'pid': os.getpid(), 'status': 'running',
              'process_started': False, 'process_completed': False,
              'started_at': now(), 'artistic_approval': False,
              'command': [sys.executable, str(SCRIPTS/COMMANDS[step][0]), *COMMANDS[step][1], str(project)]}
    try:
        os.write(fd, json.dumps({'pid': os.getpid(), 'run_id': run_id}).encode())
        previous = path.read_bytes() if path.is_file() else None
        version = digest(path) if previous is not None else None
        updates = {path: json.dumps(record, ensure_ascii=False, indent=2)}
        if previous is not None:
            updates[path.parent/'history'/f'{step}-{run_id}.json'] = previous
        atomic_batch(updates, expected={path: version})
        version = digest(path)
        try:
            before, code = inputs(project, step), code_versions()
            result = subprocess.run(record['command'], capture_output=True, text=True,
                                    encoding='utf8', errors='replace',
                                    env={**os.environ, 'PYTHONIOENCODING': 'utf8'})
            record.update(process_started=True, process_completed=True)
            # 先保存实际进程结果，后续输入/报告解析失败也不能丢掉退出码和输出。
            log_path = path.parent/'logs'/f'{run_id}.json'
            log = json.dumps({'stdout': result.stdout, 'stderr': result.stderr}, ensure_ascii=False, indent=2)
            record.update(returncode=result.returncode, log_path=str(log_path),
                          log_sha256='sha256:'+hashlib.sha256(log.encode('utf8')).hexdigest())
            atomic_batch({log_path: log, path: json.dumps(record, ensure_ascii=False, indent=2)},
                         expected={path: version})
            version = digest(path)
            # 关5会登记分镜提示词指纹，属于本命令声明的输出；其他输入不允许改变。
            after = inputs(project, step)
            allowed = {str(project/'state/storyboard.json')} if step == 'gate5' else set()
            changed = [p for p in set(before)|set(after) if before.get(p) != after.get(p) and p not in allowed]
            report_path = project/'gates'/f'{COMMANDS[step][2]}.json'
            report = json.loads(report_path.read_text(encoding='utf8')) if report_path.is_file() else {}
            failures = []
            if result.returncode != 0:
                failures.append('门禁进程非零退出')
            passed = (report.get('status') == COMMANDS[step][3] and not report.get('errors')
                      and not report.get('structural_errors')) if COMMANDS[step][3] else report.get('summary', {}).get('pass') is True
            if not passed:
                failures.append('实际门禁报告未通过')
            if step == 'gate1' and report.get('production_allowed') is not True:
                failures.append('关1仅诊断或临时时间，不能生产')
            if changed or code_versions() != code:
                failures.append('门禁执行期间输入或代码发生变化')
            record.update(status='failed' if failures else 'succeeded', returncode=result.returncode,
                          errors=failures, finished_at=now(), inputs_before=before,
                          validated_inputs=after, code_versions=code,
                          report_path=str(report_path), report_sha256=digest(report_path) if report_path.is_file() else None,
                          log_path=str(log_path), log_sha256='sha256:'+hashlib.sha256(log.encode('utf8')).hexdigest())
            atomic_batch({log_path: log, path: json.dumps(record, ensure_ascii=False, indent=2)},
                         expected={path: version, **after, **code})
        except Exception as exc:
            record.update(status='failed', finished_at=now(), errors=[f'{type(exc).__name__}: {exc}'])
            atomic_batch({path: json.dumps(record, ensure_ascii=False, indent=2)}, expected={path: version})
        return record
    finally:
        os.close(fd)
        lock.unlink(missing_ok=True)


def receipt_errors(project, steps=STEPS):
    project = Path(project).resolve()
    errors = []
    if (project/'.workflow-execution.lock').exists():
        return ['存在执行中或中断的工作流任务，不能沿用旧通过']
    for step in steps:
        if step not in STEPS:
            errors.append('未知执行步骤: ' + str(step))
            continue
        try:
            path = project/'state/executions'/f'{step}.json'
            version = digest(path)
            record = json.loads(path.read_text(encoding='utf8'))
            if not isinstance(record.get('run_id'), str) or not re.fullmatch('[0-9a-f]{32}', record['run_id']):
                raise ValueError('执行任务ID无效')
            if (record.get('project_id') != str(project) or record.get('step') != step
                    or record.get('schema_version') != 1 or record.get('status') != 'succeeded'
                    or record.get('process_started') is not True or record.get('process_completed') is not True
                    or type(record.get('returncode')) is not int or record.get('returncode') != 0
                    or record.get('errors')):
                errors.append(step + ': 缺少成功的实际执行回执')
            if record.get('validated_inputs') != inputs(project, step) or record.get('code_versions') != code_versions():
                errors.append(step + ': 执行依据已变化，须重新运行')
            report = project/'gates'/f'{COMMANDS[step][2]}.json'
            if record.get('report_path') != str(report) or record.get('report_sha256') != digest(report):
                errors.append(step + ': 实际报告被替换')
            log_path = Path(record.get('log_path', ''))
            expected_log = project/'state/executions/logs'/f"{record.get('run_id')}.json"
            if log_path != expected_log or not log_path.is_file():
                errors.append(step + ': 缺少实际进程输出日志')
            elif digest(log_path) != record.get('log_sha256'):
                errors.append(step + ': 实际进程日志已变化')
            if digest(path) != version:
                errors.append(step + ': 检查期间回执已变化')
        except (OSError, ValueError, KeyError, TypeError) as exc:
            errors.append(step + ': 执行回执无法核验 ' + str(exc))
    return errors


def run_required(project):
    """按生产依赖执行全部五项；首个失败即停止，不预填后续成功。"""
    records = []
    for step in RUN_ORDER:
        record = execute(project, step)
        records.append(record)
        if record.get('status') != 'succeeded':
            return {'status': 'failed', 'failed_step': step, 'records': records,
                    'not_run': list(RUN_ORDER[len(records):]), 'all_steps_completed': False}
    errors = receipt_errors(project)
    return {'status': 'failed' if errors else 'machine_checks_passed',
            'records': records, 'not_run': [], 'errors': errors,
            'all_steps_completed': False,
            'scope': '仅五项实际门禁，不认证十二步历史、听看或艺术批准'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('run', 'run-required', 'check'))
    parser.add_argument('project_dir', type=Path)
    parser.add_argument('--step', choices=STEPS)
    args = parser.parse_args()
    if args.action == 'run-required':
        if args.step:
            parser.error('run-required不允许--step，必须按依赖执行全部五项')
        result = run_required(args.project_dir)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result['status'] == 'machine_checks_passed' else 1
    if args.action == 'run':
        if not args.step:
            parser.error('run需要--step')
        result = execute(args.project_dir, args.step)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result['status'] == 'succeeded' else 1
    errors = receipt_errors(args.project_dir, (args.step,) if args.step else STEPS)
    print(json.dumps({'pass': not errors, 'errors': errors}, ensure_ascii=False, indent=2))
    return 1 if errors else 0


if __name__ == '__main__':
    raise SystemExit(main())
