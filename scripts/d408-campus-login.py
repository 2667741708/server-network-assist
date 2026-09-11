"""Prompt for a password and invoke netlogin.py through stdin on d408."""
import argparse
import getpass
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

SCRIPT = Path.home() / '.local/share/sna-campus/netlogin.py'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('account')
    parser.add_argument('--service', choices=['0', '1', '2', '3'], default='0')
    parser.add_argument('--stdin', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--background', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    route = json.loads(subprocess.check_output(['ip', '-j', '-4', 'route', 'get', '1.1.1.1']))[0]
    if socket.gethostname() != 'd408-4090' or route.get('dev') != 'enp4s0' or route.get('gateway') != '10.20.32.1':
        raise RuntimeError('Stop shared networking and verify the original physical gateway first.')
    payload = json.load(sys.stdin) if args.stdin else {
        'username': args.account, 'password': getpass.getpass('Campus password: '), 'service': args.service}
    if payload.get('username') != args.account or str(payload.get('service')) != args.service:
        raise ValueError('Account or service mismatch')
    env = {k: v for k, v in os.environ.items() if not k.lower().endswith('_proxy')}
    env.update(NO_PROXY='*', no_proxy='*', PYTHONIOENCODING='utf-8')
    if args.background:
        log = SCRIPT.parent / ('login-result-' + str(time.time_ns()) + '.json')
        fd = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as output:
            child = subprocess.Popen([sys.executable, __file__, args.account, '--service', args.service, '--stdin'],
                stdin=subprocess.PIPE, stdout=output, stderr=output, env=env, start_new_session=True, text=True)
            child.stdin.write(json.dumps(payload))
            child.stdin.close()
        print(json.dumps({'pid': child.pid, 'result': str(log)}))
        return 0
    result = subprocess.run([sys.executable, str(SCRIPT), 'login-stdin'], input=json.dumps(payload),
        capture_output=True, text=True, env=env, timeout=180)
    response = json.loads(result.stdout)
    message = str(response.get('message', '')).replace(payload['password'], '[redacted]')
    if result.returncode or response.get('ok') is not True:
        print(json.dumps({'ok': False, 'message': message}, ensure_ascii=False))
        return 1
    query = subprocess.run([sys.executable, str(SCRIPT), 'current-status', '--json'],
        capture_output=True, text=True, env=env, timeout=60)
    state = json.loads(query.stdout)
    summary = state.get('summary', {})
    service = {'0': '校园网', '1': '中国移动', '2': '中国联通', '3': '中国电信'}[args.service]
    verified = summary.get('online') is True and summary.get('userId') == args.account and summary.get('service') == service
    print(json.dumps({'ok': verified, 'account': summary.get('userId'), 'service': summary.get('service'),
        'ip': summary.get('userIp'), 'internet_online': state.get('internetOnline')}, ensure_ascii=False))
    return 0 if verified else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as error:
        print(json.dumps({'ok': False, 'error': type(error).__name__, 'message': 'Login did not finish; query status before retrying.'}))
        sys.exit(1)
