const { chromium } = require('playwright');
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname,'../docs/projects');
const server = http.createServer((req,res)=>{
  let route = decodeURIComponent(new URL(req.url,'http://localhost').pathname);
  if(!route.startsWith('/projects/')){res.writeHead(404);res.end();return;}
  route=route.slice('/projects/'.length);if(!route||route.endsWith('/'))route+='index.html';
  const file=path.resolve(root,route);
  if(!file.startsWith(root+path.sep)||!fs.existsSync(file)||!fs.statSync(file).isFile()){res.writeHead(404);res.end();return;}
  const mime={'.html':'text/html; charset=utf-8','.css':'text/css','.js':'text/javascript','.png':'image/png','.svg':'image/svg+xml','.json':'application/json'};
  res.writeHead(200,{'Content-Type':mime[path.extname(file)]||'text/plain', 'Content-Security-Policy':"default-src 'self'; img-src 'self'; style-src 'self'; script-src 'self'; font-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'"});
  fs.createReadStream(file).pipe(res);
});
(async()=>{
 await new Promise(r=>server.listen(0,'127.0.0.1',r));
 const browser=await chromium.launch({headless:true});
 try{
  const page=await browser.newPage({viewport:{width:1440,height:1050}});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  page.on('console',message=>{if(message.type()==='error')errors.push(message.text().slice(0,250))});
  const base=`http://127.0.0.1:${server.address().port}/projects/`;
  const manifest=JSON.parse(fs.readFileSync(path.join(root,'manifest.json'),'utf8'));
  for(const route of ['',...manifest.paths]){
   await page.goto(base+route);
   const assets=await page.locator('a[href],img[src],link[href],script[src]').evaluateAll(nodes=>nodes.map(n=>n.href||n.src).filter(u=>u.startsWith(location.origin)));
   for(const url of new Set(assets))assert.equal((await page.request.get(url)).status(),200,url);
  }
  await page.goto(base);
  assert.equal(await page.locator('.project-card:visible').count(),manifest.projects);
  await page.locator('#search').fill('server-network-assist');
  assert.equal(await page.locator('.project-card:visible').count(),1);
  await page.reload();
  assert.equal(await page.locator('.project-card:visible').count(),1);
  await page.locator('#search').fill('missing-project-xyz');
  assert.equal(await page.locator('#empty').isVisible(),true);
  await page.locator('#search').fill('');
  await page.locator('#kind').selectOption('完整教程');
  assert.equal(await page.locator('.project-card:visible').count(),manifest.tutorials);
  await page.locator('#kind').selectOption('');
  const forks=await page.locator('.project-card[data-fork="true"]').count();
  await page.getByLabel('包含 Fork').uncheck();
  assert.equal(await page.locator('.project-card:visible').count(),manifest.projects-forks);
  await page.getByLabel('包含 Fork').check();
  for(const width of [1440,390,320]){
   await page.setViewportSize({width,height:1050});
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),true,'overflow '+width);
   assert.equal(await page.locator('.project-card .item-text').evaluateAll(nodes=>nodes.every(e=>e.scrollHeight<=e.clientHeight+1)),true,'truncated descriptions');
   await page.screenshot({path:path.resolve(__dirname,`../artifacts/project-hub-${width}.png`)});
  }
  await page.locator('#theme').click();
  assert.equal(await page.locator('html').evaluate(e=>e.classList.contains('dark')),true);
  await page.goto(base+'server-network-assist/');
  await page.getByLabel('同时共享源机器的 HTTP/HTTPS 代理',{exact:true}).check();
  assert.match(await page.locator('#route-exit').textContent(),/源 HTTP 代理/);
  assert.deepEqual(errors,[]);
  console.log(`PASS ${manifest.projects} pages, every local link/asset, search, saved filters, forks, theme and 320/390/1440 full-text layouts`);
 }finally{await browser.close();await new Promise(r=>server.close(r));}
})().catch(e=>{console.error(e);process.exitCode=1});
