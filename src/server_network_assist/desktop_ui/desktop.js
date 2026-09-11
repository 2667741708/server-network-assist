'use strict';
const $ = id => document.getElementById(id);
const root = document.documentElement;
try { root.classList.toggle('dark', localStorage.getItem('desktop-theme') === 'dark'); } catch {}
const app = new Framework7({el:'#app',theme:'ios',routes:[],panel:{visibleBreakpoint:900}});
const sidebar = app.panel.create({el:'#navigation',visibleBreakpoint:900});
app.views.create('.view-main',{router:false});
let token = '';
try { token = location.hash.slice(1) || sessionStorage.getItem('desktop-token') || ''; if(location.hash){sessionStorage.setItem('desktop-token',token);history.replaceState(null,'','/');} } catch { token=location.hash.slice(1); }
let pending=false, refreshing=false, fleet={hosts:[],credentials:[],profiles:[]}, hostDraft={}, profileDraft={}, diagnosis=null, chart=null;
let currentSection='overview', lastStatus=null;
const text = (id,value) => { $(id).textContent=value ?? ''; };
function node(tag,value,cls){const el=document.createElement(tag);if(value!==undefined)el.textContent=value;if(cls)el.className=cls;return el;}
function bytes(n){if(!Number.isFinite(n))return '—';const units=['B','KB','MB','GB','TB'];let i=0;while(n>=1024&&i<units.length-1){n/=1024;i++;}return `${n.toFixed(i?1:0)} ${units[i]}`;}
function notice(message){$('notice').hidden=!message;text('notice',message);}
async function api(path,body){
  const controller=new AbortController(), timer=setTimeout(()=>controller.abort(),600000);
  try{const response=await fetch('/api/'+path,{method:body!==undefined?'POST':'GET',signal:controller.signal,headers:{'X-Desktop-Token':token,...(body!==undefined?{'Content-Type':'application/json'}:{})},...(body!==undefined?{body:JSON.stringify(body)}:{})});
    const value=await response.json();if(!response.ok)throw new Error(value.error||`请求失败 (${response.status})`);return value;
  }catch(e){if(e.name==='AbortError')throw new Error('请求超时。远端操作可能仍在执行，请刷新方案状态后再决定是否重试。');throw e;}finally{clearTimeout(timer);}
}
function confirmAction(title,description){text('confirm-title',title);text('confirm-text',description);const dialog=$('confirm-dialog');return new Promise(resolve=>{const done=answer=>{dialog.close();resolve(answer);};$('accept').onclick=()=>done(true);$('cancel').onclick=()=>done(false);dialog.oncancel=e=>{e.preventDefault();done(false);};dialog.showModal();});}
async function run(operation,confirmation){
  if(pending){notice('已有操作执行中，请等待结果。');return;}
  pending=true;
  try{if(confirmation&&!await confirmAction(...confirmation))return;
    root.setAttribute('aria-busy','true');notice('正在执行，请等待结果；远端部署可能需要数分钟。');
    await operation();
  }catch(e){notice(e.message);}finally{pending=false;root.removeAttribute('aria-busy');}
}
function navigate(section){
  currentSection=section;app.tab.show('#'+section);
  document.querySelectorAll('[data-section]').forEach(el=>{const selected=el.dataset.section===section;el.classList.toggle('item-selected',selected);if(selected){el.setAttribute('aria-current','page');text('page-title',el.textContent.trim());}else el.removeAttribute('aria-current');});
  if(innerWidth<900)sidebar.close();
  document.querySelector('.view-main .page-content').scrollTop=0;
  if(section==='hosts-page'||section==='sharing')loadFleet().catch(e=>notice(e.message));
  if(section==='proxy-page')loadProxy().catch(e=>notice(e.message));
  if(section==='diagnostics')loadDiagnostics().catch(e=>notice(e.message));
}
document.querySelectorAll('[data-section]').forEach(el=>el.onclick=e=>{e.preventDefault();navigate(el.dataset.section);});
document.querySelectorAll('[data-go]').forEach(el=>el.onclick=()=>navigate(el.dataset.go));
$('menu').onclick=()=>sidebar.open();
$('theme').onclick=()=>{const dark=root.classList.toggle('dark');try{localStorage.setItem('desktop-theme',dark?'dark':'light');}catch{}};

