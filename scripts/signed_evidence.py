"""核验用户显式配置的外部Ed25519验收者；不信任文档自己携带的公钥。"""
import base64
import json
import os
from pathlib import Path


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf8')


def verify(envelope, purpose):
    # 信任目录由启动者提供，不能由待验项目或画像指定。
    path = os.environ.get('MV_REVIEW_TRUST_STORE')
    if not path:
        raise ValueError('缺少MV_REVIEW_TRUST_STORE外部验收者信任目录')
    raw = Path(path).read_bytes()
    store = json.loads(raw)
    key = store['reviewers'][envelope['key_id']]
    if (not isinstance(key.get('purposes'), list) or purpose not in key['purposes']
            or key.get('revoked') is True):
        raise ValueError('验收者没有该用途授权或已撤销')
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    public = Ed25519PublicKey.from_public_bytes(base64.b64decode(key['public_key'], validate=True))
    public.verify(base64.b64decode(envelope['signature'], validate=True), canonical(envelope['payload']))
    if envelope['payload'].get('purpose') != purpose:
        raise ValueError('签名用途不符')
    if Path(path).read_bytes() != raw:
        raise ValueError('验收期间信任目录已变化')
    return envelope['payload']


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=('sign','verify'))
    parser.add_argument('input',type=Path)
    parser.add_argument('--purpose',choices=('film_corpus','lip_measure','workflow_step'))
    parser.add_argument('--private-key',type=Path)
    parser.add_argument('--key-id')
    parser.add_argument('--output',type=Path)
    args = parser.parse_args()
    value = json.loads(args.input.read_text(encoding='utf8'))
    if args.action == 'verify':
        if not args.purpose: parser.error('verify需要--purpose')
        verify(value,args.purpose)
        print('签名与验收用途核验通过；签名不自动证明实际观看或标注正确')
    else:
        if not args.private_key or not args.key_id or not args.output: parser.error('sign需要--private-key、--key-id和--output')
        from cryptography.hazmat.primitives.serialization import load_pem_private_key
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        key = load_pem_private_key(args.private_key.read_bytes(),password=None)
        if not isinstance(key,Ed25519PrivateKey): raise ValueError('须使用Ed25519私钥')
        envelope = {'key_id':args.key_id,'payload':value,'signature':base64.b64encode(key.sign(canonical(value))).decode('ascii')}
        with args.output.open('x',encoding='utf8') as handle:
            json.dump(envelope,handle,ensure_ascii=False,indent=2)
