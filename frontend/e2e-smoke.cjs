const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const base = process.env.PANEL_TEST_URL || 'http://127.0.0.1:9182';
const credentialsPath =
  process.env.PANEL_TEST_CREDENTIALS || path.resolve(__dirname, '../data/initial-login.json');
const credentials = JSON.parse(fs.readFileSync(credentialsPath, 'utf8'));
const artifacts = path.resolve(__dirname, '../artifacts');

(async () => {
  fs.mkdirSync(artifacts, { recursive: true });
  const browser = await chromium.launch({ headless: true });
  try {
    for (const [name, width, height] of [
      ['desktop', 1440, 960],
      ['mobile', 390, 844],
    ]) {
      const page = await browser.newPage({ viewport: { width, height } });
      const errors = [];
      page.on('pageerror', (error) => errors.push(error.message));
      const networkFixture = process.env.PANEL_TEST_NETWORK_FIXTURE === '1';
      if (networkFixture) {
        const host = { id: 'windows-fixture', name: 'Windows 长名称网络设备测试 '.repeat(8), address: '192.0.2.20', port: 22, username: 'tester', credential_id: '', jump_id: '', host_key: '', group: '', favorite: false, terminal_enabled: true };
        await page.route('**/api/hosts', (route) => route.fulfill({ json: { hosts: [host] } }));
        await page.route('**/api/network/probe', (route) => route.fulfill({ json: { results: [{ ...host, os: 'Windows', ssh: true, dns: true, internet: true, helper: true, system_internet: false, diagnosis: 'system_proxy_failed', default_route: 'VeryLongNetworkAdapterName'.repeat(12) + ': 192.0.2.1' }] } }));
      } else {
        await page.route('**/api/network/probe', (route) => route.fulfill({ json: { results: [] } }));
      }
      await page.goto(base, { waitUntil: 'networkidle' });
      await page.getByLabel('密码').fill(credentials.password);
      await page.getByRole('button', { name: '登录', exact: true }).click();
      await page.locator('.app-shell').waitFor();
      const navigation = page.locator(name === 'mobile' ? '.mobile-nav' : '.sidebar');
      await navigation.locator('button').filter({ hasText: '网络借助' }).click();
      await page.getByRole('heading', { name: '网络借助', exact: true }).waitFor();
      if (networkFixture) {
        await page.getByRole('button', { name: '探测全部主机' }).click();
        await page.getByText('直连正常，但系统代理请求失败。', { exact: false }).waitFor();
        const clipped = await page.locator('.probe-card .long-value').evaluateAll((elements) =>
          elements.some((element) => element.scrollWidth > element.clientWidth + 1 || element.scrollHeight > element.clientHeight + 1));
        if (clipped) throw new Error(`${name}: network diagnostic text is clipped`);
        await page.screenshot({ path: path.join(artifacts, `network-${name}.png`), fullPage: true });
      }
      await navigation.locator('button').filter({ hasText: '终端' }).click();
      await page.locator('.terminal-view .xterm').waitFor();
      await navigation.locator('button').filter({ hasText: 'Codex 对话' }).click();
      await page.getByRole('heading', { name: '服务器 Codex 对话', exact: true }).waitFor();
      const chatOverflow = await page.locator('.codex-shell').evaluate((element) =>
        element.scrollWidth > element.clientWidth + 1);
      if (chatOverflow) throw new Error(`${name}: Codex layout has hidden horizontal overflow`);
      await page.screenshot({ path: path.join(artifacts, `codex-${name}.png`), fullPage: true });
      await navigation.locator('button').filter({ hasText: '安全' }).click();
      await page.getByRole('heading', { name: '安全设置', exact: true }).waitFor();
      const layout = await page.locator('.sidebar').evaluate((element) => {
        const style = getComputedStyle(element);
        return { display: style.display, position: style.position, width: style.width };
      });
      if (
        name === 'desktop' &&
        (layout.display === 'none' || layout.position !== 'fixed' || layout.width !== '248px')
      ) {
        throw new Error(`desktop: unexpected sidebar layout ${JSON.stringify(layout)}`);
      }
      if (name === 'mobile' && layout.display !== 'none') {
        throw new Error(`mobile: sidebar should be hidden ${JSON.stringify(layout)}`);
      }
      const overflow = await page.evaluate(
        () => document.documentElement.scrollWidth > innerWidth + 1,
      );
      if (overflow) throw new Error(`${name}: document has horizontal overflow`);
      if (errors.length) throw new Error(errors.join('\n'));
      await page.screenshot({ path: path.join(artifacts, `${name}.png`), fullPage: true });
      await page.close();
      console.log(`${name}: login, navigation, network, terminal, security and layout passed`);
    }
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
