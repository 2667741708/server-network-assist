const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('../frontend/node_modules/playwright');
const ui = path.resolve(process.argv[2]);
const output = path.resolve(process.argv[3]);
const snapshot = {generated_at:Date.now()/1000, accounting_timezone:'CST', accounting_day:'2026-09-18', measurement_note:'isolated latency fixture', customers:[], summary:{customers:0,usable:0,active_leases:0,today_bytes:0,total_bytes:0}};
(async () => {
 const browser = await chromium.launch({headless:true});
 try {
  const samples=[];
  for(let sample=0;sample<5;sample++){
   const page=await browser.newPage({reducedMotion:process.argv[4]||'reduce'}), requests=[],errors=[];
   page.on('pageerror',e=>errors.push(e.message));
   await page.route('http://navigation.test/**',async route=>{
    const url=new URL(route.request().url()), api=url.pathname.split('/api/')[1];
    if(api){requests.push({api,at:Date.now(),method:route.request().method()});
     await new Promise(resolve=>setTimeout(resolve,api==='session'?50:200));
     return route.fulfill({json:api==='session'?{authenticated:true,csrf:'fixture'}:api==='client-service/dashboard'?snapshot:{sources:[],customers:[],grants:[],subscription_addresses:[],source_proxy:{available:false,reason:'fixture'}}});
    }
    let file=path.join(ui,path.basename(url.pathname));
    if(!fs.existsSync(file)&&['tabler.min.css','tabler.min.js'].includes(path.basename(file)))file=path.join(__dirname,'../src/server_network_assist/subscription_admin_ui',path.basename(file));
    if(!fs.existsSync(file))return route.fulfill({status:404});
    await route.fulfill({body:fs.readFileSync(file),contentType:file.endsWith('.html')?'text/html':file.endsWith('.css')?'text/css':'text/javascript'});
   });
   await page.goto('http://navigation.test/subscriptions.html',{waitUntil:'domcontentloaded'});
   await page.waitForFunction(()=>document.querySelector('#dashboard-count').textContent.includes('0 / 0'));
   const firstContent=await page.evaluate(()=>performance.now());
   const nav=[];
   for(const view of ['generate','egress','customers','generate','customers']){
    nav.push(await page.evaluate(async view=>{
     const start=performance.now();document.querySelector('#nav-'+view).click();
     const id={generate:'manager',customers:'dashboard-panel',egress:'egress-panel'}[view];
     while(document.getElementById(id).hidden)await new Promise(requestAnimationFrame);
     const commit=performance.now()-start;await new Promise(requestAnimationFrame);
     return {view,commitMs:commit,firstVisibleMs:performance.now()-start};
    },view));
   }
   if(errors.length)throw Error(errors.join('\n'));
   await page.waitForTimeout(100);
   samples.push({firstContentMs:firstContent,nav,requests,cache:await page.evaluate(()=>window.subscriptionReads?.stats||null)});
   await page.close();
  }
  fs.writeFileSync(output,JSON.stringify({mode:`isolated browser; 50ms session + 200ms each data API; motion=${process.argv[4]||'reduce'}; five cold contexts`,samples},null,2));
  console.log(JSON.stringify({firstContentMs:samples.map(s=>s.firstContentMs),navVisibleMs:samples.flatMap(s=>s.nav.map(n=>n.firstVisibleMs))}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
