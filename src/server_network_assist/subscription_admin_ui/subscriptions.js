/* Subscription admin console for the unified commercial control line.
 * Talks to /api/session, /api/login, /api/client-service, /api/client-service/action
 * and /api/client-service/access-log. All mutations ride the server's 5-minute
 * fresh() gate; a single re-auth dialog restores it instead of per-action logins.
 */
'use strict';
(function () {
  const $ = (id) => document.getElementById(id);
  let csrf = '';
  let authenticated = false;
  let busy = false;
  let pendingRetry = null;
  let reauthOpen = false;
  let snapshot = null;
  let accessPage = { limit: 50, offset: 0, total: 0 };
  const PRESET_FALLBACK = ['http://10.20.32.13:9182', 'https://10.20.32.13:8443', 'https://whm12.art'];
  const FRESH_TEXT = '请重新验证管理员密码';
  const CSRF_TEXT = '登录验证已更新';

  function message(text) {
    const el = $('message');
    el.textContent = text;
    el.hidden = !text;
  }
  function showAddress(result, title) {
    if (typeof result.url !== 'string' || !result.url.includes('#enroll=')) {
      message('服务端没有返回完整订阅地址，请先核对客户列表，避免重复开户。');
      return;
    }
    $('subscription-url').value = result.url;
    $('address-dialog-title').textContent = title;
    const status = (snapshot && snapshot.subscription_addresses && snapshot.subscription_addresses[result.customer_id]
      ? snapshot.subscription_addresses[result.customer_id].status : '') || '';
    $('address-status').textContent = status ? '状态：' + status : '';
    const rows = [];
    if (result.customer_id) rows.push('客户编号：' + result.customer_id);
    if (result.enrollment_expires_at) rows.push('开户地址有效至：' + new Date(result.enrollment_expires_at * 1000).toLocaleString());
    if (result.expires_at) rows.push('套餐到期：' + new Date(result.expires_at * 1000).toLocaleString());
    $('address-expiry').textContent = rows.join('\n');
    $('address-dialog').showModal();
  }

  async function request(path, body, options) {
    const opts = options || {};
    const headers = { Accept: 'application/json' };
    let payload;
    if (body !== undefined) {
      headers['Content-Type'] = 'application/json';
      headers['X-CSRF-Token'] = csrf;
      payload = { method: 'POST', body: JSON.stringify(body), headers };
    } else {
      payload = { headers };
    }
    payload.credentials = 'same-origin';
    const controller = new AbortController();
    payload.signal = controller.signal;
    const timer = setTimeout(() => controller.abort(), body === undefined ? 15000 : 45000);
    let response;
    try {
      response = await fetch(path, payload);
    } catch (error) {
      clearTimeout(timer);
      throw new Error(body === undefined ? '请求超时，请刷新重试。' : '请求超时，操作可能已提交。请刷新核对结果，勿重复提交。');
    }
    clearTimeout(timer);
    let value = null;
    try { value = await response.json(); } catch (e) { /* non-JSON */ }
    if (response.status === 401 || (response.status === 403 && value && typeof value === 'object' && String(value.error || '').includes(FRESH_TEXT))) {
      if (opts.noReauth) throw new Error(value && value.error ? value.error : '会话已过期');
      if (body !== undefined && !pendingRetry) {
        pendingRetry = { path, body, options: { noReauth: true } };
        openReauth();
      }
      throw new Error('需要重新验证身份');
    }
    if (response.status === 403 && value && typeof value === 'object' && String(value.error || '').includes(CSRF_TEXT)) {
      message('登录验证已更新，页面即将刷新。');
      setTimeout(() => location.reload(), 800);
      throw new Error('登录验证已更新，请刷新页面');
    }
    if (!response.ok) {
      throw new Error(value && value.error ? value.error : ('请求失败（' + response.status + '）'));
    }
    return value;
  }

  function openReauth() {
    if (reauthOpen) return;
    reauthOpen = true;
    $('reauth-status').textContent = '';
    $('reauth-form').elements.password.value = '';
    $('reauth-form').elements.key.value = '';
    $('reauth-dialog').showModal();
  }
  function closeReauth() {
    reauthOpen = false;
    pendingRetry = null;
    $('reauth-dialog').close();
  }
  async function submitReauth() {
    const form = new FormData($('reauth-form'));
    $('reauth-status').textContent = '';
    try {
      const session = await request('api/login', {
        username: form.get('username'),
        password: form.get('password'),
        key: form.get('key'),
        remember: 30,
      }, { noReauth: true });
      csrf = session.csrf || '';
      authenticated = true;
      const retry = pendingRetry;
      pendingRetry = null;
      reauthOpen = false;
      $('reauth-dialog').close();
      if (retry) {
        const value = await request(retry.path, retry.body, retry.options);
        message('身份已验证，操作已继续。');
        return value;
      }
      await refresh();
    } catch (error) {
      $('reauth-status').textContent = error.message;
    }
  }

  function inputSpeed(value) {
    if (value === null || value === '') return null;
    const rate = Math.round(Number(value) * 1e6);
    if (!Number.isSafeInteger(rate) || rate < 1 || rate > 1e10) throw new Error('速度须大于零；解除限制请勾选不限速。');
    return rate;
  }
  function validityDays(form) {
    const select = form.get('validity_days');
    if (select === 'custom') {
      const days = Number(form.get('validity_custom'));
      if (!Number.isInteger(days) || days < 1 || days > 3650) throw new Error('自定义有效期必须是 1–3650 的整数天数');
      return days;
    }
    return Number(select);
  }
  function checkedSources(containerId) {
    const ids = [];
    document.querySelectorAll('#' + containerId + ' input[name=source]:checked').forEach((input) => ids.push(input.value));
    if (!ids.length) throw new Error('请至少选择一个源网节点。');
    return ids;
  }
  function egressModes(form) {
    return form.get('dual_egress') ? ['source_physical', 'source_proxy'] : undefined;
  }
  function renderSources(containerId) {
    const container = $(containerId);
    container.replaceChildren();
    for (const source of (snapshot ? snapshot.sources || [] : [])) {
      const label = document.createElement('label');
      label.className = 'form-check col-md-6';
      const input = document.createElement('input');
      input.className = 'form-check-input';
      input.type = 'checkbox';
      input.name = 'source';
      input.value = source.id;
      label.append(input);
      label.append(document.createTextNode(source.name + '\n' + (source.endpoint || '') + ' · ' + source.id));
      container.append(label);
    }
    if (!(snapshot && snapshot.sources && snapshot.sources.length)) {
      container.textContent = '尚未登记源网节点，不能生成订阅。';
    }
  }
  function fillPresets() {
    const list = $('base-url-presets');
    list.replaceChildren();
    for (const value of (snapshot && snapshot.base_url_presets ? snapshot.base_url_presets : PRESET_FALLBACK)) {
      const option = document.createElement('option');
      option.value = value;
      list.append(option);
    }
  }
  function renderCustomers() {
    const tbody = $('customers-rows');
    tbody.replaceChildren();
    const customers = snapshot ? snapshot.customers || [] : [];
    const addresses = snapshot ? snapshot.subscription_addresses || {} : {};
    const devices = snapshot ? snapshot.devices || [] : [];
    const grants = snapshot ? snapshot.grants || [] : [];
    for (const customer of customers) {
      const status = addresses[customer.id];
      const statusName = status ? status.status : 'not-recorded';
      const statusText = { ready: '可用', used: '已兑换', expired: '已失效', 'not-recorded': '未留存' }[statusName] || statusName;
      const row = document.createElement('tr');
      const info = document.createElement('td');
      info.textContent = customer.display_name + '\n' + customer.id + ' · ' + (customer.enabled ? '启用' : '停用');
      row.append(info);
      const cell = document.createElement('td');
      cell.textContent = statusText;
      if (statusName === 'ready') cell.className = 'text-success';
      if (statusName === 'used') cell.className = 'text-secondary';
      if (statusName === 'expired') cell.className = 'text-warning';
      row.append(cell);
      const usage = document.createElement('td');
      const devCount = devices.filter((device) => device.customer_id === customer.id).length;
      const grantCount = grants.filter((grant) => grant.customer_id === customer.id).length;
      usage.textContent = '设备 ' + devCount + ' · 线路 ' + grantCount;
      row.append(usage);
      const actions = document.createElement('td');
      for (const [label, cls, enabled, handler] of [
        ['查看地址', 'btn-outline-secondary btn-sm', statusName !== 'not-recorded', () => viewAddress(customer)],
        ['补发', 'btn-outline-secondary btn-sm', true, () => reissue(customer)],
        ['延长 24h', 'btn-outline-secondary btn-sm', statusName === 'expired', () => restoreAddress(customer)],
        ['销毁地址', 'btn-outline-danger btn-sm', statusName === 'ready', () => expireAddress(customer)],
      ]) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'btn ' + cls + ' me-1';
        button.textContent = label;
        button.disabled = busy || !enabled;
        button.onclick = handler;
        actions.append(button);
      }
      row.append(actions);
      tbody.append(row);
    }
    if (!customers.length) tbody.innerHTML = '<tr><td colspan="4" class="text-secondary">暂无客户</td></tr>';
  }

  async function refresh() {
    if (busy) return;
    try {
      const value = await request('api/client-service');
      snapshot = value;
      csrf = csrf || (await request('api/session')).csrf || '';
      fillPresets();
      renderSources('sources');
      renderSources('batch-sources');
      renderCustomers();
      $('login-card').hidden = true;
      $('workspace').hidden = false;
      $('logout').hidden = false;
      message('');
    } catch (error) {
      message(error.message);
    }
  }

  async function login(event) {
    event.preventDefault();
    const form = new FormData(event.target);
    try {
      const session = await request('api/login', {
        username: form.get('username'),
        password: form.get('password'),
        key: form.get('key'),
        remember: form.get('remember') ? 30 : 0,
      }, { noReauth: true });
      csrf = session.csrf || '';
      authenticated = true;
      await refresh();
      event.target.elements.password.value = '';
      event.target.elements.key.value = '';
    } catch (error) {
      message(error.message);
    }
  }
  async function logout() {
    try { await request('api/logout', {}); } catch (error) { /* already gone */ }
    authenticated = false;
    csrf = '';
    snapshot = null;
    $('workspace').hidden = true;
    $('logout').hidden = true;
    $('login-card').hidden = false;
  }

  async function generate(event) {
    event.preventDefault();
    const form = new FormData(event.target);
    $('generation-status').textContent = '正在签发，请稍候……';
    try {
      const unlimitedSpeed = form.has('unlimited_speed');
      const result = await run({ action: 'subscription-generate', name: form.get('name'),
        base_url: form.get('base_url'), source_ids: checkedSources('sources'),
        quota_gb: form.get('quota') === 'unlimited' ? null : Number(form.get('quota')),
        download_bps: unlimitedSpeed ? null : inputSpeed(form.get('download')),
        upload_bps: unlimitedSpeed ? null : inputSpeed(form.get('upload')),
        validity_days: validityDays(form), egress_modes: egressModes(form) });
      showAddress(result, '生成成功 · 您的订阅地址');
      $('generation-status').textContent = '生成成功，完整地址已显示。';
      await refresh();
    } catch (error) {
      $('generation-status').textContent = '尚未生成：' + error.message;
      message(error.message);
    }
  }

  async function run(body) {
    busy = true;
    try {
      return await request('api/client-service/action', body);
    } finally {
      busy = false;
    }
  }

  async function batchGenerate(event) {
    event.preventDefault();
    const form = new FormData(event.target);
    $('batch-status').textContent = '正在批量签发，请稍候……';
    const names = String(form.get('names') || '')
      .split('\n').map((value) => value.trim()).filter((value) => value);
    if (!names.length) { $('batch-status').textContent = '请至少输入一个客户名称。'; return; }
    if (names.length > 100) { $('batch-status').textContent = '一次最多 100 个客户名称。'; return; }
    if (new Set(names).size !== names.length) { $('batch-status').textContent = '列表包含重复客户名称。'; return; }
    try {
      const unlimitedSpeed = form.has('unlimited_speed');
      const result = await run({ action: 'subscription-generate-batch', names,
        base_url: form.get('base_url'), source_ids: checkedSources('batch-sources'),
        quota_gb: form.get('quota') === 'unlimited' ? null : Number(form.get('quota')),
        download_bps: unlimitedSpeed ? null : inputSpeed(form.get('download')),
        upload_bps: unlimitedSpeed ? null : inputSpeed(form.get('upload')),
        validity_days: validityDays(form), egress_modes: egressModes(form) });
      renderBatchResults(result.results || []);
      storeBatchResults(result.results || []);
      $('batch-status').textContent = '完成：成功 ' + result.succeeded + ' · 失败 ' + result.failed;
      $('batch-results').hidden = false;
      await refresh();
    } catch (error) {
      $('batch-status').textContent = '批量签发失败：' + error.message;
      message(error.message);
    }
  }
  function renderBatchResults(results) {
    const tbody = $('batch-rows');
    tbody.replaceChildren();
    for (const row of results) {
      const tr = document.createElement('tr');
      const nameCell = document.createElement('td');
      nameCell.textContent = row.name;
      tr.append(nameCell);
      const statusCell = document.createElement('td');
      statusCell.textContent = row.ok ? '成功' : '失败';
      statusCell.className = row.ok ? 'text-success' : 'text-danger';
      tr.append(statusCell);
      const detail = document.createElement('td');
      if (row.ok) {
        const url = document.createElement('textarea');
        url.className = 'form-control form-control-sm';
        url.rows = 2;
        url.readOnly = true;
        url.value = row.url;
        detail.append(document.createTextNode(row.customer_id + ' '));
        detail.append(url);
      } else {
        detail.textContent = row.error || '未知错误';
      }
      tr.append(detail);
      tbody.append(tr);
    }
  }
  function csvEscape(value) {
    const text = String(value == null ? '' : value);
    if (/^[=+\-@]/.test(text)) return "'" + text;
    return '"' + text.replace(/"/g, '""') + '"';
  }
  function batchResultsData() {
    return (JSON.parse(sessionStorage.getItem('batchResults') || 'null')) || [];
  }
  function downloadText(filename, content, mime) {
    const blob = new Blob([content], { type: mime || 'text/plain;charset=utf-8' });
    const link = document.createElement('a');
    link.href = URL.createObjectURL(blob);
    link.download = filename;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(link.href), 5000);
  }
  function storeBatchResults(results) {
    sessionStorage.setItem('batchResults', JSON.stringify(results));
  }
  function copyAllAddresses() {
    const results = batchResultsData();
    const lines = results.filter((row) => row.ok).map((row) => row.url);
    downloadText('订阅地址-' + lines.length + '条.txt', lines.join('\n') + '\n');
    message('已导出 ' + lines.length + ' 条地址。');
  }
  function exportCsv() {
    const results = batchResultsData();
    const lines = ['客户名称,结果,客户编号,订阅地址,错误'];
    for (const row of results) {
      lines.push([csvEscape(row.name), row.ok ? '成功' : '失败', csvEscape(row.customer_id || ''),
        row.ok ? csvEscape(row.url) : '', row.ok ? '' : csvEscape(row.error || '')].join(','));
    }
    downloadText('订阅地址-' + results.length + '条.csv', '﻿' + lines.join('\r\n') + '\r\n', 'text/csv;charset=utf-8');
  }

  async function viewAddress(customer) {
    try {
      const result = await run({ action: 'subscription-view', id: customer.id });
      showAddress(result, '已保存的订阅地址 · ' + customer.display_name);
    } catch (error) { message(error.message); }
  }
  async function reissue(customer) {
    try {
      const result = await run({ action: 'subscription-reissue', id: customer.id, base_url: $('reissue-base').value });
      showAddress(result, '补发成功 · ' + customer.display_name);
      await refresh();
    } catch (error) { message(error.message); }
  }
  async function restoreAddress(customer) {
    try {
      const result = await run({ action: 'subscription-address-restore', id: customer.id });
      message('原开户地址已延长 24 小时（地址不变）。');
      await refresh();
    } catch (error) { message(error.message); }
  }
  async function expireAddress(customer) {
    if (!window.confirm('确定销毁 ' + customer.display_name + ' 的未兑换开户地址？销毁后如需使用必须补发新地址。')) return;
    try {
      await run({ action: 'subscription-address-expire', id: customer.id });
      message('原开户地址已销毁；如需新地址可补发。');
      await refresh();
    } catch (error) { message(error.message); }
  }

  async function loadAccessLog() {
    const params = new URLSearchParams();
    if ($('access-customer').value) params.set('customer_id', $('access-customer').value.trim());
    if ($('access-device').value) params.set('device_id', $('access-device').value.trim());
    if ($('access-from').value) params.set('from', String(Math.floor(new Date($('access-from').value).getTime() / 1000)));
    if ($('access-to').value) params.set('to', String(Math.floor(new Date($('access-to').value).getTime() / 1000)));
    params.set('limit', String(accessPage.limit));
    params.set('offset', String(accessPage.offset));
    try {
      const value = await request('api/client-service/access-log?' + params.toString());
      const tbody = $('access-rows');
      tbody.replaceChildren();
      for (const row of value.rows || []) {
        const tr = document.createElement('tr');
        const time = document.createElement('td');
        time.textContent = new Date(row.created_at * 1000).toLocaleString();
        tr.append(time);
        const customer = document.createElement('td');
        customer.textContent = (row.customer_name || '') + '\n' + row.customer_id;
        tr.append(customer);
        const device = document.createElement('td');
        device.textContent = row.device_id || '（未开户设备）';
        tr.append(device);
        const ip = document.createElement('td');
        ip.textContent = row.remote_ip;
        tr.append(ip);
        const method = document.createElement('td');
        method.textContent = row.method;
        tr.append(method);
        const path = document.createElement('td');
        path.textContent = row.path;
        tr.append(path);
        tbody.append(tr);
      }
      if (!(value.rows || []).length) tbody.innerHTML = '<tr><td colspan="6" class="text-secondary">没有符合条件的记录</td></tr>';
      accessPage.total = value.total || 0;
      $('access-count').textContent = '共 ' + accessPage.total + ' 条 · 第 ' +
        (Math.floor(accessPage.offset / accessPage.limit) + 1) + ' 页';
      $('access-prev').disabled = accessPage.offset <= 0;
      $('access-next').disabled = accessPage.offset + accessPage.limit >= accessPage.total;
    } catch (error) {
      message(error.message);
    }
  }

  $('login-form').onsubmit = login;
  $('logout').onclick = logout;
  $('generate-form').onsubmit = generate;
  $('batch-form').onsubmit = batchGenerate;
  $('customers-refresh').onclick = () => refresh().catch((error) => message(error.message));
  $('access-query').onclick = () => { accessPage.offset = 0; loadAccessLog(); };
  $('access-prev').onclick = () => { accessPage.offset = Math.max(0, accessPage.offset - accessPage.limit); loadAccessLog(); };
  $('access-next').onclick = () => { accessPage.offset += accessPage.limit; loadAccessLog(); };
  $('copy').onclick = () => {
    const url = $('subscription-url');
    url.focus({ preventScroll: true });
    url.select();
    if (navigator.clipboard) navigator.clipboard.writeText(url.value).then(() => message('订阅地址已复制。'), () => message('请手动复制已选中的地址。'));
    else message('请手动复制已选中的地址。');
  };
  $('address-dialog-close').onclick = () => $('address-dialog').close();
  $('address-dialog-cancel').onclick = () => $('address-dialog').close();
  $('reauth-submit').onclick = submitReauth;
  $('reauth-cancel').onclick = closeReauth;
  $('batch-copy').onclick = copyAllAddresses;
  $('batch-export').onclick = exportCsv;
  const generateForm = $('generate-form');
  generateForm.elements.validity_days.onchange = () => {
    $('validity-custom-wrap').hidden = generateForm.elements.validity_days.value !== 'custom';
  };
  for (const input of generateForm.querySelectorAll('[name=download],[name=upload]')) {
    input.disabled = generateForm.elements.unlimited_speed.checked;
  }
  generateForm.elements.unlimited_speed.onchange = () => {
    for (const input of generateForm.querySelectorAll('[name=download],[name=upload]')) {
      input.disabled = generateForm.elements.unlimited_speed.checked;
    }
  };
  for (const checkbox of document.querySelectorAll('[name=dual_egress]')) {
    checkbox.onchange = () => {
      document.querySelectorAll('[name=dual_egress]').forEach((other) => { other.checked = checkbox.checked; });
    };
  }

  (async function boot() {
    try {
      const session = await request('api/session');
      authenticated = !!session.authenticated;
      csrf = session.csrf || '';
      if (authenticated) { await refresh(); } else { $('login-card').hidden = false; }
    } catch (error) {
      $('login-card').hidden = false;
      message(error.message);
    }
  })();
})();
