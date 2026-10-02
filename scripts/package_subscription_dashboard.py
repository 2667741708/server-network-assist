"""Emit a transport-only Base64 manifest; no secrets or network operations."""
import base64
import argparse
import json
from pathlib import Path

root=Path(__file__).resolve().parents[1]
src=root/'src/server_network_assist';ui=src/'subscription_admin_ui'
files={n:(src/n).read_bytes() for n in ['subscription_dashboard.py','client_service_admin.py']}
files.update({'ui/'+n:(ui/n).read_bytes() for n in ['subscriptions.html','subscriptions.js','subscriptions.css','dashboard.js','dashboard.css']})
app=(src/'app.py').read_text(encoding='utf8')
for start,end,name in [("        if path == '/api/client-service':","        if path == '/api/clash':",'get-block.txt'),
                       ("        if path == '/api/client-service/action':","        if path == '/api/host/save':",'post-block.txt')]:
    files[name]=app[app.index(start):app.index(end,app.index(start))].encode()
for n in ['deploy_subscription_dashboard.py','deploy_subscription_admin.py','verify_subscription_dashboard_runtime.py','deploy_subscription_dashboard_cli.py','verify_subscription_dashboard_public.py','deploy_subscription_dashboard_styles.py']:
    files[n]=(root/'scripts'/n).read_bytes()
p=argparse.ArgumentParser();p.add_argument('--file',choices=sorted(files));a=p.parse_args()
print(json.dumps({n:base64.b64encode(b).decode() for n,b in files.items() if a.file is None or n==a.file}))
