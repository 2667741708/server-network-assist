"""Package only the verified update EXE; never run or install it."""
import hashlib
import argparse
import json
from pathlib import Path
import zipfile

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument('--artifact',type=Path,default=Path(__file__).resolve().parents[1]/'artifacts/client-campus-1030-20260918-v2')
parser.add_argument('--expected-hash',default='70f19f2e6f830e3a85a060a7bf6cd0b1ed9f4c4e22c3f5a43610517a3404364c')
parser.add_argument('--zip-name',default='pure-network-client-update-20260918.zip')
args=parser.parse_args()
ROOT=args.artifact.resolve()
payload=(ROOT/'ServerNetworkAssistClient.exe').read_bytes()
expected=args.expected_hash
assert hashlib.sha256(payload).hexdigest()==expected
if Path(args.zip_name).name!=args.zip_name or not args.zip_name.endswith('.zip'):
    raise ValueError('Invalid ZIP filename')
destination=ROOT/args.zip_name
with zipfile.ZipFile(destination,'w',zipfile.ZIP_DEFLATED) as archive:
    archive.writestr('ServerNetworkAssistClient.exe',payload)
    archive.writestr('升级说明.txt',('这是最小更新包，不是首次安装包。\n'
        '1. 退出组网并关闭旧客户端。\n2. 完整解压，将新版EXE替换原程序目录里的同名文件。\n'
        '3. 不删除原数据目录，不重新兑换订阅；打开新版，用原订阅开始入网。\n'
        '支持范围以此版本接入策略为准，仍保留物理源网可达、校园认证、热点与IPv6保护。\n'
        '缺少WireGuard/WebView2的首次用户仍需原完整离线安装包。\n'
        '客户端真实校园连接需用户复测；管理员本机没有执行入网测试。\n').encode('utf-8-sig'))
with zipfile.ZipFile(destination) as archive:
    assert archive.testzip() is None
    assert hashlib.sha256(archive.read('ServerNetworkAssistClient.exe')).hexdigest()==expected
print(json.dumps({'zip':str(destination),'bytes':destination.stat().st_size,
    'sha256':hashlib.sha256(destination.read_bytes()).hexdigest(),'client_executed':False}))
