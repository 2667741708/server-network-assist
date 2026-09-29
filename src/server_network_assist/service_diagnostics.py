"""Passive service/journal capture; no proxy, route or login changes."""
import contextlib
import os
import subprocess
import threading
import time
from uuid import uuid4
from aiohttp import web
from .diagnostics import CORRELATION, get_diagnostics

def attach(app, data):
    log = get_diagnostics(data, 'server')
    stop = threading.Event()

    @web.middleware
    async def diagnostic_request(request, handler):
        operation_id = uuid4().hex
        request['diagnostics'] = log
        binding = CORRELATION.set(operation_id)
        started = time.monotonic()
        log.emit('http.start', method=request.method, path=request.path, remote=request.remote)
        try:
            response = await handler(request)
            log.emit('http.complete', method=request.method, path=request.path, status=response.status,
                     elapsed_ms=round((time.monotonic() - started) * 1000),
                     level='warning' if response.status >= 400 else 'info')
            return response
        except Exception as exc:
            log.emit('http.failed', method=request.method, path=request.path, error=exc, level='error')
            raise
        finally:
            CORRELATION.reset(binding)

    app.middlewares.insert(0, diagnostic_request)

    def collect():
        while not stop.is_set():
            for unit in ('mihomo.service', 'wg-quick@sna-commercial.service', 'server-network-assist-relay.service'):
                if stop.is_set():
                    break
                try:
                    state = subprocess.run(['systemctl', 'show', unit, '--property=ActiveState,SubState,MainPID,Result,NRestarts'],
                                           capture_output=True, text=True, timeout=5)
                    log.emit('service.state', unit=unit, returncode=state.returncode, stdout=state.stdout, stderr=state.stderr)
                    journal = subprocess.run(['journalctl', '-u', unit, '--since', '35 seconds ago',
                                              '-n', '50', '--no-pager', '--output=cat'],
                                             capture_output=True, text=True, timeout=5)
                    log.emit('service.journal', unit=unit, returncode=journal.returncode, stdout=journal.stdout, stderr=journal.stderr)
                except Exception as exc:
                    log.emit('service.capture_failed', unit=unit, level='warning', error=exc)
            stop.wait(30)

    async def start(_app):
        log.emit('server.start', passive_service_capture=os.name == 'posix')
        if os.name == 'posix':
            try:
                threading.Thread(target=collect, name='service-diagnostics', daemon=True).start()
            except Exception as exc:
                log.emit('service.capture_start_failed', level='warning', error=exc)

    async def close(_app):
        stop.set()
        log.emit('server.stop')

    app.on_startup.append(start)
    app.on_cleanup.append(close)
    return log
