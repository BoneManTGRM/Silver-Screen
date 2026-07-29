import { chromium } from "playwright";

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
const errors = [];
page.on("pageerror", (e) => errors.push(String(e)));
page.on("console", (m) => {
  if (m.type() === "error") errors.push(m.text());
});

await page.goto("http://127.0.0.1:8080/", { waitUntil: "networkidle" });
await page.waitForTimeout(800);

// If already in project, continue; else create
const makeBtn = page.getByRole("button", { name: /Create project only/i });
if (await makeBtn.count()) {
  await makeBtn.click();
  await page.waitForTimeout(1500);
}
await page.screenshot({ path: "/workspace/screenshots/silver-screen-project.png", fullPage: true });
const title = await page.locator("h1").first().textContent();
console.log("Active project title:", title);

// Stage buttons contain step number + label
async function clickStage(label) {
  await page.locator("nav[aria-label='Studio stages'] button", { hasText: label }).click();
  await page.waitForTimeout(600);
}

await clickStage("Board");
await page.screenshot({ path: "/workspace/screenshots/silver-screen-board.png", fullPage: true });
const frames = await page.locator("img").count();
console.log("images on board:", frames);

await clickStage("Render");
const renderBtn = page.getByRole("button", { name: /Render film/i });
console.log("render btn count", await renderBtn.count());
if (await renderBtn.count()) {
  await renderBtn.click();
  try {
    await page.waitForSelector("video", { timeout: 120000 });
    console.log("Video element appeared");
  } catch (e) {
    console.log("No video after timeout", e.message);
  }
}
await page.screenshot({ path: "/workspace/screenshots/silver-screen-render.png", fullPage: true });

await clickStage("NFT");
const seal = page.getByRole("button", { name: /Seal package/i });
if (await seal.count()) await seal.click();
await page.waitForTimeout(500);
await page.screenshot({ path: "/workspace/screenshots/silver-screen-nft.png", fullPage: true });

// Mobile
await page.setViewportSize({ width: 390, height: 844 });
const back = page.getByRole("button", { name: /All projects/i });
if (await back.count()) await back.click();
await page.waitForTimeout(600);
await page.screenshot({ path: "/workspace/screenshots/silver-screen-mobile.png", fullPage: true });
const overflow = await page.evaluate(() => ({
  scrollWidth: document.documentElement.scrollWidth,
  clientWidth: document.documentElement.clientWidth,
}));
console.log("mobile overflow:", overflow);
console.log("errors:", errors);
await browser.close();
