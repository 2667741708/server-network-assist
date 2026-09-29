"""One-shot control-plane synchronization for a Linux customer relay."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import time
import urllib.request
from urllib.parse import urlsplit

from .client_relay import RelayManager


MAX_USAGE_WORKERS = 8
USAGE_TIMEOUT_SECONDS = 8


class RelayControlClient:
    def __init__(self, base_url: str, token: str, opener=None, relay_id: str = ''):
        parts = urlsplit(base_url.rstrip('/'))
        from .client_online import parse_enrollment_url
        parse_enrollment_url(base_url.rstrip('/') + '/#enroll=validation-token-1234')
        self.base = base_url.rstrip('/')
        self.host = parts.hostname
        self.token = token.strip()
        self.relay_id = relay_id
        if len(self.token) < 24:
            raise ValueError('relay token is invalid')
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                raise RuntimeError('control plane redirects are not allowed')
        self.opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def request(self, path: str, value=None, timeout=None):
        if timeout is None:
            # Usage reports have stable IDs and remain pending after a timeout.
            # A slow report must not hold up new policy reconciliation for 20s.
            timeout = USAGE_TIMEOUT_SECONDS if path == '/relay/v1/usage' else 20
        body = json.dumps(value, separators=(',', ':')).encode() if value is not None else None
        request = urllib.request.Request(self.base + path, data=body,
            headers={'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json',
                     'X-Relay-ID': self.relay_id})
        with self.opener.open(request, timeout=timeout) as response:
            final = urlsplit(response.url)
            if final.scheme != urlsplit(self.base).scheme or final.hostname != self.host or final.port != urlsplit(self.base).port:
                raise RuntimeError('control plane redirected to another origin')
            data = response.read(2 * 1024 * 1024 + 1)
        if len(data) > 2 * 1024 * 1024:
            raise RuntimeError('control response is too large')
        result = json.loads(data)
        if not isinstance(result, dict):
            raise RuntimeError('control response is invalid')
        return result


def sync(manager: RelayManager, control: RelayControlClient, cursor_path: Path, default_interface: str = '') -> dict:
    # Enforce locally known expiry before any control-plane or usage request.
    manager.expire()
    try:
        cursor = max(0, int(cursor_path.read_text(encoding='ascii')))
    except (OSError, ValueError):
        cursor = 0
    desired = control.request('/relay/v1/reconcile?since=' + str(cursor))
    policies = desired.get('policies')
    if not isinstance(policies, list):
        raise RuntimeError('control response has no policy list')
    generated_at = None
    validate_snapshot = getattr(manager, 'validate_snapshot', None)
    if validate_snapshot is not None:
        generated_at = validate_snapshot(desired)
    interfaces = {row['interface'] for row in policies if isinstance(row, dict) and isinstance(row.get('interface'), str)}
    if default_interface:
        interfaces.add(default_interface)
    current = manager.status()
    interfaces.update(record['policy']['interface'] for record in current['peers'].values()
                      if record.get('status') == 'active')
    errors = []
    for interface in sorted(interfaces):
        try:
            manager.measure(interface)
        except Exception as exc:
            errors.append(f'measure:{interface}:{type(exc).__name__}')
    prepare_usage = getattr(manager, 'prepare_usage_reports', None)
    if prepare_usage is not None:
        prepare_usage()
    measured = manager.status()
    reports = []
    for peer_id, record in measured['peers'].items():
        pending = record.get('pending_usage')
        if (pending is None and prepare_usage is None and
                record.get('accounted_received') is not None and
                record.get('accounted_sent') is not None):
            # Compatibility with relay managers that do not yet persist an
            # ACK watermark (including the separate Windows relay manager).
            pending = {
                'report_id': f"{peer_id}:{record['accounted_received']}:{record['accounted_sent']}",
                'rx_total': record['accounted_received'],
                'tx_total': record['accounted_sent'],
            }
        # The Linux manager owns its retry record. Rebuilding it from
        # accounted totals after ACK would resend historical peers each sync.
        if not isinstance(pending, dict):
            continue
        reports.append((peer_id, pending))

    # Enforce the fresh signed policy snapshot, including revocations and new
    # peers, before independent usage HTTP waits. RelayManager.apply carries a
    # stable pending report across same-ID policy refresh, and revoked records
    # retain their pending report until the control plane acknowledges it.
    result = manager.reconcile(policies)
    record_snapshot = getattr(manager, 'record_snapshot', None)
    if record_snapshot is not None and generated_at is not None:
        record_snapshot(generated_at)

    def send_usage(report):
        peer_id, pending = report
        try:
            response = control.request('/relay/v1/usage', {'lease_id': peer_id,
                'report_id': pending['report_id'],
                'rx_total': pending['rx_total'], 'tx_total': pending['tx_total']})
            if not isinstance(response.get('usage'), dict):
                raise RuntimeError('usage response is invalid')
            return (peer_id, pending['report_id'], None)
        except Exception as exc:
            return (peer_id, None, type(exc).__name__)

    # Network requests are independent. Keep all manager state changes on this
    # thread, and collect in snapshot order so ACK/errors remain deterministic.
    if len(reports) == 1:
        outcomes = [send_usage(reports[0])]
    elif reports:
        with ThreadPoolExecutor(max_workers=min(MAX_USAGE_WORKERS, len(reports))) as pool:
            outcomes = list(pool.map(send_usage, reports))
    else:
        outcomes = []
    acknowledged = []
    for peer_id, report_id, error in outcomes:
        if error is None:
            acknowledged.append((peer_id, report_id))
        else:
            errors.append(f'usage:{peer_id}:{error}')
    if acknowledged:
        try:
            mark_many = getattr(manager, 'mark_usage_reported_many', None)
            if mark_many is not None:
                mark_many(acknowledged)
            else:
                for peer_id, report_id in acknowledged:
                    manager.mark_usage_reported(peer_id, report_id)
        except Exception as exc:
            # A crash or local persistence error after a server ACK retries
            # the same stable report ID; the control plane deduplicates it.
            errors.append(f'usage_ack:{type(exc).__name__}')
    if errors:
        # Do not advance the cursor while a report is pending.  Reports use a
        # stable id and totals, so retrying is idempotent after a timeout.
        raise RuntimeError('relay synchronization incomplete: ' + ','.join(errors))
    cursor_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = cursor_path.with_suffix('.tmp')
    temporary.write_text(str(int(desired.get('generated_at', time.time()))), encoding='ascii')
    temporary.chmod(0o600)
    temporary.replace(cursor_path)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', type=Path, default=Path('/var/lib/server-network-assist-relay'))
    parser.add_argument('--control-url', required=True)
    parser.add_argument('--token-file', type=Path, required=True)
    parser.add_argument('--interface', default='wg-customer')
    parser.add_argument('--relay-id', required=True)
    args = parser.parse_args()
    token = args.token_file.read_text(encoding='utf-8').strip()
    result = sync(RelayManager(args.data), RelayControlClient(args.control_url, token, relay_id=args.relay_id), args.data / 'cursor', args.interface)
    print(json.dumps({'ok': True, 'peers': len(result['peers'])}))


if __name__ == '__main__':
    main()
