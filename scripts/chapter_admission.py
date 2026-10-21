"""正式剪辑素材准入：章节归属、有效批准和真实审片媒体必须一致。"""
import json
import hashlib
from pathlib import Path
try:
    from .director_review_gate import approved
    from .pipeline_contracts import digest
except ImportError:
    from director_review_gate import approved
    from pipeline_contracts import digest


def require(project, storyboard, clips, selections, origin_versions=None):
    project = Path(project).resolve()
    try:
        from .repair_state import require_closed
    except ImportError:
        from repair_state import require_closed
    repairs = require_closed(project)
    plan = json.loads((project / 'state/chapter_plan.json').read_text(encoding='utf-8'))
    owners = {}
    for chapter in plan['chapters']:
        for sid in chapter['shot_ids']:
            if sid in owners: raise ValueError('镜头不能同时属于多个章节')
            owners[sid] = chapter['id']
    if set(owners) != {shot['id'] for shot in storyboard['shots']}:
        raise ValueError('章节镜头归属没有完整匹配正式分镜')
    approvals = {}
    evidence = {}
    sources = dict(repairs)
    for selection in selections:
        sid = selection['shot_id']
        cid = owners[sid]
        clip = clips[selection['clip_id']]
        declared = clip.get('shot_ids')
        if (not isinstance(declared, list) or not all(isinstance(item, str) for item in declared)
                or len(declared) != len(set(declared)) or not set(declared) <= set(owners)
                or any(owners[item] != cid for item in declared)
                or clip.get('chapter_id') != cid or sid not in declared):
            raise ValueError(f'{sid} 素材章节/镜头归属不一致')
        if cid not in approvals:
            path = project / 'state/reviews' / f'{cid}.json'
            try:
                payload = path.read_bytes()
            except FileNotFoundError as exc:
                raise ValueError(f'{cid} 逐章审片未批准: 缺少审片记录') from exc
            version = 'sha256:' + hashlib.sha256(payload).hexdigest()
            ok, errors = approved(project, cid)
            if not ok: raise ValueError(f'{cid} 逐章审片未批准: ' + '; '.join(errors))
            if digest(path) != version:
                raise ValueError(f'{cid} 审批版本在准入检查期间改变，请重试')
            approvals[cid] = json.loads(payload)
            evidence[cid] = version
        review = approvals[cid]
        source = (project / clip['path']).resolve()
        actual = digest(source)
        if (actual != clip['sha256'] or actual != review['reviewed_media_sha256']
                or source != (project / review['reviewed_media']).resolve()):
            raise ValueError(f'{cid} 所选素材不是该章节实际批准的媒体版本')
        try:
            from .media_origin import require as require_origin
        except ImportError:
            from media_origin import require as require_origin
        require_origin(project, clip, sources)
    for cid, version in evidence.items():
        if digest(project / 'state/reviews' / f'{cid}.json') != version:
            raise ValueError(f'{cid} 审批版本在准入检查期间改变，请重试')
    if any(digest(path) != sha for path, sha in sources.items()):
        raise ValueError('素材准入期间来源依赖已改变')
    if origin_versions is not None:
        origin_versions.update(sources)
    return evidence