// Official Framework7 list/input markup, constructed with DOM text nodes for all external data.
function fields(container,definitions){const list=node('div',undefined,'list list-strong list-dividers field-list');const ul=node('ul');list.append(ul);$(container).replaceChildren(list);
  for(const field of definitions){const [id,label,type='text',initial='',info='']=field;const li=node('li',undefined,'item-content item-input');const inner=node('div',undefined,'item-inner');const labelEl=node('label',label,'item-title item-label');labelEl.htmlFor=id;const wrap=node('div',undefined,'item-input-wrap');let input;
    if(type==='checkboxes'){input=node('div',undefined,'check-options');}
    else if(type==='textarea'){input=node('textarea');input.rows=3;input.value=initial;}
    else if(type==='select'){input=node('select');}
    else {input=node('input');input.type=type;if(type==='checkbox')input.checked=!!initial;else input.value=initial;}
    input.id=id;if(type==='password')input.autocomplete='new-password';if(type==='number'){input.min='1';input.max='65535';}wrap.append(input);inner.append(labelEl,wrap);if(info)inner.append(node('div',info,'item-input-info'));li.append(inner);ul.append(li);
  }
}
function options(id,items,blank='请选择'){const input=$(id),value=input.value;input.replaceChildren();if(blank!==null){const option=node('option',blank);option.value='';input.append(option);}for(const item of items){const option=node('option',item.name);option.value=item.id;input.append(option);}input.value=value;}
function fill(values,prefix){for(const [key,value]of Object.entries(values)){const el=$(prefix+key);if(!el)continue;if(el.type==='checkbox')el.checked=!!value;else if(Array.isArray(value))el.value=value.join('\n');else el.value=value??'';}}
function value(id){return $(id).value.trim();}
function list(id,items,render,empty){const ul=$(id).querySelector('ul');ul.replaceChildren();if(!items.length){ul.append(node('li',empty,'item-content'));return;}for(const item of items)ul.append(render(item));}
function listItem(title,details,onClick,buttonText='编辑'){const li=node('li');const content=node('div',undefined,'item-content');const inner=node('div',undefined,'item-inner');const main=node('div',undefined,'item-title');main.append(node('strong',title));for(const detail of details.filter(Boolean))main.append(node('div',detail,'item-text'));inner.append(main);if(onClick){const b=node('button',buttonText,'button button-outline');b.type='button';b.onclick=onClick;inner.append(b);}content.append(inner);li.append(content);return li;}
function card(title,detail){const article=node('article',undefined,'card');article.append(node('div',title,'card-header'));article.append(node('div',detail,'card-content card-content-padding'));return article;}
function hostName(id){return fleet.hosts.find(h=>h.id===id)?.name||id||'未选择';}
const states={disabled:'未启用',enabling:'启用中',enabled:'共享中',disabling:'恢复中',error:'异常'};

