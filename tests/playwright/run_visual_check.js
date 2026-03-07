const playwright = require('playwright');
(async ()=>{
  const url = 'http://127.0.0.1:7860';
  const out = '/tmp/forge_playwright_pixel7_check.png';
  const browser = await playwright.chromium.launch({ headless: true, args: ['--no-sandbox','--disable-gpu'] });
  const context = await browser.newContext({ viewport: { width: 393, height: 852 }, userAgent: 'Mozilla/5.0 (Linux; Android 12; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/109.0.0.0 Mobile Safari/537.36' });
  const page = await context.newPage();
  try{
    const resp = await page.goto(url, { waitUntil: 'networkidle', timeout: 15000 });
    console.log('HTTP status:', resp && resp.status());
    // check no horizontal scroll
    const bodyScroll = await page.evaluate(() => ({ scrollWidth: document.documentElement.scrollWidth, clientWidth: document.documentElement.clientWidth }));
    console.log('scrollWidth, clientWidth', bodyScroll.scrollWidth, bodyScroll.clientWidth);
    if(bodyScroll.scrollWidth > bodyScroll.clientWidth + 2) throw new Error('Horizontal scroll detected: scrollWidth='+bodyScroll.scrollWidth+' clientWidth='+bodyScroll.clientWidth);
    // ensure key elements exist and are visible
    const ids = ['#chat', '#metrics', '#sseStatus'];
    for(const id of ids){ const el = await page.$(id); if(!el) throw new Error('Missing element: '+id); const vis = await el.isVisible(); if(!vis) throw new Error('Element not visible: '+id); }
    const heading = await page.$('text=LLM Metrics (recent)'); if(!heading) throw new Error('Missing heading: LLM Metrics (recent)');
    await page.screenshot({ path: out, fullPage: true });
    console.log('Screenshot saved to', out);
    // test agentSelect listener existence by programmatically dispatching a change
    const hasSelect = await page.$('#agentSelect');
    if(hasSelect){
      // attach a listener to spy if change events fire
      await page.evaluate(()=>{
        window.__agentSelectChangeTriggered = false;
        const sel = document.getElementById('agentSelect');
        if(sel){ sel.addEventListener('change', ()=>{ window.__agentSelectChangeTriggered = true }); }
      });
      // trigger change
      await page.selectOption('#agentSelect', 'all').catch(()=>{});
      // some environments don't allow selectOption for non-interactive selects; dispatch event
      await page.evaluate(()=>{ const sel = document.getElementById('agentSelect'); if(sel){ const ev = new Event('change', { bubbles: true }); sel.dispatchEvent(ev) } });
      const triggered = await page.evaluate(()=> !!window.__agentSelectChangeTriggered );
      console.log('agentSelect change handler triggered?', triggered);
    }
    console.log('All checks passed');
  }catch(err){ console.error('Check failed:', err && err.message); await page.screenshot({ path: '/tmp/forge_playwright_pixel7_check_fail.png', fullPage:true }); console.log('Saved failure screenshot to /tmp/forge_playwright_pixel7_check_fail.png'); process.exitCode = 2; }
  finally{ await browser.close(); }
})();
