// Render shipped UI with documentation fixtures; never connect to real servers.
const { chromium } = require('playwright');
const http = require('http');
const fs = require('fs');
const path = require('path');
const root = path.resolve(__dirname, '..');
const packageRoot = path.join(root, 'src/server_network_assist');
const output = path.join(root, 'docs/images');
const host = (id, name, address) => ({id,name,address,port:22,username:'demo-operator',credential_id:'demo-key',jump_id:'',host_key:'',group:'演示实验室',favorite:false,terminal_enabled:false});
const hosts = [host('gateway','Ubuntu 出口机','192.0.2.10'),host('windows','Windows 工作站','192.0.2.20'),host('ubuntu','Ubuntu 计算节点','192.0.2.30')];
const profile = {id:'demo-profile',name:'实验室混合系统借网',gateway_id:'gateway',client_ids:['windows','ubuntu'],port:51919,endpoint:'192.0.2.10',tunnel_cidr:'10.213.1.0/24',preserve_routes:['192.0.2.0/24'],maintenance:true,state:'disabled',updated_at:1789005600,interface:'na1234567890',last_error:''};
let proxyFailure = false;
const desktopState = () => ({hostname:'DEMO-WINDOWS',platform:'Windows',version:'0.2.0',timestamp:1789005600,elevated:true,direct:{ok:true,milliseconds:138},system:{ok:!proxyFailure,milliseconds:proxyFailure?null:152},proxy:{enabled:proxyFailure,server:proxyFailure?'127.0.0.1:7897':'',pac:false},routes:[{adapter:'Ethernet',gateway:'192.0.2.1',metric:100}],tunnels:[{name:'lab-tunnel',active:true,addresses:['10.213.1.2'],received:498345632,sent:32255662,handshake:1789005574,endpoint:'192.0.2.10:51919',telemetry:true}]});
const fixtures = {
  '/api/session': {authenticated:true,csrf:'documentation-only',passkeys:false,secure:false,version:'0.2.0'},
  '/api/hosts': {hosts}, '/api/credentials': {credentials:[{id:'demo-key',name:'演示运维凭据',kind:'key'}]},
  '/api/network': {profiles:[profile]}, '/api/audit': {events:[]},
  '/api/security': {devices:[],passkeys:[],key_enabled:false,verified:true},
  '/api/network/probe': {results:hosts.map(h=>({...h,ssh:true,dns:true,internet:h.id==='gateway',helper:true,os:h.id==='windows'?'Windows':'Linux',hostname:h.name,default_route:'默认网关 192.0.2.1',http_code:h.id==='gateway'?'204':'000',system_internet:h.id==='windows'?false:null,error:'',assist:[]}))},
};
const assets = new Map();
for (const directory of ['ui','desktop_ui']) {
  for (const file of fs.readdirSync(path.join(packageRoot,directory))) {
    if (file!=='index.html') assets.set('/'+file,path.join(packageRoot,directory,file));
  }
}
assets.set('/',path.join(packageRoot,'ui/index.html'));
assets.set('/desktop/',path.join(packageRoot,'desktop_ui/index.html'));
const mime = {'.html':'text/html; charset=utf-8','.js':'text/javascript','.css':'text/css','.svg':'image/svg+xml','.ico':'image/x-icon'};
const server = http.createServer((request,response)=>{
  const file = assets.get(new URL(request.url,'http://localhost').pathname);
  if (!file || !fs.statSync(file).isFile()) { response.writeHead(404); response.end(); return; }
  response.writeHead(200,{'Content-Type':mime[path.extname(file)]||'application/octet-stream'});
  fs.createReadStream(file).pipe(response);
});
(async()=>{
  fs.mkdirSync(output,{recursive:true});
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const base = `http://127.0.0.1:${server.address().port}`;
  let browser;
  try {
    browser = await chromium.launch({headless:true});
    const page = await browser.newPage({viewport:{width:1280,height:1000},deviceScaleFactor:1});
    const errors=[]; page.on('pageerror',e=>errors.push(e.message));
    await page.route('**/api/**',route=>{
      const url = new URL(route.request().url()).pathname;
      if (url==='/api/status') return route.fulfill({json:desktopState()});
      if (fixtures[url]) return route.fulfill({json:fixtures[url]});
      throw new Error(`Unexpected API during documentation capture: ${url}`);
    });
    await page.goto(base+'/desktop/#documentation-only');
    await page.getByRole('heading',{name:'网络已就绪',exact:true}).waitFor();
    await page.screenshot({path:path.join(output,'desktop-overview.png'),fullPage:true});
    proxyFailure=true;
    await page.getByRole('button',{name:'↻ 刷新状态',exact:true}).click();
    await page.getByRole('heading',{name:'系统代理可能异常',exact:true}).waitFor();
    await page.screenshot({path:path.join(output,'windows-proxy-diagnosis.png'),fullPage:true});
    proxyFailure=false;
    await page.getByRole('button',{name:'↻ 刷新状态',exact:true}).click();
    await page.getByRole('heading',{name:'网络已就绪',exact:true}).waitFor();
    await page.getByRole('button',{name:'断开连接',exact:true}).click();
    await page.getByRole('dialog').waitFor();
    await page.getByRole('dialog').screenshot({path:path.join(output,'disconnect-confirmation.png')});
    await page.getByRole('button',{name:'取消',exact:true}).click();
    await page.setViewportSize({width:1440,height:1080});
    await page.goto(base+'/');
    await page.locator('.app-shell').waitFor();
    await page.locator('.sidebar button').filter({hasText:'网络借助'}).click();
    await page.getByRole('button',{name:'探测全部主机',exact:true}).click();
    await page.getByText('探测完成',{exact:true}).waitFor();
    await page.locator('.profile-item').filter({hasText:profile.name}).click();
    await page.getByRole('heading',{name:'方案设置',exact:true}).waitFor();
    await page.screenshot({path:path.join(output,'mixed-network-profile.png'),fullPage:true});
    await page.getByRole('combobox',{name:'是否共享源机器的代理',exact:true}).click();
    await page.getByRole('option',{name:'共享网络，同时共享 HTTP/HTTPS 代理',exact:true}).click();
    await page.getByRole('textbox',{name:'源机器上的 HTTP 代理 IPv4',exact:true}).waitFor();
    await page.screenshot({path:path.join(output,'proxy-sharing.png'),fullPage:true});
    if (errors.length) throw new Error(errors.join('\n'));
    console.log('Captured five documentation screenshots from shipped UI with synthetic data. No server commands executed.');
  } finally {if(browser) await browser.close(); await new Promise(resolve=>server.close(resolve));}
})().catch(error=>{console.error(error);process.exitCode=1;});
