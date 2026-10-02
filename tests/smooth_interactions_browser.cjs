// Pure local DOM fixtures: never enroll, contact a server or change networking.
const {chromium}=require('../frontend/node_modules/playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
(async()=>{
 const browser=await chromium.launch({headless:true});
 try {
  const page=await browser.newPage(),errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.setContent('<button id="action">保存</button><a data-workspace><button id="nav">导航</button></a><p id="content">原内容</p>');
  await page.addStyleTag({content:fs.readFileSync(path.resolve(__dirname,'../frontend/shared/smooth-interactions.css'),'utf8')});
  await page.addScriptTag({content:fs.readFileSync(path.resolve(__dirname,'../frontend/shared/smooth-interactions.js'),'utf8')});
  const feedback=await page.evaluate(()=>{
   const button=document.getElementById('action'),start=performance.now();
   const end=smoothUI.pending(button),second=smoothUI.pending(button);
   const elapsed=performance.now()-start;
   if(button.getAttribute('aria-busy')!=='true')throw Error('Missing immediate feedback');
   end();end();if(!button.classList.contains('sna-busy'))throw Error('Overlapping request lost feedback');
   second();if(button.hasAttribute('aria-busy'))throw Error('Feedback leaked after completion');
   const nav=document.getElementById('nav');smoothUI.pending(nav)();
   if(nav.classList.contains('sna-busy'))throw Error('Navigation treated as mutation');
   return elapsed;
  });
  const visible=await page.evaluate(()=>new Promise(resolve=>{
   const start=performance.now();smoothUI.change(()=>{document.getElementById('content').textContent='新内容';resolve(performance.now()-start);});
  }));
  assert.ok(visible<150,`Idle DOM commit too slow: ${visible}ms`);
  await page.emulateMedia({reducedMotion:'reduce'});
  assert.equal(await page.evaluate(()=>{let called=false;smoothUI.change(()=>{called=true;});return called;}),true);
  await page.emulateMedia({reducedMotion:'no-preference'});
  const watchdog=await page.evaluate(async()=>{
   const original=document.startViewTransition,callbacks=[];
   const never=new Promise(()=>{});let skipped=0,updates=0;
   document.startViewTransition=fn=>{callbacks.push(fn);return {ready:never,finished:never,updateCallbackDone:never,skipTransition(){skipped++;}};};
   const start=performance.now();
   smoothUI.change(()=>{updates++;document.getElementById('content').textContent='过时目标';});
   smoothUI.change(()=>{updates++;document.getElementById('content').textContent='最新目标';});
   await new Promise(resolve=>setTimeout(resolve,110));
   const elapsed=performance.now()-start;
   callbacks.forEach(fn=>fn());document.startViewTransition=original;
   return {elapsed,updates,skipped,text:document.getElementById('content').textContent};
  });
  assert.equal(watchdog.updates,1);assert.equal(watchdog.text,'最新目标');assert.ok(watchdog.skipped>=2);
  assert.equal(await page.evaluate(()=>{
   const original=document.startViewTransition;document.startViewTransition=undefined;
   let called=false;smoothUI.change(()=>{called=true;});document.startViewTransition=original;return called;
  }),true);
  const mutations=await page.evaluate(async()=>{
   const element=document.getElementById('content');let count=0;
   const observer=new MutationObserver(records=>count+=records.length);observer.observe(element,{childList:true});
   smoothUI.text(element,element.textContent);await Promise.resolve();observer.disconnect();return count;
  });
  assert.equal(mutations,0);assert.deepEqual(errors,[]);
  console.log(JSON.stringify({passed:true,feedbackMs:feedback,domCommitMs:visible,watchdogObservationMs:watchdog.elapsed,tests:['native transition','reduced motion','rapid latest target','80ms watchdog','once-only commit','no API fallback','overlapping pending cleanup','unchanged text avoids DOM rewrite']}));
 }finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
