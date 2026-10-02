// Live post-deploy verification of the 复制地址 button on the REAL admin pages,
// in the REAL contexts. Uses a synthetic address; no real enrollment token involved.
//
// Why this exists: the admin console is reachable over plain HTTP (campus 9182,
// WireGuard 9180), where navigator.clipboard does not exist because the origin is
// not a secure context. A fix that only used the async Clipboard API silently did
// nothing there. This tool measures both paths on the deployed pages:
//   secure context   (https://whm12.art) -> navigator.clipboard.writeText first
//   insecure context (9182 / 9180)       -> document.execCommand('copy')
//
// The #address-dialog is a sibling of #workspace, so it is NOT behind the login
// gate; opening it with showModal() lets this drive the page's own #copy button as
// a real user would, without ever handling an admin credential.
//
//   node tools/admin-copy-live-check/check-copy.cjs            # all entries
//   node tools/admin-copy-live-check/check-copy.cjs 10.20.32.13:9182
//
// Exit code 0 only if every reachable entry put the FULL address on the clipboard
// with the success message and no page error.
const path=require('node:path');
const {chromium}=require(path.resolve(__dirname,'../../frontend/node_modules/playwright'));

// Defaults are the three production admin entries. Any argument replaces the list
// with a URL to an admin page; the secure flag follows the scheme.
const DEFAULTS=[
  {name:'campus-9182', url:'http://10.20.32.13:9182/subscriptions.html',              secure:false},
  {name:'wg-9180',     url:'http://10.201.250.1:9180/subscription-admin/subscriptions.html', secure:false},
  {name:'public-tls',  url:'https://whm12.art/subscription-admin/subscriptions.html', secure:true},
];
const argv=process.argv.slice(2);
const ENTRIES=argv.length
  ? argv.map(u=>({name:new URL(u).host, url:u, secure:new URL(u).protocol==='https:'}))
  : DEFAULTS;
const SAMPLE='http://10.20.32.13:9182/#enroll='+'DEMO'.repeat(110); // 472 chars