fields('host-fields',[
 ['host-name','显示名称'],['host-address','地址或域名'],['host-port','SSH 端口','number',22],['host-username','SSH 账号'],['host-credential_id','凭据','select'],['host-jump_id','跳板机','select'],['host-group','分组'],['host-host_key','SSH 主机公钥','textarea']
]);
fields('credential-fields',[
 ['credential-name','凭据名称'],['credential-kind','凭据类型','select'],['credential-password','SSH 密码','password'],['credential-key','SSH 私钥内容','textarea'],['credential-passphrase','私钥口令（可选）','password']
]);
options('credential-kind',[{id:'password',name:'密码'},{id:'key',name:'SSH 私钥'}],null);$('credential-kind').value='password';
function credentialKind(){const key=value('credential-kind')==='key';$('credential-password').closest('li').hidden=key;$('credential-key').closest('li').hidden=!key;$('credential-passphrase').closest('li').hidden=!key;}
$('credential-kind').onchange=credentialKind;credentialKind();
fields('profile-fields',[
 ['profile-name','方案名称'],['profile-gateway_id','出口机','select'],['profile-client_ids','客户端（可多选）','checkboxes'],['profile-port','出口 UDP 端口','number',51919],['profile-endpoint','客户端可达的出口地址','text','','留空时使用出口机地址'],['profile-tunnel_cidr','隧道网段（可留空自动分配）'],['profile-preserve_routes','仍走原网络的 CIDR','textarea','','每行一项，或使用空格、逗号分隔'],['profile-proxy_mode','是否共享源机器代理','select'],['profile-proxy_host','源机器 HTTP / 混合代理 IPv4','text','127.0.0.1'],['profile-proxy_port','源代理端口','number',7897],['profile-maintenance','每分钟维护隧道，连续失败后恢复原网络','checkbox',true]
]);
options('profile-proxy_mode',[{id:'direct',name:'仅共享网络，不共享源代理'},{id:'share',name:'共享网络和源 HTTP/HTTPS 代理'}],null);
fields('proxy-fields',[['proxy-enabled','启用当前用户手动代理','checkbox'],['proxy-server','代理地址','text','','Windows 可用 host:port 或 http=host:port;https=host:port。Ubuntu 使用单一 host:port。'],['proxy-bypass','绕过地址','textarea','','Windows 用分号分隔；Ubuntu 以逗号或分号分隔。']]);

