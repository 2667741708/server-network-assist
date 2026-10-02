import base64
import io
from server_network_assist.client_store import ClientStore
from server_network_assist.client_service_admin import run


def test_cli_creates_no_quota_or_speed_limit(tmp_path):
    store = ClientStore(tmp_path / 'service.sqlite')
    source = store.save_source({'name':'源网', 'endpoint':'10.20.32.13:51910',
        'relay_public_key':base64.b64encode(bytes(32)).decode(),
        'address_pool':'10.213.40.0/24', 'relay_interface':'sna-commercial',
        'egress_interface':'eth0','egress_gateway':'10.20.32.1','egress_mode':'physical'})
    output = io.StringIO()
    assert run(['subscription','generate','--name','测试','--quota-gb','unlimited',
        '--unlimited-speed','--source',source['id'],'--base-url','http://10.20.32.13:9182',
        '--output',str(tmp_path / 'subscription.txt')], store=store, output=output) == 0
    customer = store.list_customers()[0]
    plan = store.get_plan(customer['plan_id'])
    assert plan['download_bps'] is None and plan['upload_bps'] is None
    assert plan['quota_bytes'] is None
    assert '#enroll=enr_' in (tmp_path / 'subscription.txt').read_text()
