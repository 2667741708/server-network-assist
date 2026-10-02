"""Create a self-contained mock UI: fetch never contacts a real backend."""
import argparse
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[1]
UI=ROOT/'src/server_network_assist/client_ui'
MOCK=r'''
(()=>{
 const now=Math.floor(Date.now()/1000);
 let active=null,notifications=true,counter=2;
 let subscriptions=[{id:'preview-1',label:'校园月度套餐 · 示例',provider:'模拟服务',customer_id:'示例客户',enrolled_at:now,selected:true},{id:'preview-2',label:'备用套餐 · 示例',provider:'模拟服务',customer_id:'示例客户',enrolled_at:now,selected:false}];
 const routes=[{id:'preview-source-1',name:'校园源网甲 · 示例',available:true,egress_mode:'physical',download_bps:50000000,upload_bps:10000000},{id:'preview-source-2',name:'校园源网乙 · 示例',available:false,egress_mode:'physical',download_bps:20000000,upload_bps:5000000}];
 window.__previewPaths=[];
 window.fetch=async(input,options={})=>{
  const url=new URL(String(input),'http://offline-preview.invalid'),p=url.pathname,body=options.body?JSON.parse(options.body):{};
  window.__previewPaths.push(p);let data={ok:true},status=200;
  if(p==='/api/state')data={hostname:'界面预览 · 模拟设备',version:'0.7.0',active,recovering:false,error:null,lines:[],network_events:[],saved_subscriptions:subscriptions.map(s=>({...s})),preferences:{desktop_notifications:notifications},online_service:{configured:subscriptions.some(s=>s.selected),customer:{display_name:'示例客户'}},subscription:{configured:false},traffic:{today_bytes:235*1024**2,total_bytes:2.4*1024**3,download_bytes_per_second:active?2.5*1024**2:0,upload_bytes_per_second:active?128*1024:0,measured_since:now}};
  else if(p==='/api/online/subscription')data={routes,usage:{remaining_bytes:48*1024**3,used_bytes:2*1024**3,period_end:now+30*86400}};
  else if(p==='/api/online/connect')active={kind:'online',line_id:body.grant_id};
  else if(p==='/api/network/leave')active=null;
  else if(p==='/api/online/subscriptions/add'){const id='preview-'+(++counter);subscriptions.push({id,label:body.label||'新增示例订阅',provider:'模拟服务',customer_id:'示例客户',enrolled_at:now,selected:!subscriptions.length});data={ok:true,subscription_id:id};}
  else if(p==='/api/online/subscriptions/select'){active=null;subscriptions.forEach(s=>s.selected=s.id===body.subscription_id);}
  else if(p==='/api/online/subscriptions/remove'){if(subscriptions.find(s=>s.id===body.subscription_id)?.selected)active=null;subscriptions=subscriptions.filter(s=>s.id!==body.subscription_id);}
  else if(p==='/api/online/subscriptions/import')data={ok:true,imported:0,skipped:0};
  else if(p==='/api/preferences/notifications'){notifications=body.desktop_notifications;data={desktop_notifications:notifications};}
  else if(p==='/api/network/access')data={ready:true,network:'iYanDa · 模拟校园接入',campus:'模拟接入条件',service:'示例服务可达',source:'示例源网可达',account:'示例：未在线',message:'仅界面模拟，没有实际扫描或连接网络。'};
  else if(p==='/api/network/wifiscan')data={supported:true,message:'模拟列表，不执行无线扫描',networks:[{ssid:'iYanDa · 示例',interface:'模拟无线网卡',signal:85,secure:false,connected:true,connectable:true}]};
  else if(p==='/api/network/campus-logout')data={ok:true,after:'offline',message:'模拟注销完成，没有修改任何校园账号。'};
  else {status=400;data={error:'预览不执行此操作，也不会请求真实服务。'};}
  return new Response(JSON.stringify(data),{status,headers:{'Content-Type':'application/json'}});
 };
})();
'''

def script(body):
    return '<script>'+re.sub(r'</script',r'<\\/script',body,flags=re.I)+'</script>'

def build(target):
    page=(UI/'index.html').read_text(encoding='utf8')
    for name in ('framework7-bundle.min.css','framework7-default-theme.css','client.css'):
        page=page.replace('<link rel="stylesheet" href="/'+name+'">','<style>'+(UI/name).read_text(encoding='utf8')+'</style>')
    for name in ('framework7-bundle.min.js','client.js'):
        page=re.sub(r'<script\s+src="/'+re.escape(name)+r'"\s+defer></script>','',page)
    page=page.replace('<main class="page-content">','<main class="page-content"><div class="block block-strong inset" role="status">界面预览 · 全部为模拟数据<br>按钮只改变示例状态，不执行入网、扫描或网络配置；请勿输入真实订阅。</div>',1)
    page=page.replace('</body>',script((UI/'framework7-bundle.min.js').read_text(encoding='utf8'))+script(MOCK)+script((UI/'client.js').read_text(encoding='utf8'))+'</body>')
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(page,encoding='utf8')
    print(str(target.resolve()))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--serve',action='store_true',help='Serve only the mock HTML on loopback; no API or directory listing.')
    args=parser.parse_args();build(args.output)
    if args.serve:
        from http.server import BaseHTTPRequestHandler, HTTPServer
        payload=args.output.read_bytes()
        class PreviewHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path != '/':
                    self.send_error(404);return
                self.send_response(200)
                self.send_header('Content-Type','text/html; charset=utf-8')
                self.send_header('Cache-Control','no-store')
                self.send_header('Content-Length',str(len(payload)))
                self.end_headers();self.wfile.write(payload)
            def log_message(self,*args):
                pass
        server=HTTPServer(('127.0.0.1',0),PreviewHandler)
        print('MOCK_PREVIEW_URL=http://127.0.0.1:'+str(server.server_port)+'/',flush=True)
        server.serve_forever()
