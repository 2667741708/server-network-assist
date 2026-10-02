// Isolated browser fixtures only; no live credentials, customer writes or proxy starts.
const {chromium}=require('../frontend/node_modules/playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const ui=path.resolve(__dirname,'../src/server_network_assist/subscription_admin_ui');
(async()=>{
 const browser=await chromium.launch({headless:true});
 try{
  const page=await browser.newPage(),errors=[];let auth=true;
  page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(()=>{
   const timer=window.setTimeout.bind(window),fetchReal=window.fetch.bind(window);
   window.testTimeout=2000;window.stall=null;window.stalledCalls=0;
   window.setTimeout=(fn,ms,...args)=>timer(fn,[15000,45000].includes(ms)?window.testTimeout:ms,...args);
   window.fetch=(url,options)=>{
    if(!window.stall||!String(url).endsWith(window.stall.path))return fetchReal(url,options);
    window.stalledCalls++;
    const mode=window.stall.mode;
    const pending=new Promise((resolve,reject)=>{
     window.releaseSnapshot=()=>resolve({generated_at:1,accounting_day:'old',accounting_timezone:'UTC+8',measurement_note:'late snapshot',customers:[],summary:{customers:99,usable:0,active_leases:0,today_bytes:0,total_bytes:0}});
     options.signal.addEventListener('abort',()=>reject(new DOMException('Aborted','AbortError')),{once:true});
    });
    return mode==='body'?Promise.resolve({ok:true,json:()=>pending}):pending;
   };
  });
  await page.route('**/*',r=>{
   const u=new URL(r.request().url());assert.equal(u.hostname,'recovery-fixture.test');
   if(!u.pathname.includes('/api/')){const n=u.pathname.split('/').at(-1);return r.fulfill({body:fs.readFileSync(path.join(ui,n)),contentType:n.endsWith('.js')?'application/javascript':n.endsWith('.css')?'text/css':'text/html'});}
   const endpoint=u.pathname.split('/api/')[1];let json;
   if(endpoint==='session')json={authenticated:auth,csrf:'fixture'};
   else if(endpoint==='logout'){auth=false;json={ok:true};}
   else if(endpoint==='client-service')json={sources:[],customers:[],subscription_addresses:[],source_proxy:{available:true}};
   else if(endpoint==='client-service/dashboard')json={generated_at:1,accounting_day:'today',accounting_timezone:'UTC+8',measurement_note:'fixture',customers:[],summary:{customers:0,usable:0,active_leases:0,today_bytes:0,total_bytes:0}};
   else throw Error('Unexpected endpoint '+endpoint);
   return r.fulfill({json});
  });
  await page.goto('http://recovery-fixture.test/subscriptions.html');
  await page.waitForFunction(()=>authenticated&&!busy&&dashboard);
  // Foreground requests time out, including a response whose headers arrived but body stalled.
  for(const mode of ['fetch','body']){
   await page.evaluate(mode=>{testTimeout=250;stall={path:'session',mode};},mode);
   await page.locator('#dashboard-refresh').click();
   assert.equal(await page.locator('#nav-egress').isEnabled(),true);
   await page.waitForFunction(()=>!busy&&document.getElementById('message').textContent.includes('请求超时'));
   assert.equal(await page.locator('#logout').isEnabled(),true);
   assert.equal(await page.locator('#dashboard-refresh').isEnabled(),true);
   await page.evaluate(()=>{stall=null;});
   await page.locator('#dashboard-refresh').click();await page.waitForFunction(()=>!busy&&!dashboardError);
  }
  // An ambiguous POST is never retried; mutations remain locked until a fresh snapshot.
  await page.evaluate(()=>{stall={path:'logout',mode:'fetch'};stalledCalls=0;});
  await page.locator('#logout').click();
  await page.waitForFunction(()=>!busy&&document.getElementById('message').textContent.includes('操作可能已提交'));
  assert.equal(await page.evaluate(()=>stalledCalls),1);
  assert.equal(await page.locator('#proxy-form button[type=submit]').isDisabled(),true);
  assert.equal(await page.locator('#dashboard-refresh').isEnabled(),true);
  await page.evaluate(()=>{stall=null;});await page.locator('#dashboard-refresh').click();
  await page.waitForFunction(()=>!busy&&!dashboardError);
  // Background polling leaves navigation/logout responsive; late data cannot undo logout.
  await page.evaluate(()=>{testTimeout=2000;stall={path:'client-service/dashboard',mode:'body'};void refreshDashboardInBackground();});
  await page.waitForFunction(()=>backgroundRefreshing);
  assert.equal(await page.locator('#logout').isEnabled(),true);
  await page.locator('#nav-egress').click();await page.locator('#egress-panel').waitFor({state:'visible'});
  await page.locator('#logout').click();await page.waitForFunction(()=>!busy&&!authenticated);
  await page.evaluate(()=>releaseSnapshot());await page.waitForFunction(()=>!backgroundRefreshing);
  assert.equal(await page.evaluate(()=>dashboard),null);
  assert.equal(await page.locator('#dashboard-summary').textContent(),'');
  assert.deepEqual(errors,[]);
  console.log('PASS: fetch/body timeout recovery, no POST retry, stale-data locks, responsive background polling, late snapshot ignored after logout');
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
