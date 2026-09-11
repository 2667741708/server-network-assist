#!/usr/bin/env python3
"""Build a fully offline Windows desktop package with pinned dependencies.

Run from a clean CPython 3.13 Windows venv after pip install '.[desktop]'.
This script only copies installed distribution files; it never installs packages.
"""
import argparse
import ast
import hashlib
import importlib.metadata as metadata
import json
from pathlib import Path
import shutil
import sys
import sysconfig
import zipfile

from packaging.requirements import Requirement

ROOT = Path(__file__).resolve().parents[1]


def distributions():
    pending = ['aiohttp==3.14.0', 'asyncssh==2.23.0', 'cryptography==46.0.5',
               'webauthn==2.7.0', 'pystray==0.19.5', 'Pillow==12.3.0']
    seen = {}
    while pending:
        requirement = Requirement(pending.pop())
        if requirement.marker and not requirement.marker.evaluate():
            continue
        dist = metadata.distribution(requirement.name)
        if requirement.specifier and dist.version not in requirement.specifier:
            raise RuntimeError(f'{requirement.name}: expected {requirement.specifier}, installed {dist.version}')
        name = dist.metadata['Name'].lower().replace('_', '-')
        if name in seen:
            continue
        seen[name] = dist
        pending.extend(dist.requires or [])
    return seen


def build(output):
    if sys.platform != 'win32' or sys.version_info[:2] != (3, 13) or sysconfig.get_platform() != 'win-amd64':
        raise RuntimeError('Build using Windows x64 CPython 3.13 to match the embedded runtime')
    output = output.resolve()
    if ROOT / 'artifacts' not in output.parents:
        raise ValueError('Output must be a fresh directory inside project artifacts')
    if output.exists():
        raise ValueError('Output already exists; choose a fresh build directory')
    deps = distributions()  # Validate every dependency before creating output.
    packages = output / 'packages'
    packages.mkdir(parents=True)
    for dist in deps.values():
        for relative in dist.files or []:
            if '..' in relative.parts or relative.suffix == '.pyc' or '__pycache__' in relative.parts:
                continue  # Distribution scripts outside site-packages aren't needed.
            source = Path(dist.locate_file(relative))
            if not source.is_file():
                raise RuntimeError('Missing installed distribution file: ' + str(source))
            target = packages / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    shutil.copytree(ROOT / 'src/server_network_assist', packages / 'server_network_assist',
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    archive = output / 'desktop-packages.zip'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as zipped:
        for path in sorted(packages.rglob('*')):
            if path.is_file():
                zipped.write(path, path.relative_to(packages).as_posix())
    source_tree = ast.parse((ROOT / 'src/server_network_assist/__init__.py').read_text(encoding='utf-8'))
    version = next(ast.literal_eval(node.value) for node in source_tree.body
                   if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == '__version__' for target in node.targets))
    manifest = dict(application_version=version, python='3.13', platform='win-amd64',
                    sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
                    dependencies={name: dist.version for name, dist in sorted(deps.items())})
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    shutil.copy2(ROOT / 'scripts/install_desktop.ps1', output / 'install_desktop.ps1')
    print(json.dumps(dict(archive=str(archive), **manifest)))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    build(parser.parse_args().output)
