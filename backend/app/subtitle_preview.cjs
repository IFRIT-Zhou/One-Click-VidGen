// A single local screenshot using the same Chromium engine as Hyperframes.
const puppeteer = require('puppeteer-core');
const { pathToFileURL } = require('node:url');
async function main() {
  const [executablePath, htmlPath, output, width, height] = process.argv.slice(2);
  let browser;
  try {
    browser = await puppeteer.launch({ executablePath, headless: true, timeout: 15000,
      args: ['--disable-gpu', '--hide-scrollbars', '--no-first-run', '--no-default-browser-check'] });
    const page = await browser.newPage();
    await page.setViewport({ width: Number(width), height: Number(height), deviceScaleFactor: 1 });
    await page.goto(pathToFileURL(htmlPath).href, { waitUntil: 'load', timeout: 10000 });
    await page.evaluate(() => document.fonts.ready);
    await page.screenshot({ path: output });
  } finally { if (browser) await browser.close(); }
}
main().catch(error => { console.error(error.message); process.exitCode = 1; });
