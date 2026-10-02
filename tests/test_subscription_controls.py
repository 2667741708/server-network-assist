import base64
import pytest
from cryptography.fernet import Fernet
from server_network_assist.client_store import ClientStore, ClientStoreError
from server_network_assist.subscription_archive import SubscriptionArchive
from server_network_assist.subscription_dashboard import SubscriptionDashboard

@pytest.fixture
def setup(tmp_path):
    store=ClientStore(tmp_path/'data.sqlite')
    store.save_source({'id':'s','name':'source','endpoint':'10.20.32.13:51910',
        'address_pool':'10.77.0.0/24','relay_public_key':base64.b64encode(b'x'*32).decode(),
        'relay_interface':'wg-test','egress_interface':'eth0','egress_gateway':'10.20.32.1','egress_mode':'physical'})
    return store,SubscriptionArchive(store,Fernet(Fernet.generate_key()))

@pytest.mark.parametrize('days',[30,180,365,None])
def test_custom_service_term_and_independent_bandwidth(setup,days):
    store,archive=setup
    now=1000;expiry=None if days is None else now+days*86400
    created=archive.generate({'name':'custom','quota_gb':None,'source_ids':['s'],'base_url':'https://service.example',
        'expires_at':expiry,'download_bps':None,'upload_bps':12345000},now=now)
    assert created['expires_at']==expiry
    plan=store.get_plan(store.customer(created['customer_id'])['plan_id'])
    assert plan['download_bps'] is None and plan['upload_bps']==12345000
    assert plan['period_seconds']==30*86400
    assert created['enrollment_expires_at']==now+86400
    dev=store.enroll_device(created['url'].split('#enroll=')[1],'key','test',wireguard_public_key='wgkey',now=now+1)
    grant=store.list_grants(created['customer_id'])[0]
    assert grant['expires_at']==expiry
    assert archive.view(created['customer_id'],now=now+2)['url']==created['url']
    future=now+366*86400 if days is None else expiry-1
    lease=store.issue_lease(dev['id'],grant['id'],now=future)
    assert lease['upload_bps']==12345000 and lease['download_bps'] is None
    if expiry is not None:
        assert lease['expires_at']<=expiry
        with pytest.raises(ClientStoreError):store.issue_lease(dev['id'],grant['id'],now=expiry+1)
    else:
        assert lease['expires_at']==2**63-1
        assert store.validate_lease(lease['token'],now=future+100*365*86400)['id']==lease['id']
        dashboard=SubscriptionDashboard(store).snapshot(now=future)
        shown=next(row for row in dashboard['customers'] if row['id']==created['customer_id'])
        assert shown['leases'][0]['expires_at'] is None
        store.set_customer_enabled(created['customer_id'],False,now=future+1)
        with pytest.raises(ClientStoreError):store.validate_lease(lease['token'],now=future+2)

@pytest.mark.parametrize('fields',[
    {'expires_at':1000},{'expires_at':999},{'expires_at':1000+3650*86400+1},
    {'expires_at':True},{'expires_at':'forever'},{'expires_at':1001.5},
    {'download_bps':0},{'upload_bps':-1},{'download_bps':True},
    {'download_bps':float('nan')},{'upload_bps':'10'},{'download_bps':2**53},
])
def test_invalid_generation_rolls_back_before_allocating(setup,fields):
    store,archive=setup
    with pytest.raises(ClientStoreError):
        archive.generate({'name':'invalid','quota_gb':None,'source_ids':['s'],'base_url':'https://service.example'}|fields,now=1000)
    assert not store.list_customers() and not store.list_plans()
    with store._connect() as db:
        assert db.execute('SELECT COUNT(*) FROM enrollment_tokens').fetchone()[0]==0

def test_permanent_upgrade_and_custom_speed_preserve_customer_contract(setup):
    store,archive=setup
    now=1789660000
    created=archive.generate({'name':'original','quota_gb':50,'source_ids':['s'],'base_url':'https://service.example'},now=now)
    device=store.enroll_device(created['url'].split('#enroll=')[1],'key','test',wireguard_public_key='wg',now=now+1)
    grant=store.list_grants(created['customer_id'])[0]
    lease=store.issue_lease(device['id'],grant['id'],now=now+2)
    store.record_usage_by_lease(lease['id'],'first',100,200,now=now+3)
    dashboard=SubscriptionDashboard(store)
    current=dashboard.snapshot(now=now+4)['customers'][0]
    result=dashboard.update(current['id'],{'expected_version':current['version'],'expected_revision':current['revision'],
        'download_bps':None,'upload_bps':None,'expires_at':None},now=now+4)
    assert result['revoked_leases']==0 and not result['subscription_address_changed']
    assert store.line_grant(grant['id'])['expires_at'] is None
    assert archive.view(current['id'],now=now+5)['url']==created['url']
    assert store.validate_lease(lease['token'],now=now+5)['download_bps'] is None
    assert store.usage(current['id'],now=now+5)['used_bytes']==300
    current=dashboard.snapshot(now=now+5)['customers'][0]
    dashboard.update(current['id'],{'expected_version':current['version'],'expected_revision':current['revision'],
        'download_bps':34567000,'upload_bps':123000},now=now+6)
    assert store.validate_lease(lease['token'],now=now+7)['download_bps']==34567000
