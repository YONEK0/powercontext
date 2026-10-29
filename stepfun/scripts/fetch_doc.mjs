import { chromium } from 'playwright-core';
const EXE = '/Users/a/Documents/deepseek-harness/default-workspace/.tools/Google Chrome.app/Contents/MacOS/Google Chrome';
const browser = await chromium.launch({ executablePath: EXE, headless: true });
const page = await browser.newPage();
await page.goto('https://oceanbase.yuque.com/org-wiki-obtech-vh7w9r/hc3pio/brxxufkegusa86hl', { waitUntil: 'networkidle', timeout: 60000 }).catch(() => {});
await page.waitForTimeout(5000);
const text = await page.evaluate(() => document.body.innerText);
console.log(text);
await browser.close();
