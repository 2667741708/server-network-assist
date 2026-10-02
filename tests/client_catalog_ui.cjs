const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const elements = {}, dialogs = [];
function element(){return {children:[],dataset:{},textContent:'',style:{},append(...items){this.children.push(...items)},replaceChildren(...items){this.children=items},get outerHTML(){const escaped=this.textContent.replaceAll('&','&amp;').replaceAll('<','&lt;').replaceAll('>','&gt;');return `<div>${escaped}</div>`}}}
const context = vm.createContext({URLSearchParams,location:{hash:'',pathname:'/',origin:'http://127.0.0.1:12345'},history:{replaceState(){}},
  window:{addEventListener(){}},
  document:{getElementById(id){return elements[id]||(elements[id]=element())},createElement:element,querySelectorAll(){return []}},
  Framework7:function(){return {accordion:{open(){},close(){}},dialog:{create(config){dialogs.push(config);return {open(){}}}}}},
  fetch(){throw new Error('Unexpected real network request')}});
const source=fs.readFileSync('src/server_network_assist/client_ui/client.js','utf8').split("$('subscription-form').onsubmit =")[0];
vm.runInContext(source,context);
vm.runInContext(`state={online_service:{configured:true},online_catalog:{routes:[{id:'grant-test',name:'源网<img src=x>',endpoint:'10.20.32.13:51910',download_bps:null,upload_bps:null}],usage:{remaining_bytes:null,period_end:1792165490}},active:null,recovering:false};stateAvailable=true;showSourceCatalog();renderOnline(state.online_catalog.routes,state.online_catalog.usage);`,context);
assert.match(dialogs[0].content,/源网&lt;img src=x&gt;/);
assert.match(dialogs[0].content,/不限速/);
assert.match(dialogs[0].content,/尚未启动入网/);
assert.doesNotMatch(dialogs[0].content,/10\.20\.32\.13/);
assert.equal(vm.runInContext('speed(50000000)',context),'50 Mbps');
vm.runInContext(`let calls=[];api=async(path)=>{calls.push(path);return path==='state'?state:path==='online/subscription'?state.online_catalog:{}};`,context);
(async()=>{
  let pending=vm.runInContext('startBorrow()',context);
  const repeated=vm.runInContext('startBorrow()',context);
  await Promise.all([pending,repeated]);
  assert.equal(vm.runInContext("calls.filter(x=>x==='online/connect').length",context),1);
  vm.runInContext('state=null;stateAvailable=false;updateControls();',context);
  assert.equal(elements['leave-network'].disabled,false);
  assert.equal(elements['start-borrow'].disabled,true);
  vm.runInContext(`state={online_service:{configured:true},online_catalog:{routes:[],usage:{}},active:{kind:'online',line_id:'grant-test'},recovering:false};stateAvailable=true;updateControls();`,context);
  assert.equal(elements['leave-network'].disabled,false);
  assert.equal(elements['start-borrow'].disabled,true);
  vm.runInContext("pendingAction='subscribe';calls=[];updateControls();",context);
  assert.equal(elements['leave-network'].disabled,false);
  await vm.runInContext('leaveNetwork()',context);
  assert.equal(vm.runInContext('exitRequested',context),true);
  assert.equal(vm.runInContext('calls.length',context),0);
  vm.runInContext('finishAction()',context); await new Promise(resolve=>setImmediate(resolve));
  assert.equal(vm.runInContext("calls.filter(x=>x==='network/leave').length",context),1);
  console.log('Catalog escaping/privacy, paid labels, direct Start with duplicate suppression, unknown/offline exit and queued exit verified; no real network requests.');
})().catch(error=>{console.error(error);process.exitCode=1});
