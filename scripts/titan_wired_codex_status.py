"""Redacted progress for the requested D321 wired desktop task."""
import json
from pathlib import Path
import re

ROOT = Path('C:/Users/86133/sna-d321-wired-gui-20260917/artifacts')


def redact(value):
    value = re.sub(r'https?://\S+', '[URL omitted]', str(value))
    return re.sub(r'enr_[A-Za-z0-9_-]+', '[token omitted]', value)


events = []
path = ROOT / 'codex-events.jsonl'
if path.exists():
    for line in path.read_text(encoding='utf-8-sig', errors='replace').splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        item = event.get('item') or {}
        entry = {'event': event.get('type'), 'item_type': item.get('type'), 'status': item.get('status')}
        if 'thread_id' in event:
            entry['thread_id'] = event['thread_id']
        if item.get('type') == 'agent_message':
            entry['message'] = redact(item.get('text', ''))[-1600:]
        if item.get('type') == 'mcp_tool_call':
            entry.update(server=item.get('server'), tool=item.get('tool'))
            if item.get('error'):
                entry['error'] = redact(item['error'])[:600]
        events.append(entry)
print(json.dumps({'events': events[-14:], 'files': [p.name for p in ROOT.glob('*')]}, ensure_ascii=True))
for name in ['codex-process.json', 'codex-exit.json', 'TASK_STATUS.md', 'codex-final.md', 'RESULT.json']:
    path = ROOT / name
    if path.exists():
        print(json.dumps({'file': name, 'text': redact(path.read_text(encoding='utf-8-sig', errors='replace'))[-6000:]}, ensure_ascii=True))
