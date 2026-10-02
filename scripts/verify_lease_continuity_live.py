"""Read-only Windows connectivity sampling; never installs or configures networking."""
import argparse
import json
from pathlib import Path
import socket
import subprocess
import sys
import time


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--report', type=Path, required=True)
    p.add_argument('--seconds', type=int, default=390)
    a = p.parse_args()
    begin = time.monotonic()
    samples = []
    a.report.parent.mkdir(parents=True, exist_ok=True)
    while time.monotonic() - begin < a.seconds:
        row = {'time': int(time.time()), 'elapsed': round(time.monotonic() - begin, 1)}
        try:
            with socket.create_connection(('10.20.32.13', 9182), timeout=2):
                row['source_tcp'] = True
        except OSError:
            row['source_tcp'] = False
        if len(samples) % 3 == 0:
            try:
                r = subprocess.run(['curl.exe' if sys.platform == 'win32' else 'curl', '--noproxy', '*', '--connect-timeout', '2',
                    '--max-time', '4', '-sS', '-o', 'NUL' if sys.platform == 'win32' else '/dev/null', '-w', '%{http_code}',
                    'https://www.baidu.com/'], capture_output=True, text=True,
                    creationflags=0x08000000 if sys.platform == 'win32' else 0, timeout=5)
                row['domestic_https'] = r.returncode == 0 and r.stdout.strip() == '200'
                row['http_code'] = r.stdout.strip()
                row['curl_returncode'] = r.returncode
            except subprocess.TimeoutExpired:
                row['domestic_https'] = False
                row['curl_returncode'] = 'timeout'
        samples.append(row)
        report = {'complete': False, 'network_modified': False, 'samples': samples}
        temporary = a.report.with_suffix('.tmp')
        temporary.write_text(json.dumps(report), encoding='utf-8')
        temporary.replace(a.report)
        time.sleep(1)
    report['complete'] = True
    report['duration_seconds'] = round(time.monotonic() - begin, 1)
    report['source_failures'] = sum(not s['source_tcp'] for s in samples)
    report['https_failures'] = sum(s.get('domestic_https') is False for s in samples)
    a.report.write_text(json.dumps(report), encoding='utf-8')


if __name__ == '__main__':
    main()
