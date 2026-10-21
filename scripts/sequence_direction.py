"""参考段落与导演序列交接；验证可执行事实，不批准审美。"""
import argparse
import copy
import json
import math
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

try:
    from .pipeline_contracts import digest, atomic_batch, require_no_transaction
except ImportError:
    from pipeline_contracts import digest, atomic_batch, require_no_transaction


def ada_passage(path, start_ms, end_ms):
    if not 0 <= start_ms < end_ms:
        raise ValueError('参考区间必须正向')
    with zipfile.ZipFile(path) as archive:
        info = archive.getinfo('content.xml')
        if info.file_size > 32 * 1024 * 1024:
            raise ValueError('标注包过大')
        payload = archive.read(info)
    if b'<!DOCTYPE' in payload.upper() or b'<!ENTITY' in payload.upper():
        raise ValueError('禁止XML实体')
    ns = {'a': 'http://experience.univ-lyon1.fr/advene/ns'}
    rows = []
    for node in ET.fromstring(payload).findall('.//a:annotation', ns):
        fragment = node.find('a:millisecond-fragment', ns)
        content = node.find('a:content', ns)
        if fragment is None:
            continue
        begin, end = int(fragment.get('begin')), int(fragment.get('end'))
        if begin < end_ms and end > start_ms:
            rows.append({'annotation_id': node.get('id'), 'type': node.get('type', '').lstrip('#'),
                         'begin_ms': begin, 'end_ms': end,
                         'value': content.text if content is not None else ''})
    if not rows:
        raise ValueError('参考段落没有标注')
    return {'path': str(Path(path).resolve()), 'sha256': digest(Path(path)),
            'scope': 'annotation_only', 'media_observed': False,
            'license': 'CC BY-SA 3.0（标注，不是电影视频）',
            'range_ms': [start_ms, end_ms], 'annotations': rows}


def validate(plan):
    """不允许引用失效、缺镜与无目的机位被编译为正式设计。"""
    errors = []
    sources = plan.get('sources', {})
    if not sources:
        errors.append('缺少实际参考来源')
    for key, source in sources.items():
        path = Path(source.get('path', ''))
        if not path.is_file() or digest(path) != source.get('sha256'):
            errors.append('参考来源不存在或改变: ' + key)
        if source.get('scope') == 'annotation_only' and source.get('media_observed') is not False:
            errors.append('标注不能冒充电影观看: ' + key)
    shots = plan.get('shots', [])
    ids = [s.get('id') for s in shots]
    if not shots or any(not isinstance(s, str) or not s for s in ids):
        errors.append('镜头ID缺失或重复')
    elif len(set(ids)) != len(ids):
        errors.append('镜头ID缺失或重复')
    previous = 0
    for shot in shots:
        try:
            start, end = shot['window']
            if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in (start, end)):
                raise ValueError('非有限时间')
            if start != previous or end <= start:
                raise ValueError('时序不连续')
            previous = end
            for field in ('action', 'audience_gain', 'camera_reason', 'cut_reason', 'lighting', 'art', 'performance'):
                if not isinstance(shot.get(field), str) or not shot[field].strip():
                    raise ValueError('缺少 ' + field)
            for field in ('camera_xyz', 'actor_start_xy', 'actor_end_xy'):
                coordinates = shot[field]
                if not isinstance(coordinates, list) or len(coordinates) != (3 if field == 'camera_xyz' else 2):
                    raise ValueError('坐标格式错误')
                if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in coordinates):
                    raise ValueError('坐标非有限')
            if not shot.get('reference_ids') or any(k not in sources for k in shot['reference_ids']):
                raise ValueError('没有参考应用关系')
        except (KeyError, TypeError, ValueError) as exc:
            errors.append(str(shot.get('id')) + ': ' + str(exc))
    if previous != plan.get('duration_sec'):
        errors.append('没有覆盖全曲')
    if plan.get('artistic_approval') is not False:
        errors.append('设计编译不能自动批准艺术质量')
    return errors


