"""Merge sanitized acceptance evidence without relabeling earlier test artifacts."""
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
new = json.loads((root / 'artifacts/client-guard-acceptance.json').read_text(encoding='utf8'))
path = root / '借网管理面板/handoffs/2026-09-16-Titan退网安全验收.json'
data = json.loads(path.read_text(encoding='utf8'))
previous = data.get('prior_release_sha256', data['final_sha256'])
data.setdefault('release_history', {})[data['final_sha256']] = {
    'tests': data['tests'], 'records': data.get('latest_guard_records', {})}
data['prior_release_sha256'] = previous
data['final_sha256'] = new['sha256']
data['final_bytes'] = new['size']
data['tests'] = new['tests']
data['latest_guard_records'] = new['records']
data['matrix_artifact']['physical_link_loss_and_personal_login_api'] = previous
data['matrix_artifact']['latest_guard'] = new['sha256']
data['audit']['sha256'] = new['sha256']
data['audit']['remaining_P2'] = list(dict.fromkeys(data['audit']['remaining_P2'] + [
    'Permanent driver disk or registry failure cannot guarantee completed recovery',
    'Client and guard share executable and dependencies',
    'Backend startup error may retain local API until modal is dismissed',
]))
path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
for name in ('命令大全.md', '借网管理面板/test_reference.md'):
    p = root / name
    text = p.read_text(encoding='utf8').replace('65项回归', str(new['tests']) + '项回归').replace('76项回归', str(new['tests']) + '项回归')
    if name.endswith('test_reference.md'):
        text = text.replace('最终EXE重复实测。', 'd57042制品重复实测；最终596c82制品另补验崩溃恢复，原实验哈希保留。')
    p.write_text(text, encoding='utf8')
print('Sanitized acceptance evidence saved')
