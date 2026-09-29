"""Best-effort, bounded, redacted diagnostics. Never persist request bodies."""
from __future__ import annotations

import contextlib
import contextvars
from datetime import datetime, timezone
from functools import wraps
import inspect
import json
import os
from pathlib import Path
import re
import threading
import time
import traceback
import uuid

CORRELATION = contextvars.ContextVar('diagnostic_operation', default=None)
_SENSITIVE = re.compile(r'password|passwd|secret|token|private.?key|public.?key|signature|authorization|cookie|nonce|username|account_name|enroll|csrf', re.I)
_ASSIGNMENT = re.compile(r'(?i)((?:password|passwd|secret|token|private.?key|signature|authorization|cookie|username|csrf)[\w-]*[\s\"\x27]*[:=][\s\"\x27]*)([^\s,;}\"\x27]+)')
_OPAQUE = re.compile(r'(?<![\w-])(?:enr_[A-Za-z0-9_-]+|[A-Za-z0-9_+/=-]{43,})(?![\w-])')

def scrub(value, depth=0):
    if depth > 8:
        return '[depth limit]'
    if isinstance(value, dict):
        return {str(k)[:100]: '[redacted]' if _SENSITIVE.search(str(k)) else scrub(v, depth + 1)
                for k, v in list(value.items())[:100]}
    if isinstance(value, (list, tuple)):
        return [scrub(v, depth + 1) for v in value[:100]]
    if isinstance(value, str):
        value = re.sub(r'-----BEGIN [^-]*PRIVATE KEY-----[\s\S]*?-----END [^-]*PRIVATE KEY-----', '[private key redacted]', value)
        value = re.sub(r'(?i)((?:password|passwd|secret|token|private.?key|signature|authorization|cookie|username|csrf)[\w-]*\s*[:=]\s*)([\"\x27])[^\"\x27]*[\"\x27]', r'\1[redacted]', value)
        value = re.sub(r'(?i)Bearer\s+[^\s\"\x27]+', 'Bearer [redacted]', value)
        value = re.sub(r'(?i)(https?://)[^/\s@]+@', r'\1[redacted]@', value)
        value = re.sub(r'(?i)(https?://[^\s?#\"\x27]+)[?#][^\s\"\x27]*', r'\1[parameters redacted]', value)
        return _OPAQUE.sub('[redacted]', _ASSIGNMENT.sub(r'\1[redacted]', value))[:8192]
    if value is None or isinstance(value, (int, float, bool)):
        return value
    return '<' + type(value).__name__ + '>'

def exception_details(exc):
    chain, seen = [], set()
    while exc is not None and id(exc) not in seen and len(chain) < 4:
        seen.add(id(exc))
        chain.append({'type': type(exc).__name__, 'message': str(exc),
                      'errno': getattr(exc, 'errno', None), 'winerror': getattr(exc, 'winerror', None),
                      'frames': [{'file': Path(f.filename).name, 'line': f.lineno, 'function': f.name}
                                 for f in traceback.extract_tb(exc.__traceback__)[-20:]]})
        exc = exc.__cause__ or (None if exc.__suppress_context__ else exc.__context__)
    return chain

class Diagnostics:
    def __init__(self, directory, component='client', max_bytes=2 * 1024 * 1024, backups=3, retain_runs=5):
        self.directory = Path(directory)
        self.component, self.max_bytes, self.backups = component, max_bytes, backups
        self.lock, self.dropped = threading.RLock(), 0
        self.path = self.directory / (component + '-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f') + '-' + str(os.getpid()) + '-' + uuid.uuid4().hex[:8] + '.jsonl')
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            if os.name != 'nt':
                self.directory.chmod(0o700)
            # Each component has five process/run groups, each at most 8 MiB.
            groups = sorted(self.directory.glob(component + '-*.jsonl'), key=lambda p: p.stat().st_mtime, reverse=True)
            for old in groups[max(0, retain_runs - 1):]:
                for candidate in [old, *[Path(str(old) + '.' + str(n)) for n in range(1, backups + 1)]]:
                    with contextlib.suppress(OSError):
                        candidate.unlink()
        except Exception:
            self.dropped += 1

    def emit(self, event, level='info', error=None, **fields):
        try:
            record = scrub({'time': datetime.now(timezone.utc).isoformat(timespec='milliseconds'),
                            'level': level, 'component': self.component, 'pid': os.getpid(),
                            'thread': threading.current_thread().name, 'operation_id': CORRELATION.get(),
                            'event': event, **fields, **({'exception': exception_details(error)} if error else {})})
            encoded = (json.dumps(record, ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8')
            if len(encoded) > self.max_bytes:
                encoded = (json.dumps({k: v for k, v in record.items() if k not in fields} | {'detail': '[record size limit]'}) + '\n').encode()
            with self.lock:
                if self.path.exists() and self.path.stat().st_size + len(encoded) > self.max_bytes:
                    for n in range(self.backups, 0, -1):
                        source = self.path if n == 1 else Path(str(self.path) + '.' + str(n - 1))
                        destination = Path(str(self.path) + '.' + str(n))
                        if source.exists():
                            os.replace(source, destination)
                with self.path.open('ab') as stream:
                    if os.name != 'nt':
                        os.chmod(self.path, 0o600)
                    stream.write(encoded)
        except Exception:
            self.dropped += 1

    @contextlib.contextmanager
    def operation(self, name, identifier=None, **fields):
        binding = CORRELATION.set(identifier or CORRELATION.get() or uuid.uuid4().hex)
        started = time.monotonic()
        self.emit(name + '.start', **fields)
        try:
            yield
        except Exception as exc:
            self.emit(name + '.failed', level='error', error=exc, elapsed_ms=round((time.monotonic() - started) * 1000), **fields)
            raise
        else:
            self.emit(name + '.complete', elapsed_ms=round((time.monotonic() - started) * 1000), **fields)
        finally:
            CORRELATION.reset(binding)

_LOGGERS, _LOCK = {}, threading.Lock()
def get_diagnostics(data, component='client'):
    key = (str(Path(data).absolute()), component)
    with _LOCK:
        if key not in _LOGGERS:
            _LOGGERS[key] = Diagnostics(Path(data) / 'logs', component)
        return _LOGGERS[key]

def instrument(cls):
    """Wrap public backend phases without logging arguments or credentials."""
    for name, method in list(vars(cls).items()):
        if name.startswith('_') or not inspect.isfunction(method):
            continue
        if cls.__name__ == 'ClientStore' and name not in {
                'create_customer', 'issue_lease', 'renew_lease', 'release_lease',
                'record_usage_by_lease', 'revoke', 'enroll_device', 'grant_line', 'set_grant_egress',
                'set_customer_enabled', 'set_device_enabled', 'set_grant_enabled',
                'create_enrollment_token', 'save_source', 'delete_source'}:
            continue
        def wrap(method, name):
            @wraps(method)
            def tracked(self, *args, **kwargs):
                if not hasattr(self, 'diagnostics'):
                    return method(self, *args, **kwargs)
                with self.diagnostics.operation(type(self).__name__ + '.' + name):
                    result = method(self, *args, **kwargs)
                    self.diagnostics.emit('phase.result', phase=name, result='[redacted]' if _SENSITIVE.search(name) else result)
                    return result
            return tracked
        setattr(cls, name, wrap(method, name))
    return cls
