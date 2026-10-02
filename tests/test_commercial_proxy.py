import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from server_network_assist import commercial_proxy as proxy
from server_network_assist.client_store import ClientStore, ClientStoreError
from server_network_assist.client_service_api import ClientServiceAPI
from server_network_assist.client_service_admin import build_parser


def source(store):
    return store.save_source(dict(name='source',endpoint='10.20.32.13:51910',
        address_pool='10.213.40.0/24',relay_public_key='A'*43+'=',relay_interface='sna-commercial',
        egress_interface='eth0',egress_gateway='10.20.32.1'))


def test_creation_is_physical_even_when_proxy_is_running(tmp_path):
    store=ClientStore(tmp_path/'db');node=source(store)
    with patch.object(proxy,'require_proxy',side_effect=AssertionError('must not enable source proxy')):
        result=store.generate_monthly_subscription('default',10,[node['id']])
    grant=store.list_grants(result['customer_id'])[0]
    assert node['egress_mode']=='physical'
    assert json.loads(grant['egress_policy'])['egress_mode']=='physical'
    assert grant['egress_interface']=='eth0'


def test_unready_proxy_opt_in_rolls_back_entire_subscription(tmp_path):
    store=ClientStore(tmp_path/'db');node=source(store)
    with patch.object(proxy,'source_is_local',return_value=True),patch.object(proxy,'require_proxy',side_effect=ValueError('not ready')):
        with pytest.raises(ValueError,match='not ready'):
            store.generate_monthly_subscription('proxy',10,[node['id']],proxy_source_ids=[node['id']])
    assert store.list_customers()==[]
    assert store.list_grants()==[]


def test_only_selected_subscription_uses_ready_proxy(tmp_path):
    store=ClientStore(tmp_path/'db');node=source(store)
    physical=store.generate_monthly_subscription('physical',10,[node['id']])
    with patch.object(proxy,'source_is_local',return_value=True),patch.object(proxy,'require_proxy'):
        proxied=store.generate_monthly_subscription('proxy',10,[node['id']],proxy_source_ids=[node['id']])
    p=store.list_grants(physical['customer_id'])[0];q=store.list_grants(proxied['customer_id'])[0]
    assert p['egress_interface']=='eth0' and q['egress_interface']=='Meta'
    assert json.loads(q['egress_policy'])['require_source_proxy'] is True
    assert store.list_sources()[0]['egress_mode']=='physical'


def test_proxy_selection_cannot_name_unselected_or_remote_source(tmp_path):
    store=ClientStore(tmp_path/'db');node=source(store)
    for selection in (['foreign'],'not-a-list'):
        with pytest.raises(ClientStoreError):
            store.generate_monthly_subscription('invalid',10,[node['id']],proxy_source_ids=selection)
    with patch.object(proxy,'source_is_local',return_value=False):
        with pytest.raises(ClientStoreError,match='状态检测'):
            store.generate_monthly_subscription('remote',10,[node['id']],proxy_source_ids=[node['id']])


def test_unready_mode_change_does_not_modify_grant(tmp_path):
    store=ClientStore(tmp_path/'db');node=source(store)
    result=store.generate_monthly_subscription('physical',10,[node['id']]);grant=store.list_grants(result['customer_id'])[0]
    with patch.object(proxy,'source_is_local',return_value=True),patch.object(proxy,'require_proxy',side_effect=ValueError('not ready')):
        with pytest.raises(ClientStoreError,match='not ready'):
            store.set_grant_egress(grant['id'],egress_mode='source_proxy',egress_interface='Meta')
    assert store.line_grant(grant['id'])==grant


def test_proxy_loss_marks_route_unavailable_and_rejects_lease(tmp_path):
    store=ClientStore(tmp_path/'db');node=source(store)
    with patch.object(proxy,'source_is_local',return_value=True),patch.object(proxy,'require_proxy'):
        result=store.generate_monthly_subscription('proxy',10,[node['id']],proxy_source_ids=[node['id']])
    grant=store.list_grants(result['customer_id'])[0]
    device=store.enroll_device(result['token'],'sign','device',wireguard_public_key='B'*43+'=')
    with patch.object(proxy,'source_is_local',return_value=True),patch.object(proxy,'proxy_status',return_value={'available':False}):
        assert ClientServiceAPI(store).routes(device)[0]['available'] is False
        with pytest.raises(ClientStoreError,match='未就绪'):
            store.issue_lease(device['id'],grant['id'])


def test_cli_defaults_and_explicit_proxy_option():
    parser=build_parser()
    args=parser.parse_args(['grant','issue','--customer','c','--name','n','--endpoint','10.20.32.13:51910','--tunnel','t'])
    assert args.egress_mode=='physical'
    assert '::' not in args.allowed_ips
    assert parser.parse_args(['source-proxy','start']).action=='start'


def test_main_table_default_detection_ignores_tun():
    calls=[]
    def runner(argv):
        calls.append(argv)
        if 'route' in argv:
            data=[{'dev':'Meta','gateway':'198.18.0.1','metric':1},{'dev':'eth0','gateway':'10.20.32.1','metric':100}]
        else:data=[{'linkinfo':{'info_kind':'tun' if argv[-1]=='Meta' else 'ether'}}]
        return subprocess.CompletedProcess(argv,0,json.dumps(data),'')
    with patch.object(proxy.platform,'system',return_value='Linux'),patch.object(proxy,'_run',side_effect=runner):
        assert proxy.physical_defaults()=={'egress_interface':'eth0','egress_gateway':'10.20.32.1'}
    assert calls[0][-3:]==['table','main','default']


@pytest.mark.parametrize('running,tun',[(False,False),(False,True),(True,False),(True,True)])
def test_readiness_requires_running_service_and_tun(running,tun):
    def runner(argv):
        if argv[0].endswith('systemctl'):return subprocess.CompletedProcess(argv,0 if running else 3,'','')
        return subprocess.CompletedProcess(argv,0,json.dumps([{'flags':['UP'],'linkinfo':{'info_kind':'tun' if tun else 'dummy'}}]),'')
    with patch.object(proxy.platform,'system',return_value='Linux'),patch.object(proxy,'_run',side_effect=runner):
        assert proxy.proxy_status()['available'] is (running and tun)


def test_start_uses_only_fixed_privileged_helper():
    with patch.object(proxy.platform,'system',return_value='Linux'),patch.object(proxy.subprocess,'run',return_value=subprocess.CompletedProcess([],0,'','')) as run,patch.object(proxy,'proxy_status',return_value={'available':True}):
        proxy.start_proxy()
    assert run.call_args.args[0]==['/usr/bin/sudo','-n','/usr/local/sbin/sna-commercial-proxy','start']
