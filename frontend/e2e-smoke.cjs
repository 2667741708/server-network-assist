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
      await page.goto(base, { waitUntil: 'networkidle' });
      await page.getByLabel('密码').fill(credentials.password);
      await page.getByRole('button', { name: '登录', exact: true }).click();
      await page.locator('.app-shell').waitFor();
      const navigation = page.locator(name === 'mobile' ? '.mobile-nav' : '.sidebar');
      await navigation.locator('button').filter({ hasText: '网络借助' }).click();
      await page.getByRole('heading', { name: '网络借助', exact: true }).waitFor();
      await navigation.locator('button').filter({ hasText: '终端' }).click();
      await page.locator('.terminal-view .xterm').waitFor();
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
