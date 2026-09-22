'use strict';

const $ = id => document.getElementById(id);
const root = document.documentElement;
try { root.classList.toggle('dark', localStorage.getItem('desktop-theme') === 'dark'); } catch {}

const app = new Framework7({el: '#app', theme: 'ios', routes: [], panel: {visibleBreakpoint: 900}});
const sidebar = app.panel.create({el: '#navigation', visibleBreakpoint: 900});
app.views.create('.view-main', {router: false});

let token = '';
try {
  token = location.hash.slice(1) || sessionStorage.getItem('desktop-token') || '';
  if (location.hash) {
    sessionStorage.setItem('desktop-token', token);
    history.replaceState(null, '', '/');
  }
} catch { token = location.hash.slice(1); }

let pending = false;
let refreshing = false;
let fleet = {hosts: [], credentials: [], profiles: []};
let hostDraft = {};
let profileDraft = {};
let diagnosis = null;
let chart = null;
let currentSection = 'overview';
let currentHostTab = 'hosts';
let lastStatus = null;

const text = (id, value) => {
  const element = $(id);
  if (element) element.textContent = value == null ? '' : String(value);
};
function node(tag, value, cls) {
  const element = document.createElement(tag);
  if (value !== undefined && value !== null) element.textContent = String(value);
  if (cls) element.className = cls;
  return element;
}
function bytes(value) {
  if (!Number.isFinite(value)) return '—';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let number = value;
  let index = 0;
  while (number >= 1024 && index < units.length - 1) { number /= 1024; index += 1; }
  return number.toFixed(index ? 1 : 0) + ' ' + units[index];
}
function formatTime(value) {
  return value ? new Date(value * 1000).toLocaleString() : '—';
}
function replaceStatusClasses(element, prefix, kind) {
  if (!element) return;
  element.classList.remove(prefix + '-ok', prefix + '-warning', prefix + '-danger', prefix + '-info');
  element.classList.add(prefix + '-' + (kind || 'info'));
}
function statusText(id, label, kind) {
  const element = $(id);
  if (!element) return;
  element.textContent = label;
  replaceStatusClasses(element, 'status-text', kind);
}
function statusBadge(id, label, kind) {
  const element = $(id);
  if (!element) return;
  element.textContent = label;
  element.className = 'status-badge status-' + (kind || 'info');
}
function statusBadgeNode(label, kind) {
  return node('span', label, 'status-badge status-' + (kind || 'info'));
}
function statusDot(id, kind) {
  const element = $(id);
  if (!element) return;
  element.className = 'status-dot status-' + (kind || 'info');
}
function notice(message) {
  const element = $('notice');
  if (!element) return;
  element.hidden = !message;
  text('notice', message || '');
}
async function api(path, body) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 600000);
  try {
    const response = await fetch('/api/' + path, {
      method: body !== undefined ? 'POST' : 'GET',
      signal: controller.signal,
      headers: {'X-Desktop-Token': token, ...(body !== undefined ? {'Content-Type': 'application/json'} : {})},
      ...(body !== undefined ? {body: JSON.stringify(body)} : {})
    });
    const value = await response.json();
    if (!response.ok) throw new Error(value.error || '请求失败 (' + response.status + ')');
    return value;
  } catch (error) {
    if (error.name === 'AbortError') throw new Error('请求超时。远端操作可能仍在执行，请刷新方案状态后再决定是否重试。');
    throw error;
  } finally { clearTimeout(timer); }
}
function confirmAction(title, description) {
  text('confirm-title', title);
  text('confirm-text', description);
  const dialog = $('confirm-dialog');
  return new Promise(resolve => {
    const done = answer => { dialog.close(); resolve(answer); };
    $('accept').onclick = () => done(true);
    $('cancel').onclick = () => done(false);
    dialog.oncancel = event => { event.preventDefault(); done(false); };
    dialog.showModal();
  });
}
async function run(operation, confirmation) {
  if (pending) { notice('已有操作执行中，请等待结果。'); return; }
  pending = true;
  try {
    if (confirmation && !await confirmAction(confirmation[0], confirmation[1])) return;
    root.setAttribute('aria-busy', 'true');
    notice('正在执行，请等待结果；远端部署可能需要数分钟。');
    await operation();
  } catch (error) {
    notice(error.message);
  } finally {
    pending = false;
    root.removeAttribute('aria-busy');
  }
}

function navigate(section) {
  currentSection = section;
  app.tab.show('#' + section);
  document.querySelectorAll('[data-section]').forEach(element => {
    const selected = element.dataset.section === section;
    element.classList.toggle('item-selected', selected);
    if (selected) {
      element.setAttribute('aria-current', 'page');
      text('page-title', element.querySelector('.item-title')?.textContent.trim() || element.textContent.trim());
    } else element.removeAttribute('aria-current');
  });
  if (innerWidth < 900) sidebar.close();
  const content = document.querySelector('.view-main .page-content');
  if (content) content.scrollTop = 0;
  if (section === 'hosts-page' || section === 'sharing') loadFleet().catch(error => notice(error.message));
  if (section === 'proxy-page') loadProxy().catch(error => notice(error.message));
  if (section === 'diagnostics') loadDiagnostics().catch(error => notice(error.message));
}
document.querySelectorAll('[data-section]').forEach(element => element.onclick = event => { event.preventDefault(); navigate(element.dataset.section); });
document.querySelectorAll('[data-go]').forEach(element => element.onclick = () => navigate(element.dataset.go));
$('menu').onclick = () => sidebar.open();
$('theme').onclick = () => {
  const dark = root.classList.toggle('dark');
  try { localStorage.setItem('desktop-theme', dark ? 'dark' : 'light'); } catch {}
};

function showHostTab(tab) {
  currentHostTab = tab;
  document.querySelectorAll('[data-hosts-tab]').forEach(element => {
    const selected = element.dataset.hostsTab === tab;
    element.classList.toggle('is-selected', selected);
    element.setAttribute('aria-selected', selected ? 'true' : 'false');
  });
  $('hosts-panel').hidden = tab !== 'hosts';
  $('credentials-panel').hidden = tab !== 'credentials';
}
document.querySelectorAll('[data-hosts-tab]').forEach(element => element.onclick = () => showHostTab(element.dataset.hostsTab));

