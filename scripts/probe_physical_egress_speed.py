"""Bounded IPv4 physical-egress download probe; does not change host networking."""
import argparse, concurrent.futures, http.client, ipaddress, json, os, re, socket, ssl, subprocess, time
from urllib.parse import urlsplit, urljoin
MARK=0xf10a
class PhysicalHTTPS(http.client.HTTPSConnection):
    def connect(self):
        addresses=socket.getaddrinfo(self.host,self.port,socket.AF_INET,socket.SOCK_STREAM)
        last=None
        for address in addresses:
            ip=address[4][0]
            if not ipaddress.ip_address(ip).is_global: continue
            route=subprocess.check_output(['ip','route','get',ip,'mark',hex(MARK)],text=True).strip()
            if 'dev enp4s0' not in route or 'via 10.20.32.1' not in route: raise RuntimeError('Physical route not proven: '+route)
            sock=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
            sock.settimeout(self.timeout)
            sock.setsockopt(socket.SOL_SOCKET,socket.SO_MARK,MARK)
            sock.bind(('10.20.32.13',0))
            try:
                sock.connect((ip,self.port))
                self.sock=self._context.wrap_socket(sock,server_hostname=self.host)
                self.route=route;self.peer=ip
                return
            except Exception as exc:
                last=exc;sock.close()
        raise RuntimeError(str(last or 'No public IPv4 address'))
def request(url,headers=None):
    parsed=urlsplit(url)
    if parsed.scheme!='https':raise RuntimeError('HTTPS only')
    conn=PhysicalHTTPS(parsed.hostname,parsed.port or 443,timeout=4,context=ssl.create_default_context())
    conn.request('GET',(parsed.path or '/')+('?' + parsed.query if parsed.query else ''),headers=headers or {})
    resp=conn.getresponse()
    return conn,resp
def test(base):
    try:
        conn,resp=request(base)
        listing=resp.read(512*1024).decode('utf8','replace');conn.close()
        if resp.status!=200:raise RuntimeError('Listing HTTP '+str(resp.status))
        names=re.findall(r'href=["\']([^"\']+amd64\.iso)["\']',listing)
        if not names:raise RuntimeError('No ISO found')
        url=urljoin(base,names[0])
        conn,resp=request(url,{'Range':'bytes=0-33554431','User-Agent':'SNA-Physical-Egress-Probe/1'})
        if resp.status not in (200,206):raise RuntimeError('Download HTTP '+str(resp.status))
        started=time.monotonic();count=0
        while count<32*1024*1024 and time.monotonic()-started<6:
            chunk=resp.read(min(65536,32*1024*1024-count))
            if not chunk:break
            count+=len(chunk)
        elapsed=time.monotonic()-started
        result={'url':url,'peer':conn.peer,'physical_route':conn.route,'bytes':count,'seconds':round(elapsed,3),'download_mbps':round(count*8/elapsed/1e6,2),'http_status':resp.status}
        conn.close()
        return result
    except Exception as exc:return {'base':base,'error':str(exc)}

def external(url):
    try:
        for attempt in range(6):
            conn,resp=request(url,{'User-Agent':'SNA-Physical-Egress-Probe/1'})
            if resp.status in (301,302,303,307,308):
                next_url=urljoin(url,resp.getheader('Location',''))
                conn.close()
                if next_url==url:raise RuntimeError('Redirect loop')
                url=next_url
                continue
            break
        if resp.status not in (200,206):raise RuntimeError('Download HTTP '+str(resp.status))
        started=time.monotonic();count=0
        while count<32*1024*1024 and time.monotonic()-started<6:
            chunk=resp.read(min(65536,32*1024*1024-count))
            if not chunk:break
            count+=len(chunk)
        elapsed=time.monotonic()-started
        result={'url':url,'peer':conn.peer,'physical_route':conn.route,'bytes':count,'seconds':round(elapsed,3),'download_mbps':round(count*8/elapsed/1e6,2),'http_status':resp.status}
        conn.close()
        return result
    except Exception as exc:return {'url':url,'error':str(exc)}

if __name__=='__main__':
    if os.getuid()!=0:raise SystemExit('Root needed for per-socket SO_MARK; no route changes')
    parser=argparse.ArgumentParser()
    parser.add_argument('--external',action='store_true')
    args=parser.parse_args()
    urls=['https://speed.cloudflare.com/__down?bytes=33554432','https://go.microsoft.com/fwlink/p/?LinkId=2124703'] if args.external else ['https://mirrors.tuna.tsinghua.edu.cn/ubuntu-releases/24.04/','https://mirrors.ustc.edu.cn/ubuntu-releases/24.04/']
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        result=list(pool.map(external if args.external else test,urls))
    print(json.dumps({'tests':result,'note':'Simultaneous bounded downloads, maximum 64 MiB; not proof of ISP contract speed'},ensure_ascii=False))
