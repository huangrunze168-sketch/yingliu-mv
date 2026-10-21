"""正式入口共用的实时检查；不信任曾经通过的报告，不替代实际审片。"""
import json
from pathlib import Path

try:
    from . import gate_checks, sequence_direction, visual_development, visual_assets
    from .crew_rehearsal import current_report_errors
    from .pipeline_contracts import require_no_transaction, timing_authority
    from .workflow_execution import receipt_errors
    from .timing_evidence import errors as timing_source_errors
except ImportError:
    import gate_checks, sequence_direction, visual_development, visual_assets
    from crew_rehearsal import current_report_errors
    from pipeline_contracts import require_no_transaction, timing_authority
    from workflow_execution import receipt_errors
    from timing_evidence import errors as timing_source_errors


def generation_errors(project):
    """每次正式视频提交都重算关1/5/6和资产，而不是只检查文件或pass字段。"""
    project = Path(project).resolve()
    errors = []
    try:
        require_no_transaction(project)
        errors.extend(receipt_errors(project))
        load = lambda name: json.loads((project/'state'/name).read_text(encoding='utf8'))
        timeline, storyboard, style = load('timeline.json'), load('storyboard.json'), load('style.json')
        try:
            from .film_grammar_bridge import require as require_organization
        except ImportError:
            from film_grammar_bridge import require as require_organization
        require_organization(project, storyboard)
        try:
            from .sound_mix import require_plan
        except ImportError:
            from sound_mix import require_plan
        require_plan(project, storyboard)
        errors.extend(timing_source_errors(project, timeline))
        path = project/'input/lyrics.txt'
        lyrics = path.read_text(encoding='utf8') if path.is_file() else ''
        audio = gate_checks.resolve_source_audio(project, timeline)
        checks = gate_checks.check_timeline(timeline, lyrics, gate_checks.probe_audio_duration(audio) if audio else None)
        checks += gate_checks.check_storyboard(storyboard, timeline)
        errors.extend(c['item'] + ': ' + c.get('evidence', '') for c in checks if not c['pass'])
        if timing_authority(timeline) != 'production':
            errors.append('关1：时间轴不是正式生产权威')
        visual_development.require(project, storyboard, style)
        sequence_direction.require(project)
        errors.extend(current_report_errors(project))
        report, ok = gate_checks.audit_prompts(project, timeline, style)
        if not ok:
            rows = report['checks'] + [c for shot in report['shots'] for c in shot['checks']]
            errors.extend('关5：' + c['item'] + ': ' + c.get('evidence', '') for c in rows if not c['pass'])
        registry = load('visual_assets.json')
        for shot in storyboard['shots']:
            visual_assets.require_keyframe(project, shot, registry)
        require_no_transaction(project)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, StopIteration) as exc:
        errors.append('生产前置检查无法核验: ' + str(exc))
    return errors


def require_current(project):
    errors = generation_errors(project)
    if errors:
        raise ValueError('正式生产当前检查未通过: ' + '; '.join(errors))
