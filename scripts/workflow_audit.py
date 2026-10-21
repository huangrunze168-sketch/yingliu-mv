"""只读汇总当前生产检查；不补状态、不审批，也不据文件存在认证历史执行顺序。"""
import argparse
import json
from pathlib import Path

try:
    from . import animatic_review, director_review_gate, delivery_state
    from . import gate_checks, sequence_direction, visual_development, visual_assets
    from .chapter_plan_build import require_production_timing, require_director_final_cut
    from .crew_rehearsal import current_report_errors
    from .submission_contract import validate_submission_contract
    from .pipeline_contracts import digest, require_no_transaction, timing_authority
    from .workflow_execution import receipt_errors
    from .timing_evidence import errors as timing_source_errors
except ImportError:
    import animatic_review, director_review_gate, delivery_state
    import gate_checks, sequence_direction, visual_development, visual_assets
    from chapter_plan_build import require_production_timing, require_director_final_cut
    from crew_rehearsal import current_report_errors
    from submission_contract import validate_submission_contract
    from pipeline_contracts import digest, require_no_transaction, timing_authority
    from workflow_execution import receipt_errors
    from timing_evidence import errors as timing_source_errors


PHASES = ('before-generation', 'before-batch', 'before-delivery')
STEPS = (
    ('歌词与时间轴', ('timing', 'execution_receipts'), '权威文本与时间须来自真实分析，不补写verified。'),
    ('音乐分析与导演提案', ('design',), '须核对音乐cue和对标氛围观察范围；机检不证明气质匹配。'),
    ('人物与参考职责', ('design', 'submission'), '真实上传和职责绑定；不能只在提示词写引用名。'),
    ('电影镜头组织与剧组连续性', ('design', 'sequence', 'film_organization', 'crew_reports', 'execution_receipts'), '先用参考辅助信息/空间/动作关系成立，再进行导演风格化及剧组交接。'),
    ('逐镜提示词与剪辑预演', ('prompts', 'animatic', 'execution_receipts'), 'Scene Master完整编译，首次生成前完成带音乐预演审查。'),
    ('音乐窗口与口型安排', ('windows', 'execution_receipts'), '正式口型不得使用临时时间，主唱归属仍需听看。'),
    ('静帧与代表样片', ('assets', 'prototype'), '静帧和动态审片分别验证；接触表不等于动态观看。'),
    ('批量提交', ('backend_policy', 'submission', 'prototype'), '须先遵守用户后端禁用约束；实际任务、并发、预占与结果分别留证据。'),
    ('返修', ('repairs',), '开放返修须绑定失败媒体并闭环重审；无登记返修也须执行当前批准链汇总。'),
    ('素材选段与全片审片', ('media_origins', 'sequence_review'), '按帧选段、任务下载来源、逐章及全片审查绑定真实素材和版本。'),
    ('技术验收与交付', ('delivery',), 'ready不等于用户接收；另核对完整交付包。'),
    ('复盘与发布', ('retrospective',), '核验实际回归、复盘及所选本地或远端版本；不自动发布。'),
)


