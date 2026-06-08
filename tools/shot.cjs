#!/usr/bin/env node
/**
 * shot.cjs — headless screenshot helper for the 3D visualizer.
 *
 * Renders app/web/visualizer3d.html in a headless Chromium with software WebGL
 * (SwiftShader) and writes a PNG, so the 3D view can be eyeballed without a GPU
 * or a physical display. This is a LOCAL DEV TOOL — it is not part of the app
 * and is not shipped to the rig.
 *
 * Requirements (already present in the cloud dev container):
 *   - Playwright installed (globally is fine; resolved via `npm root -g`).
 *   - A Chromium build (env CHROMIUM_PATH, else /opt/pw-browsers/chromium, else
 *     whatever Playwright finds).
 *
 * Usage:
 *   node tools/shot.cjs                         # demo mode -> tools/_shots/viz.png
 *   node tools/shot.cjs out.png --wait=6000     # custom output + settle time
 *   node tools/shot.cjs --page=dashboard.html   # render the dashboard instead
 *   node tools/shot.cjs --eval="window.__lb.setUplights(6)"   # drive the dev hook
 *   node tools/shot.cjs --size=1600x900 --no-demo
 *
 * Flags:
 *   --page=FILE     file under app/web to load        (default visualizer3d.html)
 *   --wait=MS       settle time before the shot        (default 4500)
 *   --size=WxH      viewport                           (default 1280x800)
 *   --eval="JS"     run JS in the page before the shot (repeatable)
 *   --no-demo       don't auto-click the Demo button
 *   --query=STR     extra URL query (e.g. embed=1)
 */
const http = require('http');
const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

// --- resolve Playwright (repo-local first, then global) ---
function loadPlaywright() {
  try { return require('playwright'); } catch (_) {}
  try {
    const groot = execSync('npm root -g').toString().trim();
    return require(path.join(groot, 'playwright'));
  } catch (e) {
    console.error('Could not load Playwright. Install with: npm i -g playwright');
    process.exit(1);
  }
}

// --- args ---
const argv = process.argv.slice(2);
const flags = {};
const positional = [];
for (const a of argv) {
  const m = a.match(/^--([^=]+)(?:=(.*))?$/);
  if (m) (flags[m[1]] ??= []).push(m[2] ?? true);
  else positional.push(a);
}
const first = k => (flags[k] ? flags[k][0] : undefined);

const WEB_DIR  = path.resolve(__dirname, '..', 'app', 'web');
const PAGE     = first('page') || 'visualizer3d.html';
const OUT      = positional[0] || path.join(__dirname, '_shots', 'viz.png');
const WAIT     = parseInt(first('wait') || '4500', 10);
const [VW, VH] = (first('size') || '1280x800').split('x').map(n => parseInt(n, 10));
const DEMO     = !flags['no-demo'];
const EVALS    = flags['eval'] || [];
const QUERY    = first('query');

const MIME = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css',
               '.json': 'application/json', '.png': 'image/png', '.ico': 'image/x-icon' };

function startServer() {
  return new Promise(resolve => {
    const srv = http.createServer((req, res) => {
      let rel = decodeURIComponent(req.url.split('?')[0]).replace(/^\/+/, '') || PAGE;
      if (rel === 'visualizer3d') rel = 'visualizer3d.html';   // mirror the server route
      const fp = path.join(WEB_DIR, rel);
      if (!fp.startsWith(WEB_DIR) || !fs.existsSync(fp) || fs.statSync(fp).isDirectory()) {
        res.statusCode = 404; return res.end('not found');
      }
      res.setHeader('Content-Type', MIME[path.extname(fp)] || 'application/octet-stream');
      fs.createReadStream(fp).pipe(res);
    });
    srv.listen(0, '127.0.0.1', () => resolve(srv));
  });
}

(async () => {
  const { chromium } = loadPlaywright();
  const srv = await startServer();
  const port = srv.address().port;
  const q = ['dev=1', QUERY].filter(Boolean).join('&');
  const url = `http://127.0.0.1:${port}/${PAGE}?${q}`;

  const exe = process.env.CHROMIUM_PATH ||
    (fs.existsSync('/opt/pw-browsers/chromium') ? '/opt/pw-browsers/chromium' : undefined);

  const browser = await chromium.launch({
    executablePath: exe, headless: true,
    args: ['--no-sandbox', '--use-gl=angle', '--use-angle=swiftshader',
           '--enable-unsafe-swiftshader', '--ignore-gpu-blocklist',
           '--enable-webgl', '--ignore-certificate-errors'],
  });
  const ctx = await browser.newContext({ viewport: { width: VW, height: VH }, ignoreHTTPSErrors: true });
  const page = await ctx.newPage();
  const errs = [];
  page.on('pageerror', e => errs.push('PAGEERR: ' + e.message));

  await page.goto(url, { waitUntil: 'load', timeout: 30000 });
  await page.waitForTimeout(700);
  if (DEMO) { try { await page.click('#demoBtn', { timeout: 1500 }); } catch (_) {} }
  await page.waitForTimeout(Math.max(0, WAIT - 700));
  for (const js of EVALS) { try { await page.evaluate(js); } catch (e) { errs.push('EVAL: ' + e.message); } }
  if (EVALS.length) await page.waitForTimeout(3200);

  fs.mkdirSync(path.dirname(OUT), { recursive: true });
  await page.screenshot({ path: OUT });
  await browser.close();
  srv.close();
  console.log(`wrote ${OUT}  (${VW}x${VH}, ${PAGE})`);
  if (errs.length) console.log('page errors:', errs.slice(0, 6).join(' | '));
})().catch(e => { console.error('FAIL', e.message); process.exit(1); });
