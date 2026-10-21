"""审片依赖：公共锁＋本章投影；全片审片绑定整条序列。"""
import hashlib
import json
from pathlib import Path
try:
    from .pipeline_contracts import digest
except ImportError:
    from pipeline_contracts import digest


def load(project, relative):
    return json.loads((project / relative).read_text(encoding='utf-8'))


def semantic_hash(value):
    return 'sha256:' + hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                                allow_nan=False).encode()).hexdigest()


def scoped_value(value):
    # 全文件来源指纹是构建器的溯源信息，不是本章创作事实；直接文件仍逐项绑定。
    if isinstance(value, dict):
        return {key: scoped_value(item) for key, item in value.items()
                if key not in {'dependencies', 'prompt_fingerprint', 'source_prompt_sha256'}
                and not key.endswith('_fingerprint')}
    if isinstance(value, list): return [scoped_value(item) for item in value]
    return value


def snapshot(project, scope=None):
    project = Path(project).resolve()
    plan = load(project, 'state/chapter_plan.json')
    cid = scope or plan['production_policy']['prototype_chapter']
    full = cid == 'full_sequence'
    chapters = plan['chapters'] if full else [next(c for c in plan['chapters'] if c['id'] == cid)]
    ids = {sid for c in chapters for sid in c['shot_ids']}
    sb = load(project, 'state/storyboard.json')
    scenes = {s['scene_id'] for s in sb['shots'] if s['id'] in ids}
    result = {}
    try:
        from .film_grammar_bridge import require as require_organization
    except ImportError:
        from film_grammar_bridge import require as require_organization
    result.update({str(p):v for p,v in require_organization(project,sb).items()})
    try:
        from .sound_mix import require_plan
    except ImportError:
        from sound_mix import require_plan
    _, sound_versions = require_plan(project, sb)
    result.update({str(p): sha for p, sha in sound_versions.items()})
    def add(relative, value=None):
        path = (project / relative).resolve()
        if not path.is_file():
            raise FileNotFoundError(path)
        result[str(path)] = digest(path) if value is None or full else semantic_hash(scoped_value(value))
    def filtered(relative, list_key, keep):
        value = load(project, relative)
        public = {k: v for k, v in value.items() if k != list_key and not k.endswith('_fingerprint')}
        public[list_key] = [item for item in value.get(list_key, []) if keep(item)]
        add(relative, public)
    add('state/chapter_plan.json', {'production_policy': plan['production_policy'],
                                   'chapters': chapters,
                                   'backend_selection_policy': plan.get('backend_selection_policy')})
    filtered('state/storyboard.json', 'shots', lambda s: s['id'] in ids)
    filtered('state/story_plan.json', 'shots', lambda s: s['id'] in ids)
    filtered('state/visual_edit_plan.json', 'shots', lambda s: s['id'] in ids)
    filtered('state/development.json', 'scenes', lambda s: s['id'] in scenes)
    filtered('state/chapter_audio_manifest.json', 'segments', lambda s: s['chapter_id'] in {c['id'] for c in chapters})
    # 音乐权威、人物/服装、全片风格及部门bible为公共锁。
    add('state/style.json'); add('state/timeline.json')
    if (project / 'state/music_structure.json').exists(): add('state/music_structure.json')
    development = load(project, 'state/development.json')
    # P3.1：默认强制 sequence_direction
    if development.get('creative_workflow_revision') != 2:
        raise ValueError(
            'creative_workflow_revision 必须为 2；revision≠2 禁止跳过 sequence_direction'
        )
    sequence = load(project, 'state/sequence_direction.json')
    add('state/sequence_direction.json', {
        **{k: v for k, v in sequence.items() if k != 'shots'},
        'shots': [s for s in sequence['shots'] if s['id'] in ids]})
    for source in sequence['sources'].values(): add(source['path'])
    if development.get('grammar_reference'):
        binding = development['grammar_reference']
        add(binding['profile_path'])
        if binding.get('mode') == 'enforce_envelope':
            try:
                from .corpus_validation import require as require_corpus
            except ImportError:
                from corpus_validation import require as require_corpus
            result.update({str(p):v for p,v in require_corpus(project, load(project, binding['profile_path']), sb, binding['certificate_path']).items()})
    timeline = load(project, 'state/timeline.json')
    add(timeline['master_audio'])
    for path in (project / 'input').glob('protagonist.*'): add(path)
    if (project/'state/reference_roles.json').is_file():
        try:
            from .reference_roles import require as require_roles
        except ImportError:
            from reference_roles import require as require_roles
        _, identity_versions = require_roles(project)
        result.update({str(p): value for p, value in identity_versions.items()})
    registry = load(project, 'state/visual_assets.json')
    selected = {aid: asset for aid, asset in registry['assets'].items()
                if (asset.get('kind') == 'scene_master' and asset.get('scene_id') in scenes)
                or asset.get('shot_id', aid) in ids}
    for asset in selected.values():
        parent = asset.get('parent_asset')
        if parent and parent not in selected:
            raise ValueError('本章关键帧母版缺失')
        add(asset['path'])
    add('state/visual_assets.json', {'assets': selected})
    for chapter in chapters:
        add(f"prompts/chapters/{chapter['id']}.json")
        add(f"prompts/chapters/{chapter['id']}.txt")
        add(chapter['audio_asset']['local_path'])
    for sid in ids:
        for folder in ('prompts', 'prompts/keyframes', 'prompts/motion'):
            path = project / folder / f'{sid}.json'
            if folder == 'prompts' or path.exists(): add(path, load(project, str(path)))
        text = project / 'prompts' / f'{sid}.txt'
        if text.exists(): add(text)
    for scene in scenes:
        path = project / 'prompts/scenes' / f'{scene}.json'
        if path.exists(): add(path)
    if full:
        for chapter in chapters:
            add(f"state/reviews/{chapter['id']}.json")
        for name in ('edit_selection', 'edit_assembly', 'generated_clips'): add(f'state/{name}.json')
        assembly = load(project, 'state/edit_assembly.json')
        if assembly.get('status') != 'rendered' or assembly.get('technical_preview'):
            raise ValueError('全片审片前须完成正式选段渲染，不能审完再转换剪辑清单')
        for entry in assembly['entries']:
            add(entry['source_path']); add(entry['target_path'])
    return result
