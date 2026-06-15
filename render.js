// Render an HTML file to a PNG via headless Chromium.
// Usage: node render.js input.html output.png [width] [height]
//   width/height optional, default 1080x1350 (4:5 — IG feed/carousel).
//   For TikTok Photo Mode / 9:16 frames: node render.js in.html out.png 1080 1920
// Carousels: render N per-slide HTMLs to N PNGs (call this once per slide);
// the caller composes the carousel from the resulting PNG list.
const { chromium } = require('playwright');
const path = require('path');

(async () => {
  const [, , inputArg, outputArg, wArg, hArg] = process.argv;
  if (!inputArg || !outputArg) {
    console.error('Usage: node render.js input.html output.png [width] [height]');
    process.exit(1);
  }
  const input = path.resolve(inputArg);
  const output = path.resolve(outputArg);
  const width = wArg ? parseInt(wArg, 10) : 1080;
  const height = hArg ? parseInt(hArg, 10) : 1350;
  if (!Number.isFinite(width) || !Number.isFinite(height) || width <= 0 || height <= 0) {
    console.error(`Invalid dimensions: ${wArg} x ${hArg}`);
    process.exit(1);
  }
  // TikTok ceiling guard: 1080x1920 = 2.07M px is the documented max; warn if over.
  if (width * height > 1080 * 1920) {
    console.error(`Warning: ${width}x${height} = ${width * height}px exceeds the 1080x1920 (2.07M) TikTok ceiling.`);
  }

  const browser = await chromium.launch();
  const context = await browser.newContext({
    viewport: { width, height },
    deviceScaleFactor: 1, // native pixels — no upscale
  });
  const page = await context.newPage();
  await page.goto('file://' + input, { waitUntil: 'load' });
  // Wait for webfonts to actually settle (more robust than networkidle, which
  // can hang on a slow font CDN). document.fonts.ready resolves on load success
  // OR failure, so a CDN blip degrades gracefully instead of stalling.
  try { await page.evaluate(() => document.fonts.ready); } catch (e) { /* no fonts */ }
  await page.waitForTimeout(250);
  await page.screenshot({ path: output, omitBackground: false, fullPage: false, clip: { x: 0, y: 0, width, height } });
  await browser.close();
  console.log(`Rendered: ${output} (${width}x${height})`);
})();
