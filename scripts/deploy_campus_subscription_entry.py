"""Static-only campus subscription entry repair, with checked baseline and rollback."""
from pathlib import Path
import hashlib, json, os, time, urllib.request
root=Path('/home/a/.local/lib/python3.13/site-packages/server_network_assist/ui')
baselines={'subscriptions.html':'b7b394685490202d0bae47a644af5a81e311017b63a0d5baa5e1e2d4c6f17c0f','subscriptions.js':'70242c69bcc2a3f39d56a47ad029a80065da43dc126ec82e0c64dbf78aec3468'}
expected={'subscriptions.html':'c5377988efc85530a1bc202862eeb11727f3b7a14010dcc07e6c2c18ff027705','subscriptions.js':'459046a14c90aeb635d6215c5956940d4276c332d90ef98f11136110a4389b19'}
def digest(body):
    return hashlib.sha256(body).hexdigest()
originals={name:(root/name).read_bytes() for name in baselines}
for name,body in originals.items():
    assert digest(body)==baselines[name], 'Live baseline changed: '+name
html=originals['subscriptions.html'].decode('utf-8-sig')
assert html.count('value="https://whm12.art"')==2
html=html.replace('value="https://whm12.art"','value="http://10.20.32.13:9182"')
html=html.replace('客户可访问的订阅服务地址','澜桥校园 API 地址')
html=html.replace('公网 HTTPS 入口可在热点或普通路由器下读取。私有校园 IP 入口需要校园路径。读取订阅不等于源网 UDP 已连通。','默认生成澜桥校园直连地址；Wi-Fi 需先完成校园认证。')
html=html.replace('粘贴到入网客户端。','粘贴到澜桥的“添加或刷新订阅”。')
js=originals['subscriptions.js'].decode('utf-8-sig')
anchor='function showAddress(result,name,title){'
assert js.count(anchor)==1
helper="""function campusSubscriptionAddress(value){
  // Only the known public alias of this same 4090 issuer is rebased.
  const parsed=new URL(value);
  if(parsed.origin==='https://whm12.art'&&!parsed.username&&!parsed.password&&!parsed.search&&['','/'].includes(parsed.pathname)){
    return 'http://10.20.32.13:9182/'+parsed.hash;
  }
  return value;
}
"""
js=js.replace(anchor,helper+anchor)
js=js.replace("$('subscription-url').value=result.url;","$('subscription-url').value=campusSubscriptionAddress(result.url);")
candidates={'subscriptions.html':html.encode(),'subscriptions.js':js.encode()}
for name,body in candidates.items():
    assert digest(body)==expected[name], 'Candidate differs from tested local file: '+name
backup=Path(__file__).parent/('campus-entry-backup-'+str(time.time_ns()))
backup.mkdir(mode=0o700)
for name,body in originals.items():
    (backup/name).write_bytes(body)
def atomic(name,body):
    target=root/name
    temporary=target.with_name(name+'.campus-entry-new')
    temporary.write_bytes(body)
    temporary.chmod(target.stat().st_mode&0o777)
    os.replace(temporary,target)
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
try:
    for name,body in candidates.items():
        assert digest((root/name).read_bytes())==baselines[name]
        atomic(name,body)
    for name,body in candidates.items():
        with opener.open('http://127.0.0.1:9182/'+name,timeout=5) as response:
            assert response.read()==body, 'Served file mismatch: '+name
except BaseException:
    for name,body in originals.items():
        atomic(name,body)
    raise
print(json.dumps({'deployed':True,'backup':str(backup),'sha256':expected,'services_restarted':False,'customers_or_tokens_changed':False,'network_changed':False}))
