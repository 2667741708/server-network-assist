"""Verify an offline ZIP, frozen runtimes, and Install.exe self-check only.

Never launch the customer client, vendor installers, or dependency helper.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import zipfile
from PyInstaller.archive.readers import CArchiveReader

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--zip',type=Path,required=True)
parser.add_argument('--report',type=Path,required=True)
args=parser.parse_args()
root=Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix='pure-network-offline-audit-') as directory:
    unpack=Path(directory)
    with zipfile.ZipFile(args.zip) as archive:
        assert archive.testzip() is None
        for name in archive.namelist():
            target=(unpack/name).resolve()
            assert target.is_relative_to(unpack.resolve()),name
        archive.extractall(unpack)
    bundle=unpack/'纯享入网'
    manifest=json.loads((bundle/'文件校验.json').read_text(encoding='utf-8'))
    assert manifest['contains_subscriptions'] is False
    assert manifest['contains_user_data'] is False
    for name,digest in manifest['files'].items():
        target=(bundle/name).resolve()
        assert target.is_relative_to(bundle.resolve())
        assert hashlib.sha256(target.read_bytes()).hexdigest()==digest,name
    assert {p.relative_to(bundle).as_posix() for p in bundle.rglob('*') if p.is_file()}==set(manifest['files'])|{'文件校验.json'}
    client=CArchiveReader(str(bundle/'ServerNetworkAssistClient.exe'))
    keys=[key.replace('\\','/') for key in client.toc]
    required=('python313.dll','Python.Runtime.dll','WebView2Loader.dll','vcruntime140.dll')
    runtime_files={name:[key for key in keys if Path(key).name.lower()==name.lower()] for name in required}
    assert all(runtime_files.values()),runtime_files
    installer=CArchiveReader(str(bundle/'Install.exe'))
    for name in ('customer_install_windows.ps1','customer_dependencies_windows.ps1'):
        assert installer.extract(name)==(root/'scripts'/name).read_bytes(),name
    installer_binary=(bundle/'Install.exe').read_bytes()
    pe=struct.unpack_from('<I',installer_binary,0x3c)[0]
    assert struct.unpack_from('<H',installer_binary,pe+24+68)[0]==2
    report=unpack/'installer-self-check.json'
    subprocess.run([str(bundle/'Install.exe'),'--self-check','--report',str(report)],
        check=True,timeout=90,creationflags=0x08000000 if os.name=='nt' else 0)
    checked=json.loads(report.read_text(encoding='utf-8'))
    assert checked['installation_performed'] is False
    assert checked['network_operations'] is False
    assert checked['offline_dependencies_verified'] is True
    result={'files_verified':len(manifest['files']), 'embedded_runtime_files':runtime_files,
        'installer_gui_subsystem':2, 'installer_self_check':checked,
        'customer_client_executed':False,'vendor_installers_executed':False,
        'dependency_helper_executed':False,'new_windows_installation_tested':False}
    args.report.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))
