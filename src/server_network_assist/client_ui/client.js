'use strict';
// BEGIN SNA SMOOTH INTERACTIONS (generated from frontend/shared; do not hand-edit copies)
const smoothUI = (() => {
  let generation = 0, activeTransition = null, activeUpdate = null;
  const pendingCounts = new WeakMap();
  const reduced = () => Boolean(window.matchMedia?.('(prefers-reduced-motion: reduce)').matches);
  function reveal(element) {
    if (!element?.animate || reduced() || element.hidden) return;
    element.getAnimations?.().forEach(animation => animation.cancel());
    element.animate([{opacity: 0, transform: 'translateY(4px)'}, {opacity: 1, transform: 'none'}],
      {duration: 140, easing: 'cubic-bezier(.2,.7,.2,1)'});
  }
  function change(update) {
    const version = ++generation;
    activeTransition?.skipTransition();
    let applied = false;
    const apply = () => {
      if (applied || version !== generation) return;
      applied = true;
      update();
    };
    activeUpdate = apply;
    if (!document.startViewTransition || reduced() || document.hidden) {
      apply(); return;
    }
    let transition;
    try { transition = document.startViewTransition(apply); }
    catch { apply(); return; }
    activeTransition = transition;
    // Snapshot preparation must never hold local navigation hostage.
    const watchdog = setTimeout(() => {
      if (!applied) { apply(); transition.skipTransition(); }
    }, 80);
    transition.ready.catch(() => {});
    transition.updateCallbackDone.catch(error => console.error('UI update failed', error));
    transition.finished.catch(() => {}).finally(() => {
      clearTimeout(watchdog);
      if (activeTransition === transition) { activeTransition = null; activeUpdate = null; }
    });
  }
  function settle() {
    // Security boundaries finish pending pure UI updates synchronously.
    activeUpdate?.(); ++generation; activeTransition?.skipTransition();
    activeUpdate = null; activeTransition = null;
  }
  function pending(element = document.activeElement) {
    let button = element?.closest?.('button');
    if (!button) button = element?.closest?.('form')?.querySelector('button[type="submit"]');
    if (!button || button.closest('[data-window],#window-controls,[data-workspace]')) return () => {};
    const previous = pendingCounts.get(button);
    const record = previous || {count: 0, aria: button.getAttribute('aria-busy')};
    record.count++; pendingCounts.set(button, record);
    button.classList.add('sna-busy'); button.setAttribute('aria-busy', 'true');
    let ended = false;
    return () => {
      if (ended) return; ended = true;
      if (--record.count) return;
      pendingCounts.delete(button); button.classList.remove('sna-busy');
      if (record.aria === null) button.removeAttribute('aria-busy');
      else button.setAttribute('aria-busy', record.aria);
    };
  }
  function text(element, value) {
    if (element.textContent !== String(value)) element.textContent = value;
  }
  if (window.MutationObserver && document.body) {
    new window.MutationObserver(records => {
      for (const {target, attributeName, oldValue} of records) {
        if (attributeName === 'hidden' && oldValue !== null && !target.hidden &&
            target.matches('#service-form,#service-detail,#egress-form,#result,#notice')) reveal(target);
        if (attributeName === 'open' && oldValue === null && target.open && target.matches('dialog,details')) {
          reveal(target.matches('dialog') ? target : target.querySelector(':scope > :not(summary)'));
        }
      }
    }).observe(document.body, {subtree: true, attributes: true, attributeOldValue: true, attributeFilter: ['hidden', 'open']});
  }
  return {change, settle, pending, reveal, text};
})();
// END SNA SMOOTH INTERACTIONS
const $ = id => document.getElementById(id);
const suppliedToken = new URLSearchParams(location.hash.slice(1)).get('token') || '';
let token = suppliedToken;
try {
  token = suppliedToken || sessionStorage.getItem('client-window-token') || '';
  if(suppliedToken) sessionStorage.setItem('client-window-token',suppliedToken);
  history.replaceState(history.state, '', location.pathname+location.search);
} catch { /* Keep the launch fragment if tab storage is unavailable. */ }
let state = null, eventSequence = 0, selectedGrant = '', routesSignature = '', pendingAction = null;
let refreshInFlight = false, subscriptionInitialized = false, stateAvailable = false, exitRequested = false;
let refreshTask = null, subscriptionsSignature = '';
let sourceStatusTask = null, sourceStatusKey = '', sourceStatusAt = 0, sourceStatuses = new Map();
function currentSourceKey(){return state?.online_service?.configured ? JSON.stringify([state.saved_subscriptions?.find(row=>row.selected)?.id,state.online_service?.customer_id,state.online_service?.provider]) : '';}
function renderSourceStatuses(){
  document.querySelectorAll('[data-source-status]').forEach(element=>{
    const row=sourceStatuses.get(element.dataset.sourceStatus);
    const mode=state?.online_catalog?.routes?.find(line=>line.id===element.dataset.sourceStatus)?.egress_mode;
    element.textContent=(mode==='physical'?'物理出口':mode==='source_proxy'?'源机代理出口':'出口由订阅授权')+' · '+
      (!row?'正在自动检查':row.available===false?'服务端报告不可用':row.reachable===true?'源网服务可达'+(row.latency_ms==null?'':' · TCP '+row.latency_ms+' ms'):row.reachable===false?'当前网络无法直达源网': '隧道状态待入网验证');
  });
}
function scheduleSourceStatus(force=false){
  const key=currentSourceKey();
  if(!key){sourceStatuses.clear();sourceStatusKey='';text('source-status-summary','添加订阅后自动查询源网状态');return;}
  if(sourceStatusKey!==key){sourceStatusKey=key;sourceStatuses.clear();sourceStatusAt=0;renderSourceStatuses();}
  if(sourceStatusTask||pendingAction||(!force&&Date.now()-sourceStatusAt<30000))return;
  sourceStatusAt=Date.now();text('source-status-summary','正在自动查询源网 · 不修改网络');
  const task=(async()=>{
    try{
      const value=await api('network/source-status');
      if(key!==currentSourceKey())return;
      sourceStatuses=new Map((value.sources||[]).map(row=>[row.id,row]));renderSourceStatuses();
      for(const field of ['network','service','source'])text('guard-'+field,value[field]);
      text('source-status-summary',(value.sources?.some(row=>row.reachable)?'源网服务可达':'源网状态：'+value.service)+' · '+new Date().toLocaleTimeString());
    }catch(error){if(key===currentSourceKey())text('source-status-summary','自动查询失败：'+error.message+' · 请检查网络或刷新订阅');}
  })();
  sourceStatusTask=task;
  task.finally(()=>{if(sourceStatusTask===task)sourceStatusTask=null;if(key!==currentSourceKey())scheduleSourceStatus(true);});
}
const app = new Framework7({el: '#app', theme: 'ios', name: '纯享入网'});
const clientReads=window.SmoothNavigation?.createReadCache({
  allowed:['online/subscription'],load:(path,signal)=>request(path,undefined,signal),maxAge:1500
});
const clientNavigation=window.SmoothNavigation?.create({
  views:['home','subscription-history','access-settings','notification-settings'],initial:'home',
  toggleToInitial:true,
  preserveScroll:true,animate:false,
  render:view=>{
    document.querySelectorAll('.accordion-item').forEach(item=>{
      if(item.id===view)app.accordion.open(item);else app.accordion.close(item);
      item.querySelector('[aria-expanded]')?.setAttribute('aria-expanded',String(item.id===view));
      const link=item.querySelector('[data-smooth-view]');
      if(link){if(item.id===view)link.setAttribute('aria-current','page');else link.removeAttribute('aria-current');}
    });
  },
  prefetch:async view=>{if(view==='subscription-history'&&state?.online_service?.configured&&!pendingAction)await clientReads?.read('online/subscription');},
  scrollElement:()=>document.querySelector('.page-content'),
  focusTarget:view=>$(view)?.querySelector('[data-smooth-view]')||$('subscription-settings'),
  onError:error=>notice(error.message,true)
});
clientNavigation?.restore();
window.addEventListener('pywebviewready', () => {
  const controls = window.pywebview?.api;
  if (!controls?.minimize || !controls?.toggle_maximize || !controls?.close) return;
  $('window-controls').hidden = false;
  for (const [id, method] of [['window-minimize', 'minimize'], ['window-maximize', 'toggle_maximize'], ['window-close', 'close']]) {
    $(id).addEventListener('click', () => run(async () => controls[method]()));
  }
});
function uiCopy(value) { return String(value).replaceAll('借网', '入网').replaceAll('退出组网', '退出入网'); }
function text(id, value) { smoothUI.text($(id), value == null ? '—' : uiCopy(value)); }
function bytes(value) {
  if (value == null) return '未知';
  const units = ['B', 'KB', 'MB', 'GB', 'TB']; let n = value, i = 0;
  while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
  return `${n.toFixed(i ? 1 : 0)} ${units[i]}`;
}
function speed(value) { return value === null ? '不限速' : value == null ? '未公布' : `${(value / 1000000).toLocaleString(undefined, {maximumFractionDigits: 2})} Mbps`; }
function date(value) { return value ? new Date(value * 1000).toLocaleDateString('zh-CN') : '未公布'; }
function notice(value, error = false) { const box = $('notice'); box.hidden = false; box.textContent = uiCopy(value); box.style.color = error ? 'var(--f7-color-red)' : ''; }
function api(path, body) {
  if(body!==undefined){clientReads?.invalidate();return request(path,body).finally(()=>clientReads?.invalidate());}
  // Availability feeds the connect controls: foreground checks are always fresh.
  if(body===undefined&&path==='online/subscription'&&clientReads)return clientReads.read(path,{fresh:true});
  return request(path,body);
}
async function request(path, body, signal) {
  const controller = new AbortController();
  const options = {headers: {'X-Client-Token': token}, signal: controller.signal};
  const cancel=()=>controller.abort();signal?.addEventListener('abort',cancel,{once:true});
  if(signal?.aborted)controller.abort();
  if (body !== undefined) { options.method = 'POST'; options.headers['Content-Type'] = 'application/json'; options.headers.Origin = location.origin; options.body = JSON.stringify(body); }
  const timer = setTimeout(() => controller.abort(), body === undefined ? 15000 : 120000);
  try {
    const response = await fetch('/api/' + path, options); const value = await response.json();
    if(response.status===401||response.status===403){clientReads?.invalidate();try{sessionStorage.removeItem('client-window-token');}catch{}}
    if (!response.ok) throw new Error(value.error || '操作失败'); return value;
  } catch (error) {
    if (error.name === 'AbortError') throw new Error('请求超时，请检查订阅服务。可点击退出入网恢复本客户端的配置。');
    throw error;
  } finally { clearTimeout(timer);signal?.removeEventListener('abort',cancel); }
}
async function run(fn) { const end = fn === refresh ? () => {} : smoothUI.pending(); try { await fn(); } catch (error) { notice(error.message, true); } finally { end(); } }
function dialog(message, title, buttons) {
  const body = document.createElement('div'); body.className = 'catalog-dialog-content'; body.textContent = message;
  return app.dialog.create({title, content: body.outerHTML, buttons, destroyOnClose: true}).open();
}
function showSourceCatalog() {
  const catalog = state.online_catalog || {}, usage = catalog.usage || {};
  const names = (catalog.routes || []).map((line, index) => `${index + 1}. ${line.name}\n出口：${line.egress_mode === 'physical' ? '源机物理宽带，需要代理时请开启自己的 Clash' : '源机代理出口'}\n下载：${speed(line.download_bps)} · 上传：${speed(line.upload_bps)}`).join('\n\n') || '目前没有授权节点。';
  dialog(`订阅读取成功，尚未启动入网。\n\n${names}\n\n剩余流量：${usage.remaining_bytes === null ? '不限额' : bytes(usage.remaining_bytes)}\n套餐到期：${date(usage.period_end)}\n\n选择节点后点击开始入网。`, '可用源网节点', [{text: '知道了', bold: true}]);
}
function isActive() { return Boolean(state?.active || state?.lines?.some(line => line.connected)); }
function updateControls() {
  const busy = Boolean(pendingAction), active = isActive(), recovering = Boolean(state?.recovering);
  const selected = state?.online_catalog?.routes?.find(line => line.id === selectedGrant);
  const reason = busy ? '正在处理操作，请稍候；仍可请求退网。' : !stateAvailable ? '正在读取客户端状态；读取失败时仍可退出入网。' : active ? '当前已经入网，可直接使用自己的 Clash Verge。' : recovering ? '原网络恢复未完成，请先重试退出入网。' : state?.online_catalog?.error ? '订阅暂不可达：' + state.online_catalog.error + '。请展开“我的订阅”，刷新当前订阅。' : !selected ? '请先读取订阅并选择一个获授权的源网节点。' : selected.available === false ? '所选源网暂不可用，请刷新订阅或选择其他节点。' : '';
  $('start-borrow').disabled = Boolean(reason);
  text('start-help', reason); $('start-help').hidden = !reason;
  $('start-borrow').textContent = pendingAction === 'connect' ? '正在入网…' : active ? '已入网' : '开始入网';
  // Local exit stays usable when catalog refresh or subscription service fails.
  $('leave-network').disabled = pendingAction === 'leave';
  $('leave-network').textContent = pendingAction === 'leave' ? '正在恢复原网络…' : exitRequested ? '已请求退网 · 等待当前操作完成' : '退出入网 · 恢复原网络';
  $('read-subscription').disabled = busy || !stateAvailable || recovering; $('subscription-url').disabled = busy || !stateAvailable || recovering;
  const configured = Boolean(state?.online_service?.configured);
  $('read-subscription').textContent = '添加订阅'; $('subscription-url').required = true;
  $('subscription-label').disabled = busy || !stateAvailable || recovering;
  $('refresh-subscription').disabled = busy || !stateAvailable || !configured;
  $('import-subscriptions').disabled = busy || !stateAvailable || recovering;
  text('subscription-help', configured ? '添加新订阅不会覆盖或断开当前订阅。' : '添加订阅后，手动开始入网。');
  $('remove').disabled = busy || !stateAvailable || recovering || !(state?.online_service?.configured || state?.subscription?.configured);
  document.querySelectorAll('[data-subscription-action]').forEach(button => { button.disabled = busy || !stateAvailable || recovering; });
  $('personal-login').disabled = busy;
  $('campus-logout').disabled = busy || !stateAvailable || active || recovering;
  $('campus-logout').textContent = pendingAction === 'campus-logout' ? '正在注销校园账号…' : '注销校园账号';
  document.querySelectorAll('input[name="source-node"]').forEach(input => {
    const line = state?.online_catalog?.routes?.find(row => row.id === input.value);
    input.disabled = busy || active || recovering || line?.available === false;
    input.checked = input.value === selectedGrant;
  });
}
function finishAction() {
  pendingAction = null; updateControls();
  if (exitRequested) { exitRequested = false; run(leaveNetwork); }
  else scheduleSourceStatus(true);
}
function renderPlan(usage = {}) {
  text('plan-data-state', state?.online_catalog?.error ? '服务离线 · 上次数据' : state?.online_service?.configured ? '服务端计量' : '未添加订阅');
  text('online-remaining', usage.remaining_bytes === null ? '不限额' : bytes(usage.remaining_bytes)); text('online-period', date(usage.period_end));
  const line = state?.online_catalog?.routes?.find(row => row.id === selectedGrant);
  text('plan-speed', line ? `${speed(line.download_bps)} / ${speed(line.upload_bps)}` : '—');
  $('usage-note').hidden = usage.used_bytes == null; text('usage-note', `已用 ${bytes(usage.used_bytes)} · 流量以服务端计量为准`);
}
function subscriptionType(row) {
  let modes = Array.isArray(row?.egress_modes) ? row.egress_modes : [];
  if (!modes.length && row?.selected) modes = (state?.online_catalog?.routes || []).map(route => route.egress_mode);
  modes = [...new Set(modes.filter(mode => mode === 'physical' || mode === 'source_proxy'))];
  if (modes.length > 1) return '物理出口 + 源机代理出口';
  if (modes[0] === 'physical') return '物理出口订阅';
  if (modes[0] === 'source_proxy') return '源机代理出口订阅';
  return '出口类型待首次读取';
}
function renderSubscriptions() {
  const rows = state.saved_subscriptions || [];
  const current = rows.find(row => row.selected);
  text('current-subscription-name', current ? `${current.label} · ${subscriptionType(current)}` : (state.online_service?.configured || state.subscription?.configured ? '已配置订阅 · 名称未提供' : '未添加'));
  const signature = JSON.stringify(rows.map(row => [row, subscriptionType(row)]));
  if (signature === subscriptionsSignature) return;
  subscriptionsSignature = signature;
  const list = document.createElement('ul'); list.className = 'list media-list';
  for (const row of rows) {
    const item = document.createElement('li'); item.className = 'block'; item.dataset.subscriptionId = row.id;
    const heading = document.createElement('div'); heading.className = 'subscription-heading';
    const name = document.createElement('strong'); name.className = 'wrap'; name.textContent = `${row.label}${row.selected ? ' · 当前订阅' : ''}`;
    const type = document.createElement('span'); type.className = 'badge subscription-type'; type.textContent = subscriptionType(row);
    heading.append(name, type);
    const details = document.createElement('p'); details.className = 'help wrap';
    details.textContent = `服务：${row.provider}\n客户编号：${row.customer_id}\n添加日期：${date(row.enrolled_at)}`;
    const actions = document.createElement('div'); actions.className = 'action-row';
    if (!row.selected) {
      const select = document.createElement('button'); select.className = 'button button-outline'; select.textContent = '切换到此订阅';
      select.dataset.subscriptionAction = 'select'; select.onclick = () => run(() => subscriptionAction('select', row)); actions.append(select);
    }
    const remove = document.createElement('button'); remove.className = 'button button-outline color-red'; remove.textContent = '移除';
    remove.dataset.subscriptionAction = 'remove'; remove.onclick = () => run(() => subscriptionAction('remove', row)); actions.append(remove);
    item.append(heading, details, actions); list.append(item);
  }
  if (!rows.length) { const item = document.createElement('li'); item.className = 'help wrap'; item.textContent = '还没有保存的在线订阅。可添加新地址，或导入旧版目录中保留的订阅。'; list.append(item); }
  $('saved-subscriptions').replaceChildren(list);
}
async function subscriptionAction(action, row) {
  if (pendingAction || !stateAvailable || state?.recovering) return;
  pendingAction = 'subscription-' + action; updateControls();
  try {
    const message = action === 'remove' ? `移除“${row.label}”？${row.selected ? '当前入网会先退出并恢复原网络。' : '当前订阅和入网连接保持。'}移除只删除本机保存的身份；重新添加需要管理员补发开户地址。`
      : `切换到“${row.label}”？当前入网会先退出并恢复原网络。原订阅保留，切换后需要手动开始入网。`;
    const confirmed = await new Promise(resolve => dialog(message, action === 'remove' ? '移除订阅' : '切换订阅', [
      {text:'取消',onClick:()=>resolve(false)}, {text:'确认',color:action==='remove'?'red':undefined,onClick:()=>resolve(true)},
    ]));
    if (!confirmed || exitRequested) return;
    const result = await api('online/subscriptions/' + action, {subscription_id: row.id});
    if (action === 'select' || row.selected) state.online_catalog = null;
    selectedGrant = ''; routesSignature = ''; await refresh(true);
    notice((action === 'remove' ? '订阅已移除。' : '订阅已切换，尚未自动入网。') + (result.release_error ? '旧租约未能在线释放，将按短期有效期到期。' : ''));
  } finally { finishAction(); }
}
function renderOnline(routes, usage) {
  if (isActive() && state.active?.kind === 'online') selectedGrant = state.active.line_id;
  if (!routes.some(row => row.id === selectedGrant)) selectedGrant = routes[0]?.id || '';
  const signature = JSON.stringify(routes.map(row => [row.id, row.name, row.download_bps, row.upload_bps, row.available,row.egress_mode]));
  if (signature !== routesSignature) {
    routesSignature = signature; const list = document.createElement('ul');
    for (const line of routes) {
      const item = document.createElement('li'), label = document.createElement('label'); label.className = 'item-radio item-content';
      const input = document.createElement('input'); input.type = 'radio'; input.name = 'source-node'; input.value = line.id;
      input.onchange = () => { selectedGrant = line.id; updateControls(); renderPlan(state.online_catalog?.usage); smoothUI.reveal($('plan-speed').closest('.plan-panel')); };
      const icon = document.createElement('i'); icon.className = 'icon icon-radio'; const inner = document.createElement('div'); inner.className = 'item-inner';
      const name = document.createElement('div'); name.className = 'item-title'; name.textContent = line.name;
      inner.append(name);
      const health=document.createElement('div');health.className='item-subtitle wrap';health.dataset.sourceStatus=line.id;inner.append(health);
      if (line.available === false) {
        const detail = document.createElement('div'); detail.className = 'item-subtitle';
        detail.textContent = '暂不可用 · 服务端未提供具体原因，请刷新订阅或联系管理员。';
        inner.append(detail);
      }
      label.append(input, icon, inner); item.append(label); list.append(item);
    }
    if (!routes.length) { const item = document.createElement('li'); item.className = 'block muted wrap'; item.textContent = state?.online_service?.configured ? '暂无获授权的源网节点，或订阅服务暂不可达。' : '读取订阅后显示授权节点'; list.append(item); }
    $('online-lines').replaceChildren(list);
  }
  renderSourceStatuses();renderPlan(usage); updateControls();
}
async function refresh(force = false) {
  if (refreshTask) {
    await refreshTask;
    if (!force) return;
  }
  if(force)clientReads?.invalidate();
  const task = refreshNow(); refreshTask = task;
  try { await task; } finally { if (refreshTask === task) refreshTask = null; }
}
async function refreshNow() {
  if (refreshInFlight) return; refreshInFlight = true;
  try {
    const previousCatalog = state?.online_catalog;
    state = await api('state'); stateAvailable = true; const configured = Boolean(state.online_service?.configured);
    renderSubscriptions();
    const traffic = state.traffic || {};
    text('traffic-today', bytes(traffic.today_bytes)); text('traffic-total', bytes(traffic.total_bytes));
    text('traffic-speed', `${bytes(traffic.download_bytes_per_second)} /s ↓ · ${bytes(traffic.upload_bytes_per_second)} /s ↑`);
    text('traffic-note', traffic.error || `本机此订阅隧道统计${traffic.measured_since ? '，自 ' + date(traffic.measured_since) + ' 起记录' : '，首次入网后开始记录'}；含上行和下行，不含校园内网直连。约 2 秒采样、5 秒刷新；跨日间隔归入采样当天。套餐扣费以服务端为准。`);
    if (state.saved_subscriptions_error) notice(state.saved_subscriptions_error, true);
    if (!$('desktop-notifications').disabled) {
      $('desktop-notifications').checked = state.preferences?.desktop_notifications !== false;
      text('notification-state', $('desktop-notifications').checked ? '开启' : '关闭');
    }
    text('identity', `${state.hostname} · v${state.version} · ${state.client_revision || '版本信息未提供'}`);
    text('status-title', state.recovering ? '原网络恢复未完成' : isActive() ? '已入网' : '当前未入网');
    text('status-detail', state.recovering ? '请重试退出入网，恢复期间不允许重新入网。' : state.error || (isActive() ? '校园内网直连 · 公网走源网出口' : configured ? '选择源网节点，点击开始入网。' : '先添加订阅，再选择源网。'));
    $('status-dot').className = 'status-dot ' + (state.recovering || state.error ? 'warn' : isActive() ? 'ok' : '');
    text('subscription-state', `${(state.saved_subscriptions || []).length} 个已保存`);
    if (configured) {
      try { state.online_catalog = await api('online/subscription'); }
      catch (error) { state.online_catalog = {routes: previousCatalog?.routes || [], usage: previousCatalog?.usage || {}, error:error.message}; notice('订阅服务暂不可达（保留上次数据）：' + error.message, true); }
    } else state.online_catalog = {routes: [], usage: {}};
    renderOnline(state.online_catalog.routes || [], state.online_catalog.usage || {});
    renderSubscriptions();
    if (!subscriptionInitialized) { subscriptionInitialized = true; }
  } catch (error) {
    stateAvailable = false; text('status-title', '当前状态暂无法读取');
    text('plan-data-state', '状态未知 · 上次数据');
    text('status-detail', '仍可点击退出入网，尝试恢复本客户端修改的网络配置。'); throw error;
  } finally { refreshInFlight = false; updateControls(); scheduleSourceStatus(); }
}
async function startBorrow() {
  if (pendingAction || !stateAvailable || isActive() || state?.recovering) return;
  const line = state?.online_catalog?.routes?.find(row => row.id === selectedGrant); if (!line) return;
  pendingAction = 'connect'; updateControls();
  try {
    if (exitRequested) return;
    await api('online/connect', {grant_id: line.id});
    if (!exitRequested) { await refresh(true); notice(isActive() ? '入网连接已建立。可以使用自己的 Clash Verge。' : '连接操作已完成，但当前未入网。请查看接入保护提示。', !isActive()); }
  } finally { finishAction(); }
}
async function leaveNetwork() {
  if (pendingAction) {
    if (pendingAction !== 'leave') { exitRequested = true; notice('已收到退网请求，当前操作结束后立即恢复原网络。'); updateControls(); }
    return;
  }
  pendingAction = 'leave'; updateControls();
  try { const value = await api('network/leave', {}); notice('已退出入网，本客户端修改的网络配置已恢复。' + (value.release_error ? ' 服务端暂不可达，租约将按短期有效期到期。' : '')); await refresh(); }
  finally { finishAction(); }
}
$('subscription-form').onsubmit = event => {
  event.preventDefault(); run(async () => {
    if (pendingAction || state?.recovering) throw new Error('请先完成当前操作或网络恢复，再添加订阅。');
    const url = $('subscription-url').value.trim();
    if (!url.startsWith('PURE1-') && !/^https?:\/\//.test(url)) throw new Error('请粘贴完整订阅网址或 PURE1- 开头的订阅码。');
    pendingAction = 'subscribe'; updateControls();
    try {
      const result = await api('online/subscriptions/add', {url, label: $('subscription-label').value.trim()});
      $('subscription-url').value = ''; $('subscription-label').value = '';
      if (!exitRequested) {
        await refresh();
        notice(result.reused ? '该订阅已经保存，无需再次注册。' : '订阅已保存；原订阅保留。');
        if ((state.saved_subscriptions || []).filter(row => row.selected).some(row => row.id === result.subscription_id) && !isActive()) showSourceCatalog();
      }
    }
    finally { finishAction(); }
  });
};
$('start-borrow').onclick = () => run(startBorrow); $('leave-network').onclick = () => run(leaveNetwork);
$('remove').onclick = () => run(async () => {
  const current = state?.saved_subscriptions?.find(row => row.selected);
  if (current) return subscriptionAction('remove', current);
  if (pendingAction || isActive() || state?.recovering) throw new Error('请先退网并完成恢复，再移除订阅。');
  const confirmed = await new Promise(resolve => dialog('移除后需要管理员提供新的订阅地址才能重新绑定。确定移除？', '移除订阅', [
    {text: '取消', onClick: () => resolve(false)}, {text: '移除', color: 'red', onClick: () => resolve(true)},
  ]));
  if (!confirmed) return; pendingAction = 'remove'; updateControls();
  try { await api(state.online_service?.configured ? 'online/remove' : 'subscription/remove', {}); if (!exitRequested) { notice('订阅已移除。'); await refresh(); } }
  finally { finishAction(); }
});
$('refresh-subscription').onclick = () => run(async () => {
  if (pendingAction) return; pendingAction = 'subscription-refresh'; updateControls();
  try { await refresh(true); if (state.online_catalog?.error) throw new Error(state.online_catalog.error); notice('当前订阅已刷新。'); }
  finally { finishAction(); }
});
$('import-subscriptions').onclick = () => run(async () => {
  if (pendingAction || state?.recovering) return; pendingAction = 'subscription-import'; updateControls();
  try {
    const result = await api('online/subscriptions/import', {}); await refresh(true);
    notice(`已导入 ${result.imported} 个旧版订阅，跳过 ${result.skipped} 个重复或无效记录。当前订阅未切换。旧文件已删除或服务端已撤销的订阅不能保证恢复使用。`);
  } finally { finishAction(); }
});
$('check-access').onclick = () => run(async () => {
  const button = $('check-access'); button.disabled = true;
  try {
    const value = await api('network/access'); for (const key of ['network', 'campus', 'service', 'source', 'account', 'message']) text('guard-' + key, value[key]);
    text('access-badge', value.ready ? '接入检查通过' : '请查看接入检查'); $('access-badge').className = 'badge ' + (value.ready ? 'color-green' : 'color-gray');
  } finally { button.disabled = false; }
});
$('scan-wifi').onclick = () => run(async () => {
  const button = $('scan-wifi'); button.disabled = true; text('wifi-message', '正在扫描附近 Wi-Fi…');
  try {
    const value = await api('network/wifiscan'); text('wifi-message', value.message);
    const list = document.createElement('ul');
    for (const row of value.networks || []) {
      const item = document.createElement('li'); item.className = 'wrap';
      item.textContent = `${row.ssid}${row.connected ? ' · 当前已连接' : ''}\n信号 ${row.signal}% · ${row.secure ? '无线链路加密' : '无线无密码（开放）'}${row.connectable ? '' : ' · 暂不可连接'}\n${row.interface}`;
      list.append(item);
    }
    $('wifi-networks').replaceChildren(list);
  } finally { button.disabled = false; }
});
$('personal-login').onclick = () => run(async () => {
  if (pendingAction) return; pendingAction = 'login'; updateControls();
  try { const value = await api('network/personal-login', {}); if (!exitRequested) { notice('已退网，正在打开个人校园网认证页面。'); window.open(value.portal_url, '_blank'); await refresh(); } }
  finally { finishAction(); }
});
$('campus-logout').onclick = () => run(async () => {
  if (pendingAction || isActive() || state?.recovering || !stateAvailable) return;
  pendingAction = 'campus-logout'; updateControls();
  try {
    const confirmed = await new Promise(resolve => dialog('注销当前电脑的校园公网认证？保持网络连接，当前公网访问可能中断。仅注销当前设备，不会自动入网。', '注销校园账号', [
      {text:'取消',onClick:()=>resolve(false)}, {text:'确认 Logout',color:'red',onClick:()=>resolve(true)},
    ]));
    if (!confirmed || exitRequested) return;
    $('campus-logout-message').hidden = false;
    text('campus-logout-message','正在注销并验证个人校园账号下线…');
    const result = await api('network/campus-logout',{confirmed:true});
    text('campus-logout-message',result.message); text('guard-account', result.after === 'offline' ? '个人账号未在线' : '认证状态需重新检查');
    await refresh();
  } catch (error) { text('campus-logout-message',error.message); throw error; }
  finally { finishAction(); }
});
$('desktop-notifications').onchange = () => run(async () => {
  const input = $('desktop-notifications'); input.disabled = true;
  try {
    const value = await api('preferences/notifications', {desktop_notifications: input.checked});
    state.preferences = value; text('notification-state', value.desktop_notifications ? '开启' : '关闭');
    notice(value.desktop_notifications ? '已开启桌面弹窗通知。' : '已关闭桌面弹窗通知；接入保护继续运行。');
  } catch (error) { input.checked = state?.preferences?.desktop_notifications !== false; throw error; }
  finally { input.disabled = false; }
});
function displayNetworkEvents() {
  if (!state) return; const value = (state.network_events || []).filter(row => row.sequence > eventSequence && row.error).at(-1);
  if (value) { const box = $('network-alert'); box.hidden = false; box.textContent = uiCopy(value.message); box.style.color = 'var(--f7-color-red)'; text('access-badge', '接入状态已变化'); $('access-badge').className = 'badge color-orange'; eventSequence = value.sequence; }
}
document.querySelectorAll('.accordion-item').forEach(item => {
  item.addEventListener('accordion:opened', () => item.querySelector('[aria-expanded]').setAttribute('aria-expanded', 'true'));
  item.addEventListener('accordion:closed', () => item.querySelector('[aria-expanded]').setAttribute('aria-expanded', 'false'));
});
run(refresh); setInterval(() => { if (!document.hidden) run(refresh); }, 5000); setInterval(displayNetworkEvents, 500);
