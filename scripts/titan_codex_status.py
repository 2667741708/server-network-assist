"""Summarize the Titan task without replaying command outputs or credentials."""
import json
from pathlib import Path
import re

root = Path(r'C:\Users\86133\sna-wifi-goal-20260917\artifacts')
events = []
for line in (root / 'codex-events.jsonl').read_text(encoding='utf-8-sig', errors='replace').splitlines():
    try:
        event = json.loads(line)
    except ValueError:
        continue
    item = event.get('item') or {}
    entry = {'event': event.get('type'), 'item_type': item.get('type'), 'status': item.get('status')}
    if item.get('type') == 'agent_message':
        text = re.sub(r'https?://\S+', '[URL omitted]', item.get('text', ''))
        entry['message'] = text[:500]
    if item.get('type') == 'mcp_tool_call':
        entry.update(server=item.get('server'), tool=item.get('tool'))
    if 'thread_id' in event:
        entry['thread_id'] = event['thread_id']
    events.append(entry)
print(json.dumps(events[-10:], ensure_ascii=False))
for name in ('codex-exit.json', 'TASK_STATUS.md'):
    path = root / name
    print(json.dumps({'file': name, 'exists': path.is_file()}))
