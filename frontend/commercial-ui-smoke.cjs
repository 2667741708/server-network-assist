const { chromium } = require('playwright');
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

const root = path.resolve(__dirname, 'dist/frontend/browser');
const artifacts = path.resolve(__dirname, '../artifacts/commercial-ui');
const now = Math.floor(Date.now() / 1000);
const customer = { id: 'cus-fixture-001', display_name: '长名称客户 · LanBridge 校园网络服务', plan_id: 'plan-fixture-50', enabled: true, revoked_at: null, created_at: now - 86400, lifecycle: 'active', tags: ['fixture'] };
const plan = { id: 'plan-fixture-50', name: '50 GB / 50 Mbps', download_bps: 50000000, upload_bps: 10000000, quota_bytes: 50 * 1024 ** 3, period_seconds: 30 * 86400, max_devices: 3, lease_seconds: 900, enabled: true, created_at: now - 86400 };
const source = { id: 'node-fixture-001', name: '校园 WireGuard 节点 · 长名称测试', endpoint: '198.51.100.20:51820', relay_public_key: 'fixture-relay-public', address_pool: '10.203.20.0/24', relay_interface: 'wg-campus', egress_interface: 'enp4s0', egress_mode: 'physical', egress_gateway: '198.51.100.1', dns: '1.1.1.1,223.5.5.5', enabled: true, archived: false, revision: 'source-revision-fixture-001', proxy_interface: 'Meta' };
const device = { id: 'dev-fixture-001', customer_id: customer.id, label: 'Windows 办公室笔记本', public_key: 'cHVibGljLWtleS1maXh0dXJl', wireguard_public_key: 'd2lyZWd1YXJkLWZpeHR1cmU', enabled: true, revoked_at: null, created_at: now - 3600, last_seen_at: now - 60 };
const grant = { id: 'grant-fixture-001', customer_id: customer.id, alias: source.name, tunnel: 'customer-online', endpoint: source.endpoint, enabled: true, relay_public_key: source.relay_public_key, allocated_address: '10.203.20.2/32', dns: source.dns, allowed_ips: '0.0.0.0/1,128.0.0.0/1', mtu: 1420, relay_interface: source.relay_interface, egress_interface: source.egress_interface, egress_policy: JSON.stringify({ source_id: source.id, egress_mode: 'physical', egress_gateway: source.egress_gateway }), expires_at: now + 20 * 86400, created_at: now - 86400 };
const lease = { id: 'lease-fixture-001', customer_id: customer.id, device_id: device.id, grant_id: grant.id, issued_at: now - 120, expires_at: now + 780, revoked_at: null, last_rx: 1024 * 1024, last_tx: 2048 * 1024 };
let customers = [customer];
let devices = [device];
let leases = [lease];
const audit = [{ id: 1, created_at: now - 120, actor: 'admin', action: 'client_source_saved', target: source.id, details: { revision: source.revision, authorization: 'should-not-render' }, remote_ip: '127.0.0.1' }];

const mime = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.css': 'text/css', '.ico': 'image/x-icon', '.png': 'image/png', '.woff': 'font/woff2' };
const server = http.createServer((request, response) => {
  let pathname = decodeURIComponent(new URL(request.url, 'http://127.0.0.1').pathname);
  if (pathname === '/' || !pathname.includes('.')) pathname = '/index.html';
  const file = path.resolve(root, `.${pathname}`);
  if (!file.startsWith(root) || !fs.existsSync(file) || !fs.statSync(file).isFile()) { response.writeHead(404); response.end(); return; }
  response.writeHead(200, { 'Content-Type': mime[path.extname(file)] || 'application/octet-stream' });
  fs.createReadStream(file).pipe(response);
});

