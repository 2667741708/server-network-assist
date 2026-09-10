const { chromium } = require('playwright');
const http = require('http');
const fs = require('fs');
const path = require('path');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '../docs');
fs.mkdirSync(path.resolve(__dirname, '../artifacts'), { recursive: true });
const mime = {
  '.html': 'text/html; charset=utf-8',
  '.css': 'text/css',
  '.js': 'text/javascript',
  '.png': 'image/png',
};
const server = http.createServer((request, response) => {
  let pathname = decodeURIComponent(new URL(request.url, 'http://localhost').pathname);
  if (pathname.endsWith('/')) pathname += 'index.html';
  const file = path.resolve(root, '.' + pathname);
  if (!file.startsWith(root + path.sep) || !fs.existsSync(file) || !fs.statSync(file).isFile()) {
    response.writeHead(404);
    response.end();
    return;
  }
  response.writeHead(200, {
    'Content-Type': mime[path.extname(file)] || 'application/octet-stream',
  });
  fs.createReadStream(file).pipe(response);
});
(async () => {
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  let browser;
  try {
    browser = await chromium.launch({ headless: true });
    const page = await browser.newPage({ viewport: { width: 1440, height: 1100 } });
    const errors = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await page.goto(`http://127.0.0.1:${server.address().port}/blog/`);
    const localLinks = await page
      .locator('a[href],img[src],link[href],script[src]')
      .evaluateAll((nodes) =>
        nodes.map((n) => n.href || n.src).filter((x) => x.startsWith(location.origin)),
      );
    for (const url of new Set(localLinks))
      assert.equal((await page.request.get(url)).status(), 200, url);
    await page.screenshot({ path: path.resolve(__dirname, '../artifacts/blog-desktop.png') });
    await page.getByLabel('出口系统', { exact: true }).selectOption('Ubuntu');
    await page.getByLabel('客户端系统', { exact: true }).selectOption('Windows');
    await page.getByLabel('同时共享源机器的 HTTP/HTTPS 代理', { exact: true }).check();
    assert.match(await page.locator('#route-exit').textContent(), /源 HTTP 代理/);
    assert.match(await page.locator('#route-client').textContent(), /Windows/);
    await page.locator('#theme').click();
    assert.equal(await page.locator('html').getAttribute('data-theme'), 'dark');
    await page.locator('#theme').click();
    await page.locator('.zoom').first().click();
    assert.equal(await page.locator('#image-viewer').isVisible(), true);
    await page.keyboard.press('Escape');
    assert.equal(await page.locator('#image-viewer').isVisible(), false);
    await page.getByRole('button', { name: '复制命令', exact: true }).click();
    await page
      .locator('.copy-status')
      .filter({ hasText: /已复制|已选中/ })
      .waitFor();
    assert.match(await page.locator('.copy-status').textContent(), /已复制|已选中/);
    for (const width of [390, 320]) {
      await page.setViewportSize({ width, height: 844 });
      await page.evaluate(() => window.scrollTo(0, 0));
      assert.equal(
        await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth),
        true,
        `horizontal overflow at ${width}`,
      );
      await page.screenshot({
        path: path.resolve(__dirname, `../artifacts/blog-mobile-top-${width}.png`),
      });
      await page.screenshot({
        path: path.resolve(__dirname, `../artifacts/blog-mobile-${width}.png`),
        fullPage: true,
      });
    }
    assert.deepEqual(errors, []);
    console.log(
      'PASS blog links, 320/390/1440 layouts, path controls, theme, image dialog and copy fallback',
    );
  } finally {
    if (browser) await browser.close();
    await new Promise((resolve) => server.close(resolve));
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
