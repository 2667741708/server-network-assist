import base64
import datetime as dt
import json
import io
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet
from server_network_assist.client_store import ClientStore, ClientStoreError
from server_network_assist.subscription_archive import SubscriptionArchive
from server_network_assist.subscription_dashboard import SubscriptionDashboard
from server_network_assist.client_service_admin import run


def test_cli_show_and_in_place_update(service,tmp_path):
    store,archive,dashboard,created,now=service
    out=io.StringIO()
    with patch('server_network_assist.commercial_proxy.proxy_status',return_value={}),patch('server_network_assist.commercial_proxy.source_is_local',return_value=False):
        assert run(['subscription','show','--customer',created['customer_id']],store=store,output=out)==0
    current=json.loads(out.getvalue())['result']
    assert current['id']==created['customer_id'] and 'url' not in current
    fields={'expected_version':current['version'],'expected_revision':current['revision'],'quota_bytes':None,'expires_at':now+180*86400}
    file=tmp_path/'change.json';file.write_text(json.dumps(fields),encoding='utf8');out=io.StringIO()
    assert run(['subscription','update','--customer',created['customer_id'],'--file',str(file)],store=store,output=out)==0
    assert json.loads(out.getvalue())['result']['subscription_address_changed'] is False
    assert archive.view(created['customer_id'])['url']==created['url']
    assert store.get_plan(store.customer(created['customer_id'])['plan_id'])['quota_bytes'] is None


def test_cli_stale_change_fails_without_success_output(service,tmp_path):
    file=tmp_path/'change.json';file.write_text(json.dumps({'expected_version':99,'expected_revision':'stale','quota_bytes':None}),encoding='utf8')
    out=io.StringIO();error=io.StringIO()
    assert run(['subscription','update','--customer',service[3]['customer_id'],'--file',str(file)],store=service[0],output=out,error=error)==2
    assert out.getvalue()=='' and json.loads(error.getvalue())['ok'] is False


@pytest.fixture
def service(tmp_path):
    store=ClientStore(tmp_path/'service.sqlite')
    store.save_source({'id':'source','name':'4090','endpoint':'10.20.32.13:51910',
        'address_pool':'10.77.0.0/24','relay_public_key':base64.b64encode(b'x'*32).decode(),
        'relay_interface':'wg-test','egress_interface':'eth0','egress_gateway':'10.20.32.1','egress_mode':'physical'})
    archive=SubscriptionArchive(store,Fernet(Fernet.generate_key()))
    now=int(dt.datetime.now().replace(hour=0,minute=0,second=0,microsecond=0).timestamp())+100
    created=archive.generate({'name':'客户','quota_gb':10,'source_ids':['source'],'base_url':'https://service.example'},now=now)
    dashboard=SubscriptionDashboard(store)
    return store,archive,dashboard,created,now


def test_customer_labels_and_notes_preserve_live_service(service):
    store,archive,dashboard,created,now=service
    _,_,lease=enroll(service)
    before=archive.view(created['customer_id'],now=now)['url']
    dashboard.update(created['customer_id'],edit(service,tags=[' 月租 ','宿舍','月租'],notes='客户备注\n第二行 <script>文本</script>'),now=now+3)
    c=row(service,now+4)
    assert c['tags']==['月租','宿舍'] and c['notes'].startswith('客户备注\n')
    assert store.validate_lease(lease['token'],now=now+4)
    assert archive.view(created['customer_id'],now=now+4)['url']==before
    assert c['history'][0]['current']['tags']==['月租','宿舍']


@pytest.mark.parametrize('state',['archived','deleted'])
def test_customer_archive_and_recycle_restore_without_reenabling(service,state):
    store,archive,dashboard,created,now=service
    device,grant,lease=enroll(service)
    store.record_usage_by_lease(lease['id'],'first',100,200,now=now+3)
    result=dashboard.update(created['customer_id'],edit(service,lifecycle=state),now=now+4)
    c=row(service,now+5)
    assert c['lifecycle']==state and not c['enabled'] and not c['usable']
    assert result['revoked_leases']==1 and c['usage']['total_bytes']==300
    assert store.device(device['id'])['enabled'] and store.line_grant(grant['id'])['enabled']
    with pytest.raises(ValueError):store.validate_lease(lease['token'],now=now+5)
    with pytest.raises(Exception,match='请先将客户'):store.set_customer_enabled(created['customer_id'],True,now=now+5)
    with pytest.raises(ValueError,match='先从归档'):dashboard.update(created['customer_id'],edit(service,enabled=True),now=now+5)
    dashboard.update(created['customer_id'],edit(service,lifecycle='active'),now=now+6)
    assert not row(service,now+7)['enabled']
    dashboard.update(created['customer_id'],edit(service,enabled=True),now=now+8)
    assert row(service,now+9)['usable']
    assert store.issue_lease(device['id'],grant['id'],now=now+9)
    assert store.usage(created['customer_id'],now=now+9)['used_bytes']==300
    assert archive.view(created['customer_id'],now=now+9)['url']==created['url']