(async()=>{
  const browser=await chromium.launch({headless:true});
  let bad=0;
  try{
    for(const e of ENTRIES){
      const page=await browser.newPage({viewport:{width:1280,height:900}});
      const errs=[];
      page.on('pageerror',x=>errs.push(x.message));
      console.log('\n=== '+e.name+' ===');
      let resp;
      try{ resp=await page.goto(e.url,{waitUntil:'domcontentloaded',timeout:20000}); }
      catch(err){ console.log('UNREACHABLE:',err.message.split('\n')[0]); bad++; await page.close(); continue; }
      console.log('http status      :',resp.status());

      const ctx=await page.evaluate(()=>({secure:window.isSecureContext,clip:typeof navigator.clipboard,
        exec:document.queryCommandSupported?document.queryCommandSupported('copy'):null}));
      console.log('isSecureContext  :',ctx.secure,'(expect '+e.secure+')');
      console.log('navigator.clipboard:',ctx.clip);
      console.log('execCommand(copy) supported:',ctx.exec);

      // Capture BOTH paths: wrap writeText, and listen for the copy event that
      // execCommand fires. Whichever the page uses, we record it and its source.
      await page.evaluate(()=>{
        window.__copied=null; window.__path=null; window.__tried=[];
        if(navigator.clipboard&&navigator.clipboard.writeText){
          const orig=navigator.clipboard.writeText.bind(navigator.clipboard);
          navigator.clipboard.writeText=(t)=>{
            window.__tried.push('clipboard.writeText');
            return orig(t).then(
              ()=>{window.__copied=t;window.__path='clipboard.writeText';},
              (e)=>{window.__tried.push('writeText-REJECTED: '+e.name);throw e;});
          };
        }
        document.addEventListener('copy',(ev)=>{
          window.__tried.push('execCommand');
          let t='';
          if(ev.clipboardData)t=ev.clipboardData.getData('text/plain');
          if(!t){const a=document.activeElement;t=(a&&'value'in a)?a.value:document.getSelection().toString();}
          window.__copied=t; window.__path='execCommand';
        },{capture:true});
      });

      const box=page.locator('#subscription-url');
      await box.waitFor({state:'attached',timeout:8000});
      // The dialog is a sibling of #workspace, so it is not behind the login gate.
      // Opening it directly lets us click the page's OWN #copy button as a real user
      // would, without an admin credential -- which must never be used here.
      await page.evaluate((v)=>{
        document.getElementById('address-dialog').showModal();
        document.getElementById('subscription-url').value=v;
      },SAMPLE);
      const vis=await page.evaluate(()=>{const b=document.getElementById('copy').getBoundingClientRect();
        return {w:Math.round(b.width),h:Math.round(b.height)};});
      console.log('copy button box  :',vis.w+'x'+vis.h,vis.w>0&&vis.h>0?'VISIBLE ✅':'NOT VISIBLE ❌');

      await page.locator('#copy').click();
      await page.waitForTimeout(500);

      const got=await page.evaluate(()=>window.__copied);
      const via=await page.evaluate(()=>window.__path);
      const tried=await page.evaluate(()=>window.__tried);
      const msg=(await page.locator('#message').textContent()||'').trim();
      const field=await box.inputValue();

      const okLen=got===SAMPLE;
      console.log('attempts         :',tried.join(' -> ')||'(none)');
      console.log('terminal path    :',via);
      console.log('clipboard length :',got?got.length:0,'of',SAMPLE.length,okLen?'MATCH ✅':'MISMATCH ❌');
      console.log('#message         :',JSON.stringify(msg));
      console.log('field untouched  :',field===SAMPLE);
      console.log('page errors      :',errs.length?errs:'none');
      // The requirement is: the full address reaches the clipboard, with the right
      // message, whatever path survived. On a secure origin the code must PREFER
      // the async Clipboard API; falling back to execCommand when it is denied is
      // correct behaviour, not a defect.
      const prefOk = !e.secure || tried[0]==='clipboard.writeText';
      if(!prefOk)console.log('preference note  : secure origin should try clipboard.writeText first');
      if(!okLen||msg!=='订阅地址已复制。'||field!==SAMPLE||errs.length||!prefOk)bad++;
      await page.close();
    }
    // --- Second pass: secure origin WITH clipboard permission granted ---------
    // Proves the preferred path (navigator.clipboard.writeText) genuinely works
    // when the browser allows it; the first pass showed the fallback covering the
    // denied case. Together they cover both outcomes on the public entry.
    const secureEntry=ENTRIES.find(x=>x.secure);
    if(secureEntry){
      const e=secureEntry;
      const ctx=await browser.newContext({viewport:{width:1280,height:900},permissions:['clipboard-read','clipboard-write']});
      const page=await ctx.newPage();
      console.log('\n=== '+e.name+' (clipboard permission GRANTED) ===');
      await page.goto(e.url,{waitUntil:'domcontentloaded',timeout:20000});
      await page.evaluate(()=>{window.__path=null;window.__tried=[];
        const orig=navigator.clipboard.writeText.bind(navigator.clipboard);
        navigator.clipboard.writeText=(t)=>{window.__tried.push('clipboard.writeText');
          return orig(t).then(()=>{window.__path='clipboard.writeText';});};
        document.addEventListener('copy',()=>{window.__tried.push('execCommand');window.__path='execCommand';},{capture:true});
      });
      await page.evaluate((v)=>{document.getElementById('address-dialog').showModal();
        document.getElementById('subscription-url').value=v;},SAMPLE);
      await page.locator('#copy').click();
      await page.waitForTimeout(500);
      const via=await page.evaluate(()=>window.__path);
      const tried=await page.evaluate(()=>window.__tried);
      const msg=(await page.locator('#message').textContent()||'').trim();
      const real=await page.evaluate(()=>navigator.clipboard.readText().catch(()=>null));
      console.log('attempts         :',tried.join(' -> ')||'(none)');
      console.log('terminal path    :',via,via==='clipboard.writeText'?'✅':'❌ (expected the primary path here)');
      console.log('OS clipboard readback matches:',real===SAMPLE,real?('('+real.length+' chars)'):'(unreadable)');
      console.log('#message         :',JSON.stringify(msg));
      if(via!=='clipboard.writeText'||real!==SAMPLE||msg!=='订阅地址已复制。')bad++;
      await ctx.close();
    }
  }finally{await browser.close();}
  console.log('\n=== RESULT: '+(bad?'FAILED ('+bad+')':'OK')+' ===');
  process.exitCode=bad?1:0;
})().catch(e=>{console.error(e);process.exitCode=1;});
