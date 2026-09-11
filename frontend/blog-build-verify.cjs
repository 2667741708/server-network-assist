const { chromium } = require('playwright');
const fs = require('node:fs/promises');
const path = require('node:path');
const { spawn, execFileSync } = require('node:child_process');
const http = require('node:http');
const assert = require('node:assert/strict');
const root = path.resolve(__dirname, '..');
const blog = path.join(root, 'site/blog');
const evidence = path.join(root, 'artifacts/blog-cms-check');
let browser, server;

async function build(fixture, mode) {
  const child = spawn(process.execPath, [path.join(blog, 'node_modules/astro/bin/astro.mjs'), 'build', '--mode', mode], {
    cwd: fixture, env: { ...process.env, ASTRO_TELEMETRY_DISABLED: '1' },
  });
  let log = '';
  child.stdout.on('data', data => { log += data; });
  child.stderr.on('data', data => { log += data; });
  const code = await new Promise((resolve, reject) => { child.on('error', reject); child.on('exit', resolve); });
  await fs.writeFile(path.join(evidence, `build-${mode}.log`), log);
  assert.equal(code, 0, log.slice(-10000));
}

async function main() {
  const cms = JSON.parse(await fs.readFile(path.join(evidence, 'result.json'), 'utf8'));
  assert.equal(cms.drafts.length, 5);
  const fixture = path.join(evidence, `theme-fixture-${Date.now()}`, 'site/blog');
  await fs.cp(blog, fixture, { recursive: true, filter: source => {
    const relative = path.relative(blog, source);
    return !relative.split(path.sep).some(part => ['node_modules', 'dist', '.astro', '.env', '.git'].includes(part));
  }});
  await fs.cp(path.join(root, 'site/projects'), path.join(fixture, '../projects'), { recursive: true });
  await fs.symlink(path.join(blog, 'node_modules'), path.join(fixture, 'node_modules'), process.platform === 'win32' ? 'junction' : 'dir');
  for (const draft of cms.drafts) {
    const target = path.join(fixture, 'src/content/posts', draft.collection, `${draft.slug}.md`);
    await fs.mkdir(path.dirname(target), { recursive: true });
    await fs.writeFile(target, draft.raw);
  }
  const uploadedImage = execFileSync('git', ['show', `${cms.drafts[0].branch}:site/blog/public/images/uploads/windows-proxy-diagnosis.png`], { cwd: cms.content, maxBuffer: 10 * 1024 * 1024 });
  await fs.mkdir(path.join(fixture, 'public/images/uploads'), { recursive: true });
  await fs.writeFile(path.join(fixture, 'public/images/uploads/windows-proxy-diagnosis.png'), uploadedImage);
  const dist = path.join(fixture, 'dist');
  await build(fixture, 'production');
  for (const draft of cms.drafts) {
    await assert.rejects(fs.access(path.join(dist, 'posts', draft.collection, draft.slug, 'index.html')));
  }
  for (const entry of ['index.html', 'posts/index.html', 'archives/index.html', 'project/server-network-assist/index.html', 'sitemap-0.xml', 'rss.xml']) {
    assert.doesNotMatch(await fs.readFile(path.join(dist, entry), 'utf8'), /cms-verification-|CMS verification draft/);
  }
  await build(fixture, 'preview');
  for (const draft of cms.drafts) {
    const html = await fs.readFile(path.join(dist, 'posts', draft.collection, draft.slug, 'index.html'), 'utf8');
    assert.match(html, /<h1/);
  }
  server = http.createServer(async (req, res) => {
    try {
      const rel = decodeURIComponent(new URL(req.url, 'http://localhost').pathname).replace(/^\/projects\/?/, '');
      let target = path.resolve(dist, rel);
      if (!target.startsWith(dist + path.sep) && target !== dist) throw new Error('Invalid path');
      if (!path.extname(target)) target = path.join(target, 'index.html');
      res.setHeader('Content-Type', { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.png': 'image/png', '.svg': 'image/svg+xml' }[path.extname(target)] || 'application/octet-stream');
      res.end(await fs.readFile(target));
    } catch { res.statusCode = 404; res.end('Not found'); }
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  const errors = []; page.on('pageerror', error => errors.push(error.message));
  for (const width of [1440, 390]) {
    await page.setViewportSize({ width, height: 1000 });
    await page.goto(`http://127.0.0.1:${server.address().port}/projects/posts/tutorials/cms-verification-draft/`, { waitUntil: 'networkidle' });
    assert.equal(await page.locator('h1').innerText(), 'CMS verification draft');
    assert.match(await page.locator('article').innerText(), /CMS真实浏览器草稿验证/);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false);
    await page.screenshot({ path: path.join(evidence, `draft-preview-${width}.png`), fullPage: true });
  }
  assert.equal(errors.length, 0, errors.join('\n'));
  await fs.writeFile(path.join(evidence, 'build-result.json'), JSON.stringify({ fixture, productionExcludesFiveDrafts: true, previewIncludesFiveDrafts: true, previewViewports: [1440, 390], uploadedImageBytes: uploadedImage.length }, null, 2));
  console.log('PASS: five actual CMS drafts excluded from production routes/lists/RSS/sitemap, included in AstroPaper preview; desktop/mobile preview verified.');
}
main().catch(error => { console.error(error); process.exitCode = 1; }).finally(async () => { await browser?.close(); server?.close(); });
