// Test the standalone mock only. It cannot contact an actual backend.
const {chromium}=require('../frontend/node_modules/playwright');
const fs=require('node:fs');
const path=require('node:path');
const {pathToFileURL}=require('node:url');
const assert=require('node:assert/strict');
(async()=>{
  const root=path.resolve(__dirname,'..');
  const url=pathToFileURL(path.join(root,'artifacts/client-sol-ui-preview-20260917.html')).href;
  const browser=await chromium.launch({headless:true});
  try{
    const page=await browser.newPage({viewport:{width:580,height:820}}),errors=[],requests=[];
    page.on('pageerror',e=>errors.push(e.message));
    await page.route('**/*',route=>{
      const target=route.request().url();requests.push(target);
      assert.equal(target,url,'Standalone preview must have no external resource requests');
      return route.continue();
    });
    await page.goto(url,{waitUntil:'networkidle'});
    console.log(JSON.stringify({initial_errors:errors,notice:await page.locator('#notice').textContent(),paths:await page.evaluate(()=>window.__previewPaths)}));
    await page.waitForFunction(()=>document.getElementById('start-borrow').disabled===false);
    assert.match(await page.locator('main').innerText(),/全部为模拟数据/);
    assert.equal(await page.locator('input[name="source-node"][value="preview-source-2"]').isDisabled(),true);
    await page.locator('#start-borrow').click();
    await page.waitForFunction(()=>document.getElementById('status-title').textContent==='已入网');
    await page.locator('#leave-network').click();
    await page.waitForFunction(()=>document.getElementById('status-title').textContent==='当前未入网');
    assert.deepEqual(errors,[]);
    await page.locator('.page-content').evaluate(e=>e.scrollTop=0);
    await page.screenshot({path:path.join(root,'artifacts/client-sol-preview-580.png')});
    const result={passed:true,real_network_requests:false,client_executed:false,requests,body_font:await page.locator('body').evaluate(e=>getComputedStyle(e).fontFamily)};
    fs.writeFileSync(path.join(root,'artifacts/client-sol-preview-check.json'),JSON.stringify(result,null,2));
    console.log(JSON.stringify(result));
  }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