@pytest.mark.parametrize('fields',[
    {'tags':'bad'},{'tags':['x'*31]},{'tags':['bad\ntext']},{'tags':['tag']*21},
    {'notes':'x'*4001},{'notes':'bad\x00text'},{'lifecycle':'invalid'},
])
def test_customer_metadata_rejects_invalid_input_without_changes(service,fields):
    before=row(service)
    with pytest.raises(ClientStoreError):service[2].update(service[3]['customer_id'],edit(service,**fields))
    assert row(service)==before


def test_customer_metadata_conflict_and_other_customer_isolation(service):
    store,_,dashboard,created,now=service
    store.create_customer('其他客户',store.customer(created['customer_id'])['plan_id'],customer_id='other',now=now)
    stale=edit(service,tags=['旧标签'])
    dashboard.update(created['customer_id'],edit(service,tags=['新标签']),now=now+1)
    with pytest.raises(ClientStoreError):dashboard.update(created['customer_id'],stale,now=now+2)
    rows={c['id']:c for c in dashboard.snapshot(now=now+3)['customers']}
    assert rows['other']['tags']==[] and rows['other']['lifecycle']=='active' and rows['other']['enabled']


def row(service, now=None):
    return next(c for c in service[2].snapshot(now=service[4] if now is None else now)['customers'] if c['id']==service[3]['customer_id'])


def edit(service, **fields):
    c=row(service)
    return {'expected_version':c['version'],'expected_revision':c['revision']} | fields


def enroll(service):
    store,archive,dashboard,created,now=service
    token=created['url'].split('#enroll=')[1]
    device=store.enroll_device(token,'signing','device',wireguard_public_key='wg-public',now=now+1)
    grant=store.list_grants(created['customer_id'])[0]
    lease=store.issue_lease(device['id'],grant['id'],now=now+2)
    return device,grant,lease


def test_upgrade_keeps_url_devices_usage_billing_anchor_and_live_lease(service):
    store,archive,dashboard,created,now=service
    device,grant,lease=enroll(service)
    store.record_usage_by_lease(lease['id'],'r1',100,200,now=now+10)
    before=archive.view(created['customer_id'],now=now)['url']
    result=dashboard.update(created['customer_id'],edit(service,quota_bytes=None,download_bps=None,
        upload_bps=None,expires_at=now+180*86400),now=now+20)
    assert result['subscription_address_changed'] is False and result['usage_reset'] is False
    assert archive.view(created['customer_id'],now=now+20)['url']==before
    assert store.device(device['id'])['public_key']=='signing'
    assert store.validate_lease(lease['token'],now=now+21)['quota_bytes'] is None
    assert store.usage(created['customer_id'],now=now+21)['used_bytes']==300
    assert store.usage(created['customer_id'],now=now+21)['period_start']==now
    assert store.line_grant(grant['id'])['expires_at']==now+180*86400
    assert row(service)['history'][0]['version']==1


def test_shared_plan_is_not_modified(service):
    store,archive,dashboard,created,now=service
    original=store.customer(created['customer_id'])['plan_id']
    store.create_customer('其他用户',original,customer_id='other',now=now+1)
    dashboard.update(created['customer_id'],edit(service,quota_bytes=50*1024**3),now=now+2)
    assert store.get_plan(original)['quota_bytes']==10*1024**3
    assert store.customer('other')['plan_id']==original


def test_proxy_gate_and_policy_switch_revoke_only_target_lease(service):
    store,archive,dashboard,created,now=service
    device,grant,lease=enroll(service)
    fields={'grants':[{'id':grant['id'],'enabled':True,'egress_mode':'source_proxy'}]}
    with patch('server_network_assist.commercial_proxy.source_is_local',return_value=True),patch('server_network_assist.commercial_proxy.require_proxy',side_effect=ValueError('未就绪')):
        with pytest.raises(ValueError): dashboard.update(created['customer_id'],edit(service,**fields),now=now+3)
    assert json.loads(store.line_grant(grant['id'])['egress_policy'])['egress_mode']=='physical'
    assert store.validate_lease(lease['token'],now=now+4)
    with patch('server_network_assist.commercial_proxy.source_is_local',return_value=True),patch('server_network_assist.commercial_proxy.require_proxy'):
        result=dashboard.update(created['customer_id'],edit(service,**fields),now=now+5)
    assert result['revoked_leases']==1 and result['reconnect_required']
    assert json.loads(store.line_grant(grant['id'])['egress_policy'])['require_source_proxy']
    with pytest.raises(ValueError):store.validate_lease(lease['token'],now=now+6)


