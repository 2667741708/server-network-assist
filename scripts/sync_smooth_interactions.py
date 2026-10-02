"""Embed shared progressive UI helpers in existing assets, keeping CSP/URLs intact."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGETS = [
    (ROOT / 'src/server_network_assist/subscription_admin_ui/subscriptions.js', 'js'),
    (ROOT / 'src/server_network_assist/client_ui/client.js', 'js'),
    (ROOT.parent / 'campus-borrow-client-next/src/pure_next/ui/app.js', 'js'),
    (ROOT / 'src/server_network_assist/subscription_admin_ui/subscriptions.css', 'css'),
    (ROOT / 'src/server_network_assist/client_ui/client.css', 'css'),
    (ROOT.parent / 'campus-borrow-client-next/src/pure_next/ui/app.css', 'css'),
]

def embed(path, kind):
    body = path.read_text(encoding='utf-8')
    prefix = '// ' if kind == 'js' else '/* '
    marker = prefix + 'BEGIN SNA SMOOTH INTERACTIONS'
    end = prefix + 'END SNA SMOOTH INTERACTIONS'
    if marker in body:
        first, last = body.index(marker), body.index(end)
        tail = body.index('\n', last) + 1
        body = body[:first] + body[tail:]
    shared = (ROOT / 'frontend/shared' / ('smooth-interactions.' + kind)).read_text(encoding='utf-8')
    if kind == 'js' and body.startswith("'use strict';\n"):
        body = "'use strict';\n" + shared + body[len("'use strict';\n"):]
    else:
        body = shared + body
    path.write_text(body, encoding='utf-8', newline='\n')

if __name__ == '__main__':
    for path, kind in TARGETS:
        if path.exists():
            embed(path, kind)
            print(path.relative_to(ROOT.parent))
    for name in ['subscriptions.js', 'subscriptions.css', 'dashboard.js']:
        source = ROOT / 'src/server_network_assist/subscription_admin_ui' / name
        (ROOT / 'src/server_network_assist/ui' / name).write_bytes(source.read_bytes())
