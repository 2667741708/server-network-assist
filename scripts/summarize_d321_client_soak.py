"""Summarize safe connectivity receipts without credentials or network changes."""
import argparse
import json
from pathlib import Path


def intervals(rows, predicate):
    result = []
    begin = None
    for row in rows:
        bad = predicate(row)
        if bad is None:
            continue
        if bad and begin is None:
            begin = row['time']
        elif not bad and begin is not None:
            result.append({'start': begin, 'recovered': row['time'],
                           'observed_seconds': row['time'] - begin})
            begin = None
    if begin is not None:
        result.append({'start': begin, 'recovered': None})
    return result


def summarize(client, source):
    rows = client['samples']
    result = {'complete': client['complete'], 'elapsed': client['elapsed'],
              'start': rows[0]['time'], 'end': rows[-1]['time'], 'checks': {},
              'lease_transitions': client['transitions']}
    for key in ('source', 'campus5080', 'baidu', 'github', 'public_tcp', 'gpt', 'gemini'):
        checked = [r for r in rows if key in r]
        def bad(r):
            if key not in r:
                return None
            v = r[key]
            if isinstance(v, dict):
                # GPT challenges (403) still prove an HTTP response; do not
                # confuse authentication/application status with a transport outage.
                if key in ('gpt', 'gemini'):
                    return not (v.get('ok') and v.get('code', '').isdigit() and 200 <= int(v['code']) < 500)
                return not (v.get('ok') and v.get('code') == '200')
            return not v
        result['checks'][key] = {'count': len(checked),
                                'failures': sum(bad(r) for r in checked),
                                'failure_intervals': intervals(rows, bad)}
        if checked and isinstance(checked[0][key], dict):
            codes = {}
            for row in checked:
                code = row[key].get('code', 'unknown')
                codes[code] = codes.get(code, 0) + 1
            result['checks'][key]['http_codes'] = codes
    states = [r['client'] for r in rows if 'client' in r]
    result['client_state'] = {'count': len(states),
                              'inactive': sum(s.get('active') is False for s in states),
                              'errors': sum(bool(s.get('error') or s.get('api_error')) for s in states)}
    counters = [r['wireguard']['transfer'][0] for r in rows
                if r.get('wireguard', {}).get('transfer')]
    result['client_transfer_resets'] = sum(any(int(b[i]) < int(a[i]) for i in (0, 1))
                                          for a, b in zip(counters, counters[1:]))
    sr = source['samples']
    result['source'] = {'complete': source['complete'], 'count': len(sr),
                        'missing_peer': sum(not r.get('latest-handshakes') for r in sr),
                        'transfer_resets': sum(any(int(b['transfer'][i]) < int(a['transfer'][i]) for i in (0, 1))
                                              for a, b in zip(sr, sr[1:]) if a.get('transfer') and b.get('transfer')),
                        'first': sr[0], 'last': sr[-1]}
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('client', type=Path)
    p.add_argument('source', type=Path)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    value = summarize(json.loads(a.client.read_text()), json.loads(a.source.read_text()))
    a.output.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(value, ensure_ascii=True))