// Framework7 input markup is kept for compatibility, but all external values are text nodes.
function fields(container, definitions) {
  const listElement = node('div', undefined, 'list list-strong list-dividers field-list');
  const ul = node('ul');
  listElement.append(ul);
  $(container).replaceChildren(listElement);
  for (const definition of definitions) {
    const [id, label, type = 'text', initial = '', info = ''] = definition;
    const li = node('li', undefined, 'item-content item-input');
    const inner = node('div', undefined, 'item-inner');
    const labelElement = node('label', label, 'item-title item-label');
    labelElement.htmlFor = id;
    const wrap = node('div', undefined, 'item-input-wrap');
    let input;
    if (type === 'checkboxes') input = node('div', undefined, 'check-options');
    else if (type === 'textarea') { input = node('textarea'); input.rows = 3; input.value = initial; }
    else if (type === 'select') input = node('select');
    else {
      input = node('input');
      input.type = type;
      if (type === 'checkbox') input.checked = !!initial;
      else input.value = initial;
    }
    input.id = id;
    if (type === 'password') input.autocomplete = 'new-password';
    if (type === 'number') { input.min = '1'; input.max = '65535'; input.inputMode = 'numeric'; }
    wrap.append(input);
    inner.append(labelElement, wrap);
    if (info) inner.append(node('div', info, 'item-input-info'));
    li.append(inner);
    ul.append(li);
  }
}
function options(id, items, blank = '请选择') {
  const input = $(id);
  if (!input) return;
  const previous = input.value;
  input.replaceChildren();
  if (blank !== null) { const option = node('option', blank); option.value = ''; input.append(option); }
  for (const item of items) { const option = node('option', item.name); option.value = item.id; input.append(option); }
  input.value = previous;
}
function fill(values, prefix) {
  for (const [key, value] of Object.entries(values || {})) {
    const element = $(prefix + key);
    if (!element || !('value' in element)) continue;
    if (element.type === 'checkbox') element.checked = !!value;
    else if (!Array.isArray(value)) element.value = value == null ? '' : value;
  }
}
function value(id) { return $(id)?.value.trim() || ''; }
function list(id, items, render, empty) {
  const rootElement = $(id);
  if (!rootElement) return;
  const target = rootElement.querySelector('ul') || rootElement;
  target.replaceChildren();
  if (!items.length) { target.append(node('li', empty, 'item-content empty-list-item')); return; }
  for (const item of items) target.append(render(item));
}
function listItem(title, details, onClick, buttonText = '编辑') {
  const li = node('li');
  const content = node('div', undefined, 'item-content');
  const inner = node('div', undefined, 'item-inner');
  const main = node('div', undefined, 'item-title');
  main.append(node('strong', title));
  for (const detail of details.filter(Boolean)) main.append(node('div', detail, 'item-text'));
  inner.append(main);
  if (onClick) {
    const button = node('button', buttonText, 'button button-outline');
    button.type = 'button';
    button.onclick = onClick;
    inner.append(button);
  }
  content.append(inner);
  li.append(content);
  return li;
}
function hostName(id) { return fleet.hosts.find(host => host.id === id)?.name || id || '未选择'; }
const states = {disabled: '未启用', enabling: '启用中', enabled: '共享中', disabling: '恢复中', error: '异常'};
const stateKinds = {disabled: 'info', enabling: 'warning', enabled: 'ok', disabling: 'warning', error: 'danger'};

fields('host-fields', [
  ['host-name', '显示名称'], ['host-address', '地址或域名'], ['host-port', 'SSH 端口', 'number', 22],
  ['host-username', 'SSH 账号'], ['host-credential_id', '凭据', 'select'], ['host-jump_id', '跳板机', 'select'],
  ['host-group', '分组'], ['host-host_key', 'SSH 主机公钥', 'textarea']
]);
fields('credential-fields', [
  ['credential-name', '凭据名称'], ['credential-kind', '凭据类型', 'select'], ['credential-password', 'SSH 密码', 'password'],
  ['credential-key', 'SSH 私钥内容', 'textarea'], ['credential-passphrase', '私钥口令（可选）', 'password']
]);
options('credential-kind', [{id: 'password', name: '密码'}, {id: 'key', name: 'SSH 私钥'}], null);
$('credential-kind').value = 'password';
function credentialKind() {
  const key = value('credential-kind') === 'key';
  $('credential-password').closest('li').hidden = key;
  $('credential-key').closest('li').hidden = !key;
  $('credential-passphrase').closest('li').hidden = !key;
}
$('credential-kind').onchange = credentialKind;
credentialKind();
fields('profile-fields', [
  ['profile-name', '方案名称'], ['profile-gateway_id', '出口机', 'select'], ['profile-client_ids', '客户端（可多选）', 'checkboxes'],
  ['profile-port', '出口 UDP 端口', 'number', 51919], ['profile-endpoint', '客户端可达的出口地址', 'text', '', '留空时使用出口机地址'],
  ['profile-tunnel_cidr', '隧道网段（可留空自动分配）'], ['profile-preserve_routes', '仍走原网络的 CIDR', 'textarea', '', '每行一项，或使用空格、逗号分隔'],
  ['profile-proxy_mode', '是否共享源机器代理', 'select'], ['profile-proxy_host', '源机器 HTTP / 混合代理 IPv4', 'text', '127.0.0.1'],
  ['profile-proxy_port', '源代理端口', 'number', 7897], ['profile-maintenance', '每分钟维护隧道，连续失败后恢复原网络', 'checkbox', true]
]);
options('profile-proxy_mode', [{id: 'direct', name: '仅共享网络，不共享源代理'}, {id: 'share', name: '共享网络和源 HTTP/HTTPS 代理'}], null);
fields('proxy-fields', [['proxy-enabled', '启用当前用户手动代理', 'checkbox'], ['proxy-server', '代理地址', 'text', '', 'Windows 可用 host:port 或 http=host:port;https=host:port。Ubuntu 使用单一 host:port。'], ['proxy-bypass', '绕过地址', 'textarea', '', 'Windows 用分号分隔；Ubuntu 以逗号或分号分隔。']]);
fields('campus-fields', [['campus-username', '校园网账号'], ['campus-password', '密码（仅本次使用）', 'password'], ['campus-service', '运营商', 'select']]);
options('campus-service', [{id: '0', name: '校园网'}, {id: '1', name: '中国移动'}, {id: '2', name: '中国联通'}, {id: '3', name: '中国电信'}], null);
$('campus-service').value = '0';

