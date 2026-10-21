"""生成前剧组制作桌：展示设计交接与返工任务，不自动批准艺术质量。"""
import argparse
import hashlib
import html
import json
from pathlib import Path
try:
    from .crew_pipeline import validate_crew_pipeline, DEPARTMENT_FIELDS
    from .crew_execution import execution_lines
    from .director_catalog import build_catalog
    from .pipeline_contracts import atomic_batch, digest, timeline_errors, unique_ids
except ImportError:
    from crew_pipeline import validate_crew_pipeline, DEPARTMENT_FIELDS
    from crew_execution import execution_lines
    from director_catalog import build_catalog
    from pipeline_contracts import atomic_batch, digest, timeline_errors, unique_ids

REWORK = {
    'screenwriting': '写出本段人物目标、阻力和观众新获得的信息；可通过表演情绪推进，不强迫每镜剧情反转。',
    'directing': '确定观看对象与表演节拍；按需要设计覆盖候选，再明确保留/删除理由。',
    'production_design': '设计可行动空间、前中后景、关键道具与状态变化；说明每层叙事用途。',
    'lighting': '把实际来源、遮挡、人物明暗路径、材质反射和曝光保护对应到Scene Master。',
    'character_design': '锁定身份与服装，说明动作后状态；不把人物一致性等同同一姿态。',
    'blocking': '给起点→真实路径→终点和视线；确认目标与障碍的空间关系。',
    'cinematography': '画实际机位覆盖与轴线；用距离、景别、遮挡和焦点控制信息，不随机换角度。',
    'editing': '为切点写信息/动作/音乐理由；整段带音乐观看，不能直接把生成包长度当镜长。',
}


def analyze(development, storyboard, *, music_structure=None, timeline=None, fps=24.0, project=None):
    shots = storyboard.get('shots', [])
    if not unique_ids(shots): raise ValueError('预演分镜ID为空或重复')
    failures = timeline_errors(shots, shots[-1]['window']['end'])
    if failures: raise ValueError('; '.join(failures))
    errors = validate_crew_pipeline(
        development, storyboard,
        music_structure=music_structure, timeline=timeline, fps=fps, project=project,
    )
    rows, tasks, grouped_tasks = [], [], {}
    for shot in shots:
        crew = shot.get('crew') or {}
        missing = [department for department in DEPARTMENT_FIELDS if not crew.get(department)]
        duration = float(shot['window']['end'])-float(shot['window']['start'])
        rows.append({'shot_id': shot['id'], 'scene_id': shot['scene_id'], 'window': shot['window'],
            'duration_sec': duration, 'attention': shot.get('visual_center', ''),
            'information_gain': crew.get('screenwriting', {}).get('audience_gain', shot.get('audience_information', '')),
            'execution': execution_lines(crew), 'missing_departments': missing,
            'observed_media': False})
        for department in missing:
            task = grouped_tasks.setdefault((shot['scene_id'], department), {
                'shot_ids': [], 'scene_id': shot['scene_id'], 'owner': department,
                'reason': '这些镜头没有部门执行设计', 'action': REWORK[department], 'status': 'proposed'})
            task['shot_ids'].append(shot['id'])
    tasks.extend(grouped_tasks.values())
    # 仅精确重复检测，不声称理解艺术意图；有意重复可在预演中保留。
    for left, right in zip(rows, rows[1:]):
        if left['information_gain'] and left['information_gain'] == right['information_gain']:
            tasks.append({'shot_ids': [left['shot_id'], right['shot_id']], 'owner': 'screenwriting',
                'reason': '相邻镜头新增信息文本完全相同，可能重复，也可能是有意复现',
                'action': '比较删镜、合镜、改观看对象三种候选；保留有意重复需说明情绪或音乐作用。',
                'status': 'proposed', 'heuristic_only': True})
    cuts = []
    for left, right in zip(shots, shots[1:]):
        a, b = left.get('crew') or {}, right.get('crew') or {}
        editing = b.get('editing') or {}
        same = left['scene_id'] == right['scene_id']
        relation = editing.get('time_relation', 'continuous')
        cuts.append({'from': left['id'], 'to': right['id'], 'time_sec': right['window']['start'],
            'time_relation': relation, 'continuous_actor_path_required': same and relation == 'continuous',
            'previous_end': a.get('blocking', {}).get('end_mark', ''),
            'next_start': b.get('blocking', {}).get('start_mark', ''),
            'attention_from': left.get('visual_center', ''), 'attention_to': right.get('visual_center', ''),
            'outgoing_relation': a.get('editing', {}).get('outgoing_relation', ''),
            'incoming_relation': editing.get('incoming_relation', ''),
            'music_cue': editing.get('music_cue', ''), 'watched': False})
    scenes = []
    for scene in development.get('scenes', []):
        scenes.append({'scene_id': scene['id'], 'art_direction': scene.get('art_direction', {}),
                       'lighting_plan': scene.get('lighting_plan', {}),
                       'review_questions': ['构图的前中后层分别让观众看见什么？',
                           '人物路径在哪些点改变遮挡、受光或焦点？',
                           '主要光源是否真实存在，哪些暗部保留，哪个材质承接高光？',
                           '道具/空间是否随人物行为发生状态变化，而非静态装饰？'],
                       'look_test_observed': False})
    return {'schema_version': 1, 'stage': 'preproduction_crew_rehearsal',
            'status': 'structural_rework_required' if errors else 'design_ready_for_rehearsal',
            'structural_errors': errors, 'shots': rows, 'cuts': cuts, 'scene_workshops': scenes,
            'department_rework': tasks, 'director_stylization': {
                'eligible_for_routing': not errors, 'applied': False,
                'reason': '先修基础剧组设计，再按叙事目标读取ZY推荐矩阵与选定导演文件'},
            'artistic_approval': False, 'dynamic_review_required': True,
            'metrics': {'shots': len(rows), 'average_shot_sec': sum(r['duration_sec'] for r in rows)/len(rows),
                        'max_shot_sec': max(r['duration_sec'] for r in rows)}}


