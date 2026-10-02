"""Apply narrowly scoped, hash-guarded lease continuity changes with rollback copies."""
import argparse
import ast
import hashlib
import json
import os
from pathlib import Path
import time


def methods(text, class_name):
    tree = ast.parse(text)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == class_name)
    return {n.name: n for n in cls.body if isinstance(n, ast.FunctionDef)}


def segment(text, node):
    start = min([node.lineno] + [d.lineno for d in node.decorator_list]) - 1
    return start, node.end_lineno, ''.join(text.splitlines(keepends=True)[start:node.end_lineno])


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--prepare', type=Path)
    p.add_argument('--payload', type=Path, required=True)
    p.add_argument('--role', choices=['store', 'relay'])
    p.add_argument('--path', type=Path)
    p.add_argument('--expected-hash')
    a = p.parse_args()
    specs = {'store': ('client_store.py', 'ClientStore', ['issue_lease', 'renew_lease']),
             'relay': ('client_relay.py', 'RelayManager', ['apply'])}
    if a.prepare:
        value = {}
        for role, (name, cls, names) in specs.items():
            text = (a.prepare / 'src/server_network_assist' / name).read_text(encoding='utf-8')
            nodes = methods(text, cls)
            value[role] = {name: segment(text, nodes[name])[2] for name in names}
        a.payload.parent.mkdir(parents=True, exist_ok=True)
        a.payload.write_text(json.dumps(value), encoding='utf-8')
        print('Prepared two narrow method patches; no user data included')
        return
    if not a.role or not a.path or not a.expected_hash:
        p.error('Applying requires role, path and expected-hash')
    raw = a.path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != a.expected_hash:
        raise RuntimeError('Production source changed; refuse overwrite')
    text = raw.decode('utf-8')
    _, cls, names = specs[a.role]
    nodes = methods(text, cls)
    updates = json.loads(a.payload.read_text(encoding='utf-8'))[a.role]
    lines = text.splitlines(keepends=True)
    for name in sorted(names, key=lambda n: nodes[n].lineno, reverse=True):
        start, end, _ = segment(text, nodes[name])
        lines[start:end] = [updates[name].rstrip() + '\n']
    candidate = ''.join(lines).encode('utf-8')
    compile(candidate, str(a.path), 'exec')
    info = a.path.stat()
    backup = a.path.with_name(a.path.name + '.continuity-' + str(time.time_ns()) + '.bak')
    backup.write_bytes(raw)
    os.chmod(backup, info.st_mode & 0o777)
    temporary = a.path.with_name(a.path.name + '.continuity-tmp-' + str(os.getpid()))
    try:
        temporary.write_bytes(candidate)
        os.chmod(temporary, info.st_mode & 0o777)
        if hasattr(os, 'chown'):
            os.chown(temporary, info.st_uid, info.st_gid)
            os.chown(backup, info.st_uid, info.st_gid)
        temporary.replace(a.path)
    finally:
        temporary.unlink(missing_ok=True)
    print(json.dumps({'role': a.role, 'path': str(a.path), 'backup': str(backup),
                      'sha256': hashlib.sha256(candidate).hexdigest(), 'network_modified': False}))


if __name__ == '__main__':
    main()
