'use strict';
// Official Tabler 1.5.1 components; existing commercial API and security contract.
let dashboard=null,serviceEdit=null,customerEdit=null,filteredCustomers=[],dashboardError=false;
let dashboardGeneration=0,backgroundRefreshing=false;
function invalidateDashboardRefresh(){dashboardGeneration++;}
function setSnapshotLock(){
  document.querySelectorAll('#generate-form button[type=submit],#proxy-form button[type=submit],#egress-form button[type=submit],#service-save,#customer-manage-save,#customer-action-submit,#egress-sources button,#egress-grants button,[data-snapshot-mutation]').forEach(b=>{b.dataset.locked=String(dashboardError);b.disabled=busy||dashboardError;});
}
function clearDashboard(){closeCustomerDialog();customerForm.reset();$('dashboard-lifecycle').value='active';$('dashboard-tag').replaceChildren();const o=addText($('dashboard-tag'),'option','全部标签');o.value='all';dashboard=null;serviceEdit=null;filteredCustomers=[];dashboardError=false;for(const id of ['dashboard-rows','dashboard-summary','service-detail-content','service-grants','dashboard-source'])$(id).replaceChildren();for(const id of ['service-title','service-detail-title','service-status','service-period-note','dashboard-updated','dashboard-note','dashboard-count'])$(id).textContent='';$('dashboard-search').value='';$('dashboard-filter').value='all';$('service-form').reset();$('service-form').hidden=true;$('service-detail').hidden=true;}
const formatBytes=v=>v===undefined?'未知':v===null?'无限':v>=1024**3?(v/1024**3).toFixed(2)+' GiB':(v/1024**2).toFixed(2)+' MiB';
const formatDate=v=>v===undefined?'未知':v===null?'永不过期':new Date(v*1000).toLocaleString();
const formatSpeed=v=>v==null?'不限速':(v/1e6).toLocaleString()+' Mbps';
const formatUsage=v=>v==null?'未知':formatBytes(v);
const rateText=v=>v==null?'未知':(v/1024**2).toFixed(2)+' MiB/s';
function addText(parent,tag,value){const el=document.createElement(tag);el.textContent=value;parent.append(el);return el;}
function localDateInput(s){const d=new Date(s*1000);return new Date(d.getTime()-d.getTimezoneOffset()*60000).toISOString().slice(0,16);}
async function loadDashboard(preloaded){
  const generation=++dashboardGeneration;
  try{const snapshot=preloaded||await (subscriptionReads?subscriptionReads.read('client-service/dashboard',{fresh:true}):api('client-service/dashboard'));if(generation!==dashboardGeneration)return;dashboard=snapshot;dashboardError=false;$('dashboard-export').dataset.locked='false';$('dashboard-export').disabled=busy;
    $('dashboard-updated').textContent='数据时间：'+formatDate(dashboard.generated_at)+' · 今日按 '+dashboard.accounting_timezone+' / '+dashboard.accounting_day+' 统计';$('dashboard-note').textContent=dashboard.measurement_note;
    $('dashboard-summary').replaceChildren();const sum=dashboard.summary;
    for(const [title,value] of [['已开户客户',sum.customers],['授权可入网',sum.usable],['有效租约',sum.active_leases],['今日消费',formatUsage(sum.today_bytes)],['累计消费',formatUsage(sum.total_bytes)]]){const card=document.createElement('div');card.className='card';const body=addText(card,'div','');body.className='card-body';addText(body,'div',title).className='subheader mb-2';addText(body,'div',value).className='h1';$('dashboard-summary').append(card);}
    const select=$('dashboard-source'),selected=select.value;select.replaceChildren();const all=addText(select,'option','全部源网');all.value='all';for(const s of commercialState.sources||[]){const o=addText(select,'option',s.name);o.value=s.id;}select.value=[...select.options].some(o=>o.value===selected)?selected:'all';
    const tagSelect=$('dashboard-tag'),tag=tagSelect.value;tagSelect.replaceChildren();for(const [value,title] of [['all','全部标签'],['untagged','未设置标签'],...[...new Set(dashboard.customers.flatMap(c=>c.tags||[]))].sort().map(t=>['tag:'+t,t])]){const o=addText(tagSelect,'option',title);o.value=value;}tagSelect.value=[...tagSelect.options].some(o=>o.value===tag)?tag:'all';renderDashboard();
  }catch(e){if(generation!==dashboardGeneration)return;dashboardError=true;$('dashboard-export').dataset.locked='true';$('dashboard-export').disabled=true;$('dashboard-updated').textContent='刷新失败：'+e.message+'；保留的旧数据不可用于修改，请先刷新。';renderDashboard();}
}
function renderDashboard(){
  const query=$('dashboard-search').value.trim().toLowerCase(),filter=$('dashboard-filter').value,source=$('dashboard-source').value,now=Date.now()/1000;
  filteredCustomers=(dashboard?.customers||[]).filter(c=>{
    if($('dashboard-lifecycle').value!=='all'&&(c.lifecycle||'active')!==$('dashboard-lifecycle').value)return false;
    const tag=$('dashboard-tag').value;if(tag==='untagged'&&(c.tags||[]).length||tag.startsWith('tag:')&&!(c.tags||[]).includes(tag.slice(4)))return false;
    if(query&&![c.id,c.display_name,c.notes||'',...(c.tags||[]),...c.grants.flatMap(g=>[g.name,g.source_name,g.endpoint])].join(' ').toLowerCase().includes(query))return false;
    if(source!=='all'&&!c.grants.some(g=>g.source_id===source))return false;
    return filter==='all'||filter==='usable'&&c.usable||filter==='unusable'&&!c.usable||filter==='leased'&&c.active_lease_count>0||filter==='quota'&&c.usage.remaining_bytes===0||filter==='expiring'&&c.grants.some(g=>g.enabled&&g.expires_at!=null&&g.expires_at>now&&g.expires_at<=now+7*86400)||filter==='stale'&&c.usage.measurement_status!=='fresh';
  });
  $('dashboard-count').textContent=`显示 ${filteredCustomers.length} / ${dashboard?.customers.length||0} 个客户`;const rows=document.createDocumentFragment();
  const statuses=new Map((commercialState.subscription_addresses||[]).map(s=>[s.customer_id,s]));
  const badge=(parent,text,tone)=>{const b=addText(parent,'span',text);b.className='badge bg-'+tone+'-lt';return b;};
  const meta=(parent,text)=>{const el=addText(parent,'span',text);el.className='cell-meta';return el;};
  for(const c of filteredCustomers){
    const row=document.createElement('tr');row.dataset.customerId=c.id;const u=c.usage,s=statuses.get(c.id);
    const identity=addText(row,'td','');addText(identity,'span',c.display_name).className='customer-name';meta(identity,c.id);
    if(c.lifecycle&&c.lifecycle!=='active')badge(identity,c.lifecycle==='archived'?'已归档':'回收站','secondary').classList.add('mt-2');
    for(const tag of c.tags||[])badge(identity,tag,'azure').classList.add('mt-2');
    if(c.notes){const d=addText(identity,'details','');addText(d,'summary','查看备注');addText(d,'p',c.notes).className='text-wrap-anywhere';}
    badge(identity,statusNames[s?.status]||'地址状态未知',s?.status==='ready'?'green':s?.status==='used'?'azure':'secondary').classList.add('mt-2');
    const state=addText(row,'td','');badge(state,c.usable?'可入网':'当前不可用',c.usable?'green':'red');
    if(c.reasons.length)meta(state,c.reasons.join('；'));
    meta(state,c.active_lease_count+' 个有效租约 · '+c.devices.filter(d=>d.enabled).length+'/'+c.plan.max_devices+' 个启用设备');
    if(u.measurement_status!=='fresh')badge(state,u.last_report_at==null?'计量未上报':'计量滞后','yellow').classList.add('mt-2');
    const grants=addText(row,'td','');
    for(const g of c.grants){const block=addText(grants,'div','');block.className='grant-summary';addText(block,'span',g.source_name).className='customer-name';badge(block,g.egress_mode==='physical'?'物理宽带':'源机代理',g.egress_mode==='physical'?'secondary':'azure');if(!g.enabled)badge(block,'线路停用','red');meta(block,g.endpoint);meta(block,'到期：'+formatDate(g.expires_at));if(g.reasons.length)meta(block,g.reasons.join('；'));}
    if(!c.grants.length)meta(grants,'无源网授权');
    const quota=addText(row,'td','');addText(quota,'span',formatUsage(u.used_bytes)).className='customer-name';meta(quota,'额度：'+formatBytes(u.quota_bytes));
    if(u.quota_bytes!=null&&u.quota_bytes>0&&u.used_bytes!=null){const percent=Math.max(0,Math.min(100,u.used_bytes/u.quota_bytes*100)),progress=addText(quota,'div','');progress.className='progress usage-progress';progress.setAttribute('role','progressbar');progress.setAttribute('aria-label','本账期已用额度');progress.setAttribute('aria-valuenow',String(Math.round(percent)));progress.setAttribute('aria-valuemin','0');progress.setAttribute('aria-valuemax','100');const bar=addText(progress,'div','');bar.className='progress-bar'+(percent>=100?' bg-red':percent>=80?' bg-yellow':'');bar.style.width=percent+'%';}
    meta(quota,'剩余：'+formatBytes(u.remaining_bytes));meta(quota,'账期结束：'+formatDate(u.period_end));
    const spend=addText(row,'td','');addText(spend,'span','今日：'+formatUsage(u.today_bytes)).className='cell-line';meta(spend,'累计：'+formatUsage(u.total_bytes));
    const actions=addText(row,'td',''),group=addText(actions,'div','');group.className='table-actions';
    for(const [title,fn,allowed,style] of [['修改服务',()=>editService(c),!c.lifecycle||c.lifecycle==='active','btn-outline-primary'],['标签 / 备注 / 归档',()=>manageCustomer(c),true,'btn-outline-secondary'],['详情 / 设备 / 变更记录',()=>showServiceDetail(c),true,'btn-outline-secondary'],['删除订阅',()=>deleteCustomer(c),c.lifecycle!=='deleted','btn-outline-danger'],['查看订阅地址',()=>selectCustomerAction(c,'subscription-view'),!!s?.available,'btn-outline-secondary'],['补发订阅地址',()=>selectCustomerAction(c,'subscription-reissue'),!!s?.can_reissue&&(!c.lifecycle||c.lifecycle==='active'),'btn-outline-secondary']]){
      const b=addText(group,'button',title);b.className='btn btn-sm '+style;b.type='button';b.dataset.locked=String(dashboardError||!allowed);b.disabled=busy||dashboardError||!allowed;b.onclick=fn;
    }
    rows.append(row);
  }
  if(!filteredCustomers.length){const row=document.createElement('tr'),cell=addText(row,'td',dashboardError&&!dashboard?'数据暂不可用，请刷新后查看客户':'没有符合条件的客户');cell.colSpan=6;cell.className='text-center text-secondary py-5';rows.append(row);}
  $('dashboard-rows').replaceChildren(rows);
  setSnapshotLock();

}
function editService(c){
  if(dashboardError)return;showWorkspace('customers');serviceEdit=c;const f=$('service-form');f.reset();f.hidden=false;$('service-title').textContent='修改服务 · '+c.display_name+' · '+c.id;$('service-status').textContent='';f.elements.display_name.value=c.display_name;f.elements.enabled.checked=c.enabled;f.elements.max_devices.value=c.plan.max_devices;
  for(const [input,flag,value,divisor] of [['quota_gib','unlimited_quota',c.plan.quota_bytes,1024**3],['download_mbps','unlimited_download',c.plan.download_bps,1e6],['upload_mbps','unlimited_upload',c.plan.upload_bps,1e6]]){f.elements[flag].checked=value===null;f.elements[input].value=value===null?'':Number((value/divisor).toFixed(3));}
  $('service-period-note').textContent='额度按原账期 '+c.plan.period_seconds/86400+' 天计算；延长到180天不改变账期，不清零已用。修改生成仅此客户使用的独立套餐。';const expires=c.grants.map(g=>g.expires_at);f.elements.never_expires.checked=expires.includes(null);f.elements.expires_at.value=expires.length&&expires.every(v=>v!=null)?localDateInput(Math.max(...expires)):'';
  $('service-grants').replaceChildren();for(const g of c.grants){const block=document.createElement('div');block.className='business-row';const label=document.createElement('label');label.className='source form-check';const input=document.createElement('input');input.type='checkbox';input.className='form-check-input';input.checked=g.enabled;input.dataset.grantId=g.id;label.append(input,document.createTextNode(g.source_name+' · '+g.id));block.append(label);const select=document.createElement('select');select.className='form-select';select.dataset.grantMode=g.id;select.setAttribute('aria-label',g.source_name+' 出口权限');for(const [mode,title] of [['physical','物理宽带'],['source_proxy','源机代理']]){const option=addText(select,'option',title);option.value=mode;option.disabled=mode==='source_proxy'&&!g.proxy_ready&&g.egress_mode!==mode;}select.value=g.egress_mode;block.append(select);addText(block,'p',g.endpoint+' · 原到期 '+formatDate(g.expires_at)+(g.proxy_ready?'':' · 源机代理未就绪'));$('service-grants').append(block);}
  const s=(commercialState.subscription_addresses||[]).find(s=>s.customer_id===c.id);$('restore-enrollment-label').hidden=!(s?.available&&s.used_at==null&&s.device_count<s.max_devices);syncServiceInputs();f.scrollIntoView({block:'start'});
}
function syncServiceInputs(){const f=$('service-form');for(const [input,flag] of [['quota_gib','unlimited_quota'],['download_mbps','unlimited_download'],['upload_mbps','unlimited_upload']]){f.elements[input].disabled=f.elements[flag].checked;f.elements[input].required=!f.elements[flag].checked;}f.elements.expires_at.disabled=!f.elements.change_expiry.checked||f.elements.never_expires.checked;f.elements.expires_at.required=f.elements.change_expiry.checked&&!f.elements.never_expires.checked;}
for(const name of ['unlimited_quota','unlimited_download','unlimited_upload','change_expiry','never_expires'])$('service-form').elements[name].onchange=syncServiceInputs;
document.querySelectorAll('[data-service-never]').forEach(b=>b.onclick=()=>{const f=$('service-form');f.elements.change_expiry.checked=true;f.elements.never_expires.checked=true;syncServiceInputs();});
document.querySelectorAll('[data-service-days]').forEach(b=>b.onclick=()=>{const f=$('service-form');f.elements.change_expiry.checked=true;f.elements.never_expires.checked=false;f.elements.expires_at.value=localDateInput(Date.now()/1000+Number(b.dataset.serviceDays)*86400);syncServiceInputs();});
$('service-cancel').onclick=()=>{serviceEdit=null;$('service-form').hidden=true;$('service-form').elements.confirmation.value='';};
document.querySelectorAll('[data-close-service]').forEach(button=>button.onclick=()=> $('service-cancel').click());
$('service-form').onsubmit=event=>{event.preventDefault();run(async()=>{try{
  if(!serviceEdit||dashboardError)throw new Error('请刷新后重新选择客户。');const f=event.target,c=serviceEdit;
  const service={expected_version:c.version,expected_revision:c.revision,display_name:f.elements.display_name.value,enabled:f.elements.enabled.checked,max_devices:Number(f.elements.max_devices.value),quota_bytes:f.elements.unlimited_quota.checked?null:Math.round(Number(f.elements.quota_gib.value)*1024**3),download_bps:inputSpeed(f.elements.download_mbps.value,f.elements.unlimited_download.checked),upload_bps:inputSpeed(f.elements.upload_mbps.value,f.elements.unlimited_upload.checked),grants:[...document.querySelectorAll('[data-grant-id]')].map(i=>({id:i.dataset.grantId,enabled:i.checked,egress_mode:[...document.querySelectorAll('[data-grant-mode]')].find(s=>s.dataset.grantMode===i.dataset.grantId).value}))};
  if(f.elements.change_expiry.checked)service.expires_at=f.elements.never_expires.checked?null:inputExpiry(f.elements.expires_at.value);
  if(!$('restore-enrollment-label').hidden&&f.elements.restore_enrollment.checked)service.restore_enrollment=true;
  const verified=await api('login',{username:'admin',password:f.elements.confirmation.value,key:f.elements.confirmation.value,remember:false});csrf=verified.csrf;
  const result=await api('client-service/action',{action:'subscription-update',id:c.id,service});serviceEdit=null;f.hidden=true;await refresh();message('服务已更新，原地址和已消费流量保留。'+(result.reconnect_required?'旧租约已撤销，客户需重新入网。':'客户刷新订阅可读取新权限。'));
}finally{event.target.elements.confirmation.value='';}},'service-status');};
function showServiceDetail(c){if(dashboardError)return;showWorkspace('customers');const panel=$('service-detail');panel.hidden=false;$('service-detail-title').textContent=c.display_name+' · 详情';const content=$('service-detail-content');content.replaceChildren();addText(content,'h4','服务与计量');addText(content,'p','服务：'+(c.enabled?'启用':'停用')+'\n下载上限：'+formatSpeed(c.plan.download_bps)+' · 上传上限：'+formatSpeed(c.plan.upload_bps)+'\n当前下载：'+rateText(c.usage.download_bytes_per_second)+' · 当前上传：'+rateText(c.usage.upload_bytes_per_second)+'\n最后计量：'+(c.usage.last_report_at==null?'未上报':formatDate(c.usage.last_report_at))+'\n计量状态：'+(c.usage.measurement_status==='fresh'?'近期上报':c.usage.measurement_status==='stale'?'滞后':'未上报'));addText(content,'h4','设备');for(const d of c.devices){const block=document.createElement('div');block.className='business-row';addText(block,'p',d.label+'\n'+d.id+' · '+(d.enabled?'启用':'停用')+'\n绑定：'+formatDate(d.created_at));const form=document.createElement('form'),label=addText(form,'label','管理员凭据 · '+(d.enabled?'停用':'恢复')+'此设备'),input=document.createElement('input');input.type='password';input.className='form-control';input.autocomplete='off';input.required=true;label.append(input);const button=addText(form,'button',d.enabled?'确认停用设备':'确认恢复设备');button.type='submit';button.className='btn btn-outline-danger mt-3';button.dataset.snapshotMutation='true';button.disabled=dashboardError;form.onsubmit=event=>{event.preventDefault();run(async()=>{try{const verified=await api('login',{username:'admin',password:input.value,key:input.value,remember:false});csrf=verified.csrf;await api('client-service/action',{action:'device-enable',id:d.id,enabled:!d.enabled});panel.hidden=true;await refresh();message('设备状态已更新，身份未删除。');}finally{input.value='';}});};block.append(form);content.append(block);}if(!c.devices.length)addText(content,'p','尚未绑定设备。');addText(content,'h4','有效租约');for(const l of c.leases)addText(content,'p',l.id+'\n设备：'+l.device_id+'\n线路：'+l.grant_id+'\n有效至：'+formatDate(l.expires_at));if(!c.leases.length)addText(content,'p','没有有效租约。');addText(content,'h4','最近12个账期');for(const p of c.usage_periods)addText(content,'p',formatDate(p.period_start)+' — '+formatDate(p.period_end)+'\n上行：'+formatBytes(p.rx_bytes)+' · 下行：'+formatBytes(p.tx_bytes));addText(content,'h4','最近10次服务变更');for(const h of c.history){const details=document.createElement('details');addText(details,'summary','版本 '+h.version+' · '+formatDate(h.changed_at));addText(details,'pre',JSON.stringify({之前:h.previous,之后:h.current},null,2));content.append(details);}if(!c.history.length)addText(content,'p','新版启用前的变更未在此记录。');panel.scrollIntoView({block:'start'});}
$('service-detail-close').onclick=()=>{$('service-detail').hidden=true;};
for(const id of ['dashboard-search','dashboard-filter','dashboard-source','dashboard-lifecycle','dashboard-tag'])$(id).addEventListener(id==='dashboard-search'?'input':'change',renderDashboard);
$('dashboard-refresh').onclick=()=>run(refresh);
$('dashboard-export').onclick=()=>{if(dashboardError||!dashboard)return;const escape=v=>'"'+String(v??'').replace(/^(\s*[=+@-])/,"'$&").replaceAll('"','""')+'"';const rows=[['客户','编号','可入网','原因','源网与出口','账期已用字节','额度字节（空=无限）','今日字节','累计字节','有效租约','最后计量'],...filteredCustomers.map(c=>[c.display_name,c.id,c.usable,c.reasons.join('；'),c.grants.map(g=>g.source_name+':'+g.egress_mode).join('；'),c.usage.used_bytes,c.plan.quota_bytes,c.usage.today_bytes,c.usage.total_bytes,c.active_lease_count,c.usage.last_report_at])];const url=URL.createObjectURL(new Blob(['\ufeff'+rows.map(r=>r.map(escape).join(',')).join('\r\n')],{type:'text/csv;charset=utf-8'}));const link=document.createElement('a');link.href=url;link.download='订阅用量-'+dashboard.accounting_day+'.csv';link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);};
const customerDialog=$('customer-manage-dialog'),customerForm=$('customer-manage-form');
function customerNotice(){const state=customerForm.elements.lifecycle.value;$('customer-manage-notice').textContent=state==='active'?(customerEdit?.lifecycle&&customerEdit.lifecycle!=='active'?'恢复只移回日常列表，服务仍停用。之后请在“修改服务”中明确启用；不续期，不清零流量。':'标签与备注不会修改网络权限、套餐或现有租约。'):state==='archived'?'归档将停用客户并撤销租约；原订阅地址、设备和历史用量全部保留。可从已归档分类恢复。':'删除至回收站将停用客户并撤销租约；保留账务历史和原订阅，允许恢复，不永久删除。';}
function manageCustomer(c){if(dashboardError)return;customerEdit=c;customerForm.reset();customerForm.elements.tags.value=(c.tags||[]).join('，');customerForm.elements.notes.value=c.notes||'';customerForm.elements.lifecycle.value=c.lifecycle||'active';$('customer-manage-title').textContent=c.display_name+' · '+c.id;$('customer-manage-status').textContent='';customerNotice();customerDialog.showModal();customerForm.elements.tags.focus({preventScroll:true});}
function deleteCustomer(c){if(dashboardError||c.lifecycle==='deleted')return;manageCustomer(c);customerForm.elements.lifecycle.value='deleted';$('customer-manage-title').textContent='删除订阅 · '+c.display_name+' · '+c.id;customerNotice();customerForm.elements.confirmation.focus({preventScroll:true});}
function closeCustomerDialog(){if(customerDialog.open)customerDialog.close();customerForm.elements.confirmation.value='';customerEdit=null;}
$('customer-manage-close').onclick=closeCustomerDialog;
customerDialog.addEventListener('cancel',e=>{e.preventDefault();if(!busy)closeCustomerDialog();});
customerDialog.addEventListener('close',()=>{if(!customerDialog.open){customerForm.elements.confirmation.value='';customerEdit=null;}});
customerForm.elements.lifecycle.onchange=customerNotice;
customerForm.onsubmit=e=>{e.preventDefault();run(async()=>{try{if(!customerEdit)throw Error('请先选择客户');const c=customerEdit,service={expected_version:c.version,expected_revision:c.revision,tags:customerForm.elements.tags.value.split(/[,，]/).map(t=>t.trim()).filter(Boolean),notes:customerForm.elements.notes.value,lifecycle:customerForm.elements.lifecycle.value};const verified=await api('login',{username:'admin',password:customerForm.elements.confirmation.value,key:customerForm.elements.confirmation.value,remember:false});csrf=verified.csrf;await api('client-service/action',{action:'subscription-update',id:c.id,service});closeCustomerDialog();await refresh();message('客户资料与状态已保存。历史用量、设备和原订阅地址保留。');}finally{customerForm.elements.confirmation.value='';}},'customer-manage-status');};
async function refreshDashboardInBackground(){
  if(busy||backgroundRefreshing||serviceEdit||customerEdit||addressDialog.open||!authenticated||document.hidden)return;
  backgroundRefreshing=true;
  try{await loadDashboard();}finally{backgroundRefreshing=false;}
}
setInterval(refreshDashboardInBackground,15000);
run(refresh);