function editHost(host={}){hostDraft={id:'',port:22,terminal_enabled:true,favorite:false,...host};$('host-form').reset();fill(hostDraft,'host-');$('fingerprint-confirmed').checked=!!host.host_key;text('fingerprint','');text('host-title',host.id?'编辑主机':'添加主机');updateChoices();['inspect-host','test-host','delete-host'].forEach(id=>$(id).disabled=!host.id);}
function selectedClients(){return [...$('profile-client_ids').querySelectorAll('input:checked')].map(el=>el.value);}
function updateClients(selected=selectedClients()){
  const gateway=value('profile-gateway_id');$('profile-client_ids').replaceChildren();
  for(const host of fleet.hosts.filter(h=>h.id!==gateway)){const label=node('label');const input=node('input');input.type='checkbox';input.value=host.id;input.checked=selected.includes(host.id);input.onchange=updateSummary;label.append(input,node('span',`${host.name} · ${host.address}`));$('profile-client_ids').append(label);}
  if(!fleet.hosts.length)$('profile-client_ids').append(node('p','请先在“主机与凭据”添加出口机与客户端。'));updateSummary();
}
function updateSummary(){text('selected-hosts',`出口：${hostName(value('profile-gateway_id'))}；客户端：${selectedClients().map(hostName).join('、')||'未选择'}`);}
function proxyMode(){const share=value('profile-proxy_mode')==='share';$('profile-proxy_host').closest('li').hidden=!share;$('profile-proxy_port').closest('li').hidden=!share;text('share-explanation',share?'通过 WireGuard 转发源 HTTP / 混合代理端口，支持 HTTPS CONNECT。Windows 修改 SSH 用户代理；Ubuntu 修改新登录 shell、APT 和该用户 GNOME 会话，断开后恢复。源端口需无需账号密码。':'保留客户端当前代理设置，不复制源机器应用代理。源机器的 VPN / TUN 属于系统路由，此开关不会绕过或关闭它。');}
function editProfile(profile={}){profileDraft={id:'',name:'',gateway_id:'',client_ids:[],port:51919,endpoint:'',tunnel_cidr:'',preserve_routes:[],maintenance:true,proxy_mode:'direct',proxy_host:'127.0.0.1',proxy_port:7897,state:'disabled',...profile};fill(profileDraft,'profile-');updateClients(profileDraft.client_ids);proxyMode();text('profile-title',profile.id?'共享方案设置':'新建共享方案');syncProfileControls();}
function syncProfileControls(){const p=profileDraft;const locked=!!p.cleanup_pending||!['disabled','error'].includes(p.state);$('save-profile').disabled=locked;$('delete-profile').disabled=!p.id||locked;$('enable-profile').disabled=!p.id||locked;$('disable-profile').disabled=!p.id||(!p.cleanup_pending&&!['enabled','error','enabling','disabling'].includes(p.state));$('install-helper').disabled=locked;for(const el of $('profile-fields').querySelectorAll('input,select,textarea'))el.disabled=locked;text('profile-state',`${states[p.state]||p.state}${p.cleanup_pending?' · 恢复未完成，请重试断开恢复':''}${p.last_error?'\n'+p.last_error:''}`);}
function updateChoices(){options('host-credential_id',fleet.credentials);$('host-credential_id').value=hostDraft.credential_id||$('host-credential_id').value;options('host-jump_id',fleet.hosts.filter(h=>h.id!==hostDraft.id),'不使用跳板');$('host-jump_id').value=hostDraft.jump_id||$('host-jump_id').value;options('profile-gateway_id',fleet.hosts);updateClients();}
function renderFleet(){list('hosts',fleet.hosts,h=>listItem(h.name,[`${h.username}@${h.address}:${h.port}`,h.jump_id?`经 ${hostName(h.jump_id)}`:'直连',h.host_key?'已保存 SSH 公钥':'尚未固定 SSH 公钥'],()=>editHost(h)),'尚未添加主机。');list('credentials',fleet.credentials,c=>listItem(c.name,[c.kind==='key'?'SSH 私钥':'密码'],()=>run(async()=>{await api('fleet/credential/delete',{id:c.id});await loadFleet();notice('凭据已删除。');},['删除凭据？',`删除 ${c.name}。仍被主机使用时后台会拒绝。`]),'删除'),'尚未保存凭据。');list('profiles',fleet.profiles,p=>listItem(p.name,[`${hostName(p.gateway_id)} → ${p.client_ids.map(hostName).join('、')}`,states[p.state]||p.state,p.last_error],()=>editProfile(p)),'尚未创建共享方案。');updateChoices();}
async function loadFleet(){const values=await Promise.all([api('fleet/hosts'),api('fleet/credentials'),api('fleet/network')]);fleet={hosts:values[0].hosts,credentials:values[1].credentials,profiles:values[2].profiles};renderFleet();}
$('profile-gateway_id').onchange=()=>updateClients();$('profile-proxy_mode').onchange=proxyMode;
$('new-host').onclick=()=>editHost();$('new-profile').onclick=()=>editProfile();
$('host-host_key').oninput=()=>{$('fingerprint-confirmed').checked=false;};
$('host-form').onsubmit=e=>{e.preventDefault();run(async()=>{const host={...hostDraft};for(const key of ['name','address','username','credential_id','jump_id','group','host_key'])host[key]=value('host-'+key);host.port=Number(value('host-port'));if(!host.name||!host.address||!host.username)throw new Error('请填写主机名称、地址和 SSH 账号。');if(host.host_key&&!$('fingerprint-confirmed').checked)throw new Error('请先通过可信渠道核对 SSH 指纹 / 公钥，再勾选确认。');const result=await api('fleet/host/save',host);await loadFleet();editHost(result.host);notice('主机已保存。');});};
$('inspect-host').onclick=()=>run(async()=>{const result=await api('fleet/host/inspect',{id:hostDraft.id});$('host-host_key').value=result.host_key;$('fingerprint-confirmed').checked=false;text('fingerprint',`待核对指纹：${result.fingerprint}`);notice('指纹已读取，尚未信任。请通过服务器控制台等可信渠道核对后勾选并保存。');});
$('test-host').onclick=()=>run(async()=>{const result=await api('fleet/network/probe',{ids:[hostDraft.id]});renderProbes(result.results);notice(result.results.map(r=>`${r.name}: ${r.ssh?'SSH 成功':'SSH 失败'}\n${r.error||r.diagnosis||r.default_route||''}`).join('\n'));});
$('delete-host').onclick=()=>run(async()=>{await api('fleet/host/delete',{id:hostDraft.id});editHost();await loadFleet();notice('主机已删除。');},['删除主机？',`删除 ${hostDraft.name||''}。若仍被共享方案引用，后台会拒绝。`]);
$('credential-form').onsubmit=e=>{e.preventDefault();run(async()=>{const kind=value('credential-kind');const secret=kind==='key'?$('credential-key').value:$('credential-password').value;if(!value('credential-name')||!secret)throw new Error('请填写凭据名称和内容。');await api('fleet/credential/save',{name:value('credential-name'),kind,secret,passphrase:$('credential-passphrase').value});$('credential-form').reset();credentialKind();await loadFleet();notice('凭据已加密保存，输入内容已清空。');});};
function profilePayload(){return {...profileDraft,name:value('profile-name'),gateway_id:value('profile-gateway_id'),client_ids:selectedClients(),port:Number(value('profile-port')),endpoint:value('profile-endpoint'),tunnel_cidr:value('profile-tunnel_cidr'),preserve_routes:value('profile-preserve_routes'),maintenance:$('profile-maintenance').checked,proxy_mode:value('profile-proxy_mode'),proxy_host:value('profile-proxy_host'),proxy_port:Number(value('profile-proxy_port'))};}
$('profile-form').onsubmit=e=>{e.preventDefault();run(async()=>{const payload=profilePayload();if(!payload.name||!payload.gateway_id||!payload.client_ids.length)throw new Error('请填写方案名称，并选择一台出口机和至少一台客户端。');const result=await api('fleet/network/profile/save',payload);await loadFleet();editProfile(result.profile);notice('方案已保存，尚未自动启用。');});};
function renderProbes(results){$('probe-results').replaceChildren();for(const r of results)$('probe-results').append(card(`${r.name} · ${r.os||'系统未识别'}`,`SSH ${r.ssh?'成功':'失败'} · DNS ${r.dns?'成功':'失败'} · 公网 ${r.internet?'成功':'失败'} · 辅助程序 ${r.helper?'就绪':'未就绪'}\n${r.error||r.diagnosis||r.default_route||''}`));}
$('probe-all').onclick=()=>run(async()=>{if(!fleet.hosts.length)throw new Error('请先添加主机。');const result=await api('fleet/network/probe',{ids:fleet.hosts.map(h=>h.id)});renderProbes(result.results);notice('探测完成，查看各主机的实际结果。');});
$('install-helper').onclick=()=>run(async()=>{const ids=[value('profile-gateway_id'),...selectedClients()].filter(Boolean);if(ids.length<2)throw new Error('请先选择出口机和客户端。');await api('fleet/network/helper/install',{ids});notice('辅助程序安装请求已完成，请再次探测确认各主机能力。');},['安装远端辅助程序？','将在所选出口机与客户端安装网络辅助程序，需要远端管理员权限。请确认主机选择和指纹无误。']);
async function profileAction(kind){await run(async()=>{try{const result=await api('fleet/network/profile/'+kind,{id:profileDraft.id});await loadFleet();if(kind==='delete')editProfile();else editProfile(result.profile);await refresh(true);notice(kind==='enable'?'共享已启用并完成后端连通性检查。':kind==='disable'?'共享已断开并恢复原网络。':'方案已删除。');}catch(e){await loadFleet().catch(()=>{});const latest=fleet.profiles.find(p=>p.id===profileDraft.id);if(latest)editProfile(latest);throw e;}},kind==='enable'?['启用多机网络共享？',`出口：${hostName(profileDraft.gateway_id)}\n客户端：${(profileDraft.client_ids||[]).map(hostName).join('、')}\n将改变客户端公网路由${profileDraft.proxy_mode==='share'?'及用户代理':''}。复检失败会尝试回退，可能短暂中断连接。`]:kind==='disable'?['断开并恢复原网络？','将清理所选方案的隧道和临时路由，并恢复它更改的代理。相关下载和远程连接可能中断。']:['删除共享方案？','删除保存的方案；仍在运行或未清理完成的方案不能删除。']);}
$('enable-profile').onclick=()=>profileAction('enable');$('disable-profile').onclick=()=>profileAction('disable');$('delete-profile').onclick=()=>profileAction('delete');

