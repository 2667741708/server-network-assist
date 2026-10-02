"""Change only the two explicitly supplied subscriptions; preserve tokens and quota."""
import argparse, json, sqlite3, sys, time
from pathlib import Path
from server_network_assist.client_crypto import secret_digest
from server_network_assist.client_store import ClientStore
from server_network_assist.subscription_dashboard import SubscriptionDashboard
DB=Path('/home/a/.local/share/server-network-assist-commercial/data/commercial-service.sqlite3')
p=argparse.ArgumentParser()
p.add_argument('--apply',action='store_true')
args=p.parse_args()
tokens=json.load(sys.stdin)
assert len(tokens)==2 and len(set(tokens))==2
connection=sqlite3.connect(f'file:{DB}?mode=ro',uri=True)
connection.row_factory=sqlite3.Row
rows=[]
for index,token in enumerate(tokens,1):
    row=connection.execute('SELECT customer_id FROM enrollment_tokens WHERE digest=?',(secret_digest(token,'enrollment'),)).fetchone()
    if not row: raise SystemExit(f'Subscription {index} not found; no changes')
    cid=row['customer_id']
    customer=dict(connection.execute('SELECT * FROM customers WHERE id=?',(cid,)).fetchone())
    plan=dict(connection.execute('SELECT * FROM plans WHERE id=?',(customer['plan_id'],)).fetchone())
    grants=[dict(g) for g in connection.execute('SELECT * FROM line_grants WHERE customer_id=?',(cid,))]
    rows.append({'index':index,'customer':customer,'plan':plan,'grants':grants})
connection.close()
assert len({r['customer']['id'] for r in rows})==2
if args.apply:
    backup=Path('/home/a/subscription-speed-before-'+str(time.time_ns())+'.json')
    backup.write_text(json.dumps(rows,ensure_ascii=False),encoding='utf8')
    backup.chmod(0o600)
    store=ClientStore(DB)
    dashboard=SubscriptionDashboard(store)
    for row in rows:
        cid=row['customer']['id']
        with store._connect() as db:
            customer=dict(db.execute('SELECT * FROM customers WHERE id=?',(cid,)).fetchone()) | dashboard.metadata(db,cid)
            plan=dict(db.execute('SELECT * FROM plans WHERE id=?',(customer['plan_id'],)).fetchone())
            grants=[dict(g) for g in db.execute('SELECT * FROM line_grants WHERE customer_id=?',(cid,))]
            version=db.execute('SELECT COALESCE(MAX(version),0) FROM commercial_service_changes WHERE customer_id=?',(cid,)).fetchone()[0]
        result=dashboard.update(cid,{'expected_version':version,'expected_revision':dashboard.revision(customer,plan,grants),'download_bps':None,'upload_bps':None})
        assert result['revoked_leases']==0 and not result['subscription_address_changed']
        row['change']=result
        row['plan']=store.get_plan(store.customer(cid)['plan_id'])
    print(json.dumps({'backup':str(backup),'changed':True}))
print(json.dumps([{'index':r['index'],'customer_id':r['customer']['id'],'name':r['customer']['display_name'],'download_bps':r['plan']['download_bps'],'upload_bps':r['plan']['upload_bps'],'quota_bytes':r['plan']['quota_bytes'],'grants':[{'id':g['id'],'address':g['allocated_address'],'expires_at':g['expires_at'],'mode':json.loads(g['egress_policy']).get('egress_mode')} for g in r['grants']],'change':r.get('change')} for r in rows],ensure_ascii=False))
