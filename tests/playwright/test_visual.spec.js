const { test, expect } = require('@playwright/test');

test.describe('FORGE visual checks', () => {
  test('Pixel 7 layout has no horizontal scroll and shows key elements', async ({ page }) => {
    await page.goto('http://127.0.0.1:7860');
    await page.setViewportSize({ width: 393, height: 852 });
    // ensure no horizontal scroll
    const bodyScroll = await page.evaluate(() => ({ w: document.documentElement.scrollWidth, vw: document.documentElement.clientWidth }));
    expect(bodyScroll.w).toBeLessThanOrEqual(bodyScroll.vw + 2);
    // key elements
    await expect(page.locator('#chat')).toBeVisible();
    await expect(page.locator('#metrics')).toBeVisible();
    await expect(page.locator('#sseStatus')).toBeVisible();
    await expect(page.locator('text=LLM Metrics (recent)')).toBeVisible();
    // take screenshot
    await page.screenshot({ path: '/tmp/forge_playwright_pixel7.png', fullPage: true });
  });
});
