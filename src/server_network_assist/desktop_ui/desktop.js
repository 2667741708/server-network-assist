'use strict';
const $ = (id) => document.getElementById(id);
const token = location.hash.slice(1) || sessionStorage.getItem('desktop-token') || '';
if (location.hash) { sessionStorage.setItem('desktop-token', token); history.replaceState(null, '', '/'); }
let refreshing = false;
let pending = false;
function text(id, value) { $(id).textContent = value; }
function bytes(n) { const units = ['B', 'KB', 'MB', 'GB', 'TB']; let i = 0; while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; } return `${n.toFixed(i ? 1 : 0)} ${units[i]}`; }
function notice(message) { $('notice').hidden = !message; text('notice', message); }
async function api(path, body) {
  const response = await fetch('/api/' + path, { method: body ? 'POST' : 'GET', headers: { 'X-Desktop-Token': token, ...(body ? {'Content-Type': 'application/json'} : {}) }, ...(body ? {body: JSON.stringify(body)} : {}) });
  const value = await response.json();
  if (!response.ok) throw new Error(value.error || '无法读取本机状态');
  return value;
}
function confirmAction(title, description) {
  text('confirm-title', title); text('confirm-text', description);
  const dialog = $('confirm-dialog');
  return new Promise((resolve) => {
    $('accept').onclick = () => { dialog.close(); resolve(true); };
    $('cancel').onclick = () => { dialog.close(); resolve(false); };
    dialog.oncancel = () => resolve(false);
    dialog.showModal();
  });
}
async function action(kind, tunnel) {
  if (pending) return;
  if (kind === 'disconnect' && !await confirmAction('断开借网隧道？', '本机将恢复使用原有网卡路由。依赖当前隧道的下载、远程连接或应用可能中断；你可以随时重新连接。')) return;
  if (kind === 'disable-proxy' && !await confirmAction('关闭 Windows 手动代理？', '将备份当前代理配置，再关闭当前用户的手动代理。代理软件若重新接管系统设置，仍需在该软件中关闭系统代理开关。')) return;
  pending = true; notice('正在应用更改…');
  try { await api('action', {action: kind, tunnel}); notice('已完成，正在重新检测网络。'); await refresh(); }
  catch (e) { notice(e.message); }
  finally { pending = false; }
}
function render(s) {
  text('hostname', s.hostname); text('platform', s.platform); text('version', 'v' + s.version);
  const ok = s.direct.ok && s.system.ok;
  text('network-label', ok ? '连接运行正常' : '需要检查网络');
  text('network-title', ok ? '网络已就绪' : s.direct.ok ? '系统代理可能异常' : s.system.ok ? '当前依赖代理联网' : '暂时无法访问公网');
  text('network-description', ok ? '原生网络与系统应用均可访问公网，连接正常。' : s.direct.ok ? '直连请求成功，但系统代理路径失败。可在下方检查或关闭故障手动代理。' : '检查隧道、出口机或本机网络，然后刷新状态。');
  document.querySelector('.hero').classList.toggle('warn', !ok);
  $('network-dot').style.background = ok ? '#328c5f' : '#cf9747';
  text('direct', s.direct.ok ? '可用' : '不可用'); text('system', s.system.ok ? '可用' : '不可用');
  text('latency', s.direct.ok ? `HTTPS 响应 ${s.direct.milliseconds} ms` : 'HTTPS 连通性检测未通过');
  const received = s.tunnels.reduce((n,t) => n+t.received, 0), sent = s.tunnels.reduce((n,t) => n+t.sent, 0);
  text('traffic', bytes(received+sent)); text('traffic-detail', `↓ ${bytes(received)}　↑ ${bytes(sent)}`);
  text('tunnel-count', `${s.tunnels.filter(t => t.active).length} / ${s.tunnels.length} 已连接`);
  $('tunnels').replaceChildren();
  for (const tunnel of s.tunnels) {
    const card = document.createElement('article'); card.className = 'tunnel';
    const content = document.createElement('div'); const header = document.createElement('div'); header.className = 'tunnel-header';
    const name = document.createElement('h3'); name.textContent = tunnel.name;
    const pill = document.createElement('span'); pill.className = 'pill' + (tunnel.active ? ' good' : ''); pill.textContent = tunnel.active ? '已连接' : '已断开';
    header.append(name, pill); content.append(header);
    const endpoint = document.createElement('p'); endpoint.className = 'mono'; endpoint.textContent = tunnel.endpoint ? `出口 ${tunnel.endpoint}` : '本机已有 WireGuard 配置'; content.append(endpoint);
    const details = document.createElement('div'); details.className = 'tunnel-details';
    const age = tunnel.handshake ? Math.max(0, s.timestamp - tunnel.handshake) : null;
    for (const value of [tunnel.addresses.join(' / '), `↓ ${bytes(tunnel.received)}　↑ ${bytes(tunnel.sent)}`, age === null ? '暂无握手信息' : `最近握手 ${age} 秒前`]) {
      if (value) { const span = document.createElement('span'); span.textContent = value; details.append(span); }
    }
    content.append(details);
    const button = document.createElement('button'); button.textContent = tunnel.active ? '断开连接' : '连接'; button.className = tunnel.active ? 'secondary' : '';
    button.disabled = !s.elevated; button.onclick = () => action(tunnel.active ? 'disconnect' : 'connect', tunnel.name);
    card.append(content, button); $('tunnels').append(card);
  }
  if (!s.tunnels.length) { const empty = document.createElement('div'); empty.className = 'empty'; empty.textContent = '尚未发现已安装的 WireGuard 隧道。可先通过服务器管理台配置借网客户端。'; $('tunnels').append(empty); }
  text('proxy-state', s.proxy.enabled ? '已开启' : '已关闭');
  text('proxy-address', s.proxy.enabled ? s.proxy.server || '已设置代理环境' : '应用使用当前网络直接访问');
  text('proxy-description', s.proxy.pac ? '检测到自动代理 PAC；关闭手动代理不会移除 PAC。' : '已有 WireGuard 出口时，通常无需额外启用系统代理。');
  $('disable-proxy').disabled = !s.proxy.enabled || s.platform !== 'Windows';
  $('routes').replaceChildren();
  for (const route of s.routes) { const row = document.createElement('div'); const label = document.createElement('strong'); label.textContent = route.adapter; const address = document.createElement('span'); address.textContent = `网关 ${route.gateway} · metric ${route.metric}`; row.append(label,address); $('routes').append(row); }
  text('permission', s.elevated ? '网络控制权限已就绪' : '当前为普通用户：连接与断开需要管理员权限');
  text('updated', `更新于 ${new Date(s.timestamp*1000).toLocaleTimeString()}`);
}
async function refresh() {
  if (refreshing) return;
  refreshing = true; $('refresh').disabled = true;
  try { render(await api('status')); notice(''); }
  catch (e) { notice(e.message + '。若后台已关闭，请重新打开桌面快捷方式。'); }
  finally { refreshing = false; $('refresh').disabled = false; }
}
$('refresh').onclick = refresh;
$('disable-proxy').onclick = () => action('disable-proxy');
refresh(); setInterval(refresh, 30000);