function dashboardCustomer() {
  const currentCustomer = customers.find((item) => item.id === customer.id) || customer;
  return { ...currentCustomer, plan, usage: { used_bytes: lease.last_rx + lease.last_tx, remaining_bytes: plan.quota_bytes - lease.last_rx - lease.last_tx, quota_bytes: plan.quota_bytes, today_bytes: 0, total_bytes: lease.last_rx + lease.last_tx, measurement_status: 'fresh', last_report_at: now - 60 }, grants: [{ id: grant.id, name: grant.alias, source_id: source.id, source_name: source.name, endpoint: grant.endpoint, enabled: true, expires_at: grant.expires_at, egress_mode: 'physical', available: true, reasons: [] }], devices, leases, active_lease_count: leases.filter(item => !item.revoked_at && item.expires_at > now).length, usable: currentCustomer.enabled, reasons: currentCustomer.enabled ? [] : ['客户服务已停用'], version: 1, revision: 'customer-revision-fixture-001' };
}

async function main() {
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  const base = `http://127.0.0.1:${server.address().port}`;
  const browser = await chromium.launch({ headless: true });
  try {
    for (const [name, width, height] of [['desktop', 1440, 960], ['mobile', 390, 844]]) {
      customers = [{ ...customer }];
      devices = [{ ...device }];
      leases = [{ ...lease }];
      const page = await browser.newPage({ viewport: { width, height } });
      const errors = [];
      const actions = [];
      page.on('pageerror', (error) => errors.push(error.message));
      await page.route('**/api/**', async (route) => {
        const request = route.request();
        const pathname = new URL(request.url()).pathname;
        const body = request.method() === 'POST' ? (request.postDataJSON() || {}) : {};
        const send = (json) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(json) });
        if (request.method() === 'POST') actions.push({ pathname, body });
        if (pathname === '/api/session') return send({ authenticated: true, csrf: 'fixture-csrf', passkeys: false, secure: true, version: 'fixture' });
        if (pathname === '/api/hosts') return send({ hosts: [] });
        if (pathname === '/api/credentials') return send({ credentials: [] });
        if (pathname === '/api/network') return send({ profiles: [] });
        if (pathname === '/api/security') return send({ devices: [], passkeys: [], key_enabled: false, verified: true });
        if (pathname === '/api/audit') return send({ events: audit });
        if (pathname === '/api/client-service') return send({ sources: [source], plans: [plan], customers, subscription_addresses: [{ customer_id: customer.id, expires_at: now + 3600, used_at: null, created_at: now - 86400, available: true, can_reissue: true, status: 'ready' }], devices, grants: [grant], leases, source_management_supported: true, source_proxy: { available: false, running: false, tun_ready: false, reason: 'fixture: 未启动' }, physical_defaults: {}, local_proxy_source_ids: [] });
        if (pathname === '/api/client-service/dashboard') return send({ generated_at: now, customers: customers.map((item) => item.id === customer.id ? dashboardCustomer() : item), summary: { customers: customers.length, usable: customers.filter((item) => item.enabled).length, active_leases: leases.filter((item) => !item.revoked_at && item.expires_at > now).length, today_bytes: 0, total_bytes: 0 }, measurement_note: 'fixture' });
        if (pathname === '/api/reauth') return send({ ok: true });
        if (pathname === '/api/client-service/action') {
          if (body.action === 'customer-enable') { customers = customers.map((item) => item.id === body.id ? { ...item, enabled: body.enabled, revoked_at: body.enabled ? null : now } : item); if (!body.enabled) leases = leases.map((item) => item.customer_id === body.id ? { ...item, revoked_at: now } : item); }
          if (body.action === 'device-enable') { devices = devices.map((item) => item.id === body.id ? { ...item, enabled: body.enabled, revoked_at: body.enabled ? null : now } : item); }
          return send({ ok: true });
        }
        return route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ error: `unexpected ${pathname}` }) });
      });

      await page.goto(`${base}/?view=overview`, { waitUntil: 'networkidle' });
      const nav = page.locator(width < 720 ? '.mobile-nav' : '.sidebar');
      await page.getByRole('heading', { name: '商业服务概览', exact: true }).waitFor();
      await page.waitForTimeout(450);
      await page.screenshot({ path: path.join(artifacts, `overview-${name}.png`), fullPage: true });

      const visit = async (label, heading, file) => {
        await nav.locator('a').filter({ hasText: label }).click();
        await page.waitForFunction((value) => Array.from(document.querySelectorAll('h1')).some((element) => {
          const style = getComputedStyle(element);
          return style.display !== 'none' && element.textContent?.trim() === value;
        }), heading);
        await page.waitForTimeout(450);
        await page.screenshot({ path: path.join(artifacts, `${file}-${name}.png`), fullPage: true });
      };

      await visit('客户', '客户', 'customers');
      await page.getByRole('button', { name: /长名称客户/ }).click();
      await page.getByRole('tab', { name: '概览' }).count().catch(() => 0);
      await page.getByRole('button', { name: '停用客户服务' }).click();
      await page.getByText('当前租约会失效', { exact: false }).waitFor();
      assert.equal(actions.filter((item) => item.pathname === '/api/client-service/action').length, 0, 'confirmation must precede action');
      await page.getByLabel('管理员密码').fill('fixture-password');
      await page.getByRole('button', { name: '确认并执行' }).click();
      await page.getByText('客户服务已停用，活动租约已撤销', { exact: true }).waitFor();
      await page.getByText('服务停用', { exact: true }).first().waitFor();
      await page.getByRole('button', { name: '恢复客户服务' }).waitFor();
      assert.ok(actions.some((item) => item.pathname === '/api/reauth'));
      assert.ok(actions.some((item) => item.pathname === '/api/client-service/action' && item.body.action === 'customer-enable'));
      await page.screenshot({ path: path.join(artifacts, `customer-detail-${name}.png`), fullPage: true });
      await page.getByRole('button', { name: '关闭' }).click();

      await visit('订阅与套餐', '订阅与套餐', 'subscriptions');
      await page.getByText('选择客户', { exact: false }).count().catch(() => 0);
      await page.screenshot({ path: path.join(artifacts, `subscriptions-${name}.png`), fullPage: true });
      await visit('设备', '设备', 'devices');
      await page.getByText('签名公钥指纹', { exact: false }).waitFor();
      await page.screenshot({ path: path.join(artifacts, `devices-${name}.png`), fullPage: true });
      await visit('节点与出口', '节点与出口', 'nodes');
      await page.locator('mat-expansion-panel').first().locator('mat-expansion-panel-header').click();
      await page.getByText('Transport · WireGuard', { exact: true }).waitFor();
      await page.screenshot({ path: path.join(artifacts, `nodes-${name}.png`), fullPage: true });
      await visit('租约', '租约', 'leases');
      await page.getByText('Active', { exact: true }).first().waitFor();
      await page.screenshot({ path: path.join(artifacts, `leases-${name}.png`), fullPage: true });
      await visit('目录发布', '目录发布', 'directory');
      await page.getByText('当前 API 未接入', { exact: true }).waitFor();
      assert.equal(await page.getByRole('button', { name: '发布' }).count(), 0, 'unsupported directory must not expose publish');
      await page.screenshot({ path: path.join(artifacts, `directory-${name}.png`), fullPage: true });
      await visit('审计', '审计', 'audit');
      await page.getByText('source-revision-fixture-001', { exact: true }).waitFor();
      assert.equal(await page.getByText('should-not-render', { exact: false }).count(), 0, 'audit must redact secrets');
      await page.screenshot({ path: path.join(artifacts, `audit-${name}.png`), fullPage: true });

      const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1);
      assert.equal(overflow, false, `${name}: root horizontal overflow`);
      assert.deepEqual(errors, [], `${name}: browser errors`);
      await page.close();
      console.log(`${name}: commercial overview, customers, subscriptions, devices, nodes, leases, directory, audit and reauth passed`);
    }
  } finally {
    await browser.close();
    await new Promise((resolve) => server.close(resolve));
  }
}

fs.mkdirSync(artifacts, { recursive: true });
main().catch((error) => { console.error(error); process.exitCode = 1; });