def require(project):
    path = Path(project) / 'state/sequence_direction.json'
    raw = path.read_bytes()
    plan = json.loads(raw)
    errors = validate(plan)
    storyboard = json.loads((Path(project) / 'state/storyboard.json').read_text(encoding='utf-8'))
    if [s['id'] for s in plan['shots']] != [s['id'] for s in storyboard['shots']]:
        errors.append('导演序列与正式分镜不一致')
    for designed, formal in zip(plan['shots'], storyboard['shots']):
        if designed['window'] != [formal['window']['start'], formal['window']['end']]:
            errors.append('导演序列与正式时间窗不一致')
        if formal.get('sequence_direction') != designed:
            errors.append('正式分镜未保留导演序列执行事实: ' + formal['id'])
        camera = formal.get('crew', {}).get('cinematography', {})
        if camera.get('position_xy') != designed['camera_xyz'][:2] or camera.get('shot_size') != designed['shot_size']:
            errors.append('摄影事实与导演序列冲突: ' + formal['id'])
        if formal.get('current_action') != designed['action']:
            errors.append('当前动作与导演序列冲突: ' + formal['id'])
    if path.read_bytes() != raw:
        errors.append('导演序列在读取期间改变')
    for key, source in plan['sources'].items():
        if not Path(source['path']).is_file() or digest(Path(source['path'])) != source['sha256']:
            errors.append('参考在读取期间改变: ' + key)
    if errors:
        raise ValueError('; '.join(errors))
    return plan



def align_to_storyboard(plan, storyboard):
    """只吸附音乐时间窗；创作事实必须已由导演提供，不自动补写或改机位。"""
    designed_shots, formal_shots = plan['shots'], storyboard.get('shots', [])
    ids = [s['id'] for s in designed_shots]
    if (not ids or len(ids) != len(set(ids))
            or ids != [s.get('id') for s in formal_shots]):
        raise ValueError('导演序列镜头集合与正式分镜不一致，须先重新设计')
    result = copy.deepcopy(plan)
    for designed, formal in zip(result['shots'], formal_shots):
        camera = (formal.get('crew') or {}).get('cinematography') or {}
        if (formal.get('current_action') != designed['action']
                or camera.get('position_xy') != designed['camera_xyz'][:2]
                or camera.get('shot_size') != designed.get('shot_size')):
            raise ValueError('导演序列动作/机位/景别与分镜冲突，禁止自动改写: ' + formal['id'])
        designed['window'] = [formal['window']['start'], formal['window']['end']]
        formal['sequence_direction'] = copy.deepcopy(designed)
    result['duration_sec'] = result['shots'][-1]['window'][1]
    errors = validate(result)
    if errors:
        raise ValueError('时间窗吸附后导演序列无效: ' + '; '.join(errors))
    return result


def realign_after_storyboard(project, storyboard=None):
    """兼容入口：显式检查后原子发布，不生成缺失设计，也不吞掉错误。"""
    project = Path(project).resolve()
    require_no_transaction(project)
    path = project / 'state/sequence_direction.json'
    if not path.is_file():
        return None
    sb_path = project / 'state/storyboard.json'
    expected = {p: digest(p) if p.is_file() else None for p in (path, sb_path)}
    plan = json.loads(path.read_text(encoding='utf8'))
    storyboard = copy.deepcopy(storyboard) if storyboard is not None else json.loads(sb_path.read_text(encoding='utf8'))
    result = align_to_storyboard(plan, storyboard)
    expected.update({Path(v['path']): v['sha256'] for v in result['sources'].values()})
    atomic_batch({path: json.dumps(result, ensure_ascii=False, indent=2)+'\n',
                  sb_path: json.dumps(storyboard, ensure_ascii=False, indent=2)+'\n'}, expected=expected)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('plan', type=Path)
    args = parser.parse_args()
    errors = validate(json.loads(args.plan.read_text(encoding='utf-8')))
    print(json.dumps({'structural_errors': errors, 'artistic_approval': False}, ensure_ascii=False))
    raise SystemExit(bool(errors))