function campusSummary(state) {
  text('campus-state', '当前出口认证状态：' + state.state + '；账号：' + (state.account || '未确认') + '；运营商：' + (state.service || '未确认') + '；IP：' + (state.ip || '未确认') + '。这反映当前出口会话，需确认没有借用其他机器出口。');
}
const campusStatus = node('button', '查询当前校园网状态', 'button button-outline');
campusStatus.type = 'button';
campusStatus.id = 'campus-status';
campusStatus.onclick = () => run(async () => { campusSummary(await api('campus/status')); notice('只读查询已完成，没有执行登录或下线。'); });
$('campus-check').after(campusStatus);
$('campus-check').onclick = () => run(async () => {
  const state = await api('campus');
  text('campus-state', state.configured ? '校园网脚本已安装，可在恢复物理网络后登录。' : '尚未安装脚本；请将已核验的 netlogin.py 放入面板数据目录，或设置 SNA_NETLOGIN_SCRIPT 绝对路径。');
  notice('脚本安装状态已更新。');
});
$('campus-form').onsubmit = event => {
  event.preventDefault();
  run(async () => {
    const payload = {username: value('campus-username'), password: $('campus-password').value, service: value('campus-service'), physical_network_confirmed: $('campus-physical').checked};
    $('campus-password').value = '';
    try {
      const result = await api('campus/login', payload);
      if (result.current) campusSummary(result.current);
      await refresh(true);
      notice(result.message);
    } finally { payload.password = ''; }
  }, ['使用本机物理网络登录校园网？', '将通过本机 netlogin.py 提交此账号；不会自动下线已有账号或修改其他机器。请确认原网关、代理及其他 VPN 状态。']);
};

function updateFingerprintIndicator() {
  const key = value('host-host_key');
  const confirmed = !!$('fingerprint-confirmed')?.checked && !!key;
  statusBadge('fingerprint-status', confirmed ? '已确认 SSH 指纹 / 公钥' : key ? '待确认 SSH 指纹 / 公钥' : '未固定 SSH 指纹', confirmed ? 'ok' : 'warning');
}
function openHostDrawer() { const dialog = $('host-drawer'); if (!dialog.open) dialog.showModal(); }
function editHost(host = {}, open = false) {
  hostDraft = {id: '', port: 22, terminal_enabled: true, favorite: false, ...host};
  $('host-form').reset();
  fill(hostDraft, 'host-');
  $('fingerprint-confirmed').checked = !!host.host_key;
  text('fingerprint', host.host_key ? '已保存公钥：' + host.host_key : '');
  text('host-title', host.id ? '编辑主机' : '添加主机');
  updateFingerprintIndicator();
  updateChoices();
  ['inspect-host', 'test-host', 'delete-host'].forEach(id => $(id).disabled = !host.id);
  if (open) openHostDrawer();
}
function selectedClients() { return [...$('profile-client_ids').querySelectorAll('input:checked')].map(element => element.value); }
function pathNode(label, mono) { return node('span', label, 'path-node' + (mono ? ' mono' : '')); }
function renderProfilePath() {
  const container = $('profile-path');
  if (!container) return;
  const gateway = hostName(value('profile-gateway_id'));
  const shareProxy = value('profile-proxy_mode') === 'share';
  const clients = selectedClients();
  container.replaceChildren();
  const values = shareProxy ? [clients.length ? 'Client（' + clients.length + '）' : 'Client', 'WireGuard', 'Source Proxy'] : [clients.length ? 'Client（' + clients.length + '）' : 'Client', 'WireGuard', gateway === '未选择' ? 'Source Host' : 'Source Host · ' + gateway, 'Internet'];
  values.forEach((label, index) => { if (index) container.append(node('span', '→', 'path-arrow')); container.append(pathNode(label, index === 3 && !shareProxy)); });
}
function updateClients(selected = selectedClients()) {
  const gateway = value('profile-gateway_id');
  $('profile-client_ids').replaceChildren();
  for (const host of fleet.hosts.filter(item => item.id !== gateway)) {
    const label = node('label');
    const input = node('input');
    input.type = 'checkbox'; input.value = host.id; input.checked = selected.includes(host.id); input.onchange = updateSummary;
    label.append(input, node('span', host.name + ' · ' + host.address));
    $('profile-client_ids').append(label);
  }
  if (!fleet.hosts.length) $('profile-client_ids').append(node('p', '请先在“主机与凭据”添加出口机与客户端。'));
  updateSummary();
}
function updateSummary() {
  const clients = selectedClients();
  text('selected-hosts', '出口：' + hostName(value('profile-gateway_id')) + '；客户端：' + (clients.map(hostName).join('、') || '未选择'));
  renderProfilePath();
}
function proxyMode() {
  const share = value('profile-proxy_mode') === 'share';
  $('profile-proxy_host').closest('li').hidden = !share;
  $('profile-proxy_port').closest('li').hidden = !share;
  text('share-explanation', share ? '通过 WireGuard 转发源 HTTP / 混合代理端口，支持 HTTPS CONNECT。Windows 修改 SSH 用户代理；Ubuntu 修改新登录 shell、APT 和该用户 GNOME 会话，断开后恢复。源端口无需账号密码。' : '保留客户端当前代理设置，不复制源机器应用代理。源机器的 VPN / TUN 属于系统路由，此开关不会绕过或关闭它。');
  renderProfilePath();
}
function editProfile(profile = {}) {
  profileDraft = {id: '', name: '', gateway_id: '', client_ids: [], port: 51919, endpoint: '', tunnel_cidr: '', preserve_routes: [], maintenance: true, proxy_mode: 'direct', proxy_host: '127.0.0.1', proxy_port: 7897, state: 'disabled', ...profile};
  fill(profileDraft, 'profile-');
  updateClients(profileDraft.client_ids);
  proxyMode();
  text('profile-title', profile.id ? '共享方案设置' : '新建共享方案');
  syncProfileControls();
}
function syncProfileControls() {
  const profile = profileDraft;
  const locked = !!profile.cleanup_pending || !['disabled', 'error'].includes(profile.state);
  statusBadge('profile-state', states[profile.state] || profile.state || '尚未保存', stateKinds[profile.state] || 'info');
  $('save-profile').disabled = locked;
  $('delete-profile').disabled = !profile.id || locked;
  $('enable-profile').disabled = !profile.id || locked;
  $('disable-profile').disabled = !profile.id || (!profile.cleanup_pending && !['enabled', 'error', 'enabling', 'disabling'].includes(profile.state));
  $('install-helper').disabled = locked;
  for (const element of $('profile-fields').querySelectorAll('input,select,textarea')) element.disabled = locked;
}
function updateChoices() {
  options('host-credential_id', fleet.credentials);
  $('host-credential_id').value = hostDraft.credential_id || $('host-credential_id').value;
  options('host-jump_id', fleet.hosts.filter(host => host.id !== hostDraft.id), '不使用跳板机');
  $('host-jump_id').value = hostDraft.jump_id || $('host-jump_id').value;
  options('profile-gateway_id', fleet.hosts);
  updateClients();
}