def render_html(report):
    def esc(value):
        return html.escape(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False))
    parts = ['<!doctype html><meta charset="utf-8"><title>剧组制作桌</title>',
        '<style>body{font:16px sans-serif;max-width:1100px;margin:30px auto;background:#f4f1e9;color:#202426}section{background:white;padding:20px;margin:15px 0;border:1px solid #ccc}table{border-collapse:collapse;width:100%}td,th{border:1px solid #ddd;padding:8px;vertical-align:top}</style>',
        '<h1>生成前剧组制作桌</h1><p>设计诊断，不是已观看预演、空间模拟或艺术批准。</p>',
        '<p>状态：'+esc(report['status'])+'</p>', '<h2>结构问题</h2><ul>']
    parts.extend('<li>'+esc(e)+'</li>' for e in report['structural_errors'])
    parts.append('</ul><h2>按音乐时间排序的镜头</h2>')
    for row in report['shots']:
        parts.append('<section><h3>'+esc(row['shot_id'])+' · '+esc(row['window'])+'</h3><p>观看对象：'+esc(row['attention'])+'</p><p>新增信息：'+esc(row['information_gain'])+'</p>')
        parts.extend('<p>'+esc(line)+'</p>' for line in row['execution'])
        parts.append('<p>待设计部门：'+esc(row['missing_departments'])+'</p></section>')
    parts.append('<h2>逐切点交接</h2><table><tr><th>切点</th><th>观看/动作</th><th>衔接/音乐</th></tr>')
    for cut in report['cuts']:
        parts.append('<tr><td>'+esc([cut['from'], cut['to'], cut['time_sec']])+'</td><td>'+esc([cut['attention_from'], cut['attention_to'], cut['previous_end'], cut['next_start']])+'</td><td>'+esc([cut['time_relation'], cut['outgoing_relation'], cut['incoming_relation'], cut['music_cue']])+'</td></tr>')
    parts.append('</table><h2>美术与灯光共审</h2>')
    for scene in report['scene_workshops']:
        parts.append('<section><h3>'+esc(scene['scene_id'])+'</h3><p>'+esc(scene['art_direction'])+'</p><p>'+esc(scene['lighting_plan'])+'</p>')
        parts.extend('<p>'+esc(q)+'</p>' for q in scene['review_questions'])
        parts.append('</section>')
    parts.append('<h2>部门返工候选（未自动执行）</h2>')
    parts.extend('<p>'+esc(t)+'</p>' for t in report['department_rework'])
    return '\n'.join(parts)


