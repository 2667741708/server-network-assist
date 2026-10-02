"""Exercise deployed candidate against an isolated database, not customer services."""
import base64
import json
from pathlib import Path
import tempfile
import time
from unittest.mock import patch
from cryptography.fernet import Fernet
from server_network_assist.client_store import ClientStore,ClientStoreError
from server_network_assist.subscription_archive import SubscriptionArchive
from server_network_assist.subscription_dashboard import SubscriptionDashboard

with tempfile.TemporaryDirectory(prefix='dashboard-verification-') as temp:
    store=ClientStore(Path(temp)/'isolated.sqlite')
    store.save_source({'id':'test-source','name':'isolated','endpoint':'10.20.32.13:51910','address_pool':'10.77.0.0/24',
        'relay_public_key':base64.b64encode(b'x'*32).decode(),'relay_interface':'wg-test','egress_interface':'eth0','egress_gateway':'10.20.32.1','egress_mode':'physical'})
    archive=SubscriptionArchive(store,Fernet(Fernet.generate_key()));now=int(time.time())
    sub=archive.generate({'name':'isolated customer','quota_gb':10,'source_ids':['test-source'],'base_url':'https://fixture.example'},now=now)
    cid=sub['customer_id'];device=store.enroll_device(sub['url'].split('#enroll=')[1],'signing','fixture',wireguard_public_key='wg-public',now=now+1)
    grant=store.list_grants(cid)[0];lease=store.issue_lease(device['id'],grant['id'],now=now+2)
    store.record_usage_by_lease(lease['id'],'report',100,200,now=now+10)
    dashboard=SubscriptionDashboard(store)
    def row():return next(c for c in dashboard.snapshot(now=now+20)['customers'] if c['id']==cid)
    def edit(**fields):
        c=row();return {'expected_version':c['version'],'expected_revision':c['revision']}|fields
    old=edit();old_plan=store.customer(cid)['plan_id'];store.create_customer('other',old_plan,customer_id='other',now=now+1)
    result=dashboard.update(cid,edit(quota_bytes=None,download_bps=None,upload_bps=None,expires_at=now+180*86400),now=now+20)
    assert not result['subscription_address_changed'] and not result['usage_reset']
    assert archive.view(cid,now=now+20)['url']==sub['url']
    assert store.validate_lease(lease['token'],now=now+21)['quota_bytes'] is None
    assert store.usage(cid,now=now+21)['used_bytes']==300
    assert store.get_plan(old_plan)['quota_bytes']==10*1024**3 and store.customer('other')['plan_id']==old_plan
    assert store.line_grant(grant['id'])['expires_at']==now+180*86400
    assert row()['history'][0]['version']==1
    try:dashboard.update(cid,old|{'display_name':'stale'},now=now+21);raise AssertionError('stale edit accepted')
    except ClientStoreError:pass
    fields={'grants':[{'id':grant['id'],'enabled':True,'egress_mode':'source_proxy'}]}
    with patch('server_network_assist.commercial_proxy.source_is_local',return_value=True),patch('server_network_assist.commercial_proxy.require_proxy',side_effect=ValueError('unready')):
        try:dashboard.update(cid,edit(quota_bytes=20,**fields),now=now+22);raise AssertionError('unready proxy accepted')
        except ValueError:pass
    assert store.get_plan(store.customer(cid)['plan_id'])['quota_bytes'] is None
    with patch('server_network_assist.commercial_proxy.source_is_local',return_value=True),patch('server_network_assist.commercial_proxy.require_proxy',return_value={}):
        result=dashboard.update(cid,edit(**fields),now=now+23)
    assert result['reconnect_required'] and store.list_leases(customer_id=cid)[0]['revoked_at'] is not None
    assert archive.view(cid,now=now+23)['url']==sub['url']
    dashboard.update(cid,edit(expires_at=now+1),now=now+24)
    dashboard.update(cid,edit(expires_at=now+180*86400,grants=[{'id':grant['id'],'enabled':True,'egress_mode':'physical'}]),now=now+25)
    assert store.issue_lease(device['id'],grant['id'],now=now+26)
    dashboard.update(cid,edit(tags=['月租','测试'],notes='isolated notes'),now=now+27)
    assert row()['tags']==['月租','测试']
    for lifecycle in ['archived','deleted']:
        dashboard.update(cid,edit(lifecycle=lifecycle),now=now+28)
        assert not row()['enabled'] and row()['lifecycle']==lifecycle
        try:store.issue_lease(device['id'],grant['id'],now=now+29);raise AssertionError('archived lease accepted')
        except ClientStoreError:pass
    dashboard.update(cid,edit(lifecycle='active'),now=now+30)
    assert not row()['enabled'] and row()['usage']['total_bytes']==300
    dashboard.update(cid,edit(enabled=True),now=now+31)
    assert store.issue_lease(device['id'],grant['id'],now=now+32)
    assert archive.view(cid,now=now+32)['url']==sub['url']
    print(json.dumps({'isolated_runtime_verified':True,'url_preserved':True,'usage_preserved':True,'shared_plan_preserved':True,
        'customer_labels_archive_recycle_restore_verified':True,
        'duration_extended_180_days':True,'proxy_gate_and_reconnect_verified':True,'expired_service_restored_with_existing_identity':True,'live_database_touched':False}))