function makeTable(headers) {
  const table = node('table');
  const thead = node('thead');
  const headRow = node('tr');
  headers.forEach(header => headRow.append(node('th', header)));
  thead.append(headRow);
  const body = node('tbody');
  table.append(thead, body);
  return {table, body};
}
function appendCell(row, value, cls) {
  const cell = node('td', undefined, cls);
  if (value instanceof Node) cell.append(value); else cell.textContent = value == null ? '' : String(value);
  row.append(cell);
  return cell;
}
function hostTestState(host) {
  if (host.last_test_ok === true) return ['通过', 'ok'];
  if (host.last_test_ok === false) return ['失败', 'danger'];
  return ['未测试', 'info'];
}
function renderHostsTable() {
  const shell = $('hosts');
  shell.replaceChildren();
  if (!fleet.hosts.length) { shell.append(node('div', '尚未添加主机。', 'empty-state')); return; }
  const result = makeTable(['名称', '地址', '用户 / 端口', '跳板', '指纹状态', '最近测试', '状态', '操作']);
  for (const host of fleet.hosts) {
    const row = node('tr');
    const name = node('div'); name.append(node('strong', host.name, 'table-primary')); if (host.group) name.append(node('span', host.group, 'table-secondary'));
    appendCell(row, name);
    appendCell(row, node('span', host.address, 'mono'));
    const account = node('div'); account.append(node('span', host.username)); account.append(node('span', ' · ' + host.port, 'table-secondary')); appendCell(row, account);
    appendCell(row, host.jump_id ? hostName(host.jump_id) : '直连');
    appendCell(row, statusBadgeNode(host.host_key ? '已确认' : '待确认', host.host_key ? 'ok' : 'warning'));
    const test = hostTestState(host); appendCell(row, test[0]);
    appendCell(row, statusBadgeNode(host.last_test_ok === false ? '异常' : host.last_test_ok === true ? '可用' : '未测试', test[1]));
    const actions = node('div');
    const edit = node('button', '编辑', 'button button-outline table-action'); edit.type = 'button'; edit.onclick = () => editHost(host, true); actions.append(edit);
    appendCell(row, actions);
    result.body.append(row);
  }
  shell.append(result.table);
}
function renderCredentialsTable() {
  const shell = $('credentials');
  shell.replaceChildren();
  if (!fleet.credentials.length) { shell.append(node('div', '尚未保存凭据。', 'empty-state')); return; }
  const result = makeTable(['名称', '类型', '关联主机数', '操作']);
  for (const credential of fleet.credentials) {
    const row = node('tr');
    appendCell(row, node('strong', credential.name, 'table-primary'));
    appendCell(row, credential.kind === 'key' ? 'SSH 私钥' : '密码');
    appendCell(row, String(fleet.hosts.filter(host => host.credential_id === credential.id).length));
    const remove = node('button', '删除', 'button button-outline color-red table-action'); remove.type = 'button';
    remove.onclick = () => run(async () => { await api('fleet/credential/delete', {id: credential.id}); await loadFleet(); notice('凭据已删除。'); }, ['删除凭据？', '删除 ' + credential.name + '。仍被主机使用时后台会拒绝。']);
    appendCell(row, remove);
    result.body.append(row);
  }
  shell.append(result.table);
}
function renderProfiles() {
  const container = $('profiles');
  container.replaceChildren();
  text('profile-count', fleet.profiles.length + ' 个');
  if (!fleet.profiles.length) { container.append(node('div', '尚未创建共享方案。', 'empty-state')); return; }
  for (const profile of fleet.profiles) {
    const entry = node('div', undefined, 'profile-entry' + (profile.id === profileDraft.id ? ' is-selected' : ''));
    const button = node('button'); button.type = 'button'; button.onclick = () => editProfile(profile);
    button.append(node('strong', profile.name || '未命名方案'));
    button.append(node('span', hostName(profile.gateway_id) + ' → ' + (profile.client_ids || []).map(hostName).join('、')));
    const line = node('div', undefined, 'profile-state-line'); line.append(statusBadgeNode(states[profile.state] || profile.state, stateKinds[profile.state] || 'info'));
    if (profile.cleanup_pending) line.append(node('span', '恢复未完成'));
    button.append(line);
    if (profile.last_error) button.append(node('span', profile.last_error));
    entry.append(button); container.append(entry);
  }
}
function renderFleet() { renderHostsTable(); renderCredentialsTable(); renderProfiles(); updateChoices(); }
async function loadFleet() {
  const values = await Promise.all([api('fleet/hosts'), api('fleet/credentials'), api('fleet/network')]);
  fleet = {hosts: values[0].hosts || [], credentials: values[1].credentials || [], profiles: values[2].profiles || []};
  renderFleet();
}
$('profile-gateway_id').onchange = () => updateClients();
$('profile-proxy_mode').onchange = proxyMode;
$('new-host').onclick = () => editHost({}, true);
$('new-profile').onclick = () => editProfile();
$('close-host').onclick = () => $('host-drawer').close();
$('host-host_key').oninput = () => { $('fingerprint-confirmed').checked = false; updateFingerprintIndicator(); };
$('fingerprint-confirmed').onchange = updateFingerprintIndicator;
$('host-form').onsubmit = event => {
  event.preventDefault();
  run(async () => {
    const host = {...hostDraft};
    for (const key of ['name', 'address', 'username', 'credential_id', 'jump_id', 'group', 'host_key']) host[key] = value('host-' + key);
    host.port = Number(value('host-port'));
    if (!host.name || !host.address || !host.username) throw new Error('请填写主机名称、地址和 SSH 账号。');
    if (host.host_key && !$('fingerprint-confirmed').checked) throw new Error('请先通过可信渠道核对 SSH 指纹 / 公钥，再勾选确认。');
    const trusted = !!host.host_key && $('fingerprint-confirmed').checked;
    const result = await api('fleet/host/save', host);
    await loadFleet();
    editHost(result.host);
    if (trusted) $('host-drawer').close();
    notice('主机已保存。');
  });
};
$('inspect-host').onclick = () => run(async () => {
  const result = await api('fleet/host/inspect', {id: hostDraft.id});
  $('host-host_key').value = result.host_key;
  $('fingerprint-confirmed').checked = false;
  text('fingerprint', '待核对指纹：' + result.fingerprint);
  updateFingerprintIndicator();
  notice('指纹已读取，尚未信任。请通过服务器控制台等可信渠道核对后勾选并保存。');
});
$('test-host').onclick = () => run(async () => {
  const result = await api('fleet/network/probe', {ids: [hostDraft.id]});
  renderProbes(result.results || []);
  notice((result.results || []).map(item => item.name + ': ' + (item.ssh ? 'SSH 成功' : 'SSH 失败') + '\n' + (item.error || item.diagnosis || item.default_route || '')).join('\n'));
});
$('delete-host').onclick = () => run(async () => { await api('fleet/host/delete', {id: hostDraft.id}); $('host-drawer').close(); editHost(); await loadFleet(); notice('主机已删除。'); }, ['删除主机？', '删除 ' + (hostDraft.name || '') + '。若仍被共享方案引用，后台会拒绝。']);
$('credential-form').onsubmit = event => {
  event.preventDefault();
  run(async () => {
    const kind = value('credential-kind');
    const secret = kind === 'key' ? $('credential-key').value : $('credential-password').value;
    if (!value('credential-name') || !secret) throw new Error('请填写凭据名称和内容。');
    await api('fleet/credential/save', {name: value('credential-name'), kind, secret, passphrase: $('credential-passphrase').value});
    $('credential-form').reset(); credentialKind(); await loadFleet(); notice('凭据已加密保存，输入内容已清空。');
  });
};
function profilePayload() {
  return {...profileDraft, name: value('profile-name'), gateway_id: value('profile-gateway_id'), client_ids: selectedClients(), port: Number(value('profile-port')), endpoint: value('profile-endpoint'), tunnel_cidr: value('profile-tunnel_cidr'), preserve_routes: value('profile-preserve_routes'), maintenance: $('profile-maintenance').checked, proxy_mode: value('profile-proxy_mode'), proxy_host: value('profile-proxy_host'), proxy_port: Number(value('profile-proxy_port'))};
}
$('profile-form').onsubmit = event => {
  event.preventDefault();
  run(async () => {
    const payload = profilePayload();
    if (!payload.name || !payload.gateway_id || !payload.client_ids.length) throw new Error('请填写方案名称，并选择一台出口机和至少一台客户端。');
    const result = await api('fleet/network/profile/save', payload);
    await loadFleet(); editProfile(result.profile); notice('方案已保存，尚未自动启用。');
  });
};
function renderProbes(results) {
  const container = $('probe-results'); container.replaceChildren();
  for (const result of results) {
    const card = node('article', undefined, 'probe-card');
    card.append(node('h3', result.name + ' · ' + (result.os || '系统未识别')));
    card.append(node('p', 'SSH ' + (result.ssh ? '成功' : '失败') + ' · DNS ' + (result.dns ? '成功' : '失败') + ' · 公网 ' + (result.internet ? '成功' : '失败') + ' · 辅助程序 ' + (result.helper ? '就绪' : '未就绪')));
    card.append(node('p', result.error || result.diagnosis || result.default_route || ''));
    container.append(card);
  }
}
$('probe-all').onclick = () => run(async () => { if (!fleet.hosts.length) throw new Error('请先添加主机。'); const result = await api('fleet/network/probe', {ids: fleet.hosts.map(host => host.id)}); renderProbes(result.results || []); notice('探测完成，查看各主机的实际结果。'); });
$('install-helper').onclick = () => run(async () => { const ids = [value('profile-gateway_id'), ...selectedClients()].filter(Boolean); if (ids.length < 2) throw new Error('请先选择出口机和客户端。'); await api('fleet/network/helper/install', {ids}); notice('辅助程序安装请求已完成，请再次探测确认各主机能力。'); }, ['安装远端辅助程序？', '将在所选出口机与客户端安装网络辅助程序，需要远端管理员权限。请确认主机选择和指纹无误。']);
async function profileAction(kind) {
  await run(async () => {
    try {
      const result = await api('fleet/network/profile/' + kind, {id: profileDraft.id});
      await loadFleet();
      if (kind === 'delete') editProfile(); else editProfile(result.profile);
      await refresh(true);
      notice(kind === 'enable' ? '共享已启用并完成后端连通性检查。' : kind === 'disable' ? '共享已断开并恢复原网络。' : '方案已删除。');
    } catch (error) {
      await loadFleet().catch(() => {});
      const latest = fleet.profiles.find(profile => profile.id === profileDraft.id);
      if (latest) editProfile(latest);
      throw error;
    }
  }, kind === 'enable' ? ['启用多机网络共享？', '出口：' + hostName(profileDraft.gateway_id) + '\n客户端：' + (profileDraft.client_ids || []).map(hostName).join('、') + '\n将改变客户端公网路由' + (profileDraft.proxy_mode === 'share' ? '及用户代理' : '') + '。复检失败会尝试回退，可能短暂中断连接。'] : kind === 'disable' ? ['断开并恢复原网络？', '将清理所选方案的隧道和临时路由，并恢复它更改的代理。相关下载和远程连接可能中断。'] : ['删除共享方案？', '删除保存的方案；仍在运行或未清理完成的方案不能删除。']);
}
$('enable-profile').onclick = () => profileAction('enable');
$('disable-profile').onclick = () => profileAction('disable');
$('delete-profile').onclick = () => profileAction('delete');

