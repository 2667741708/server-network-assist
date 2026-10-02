"""Guarded source-registry API release; no live customer or network mutations."""
import argparse
import hashlib
import http.cookiejar
import importlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT=Path('/home/a/.local/lib/python3.13/site-packages/server_network_assist')
DATA=Path('/home/a/.local/share/server-network-assist-commercial/data')

def digest(body):return hashlib.sha256(body).hexdigest()

def network():
    result={}
    for name,args in [('routes',['ip','-j','-4','route','show','table','all']),('rules',['ip','-j','rule','show'])]:
        rows=json.loads(subprocess.run(args,check=True,capture_output=True,text=True).stdout)
        for row in rows:
            for key in ('expires','cache'):row.pop(key,None)
        result[name]=digest(json.dumps(rows,sort_keys=True).encode())
    for unit in ['mihomo.service','wg-quick@sna-commercial.service','server-network-assist-relay.timer']:
        result[unit]=subprocess.run(['systemctl','show',unit,'-p','InvocationID','-p','MainPID','-p','ActiveState'],
            check=True,capture_output=True,text=True).stdout
    return result

def identities():
    values=[]
    with sqlite3.connect(DATA/'commercial-service.sqlite3') as db:
        for table in ['sources','plans','customers','devices','enrollment_tokens','line_grants','subscription_addresses','billing_anchors']:
            values.append([table,db.execute('SELECT * FROM '+table+' ORDER BY rowid').fetchall()])
    return digest(json.dumps(values,default=str).encode())

def check_files(stage):
    manifest=json.loads((stage/'manifest.json').read_text())
    for name,expected in manifest['candidate'].items():
        assert digest((stage/'candidate'/name).read_bytes())==expected,'Candidate checksum mismatch: '+name
        compile((stage/'candidate'/name).read_bytes(),name,'exec')
    return manifest

def prepare(stage):
    assert subprocess.check_output(['hostname'],text=True).strip()=='a-MS-7E06'
    manifest=check_files(stage)
    for name,expected in manifest['baseline'].items():
        path=ROOT/name
        assert (digest(path.read_bytes()) if path.exists() else None)==expected,'Live file changed: '+name
    runtime=stage/'runtime/server_network_assist'
    shutil.copytree(ROOT,runtime,dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__','ui','subscription_admin_ui'))
    for name in manifest['candidate']:shutil.copy2(stage/'candidate'/name,runtime/name)
    sys.path.insert(0,str(runtime.parent))
    importlib.import_module('server_network_assist.app')
    tests=stage/'tests'
    import re
    import types
    import unittest
    class Raises:
        def __init__(self,kind,match=None):self.kind,self.match=kind,match
        def __enter__(self):return self
        def __exit__(self,kind,value,traceback):
            assert kind is not None and issubclass(kind,self.kind),'Expected validation error'
            if self.match:assert re.search(self.match,str(value)),str(value)
            return True
    # These six tests need only fixture and raises. Supply those two helpers
    # without installing a test runner or changing the production environment.
    sys.modules['pytest']=types.SimpleNamespace(fixture=lambda function:function,raises=Raises)
    def load(name):
        spec=importlib.util.spec_from_file_location(name,tests/(name+'.py'))
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module
    module=load('test_source_management');suite=unittest.TestSuite()
    for name in sorted(n for n in vars(module) if n.startswith('test_')):
        def run(name=name):
            with tempfile.TemporaryDirectory(prefix='source-management-isolated-') as folder:
                getattr(module,name)(module.registry(Path(folder)))
        suite.addTest(unittest.FunctionTestCase(run,description=name))
    sys.modules.pop('pytest',None)
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(load('test_source_management_api').SourceManagementAPITests))
    result=unittest.TextTestRunner(verbosity=1).run(suite)
    assert result.wasSuccessful(),'Isolated runtime tests failed'
    report={'prepared':True,'isolated_api_and_migration_tests':result.testsRun,'network':network(),'live_files_changed':False}
    (stage/'prepared.json').write_text(json.dumps(report,indent=2));print(json.dumps(report))

def verify(stage):
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    def call(path,payload=None,csrf=''):
        request=urllib.request.Request('http://127.0.0.1:9182/api/'+path,
            data=None if payload is None else json.dumps(payload).encode(),
            headers={'Origin':'http://10.20.32.13:9182','X-CSRF-Token':csrf,'Content-Type':'application/json'})
        with opener.open(request,timeout=8) as response:return json.load(response)
    for attempt in range(25):
        try:call('session');break
        except urllib.error.URLError:
            if attempt==24:raise
            time.sleep(.2)
    try:call('client-service');raise AssertionError('Anonymous management read accepted')
    except urllib.error.HTTPError as error:assert error.code==401
    account=json.loads((DATA/'initial-login.json').read_text())
    session=call('login',{'username':'admin','key':account['key'],'remember':False})
    try:
        listing=call('client-service')
        assert listing['source_management_supported']
        assert all(isinstance(s.get('revision'),str) and len(s['revision'])==64 for s in listing['sources'])
        return {'authenticated_source_list':True,'anonymous_denied':True,'source_count':len(listing['sources']),
            'customer_count':len(listing['customers']),'real_customer_modified':False}
    finally:call('logout',{},session['csrf'])

def atomic(path,body):
    temp=path.with_name(path.name+'.source-management-new');temp.write_bytes(body)
    temp.chmod(path.stat().st_mode & 0o777 if path.exists() else 0o644);os.replace(temp,path)

def apply(stage):
    assert (stage/'prepared.json').exists()
    manifest=check_files(stage)
    for name,expected in manifest['baseline'].items():
        path=ROOT/name
        assert (digest(path.read_bytes()) if path.exists() else None)==expected,'Live file changed: '+name
    baseline=network();before=identities();backup=stage/('backup-'+str(time.time_ns()));backup.mkdir(mode=0o700)
    original={name:(ROOT/name).read_bytes() if (ROOT/name).exists() else None for name in manifest['candidate']}
    for name,body in original.items():
        if body is not None:(backup/name).write_bytes(body)
    with sqlite3.connect(DATA/'commercial-service.sqlite3') as source:
        with sqlite3.connect(backup/'commercial-service.sqlite3') as destination:source.backup(destination)
    (backup/'commercial-service.sqlite3').chmod(0o600)
    try:
        for name in manifest['candidate']:atomic(ROOT/name,(stage/'candidate'/name).read_bytes())
        subprocess.run(['systemctl','--user','restart','sna-commercial.service'],check=True,timeout=20)
        report=verify(stage)
        assert network()==baseline,'Protected routes, rules or network services changed'
        assert identities()==before,'Customer, source, device or subscription identity changed'
    except BaseException:
        for name,body in original.items():
            if body is None:(ROOT/name).unlink(missing_ok=True)
            else:atomic(ROOT/name,body)
        subprocess.run(['systemctl','--user','restart','sna-commercial.service'],check=True,timeout=20)
        raise
    report.update(deployed=True,backup=str(backup),network_unchanged=True,customer_and_subscription_identities_unchanged=True)
    (stage/'applied.json').write_text(json.dumps(report,indent=2));print(json.dumps(report))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['prepare','apply','verify'])
    parser.add_argument('--stage',type=Path,required=True);args=parser.parse_args()
    {'prepare':prepare,'apply':apply,'verify':verify}[args.action](args.stage)
