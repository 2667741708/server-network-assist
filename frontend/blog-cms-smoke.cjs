const { chromium } = require('playwright');
const fs = require('node:fs/promises');
const path = require('node:path');
const http = require('node:http');
const net = require('node:net');
const { spawn, execFileSync } = require('node:child_process');
const assert = require('node:assert/strict');
const { createHash } = require('node:crypto');
const root = path.resolve(__dirname, '..');
const blog = path.join(root, 'site/blog');
const output = path.join(root, 'artifacts/blog-cms-check');
const content = path.join(output, `content-repo-${Date.now()}`);
const children = [];
const drafts = [];
let browser, server;
let renderRoot = path.join(blog, 'dist');
let proxyLog = '';
let adminEval = true;
const mime = {'.html':'text/html','.js':'text/javascript','.css':'text/css','.json':'application/json','.png':'image/png','.svg':'image/svg+xml','.woff2':'font/woff2'};
async function main() {
  await fs.mkdir(output,{recursive:true});
  // Never attach the smoke test to a developer's already running CMS repository.
  const portCheck = net.createServer();
  await new Promise((resolve,reject)=>{portCheck.once('error',reject);portCheck.listen(8081,'127.0.0.1',resolve);});
  await new Promise(resolve=>portCheck.close(resolve));
  await fs.cp(path.join(blog,'src/content'),path.join(content,'site/blog/src/content'),{recursive:true});
  await fs.mkdir(path.join(content,'site/blog/public/images/uploads'),{recursive:true});
  for (const args of [['init','-b','main'],['config','user.name','Blog verification'],['config','user.email','blog-check@example.invalid'],['add','.'],['commit','--allow-empty','-m','Isolated CMS fixture']]) execFileSync('git',args,{cwd:content,stdio:'pipe'});
  const proxy = spawn(process.execPath,[path.join(blog,'node_modules/decap-server/dist/index.js')],{cwd:content,env:{...process.env,MODE:'git',GIT_REPO_DIRECTORY:content,PORT:'8081',BIND_HOST:'127.0.0.1'}});
  children.push(proxy);
  proxy.stdout.on('data',d=>proxyLog+=d); proxy.stderr.on('data',d=>proxyLog+=d);
  server = http.createServer(async(req,res)=>{
    try {
      let rel = decodeURIComponent(new URL(req.url,'http://localhost').pathname).replace(/^\/projects\/?/,'');
      if(!path.extname(rel)) rel=rel.replace(/\/?$/,'/')+'index.html';
      const base = rel.startsWith('images/uploads/') ? path.join(content,'site/blog/public') : rel.startsWith('admin/') ? path.join(blog,'public') : renderRoot;
      const filename=path.resolve(base,rel);
      if(!filename.startsWith(base+path.sep)) throw Error('Outside site');
      let bytes;
      try { bytes = await fs.readFile(filename); }
      catch(error) {
        if(!rel.startsWith('images/uploads/')) throw error;
        bytes = execFileSync('git',['show',`cms/tutorials/cms-verification-draft:site/blog/public/${rel}`],{cwd:content,stdio:['ignore','pipe','ignore']});
      }
      res.setHeader('Content-Type',mime[path.extname(filename)]||'application/octet-stream');
      if(path.extname(filename)==='.html') {
        const hashes=[...bytes.toString().matchAll(/<script\b[^>]*>([\s\S]*?)<\/script>/g)].filter(m=>m[1].trim()).map(m=>`'sha256-${createHash('sha256').update(m[1]).digest('base64')}'`).join(' ');
        res.setHeader('Content-Security-Policy',`default-src 'self'; script-src 'self' ${rel.startsWith('admin/') && adminEval ? "'unsafe-eval'" : "'wasm-unsafe-eval'"} ${hashes}; style-src 'self' 'unsafe-inline'; connect-src 'self' blob: http://localhost:8081 http://127.0.0.1:8081 https://api.github.com https://github.com; img-src 'self' data: blob: https:; font-src 'self' data:; frame-src 'self' blob:; worker-src 'self' blob:; frame-ancestors 'none'`);
      }
      res.end(bytes);
    } catch {res.statusCode=404;res.end('Not found');}
  });
  await new Promise((resolve,reject)=>{server.once('error',reject);server.listen(9421,'127.0.0.1',resolve);});
  let ready = false;
  for(let n=0;n<100;n++){
    try { const response=await fetch('http://127.0.0.1:8081/api/v1',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action:'info'})}); if(response.ok){const info=await response.json();assert.equal(info.repo,path.basename(content));ready=true;break;} } catch(error) { if(error.code==='ERR_ASSERTION') throw error; }
    if(proxy.exitCode !== null) throw Error('CMS proxy exited: '+proxyLog);
    await new Promise(r=>setTimeout(r,200));
  }
  assert.ok(ready,'CMS proxy not ready: '+proxyLog);
  browser=await chromium.launch({headless:true});
  adminEval=false;
  const negative=await browser.newPage();
  const negativeErrors=[];
  negative.on('pageerror',error=>negativeErrors.push(error.message));
  negative.on('console',message=>{if(message.type()==='error')negativeErrors.push(message.text());});
  await negative.goto('http://127.0.0.1:9421/projects/admin/',{waitUntil:'networkidle'});
  await negative.waitForTimeout(700);
  assert.match(negativeErrors.join('\n')+'\n'+await negative.locator('body').innerText(),/unsafe-eval|Content Security Policy|EvalError/);
  await fs.writeFile(path.join(output,'csp-negative.json'),JSON.stringify({adminWithoutEvalFails:true,errors:negativeErrors},null,2));
  await negative.close();
  adminEval=true;
  const page=await browser.newPage({viewport:{width:1440,height:1000}});
  const errors=[]; page.on('pageerror',e=>errors.push(e.message));
  page.on('console',message=>{if(message.type()==='error' && /Content Security Policy|Refused to|unsafe-eval/i.test(message.text())) errors.push(message.text());});
  page.on('response', response=>{if(response.status()>=400) console.log('HTTP '+response.status()+' '+response.url());});
  page.on('console',message=>{if(message.type()==='error')console.log('BROWSER ERROR: '+message.text());});
  await page.goto('http://127.0.0.1:9421/projects/admin/',{waitUntil:'networkidle'});
  await page.getByRole('button',{name:/登录/}).click();
  await page.waitForTimeout(1000);
  await page.getByText('＋ 实操教程',{exact:true}).click();
  await page.waitForTimeout(700);
  await page.locator('input[id^="project-field"]').fill('server-network-assist');
  await page.getByRole('option',{name:'server-network-assist',exact:true}).click();
  await page.locator('input[id^="title-field"]').fill('CMS verification draft');
  await page.locator('textarea[id^="description-field"]').fill('验证真实草稿保存、图片与主题预览。');
  await page.locator('input[id^="pubDatetime-field"]').fill('2026-09-10T12:00');
  await page.getByText('选择图片',{exact:true}).click();
  await page.locator('input[type=file]').setInputFiles(path.join(blog,'public/images/windows-proxy-diagnosis.png'));
  await page.getByRole('button',{name:'选用已选中项目',exact:true}).click();
  await page.locator('[contenteditable=true]').click();
  await page.keyboard.press('Control+End');
  await page.keyboard.press('Enter');
  await page.keyboard.insertText('CMS真实浏览器草稿验证：仅在隔离仓库运行。');
  const persisted=page.waitForResponse(r=>r.url().endsWith('/api/v1')&&r.request().postDataJSON()?.action==='persistEntry');
  await page.getByRole('button',{name:'保存',exact:true}).click();
  assert.equal((await persisted).status(),200);
  await page.getByText('修改已保存',{exact:true}).waitFor();
  await page.waitForTimeout(500);
  const editorUrl=page.url();
  console.log('Saved editor URL: '+editorUrl);
  await page.goto('http://127.0.0.1:9421/projects/admin/#/collections/tutorials/entries/cms-verification-draft',{waitUntil:'networkidle'});
  await page.waitForFunction(()=>document.querySelector('input[id^="title-field"]')?.value==='CMS verification draft');
  assert.equal(await page.locator('input[id^="title-field"]').inputValue(),'CMS verification draft');
  assert.match(await page.locator('[contenteditable=true]').innerText(),/CMS真实浏览器草稿验证/);
  assert.equal((await page.request.get('http://127.0.0.1:9421/projects/images/uploads/windows-proxy-diagnosis.png')).status(),200);
  const branch='cms/tutorials/cms-verification-draft';
  const raw=execFileSync('git',['show',`${branch}:site/blog/src/content/posts/tutorials/cms-verification-draft.md`],{cwd:content,encoding:'utf8'});
  assert.match(raw,/draft: true/); assert.match(raw,/project: server-network-assist/); assert.match(raw,/ogImage:/);
  const mainFiles=execFileSync('git',['ls-tree','-r','--name-only','main'],{cwd:content,encoding:'utf8'});
  assert.doesNotMatch(mainFiles,/cms-verification-draft.md/);
  drafts.push({collection:'tutorials',slug:'cms-verification-draft',branch,raw});
  await page.screenshot({path:path.join(output,'cms-initial.png'),fullPage:true});
  for (const collection of ['introductions','retrospectives','experiments','explanations']) {
    const slug = `cms-verification-${collection}`;
    await page.goto(`http://127.0.0.1:9421/projects/admin/#/collections/${collection}/new`,{waitUntil:'networkidle'});
    await page.locator('input[id^="project-field"]').fill('server-network-assist');
    await page.getByRole('option',{name:'server-network-assist',exact:true}).click();
    await page.locator('input[id^="title-field"]').fill(slug);
    await page.locator('textarea[id^="description-field"]').fill('隔离验证各类正文结构与草稿保存。');
    await page.locator('input[id^="pubDatetime-field"]').fill('2026-09-10T12:00');
    assert.ok((await page.locator('[contenteditable=true]').innerText()).length>20,'Template body must be populated');
    const saved = page.waitForResponse(r=>r.url().endsWith('/api/v1')&&r.request().postDataJSON()?.action==='persistEntry');
    await page.getByRole('button',{name:'保存',exact:true}).click();
    assert.equal((await saved).status(),200);
    await page.getByText('修改已保存',{exact:true}).waitFor();
    await page.goto(`http://127.0.0.1:9421/projects/admin/#/collections/${collection}/entries/${slug}`,{waitUntil:'networkidle'});
    await page.waitForFunction(expected=>document.querySelector('input[id^="title-field"]')?.value===expected,slug);
    const draftBranch=`cms/${collection}/${slug}`;
    const markdown=execFileSync('git',['show',`${draftBranch}:site/blog/src/content/posts/${collection}/${slug}.md`],{cwd:content,encoding:'utf8'});
    assert.match(markdown,/draft: true/);
    assert.match(markdown,/project: server-network-assist/);
    drafts.push({collection,slug,branch:draftBranch,raw:markdown});
  }
  for (const width of [1440,390]) {
    await page.setViewportSize({width,height:1000});
    await page.goto('http://127.0.0.1:9421/projects/posts/tutorials/windows-proxy-diagnosis/',{waitUntil:'networkidle'});
    assert.match(await page.locator('h1').innerText(),/Windows/);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+1),false);
    assert.equal(await page.locator('article img').evaluateAll(nodes=>nodes.every(n=>n.complete&&n.naturalWidth>0)),true);
    const imageLink=await page.locator('article img').first().evaluate(image=>image.closest('a')?.href);
    if(imageLink){
      await Promise.all([page.waitForURL(imageLink,{waitUntil:'load'}),page.locator('article img').first().click()]);
      assert.equal(page.url(),imageLink);await page.goBack({waitUntil:'networkidle'});
    }
    else {await page.locator('article img').first().click();await page.getByRole('dialog').waitFor({state:'visible'});await page.keyboard.press('Escape');await page.getByRole('dialog').waitFor({state:'hidden'});}
    await page.screenshot({path:path.join(output,`article-${width}.png`),fullPage:true});
  }
  await page.goto('http://127.0.0.1:9421/projects/search/',{waitUntil:'networkidle'});
  await page.locator('.pagefind-ui__search-input').fill('Windows');
  await page.locator('.pagefind-ui__result').first().waitFor();
  assert.equal(errors.length,0,errors.join('\n'));
  assert.doesNotMatch(execFileSync('git',['ls-tree','-r','--name-only','main'],{cwd:content,encoding:'utf8'}),/cms-verification-/);
  await fs.writeFile(path.join(output,'result.json'),JSON.stringify({content,drafts,savedAndReopened:true,publishedToMain:false},null,2));
  console.log('PASS: five isolated Git drafts saved and reopened, image uploaded; main unchanged; production article responsive rendering, CSP and search.');
  await fs.writeFile(path.join(output,'proxy.log'),proxyLog);
}
main().catch(e=>{console.error(e);process.exitCode=1;}).finally(async()=>{await fs.writeFile(path.join(output,'proxy.log'),proxyLog);await browser?.close();server?.close();children.forEach(p=>p.kill());});
