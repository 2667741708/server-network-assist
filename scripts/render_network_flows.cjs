// Build static diagrams with Mermaid 11.12.0's existing neutral theme.
// npm install --prefix artifacts/diagram-tools --no-audit --no-fund mermaid@11.12.0
// Requires the repository's frontend Playwright development dependency.
const fs = require('node:fs');
const path = require('node:path');
const http = require('node:http');
const { chromium } = require('../frontend/node_modules/playwright');
const root = path.resolve(__dirname, '..');
const modules = path.join(root, 'artifacts/diagram-tools/node_modules/mermaid/dist');
const server = http.createServer((req, res) => {
  if (req.url === '/') { res.setHeader('Content-Type', 'text/html'); return res.end('<!doctype html><html><body></body></html>'); }
  const file = path.resolve(modules, '.' + decodeURIComponent(req.url.split('?')[0]));
  if (!file.startsWith(modules + path.sep) || !fs.existsSync(file)) { res.writeHead(404); return res.end(); }
  res.setHeader('Content-Type', 'text/javascript');
  fs.createReadStream(file).pipe(res);
});
(async () => {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  let browser;
  try {
    browser = await chromium.launch({headless:true});
    const page = await browser.newPage();
    await page.goto(`http://127.0.0.1:${server.address().port}/`);
    for (const name of ['borrow-network-flow', 'restore-network-flow']) {
      const source = fs.readFileSync(path.join(root, 'docs/diagrams', name + '.mmd'), 'utf8');
      const svg = await page.evaluate(async ({source, name}) => {
        const {default:mermaid} = await import('/mermaid.esm.min.mjs');
        mermaid.initialize({startOnLoad:false, theme:'neutral', htmlLabels:false, fontFamily:'Microsoft YaHei, sans-serif',
          flowchart:{htmlLabels:false, useMaxWidth:true, nodeSpacing:24, rankSpacing:34}});
        const output = (await mermaid.render(name, source)).svg;
        const doc = new DOMParser().parseFromString(output, 'image/svg+xml');
        if (doc.querySelector('parsererror')) throw new Error('Invalid diagram SVG');
        doc.documentElement.style.background = 'white';
        return new XMLSerializer().serializeToString(doc);
      }, {source, name});
      fs.writeFileSync(path.join(root, 'site/blog/public/images', name + '.svg'), svg);
      console.log('Rendered ' + name);
    }
  } finally {
    if (browser) await browser.close();
    server.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
