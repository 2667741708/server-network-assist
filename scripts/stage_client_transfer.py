"""Split a built EXE for bounded managed uploads; never execute it."""
import hashlib
import json
import argparse
from pathlib import Path

root = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser()
parser.add_argument('--artifact', type=Path, default=root / 'artifacts/client-url-code-20260917')
artifact = parser.parse_args().artifact.resolve()
source = artifact / 'ServerNetworkAssistClient.exe'
stage = Path('C:/Users/hmw20/AppData/Local/OpenAI/Codex/bin/12219cbfbcbddde7')
contents = source.read_bytes()
parts = []
for index, start in enumerate(range(0, len(contents), 1024 * 1024)):
    name = f'sna-url-code-part-{index:03d}.bin'
    payload = contents[start:start + 1024 * 1024]
    (stage / name).write_bytes(payload)
    parts.append({'name': name, 'sha256': hashlib.sha256(payload).hexdigest(), 'size': len(payload)})
manifest = {'sha256': hashlib.sha256(contents).hexdigest(), 'size': len(contents), 'parts': parts}
(artifact / 'transfer.json').write_text(json.dumps(manifest), encoding='utf-8')
print(json.dumps(manifest))
