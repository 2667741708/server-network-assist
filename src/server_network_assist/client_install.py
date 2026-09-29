"""Per-user, versioned file installation; no service or network operations."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile

PRODUCT = 'PureNetworkClient'

def choose_data_directory(local, legacy):
    """Reuse this user's standard portable config without copying identities."""
    local, legacy = Path(local), Path(legacy)
    names = ('customer-online-service.json', 'customer-online-subscriptions.json')
    if any((local/name).is_file() for name in names):
        return local
    if any((legacy/name).is_file() for name in names):
        return legacy
    return local


def installed_data(executable):
    config = Path(executable).parent / 'client-install.json'
    if not config.exists():
        return None
    value = json.loads(config.read_text(encoding='utf-8'))
    path = Path(value.get('data_directory', ''))
    if value.get('schema_version') != 1 or not path.is_absolute():
        raise ValueError('安装配置无效，请重新安装或使用 --data 指定配置目录。')
    return path


def install_files(bundle, destination, data, *, prepare_data=None):
    bundle, destination, data = (Path(p).resolve() for p in (bundle, destination, data))
    if destination == Path(destination.anchor) or destination == bundle or destination.is_relative_to(bundle):
        raise ValueError('请选择独立的安装目录，不要选择磁盘根目录或解压包内部。')
    manifest = json.loads((bundle / '文件校验.json').read_text(encoding='utf-8'))
    if manifest.get('product') != '纯享入网' or manifest.get('contains_user_data') is not False:
        raise ValueError('不是有效的纯享入网分发包。')
    files = manifest['files']
    if 'ServerNetworkAssistClient.exe' not in files:
        raise ValueError('分发包缺少客户端。')
    allowed = {'ServerNetworkAssistClient.exe', '使用说明.md', 'LICENSE.txt', 'THIRD_PARTY_NOTICES.md'}
    copied = {}
    for name, digest in files.items():
        if name not in allowed and not name.startswith('licenses/'):
            continue  # Installer and helper scripts need not remain installed.
        relative = Path(name)
        source = (bundle / relative).resolve()
        if relative.is_absolute() or '..' in relative.parts or not source.is_relative_to(bundle):
            raise ValueError('分发包包含不安全路径。')
        content = source.read_bytes()
        if hashlib.sha256(content).hexdigest() != digest:
            raise ValueError('文件校验失败，请重新下载完整分发包：' + name)
        copied[name] = content
    digest = files['ServerNetworkAssistClient.exe']
    app = destination / ('app-' + digest[:12])
    # Customer config lives outside versioned app files, preserved on upgrades.
    if data.is_relative_to(app) or app.is_relative_to(data):
        raise ValueError('配置目录和程序目录必须分开。')
    if prepare_data:
        prepare_data(data)
    else:
        data.mkdir(parents=True, exist_ok=True)
        if os.name != 'nt':
            data.chmod(0o700)
    destination.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.install-', dir=destination))
    try:
        for name, content in copied.items():
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        (stage / 'client-install.json').write_text(json.dumps({
            'schema_version': 1, 'product': '纯享入网',
            'data_directory': str(data), 'revision': manifest.get('revision')},
            ensure_ascii=False, indent=2), encoding='utf-8')
        if app.exists():
            # Never overwrite a running EXE or reuse a modified installed payload.
            for name, content in copied.items():
                if (app / name).read_bytes() != content:
                    raise ValueError('现有同版本文件被修改，请选择新的安装目录。')
            if installed_data(app / 'ServerNetworkAssistClient.exe') != data:
                raise ValueError('同版本安装使用不同配置目录，请保留原配置目录。')
        else:
            stage.replace(app)
        return app / 'ServerNetworkAssistClient.exe'
    finally:
        if stage.exists():
            shutil.rmtree(stage)
