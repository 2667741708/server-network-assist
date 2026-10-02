// Actual controller and compiled Angular shell; all data is isolated/intercepted.
const {chromium}=require('../frontend/node_modules/playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..'),assets=path.join(root,'src/server_network_assist/subscription_admin_ui');
const built=path.join(root,'frontend/dist/frontend/browser');
(async()=>{
 const browser=await chromium.launch({headless:true});
 try{
  const page=await browser.newPage({reducedMotion:'reduce'}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.route('http://kernel.test/**',r=>{
   const name=path.basename(new URL(r.request().url()).pathname);
   if(name==='smooth-navigation.js'||name==='smooth-navigation.css')return r.fulfill({body:fs.readFileSync(path.join(assets,name)),contentType:name.endsWith('js')?'text/javascript':'text/css'});
   return r.fulfill({contentType:'text/html',body:`<link rel="stylesheet" href="/smooth-navigation.css"><script src="/smooth-navigation.js"></script><nav><a data-smooth-view="one" href="?view=one">One</a><a data-smooth-view="two" href="?view=two">Two</a><a href="https://outside.test/">External</a><a data-smooth-view="two" href="?view=two" download>Download</a></nav><main tabindex="-1" style="height:2500px"><section id="one"><h1 data-navigation-heading>One</h1><input id="draft"></section><section id="two" hidden><h1>Two</h1></section></main>`});
  });
  await page.goto('http://kernel.test/?view=one');
  await page.evaluate(()=>{
   window.warmed=[];window.commits=[];
   window.controller=SmoothNavigation.create({views:['one','two'],initial:'one',render:view=>{
    commits.push(view);for(const id of ['one','two'])document.getElementById(id).hidden=id!==view;
   },prefetch:view=>warmed.push(view),focusTarget:()=>document.querySelector('main')});
  });
  await page.locator('#draft').fill('preserved draft');
  await page.evaluate(()=>scrollTo(0,700));await page.waitForTimeout(30);
  // Programmatic buttons and delegated anchors share the same controller.
  await page.evaluate(()=>controller.navigate('two'));
  assert.equal(await page.locator('#two').isVisible(),true);
  assert.equal(await page.evaluate(()=>scrollY),0);
  await page.goBack();await page.locator('#one').waitFor({state:'visible'});
  await page.waitForFunction(()=>scrollY>=690);
  assert.equal(await page.locator('#draft').inputValue(),'preserved draft');
  await page.goForward();await page.locator('#two').waitFor({state:'visible'});
  assert.equal(new URL(page.url()).searchParams.get('view'),'two');
  await page.evaluate(()=>scrollTo(0,400));await page.waitForTimeout(30);
  await page.reload();
  await page.evaluate(()=>{
   window.warmed=[];
   window.controller=SmoothNavigation.create({views:['one','two'],initial:'one',render:view=>{for(const id of ['one','two'])document.getElementById(id).hidden=id!==view;},prefetch:view=>warmed.push(view),focusTarget:()=>document.querySelector('main')});controller.restore();
  });
  await page.locator('#two').waitFor({state:'visible'});await page.waitForFunction(()=>scrollY>=390);
  await page.evaluate(()=>{document.querySelector('a[data-smooth-view=one]').dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true,ctrlKey:true}));});
  assert.equal(new URL(page.url()).searchParams.get('view'),'two','Modified click must not be intercepted');
  await page.evaluate(()=>{Object.defineProperty(navigator,'connection',{configurable:true,value:{saveData:true,effectiveType:'4g'}});warmed=[];controller.warm('one');});
  await page.waitForTimeout(100);assert.deepEqual(await page.evaluate(()=>warmed),[]);
  // An obsolete response may resolve despite abort; it cannot repopulate the cache.
  const cache=await page.evaluate(async()=>{
   let calls=0,release;
   const cache=SmoothNavigation.createReadCache({allowed:['safe'],load:()=>{calls++;return new Promise(r=>release=r);}});
   const a=cache.read('safe'),b=cache.read('safe');await Promise.resolve();await Promise.resolve();release({value:1});
   await Promise.all([a,b]);await cache.read('safe');
   const pending=cache.read('safe',{fresh:true}).catch(e=>e.name);await Promise.resolve();await Promise.resolve();cache.invalidate();release({value:2});
   const obsolete=await pending;
   const next=cache.read('safe');await Promise.resolve();await Promise.resolve();release({value:3});await next;
   let denied=false;try{await cache.read('login');}catch{denied=true;}
   return {calls,obsolete,denied,stats:cache.stats};
  });
  assert.equal(cache.calls,3);assert.equal(cache.obsolete,'AbortError');assert.equal(cache.denied,true);assert.equal(cache.stats.hits,1);assert.equal(cache.stats.deduplicated,1);
  // Display-only SWR returns the old value, then publishes a revalidated value.
  assert.deepEqual(await page.evaluate(async()=>{
   let value=0;const cache=SmoothNavigation.createReadCache({allowed:['label'],maxAge:1,load:async()=>({value:++value})});
   await cache.read('label');await new Promise(r=>setTimeout(r,5));const stale=await cache.read('label',{stale:true});
   await new Promise(r=>setTimeout(r,0));const live=await cache.read('label',{fresh:true});return {stale:stale.value,live:live.value,staleHits:cache.stats.staleHits};
  }),{stale:1,live:3,staleHits:1});
  // Unsupported/failing snapshot APIs cannot prevent the pure UI commit.
  await page.emulateMedia({reducedMotion:'no-preference'});
  await page.evaluate(()=>{document.startViewTransition=undefined;});await page.evaluate(()=>controller.navigate('one'));assert.equal(await page.locator('#one').isVisible(),true);
  await page.evaluate(()=>{document.startViewTransition=()=>{throw Error('fixture snapshot failure')};});
  await page.evaluate(()=>controller.navigate('two'));assert.equal(await page.locator('#two').isVisible(),true,'Snapshot error falls back to immediate DOM commit');
  await page.evaluate(()=>{
   document.startViewTransition=update=>{let resolve;const updateCallbackDone=new Promise(r=>resolve=r);setTimeout(()=>{update();resolve();},140);return {ready:Promise.resolve(),updateCallbackDone,finished:updateCallbackDone,skipTransition(){}};};
  });
  await page.evaluate(async()=>{const older=controller.navigate('one'),newer=controller.navigate('two');await Promise.all([older,newer]);});
  assert.equal(await page.locator('#two').isVisible(),true,'Only newest rapid-navigation callback commits');
  await page.waitForTimeout(150);assert.equal(await page.locator('#two').isVisible(),true,'Late callback cannot restore obsolete view');
  await page.close();

  const noJS=await browser.newContext({javaScriptEnabled:false});
  const accessible=await noJS.newPage();
  await accessible.route('http://no-js.test/**',r=>{
   const url=new URL(r.request().url());
   const name=url.pathname==='/'?'subscriptions.html':path.basename(url.pathname);
   if(!fs.existsSync(path.join(assets,name)))return r.fulfill({contentType:'text/html',body:'<h1>Native destination</h1>'});
   return r.fulfill({body:fs.readFileSync(path.join(assets,name)),contentType:name.endsWith('html')?'text/html':name.endsWith('css')?'text/css':'text/javascript'});
  });
  await accessible.goto('http://no-js.test/');assert.equal(await accessible.locator('noscript').isVisible(),true);
  await accessible.getByRole('link',{name:'返回协作台'}).click();assert.equal(new URL(accessible.url()).pathname,'/network-assist/');
  await noJS.close();

  const fallback=await browser.newPage();
  await fallback.route('http://fallback.test/**',r=>{
   const name=path.basename(new URL(r.request().url()).pathname)||'subscriptions.html';
   if(name==='smooth-navigation.js')return r.fulfill({status:503});
   if(name==='subscriptions.html')return r.fulfill({body:fs.readFileSync(path.join(assets,name)),contentType:'text/html'});
   if(name.endsWith('.js')||name.endsWith('.css'))return r.fulfill({body:fs.readFileSync(path.join(assets,name)),contentType:name.endsWith('js')?'text/javascript':'text/css'});
   const api=new URL(r.request().url()).pathname.split('/api/')[1];
   return r.fulfill({json:api==='session'?{authenticated:true,csrf:'fixture'}:api==='client-service/dashboard'?{generated_at:1,accounting_timezone:'CST',accounting_day:'fixture',customers:[],summary:{customers:0,usable:0,active_leases:0,today_bytes:0,total_bytes:0}}:{sources:[],customers:[],grants:[]}});
  });
  await fallback.goto('http://fallback.test/subscriptions.html');await fallback.locator('#workspace-nav').waitFor();await fallback.locator('#nav-generate').click();assert.equal(await fallback.locator('#manager').isVisible(),true);await fallback.close();

  // Test the real compiled Angular app, including prefetched data and draft retention.
  const angular=await browser.newPage({reducedMotion:'reduce'}),requests=[],angularErrors=[];
  angular.on('pageerror',e=>angularErrors.push(e.message));
  await angular.route('http://angular-navigation.test/**',async r=>{
   const url=new URL(r.request().url()),api=url.pathname.split('/api/')[1];
   if(api){assert.equal(r.request().method(),'GET','Navigation must never submit a mutation');requests.push(api);
    const data={session:{authenticated:true,csrf:'fixture'},hosts:{hosts:[]},credentials:{credentials:[]},network:{profiles:[]},security:{devices:[],passkeys:[],key_enabled:false,verified:false},audit:{events:[]},'client-service':{sources:[],customers:[],grants:[],source_proxy:{available:false,reason:'prefetched-fixture-ready'}}}[api];
    if(!data)throw Error('Unexpected API '+api);if(api==='client-service')await new Promise(resolve=>setTimeout(resolve,180));return r.fulfill({json:data});
   }
   const name=url.pathname.endsWith('/')?'index.html':path.basename(url.pathname),file=path.join(built,name);
   let body=fs.readFileSync(file);if(name==='index.html')body=body.toString().replace('<base href="/">','<base href="/network-assist/">').replace('<base href="/" />','<base href="/network-assist/" />');
   return r.fulfill({body,contentType:name.endsWith('html')?'text/html':name.endsWith('css')?'text/css':name.endsWith('js')?'text/javascript':'application/octet-stream'});
  });
  await angular.goto('http://angular-navigation.test/network-assist/');
  const commercial=angular.getByRole('link',{name:/商业订阅/}).first(),overview=angular.getByRole('link',{name:/总览/}).first();
  await commercial.waitFor();await commercial.hover();await angular.waitForTimeout(300);
  assert.equal(requests.filter(p=>p==='client-service').length,1,'Intent/viewport preparation deduplicates');
  const started=Date.now();await commercial.click();await angular.waitForFunction(()=>document.querySelector('app-commercial')?.textContent.includes('prefetched-fixture-ready'));
  const firstWarmContent=Date.now()-started;
  await angular.getByLabel('客户名称',{exact:true}).fill('retained customer draft');
  await overview.click();await commercial.click();assert.equal(await angular.getByLabel('客户名称',{exact:true}).inputValue(),'retained customer draft');
  assert.equal(requests.filter(p=>p==='client-service').length,1,'Warm reentry uses cache without recreating the editor');
  await angular.goBack();await angular.waitForFunction(()=>document.querySelector('app-commercial')?.hidden===true);
  await angular.goForward();await angular.waitForFunction(()=>document.querySelector('app-commercial')?.hidden===false);
  const warmHops=[];
  for(let i=0;i<20;i++){
   await overview.click();const hop=Date.now();await commercial.click();
   await angular.waitForFunction(()=>document.querySelector('app-commercial')?.hidden===false);
   warmHops.push(Date.now()-hop);
   assert.equal(await angular.getByLabel('客户名称',{exact:true}).inputValue(),'retained customer draft');
  }
  warmHops.sort((a,b)=>a-b);
  await angular.reload();await angular.waitForFunction(()=>document.querySelector('app-commercial')?.textContent.includes('prefetched-fixture-ready'));
  assert.equal(new URL(angular.url()).searchParams.get('view'),'commercial');
  assert.deepEqual(errors,[]);assert.deepEqual(angularErrors,[]);
  console.log(JSON.stringify({result:'PASS',kernel:'history/scroll/drafts/modifier/Save-Data/single-flight/invalidation/SWR/fallback',angular:{firstWarmContentMs:firstWarmContent,commercialGets:requests.filter(p=>p==='client-service').length,prefetchedWarmVisits:22,warmHopMedianMs:warmHops[10],warmHopP95Ms:warmHops[18]}}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