def audit(project, *, phase='before-delivery', submission=None, output_name='final_mv.mp4'):
    if phase not in PHASES:
        raise ValueError('无效检查阶段')
    project = Path(project).resolve()
    versions, checks = {}, []

    def load(relative):
        path = project / relative
        version = digest(path)
        value = json.loads(path.read_text(encoding='utf8'))
        versions[path] = version
        return value

    def check(key, operation, due=True):
        if not due:
            checks.append({'id': key, 'status': 'not_due', 'errors': []})
            return
        try:
            errors = operation() or []
        except Exception as exc:
            errors = [f'{type(exc).__name__}: {exc}']
        checks.append({'id': key, 'status': 'blocked' if errors else 'checked', 'errors': errors})

    def timing():
        tl = load('state/timeline.json')
        try:
            from .timing_evidence import alignment_versions
        except ImportError:
            from timing_evidence import alignment_versions
        versions.update(alignment_versions(project, tl))
        audio = gate_checks.resolve_source_audio(project, tl)
        lyric_path = project/'input/lyrics.txt'
        versions[lyric_path] = digest(lyric_path) if lyric_path.is_file() else None
        lyrics = lyric_path.read_text(encoding='utf8') if lyric_path.is_file() else ''
        results = gate_checks.check_timeline(tl, lyrics, gate_checks.probe_audio_duration(audio) if audio else None)
        errors = [c['item'] + ': ' + c.get('evidence', '') for c in results if not c['pass']]
        errors.extend(timing_source_errors(project, tl))
        if timing_authority(tl) != 'production':
            errors.append('时间轴尚未成为正式生产权威')
        return errors

    def design():
        visual_development.require(project, load('state/storyboard.json'), load('state/style.json'))

    def prompts():
        report, ok = gate_checks.audit_prompts(project, load('state/timeline.json'), load('state/style.json'))
        rows = report['checks'] + [c for shot in report['shots'] for c in shot['checks']]
        return [c['item'] + ': ' + c.get('evidence', '') for c in rows if not c['pass']]

    def windows():
        sb, tl = load('state/storyboard.json'), load('state/timeline.json')
        errors = [c['item'] + ': ' + c.get('evidence', '') for c in gate_checks.check_storyboard(sb, tl) if not c['pass']]
        require_production_timing(tl, sb['shots'])
        require_director_final_cut(load('state/visual_edit_plan.json'), sb['shots'])
        return errors

    def assets():
        sb, registry = load('state/storyboard.json'), load('state/visual_assets.json')
        if not sb.get('shots'):
            return ['没有正式镜头，不能核验资产覆盖']
        for shot in sb['shots']:
            visual_assets.require_keyframe(project, shot, registry)

    def submission_errors():
        if submission is None:
            return ['未提供真实外部提交契约；生成回执或自写manifest不能替代提交前校验']
        path = Path(submission).resolve()
        versions[path] = digest(path)
        return validate_submission_contract(json.loads(path.read_text(encoding='utf8')), project)

    def origins():
        try:
            from .chapter_admission import require as require_chapters
        except ImportError:
            from chapter_admission import require as require_chapters
        require_chapters(project, load('state/storyboard.json'), load('state/generated_clips.json')['clips'],
                         load('state/edit_selection.json')['selections'], versions)

    check('transaction', lambda: require_no_transaction(project))
    def backend_policy():
        try:
            from .execution_policy import require_video_backend
        except ImportError:
            from execution_policy import require_video_backend
        versions.update(require_video_backend(project))
    check('backend_policy', backend_policy, phase != 'before-delivery')
    check('execution_receipts', lambda: receipt_errors(project))
    check('timing', timing)
    check('design', design)
    check('sequence', lambda: sequence_direction.require(project) and [])
    def film_organization():
        try:
            from .film_grammar_bridge import require as require_organization
        except ImportError:
            from film_grammar_bridge import require as require_organization
        versions.update(require_organization(project))
    story_path=project/'state/story_plan.json'
    organization_enabled=(project/'state/creative_workflow.json').is_file()
    if story_path.is_file():
        try:
            organization_enabled=organization_enabled or bool(load('state/story_plan.json').get('film_grammar_bridge'))
        except (OSError, ValueError, TypeError, AttributeError):
            organization_enabled=True
    check('film_organization', film_organization, organization_enabled)
    check('crew_reports', lambda: current_report_errors(project))
    check('prompts', prompts)
    check('windows', windows)
    check('assets', assets)
    check('animatic', lambda: animatic_review.approved(project)[1])
    # 交付不要求重复提供某个章节的提交包；但不能据此认证历史提交顺序。
    check('submission', submission_errors, phase != 'before-delivery' or submission is not None)
    check('prototype', lambda: director_review_gate.approved(project)[1], phase != 'before-generation')
    check('media_origins', origins, phase == 'before-delivery')
    def repairs():
        try:
            from .repair_state import require_assessed
        except ImportError:
            from repair_state import require_assessed
        versions.update(require_assessed(project))
    check('repairs', repairs, phase == 'before-delivery')
    check('sequence_review', lambda: director_review_gate.delivery_approved(project)[1], phase == 'before-delivery')
    check('delivery', lambda: _delivery_errors(project, output_name), phase == 'before-delivery')
    def retrospective():
        try:
            from .release_evidence import require as require_release
        except ImportError:
            from release_evidence import require as require_release
        versions.update(require_release(project))
    check('retrospective', retrospective, phase == 'before-delivery')
    # 包含缺失文件标记；读取期间新建/修改也使本次结果失效。
    def changes():
        return [f'读取期间输入已改变: {p}' for p, version in versions.items()
                if (digest(p) if p.is_file() else None) != version]
    check('read_consistency', changes)
    by_id = {item['id']: item for item in checks}
    steps = []
    for index, (name, ids, note) in enumerate(STEPS, 1):
        relevant = [by_id[key] for key in ids if by_id[key]['status'] != 'not_due']
        status = ('blocked' if any(r['status'] == 'blocked' for r in relevant)
                  else 'machine_checked_review_scope_required' if relevant else 'evidence_required')
        steps.append({'step': index, 'name': name, 'status': status, 'checks': list(ids), 'review_note': note})
    return {'schema_version': 1, 'project_id': str(project), 'phase': phase,
            'current_phase_checks_pass': all(c['status'] != 'blocked' for c in checks),
            'all_steps_completed': False, 'historical_execution_order': 'not_proven_by_current_files',
            'artistic_approval': False, 'checks': checks, 'steps': steps,
            'limits': '只读当前检查，不补旧记录或审批；不证明过去按顺序执行，不认证实际观看或用户接受。'}


def _delivery_errors(project, output_name):
    result = delivery_state.check(project, output_name, persist=False)
    return [] if result.get('ready_for_delivery') is True else result.get('errors') or ['成片尚未ready']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project_dir', type=Path)
    parser.add_argument('--phase', choices=PHASES, default='before-delivery')
    parser.add_argument('--submission', type=Path)
    parser.add_argument('--output-name', default='final_mv.mp4')
    args = parser.parse_args()
    report = audit(args.project_dir, phase=args.phase, submission=args.submission, output_name=args.output_name)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['current_phase_checks_pass'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
