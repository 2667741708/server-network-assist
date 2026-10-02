"""Package a source-only snapshot for the explicitly requested Titan Codex task."""
from pathlib import Path
import zipfile

root = Path(__file__).resolve().parents[1]
target = root / 'artifacts' / 'titan-wifi-task-source-20260917.zip'
paths = []
for folder in ('src', 'tests', '借网管理面板'):
    paths.extend((root / folder).rglob('*'))
paths.extend(root / name for name in ('AGENTS.md', 'README.md', 'pyproject.toml'))
paths.extend((root / 'scripts').glob('titan_gui_*.ps1'))
paths.append(root / 'scripts' / 'build_client_exe.py')
with zipfile.ZipFile(target, 'w', zipfile.ZIP_DEFLATED) as archive:
    for path in sorted(set(paths)):
        if not path.is_file() or any(part in ('__pycache__', '.pytest_cache', 'node_modules') for part in path.parts):
            continue
        if path.suffix in ('.pyc', '.pyo'):
            continue
        archive.write(path, path.relative_to(root))
print(f'{target.name}: {target.stat().st_size} bytes')
