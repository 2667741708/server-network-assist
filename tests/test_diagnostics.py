import json
from concurrent.futures import ThreadPoolExecutor
import pytest
from server_network_assist.diagnostics import Diagnostics, scrub

def rows(log):
    return [json.loads(line) for file in log.directory.glob('*.jsonl*') for line in file.read_text(encoding='utf-8').splitlines()]

def test_secrets_are_removed_from_nested_fields_urls_errors(tmp_path):
    log = Diagnostics(tmp_path)
    secret = 'sensitive-value'
    log.emit('test', nested={'password': secret, 'wireguard_private_key': secret, 'token': secret},
             url='https://example.test/#enroll=enr_sensitive-value',
             error=ValueError('password=sensitive-value Authorization=Bearer sensitive-value'))
    text = log.path.read_text(encoding='utf-8')
    assert secret not in text
    assert '[redacted]' in text

def test_exception_operation_correlation_and_original_failure(tmp_path):
    log = Diagnostics(tmp_path)
    error = OSError(10049, 'invalid interface address')
    with pytest.raises(OSError) as caught:
        with log.operation('connect', identifier='correlation-1'):
            raise error
    assert caught.value is error
    items = rows(log)
    assert all(r['operation_id'] == 'correlation-1' for r in items)
    failure = items[-1]
    assert failure['event'] == 'connect.failed'
    assert failure['exception'][0]['errno'] == 10049
    assert failure['exception'][0]['frames']

def test_rotation_concurrent_json_lines_and_bounds(tmp_path):
    log = Diagnostics(tmp_path, max_bytes=2048, backups=2)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda n: log.emit('sample', number=n, detail='x ' * 90), range(100)))
    assert rows(log)
    assert len(list(tmp_path.glob('*.jsonl*'))) <= 3
    assert all(p.stat().st_size <= 2048 for p in tmp_path.glob('*.jsonl*'))

def test_filesystem_failure_never_blocks_primary_operation(tmp_path):
    blocked = tmp_path / 'not-a-directory'
    blocked.write_text('file')
    log = Diagnostics(blocked)
    with log.operation('login'):
        log.emit('login.result', result={'online': True})
    assert log.dropped >= 3

def test_run_names_unique_and_retention(tmp_path):
    paths = []
    for _ in range(8):
        log = Diagnostics(tmp_path, retain_runs=3)
        log.emit('start')
        paths.append(log.path)
    assert len(set(paths)) == 8
    assert len(list(tmp_path.glob('*.jsonl'))) == 3
