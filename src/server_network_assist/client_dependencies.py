"""Read-only Windows prerequisite detection and offline payload validation."""
import hashlib
import json
import os
from pathlib import Path

OFFLINE_FILES = ('dependencies/wireguard-amd64.msi',
                 'dependencies/MicrosoftEdgeWebView2RuntimeInstallerX64.exe')
WEBVIEW_ID = '{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'

def wireguard_directory():
    return Path(os.environ.get('ProgramW6432') or os.environ.get('ProgramFiles', r'C:\Program Files')) / 'WireGuard'

def require_wireguard():
    missing = [name for name in ('wireguard.exe', 'wg.exe') if not (wireguard_directory() / name).is_file()]
    if missing:
        raise ValueError('缺少 WireGuard 入网组件（' + '、'.join(missing) + '）。请退出客户端，完整解压离线安装包并运行 Install.exe；原订阅已保存，无需重新领取。')

def webview_installed():
    import winreg
    subkey = 'Software\\Microsoft\\EdgeUpdate\\Clients\\' + WEBVIEW_ID
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
            try:
                with winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ | view) as key:
                    version = winreg.QueryValueEx(key, 'pv')[0]
                if isinstance(version, str) and any(int(v) for v in version.split('.')):
                    return True
            except (OSError, ValueError, TypeError):
                continue
    return False

def status():
    return {'wireguard': all((wireguard_directory()/name).is_file() for name in ('wireguard.exe','wg.exe')),
            'webview2': webview_installed()}

def verify_offline_payload(bundle):
    bundle = Path(bundle).resolve()
    manifest = json.loads((bundle/'文件校验.json').read_text(encoding='utf-8'))
    if manifest.get('product') != '纯享入网' or manifest.get('contains_user_data') is not False:
        raise ValueError('不是完整的纯享入网分发包。')
    for name in OFFLINE_FILES:
        expected = manifest.get('files',{}).get(name)
        target = (bundle/name).resolve()
        if not expected or not target.is_relative_to(bundle) or not target.is_file():
            raise ValueError('缺少离线依赖，请完整解压安装包：' + name)
        with target.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if digest != expected:
            raise ValueError('离线依赖文件校验失败：' + name)
    return True
