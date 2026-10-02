import json
from unittest.mock import Mock
import pytest
from test_client_attachment import network
from server_network_assist import client, client_attachment, client_campus, client_native


class PortalResponse:
    def __init__(self, value=None, location=None, status=200, cookie=None):
        self.status,self.location,self.cookie = status,location,cookie
        self.body=json.dumps(value or {}).encode()
    def read(self,limit):
        return self.body[:limit]
    def getheader(self,name):
        return self.location if name=='Location' else None
    def getheaders(self):
        return [('Set-Cookie',self.cookie)] if self.cookie else []


def portal(monkeypatch,responses):
    calls=[]
    class Connection:
        def __init__(self,host,link):
            self.host,self.link=host,link
        def request(self,method,path,body=None,headers=None):
            calls.append((self.host,self.link['id'],method,path,body,headers or {}))
        def getresponse(self):
            return responses.pop(0)
        def close(self):
            pass
    monkeypatch.setattr(client_campus,'BoundHTTPS',Connection)
    return calls


def session():
    return PortalResponse(location='http://auth1.ysu.edu.cn/portal/?sessionId=fixture-session',status=302,cookie='portalCookie=fixture; Path=/')


def online(result='success'):
    return PortalResponse({'data':{'portalOnlineUserInfo':{'result':result}}})


def test_manual_logout_matches_netlogin_auth1_protocol_and_verifies_offline(monkeypatch):
    value=network('10.126.63.249')
    responses=[session(),online(),PortalResponse({'code':200}),PortalResponse(location='http://124.124.124.124/',status=302)]
    calls=portal(monkeypatch,responses)
    monkeypatch.setattr(client_attachment,'snapshot',lambda:value)
    result=client_campus.logout(value)
    assert result['after']=='offline' and result['logout_requested']
    posts=[row for row in calls if row[2]=='POST']
    assert len(posts)==1 and posts[0][3]=='/eportal/network/offline'
    assert json.loads(posts[0][4])=={'sessionId':'fixture-session'}
    assert posts[0][5]['Cookie']=='portalCookie=fixture'
    assert posts[0][5]['Origin']=='https://auth1.ysu.edu.cn'
    assert all(row[0]=='auth1.ysu.edu.cn' and row[1]==16 for row in calls)
    assert not responses
    assert 'fixture-session' not in json.dumps(result)


def test_already_offline_does_not_post_logout(monkeypatch):
    calls=portal(monkeypatch,[PortalResponse(location='http://124.124.124.124/',status=302)])
    snapshot=Mock(side_effect=AssertionError('not needed'))
    monkeypatch.setattr(client_attachment,'snapshot',snapshot)
    assert not client_campus.logout(network())['logout_requested']
    assert not any(row[2]=='POST' for row in calls)


@pytest.mark.parametrize('kind',['unknown','redirect','network-change'])
def test_ambiguous_or_changed_session_never_posts_logout(monkeypatch,kind):
    responses=[session(),online('unrecognized')] if kind=='unknown' else [PortalResponse(location='https://untrusted.example/?sessionId=bad',status=302)] if kind=='redirect' else [session(),online()]
    calls=portal(monkeypatch,responses)
    monkeypatch.setattr(client_attachment,'snapshot',lambda:network('192.168.43.2',17,'hotspot'))
    with pytest.raises(ValueError):
        client_campus.logout(network())
    assert not any(row[2]=='POST' for row in calls)


def test_logout_does_not_claim_success_if_school_returns_error_or_account_stays_online(monkeypatch):
    value=network()
    monkeypatch.setattr(client_attachment,'snapshot',lambda:value)
    for post,after in [(PortalResponse({'code':500}),[]),(PortalResponse({'code':200}),[session(),online()])]:
        calls=portal(monkeypatch,[session(),online(),post,*after])
        with pytest.raises(ValueError):
            client_campus.logout(value)
        assert sum(row[2]=='POST' for row in calls)==1


def test_hotspot_is_not_used_for_campus_logout(monkeypatch):
    connection=Mock()
    monkeypatch.setattr(client_campus,'BoundHTTPS',connection)
    with pytest.raises(ValueError,match='不是已配置的校园'):
        client_campus.logout(network('192.168.43.2'))
    connection.assert_not_called()


def test_panel_requires_manual_confirmation_and_idle_recovered_state(tmp_path,monkeypatch):
    panel=client.ClientPanel(tmp_path)
    monkeypatch.setattr(client,'WINDOWS',True)
    logout=Mock(return_value={'ok':True,'after':'offline','message':'下线已验证'})
    monkeypatch.setattr(client_campus,'logout',logout)
    monkeypatch.setattr(client_attachment,'snapshot',lambda:network())
    leave,lease=Mock(),Mock()
    monkeypatch.setattr(panel,'leave_network',leave)
    monkeypatch.setattr(panel.online,'lease',lease)
    with pytest.raises(ValueError,match='明确确认'):
        panel.campus_logout()
    panel.save_active('grant','sna-fixture','online')
    with pytest.raises(ValueError,match='先退出'):
        panel.campus_logout(True)
    panel.clear_active()
    panel.leaving_path.write_text('{}')
    with pytest.raises(ValueError,match='先退出'):
        panel.campus_logout(True)
    panel.leaving_path.unlink()
    logout.assert_not_called()
    assert panel.campus_logout(True)['ok']
    logout.assert_called_once()
    leave.assert_not_called();lease.assert_not_called()


def test_new_launcher_explains_reused_old_backend_without_stopping_it(monkeypatch,tmp_path):
    info={'token':'fixture','pid':123}
    monkeypatch.setattr(client_native,'existing_panel',lambda _:(info,'http://127.0.0.1:9999',{'native_ui':True}))
    response=Mock()
    response.__enter__=Mock(return_value=response)
    response.__exit__=Mock(return_value=None)
    response.read.return_value=b'{"shown":true}'
    opener=Mock()
    opener.open.return_value=response
    monkeypatch.setattr(client_native.urllib.request,'build_opener',lambda *_:opener)
    warning=Mock()
    monkeypatch.setattr(client_native,'windowed_notice',warning)
    assert client_native.show_existing_panel(tmp_path)
    assert '旧版仍在运行' in warning.call_args.args[0]
    assert '一键入网' in warning.call_args.args[0]
