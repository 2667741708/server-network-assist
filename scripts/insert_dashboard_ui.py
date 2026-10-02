from pathlib import Path
root=Path(__file__).resolve().parents[1]/'src/server_network_assist/subscription_admin_ui'
page=root/'subscriptions.html'
content=page.read_text(encoding='utf-8')
anchor='<div id="manager" hidden>'
if 'id="dashboard-panel"' not in content:
    if content.count(anchor)!=1: raise ValueError('Admin page anchor changed')
    content=content.replace(anchor,(root/'dashboard.html').read_text(encoding='utf-8')+'\n'+anchor,1)
    content=content.replace('<script defer src="subscriptions.js"></script>','<script defer src="subscriptions.js"></script><script defer src="dashboard.js"></script>')
if 'href="dashboard.css"' not in content:
    content=content.replace('<link rel="stylesheet" href="subscriptions.css">','<link rel="stylesheet" href="subscriptions.css"><link rel="stylesheet" href="dashboard.css">')
page.write_text(content,encoding='utf-8')
