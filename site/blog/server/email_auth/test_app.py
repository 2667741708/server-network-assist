import base64
import json
from pathlib import Path
import secrets
import time
from urllib.parse import parse_qs, urlsplit

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest

from app import (Settings, create_app, PREFIX, SESSION_COOKIE, POSTS, MEDIA, safe_path)


class FakeRepository:
    def __init__(self):
        self.data = {POSTS + 'tutorials/example.md': ('a'*40, b'---\ntitle: Example\n---\nBody')}
        self.saved = []
    def file(self, path):
        safe_path(path)
        if path not in self.data:
            raise HTTPException(404)
        sha, content = self.data[path]
        return {'sha': sha, 'content': base64.b64encode(content).decode()}
    def files(self, folder):
        safe_path(folder, folder=True)
        return [{'path': p, 'sha': s, 'size': len(c)} for p, (s, c) in self.data.items() if p.startswith(folder+'/')]
    def save(self, path, content, sha):
        safe_path(path)
        if path in self.data and sha != self.data[path][0]:
            raise HTTPException(409)
        self.data[path] = ('b'*40, content)
        self.saved.append(path)
        return {'content': {'sha': 'b'*40}}
    def delete(self, path, sha):
        safe_path(path)
        if self.data[path][0] != sha:
            raise HTTPException(409)
        del self.data[path]


@pytest.fixture
def env(tmp_path):
    (tmp_path/'key').write_text(secrets.token_urlsafe(48))
    (tmp_path/'github').write_text('not-a-real-github-credential')
    settings = Settings(tmp_path/'state/db.sqlite', tmp_path/'key', tmp_path/'github',
                        tmp_path/'smtp.json', tmp_path/'google.json',
                        qq_email='owner@qq.com', google_email='owner@gmail.com')
    sent = []
    repo = FakeRepository()
    app = create_app(settings, repo, lambda settings, address, code: sent.append((address, code)))
    client = TestClient(app, base_url=settings.origin, headers={'Origin':settings.origin})
    client.get(PREFIX+'/login')
    yield client, app, settings, sent, repo
    client.close()


def request_code(env, email=None):
    client, _, settings, _, _ = env
    return client.post(PREFIX+'/otp/request', json={'email':email or settings.qq_email})


def authenticate(env):
    client, _, _, sent, _ = env
    challenge = request_code(env).json()['challenge']
    reply = client.post(PREFIX+'/otp/verify', json={'challenge':challenge, 'code':sent[-1][1]})
    assert reply.status_code == 200
    return client.get(PREFIX+'/session').json()


@pytest.mark.parametrize('identity', ['allowed', 'wrong_email', 'unverified', 'wrong_nonce'])
def test_google_callback_checks_verified_identity_and_nonce(env, monkeypatch, identity):
    import httpx
    from google.oauth2 import id_token
    client, app, settings, _, _ = env
    settings.google_client_file.write_text(json.dumps({'web':{
        'client_id':'test-client', 'client_secret':'test-secret',
        'redirect_uris':[settings.origin+PREFIX+'/google/callback']}}))
    start=client.get(PREFIX+'/google/start', follow_redirects=False)
    state=parse_qs(urlsplit(start.headers['location']).query)['state'][0]
    with app.state.store.connection() as db:
        row=dict(db.execute('SELECT * FROM oauth').fetchone())
    claims={'email':settings.google_email,'email_verified':True,'nonce':row['nonce']}
    if identity=='wrong_email': claims['email']='someone@gmail.com'
    if identity=='unverified': claims['email_verified']=False
    if identity=='wrong_nonce': claims['nonce']='incorrect-nonce'

    class TokenClient:
        def __init__(self,*args,**kwargs): pass
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def post(self,url,data):
            assert url=='https://oauth2.googleapis.com/token'
            assert data['code_verifier']==row['verifier']
            return httpx.Response(200,json={'id_token':'signed-test-token'},
                                  request=httpx.Request('POST',url))

    def verify(token,request,audience):
        assert token=='signed-test-token' and audience=='test-client'
        return claims
    monkeypatch.setattr(httpx,'Client',TokenClient)
    monkeypatch.setattr(id_token,'verify_oauth2_token',verify)
    response=client.get(PREFIX+'/google/callback',params={'state':state,'code':'test-code'},
                        follow_redirects=False)
    assert response.status_code==(302 if identity=='allowed' else 403)
    assert client.get(PREFIX+'/session').json()['authenticated']==(identity=='allowed')
    assert client.get(PREFIX+'/google/callback',params={'state':state,'code':'test-code'}).status_code==400


def test_qq_signin_one_time_cookie_and_logout(env):
    client, app, settings, sent, _ = env
    challenge = request_code(env).json()['challenge']
    assert len(sent)==1 and sent[0][0]==settings.qq_email
    body={'challenge':challenge, 'code':sent[0][1]}
    reply=client.post(PREFIX+'/otp/verify',json=body)
    assert reply.status_code==200
    cookie=reply.headers['set-cookie'].lower()
    assert 'httponly' in cookie and 'secure' in cookie and 'samesite=lax' in cookie
    assert client.post(PREFIX+'/otp/verify',json=body).status_code==400
    session=client.get(PREFIX+'/session').json()
    assert session['user']['email']==settings.qq_email
    assert 'not-a-real-github-credential' not in json.dumps(session)
    token=client.cookies.get(SESSION_COOKIE)
    assert client.post(PREFIX+'/logout',json={},headers={'X-CSRF-Token':session['csrf']}).status_code==200
    assert not app.state.store.session(token)