function renderProxy(result, overwrite = false) {
  const proxy = result.proxy || {};
  const supported = proxy.supported !== false;
  const label = !supported || proxy.enabled == null ? '无法读取' : proxy.enabled ? '手动代理已开启' : proxy.pac ? 'PAC 生效，手动代理已关闭' : '手动代理已关闭';
  const kind = !supported || proxy.enabled == null ? 'danger' : proxy.enabled ? 'warning' : 'ok';
  statusBadge('proxy-state', label, kind);
  text('proxy-manual-state', !supported || proxy.enabled == null ? '未知' : proxy.enabled ? '已开启' : '已关闭');
  text('proxy-pac-state', !supported || proxy.enabled == null ? '未知' : proxy.pac ? '存在 PAC' : '未设置 PAC');
  text('proxy-address', proxy.server || '未设置手动代理地址');
  text('proxy-description', (proxy.scope || '当前用户代理') + (proxy.pac ? '。存在自动代理 PAC，关闭手动代理不会移除 PAC。' : '') + (proxy.error ? '\n' + proxy.error : ''));
  text('overview-proxy-state', !supported ? '无法读取' : proxy.enabled ? '手动代理已开启' : proxy.pac ? 'PAC 生效' : '手动代理已关闭');
  $('save-proxy').disabled = !supported;
  $('disable-proxy').disabled = !supported || !proxy.enabled;
  if (overwrite) {
    $('proxy-enabled').checked = !!proxy.enabled;
    $('proxy-server').value = proxy.server || '';
    $('proxy-bypass').value = Array.isArray(proxy.bypass) ? proxy.bypass.join(';') : proxy.bypass || '';
  }
  list('proxy-backups', result.backups || [], backup => {
    const item = listItem(formatTime(backup.created_at), [backup.id, backup.platform + (backup.compatible === false ? ' · 不兼容当前系统' : '')], () => run(async () => { const restored = await api('proxy/restore', {id: backup.id}); renderProxy(restored, true); notice('代理备份已恢复。'); await refresh(true); }, ['恢复代理配置？', '将恢复备份 ' + backup.id + '，并先备份当前设置。']), '恢复');
    if (backup.compatible === false) item.querySelector('button').disabled = true;
    return item;
  }, '还没有代理配置备份。');
}
async function loadProxy() { renderProxy(await api('proxy'), true); }
$('load-proxy').onclick = () => run(async () => { await loadProxy(); notice('代理和备份已重新读取。'); });
$('proxy-form').onsubmit = event => { event.preventDefault(); run(async () => { const result = await api('proxy/save', {enabled: $('proxy-enabled').checked, server: value('proxy-server'), bypass: value('proxy-bypass')}); renderProxy(result, true); await refresh(true); notice('已备份原配置并保存代理，请检查系统应用联网结果。'); }, ['保存当前用户代理？', '将先备份当前配置，再应用编辑后的代理。错误的地址会影响使用系统代理的应用。']); };
$('disable-proxy').onclick = () => run(async () => { await api('action', {action: 'disable-proxy'}); await loadProxy(); await refresh(true); notice('手动代理已关闭，原配置已备份。'); }, ['关闭手动代理？', '将备份当前配置，再关闭手动代理。自动代理 PAC 或代理软件重新接管的设置需要单独检查。']);