def build(project):
    project = Path(project).resolve()
    inputs, expected = {}, {}
    for name in ('development', 'storyboard'):
        path = project / 'state' / f'{name}.json'
        payload = path.read_bytes()
        expected[path] = 'sha256:'+hashlib.sha256(payload).hexdigest()
        inputs[name] = json.loads(payload)
    music_path = project / 'state' / 'music_structure.json'
    timeline_path = project / 'state' / 'timeline.json'
    music = json.loads(music_path.read_text(encoding='utf-8')) if music_path.is_file() else None
    timeline = json.loads(timeline_path.read_text(encoding='utf-8')) if timeline_path.is_file() else None
    fps = float((inputs['storyboard'] or {}).get('fps') or (timeline or {}).get('fps_assumed') or 24)
    report = analyze(
        inputs['development'], inputs['storyboard'],
        music_structure=music, timeline=timeline, fps=fps, project=project,
    )
    # P3.1：默认强制 sequence_direction；revision≠2 直接不合格
    rev = inputs['development'].get('creative_workflow_revision')
    if rev != 2:
        raise ValueError(
            'creative_workflow_revision 必须为 2；revision≠2 禁止跳过 sequence_direction'
        )
    try:
        from .sequence_direction import require
    except ImportError:
        from sequence_direction import require
    path = project / 'state/sequence_direction.json'
    version = digest(path)
    sequence = require(project)
    if digest(path) != version:
        raise ValueError('导演序列版本在制作桌编译期间改变')
    report['sequence_direction'] = sequence
    expected[path] = version
    expected.update({Path(s['path']): s['sha256'] for s in sequence['sources'].values()})
    report['input_fingerprints'] = {str(k): v for k, v in expected.items()}
    folder = Path(__file__).resolve().parents[1]/'vendor/zy-cinematic-realism/references/directors'
    catalog = build_catalog(folder)
    expected[folder/'index.md'] = catalog['index_sha256']
    expected.update({folder/d['file']: d['sha256'] for d in catalog['directors']})
    report['director_library'] = catalog
    output = project / 'gates/crew_rehearsal.json'
    page = output.with_suffix('.html')
    for path in (output, page): expected[path] = digest(path) if path.is_file() else None
    atomic_batch({output: json.dumps(report, ensure_ascii=False, indent=2)+'\n', page: render_html(report)}, expected=expected)
    return report


def require_before_prompts(project):
    """编译入口自动更新制作桌；不是依赖用户记得单独运行CLI。"""
    report = build(project)
    if report['structural_errors']:
        raise ValueError('基础剧组设计须先返工，禁止编译导演风格提示词；见gates/crew_rehearsal.html:\n'
                         + '\n'.join(report['structural_errors']))
    return report


def current_report_errors(project):
    """外部提交与审计共用：旧报告的通过字符串不能代替当前输入。"""
    project = Path(project).resolve()
    errors = []
    required_inputs = {str(project/'state'/f'{name}.json'): project/'state'/f'{name}.json'
                       for name in ('development', 'storyboard', 'sequence_direction')}
    try:
        sequence = json.loads((project/'state/sequence_direction.json').read_text(encoding='utf8'))
        for source in sequence['sources'].values():
            path = (project/source['path']).resolve()
            required_inputs[str(path)] = path
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        errors.append('导演序列来源无法核验: ' + str(exc))
    for filename, status, field, required in (
        ('crew_pipeline.json', 'structural_pass', 'source_fingerprints',
         {'development': project/'state/development.json', 'storyboard': project/'state/storyboard.json'}),
        ('crew_rehearsal.json', 'design_ready_for_rehearsal', 'input_fingerprints',
         required_inputs),
    ):
        try:
            report = json.loads((project/'gates'/filename).read_text(encoding='utf8'))
            if report.get('status') != status or report.get('errors') or report.get('structural_errors'):
                errors.append(filename + ': 门禁未通过')
            versions = report.get(field)
            if not isinstance(versions, dict) or not versions:
                errors.append(filename + ': 缺少输入指纹，须重新运行门禁')
                continue
            paths = required if filename == 'crew_pipeline.json' else {
                key: project/str(key) for key in versions}
            for key in required:
                if key not in versions:
                    errors.append(filename + ': 缺少输入指纹 ' + key)
            for key, path in paths.items():
                if not path.is_file() or versions.get(key) != digest(path):
                    errors.append(filename + ': 输入指纹过期 ' + key)
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            errors.append(filename + ': 无法核验 ' + str(exc))
    return errors


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='生成后端无关的剧组预演与部门返工制作桌')
    parser.add_argument('project', type=Path)
    args = parser.parse_args()
    result = build(args.project)
    print(json.dumps({'status': result['status'], 'metrics': result['metrics'],
                      'rework_tasks': len(result['department_rework']), 'report_generated': True,
                      'artistic_approval': False}, ensure_ascii=False))
    raise SystemExit(1 if result['structural_errors'] else 0)
