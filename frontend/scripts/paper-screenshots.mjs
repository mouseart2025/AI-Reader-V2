/**
 * Automated paper screenshots using Playwright.
 * Captures 4 key visualizations for EMNLP demo paper.
 *
 * Usage: cd frontend && npx playwright test scripts/paper-screenshots.mjs
 *   or:  cd frontend && node scripts/paper-screenshots.mjs
 */

import { chromium } from "@playwright/test";

const BASE = "http://localhost:5173";
const XIYOU_ID = "3b2ef56c-1a55-466a-a7d1-34272446a198";
const HONGLOUMENG_ID = "c384901a-8b71-437a-af35-b5ec1c56c696";
const OUT = "/Users/leonfeng/Baiduyun/AISoul/AI-Reader-V2/scripts/paper-shots-v2";

async function main() {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({
    viewport: { width: 1920, height: 1080 },
    deviceScaleFactor: 2, // retina for crisp paper figures
  });
  const page = await context.newPage();

  // Wait helper
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));

  console.log("📸 1/4: Relationship Graph (西游记)...");
  await page.goto(`${BASE}/novels/${XIYOU_ID}/graph`);
  await wait(5000); // let graph settle
  await page.screenshot({
    path: `${OUT}/fig-graph.png`,
    fullPage: false,
  });
  console.log("  ✅ saved fig-graph.png");

  console.log("📸 2/4: World Map (西游记)...");
  await page.goto(`${BASE}/novels/${XIYOU_ID}/map`);
  await wait(5000); // let map render
  await page.screenshot({
    path: `${OUT}/fig-map.png`,
    fullPage: false,
  });
  console.log("  ✅ saved fig-map.png");

  console.log("📸 3/4: Timeline Storyline (西游记)...");
  await page.goto(`${BASE}/novels/${XIYOU_ID}/timeline`);
  await wait(4000);
  await page.screenshot({
    path: `${OUT}/fig-timeline.png`,
    fullPage: false,
  });
  console.log("  ✅ saved fig-timeline.png");

  console.log("📸 4/4: Encyclopedia (红楼梦)...");
  await page.goto(`${BASE}/novels/${HONGLOUMENG_ID}/encyclopedia`);
  await wait(3000);
  await page.screenshot({
    path: `${OUT}/fig-encyclopedia.png`,
    fullPage: false,
  });
  console.log("  ✅ saved fig-encyclopedia.png");

  await browser.close();
  console.log(`\n✅ All 4 figures saved to ${OUT}/`);
}

main().catch(console.error);