function diagnosticRow(label, detail, kind, status) {
  const row = node('div', undefined, 'diagnostic-row');
  const main = node('div', undefined, 'diagnostic-row-main'); main.append(node('strong', label)); main.append(node('p', detail));
  row.append(main, statusBadgeNode(status, kind));
  return row;
}
function renderDiagnosticSummary(status, hosts, profiles) {
  const container = $('diagnostic-summary'); container.replaceChildren();
  if (!status) { container.append(node('div', '本机状态读取失败；可先查看操作记录或独立探测远端主机。', 'empty-state')); return; }
  const direct = status.direct || {}, system = status.system || {}, tunnels = status.tunnels || [], routes = status.routes || [];
  container.append(diagnosticRow('公网', direct.ok === true ? '直连 HTTPS 可用' + (direct.milliseconds ? ' · ' + direct.milliseconds + ' ms' : '') : direct.ok === false ? '直连 HTTPS 未通过' : '未获得直连结果', direct.ok === true ? 'ok' : direct.ok === false ? 'danger' : 'info', direct.ok === true ? '正常' : direct.ok === false ? 'Failed' : 'Unknown'));
  container.append(diagnosticRow('代理 / 应用路径', system.ok === true ? '系统应用联网探测可用' : system.ok === false ? '系统应用联网探测未通过' : '未获得系统应用结果', system.ok === true ? 'ok' : system.ok === false ? 'warning' : 'info', system.ok === true ? '正常' : system.ok === false ? 'Warning' : 'Unknown'));
  if (!tunnels.length) container.append(diagnosticRow('隧道', '未发现本机 WireGuard 隧道', 'info', '未配置'));
  else {
    const active = tunnels.filter(tunnel => tunnel.active);
    const stale = active.some(tunnel => !tunnel.handshake || (status.timestamp && status.timestamp - tunnel.handshake > 180));
    container.append(diagnosticRow('隧道', active.length + ' / ' + tunnels.length + ' 个隧道已连接' + (stale ? '，存在握手未更新' : ''), stale ? 'warning' : active.length ? 'ok' : 'warning', stale ? 'Warning' : active.length ? '正常' : 'Warning'));
  }
  container.append(diagnosticRow('路由', routes.length ? '已读取 ' + routes.length + ' 条默认路由' : '没有可用的默认路由信息', routes.length ? 'ok' : 'warning', routes.length ? '正常' : 'Warning'));
  if (!hosts.length) container.append(diagnosticRow('SSH', '尚未配置远端主机', 'info', '未配置'));
  else { const untrusted = hosts.filter(host => !host.host_key).length; container.append(diagnosticRow('SSH', untrusted ? untrusted + ' 台主机待确认指纹' : hosts.length + ' 台主机已固定公钥', untrusted ? 'warning' : 'ok', untrusted ? 'Warning' : '正常')); }
  if (!profiles.length) container.append(diagnosticRow('共享方案', '尚未配置共享方案', 'info', '未配置'));
  else { const recovery = profiles.filter(profile => profile.cleanup_pending || profile.state === 'error').length; const enabled = profiles.filter(profile => profile.state === 'enabled').length; container.append(diagnosticRow('共享方案', recovery ? recovery + ' 个方案需要恢复处理' : enabled ? enabled + ' 个方案正在运行' : '方案已保存但未启用', recovery ? 'danger' : enabled ? 'ok' : 'info', recovery ? 'Failed' : enabled ? '正常' : '未启用')); }
}
function renderGuidance(items) {
  const container = $('guidance'); container.replaceChildren();
  if (!items.length) { container.append(node('div', '没有可显示的建议。', 'empty-state')); return; }
  for (const item of items) {
    const kind = item.severity === 'error' ? 'danger' : item.severity === 'warning' ? 'warning' : 'info';
    const card = node('article', undefined, 'guidance-item status-' + kind);
    card.append(node('h3', item.title || '建议')); card.append(node('p', item.detail || ''));
    container.append(card);
  }
}
async function loadDiagnostics() {
  const result = await api('diagnostics');
  diagnosis = result;
  let hosts = fleet.hosts, profiles = fleet.profiles;
  try {
    const values = await Promise.all([api('fleet/hosts'), api('fleet/network')]);
    hosts = values[0].hosts || []; profiles = values[1].profiles || [];
    fleet.hosts = hosts; fleet.profiles = profiles;
  } catch {}
  $('export-diagnostics').disabled = false;
  renderDiagnosticSummary(result.status, hosts, profiles);
  renderGuidance(result.guidance || []);
  list('diagnostic-events', result.events || [], event => listItem(formatTime(event.timestamp) + ' · ' + event.action, [event.outcome, typeof event.detail === 'object' ? JSON.stringify(event.detail, null, 2) : event.detail]), '尚无本机操作记录。');
  try {
    const audit = await api('fleet/audit');
    list('fleet-events', audit.events || [], event => listItem(formatTime(event.created_at) + ' · ' + event.action, [event.target, JSON.stringify(event.details || {})]), '尚无多机操作记录。');
  } catch (error) { list('fleet-events', [{message: error.message}], event => listItem('Fleet 审计暂不可用', [event.message]), '尚无多机操作记录。'); }
}
$('run-diagnostics').onclick = () => run(async () => { await loadDiagnostics(); notice('诊断完成；请按真实结果选择恢复操作。'); });
$('export-diagnostics').onclick = () => { if (!diagnosis) return; const blob = new Blob([JSON.stringify(diagnosis, null, 2)], {type: 'application/json'}); const url = URL.createObjectURL(blob); const link = node('a'); link.href = url; link.download = 'network-diagnostics-' + Date.now() + '.json'; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); };
$('check-updates').onclick = () => run(async () => {
  const result = await api('updates');
  text('update-current', result.current || '—'); text('update-latest', result.latest || '暂无正式发布');
  text('update-state', result.error || '当前 ' + (result.current || '—') + ' · 最新 ' + (result.latest || '尚无发布版本') + (result.available ? ' · 有更新' : ' · 无可用更新'));
  $('update-links').replaceChildren();
  for (const link of [{name: '查看 GitHub 发布说明', url: result.release_url}, ...(result.assets || [])]) {
    if (!link.url) continue;
    let url; try { url = new URL(link.url); } catch { continue; }
    if (url.protocol !== 'https:' || url.hostname !== 'github.com') continue;
    const anchor = node('a', link.name, 'button button-outline external'); anchor.href = url.href; anchor.target = '_blank'; anchor.rel = 'noopener noreferrer'; $('update-links').append(anchor);
  }
  notice('更新检查已完成。');
});