def test_non_allowed_email_never_receives_or_verifies_code(env):
    client, _, _, sent, _=env
    challenge=request_code(env,'someone@qq.com').json()['challenge']
    assert sent==[]
    assert client.post(PREFIX+'/otp/verify',json={'challenge':challenge,'code':'123456'}).status_code==400


def test_five_wrong_attempts_disable_code(env):
    client, _, _, sent, _=env
    challenge=request_code(env).json()['challenge']
    wrong='000000' if sent[0][1]!='000000' else '111111'
    for _ in range(5):
        assert client.post(PREFIX+'/otp/verify',json={'challenge':challenge,'code':wrong}).status_code==400
    assert client.post(PREFIX+'/otp/verify',json={'challenge':challenge,'code':sent[0][1]}).status_code==400


def test_expiration_and_browser_binding(env):
    client, app, settings, sent, _=env
    challenge=request_code(env).json()['challenge']
    outsider=TestClient(app,base_url=settings.origin,headers={'Origin':settings.origin})
    outsider.get(PREFIX+'/login')
    assert outsider.post(PREFIX+'/otp/verify',json={'challenge':challenge,'code':sent[0][1]}).status_code==400
    with app.state.store.connection() as db:
        db.execute('UPDATE codes SET expires=? WHERE id=?',(int(time.time())-1,challenge))
    assert client.post(PREFIX+'/otp/verify',json={'challenge':challenge,'code':sent[0][1]}).status_code==400


def test_send_rate_limit(env):
    assert request_code(env).status_code==200
    assert request_code(env).status_code==429


def test_cross_origin_and_csrf_cannot_write(env):
    client, _, _, _, repo=env
    session=authenticate(env)
    body={'path':POSTS+'tutorials/new.md','content':base64.b64encode(b'---\ntitle: Test\n---\n').decode()}
    assert client.post(PREFIX+'/content/save',json=body).status_code==403
    assert client.post(PREFIX+'/content/save',json=body,headers={'Origin':'https://attacker.invalid','X-CSRF-Token':session['csrf']}).status_code==403
    assert repo.saved==[]


@pytest.mark.parametrize('path',[
    '.github/workflows/ci.yml','site/blog/src/pages/index.astro','site/blog/public/admin/start.js',
    POSTS+'tutorials/../../config.md',POSTS+'tutorials/%2e%2e/config.md',
    POSTS+'tutorials/a.mdx',MEDIA+'bad.svg',MEDIA+'../bad.png',POSTS+'tutorials/.hidden.md',
    '//evil.invalid/path.md',POSTS+'tutorials\\bad.md'])
def test_path_allowlist(path):
    with pytest.raises(HTTPException):safe_path(path)


def test_content_read_save_version_conflict(env):
    client, _, _, _, repo=env
    assert client.get(PREFIX+'/content/files',params={'folder':POSTS+'tutorials'}).status_code==401
    session=authenticate(env)
    files=client.get(PREFIX+'/content/files',params={'folder':POSTS+'tutorials'}).json()['files']
    assert len(files)==1
    path=POSTS+'tutorials/example.md'
    data=client.get(PREFIX+'/content/file',params={'path':path}).json()
    body={'path':path,'sha':'f'*40,'content':base64.b64encode(b'---\ntitle: Updated\n---\nBody').decode()}
    headers={'X-CSRF-Token':session['csrf']}
    assert client.post(PREFIX+'/content/save',json=body,headers=headers).status_code==409
    body['sha']=data['sha']
    assert client.post(PREFIX+'/content/save',json=body,headers=headers).status_code==200
    assert repo.saved==[path]


def test_reject_code_disguised_as_image(env):
    client, _, _, _, repo=env
    session=authenticate(env)
    response=client.post(PREFIX+'/content/save',json={'path':MEDIA+'x.png',
        'content':base64.b64encode(b'<svg onload="alert(1)"></svg>').decode()},headers={'X-CSRF-Token':session['csrf']})
    assert response.status_code==400 and not repo.saved


def test_google_disabled_until_client_present(env):
    client, _, _, _, _=env
    assert not client.get(PREFIX+'/healthz').json()['google_enabled']
    assert client.get(PREFIX+'/google/start',follow_redirects=False).status_code==503


def test_google_redirect_pkce_and_state_binding(env):
    client, _, settings, _, _=env
    settings.google_client_file.write_text(json.dumps({'web':{'client_id':'demo.apps.googleusercontent.com',
        'client_secret':'test-only','redirect_uris':[settings.origin+PREFIX+'/google/callback']}}))
    reply=client.get(PREFIX+'/google/start',follow_redirects=False)
    assert reply.status_code==302
    url=urlsplit(reply.headers['location']);query=parse_qs(url.query)
    assert url.hostname=='accounts.google.com'
    assert query['code_challenge_method']==['S256']
    assert query['scope']==['openid email profile']
    assert 'test-only' not in reply.headers['location']
    assert client.get(PREFIX+'/google/callback?state=wrong&code=wrong').status_code==400
    state=query['state'][0]
    assert client.get(PREFIX+'/google/callback',params={'state':state,'error':'access_denied'}).status_code==400
    assert client.get(PREFIX+'/google/callback',params={'state':state,'error':'access_denied'}).status_code==400
