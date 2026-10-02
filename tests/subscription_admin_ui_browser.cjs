// Isolated browser fixtures for the v2 subscription admin console.
// Every request is intercepted; no real server, credential, or network access.
//
// Regression guards added 2026-09-30 after the console shipped with two defects:
//   1. #address-dialog / #reauth-dialog carried Tabler's `.modal` class, whose
//      `display:none` beats the [open] UA style — showModal() set `open` but the
//      dialog rendered as 0x0. Asserting `dialog.open` alone does NOT catch that,
//      so every dialog assertion here checks computed display + a real box.
//   2. A replayed action after the 5-minute fresh() gate re-authenticated and then
//      threw the server's result away, so an issued address was never shown.
const {chromium}=require('../frontend/node_modules/playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..'),ui=path.join(root,'src/server_network_assist/subscription_admin_ui');
const FRESH_TEXT='请重新验证管理员密码';
const CSRF_TEXT='登录验证已更新';

(async()=>{const browser=await chromium.launch({headless:true});try{
  const page=await browser.newPage({viewport:{width:1280,height:900},acceptDownloads:true});
  const posts=[],errors=[];
  let authenticated=false,csrf='csrf-1',logins=0,freshExpired=false;
  const source={id:'source-fixture',name:'校园源网节点',endpoint:'10.20.32.13:51910'};
  const issued='http://10.20.32.13:9182/#enroll='+'t'.repeat(450);
  const batchUrls={'客户甲':'http://10.20.32.13:9182/#enroll='+'a'.repeat(40),'客户乙':'http://10.20.32.13:9182/#enroll='+'b'.repeat(40)};
  const now=Math.floor(Date.now()/1000);
  const customer=(id,name,enabled)=>({id,display_name:name,enabled});
  const customers=[customer('cus_0001','客户甲',true),customer('cus_0002','客户乙',true),
                   customer('cus_0003','客户丙',true),customer('cus_0004','客户丁',false)];
  // status drives the row-button gates in renderCustomers()
  const addresses={'cus_0001':{status:'ready'},'cus_0002':{status:'used'},
                   'cus_0003':{status:'expired'}};
  const snapshot=()=>({generated_at:now,accounting_day:'2026-09-30',sources:[source],
    base_url_presets:['http://10.20.32.13:9182','https://10.20.32.13:8443','https://whm12.art'],
    customers,subscription_addresses:addresses,devices:[],grants:[]});
  const accessRows=[{created_at:now,customer_id:'cus_0001',customer_name:'客户甲',device_id:'dev_1',remote_ip:'10.20.31.134',method:'POST',path:'/client/v1/subscription'}];

  // Reports whether a dialog is genuinely painted, not merely `open`.
  const dialogVisible=(id)=>page.evaluate((id)=>{
    const d=document.getElementById(id);
    if(!d||!d.open)return{open:false,visible:false};
    const cs=getComputedStyle(d),r=d.getBoundingClientRect();
    return{open:true,display:cs.display,width:Math.round(r.width),height:Math.round(r.height),
           visible:cs.display!=='none'&&r.width>0&&r.height>0};
  },id);
  const assertDialogVisible=async(id,label)=>{
    // The action rides a fetch; wait for showModal() before measuring. A timeout
    // falls through to the assertion below so the failure still names the label.
    await page.waitForFunction((id)=>!!document.getElementById(id)?.open,id,{timeout:8000}).catch(()=>{});
    const s=await dialogVisible(id);
    assert.equal(s.open,true,label+': dialog should be open');
    assert.notEqual(s.display,'none',label+': dialog must not be display:none (Tabler .modal regression)');
    assert.ok(s.width>0&&s.height>0,label+': dialog must occupy a real box, got '+s.width+'x'+s.height);
  };

  page.on('pageerror',e=>errors.push(e.message));
  page.on('dialog',d=>d.accept()); // window.confirm in expireAddress()

  await page.route('**/*',async route=>{
    const request=route.request(),url=new URL(request.url());
    if(url.hostname!=='admin-fixture.test'){errors.push('Unexpected host: '+url.hostname);return route.abort();}
    if(!url.pathname.includes('/api/')){
      const name=url.pathname.split('/').at(-1);
      if(!['subscriptions.html','subscriptions.js','subscriptions.css','dashboard.js','dashboard.css','tabler.min.css','tabler.min.js','smooth-navigation.js','smooth-navigation.css'].includes(name))return route.fulfill({status:204});
      return route.fulfill({body:fs.readFileSync(path.join(ui,name)),
        contentType:name.endsWith('html')?'text/html; charset=utf-8':name.endsWith('css')?'text/css':'application/javascript'});
    }
    const api=url.pathname.split('/api/')[1];
    const body=request.method()==='POST'?request.postDataJSON():null;
    if(body)posts.push({api,body,csrf:request.headers()['x-csrf-token']});
    if(api==='session')return route.fulfill({json:{authenticated,csrf:authenticated?csrf:null}});
    if(api==='login'){authenticated=true;freshExpired=false;csrf='csrf-'+(++logins+1);return route.fulfill({json:{csrf}});}
    if(api==='logout'){authenticated=false;return route.fulfill({json:{ok:true}});}
    if(api==='client-service')return route.fulfill({json:snapshot()});
    if(api.startsWith('client-service/access-log'))return route.fulfill({json:{rows:accessRows,total:accessRows.length}});
    if(api==='client-service/action'){
      if(request.headers()['x-csrf-token']!==csrf)
        return route.fulfill({status:403,json:{error:CSRF_TEXT+'，请刷新页面'}});
      if(freshExpired)
        return route.fulfill({status:403,json:{error:FRESH_TEXT+'（验证有效期 5 分钟）'}});
      const a=body.action;
      if(a==='subscription-generate-batch'){
        const results=body.names.map(n=>batchUrls[n]?{name:n,ok:true,customer_id:'cus_b_'+n,url:batchUrls[n]}
          :{name:n,ok:false,error:'客户已存在'});
        return route.fulfill({json:{results,succeeded:results.filter(r=>r.ok).length,failed:results.filter(r=>!r.ok).length}});
      }
      return route.fulfill({json:{url:issued,customer_id:body.id||'cus_0001',
        expires_at:now+2592000,enrollment_expires_at:now+86400,status:'ready'}});
    }
    errors.push('Unstubbed API: '+api);return route.fulfill({status:404,json:{error:'unstubbed'}});
  });

  await page.goto('http://admin-fixture.test/subscription-admin/subscriptions.html',{waitUntil:'networkidle'});

  // --- Login ---------------------------------------------------------------
  assert.equal(await page.locator('#workspace').isVisible(),false,'Workspace stays hidden before login');
  await page.locator('#login-form [name=key]').fill('fixture-key-not-a-real-credential');
  await page.locator('#login-form [name=remember]').check();
  await page.getByRole('button',{name:'登录源网管理端'}).click();
  await page.locator('#workspace').waitFor({state:'visible'});
  assert.equal(posts.filter(p=>p.api==='login').length,1);
  assert.equal(posts.find(p=>p.api==='login').body.remember,30,'Remember-me must be sent as the integer 30');
  assert.equal(logins,1);

  // --- Generate + the invisible-dialog regression ---------------------------
  await page.locator('#generate-form [name=name]').fill('客户甲');
  await page.locator('#generate-form [name=base_url]').fill('http://10.20.32.13:9182');
  await page.locator('#sources input[name=source]').first().waitFor();
  await page.locator('#sources input[name=source]').check();
  await page.getByRole('button',{name:'生成订阅地址',exact:true}).click();
  await assertDialogVisible('address-dialog','After generating an address');
  assert.equal(await page.locator('#subscription-url').inputValue(),issued);
  assert.equal(await page.locator('#address-dialog-title').textContent(),'生成成功 · 您的订阅地址');
  const gen=posts.filter(p=>p.body&&p.body.action==='subscription-generate').at(-1);
  assert.equal(gen.body.base_url,'http://10.20.32.13:9182');
  assert.deepEqual(gen.body.source_ids,[source.id]);
  assert.equal(gen.body.quota_gb,10);
  assert.equal(logins,1,'Generating must not trigger another login');

  // --- Address .txt export --------------------------------------------------
  const [download]=await Promise.all([
    page.waitForEvent('download'),
    page.locator('#address-dialog-txt').click(),
  ]);
  assert.match(download.suggestedFilename(),/^订阅地址-cus_0001\.txt$/);
  const saved=await download.createReadStream();
  let text='';for await(const chunk of saved)text+=chunk.toString('utf8');
  assert.equal(text.trim(),issued,'Exported .txt must contain the full issued address');

  // --- Copy button must actually copy, in an INSECURE context ---------------
  // The campus (9182) and WireGuard (9180) admin entries are plain HTTP, so they
  // are not secure contexts and navigator.clipboard is undefined there. The button
  // used to skip straight to "please copy by hand" and never touch the clipboard.
  // This fixture origin is plain HTTP too, which reproduces the real condition.
  const insecure=await page.evaluate(()=>({secure:window.isSecureContext,clip:typeof navigator.clipboard}));
  assert.equal(insecure.secure,false,'Fixture origin must be an insecure context or this test proves nothing');
  assert.equal(insecure.clip,'undefined','navigator.clipboard is expected to be absent here, as on 9182/9180');
  await page.evaluate(()=>{
    window.__copied=null;
    document.addEventListener('copy',(e)=>{
      let t='';
      if(e.clipboardData)t=e.clipboardData.getData('text/plain');
      if(!t){const a=document.activeElement;t=(a&&'value'in a)?a.value:document.getSelection().toString();}
      window.__copied=t;
    },{capture:true});
  });
  await page.locator('#copy').click();
  assert.equal(await page.evaluate(()=>window.__copied),issued,
    'Copy button must put the FULL address on the clipboard (not just tell the user to copy by hand)');
  assert.equal(await page.locator('#message').textContent(),'订阅地址已复制。');
  assert.equal(await page.locator('#subscription-url').inputValue(),issued,'Copying must not disturb the field');

  await page.locator('#address-dialog-cancel').click();
  await page.waitForFunction(()=>!document.getElementById('address-dialog').open);

  // --- Re-auth replay must still SHOW the address ---------------------------
  freshExpired=true;
  await page.locator('#generate-form [name=name]').fill('客户丁');
  // A successful action calls refresh(), which rebuilds #sources — re-select.
  await page.locator('#sources input[name=source]').first().waitFor();
  await page.locator('#sources input[name=source]').check();
  await page.getByRole('button',{name:'生成订阅地址',exact:true}).click();
  await assertDialogVisible('reauth-dialog','When the fresh() gate expires');
  assert.equal(logins,1,'The gate must ask for re-verification, not a full re-login');
  await page.locator('#reauth-form [name=key]').fill('fixture-key-not-a-real-credential');
  await page.locator('#reauth-submit').click();
  await assertDialogVisible('address-dialog','After re-authenticating and replaying the action');
  assert.equal(await page.locator('#subscription-url').inputValue(),issued,
    'The replayed action result must be surfaced, not discarded');
  assert.equal(logins,2);
  const replay=posts.filter(p=>p.body&&p.body.action==='subscription-generate').at(-1);
  assert.equal(replay.csrf,'csrf-3','Replay must carry the rotated CSRF token');
  assert.equal(await page.locator('#generation-status').textContent(),'生成成功，完整地址已显示。');
  await page.locator('#address-dialog-cancel').click();

  // --- Customer rows: status-gated actions ---------------------------------
  await page.locator('#customers-rows tr').first().waitFor();
  const row=(i)=>page.locator('#customers-rows tr').nth(i);
  assert.equal(await row(0).getByRole('button',{name:'查看地址'}).isEnabled(),true,'ready -> 查看地址 enabled');
  assert.equal(await row(0).getByRole('button',{name:'销毁地址'}).isEnabled(),true,'ready -> 销毁地址 enabled');
  assert.equal(await row(0).getByRole('button',{name:'延长 24h'}).isEnabled(),false,'ready -> 延长 disabled');
  assert.equal(await row(1).getByRole('button',{name:'销毁地址'}).isEnabled(),false,'used -> 销毁地址 disabled (redeemed history is immutable)');
  assert.equal(await row(2).getByRole('button',{name:'延长 24h'}).isEnabled(),true,'expired -> 延长 24h enabled');
  assert.equal(await row(3).getByRole('button',{name:'查看地址'}).isEnabled(),false,'not-recorded -> 查看地址 disabled');

  await row(0).getByRole('button',{name:'查看地址'}).click();
  await assertDialogVisible('address-dialog','When viewing a stored address');
  assert.equal(await page.locator('#address-dialog-title').textContent(),'已保存的订阅地址 · 客户甲');
  assert.equal(posts.filter(p=>p.api==='client-service/action').at(-1).body.action,'subscription-view');
  await page.locator('#address-dialog-cancel').click();

  // expire (guarded by window.confirm, auto-accepted above). Waiting on the
  // request, not on #message: expireAddress() awaits refresh(), which clears it.
  await Promise.all([
    page.waitForRequest(r=>r.url().includes('client-service/action')
      &&(r.postData()||'').includes('subscription-address-expire')),
    row(0).getByRole('button',{name:'销毁地址'}).click(),
  ]);
  assert.equal(posts.filter(p=>p.api==='client-service/action').at(-1).body.action,'subscription-address-expire');

  // --- Batch issuance + exports --------------------------------------------
  await page.locator('#batch-form [name=names]').fill('客户甲\n客户乙\n客户丙');
  await page.locator('#batch-sources input[name=source]').first().waitFor();
  await page.locator('#batch-sources input[name=source]').check();
  await page.getByRole('button',{name:'批量生成',exact:true}).click();
  await page.locator('#batch-results').waitFor({state:'visible'});
  assert.equal(await page.locator('#batch-rows tr').count(),3);
  assert.match(await page.locator('#batch-status').textContent(),/成功 2 · 失败 1/);
  const [batchTxt]=await Promise.all([
    page.waitForEvent('download'),
    page.locator('#batch-copy').click(),
  ]);
  const bs=await batchTxt.createReadStream();let btxt='';
  for await(const c of bs)btxt+=c.toString('utf8');
  assert.equal(btxt.trim().split('\n').length,2,'Batch .txt holds only the successful addresses');
  const [csv]=await Promise.all([page.waitForEvent('download'),page.locator('#batch-export').click()]);
  assert.match(csv.suggestedFilename(),/\.csv$/);

  // --- Access log -----------------------------------------------------------
  await page.locator('#access-query').click();
  await page.locator('#access-rows tr').first().waitFor();
  assert.match(await page.locator('#access-rows').textContent(),/10\.20\.31\.134/);
  assert.match(await page.locator('#access-count').textContent(),/共 1 条/);
  assert.equal(await page.locator('#access-prev').isDisabled(),true);

  // --- Layout at phone width ------------------------------------------------
  for(const width of [1440,1024,390]){
    await page.setViewportSize({width,height:844});
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1),true,
      'No horizontal page overflow at '+width+'px');
    await row(1).getByRole('button',{name:'查看地址'}).click();
    await assertDialogVisible('address-dialog','Address dialog at '+width+'px');
    const box=await page.locator('#address-dialog').boundingBox();
    assert.ok(box.x>=0&&box.y>=0&&box.x+box.width<=width+1,'Dialog stays inside '+width+'px viewport');
    await page.keyboard.press('Escape');
    await page.waitForFunction(()=>!document.getElementById('address-dialog').open);
  }

  // --- No credential ever reaches browser storage ---------------------------
  const stored=await page.evaluate(()=>({local:Object.keys(localStorage),session:Object.keys(sessionStorage),
    dump:JSON.stringify(localStorage)+JSON.stringify(sessionStorage)}));
  assert.deepEqual(stored.local,[],'Nothing may be persisted in localStorage');
  assert.deepEqual(stored.session,['batchResults'],'sessionStorage holds only batch results');
  assert.ok(!/fixture-key-not-a-real-credential/.test(stored.dump),'No credential may be written to browser storage');

  // --- Logout ---------------------------------------------------------------
  await page.getByRole('button',{name:'退出登录',exact:true}).click();
  await page.locator('#login-card').waitFor({state:'visible'});
  assert.equal(await page.locator('#workspace').isVisible(),false);
  assert.deepEqual(errors,[]);
  console.log('Subscription admin fixtures passed: single login across the session, address dialog actually paints, .txt export, re-auth replay surfaces the issued address, status-gated row actions, batch + exports, access log, responsive layout, no credential in browser storage. No real requests.');
}finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
