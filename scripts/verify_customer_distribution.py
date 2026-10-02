"""Verify ZIP, windowless PE files and frozen installer resources; no install/network."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import zipfile

parser = argparse.ArgumentParser()
parser.add_argument('archive', type=Path)
args = parser.parse_args()
archive_path = args.archive.resolve()
report_path = archive_path.parent / 'installer-runtime-verification.json'
with tempfile.TemporaryDirectory(prefix='sna-installer-check-', dir=archive_path.parent) as directory:
    stage = Path(directory)
    with zipfile.ZipFile(archive_path) as archive:
        if archive.testzip() is not None:
            raise ValueError('Corrupt ZIP')
        for entry in archive.infolist():
            target = (stage / entry.filename).resolve()
            if not target.is_relative_to(stage.resolve()):
                raise ValueError('Unsafe ZIP entry')
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(entry))
    bundle = stage / '纯享入网'
    manifest = json.loads((bundle / '文件校验.json').read_text(encoding='utf-8'))
    for name in ('Install.exe', 'ServerNetworkAssistClient.exe'):
        content = (bundle / name).read_bytes()
        offset = struct.unpack_from('<I', content, 0x3c)[0]
        if content[:2] != b'MZ' or content[offset:offset + 4] != b'PE\0\0':
            raise ValueError('Invalid Windows EXE')
        subsystem = struct.unpack_from('<H', content, offset + 24 + 68)[0]
        if subsystem != 2:
            raise ValueError('EXE must not open a console window')
        if hashlib.sha256(content).hexdigest() != manifest['files'][name]:
            raise ValueError('EXE hash mismatch')
    subprocess.run([str(bundle / 'Install.exe'), '--self-check', '--report', str(report_path)],
                   check=True, timeout=30, creationflags=0x08000000 if os.name == 'nt' else 0)
    result = json.loads(report_path.read_text(encoding='utf-8'))
    assert result['bundle_verified'] and result['helper_present'] and result['tk_dll_present']
    assert result['network_operations'] is False and result['installation_performed'] is False
    print(json.dumps(result))
