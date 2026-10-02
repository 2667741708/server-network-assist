"""Anonymous GET probes only. Never enroll, lease, switch networks or write keys."""
import datetime
import json
import ssl
import sys
import tempfile
import urllib.error
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from server_network_assist.client_attachment import snapshot
from server_network_assist.client_lan_transport import subscription_opener, subscription_links, connect
from server_network_assist.client_online import OnlineServiceClient

base = 'https://whm12.art'
attachment = snapshot()
report = {'utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'python':sys.version,
    'paths':[{'name':r.get('name'),'wifi':r.get('wifi'),'addresses':r.get('addresses')} for r in subscription_links(attachment,'10.20.32.13')],
    'attempts':[],'enrolled':False,'network_changed':False}
with tempfile.TemporaryDirectory() as directory:
    client = OnlineServiceClient(directory)
    for name,context in [('default',ssl.create_default_context()),('client',client.tls_context)]:
        row={'context':name,'flags':context.verify_flags,'trust':context.cert_store_stats()}
        try:
            opener=subscription_opener(base,context,attachment=attachment)
            with opener.open(base+'/client/v1/subscription',timeout=7) as response:
                row['status']=response.status
        except urllib.error.HTTPError as error:
            row['status']=error.code
        except (OSError,ValueError) as error:
            reason=error.reason if isinstance(error,urllib.error.URLError) else error
            row.update({'error_type':type(reason).__name__,'verify_code':getattr(reason,'verify_code',None),
                'verify_message':getattr(reason,'verify_message',str(reason))})
        report['attempts'].append(row)
report['campus_api_probes']=[]
for row in subscription_links(attachment,'10.20.32.13')[:4]:
    probe={'interface':row.get('name'),'host':'10.20.32.13','port':9182}
    try:
        with connect('10.20.32.13',9182,row):
            probe['tcp_reachable']=True
    except OSError as error:
        probe.update({'tcp_reachable':False,'error_type':type(error).__name__})
    report['campus_api_probes'].append(probe)
print(json.dumps(report,ensure_ascii=True))
