from pathlib import Path
import zipfile

root = Path(__file__).resolve().parents[1]
target = root / 'artifacts/titan-exit-source.zip'
with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as z:
    for p in (root / 'src/server_network_assist').rglob('*'):
        if p.is_file() and '__pycache__' not in p.parts:
            z.write(p, p.relative_to(root / 'src'))
    for name in ['titan_prepare_exit_test.ps1']:
        z.write(root / 'scripts' / name, name)
print(target)
