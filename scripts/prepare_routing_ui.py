"""Build a snapshot with routing changes without deploying unrelated WIP UI."""
from pathlib import Path
import shutil
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
destination = root / '.routing-ui-build'
if destination.exists():
    raise SystemExit('Snapshot already exists; use its existing contents or choose a new reviewed path')
shutil.copytree(root / 'frontend', destination,
                ignore=shutil.ignore_patterns('node_modules', 'dist', '.angular'))
for name in ('app.ts', 'app.html'):
    result = subprocess.run(['git', 'show', f'HEAD:frontend/src/app/{name}'],
                            cwd=root, check=True, capture_output=True)
    text = result.stdout.decode('utf-8')
    if name == 'app.ts':
        text = text.replace('post<{ results: ProbeResult[] }>', 'post<{ results: ProbeResult[]; profiles: NetworkProfile[] }>')
        text = text.replace('next: (value) => this.probes.set(value.results),',
                            'next: (value) => { this.probes.set(value.results); this.profiles.set(value.profiles); },')
    (destination / 'src' / 'app' / name).write_text(text, encoding='utf-8')
print(destination)
