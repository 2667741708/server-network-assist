"""Verify frozen WLAN/physical transport code and UI without executing the EXE."""
import hashlib
import json
from pathlib import Path
import struct
import types
import argparse
from PyInstaller.archive.readers import CArchiveReader

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--exe', type=Path, default=root / 'artifacts/client-wifi-20260917/ServerNetworkAssistClient.exe')
parser.add_argument('--report', type=Path, default=root / 'artifacts/client-wifi-exe-verification-20260917.json')
args = parser.parse_args()
exe = args.exe
archive = CArchiveReader(str(exe))
def resource(name):
    key = next(key for key in archive.toc if key.replace('\\', '/') == name)
    return archive.extract(key)

manifest = json.loads(resource('server_network_assist/client-exe-source-manifest.json'))
for name, expected in manifest['files'].items():
    assert hashlib.sha256((root / name).read_bytes()).hexdigest() == expected, name
ui_assets = ('index.html', 'client.js', 'client.css', 'framework7-bundle.min.js',
             'framework7-bundle.min.css', 'framework7-default-theme.css')
for name in ui_assets:
    assert resource('server_network_assist/client_ui/' + name) == (root / 'src/server_network_assist/client_ui' / name).read_bytes(), name
for name in ('smooth-navigation.js','smooth-navigation.css'):
    assert resource('server_network_assist/subscription_admin_ui/'+name) == (root/'src/server_network_assist/subscription_admin_ui'/name).read_bytes(), name
assert resource('server_network_assist/client_attachment_windows.ps1') == (root / 'src/server_network_assist/client_attachment_windows.ps1').read_bytes()
pyz = archive.open_embedded_archive(next(name for name in archive.toc if name.startswith('PYZ')))
def normalize(code):
    return (code.co_code, code.co_names, code.co_varnames, code.co_flags, code.co_argcount,
            code.co_kwonlyargcount, code.co_freevars, code.co_cellvars,
            tuple(normalize(item) if isinstance(item, types.CodeType) else item for item in code.co_consts))
modules = ('client', 'client_online', 'client_attachment', 'client_lan_transport', 'client_wifi', 'client_campus', 'client_native', 'client_clash_coexist', 'client_tunnel', 'client_native_tray', 'client_dependencies', 'client_install')
for module in modules:
    name = 'server_network_assist.' + module
    frozen = pyz.extract(name)
    source = root / 'src/server_network_assist' / (module + '.py')
    assert normalize(frozen) == normalize(compile(source.read_bytes(), str(source), 'exec')), name
binary = exe.read_bytes()
pe = struct.unpack_from('<I', binary, 0x3c)[0]
subsystem = struct.unpack_from('<H', binary, pe + 24 + 68)[0]
assert subsystem == 2, 'Must be a windowed EXE'
import pefile
pe_image = pefile.PE(str(exe))
icons = set()
for resource_type in pe_image.DIRECTORY_ENTRY_RESOURCE.entries:
    if resource_type.id == 3:
        for identity in resource_type.directory.entries:
            for language in identity.directory.entries:
                entry = language.data.struct
                icons.add(pe_image.get_data(entry.OffsetToData, entry.Size))
ico = (root / 'src/server_network_assist/desktop_ui/icon.ico').read_bytes()
expected_icons = set()
for index in range(struct.unpack_from('<H', ico, 4)[0]):
    length, offset = struct.unpack_from('<II', ico, 6 + 16 * index + 8)
    expected_icons.add(ico[offset:offset + length])
assert icons == expected_icons, 'EXE must contain exactly the brand icon resources'
report = {'sha256': hashlib.sha256(binary).hexdigest(), 'bytes': len(binary),
          'subsystem': subsystem, 'manifest_files_verified': len(manifest['files']),
          'ui_assets_verified': len(ui_assets),
          'physical_and_wifi_modules_verified': len(modules), 'brand_icon_resources_verified': len(icons), 'client_executed': False}
try:
    args.report.write_text(json.dumps(report, indent=2), encoding='utf-8')
except OSError:
    print('Optional EXE verification report could not be written.')
print(json.dumps(report))
