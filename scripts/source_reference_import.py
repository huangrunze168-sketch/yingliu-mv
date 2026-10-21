"""导入两种真实公开标注的证据与摘要；不下载影片，不把视频数冒充电影数。"""
import argparse
import csv
import json
import subprocess
import zipfile
from collections import Counter
from pathlib import Path
import xml.etree.ElementTree as ET
try:
    from .pipeline_contracts import atomic_batch, digest
    from .film_grammar_corpus import quantile
except ImportError:
    from pipeline_contracts import atomic_batch, digest
    from film_grammar_corpus import quantile


def pairs(path):
    result, expected = [], 0
    for raw in Path(path).read_text(encoding='utf-8').splitlines():
        if not raw.strip(): continue
        fields = raw.split()
        if len(fields) != 2: raise ValueError(f'{path}: 需两个整数端点')
        start, end = map(int, fields)
        if start != expected or end < start:
            raise ValueError(f'{path}: 窗口缺口、倒序或重叠')
        result.append((start, end))
        expected = end + 1
    if not result: raise ValueError(f'{path}: 空标注')
    return result


def moviechat(repo):
    repo = Path(repo).resolve()
    types_path = repo / 'data/metadata/video_type.csv'
    with types_path.open(encoding='utf-8-sig', newline='') as handle:
        rows = list(csv.DictReader(handle))
    types = {r['video_id']: r['type'] for r in rows}
    if len(types) != len(rows): raise ValueError('MovieChat视频ID重复')
    shots = {p.stem: p for p in (repo / 'data/shot').glob('*.txt')}
    scenes = {p.stem: p for p in (repo / 'data/scene').glob('*.txt')}
    if not types or set(types) != set(shots) or set(shots) != set(scenes):
        raise ValueError('MovieChat镜头、场景和类别文件没有完全对应')
    evidence = {str(types_path): digest(types_path)}
    grouped, totals = {}, Counter()
    for vid in sorted(types):
        kind = types[vid]
        if kind not in {'movie', 'documentary', 'tvseries'}: raise ValueError('未知视频类别')
        shot_pairs, scene_pairs = pairs(shots[vid]), pairs(scenes[vid])
        if scene_pairs[-1][1] != len(shot_pairs)-1:
            raise ValueError(f'{vid}: 场景未完整覆盖镜头')
        bucket = grouped.setdefault(kind, {'videos': 0, 'shots': 0, 'scenes': 0,
                                          'shot_lengths': [], 'scene_sizes': []})
        bucket['videos'] += 1
        bucket['shots'] += len(shot_pairs)
        bucket['scenes'] += len(scene_pairs)
        bucket['shot_lengths'].extend(b-a+1 for a, b in shot_pairs)
        bucket['scene_sizes'].extend(b-a+1 for a, b in scene_pairs)
        totals.update(videos=1, shots=len(shot_pairs), scenes=len(scene_pairs))
        for path in (shots[vid], scenes[vid]): evidence[str(path)] = digest(path)
    for bucket in grouped.values():
        for key in ('shot_lengths', 'scene_sizes'):
            values = bucket.pop(key)
            bucket[key + '_quantiles'] = {name: quantile(values, q) for name, q in
                                         [('p10', .1), ('p50', .5), ('p90', .9)]}
    return {'source_id': 'moviechat-sseg', 'role': 'rhythm_and_scene_structure',
            'status': 'exploratory_only', 'unit': 'frames_and_shot_counts',
            'totals': dict(totals), 'by_video_type': grouped,
            'independent_film_count': None, 'rating_verified': False,
            'seconds_statistics_available': False,
            'limits': ['不假设24FPS；未经实测帧率不生成秒数统计', '不把视频片段数计为独立电影数',
                       '没有景别和运镜标签，不填补或猜测这些字段']}, evidence


def advene_package(path):
    with zipfile.ZipFile(path) as archive:
        entry = archive.getinfo('content.xml')
        if entry.file_size > 32*1024*1024: raise ValueError('AdA包过大，拒绝解析')
        payload = archive.read(entry)
    if b'<!DOCTYPE' in payload or b'<!ENTITY' in payload: raise ValueError('拒绝XML实体声明')
    root = ET.fromstring(payload)
    ns = {'a': 'http://experience.univ-lyon1.fr/advene/ns'}
    counts, invalid = Counter(), 0
    for annotation in root.findall('a:annotations/a:annotation', ns):
        fragment = annotation.find('a:millisecond-fragment', ns)
        if fragment is None: invalid += 1; continue
        try:
            start, end = int(fragment.attrib['begin']), int(fragment.attrib['end'])
            if start < 0 or end <= start: raise ValueError
        except (KeyError, ValueError): invalid += 1; continue
        counts[annotation.attrib.get('type', 'unknown').lstrip('#')] += 1
    return counts, invalid


def ada(repo):
    repo = Path(repo).resolve()
    packages = sorted((repo / 'annotations/manual-annotations_advene_azp_files').rglob('*.azp'))
    if not packages: raise ValueError('缺少ProjectAdA人工标注包')
    counts, invalid, evidence = Counter(), 0, {}
    for path in packages:
        types, failures = advene_package(path)
        counts.update(types)
        invalid += failures
        evidence[str(path)] = digest(path)
    return {'source_id': 'projectada', 'role': 'visual_design', 'status': 'exploratory_only',
            'manual_package_count': len(packages), 'annotation_type_counts': dict(counts),
            'valid_annotation_count': sum(counts.values()), 'invalid_time_intervals': invalid,
            'quality_review_required': True,
            'automatic_annotations_imported': False, 'content_observation_verified': False,
            'limits': ['仅统计真实人工标注包；未混入自动标注', '语义标注可重叠或有缺口，不等同逐镜切分',
                       '未复制对白、字幕或电影画面；尚未做语义逐项抽检']}, evidence


def build(ada_repo, moviechat_repo, output):
    reports, expected = [], {}
    for path, importer, url, license_name, license_path in (
        (Path(ada_repo).resolve(), ada, 'https://github.com/ProjectAdA/public', 'CC BY-SA 3.0', 'annotations/README.md'),
        (Path(moviechat_repo).resolve(), moviechat, 'https://github.com/jungucho92/moviechat-sseg', 'CC BY 4.0', 'LICENSE-DATA')):
        report, files = importer(path)
        commit = subprocess.run(['git', '-C', str(path), 'rev-parse', 'HEAD'],
                                capture_output=True, text=True, check=True).stdout.strip()
        report.update(repository=url, checkout=str(path), commit=commit, license=license_name,
                      license_evidence=str(path / license_path))
        files[str(path / license_path)] = digest(path / license_path)
        expected.update(files)
        reports.append(report)
    record = {'schema_version': 1, 'media_policy': 'annotations_only', 'status': 'exploratory_only',
              'production_gate_enabled': False, 'sources': reports, 'input_fingerprints': expected,
              'usage': '两库职责分离，供建议性设计与结构参考；不是生产电影画像'}
    atomic_batch({Path(output): json.dumps(record, ensure_ascii=False, indent=2)+'\n'}, expected=expected)
    return record


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='导入ProjectAdA与MovieChat-SSeg真实标注参考')
    parser.add_argument('--ada-repo', type=Path, required=True)
    parser.add_argument('--moviechat-repo', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    result = build(args.ada_repo, args.moviechat_repo, args.output)
    print(json.dumps({'status': result['status'], 'output': str(args.output),
                      'sources': [{k: v for k, v in s.items() if k not in {'annotation_type_counts', 'limits'}}
                                  for s in result['sources']]}, ensure_ascii=False))
