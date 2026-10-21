"""将既有原图和真实托管回执登记为身份/服装职责；不上传、不批准或派发。"""
import argparse
import json
from pathlib import Path
import uuid

try:
    from .pipeline_contracts import atomic_batch, digest, require_no_transaction
    from .submission_jobs import upload_errors
except ImportError:
    from pipeline_contracts import atomic_batch, digest, require_no_transaction
    from submission_jobs import upload_errors


def register(project, receipts_path):
    project, receipts_path = Path(project).resolve(), Path(receipts_path).resolve()
    require_no_transaction(project)
    receipt_version = digest(receipts_path)
    rows = json.loads(receipts_path.read_text(encoding='utf-8'))
    if not isinstance(rows, list) or not rows:
        raise ValueError('没有实际人物回执')
    bindings, characters, names = [], [], set()
    expected = {receipts_path: receipt_version}
    for index, row in enumerate(rows, 1):
        name = row['character']
        if not isinstance(name, str) or not name.strip() or name in names:
            raise ValueError('人物名称为空或重复')
        names.add(name)
        path = Path(row['receipt_path']).resolve()
        path.relative_to(project)
        expected[path] = digest(path)
        receipt = json.loads(path.read_text(encoding='utf-8'))
        local = Path(receipt['local_path']).resolve()
        local.relative_to(project/'input')
        binding = {'handle': f'@图片{index}', 'modality': 'image', 'character': name,
                   'intents': ['character_identity', 'wardrobe'], 'binding_status': 'uploaded',
                   'upload_receipt_path': str(path.relative_to(project)),
                   'source': {'url': receipt['locator'], 'sha256': digest(local), 'local_path': str(local)},
                   'reuse_scope': 'existing_recorded_hosted_asset_not_new_upload'}
        bindings.append(binding)
        characters.append({'name': name, 'path': str(local.relative_to(project)), 'sha256': digest(local),
                           'roles': ['likeness', 'wardrobe'],
                           'exclusions': ['background', 'composition', 'lighting', 'pose'],
                           'upload_receipt_path': binding['upload_receipt_path']})
    errors, versions = upload_errors(project, {'reference_bindings': bindings})
    if errors:
        raise ValueError('; '.join(errors))
    expected.update(versions)
    record = {'schema_version': 1, 'project_id': str(project), 'characters': characters,
              'reference_bindings': bindings, 'production_approval': False, 'dispatch_allowed': False,
              'scope': '当前原图/既有托管响应一致性，参考职责固定为身份与服装；不认证回片人物一致'}
    target = project/'state/reference_roles.json'
    if target.is_file():
        if json.loads(target.read_text(encoding='utf-8')) == record:
            return record
        raise FileExistsError('已有不同的参考职责登记，须显式修订并重审，不覆盖')
    expected[target] = None
    atomic_batch({target: json.dumps(record, ensure_ascii=False, indent=2)}, expected=expected)
    return record


def require(project):
    """每次消费职责时重算人物图片与托管证据，返回可加入事务的版本。"""
    project = Path(project).resolve()
    path = project/'state/reference_roles.json'
    version = digest(path)
    record = json.loads(path.read_text(encoding='utf-8'))
    if record.get('project_id') != str(project) or not record.get('characters'):
        raise ValueError('人物参考职责缺少本项目身份')
    names = []
    versions = {path: version}
    for character in record['characters']:
        image = (project/character['path']).resolve()
        image.relative_to(project/'input')
        names.append(character['name'])
        if set(character.get('roles', [])) != {'likeness', 'wardrobe'} or digest(image) != character['sha256']:
            raise ValueError('人物原图或职责已改变')
        versions[image] = character['sha256']
    bindings = record.get('reference_bindings', [])
    if (len(set(names)) != len(names) or len(bindings) != len(names)
            or {r.get('character') for r in bindings} != set(names)
            or any(set(r.get('intents', [])) != {'character_identity', 'wardrobe'} for r in bindings)):
        raise ValueError('人物与正式绑定职责不一致')
    by_name = {c['name']: c for c in record['characters']}
    for binding in bindings:
        if binding['source']['sha256'] != by_name[binding['character']]['sha256']:
            raise ValueError('正式绑定与人物原图不同')
    errors, checked = upload_errors(project, {'reference_bindings': bindings})
    if errors:
        raise ValueError('; '.join(errors))
    versions.update(checked)
    if digest(path) != version:
        raise ValueError('读取期间参考职责改变')
    return record, versions


def migrate_legacy_intent(project):
    """只修正早期登记的职责词汇，不改人物、SHA、托管标识或批准。"""
    project = Path(project).resolve()
    path = project/'state/reference_roles.json'
    before = path.read_bytes()
    version = digest(path)
    record = json.loads(before)
    if record.get('project_id') != str(project):
        raise ValueError('参考职责项目不匹配')
    changed = False
    for binding in record.get('reference_bindings', []):
        intents = set(binding.get('intents', []))
        if intents == {'likeness', 'wardrobe'}:
            binding['intents'] = ['character_identity', 'wardrobe']
            changed = True
        elif intents != {'character_identity', 'wardrobe'}:
            raise ValueError('禁止迁移非身份/服装职责')
    if not changed:
        return require(project)[0]
    errors, versions = upload_errors(project, record)
    if errors:
        raise ValueError('; '.join(errors))
    archive = project/'state/reference_history'/f'{uuid.uuid4().hex}.json'
    atomic_batch({path: json.dumps(record, ensure_ascii=False, indent=2), archive: before},
                 expected={**versions, path: version, archive: None})
    return require(project)[0]


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('project', type=Path)
    parser.add_argument('receipts', type=Path)
    args = parser.parse_args()
    record = register(args.project, args.receipts)
    print(json.dumps({'status': 'registered_not_approved', 'characters': len(record['characters']),
                      'dispatch_allowed': False}, ensure_ascii=False))