def test_quota_lowering_suspension_and_expiry_preserve_history(service):
    store,archive,dashboard,created,now=service
    _,_,lease=enroll(service)
    store.record_usage_by_lease(lease['id'],'r1',100,200,now=now+3)
    dashboard.update(created['customer_id'],edit(service,quota_bytes=200),now=now+4)
    c=row(service,now+5)
    assert not c['usable'] and c['usage']['total_bytes']==300 and c['usage']['remaining_bytes']==0
    dashboard.update(created['customer_id'],edit(service,enabled=False,expires_at=now),now=now+6)
    assert not row(service,now+7)['usable']
    assert store.usage(created['customer_id'],now=now+7)['used_bytes']==300


def test_expired_service_can_be_extended_without_new_enrollment(service):
    store,archive,dashboard,created,now=service
    device,grant,lease=enroll(service)
    later=now+31*86400
    assert not row(service,later)['usable']
    c=row(service,later)
    dashboard.update(created['customer_id'],{'expected_version':c['version'],'expected_revision':c['revision'],'expires_at':now+180*86400},now=later)
    assert store.issue_lease(device['id'],grant['id'],now=later+1)
    assert archive.view(created['customer_id'],now=later)['url']==created['url']


def test_original_unused_enrollment_can_be_restored_not_rotated(service):
    store,archive,dashboard,created,now=service
    later=now+2*86400
    assert archive.view(created['customer_id'],now=later)['status']=='expired'
    c=row(service,later)
    dashboard.update(created['customer_id'],{'expected_version':c['version'],'expected_revision':c['revision'],'restore_enrollment':True},now=later)
    assert archive.view(created['customer_id'],now=later)['url']==created['url']
    assert archive.view(created['customer_id'],now=later)['status']=='ready'
    store.enroll_device(created['url'].split('#enroll=')[1],'key','test',now=later+1)
    with pytest.raises(ValueError,match='尚未兑换'):
        dashboard.update(created['customer_id'],edit(service,restore_enrollment=True),now=later+2)


def test_concurrent_and_legacy_changes_refuse_stale_form(service):
    store,archive,dashboard,created,now=service
    stale=edit(service,quota_bytes=None)
    store.set_customer_enabled(created['customer_id'],False,now=now+1)
    with pytest.raises(ValueError,match='其他操作'):
        dashboard.update(created['customer_id'],stale,now=now+2)
    fresh=edit(service,enabled=True)
    dashboard.update(created['customer_id'],fresh,now=now+3)
    with pytest.raises(ValueError,match='更新'):
        dashboard.update(created['customer_id'],fresh,now=now+4)


def test_accounting_midnight_rates_and_secret_free_snapshot(service):
    store,archive,dashboard,created,now=service
    _,_,lease=enroll(service)
    midnight=now-100
    store.record_usage_by_lease(lease['id'],'before',100,200,now=midnight-1)
    store.record_usage_by_lease(lease['id'],'after',130,260,now=midnight+1)
    c=row(service,midnight+2)
    assert c['usage']['today_bytes']==90
    assert c['usage']['total_bytes']==390
    assert c['usage']['download_bytes_per_second']==30
    assert c['usage']['upload_bytes_per_second']==15
    assert row(service,now+100)['usage']['measurement_status']=='stale'
    serialized=json.dumps(dashboard.snapshot(now=now))
    for secret in (lease['token'],created['url'],'token_digest','encrypted_url','public_key'):
        assert secret not in serialized


@pytest.mark.parametrize('fields',[{'quota_bytes':True},{'download_bps':0},{'enabled':'false'},{'expires_at':-1},{'max_devices':0},{'shell':'evil'}])
def test_invalid_changes_are_atomic(service,fields):
    with pytest.raises(ValueError):service[2].update(service[3]['customer_id'],edit(service,**fields),now=service[4])
    assert row(service)['version']==0


def test_cross_customer_grant_not_editable_and_no_partial_plan(service):
    store,archive,dashboard,created,now=service
    other=store.generate_monthly_subscription('其他',10,['source'],now=now)
    gid=store.list_grants(other['customer_id'])[0]['id']
    count=len(store.list_plans())
    with pytest.raises(ValueError):dashboard.update(created['customer_id'],edit(service,quota_bytes=None,grants=[{'id':gid,'enabled':True,'egress_mode':'physical'}]),now=now)
    assert len(store.list_plans())==count
