(async ()=>{
  const { chromium } = require('playwright');
  const browser = await chromium.launch({executablePath:'/opt/google/chrome/chrome', args:['--no-sandbox','--disable-setuid-sandbox']});
  const page = await browser.newPage();

  page.on('console', msg => {
    console.log('PAGE LOG:', msg.type(), msg.text());
  });
  page.on('pageerror', err => {
    console.log('PAGE ERROR:', err.toString());
  });
  page.on('requestfailed', req => {
    console.log('REQUEST FAILED:', req.url(), req.failure() && req.failure().errorText);
  });
  page.on('response', res => {
    if(res.status() >= 400) console.log('RESPONSE:', res.status(), res.url());
  });

  console.log('goto');
  await page.goto('http://127.0.0.1:7860', { waitUntil: 'domcontentloaded', timeout: 30000 });
  await page.waitForSelector('#status', { timeout: 10000 });

  // Check computed styles
  const bg = await page.evaluate(()=>getComputedStyle(document.body).backgroundColor);
  const panelBg = await page.evaluate(()=>getComputedStyle(document.querySelector('#chat')).backgroundColor);
  console.log('COMPUTED body background:', bg, 'chat background:', panelBg);

  console.log('start stream');
  await page.click('#startStream');
  await page.waitForTimeout(1000);
  console.log('refresh metrics');
  await page.click('#refreshMetrics');
  await page.waitForSelector('#metrics div', { timeout: 8000 }).catch(()=>{});
  console.log('send chat');
  await page.fill('#msg','playwright test message');
  await page.click('#send');
  await page.waitForTimeout(1500);
  await page.click('#darkToggle');
  await page.waitForTimeout(500);

  // check if dark class applied
  const hasDark = await page.evaluate(()=>document.body.classList.contains('dark'));
  console.log('DARK CLASS APPLIED:', hasDark);

  await page.screenshot({ path: '/tmp/forge_ui_playwright.png', fullPage: true });
  console.log('screenshot saved /tmp/forge_ui_playwright.png');
  await browser.close();
})().catch(e=>{ console.error('ERROR',e); process.exit(1) });