async function tunnelAction(kind, tunnel) {
  const confirmations = {
    disconnect: ['临时断开借网隧道？', '停止本机隧道，不禁用下次自动启动。仅移除隧道拥有的路由；额外路由、代理和登录任务需另行核对。依赖隧道的连接可能中断。'],
    'pause-sharing': ['停止借网并禁用自动启动？', '先备份启动配置，再停止并禁用此本机隧道服务。依赖隧道的连接可能中断。不会修改其他机器，也不会猜测删除旧脚本额外路由或恢复代理。'],
    'restore-startup': ['恢复原借网服务配置？', '恢复备份的启动方式及原运行状态；如果之前正在运行，将重新开始借网。']
  };
  await run(async () => { await api('action', {action: kind, tunnel}); await refresh(true); notice(kind === 'pause-sharing' ? '已停止本机借网并禁用自动启动。请核对物理网关、代理和旧脚本额外路由，再登录校园网。' : '隧道操作已完成，请核对最新连通性。'); }, confirmations[kind]);
}
function renderTraffic(traffic) {
  const chartElement = $('traffic-chart');
  text('traffic-scope', traffic?.scope || '仅为 WireGuard 隧道流量，不是整机网卡流量。');
  if (!traffic || !traffic.available) {
    text('traffic-rate', '暂无可用遥测，不将未知速率显示为零。'); chartElement.hidden = true; chartElement.replaceChildren(); chart = null; $('traffic-history').replaceChildren(); return;
  }
  const history = (traffic.history || []).filter(point => Number.isFinite(point.received_per_second) && Number.isFinite(point.sent_per_second));
  text('traffic-rate', '↓ ' + bytes(traffic.received_per_second) + '/s　↑ ' + bytes(traffic.sent_per_second) + '/s');
  chartElement.hidden = history.length < 2;
  const config = {el: '#traffic-chart', lineChart: true, axis: true, legend: true, tooltip: true, axisLabels: history.map(point => new Date(point.timestamp * 1000).toLocaleTimeString()), maxAxisLabels: 4, datasets: [{label: '下载 B/s', color: '#2563eb', values: history.map(point => point.received_per_second)}, {label: '上传 B/s', color: '#21a366', values: history.map(point => point.sent_per_second)}]};
  if (history.length >= 2) { if (chart) chart.update(config); else chart = app.areaChart.create(config); }
  $('traffic-history').replaceChildren();
  for (const point of [...history].reverse()) { const row = node('tr'); [new Date(point.timestamp * 1000).toLocaleTimeString(), bytes(point.received_per_second) + '/s', bytes(point.sent_per_second) + '/s'].forEach(item => row.append(node('td', item))); $('traffic-history').append(row); }
}
function tunnelFact(label, valueText, cls) { const item = node('div'); item.append(node('dt', label)); item.append(node('dd', valueText, cls)); return item; }
function render(s) {
  lastStatus = s;
  const directOk = s.direct?.ok === true, systemOk = s.system?.ok === true;
  const activeTunnels = (s.tunnels || []).filter(tunnel => tunnel.active);
  const tunnelIssue = activeTunnels.some(tunnel => !tunnel.handshake || (s.timestamp && s.timestamp - tunnel.handshake > 180));
  let kind = 'ok', label = '连接运行正常', title = '网络已就绪', description = '原生网络与系统应用均可访问公网。';
  if (directOk === false && systemOk === false) { kind = 'danger'; label = '无法访问公网'; title = '无法访问公网'; description = '直连和系统应用路径都未通过，请查看路由、隧道和远端探测。'; }
  else if (directOk && systemOk === false) { kind = 'warning'; label = '系统代理异常'; title = '直连正常，系统应用路径异常'; description = '直连 HTTPS 成功，但系统应用路径失败，请检查系统代理和 PAC。'; }
  else if (directOk === false && systemOk) { kind = 'warning'; label = '部分异常'; title = '系统应用仍可用，直连检测失败'; description = '当前应用路径仍可用，但本机直连公网检测未通过。'; }
  else if (tunnelIssue) { kind = 'warning'; label = '隧道异常'; title = '隧道握手需要复核'; description = '公网检测可能正常，但有活动隧道的握手未更新，请结合实际服务探测判断。'; }
  else if (s.direct?.ok == null || s.system?.ok == null) { kind = 'info'; label = '状态待确认'; title = '连接状态待确认'; description = '部分检测结果不可用，未知值不会被当作正常或零流量。'; }
  text('hostname', s.hostname); text('platform', s.platform); text('version', 'v' + s.version); text('header-hostname', s.hostname); text('hero-hostname', s.hostname); text('hero-platform', s.platform); text('page-title', currentSection === 'overview' ? '连接概览' : $('page-title').textContent); text('settings-version', 'v' + s.version);
  statusText('network-label', label, kind); text('network-title', title); text('network-description', description); statusDot('network-dot', kind); statusDot('sidebar-status-dot', s.background?.running ? 'ok' : 'warning');
  setSummaryClass(kind);
  statusText('direct', directOk ? '可用' : directOk === false ? '不可用' : '未知', directOk ? 'ok' : directOk === false ? 'danger' : 'info');
  statusText('system', systemOk ? '可用' : systemOk === false ? '不可用' : '未知', systemOk ? 'ok' : systemOk === false ? 'warning' : 'info');
  text('latency', directOk ? 'HTTPS 响应 ' + (s.direct.milliseconds == null ? '—' : s.direct.milliseconds + ' ms') : 'HTTPS 连通性检测未通过');
  text('system-detail', s.system?.scope || '使用当前用户的代理设置');
  const measured = (s.tunnels || []).filter(tunnel => tunnel.telemetry !== false && Number.isFinite(tunnel.received) && Number.isFinite(tunnel.sent));
  const received = measured.length ? measured.reduce((sum, tunnel) => sum + tunnel.received, 0) : null;
  const sent = measured.length ? measured.reduce((sum, tunnel) => sum + tunnel.sent, 0) : null;
  text('traffic', received === null ? '—' : bytes(received + sent)); text('traffic-detail', '↓ ' + bytes(received) + '　↑ ' + bytes(sent)); text('tunnel-count', activeTunnels.length + ' / ' + (s.tunnels || []).length + ' 已连接');
  text('permission', s.elevated ? '网络控制权限已就绪' : '本机连接控制需要管理员权限'); text('hero-permission', s.elevated ? '管理员权限已就绪' : '需要管理员权限'); text('updated', '更新于 ' + new Date(s.timestamp * 1000).toLocaleTimeString());
  statusText('sidebar-status', s.background?.running ? '正在运行' : '未运行', s.background?.running ? 'ok' : 'warning');
  const background = s.background || {};
  statusBadge('runtime-badge', background.running ? '后台服务运行中' : '后台服务未运行', background.running ? 'ok' : 'warning');
  text('background-state', background.running ? '后台服务正在运行。' : '后台能力状态未提供。');
  text('tray-state', background.tray_running ? '系统托盘已运行；状态通知' + (background.notifications_enabled ? '已开启' : '未开启') + '。' : background.tray_available ? '系统支持托盘；当前后台启动方式未运行托盘。' : '当前会话无法使用系统托盘，可通过桌面快捷方式重新打开。');
  text('overview-proxy-state', !systemOk ? '需检查系统代理 / PAC' : '系统应用路径可用');
  $('tunnels').replaceChildren();
  for (const tunnel of s.tunnels || []) {
    const article = node('article', undefined, 'tunnel-card surface-section');
    const header = node('div', undefined, 'card-header');
    const titleElement = node('h3', tunnel.name); header.append(titleElement, statusBadgeNode(tunnel.active ? '已连接' : '已断开', tunnel.active ? (tunnelIssue ? 'warning' : 'ok') : 'warning'));
    const content = node('div', undefined, 'card-content');
    const age = tunnel.handshake ? Math.max(0, Math.floor(s.timestamp - tunnel.handshake)) : null;
    const facts = node('dl', undefined, 'tunnel-facts');
    facts.append(tunnelFact('最近握手', age === null ? '暂无握手信息' : '约 ' + age + ' 秒前'));
    facts.append(tunnelFact('RX', tunnel.telemetry === false ? '—' : bytes(tunnel.received), 'mono'));
    facts.append(tunnelFact('TX', tunnel.telemetry === false ? '—' : bytes(tunnel.sent), 'mono'));
    facts.append(tunnelFact('本地地址', (tunnel.addresses || []).join(' / ') || '—', 'mono'));
    content.append(facts);
    const details = document.createElement('details'); details.append(node('summary', '查看详情'));
    const extra = node('dl', undefined, 'tunnel-facts'); extra.append(tunnelFact('Endpoint', tunnel.endpoint || '未提供', 'mono')); extra.append(tunnelFact('服务启动方式', tunnel.start_mode || '未知')); extra.append(tunnelFact('服务状态', tunnel.service_state || '未知')); details.append(extra); content.append(details);
    const footer = node('div', undefined, 'card-footer');
    const backup = (s.recovery || []).find(record => record.name === tunnel.name && record.state !== 'restored');
    const disabled = ['Disabled', 'masked'].includes(tunnel.start_mode);
    const action = node('button', tunnel.active ? '临时断开' : '连接', 'button ' + (tunnel.active ? 'button-outline' : 'button-fill')); action.type = 'button'; action.disabled = !s.elevated || (!tunnel.active && disabled); action.onclick = () => tunnelAction(tunnel.active ? 'disconnect' : 'connect', tunnel.name); footer.append(action);
    const pause = node('button', '停止借网并禁用自动启动', 'button button-outline color-red'); pause.type = 'button'; pause.disabled = !s.elevated; pause.onclick = () => tunnelAction('pause-sharing', tunnel.name); footer.append(pause);
    if (backup) { const restore = node('button', '恢复原借网服务配置', 'button button-outline'); restore.type = 'button'; restore.disabled = !s.elevated; restore.onclick = () => tunnelAction('restore-startup', tunnel.name); footer.append(restore); }
    article.append(header, content, footer); $('tunnels').append(article);
  }
  if (!(s.tunnels || []).length) $('tunnels').append(node('p', '尚未发现已安装的隧道。可在“共享网络”创建方案。', 'empty-state'));
  $('routes').replaceChildren();
  if (!(s.routes || []).length) $('routes').append(node('p', '没有可用的默认路由信息。', 'empty-state'));
  for (const route of s.routes || []) { const row = node('div', undefined, 'route-row'); row.append(node('strong', route.adapter || '未识别网卡')); row.append(node('span', '网关 ' + (route.gateway || '—') + ' · metric ' + (route.metric == null ? '—' : route.metric))); $('routes').append(row); }
  renderTraffic(s.traffic);
}
function setSummaryClass(kind) { const element = $('network-summary'); if (!element) return; element.dataset.state = kind; }
async function refresh(required = false) {
  if (!refreshing) { $('refresh').disabled = true; refreshing = api('status').then(render).finally(() => { refreshing = false; $('refresh').disabled = false; }); }
  try { await refreshing; } catch (error) { notice(error.message + '。若后台已关闭，请重新打开桌面快捷方式。'); if (required) throw new Error('状态复检失败：' + error.message + '。请刷新确认，不要重复切换网络。'); }
}
$('refresh').onclick = () => run(async () => { await refresh(true); if (currentSection === 'sharing' || currentSection === 'hosts-page') await loadFleet(); notice('状态已刷新。'); });

showHostTab('hosts');
editHost();
editProfile();
refresh();
loadFleet().catch(error => notice(error.message));
setInterval(() => { if (!document.hidden) refresh(); }, 5000);
