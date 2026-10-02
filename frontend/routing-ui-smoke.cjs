const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');

(async () => {
  const browser = await chromium.launch({ headless: true });
  try {
    for (const width of [1440, 390]) {
      const page = await browser.newPage({ viewport: { width, height: 950 } });
      const errors = [];
      page.on('pageerror', (e) => errors.push(e.message));
      const host = { id: 'd408', name: '校园客户端长名称测试 '.repeat(12), address: '10.20.32.14', port: 22, username: 'd408', terminal_enabled: true };
      const now = Math.floor(Date.now() / 1000);
      const profile = { id: 'p', name: '借网方案长名称测试 '.repeat(8), gateway_id: 'source', client_ids: ['d408'], port: 51919, endpoint: '10.20.32.13', tunnel_cidr: '10.213.40.0/24', preserve_routes: [], maintenance: true, state: 'enabled', updated_at: now, interface: 'na1234567890', last_error: '', runtime: { status: 'mixed', mismatch: true, nodes: [{ host_id: 'd408', status: 'disabled', checked_at: now, external_tunnels: ['wg-fleet'], last_error: '' }] } };
      let explicitProbe = false;
      await page.route('**/api/**', async (route) => {
        const name = new URL(route.request().url()).pathname.split('/api/')[1];
        let json = {};
        if (name === 'session') json = { authenticated: true, csrf: 'fixture', version: 'routing-test' };
        if (name === 'hosts') json = { hosts: [host] };
        if (name === 'credentials') json = { credentials: [] };
        if (name === 'network') json = { profiles: [profile] };
        if (name === 'security') json = { devices: [], passkeys: [], verified: true };
        if (name === 'audit') json = { events: [] };
        if (name === 'network/probe') {
          explicitProbe ||= route.request().postDataJSON().return_target === '10.80.62.217';
          json = { profiles: [profile], results: [{ ...host, checked_at: now, ssh: true, helper: true, os: 'Linux', public_route: '1.1.1.1 dev wg-fleet', return_route: '10.80.62.217 via 10.20.32.1 dev enp4s0', route_warnings: ['路由冲突说明长文本 '.repeat(35)], assist: [{ profile_id: 'p', interface: 'na1234567890', active: false, desired: false }] }] };
        }
        await route.fulfill({ json });
      });
      await page.goto('https://whm12.art/network-assist/', { waitUntil: 'networkidle' });
      const nav = page.locator(width < 720 ? '.mobile-nav' : '.sidebar');
      await nav.locator('a').filter({ hasText: '网络借助' }).click();
      await page.getByLabel('原网络回程测试地址（可选 IPv4）').fill('10.80.62.217');
      await page.getByRole('button', { name: '探测全部主机' }).click();
      await page.getByText('公网实际路径：', { exact: false }).waitFor();
      await page.locator('.profile-item').click();
      await page.getByText('方案记录与客户端真实状态不同。', { exact: false }).waitFor();
      if (!explicitProbe) throw new Error('Return target was not submitted');
      const clipped = await page.locator('.probe-card .long-value, .editor-card .long-value').evaluateAll((elements) => elements.some((e) => e.scrollWidth > e.clientWidth + 1 || e.scrollHeight > e.clientHeight + 1));
      if (clipped || await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1)) throw new Error('Long routing text is clipped');
      if (errors.length) throw new Error(errors.join('\n'));
      const folder = path.resolve(__dirname, '../artifacts/routing-upgrade');
      fs.mkdirSync(folder, { recursive: true });
      await page.screenshot({ path: path.join(folder, `routing-${width}.png`), fullPage: true });
      await page.close();
      console.log(`routing UI ${width}: runtime mismatch, return probe and long text passed (fixture API)`);
    }
  } finally { await browser.close(); }
})().catch((e) => { console.error(e); process.exitCode = 1; });
