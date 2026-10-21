"""从实际ZY索引发现导演文件；不维护少数固定导演预设，不伪造缺失方法。"""
import argparse
import json
import re
from pathlib import Path
try:
    from .pipeline_contracts import digest, atomic_batch
except ImportError:
    from pipeline_contracts import digest, atomic_batch

AXES = ('Light and Contrast Fingerprint', 'Color and Exposure Fingerprint',
        'Lens and Camera Fingerprint', 'Composition and Spatial Fingerprint')


def build_catalog(folder):
    folder = Path(folder).resolve()
    index = folder / 'index.md'
    payload = index.read_text(encoding='utf-8')
    matches = re.findall(r'- ([^\n]+)\n\s+→ \[[^\]]+\]\(([^)]+\.md)\)', payload)
    if not matches: raise ValueError('导演索引没有可发现的路由条目')
    directors, seen = [], set()
    for aliases, filename in matches:
        path = (folder / filename).resolve()
        path.relative_to(folder)
        if path.parent != folder or path in seen: raise ValueError('导演索引重复或路径无效')
        seen.add(path)
        text = path.read_text(encoding='utf-8')
        if any('## ' + axis not in text for axis in AXES):
            raise ValueError(f'{filename}: 缺少四轴方法，不能冒充完整导演文件')
        directors.append({'name': text.splitlines()[0].removeprefix('# ').strip(),
                          'aliases': [a.strip() for a in aliases.split(' / ')],
                          'file': filename, 'sha256': digest(path)})
    return {'schema_version': 1, 'source': 'ZY director index', 'index_sha256': digest(index),
            'directors': directors, 'selection_policy': '基础剧组设计先行；按叙事目标选择主导演，不按镜头随机轮换',
            'style_quality_verified': False}


def resolve_director(name, folder):
    if not isinstance(name, str) or not name.strip(): raise ValueError('导演名称无效')
    query = name.strip().casefold()
    matches = [d for d in build_catalog(folder)['directors']
               if query in {a.casefold() for a in [d['name'], *d['aliases']]}]
    if len(matches) != 1:
        raise ValueError('导演未在实际ZY索引唯一匹配，不能杜撰方法文件')
    return matches[0]


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='发现完整ZY导演库，查询别名，不自动选择风格')
    parser.add_argument('--folder', type=Path, default=Path(__file__).resolve().parents[1]/'vendor/zy-cinematic-realism/references/directors')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--query')
    args = parser.parse_args()
    catalog = build_catalog(args.folder)
    if args.output:
        expected = {args.folder/'index.md': catalog['index_sha256']}
        expected.update({args.folder/d['file']: d['sha256'] for d in catalog['directors']})
        atomic_batch({args.output: json.dumps(catalog, ensure_ascii=False, indent=2)+'\n'}, expected=expected)
    results = catalog['directors']
    if args.query:
        results = [d for d in results if args.query.casefold() in (' '.join(d['aliases'])+' '+d['name']).casefold()]
    print(json.dumps({'director_count': len(catalog['directors']), 'matches': results,
                      'missing_query_is_not_a_method': True}, ensure_ascii=False))
