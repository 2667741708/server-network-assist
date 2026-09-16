"""Offline administrator utility for creating customer subscription envelopes."""
from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .client_subscription import _canonical, validate_payload


def encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip('=')


def keygen(path: Path) -> str:
    if path.exists():
        raise ValueError('私钥文件已经存在')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    private = Ed25519PrivateKey.generate()
    raw = private.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                serialization.NoEncryption())
    path.write_bytes(raw)
    if __import__('os').name != 'nt':
        path.chmod(0o600)
    public = private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return encode(public)


def sign(private_path: Path, payload_path: Path, output: Path) -> None:
    private = Ed25519PrivateKey.from_private_bytes(private_path.read_bytes())
    payload = validate_payload(json.loads(payload_path.read_text(encoding='utf-8')))
    envelope = {'payload': payload, 'signature': encode(private.sign(_canonical(payload)))}
    temporary = output.with_suffix(output.suffix + '.tmp')
    temporary.write_text(json.dumps(envelope, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    temporary.replace(output)


def main():
    parser = argparse.ArgumentParser(description='Create signed customer subscriptions')
    sub = parser.add_subparsers(dest='command', required=True)
    create = sub.add_parser('keygen')
    create.add_argument('--private-key', type=Path, required=True)
    issue = sub.add_parser('sign')
    issue.add_argument('--private-key', type=Path, required=True)
    issue.add_argument('--payload', type=Path, required=True)
    issue.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'keygen':
        print(json.dumps({'public_key': keygen(args.private_key)}, ensure_ascii=False))
    else:
        sign(args.private_key, args.payload, args.output)
        print(json.dumps({'ok': True, 'output': str(args.output)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
