const { chromium } = require('playwright');
const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');

const root = path.resolve(__dirname, '..');
const data = path.join(root, 'artifacts', `desktop-e2e-${Date.now()}`);
const python = process.env.PANEL_TEST_PYTHON || path.join(root, '.venv-clean', 'Scripts', 'python.exe');
const service = spawn(python, ['-m', 'server_network_assist.desktop', '--serve', '--data', data], {cwd: root, windowsHide: true, stdio: 'ignore'});
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
const longName = 'Titan-Windows-Ubuntu-很长的主机名称与出口标识-0123456789-abcdefghijklmnopqrstuvwxyz';

(async () => {
  let browser;
  try {
    const instance = path.join(data, 'desktop-instance.json');
    for (let i = 0; i < 150 && !fs.existsSync(instance); i += 1) await pause(100);
    const state = JSON.parse(fs.readFileSync(instance, 'utf8'));
    browser = await chromium.launch({headless: true});
    const base = `http://127.0.0.1:${state.port}`;
    const probe = await browser.newPage();
    assert.equal((await probe.request.get(`${base}/api/fleet/hosts`)).status(), 403, 'Fleet API must require token');
    assert.equal((await probe.request.get(`${base}/desktop.js`)).status(), 200, 'React runtime served');
    assert.equal((await probe.request.get(`${base}/desktop.css`)).status(), 200, 'Fluent stylesheet served');
    assert.equal((await probe.request.get(`${base}/framework7-bundle.min.js`, {headers: {'X-Desktop-Token': state.token}})).status(), 404, 'Framework7 runtime is no longer served');
    const html = await (await probe.request.get(base)).text();
    assert.doesNotMatch(html.toLowerCase(), /framework7/, 'Desktop document has no Framework7 dependency');
    await probe.close();

    for (const [name, width, height] of [['desktop', 1280, 960], ['mobile', 390, 844]]) {
      const page = await browser.newPage({viewport: {width, height}});
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      let active = true;
      let proxy = {supported: true, enabled: false, server: '', bypass: '<local>', scope: 'Windows 当前用户', pac: false};
      let backups = [];
      let credentials = [];
      let hosts = [];
      let profiles = [];
      let mutationCalls = [];
      let recovery = [];
      let startMode = 'Automatic';
      const campusCurrent = {state: 'online', online: true, account: 'test-campus-account', service: '校园网', ip: '192.0.2.20', internet_online: true};
      const now = Math.floor(Date.now() / 1000);

      await page.route('**/api/**', async route => {
        const request = route.request();
        const endpoint = new URL(request.url()).pathname.replace('/api/', '');
        const body = request.method() === 'POST' ? request.postDataJSON() : {};
        assert.equal(request.headers()['x-desktop-token'], state.token, `Token required for ${endpoint}`);
        if (request.method() === 'POST') mutationCalls.push({endpoint, body});
        const send = json => route.fulfill({json});
        if (endpoint === 'status') return send({hostname: longName, platform: 'Windows', version: '0.3.1', timestamp: now, elevated: true, direct: {ok: true, milliseconds: 138}, system: {ok: true, milliseconds: 152, scope: '当前用户代理'}, proxy, recovery, routes: [{adapter: 'Ethernet-long-adapter-网络适配器-012345678901234567890', gateway: '192.0.2.1', metric: 100}], tunnels: [{name: longName, active, start_mode: startMode, service_state: active ? 'Running' : 'Stopped', addresses: ['192.0.2.20'], received: 498345632, sent: 32255662, handshake: now - 26, endpoint: '192.0.2.10:51909'}], traffic: {available: true, scope: '仅为 WireGuard 隧道流量，不是整机网卡流量。', received_per_second: 2048, sent_per_second: 1024, history: [{timestamp: now - 10, received_per_second: 4096, sent_per_second: 512}, {timestamp: now - 5, received_per_second: 2048, sent_per_second: 1024}]}, background: {running: true, tray_available: true, tray_running: true, notifications_enabled: true}});
        if (endpoint === 'action') {
          if (body.action === 'disable-proxy') {backups.push({id: 'backup-1', created_at: now, platform: 'Windows', compatible: true}); proxy.enabled = false;}
          else if (body.action === 'pause-sharing') {active = false; startMode = 'Disabled'; recovery = [{name: longName, state: 'paused', start_mode: 'Automatic', was_active: true}];}
          else if (body.action === 'restore-startup') {active = true; startMode = 'Automatic'; recovery = [];}
          else active = body.action === 'connect';
          return send({ok: true});
        }
        if (endpoint === 'campus') return send({configured: true});
        if (endpoint === 'campus/status') return send(campusCurrent);
        if (endpoint === 'campus/login') {assert.equal(active, false); assert.equal(body.service, '0'); assert.equal(body.physical_network_confirmed, true); return send({ok: true, verified: true, current: campusCurrent, message: '已核对当前校园网账号与运营商。'});}
        if (endpoint === 'fleet/hosts') return send({hosts});
        if (endpoint === 'fleet/credentials') return send({credentials});
        if (endpoint === 'fleet/network') return send({profiles});
        if (endpoint === 'fleet/audit') return send({events: [{created_at: now, action: 'network.profile.save', target: longName, details: {state: 'disabled'}}]});
        if (endpoint === 'fleet/credential/save') {credentials.push({id: 'credential-1', name: body.name, kind: body.kind}); return send({ok: true});}
        if (endpoint === 'fleet/credential/delete') {credentials = []; return send({ok: true});}
        if (endpoint === 'fleet/host/save') {const host = {...body, id: body.id || `host-${hosts.length + 1}`}; hosts = hosts.filter(item => item.id !== host.id); hosts.push(host); return send({host});}
        if (endpoint === 'fleet/host/inspect') return send({host_key: 'ssh-ed25519 AAAATESTPUBLICKEY', fingerprint: 'SHA256:verified-via-server-console-123456789'});
        if (endpoint === 'fleet/host/delete') {hosts = hosts.filter(host => host.id !== body.id); return send({ok: true});}
        if (endpoint === 'fleet/network/probe') return send({results: body.ids.map(id => ({id, name: hosts.find(host => host.id === id)?.name, os: 'Windows', ssh: true, dns: true, internet: true, helper: true, ok: true, default_route: 'default via 192.0.2.1', error: ''}))});
        if (endpoint === 'fleet/network/profile/save') {const profile = {...body, id: body.id || 'profile-1', preserve_routes: Array.isArray(body.preserve_routes) ? body.preserve_routes : String(body.preserve_routes || '').split(/\s+/).filter(Boolean)}; profiles = [profile]; return send({profile});}
        if (endpoint === 'fleet/network/helper/install') return send({ok: true});
        if (endpoint === 'fleet/network/profile/enable') {profiles[0].state = 'enabled'; return send({profile: profiles[0]});}
        if (endpoint === 'fleet/network/profile/disable') {profiles[0].state = 'disabled'; return send({profile: profiles[0]});}
        if (endpoint === 'fleet/network/profile/delete') {profiles = []; return send({ok: true});}
        if (endpoint === 'proxy') return send({proxy, backups});
        if (endpoint === 'proxy/save') {backups.push({id: 'backup-1', created_at: now, platform: 'Windows', compatible: true}); proxy = {...proxy, ...body}; return send({ok: true, backup_id: 'backup-1', proxy, backups});}
        if (endpoint === 'proxy/restore') {proxy = {...proxy, enabled: false, server: ''}; return send({ok: true, proxy, backups});}
        if (endpoint === 'diagnostics') return send({status: {hostname: longName}, events: [{timestamp: now, action: 'proxy.save', outcome: 'success', detail: `长错误消息测试：${longName}`}], guidance: [{title: '联网正常', detail: '直连和系统应用探测成功。'}]});
        if (endpoint === 'updates') return send({current: '0.3.0', latest: '0.4.0', available: true, release_url: 'https://github.com/2667741708/server-network-assist/releases/tag/v0.4.0', assets: [{name: 'Windows 安装包', url: 'https://github.com/2667741708/server-network-assist/releases/download/v0.4.0/desktop.zip'}]});
        return route.fulfill({status: 404, json: {error: `Unexpected test API ${endpoint}`}});
      });

      async function nav(section) {
        if (width < 900) await page.getByRole('button', {name: '打开导航'}).click();
        await page.locator(`[data-section="${section}"]`).click();
        await page.locator(`[data-section="${section}"][aria-current="page"]`).waitFor();
      }
      async function settled(message) {await page.locator('#notice').filter({hasText: message}).waitFor();}
      async function waitForConfirmationClosed() {await page.locator('#accept').waitFor({state: 'hidden'});}
      async function resetScroll() {await page.evaluate(() => window.scrollTo(0, 0)); await page.waitForTimeout(20);}
      async function waitForPost(endpoint, predicate = () => true) {
        return page.waitForRequest(request => request.method() === 'POST' && new URL(request.url()).pathname.endsWith(`/api/${endpoint}`) && predicate(request.postDataJSON()));
      }
      async function checkLayout(label) {
        const layout = await page.evaluate(() => ({root: document.documentElement.scrollWidth, viewport: innerWidth}));
        assert.equal(layout.root > layout.viewport + 1, false, `${label} root overflow`);
        const clipped = await page.locator('.page-content h1,.page-content h2,.page-content h3,.page-content h4,.notice,.path-node').evaluateAll(elements => elements.filter(element => element.offsetParent !== null).some(element => element.scrollWidth > element.clientWidth + 2 && getComputedStyle(element).overflowX === 'hidden'));
        assert.equal(clipped, false, `${label} clipped heading or path text`);
      }

      await page.goto(`${base}/#${state.token}`);
      await page.getByRole('heading', {name: '网络已就绪', exact: true}).waitFor();
      assert.equal(await page.evaluate(() => location.hash), '', 'Token removed from URL');
      await page.locator('#traffic-chart svg').waitFor();
      await checkLayout('overview');
      await resetScroll();
      await page.screenshot({path: path.join(root, 'artifacts', `desktop-panel-${name}.png`), fullPage: true});

      await nav('tunnel-page');
      await page.getByRole('button', {name: '临时断开', exact: true}).click();
      await page.locator('#cancel').click();
      assert.equal(active, true);
      await page.getByRole('button', {name: '临时断开', exact: true}).click();
      await page.locator('#accept').click();
      await settled('隧道操作已完成');
      assert.equal(active, false);
      const connectRequest = waitForPost('action', body => body.action === 'connect');
      await page.getByRole('button', {name: '连接', exact: true}).click();
      await page.locator('#accept').click();
      await connectRequest;
      await settled('隧道操作已完成');
      assert.equal(active, true, JSON.stringify(mutationCalls.slice(-3)));
      await page.getByRole('button', {name: '停止借网', exact: true}).click();
      await page.locator('#accept').click();
      await settled('已停止本机借网');
      assert.equal(active, false);
      assert.equal(await page.getByRole('button', {name: '连接', exact: true}).isDisabled(), true);
      await page.locator('#campus-check').click();
      await settled('校园网脚本状态已读取');
      await page.locator('#campus-open').click();
      await page.locator('#campus-username').fill('test-campus-account');
      await page.locator('#campus-password').fill('not-a-real-secret');
      await page.locator('#campus-physical').check();
      await page.locator('#campus-form button[type="submit"]').click();
      await settled('校园网认证请求已完成');
      await page.locator('#campus-open').click();
      assert.equal(await page.locator('#campus-password').inputValue(), '', 'Campus password cleared after submit');
      await page.locator('#campus-form button[type="button"]').first().click();
      await page.locator('#campus-status').click();
      await settled('只读查询已完成');
      await page.getByRole('button', {name: '恢复配置', exact: true}).click();
      await page.locator('#accept').click();
      await waitForConfirmationClosed();
      await settled('隧道操作已完成');
      assert.equal(active, true);
      await checkLayout('tunnels');
      await resetScroll();
      await page.screenshot({path: path.join(root, 'artifacts', `desktop-tunnels-${name}.png`), fullPage: true});

      await nav('hosts-page');
      await page.locator('#credentials-tab').click();
      await page.getByRole('button', {name: '新增凭据', exact: true}).click();
      await page.locator('#credential-name').fill('主机连接凭据');
      await page.locator('#credential-secret').fill('test-password-not-real');
      await page.getByRole('button', {name: '加密保存', exact: true}).click();
      await settled('凭据已加密保存');
      await page.getByRole('button', {name: '新增凭据', exact: true}).click();
      assert.equal(await page.locator('#credential-secret').inputValue(), '', 'Credential secret cleared after save');
      await page.keyboard.press('Escape');
      await page.locator('#hosts-tab').click();

      for (const [hostName, address] of [[longName, '192.0.2.10'], ['Ubuntu 客户端', '192.0.2.20']]) {
        await page.getByRole('button', {name: '新增主机', exact: true}).click();
        await page.locator('#host-name').fill(hostName);
        await page.locator('#host-address').fill(address);
        await page.locator('#host-username').fill('operator');
        await page.locator('#host-credential_id').selectOption('credential-1');
        await page.getByRole('button', {name: '保存主机', exact: true}).click();
        await settled('主机已保存');
        const row = page.locator('#hosts tbody tr').filter({hasText: hostName});
        await row.getByRole('button', {name: '编辑', exact: true}).click();
        await page.getByRole('button', {name: '读取远端指纹', exact: true}).click();
        await settled('已读取远端公钥');
        const count = mutationCalls.length;
        await page.getByRole('button', {name: '保存主机', exact: true}).click();
        await settled('请先通过可信渠道');
        assert.equal(mutationCalls.length, count, 'Unconfirmed key must not save');
        await page.locator('#fingerprint-confirmed').check();
        await page.getByRole('button', {name: '保存主机', exact: true}).click();
        await settled('主机已保存');
      }
      await checkLayout('hosts');
      await resetScroll();
      await page.screenshot({path: path.join(root, 'artifacts', `desktop-hosts-${name}.png`), fullPage: true});

      await nav('sharing');
      await page.locator('#new-profile').click();
      await page.locator('#profile-name').fill('Windows → Ubuntu 双系统共享与原网络恢复');
      await page.locator('#profile-gateway_id').selectOption('host-1');
      await page.locator('#profile-client_ids').selectOption('host-2');
      await page.locator('#profile-proxy_mode').selectOption('share');
      await page.locator('#profile-proxy_host').fill('127.0.0.1');
      await page.locator('#profile-proxy_port').fill('7897');
      await page.getByRole('button', {name: '保存方案', exact: true}).click();
      await settled('共享方案已保存');
      assert.equal(profiles[0].proxy_mode, 'share');
      assert.deepEqual(profiles[0].client_ids, ['host-2']);
      await page.getByRole('button', {name: '安装辅助程序', exact: true}).click();
      await page.locator('#cancel').click();
      assert.equal(mutationCalls.some(call => call.endpoint.endsWith('helper/install')), false);
      await page.getByRole('button', {name: '安装辅助程序', exact: true}).click();
      await page.locator('#accept').click();
      await settled('辅助程序安装请求已完成');
      await page.getByRole('button', {name: '探测出口与客户端', exact: true}).click();
      await settled('已完成所选主机的实际探测');
      await page.getByRole('button', {name: '启用共享', exact: true}).click();
      await page.locator('#cancel').click();
      assert.equal(profiles[0].state, 'disabled');
      await page.getByRole('button', {name: '启用共享', exact: true}).click();
      await page.locator('#accept').click();
      await settled('共享方案启用请求已完成');
      assert.equal(profiles[0].state, 'enabled');
      await page.getByRole('button', {name: '停止并恢复', exact: true}).click();
      await page.locator('#accept').click();
      await waitForConfirmationClosed();
      await settled('共享方案恢复请求已完成');
      assert.equal(profiles[0].state, 'disabled');
      await checkLayout('sharing');
      await resetScroll();
      await page.screenshot({path: path.join(root, 'artifacts', `desktop-sharing-${name}.png`), fullPage: true});

      await nav('proxy-page');
      await page.locator('#save-proxy').click();
      await page.locator('#proxy-enabled').check();
      await page.locator('#proxy-server').fill('127.0.0.1:7897');
      await page.getByRole('button', {name: '备份并保存', exact: true}).click();
      await page.locator('#cancel').click();
      assert.equal(proxy.enabled, false);
      await page.reload();
      await page.getByRole('heading', {name: '网络已就绪', exact: true}).waitFor();
      await nav('proxy-page');
      await page.locator('#save-proxy').click();
      await page.locator('#proxy-enabled').check();
      await page.locator('#proxy-server').fill('127.0.0.1:7897');
      await page.getByRole('button', {name: '备份并保存', exact: true}).click();
      await page.locator('#accept').click();
      await waitForConfirmationClosed();
      await settled('已备份原配置并保存代理');
      assert.equal(proxy.enabled, true);
      await page.getByRole('button', {name: '恢复', exact: true}).first().click();
      await page.locator('#accept').click();
      await waitForConfirmationClosed();
      await settled('代理备份已恢复');
      assert.equal(proxy.enabled, false);
      await checkLayout('proxy');
      await resetScroll();
      await page.screenshot({path: path.join(root, 'artifacts', `desktop-proxy-${name}.png`), fullPage: true});

      proxy = {...proxy, supported: false, enabled: null, error: '当前会话没有 GNOME 设置服务'};
      await page.locator('#load-proxy').click();
      await settled('代理和备份已重新读取');
      assert.match(await page.locator('#proxy-state').innerText(), /无法读取/);
      assert.equal(await page.locator('#save-proxy').isDisabled(), true);

      await nav('diagnostics');
      await page.getByRole('button', {name: '导出报告', exact: true}).waitFor();
      await page.getByText('联网正常', {exact: true}).waitFor();
      const download = page.waitForEvent('download');
      await page.getByRole('button', {name: '导出报告', exact: true}).click();
      assert.match((await download).suggestedFilename(), /network-diagnostics/);
      await checkLayout('diagnostics');
      await resetScroll();
      await page.screenshot({path: path.join(root, 'artifacts', `desktop-diagnostics-${name}.png`), fullPage: true});

      await nav('settings');
      await page.getByRole('button', {name: '检查更新', exact: true}).click();
      await settled('更新检查已完成');
      assert.match(await page.locator('#update-state').innerText(), /有更新/);
      assert.equal(await page.locator('#update-links a').count(), 2);
      await checkLayout('settings');

      await page.route('**/api/fleet/hosts', route => route.fulfill({status: 400, json: {error: '模拟凭据库读取失败，请检查目录权限。'}}));
      await nav('hosts-page');
      await settled('模拟凭据库读取失败');
      await page.unroute('**/api/fleet/hosts');

      await page.locator('#theme').selectOption('dark');
      assert.equal(await page.locator('body').evaluate(element => element.classList.contains('sna-dark')), true);
      await nav('overview');
      await page.reload();
      await page.getByRole('heading', {name: '网络已就绪', exact: true}).waitFor();
      assert.equal(await page.locator('body').evaluate(element => element.classList.contains('sna-dark')), true);
      await page.screenshot({path: path.join(root, 'artifacts', `desktop-panel-${name}-dark.png`), fullPage: true});
      assert.deepEqual(errors, []);
      await page.close();
      console.log(`${name}: navigation, long values, credentials, host fingerprint warning, sharing lifecycle, proxy backup/restore, diagnostics export, updates, failures, cancellation, responsive layout and theme passed`);
    }
  } finally {
    if (browser) await browser.close();
    service.kill();
  }
})().catch(error => {console.error(error); process.exitCode = 1;});
