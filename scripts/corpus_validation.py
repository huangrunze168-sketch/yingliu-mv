"""电影语料独立签名验收与逐镜语义适配；不按数量自动认证。"""
import json
from pathlib import Path
try:
    from .signed_evidence import verify
    from .pipeline_contracts import digest, number
except ImportError:
    from signed_evidence import verify
    from pipeline_contracts import digest, number


def require(project, profile, storyboard, certificate_path):
    project = Path(project).resolve()
    path = (project/certificate_path).resolve()
    path.relative_to(project)
    versions = {path: digest(path)}
    payload = verify(json.loads(path.read_text(encoding='utf8')), 'film_corpus')
    # 证书在画像之外，避免自签状态和画像指纹循环。
    profile_path = (project/payload['profile_path']).resolve()
    profile_path.relative_to(project)
    if digest(profile_path) != payload['profile_sha256'] or json.loads(profile_path.read_text(encoding='utf8')) != profile:
        raise ValueError('签名验收不是当前电影画像')
    versions[profile_path] = payload['profile_sha256']
    for name, value in payload['artifacts'].items():
        source = (project/name).resolve()
        source.relative_to(project)
        if digest(source) != value: raise ValueError('验收原始标注或来源清单已变化')
        versions[source] = value
    if not payload['artifacts'] or payload.get('annotation_review') != 'pass' or not str(payload.get('review_method', '')).strip():
        raise ValueError('缺少独立标注质量验收及方法')
    films = payload['films']
    keys = [(r['canonical_film_id'], r['edition']) for r in films]
    if not keys or len(set(keys)) != len(keys) or any(not all(isinstance(v,str) and v.strip() for v in k) for k in keys):
        raise ValueError('影片身份和版本为空或重复')
    if len(keys) != profile['sample']['film_count'] or len(keys) < profile['selection']['minimum_films']:
        raise ValueError('独立去重影片数不符合画像与选择条件')
    # 逐镜匹配为签名验收的一部分，指向具体合法标注，不把统计包等同导演设计。
    mappings = payload['semantic_mapping']
    if len(mappings) != len(storyboard['shots']): raise ValueError('语义适配未覆盖全部当前镜头')
    for row, shot in zip(mappings, storyboard['shots']):
        crew = shot.get('crew', {})
        if (row.get('shot_id') != shot['id'] or row.get('scene_id') != shot['scene_id']
                or row.get('window') != shot['window'] or row.get('scene_kind') != shot.get('scene_kind', crew.get('scene_kind'))
                or row.get('scene_kind') not in {'narrative', 'performance', 'montage', 'oner'}
                or not str(row.get('rationale', '')).strip() or not str(row.get('reference_annotation', '')).strip()
                or row.get('decision') != 'applicable'):
            raise ValueError('逐镜语义适配与当前镜头/类型/窗口不符')
    if any(digest(p) != v for p,v in versions.items()): raise ValueError('语料验收期间依据变化')
    return versions
