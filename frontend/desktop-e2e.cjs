const { chromium } = require('playwright');
const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');
const root = path.resolve(__dirname, '..');
const data = path.join(root, 'artifacts', `desktop-e2e-${Date.now()}`);
const python = process.env.PANEL_TEST_PYTHON || path.join(root, '.venv-clean', 'Scripts', 'python.exe');
const service = spawn(python, ['-m', 'server_network_assist.desktop', '--serve', '--data', data], {cwd: root, windowsHide: true, stdio: 'ignore'});
const pause = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
(async () => {
  let browser;
  try {
    const instance = path.join(data, 'desktop-instance.json');
    for (let i=0; i<100 && !fs.existsSync(instance); i++) await pause(100);
    const state = JSON.parse(fs.readFileSync(instance, 'utf8'));
    browser = await chromium.launch({headless: true});
    for (const [name,width,height] of [['desktop',1200,940],['mobile',390,844]]) {
      const page = await browser.newPage({viewport:{width,height}});
      const errors = []; page.on('pageerror', e => errors.push(e.message));
      let active = true;
      await page.route('**/api/status', route => route.fulfill({json:{hostname:'TITAN-WINDOWS',platform:'Windows',version:'0.2.0',timestamp:Math.floor(Date.now()/1000),elevated:true,direct:{ok:true,milliseconds:138},system:{ok:true,milliseconds:152},proxy:{enabled:false,server:'',pac:false},routes:[{adapter:'Ethernet 2',gateway:'192.0.2.1',metric:100}],tunnels:[{name:'fleet-titan',active,addresses:['192.0.2.20'],received:498345632,sent:32255662,handshake:Math.floor(Date.now()/1000)-26,endpoint:'192.0.2.10:51909',telemetry:true}]}}));
      await page.route('**/api/action', route => { const body=route.request().postDataJSON(); active=body.action==='connect'; return route.fulfill({json:{ok:true}}); });
      await page.goto(`http://127.0.0.1:${state.port}/#${state.token}`);
      await page.getByRole('heading',{name:'网络已就绪',exact:true}).waitFor();
      await page.getByRole('button',{name:'断开连接',exact:true}).click();
      await page.getByRole('button',{name:'取消',exact:true}).click();
      if (!active) throw new Error('Cancel must preserve connection');
      await page.getByRole('button',{name:'断开连接',exact:true}).click();
      await page.getByRole('button',{name:'确认',exact:true}).click();
      await page.getByRole('button',{name:'连接',exact:true}).waitFor();
      await page.getByRole('button',{name:'连接',exact:true}).click();
      await page.getByRole('button',{name:'断开连接',exact:true}).waitFor();
      if (await page.evaluate(() => document.documentElement.scrollWidth > innerWidth+1)) throw new Error('Horizontal overflow');
      if (errors.length) throw new Error(errors.join('\n'));
      await page.screenshot({path:path.join(root,'artifacts',`desktop-panel-${name}.png`),fullPage:true});
      await page.close();
      console.log(`${name}: local desktop panel, connection actions, cancel and layout passed`);
    }
  } finally { if (browser) await browser.close(); service.kill(); }
})().catch(e => {console.error(e);process.exitCode=1;});