function renderProxy(value,overwrite=false){const p=value.proxy;const supported=p.supported!==false;text('proxy-state',!supported||p.enabled==null?'无法读取':p.enabled?'已开启':'已关闭');text('proxy-address',p.server||'未设置手动代理地址');text('proxy-description',`${p.scope||'当前用户代理'}${p.pac?'。存在自动代理 PAC，关闭手动代理不会移除 PAC。':''}${p.error?'\n'+p.error:''}`);$('save-proxy').disabled=!supported;$('disable-proxy').disabled=!supported||!p.enabled;if(overwrite){$('proxy-enabled').checked=!!p.enabled;$('proxy-server').value=p.server||'';$('proxy-bypass').value=Array.isArray(p.bypass)?p.bypass.join(';'):p.bypass||'';}
  list('proxy-backups',value.backups||[],b=>{const li=listItem(new Date(b.created_at*1000).toLocaleString(),[b.id,`${b.platform}${b.compatible===false?' · 不兼容当前系统':''}`],()=>run(async()=>{const result=await api('proxy/restore',{id:b.id});renderProxy(result,true);notice('代理备份已恢复。');await refresh(true);},['恢复代理配置？',`将恢复备份 ${b.id}，并先备份当前设置。`]),'恢复');if(b.compatible===false)li.querySelector('button').disabled=true;return li;},'还没有代理配置备份。');
}
async function loadProxy(){renderProxy(await api('proxy'),true);}
$('load-proxy').onclick=()=>run(async()=>{await loadProxy();notice('代理和备份已重新读取。');});
$('proxy-form').onsubmit=e=>{e.preventDefault();run(async()=>{const result=await api('proxy/save',{enabled:$('proxy-enabled').checked,server:value('proxy-server'),bypass:value('proxy-bypass')});renderProxy(result,true);await refresh(true);notice('已备份原配置并保存代理，请检查系统应用联网结果。');},['保存当前用户代理？','将先备份当前配置，再应用编辑后的代理。错误的地址会影响使用系统代理的应用。']);};
$('disable-proxy').onclick=()=>run(async()=>{await api('action',{action:'disable-proxy'});await loadProxy();await refresh(true);notice('手动代理已关闭，原配置已备份。');},['关闭手动代理？','将备份当前配置，再关闭手动代理。自动代理 PAC 或代理软件重新接管的设置需要单独检查。']);
async function loadDiagnostics(){const result=await api('diagnostics');diagnosis=result;$('export-diagnostics').disabled=false;$('guidance').replaceChildren();for(const g of result.guidance||[])$('guidance').append(card(g.title,g.detail));list('diagnostic-events',result.events||[],e=>listItem(`${new Date(e.timestamp*1000).toLocaleString()} · ${e.action}`,[e.outcome,typeof e.detail==='object'?JSON.stringify(e.detail,null,2):e.detail]),'尚无本机操作记录。');try{const audit=await api('fleet/audit');list('fleet-events',audit.events||[],e=>listItem(`${new Date(e.created_at*1000).toLocaleString()} · ${e.action}`,[e.target,JSON.stringify(e.details||{})]),'尚无多机操作记录。');}catch(e){$('fleet-events').querySelector('ul').replaceChildren(node('li',e.message,'item-content'));}}
$('run-diagnostics').onclick=()=>run(async()=>{await loadDiagnostics();notice('诊断完成；请按真实结果选择恢复操作。');});
$('export-diagnostics').onclick=()=>{if(!diagnosis)return;const blob=new Blob([JSON.stringify(diagnosis,null,2)],{type:'application/json'});const url=URL.createObjectURL(blob);const a=node('a');a.href=url;a.download=`network-diagnostics-${Date.now()}.json`;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
$('check-updates').onclick=()=>run(async()=>{const result=await api('updates');text('update-state',result.error||`当前 ${result.current} · 最新 ${result.latest||'尚无发布版本'}${result.available?' · 有更新':' · 无可用更新'}`);$('update-links').replaceChildren();for(const link of [{name:'查看 GitHub 发布说明',url:result.release_url},...(result.assets||[])]){if(!link.url)continue;let url;try{url=new URL(link.url);}catch{continue;}if(url.protocol!=='https:'||url.hostname!=='github.com')continue;const a=node('a',link.name,'button button-outline external');a.href=url.href;a.target='_blank';a.rel='noopener noreferrer';$('update-links').append(a);}notice('更新检查已完成。');});

async function tunnelAction(kind,tunnel){await run(async()=>{await api('action',{action:kind,tunnel});await refresh(true);notice('隧道操作已完成，请核对最新连通性。');},kind==='disconnect'?['断开借网隧道？','本机将恢复原有网卡路由。依赖当前隧道的下载、远程连接或应用可能中断。']:undefined);}
function renderTraffic(traffic){if(!traffic||!traffic.available){text('traffic-rate','暂无可用遥测，不将未知速率显示为零。');$('traffic-chart').hidden=true;return;}const history=(traffic.history||[]).filter(p=>Number.isFinite(p.received_per_second)&&Number.isFinite(p.sent_per_second));text('traffic-rate',`↓ ${bytes(traffic.received_per_second)}/s　↑ ${bytes(traffic.sent_per_second)}/s`);$('traffic-chart').hidden=history.length<2;const config={el:'#traffic-chart',lineChart:true,axis:true,legend:true,tooltip:true,axisLabels:history.map(p=>new Date(p.timestamp*1000).toLocaleTimeString()),maxAxisLabels:4,datasets:[{label:'下载 B/s',color:'#007aff',values:history.map(p=>p.received_per_second)},{label:'上传 B/s',color:'#34c759',values:history.map(p=>p.sent_per_second)}]};if(history.length>=2){if(chart)chart.update(config);else chart=app.areaChart.create(config);}$('traffic-history').replaceChildren();for(const p of [...history].reverse()){const tr=node('tr');for(const v of [new Date(p.timestamp*1000).toLocaleTimeString(),bytes(p.received_per_second)+'/s',bytes(p.sent_per_second)+'/s'])tr.append(node('td',v));$('traffic-history').append(tr);}}
function render(s){lastStatus=s;text('hostname',s.hostname);text('platform',s.platform);text('version','v'+s.version);const ok=s.direct.ok&&s.system.ok;text('network-label',ok?'连接运行正常':'需要检查网络');text('network-title',ok?'网络已就绪':s.direct.ok?'系统代理可能异常':s.system.ok?'当前依赖代理联网':'暂时无法访问公网');text('network-description',ok?'原生网络与系统应用均可访问公网。':s.direct.ok?'直连成功但系统应用路径失败。请检查系统代理和 PAC。':'检查隧道、出口机或本机网络，并在诊断页查看详情。');$('network-dot').className='badge '+(ok?'color-green':'color-orange');text('direct',s.direct.ok?'可用':'不可用');text('system',s.system.ok?'可用':'不可用');text('latency',s.direct.ok?`HTTPS 响应 ${s.direct.milliseconds} ms`:'HTTPS 连通性检测未通过');const measured=s.tunnels.filter(t=>t.telemetry!==false&&Number.isFinite(t.received)&&Number.isFinite(t.sent)),received=measured.length?measured.reduce((n,t)=>n+t.received,0):null,sent=measured.length?measured.reduce((n,t)=>n+t.sent,0):null;text('traffic',bytes(received===null?null:received+sent));text('traffic-detail',`↓ ${bytes(received)}　↑ ${bytes(sent)}`);text('tunnel-count',`${s.tunnels.filter(t=>t.active).length} / ${s.tunnels.length} 已连接`);$('tunnels').replaceChildren();
  for(const tunnel of s.tunnels){const article=node('article',undefined,'tunnel card');const header=node('div',undefined,'card-header');header.append(node('h3',tunnel.name,'no-margin'),node('span',tunnel.active?'已连接':'已断开','badge'+(tunnel.active?' color-green':'')));const content=node('div',undefined,'card-content card-content-padding');content.append(node('p',tunnel.endpoint?`出口 ${tunnel.endpoint}`:'本机已有 WireGuard 配置','mono'));const age=tunnel.handshake?Math.max(0,Math.floor(s.timestamp-tunnel.handshake)):null;content.append(node('p',`${(tunnel.addresses||[]).join(' / ')}\n↓ ${bytes(tunnel.telemetry===false?null:tunnel.received)}　↑ ${bytes(tunnel.telemetry===false?null:tunnel.sent)}\n${age===null?'暂无握手信息':`最近握手 ${age} 秒前`}`,'mono'));const footer=node('div',undefined,'card-footer');const button=node('button',tunnel.active?'断开连接':'连接','button button-round '+(tunnel.active?'button-outline':'button-fill'));button.disabled=!s.elevated;button.onclick=()=>tunnelAction(tunnel.active?'disconnect':'connect',tunnel.name);footer.append(button);article.append(header,content,footer);$('tunnels').append(article);}
  if(!s.tunnels.length)$('tunnels').append(node('p','尚未发现已安装的隧道。可在“共享网络”创建方案。','block'));$('routes').replaceChildren();for(const route of s.routes){const row=node('div');row.append(node('strong',route.adapter),node('span',`网关 ${route.gateway} · metric ${route.metric}`));$('routes').append(row);}text('permission',s.elevated?'网络控制权限已就绪':'本机连接控制需要管理员权限');text('updated',`更新于 ${new Date(s.timestamp*1000).toLocaleTimeString()}`);renderTraffic(s.traffic);const b=s.background||{};text('background-state',b.running?'后台服务正在运行。':'后台能力状态未提供。');text('tray-state',b.tray_running?`系统托盘已运行；状态通知${b.notifications_enabled?'已开启':'未开启'}。`:b.tray_available?'系统支持托盘；当前后台启动方式未运行托盘。请从桌面快捷方式交互启动。':'当前会话无法使用系统托盘，可通过桌面快捷方式重新打开。');
}
async function refresh(required=false){if(!refreshing){$('refresh').disabled=true;refreshing=api('status').then(render).finally(()=>{refreshing=false;$('refresh').disabled=false;});}try{await refreshing;}catch(e){notice(e.message+'。若后台已关闭，请重新打开桌面快捷方式。');if(required)throw new Error('状态复检失败：'+e.message+'。请刷新确认，不要重复切换网络。');}}
$('refresh').onclick=()=>run(async()=>{await refresh(true);if(currentSection==='sharing'||currentSection==='hosts-page')await loadFleet();notice('状态已刷新。');});
editHost();editProfile();refresh();loadFleet().catch(e=>notice(e.message));setInterval(()=>{if(!document.hidden)refresh();},5000);
