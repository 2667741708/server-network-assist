import base64
import json
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from server_network_assist.client_store import ClientStore, ClientStoreError
from server_network_assist.source_management import SourceManagement
from server_network_assist.subscription_archive import SubscriptionArchive


@pytest.fixture
def registry(tmp_path):
    store = ClientStore(tmp_path/'state.sqlite3')
    for i in (1,2):
        store.save_source({'id':f's{i}','name':f'源网{i}','endpoint':f'10.20.32.{i}:51910',
            'address_pool':f'10.214.{i}.0/24','relay_public_key':base64.b64encode(bytes([i])*32).decode(),
            'relay_interface':f'wg-source{i}','egress_interface':'eth0','egress_gateway':f'10.20.32.{i+10}',
            'dns':'223.5.5.5'})
    archive = SubscriptionArchive(store,Fernet(Fernet.generate_key()))
    issued = archive.generate({'name':'客户','source_ids':['s1'],'quota_gb':10,'base_url':'https://entry.example'},now=1000)
    manager = SourceManagement(store)
    return store,manager,archive,issued


def migrate(registry, **changes):
    store,manager,_,issued = registry
    grant = store.list_grants(issued['customer_id'])[0]
    source = next(s for s in store.list_sources() if s['id']=='s2')
    payload = dict(grant_id=grant['id'],source_id='s2',mode='physical',expected_grant=grant,
        expected_source_revision=source['revision'],now=1100)
    payload.update(changes)
    return manager.migrate(**payload)


def test_migration_preserves_url_customer_devices_bill_and_old_relay_ownership(registry):
    store,manager,archive,issued = registry
    device = store.enroll_device(issued['url'].split('#enroll=')[1],'device-key','测试',now=1001)
    old = store.list_grants(issued['customer_id'])[0]
    # A live lease belongs to the old grant; migration must retain its relay attribution.
    with store._connect() as db:
        db.execute('INSERT INTO leases(id,token_digest,customer_id,device_id,grant_id,issued_at,expires_at) VALUES(?,?,?,?,?,?,?)',
            ('lease1','token-digest',issued['customer_id'],device['id'],old['id'],1002,1600))
    before = (store.customer(issued['customer_id']),store.list_devices(),store.list_plans(),store.usage(issued['customer_id'],now=1100))
    result = migrate(registry)
    assert result['reconnect_required'] and result['grant']['endpoint']=='10.20.32.2:51910'
    assert result['grant']['expires_at']==old['expires_at']
    assert before == (store.customer(issued['customer_id']),store.list_devices(),store.list_plans(),store.usage(issued['customer_id'],now=1100))
    assert archive.view(issued['customer_id'],now=1100)['url']==issued['url']
    assert store.list_leases()[0]['revoked_at']==1100
    assert store.lease_relay_id('lease1')=='wg-source1'
    assert store.line_grant(old['id'])['endpoint']==old['endpoint']
    assert json.loads(store.line_grant(old['id'])['egress_policy'])['migration_retired']
    with pytest.raises(ClientStoreError,match='历史'):store.set_grant_enabled(old['id'],True)
    store.record_usage_by_lease('lease1','final',10,20,now=1101)
    assert store.usage(issued['customer_id'],now=1101)['used_bytes']==30


def test_stale_grant_and_source_stop_without_any_write(registry):
    store,_,_,issued = registry
    before = store.list_grants()
    with pytest.raises(ClientStoreError,match='线路已变化'):migrate(registry,expected_grant={})
    with pytest.raises(ClientStoreError,match='配置已变化'):migrate(registry,expected_source_revision='stale')
    assert store.list_grants()==before


def test_disabled_source_cannot_generate_or_migrate_but_existing_customer_survives(registry):
    store,manager,_,issued = registry
    source = store.list_sources()[0]
    manager.set_enabled(source['id'],False,source['revision'])
    assert store.list_grants(issued['customer_id'])[0]['enabled']==1
    with pytest.raises(ClientStoreError,match='停用'):store.generate_monthly_subscription('new',10,[source['id']],now=1100)
    target = next(s for s in store.list_sources() if s['id']=='s2')
    manager.set_enabled('s2',False,target['revision'])
    with pytest.raises(ClientStoreError,match='停用'):migrate(registry)


def test_delete_rejects_live_references_and_preserves_retired_history(registry):
    store,manager,_,issued = registry
    source = next(s for s in store.list_sources() if s['id']=='s1')
    with pytest.raises(ClientStoreError,match='启用'):manager.delete('s1',source['revision'])
    migrate(registry)
    result = manager.delete('s1',source['revision'])
    assert result['history_preserved']
    old = next(s for s in store.list_sources() if s['id']=='s1')
    assert old['archived'] and not old['enabled']
    assert manager.set_enabled('s1',True,old['revision'])['enabled']


def test_unused_source_delete_and_stale_edit(registry):
    store,manager,_,_ = registry
    source = next(s for s in store.list_sources() if s['id']=='s2')
    edited = store.save_source(source|{'name':'更名'},expected_revision=source['revision'])
    with pytest.raises(ClientStoreError,match='配置已变化'):store.save_source(source,expected_revision=source['revision'])
    assert not manager.delete('s2',edited['revision'])['history_preserved']
    assert [s['id'] for s in store.list_sources()]==['s1']


def test_relay_identity_must_not_overlap_between_source_hosts(registry):
    store,_,_,_=registry
    source=next(s for s in store.list_sources() if s['id']=='s2')
    with pytest.raises(ClientStoreError,match='交叉下发'):
        store.save_source(source|{'relay_interface':'wg-source1'},expected_revision=source['revision'])
    assert next(s for s in store.list_sources() if s['id']=='s2')['relay_interface']=='wg-source2'


def test_proxy_unmanaged_target_and_pool_exhaustion_are_atomic(registry):
    store,_,_,_ = registry
    before = store.list_grants()
    with pytest.raises(ClientStoreError,match='状态检测'):migrate(registry,mode='source_proxy')
    target = next(s for s in store.list_sources() if s['id']=='s2')
    store.save_source(target|{'address_pool':'10.214.2.0/29'},expected_revision=target['revision'])
    customer = store.list_customers()[0]['id']
    for i in range(2,7):
        store.grant_line(customer,f'占用{i}','online',target['endpoint'],allocated_address=f'10.214.2.{i}/32',egress_mode='physical',
            egress_interface='eth0',egress_gateway='10.20.32.12',allowed_ips='0.0.0.0/0',now=1000)
    before = store.list_grants()
    with pytest.raises(ClientStoreError,match='已满'):migrate(registry)
    assert before==store.list_grants()
