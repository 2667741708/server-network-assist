"""Local owned-tunnel counters. Optional telemetry never controls networking.

Totals are per saved identity on THIS computer, from first measurement onward.
They are not the provider's billable quota and cannot reconstruct old usage.
"""
import datetime as dt
import json
import os
from pathlib import Path
import tempfile
import threading
import time


class ClientTraffic:
    def __init__(self, data):
        self.path = Path(data) / 'client-traffic.json'
        self.lock = threading.Lock()
        self.value = {'schema_version': 1, 'subscriptions': {}}
        self.rates = {}
        self.error = None
        try:
            value = json.loads(self.path.read_text(encoding='utf-8'))
            if value.get('schema_version') != 1 or not isinstance(value.get('subscriptions'), dict):
                raise ValueError('invalid traffic file')
            for row in value['subscriptions'].values():
                if not isinstance(row, dict) or not isinstance(row.get('days'), dict):
                    raise ValueError('invalid traffic row')
                for key in ('received', 'sent', 'last_received', 'last_sent'):
                    if type(row.get(key)) is not int or row[key] < 0:
                        raise ValueError('invalid counter')
                for day in row['days'].values():
                    if any(type(day.get(k)) is not int or day[k] < 0 for k in ('received', 'sent')):
                        raise ValueError('invalid day')
            self.value = value
        except FileNotFoundError:
            pass
        except (OSError, ValueError, AttributeError, TypeError):
            self.error = '流量记录无法读取，保留原文件；网络操作不受影响。'

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        name = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.path.parent,
                                             prefix='traffic-', delete=False) as stream:
                name = stream.name
                json.dump(self.value, stream, ensure_ascii=False)
            if os.name != 'nt':
                os.chmod(name, 0o600)
            os.replace(name, self.path)
        finally:
            if name and os.path.exists(name):
                os.unlink(name)

    def sample(self, identifier, generation, received, sent, *, now=None, monotonic=None):
        if any(type(n) is not int or n < 0 for n in (received, sent)):
            raise ValueError('invalid tunnel counters')
        now = time.time() if now is None else now
        tick = time.monotonic() if monotonic is None else monotonic
        day = dt.datetime.fromtimestamp(now).astimezone().date().isoformat()
        with self.lock:
            if self.error:
                return
            row = self.value['subscriptions'].setdefault(identifier, {
                'received': 0, 'sent': 0, 'last_received': 0, 'last_sent': 0,
                'generation': None, 'days': {}, 'measured_since': int(now)})
            same = row['generation'] == generation
            # A new tunnel or reset starts at zero. A process restart with the
            # same durable generation resumes from persisted counters.
            rx = received - row['last_received'] if same and received >= row['last_received'] else received
            tx = sent - row['last_sent'] if same and sent >= row['last_sent'] else sent
            previous = self.rates.get(identifier)
            elapsed = tick - previous['tick'] if previous and previous['generation'] == generation else 0
            valid = same and received >= row['last_received'] and sent >= row['last_sent'] and 0 < elapsed <= 10
            self.rates[identifier] = {'generation': generation, 'tick': tick,
                'download_bytes_per_second': rx / elapsed if valid else None,
                'upload_bytes_per_second': tx / elapsed if valid else None}
            row['received'] += rx
            row['sent'] += tx
            bucket = row['days'].setdefault(day, {'received': 0, 'sent': 0})
            bucket['received'] += rx
            bucket['sent'] += tx
            row.update(generation=generation, last_received=received, last_sent=sent)
            try:
                self._save()
            except OSError:
                self.error = '流量记录保存失败，当前统计可能无法在重启后保留；网络操作不受影响。'

    def summary(self, identifier, active=False):
        day = dt.datetime.now().astimezone().date().isoformat()
        with self.lock:
            row = self.value['subscriptions'].get(identifier, {})
            today = row.get('days', {}).get(day, {})
            rate = self.rates.get(identifier, {})
            fresh = active and 0 <= time.monotonic() - rate.get('tick', -100) <= 6
            return {'today_bytes': today.get('received', 0) + today.get('sent', 0),
                'total_bytes': row.get('received', 0) + row.get('sent', 0),
                'download_bytes_per_second': rate.get('download_bytes_per_second') if fresh else (None if active else 0),
                'upload_bytes_per_second': rate.get('upload_bytes_per_second') if fresh else (None if active else 0),
                'measured_since': row.get('measured_since'), 'error': self.error,
                'scope': 'local_owned_tunnel', 'day': day}
