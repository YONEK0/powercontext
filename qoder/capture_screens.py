from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "dist" / "assets"

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=1)
    page.goto("http://127.0.0.1:5000/#overview", wait_until="networkidle")
    page.screenshot(path=ASSETS / "overview.png", full_page=True)

    page.locator('.nav-item[data-view="search"]').click()
    page.locator("#search-input").wait_for(state="visible")
    page.locator("#search-input").fill("怎样对抗遗忘")
    page.locator('#search-form button[type="submit"]').click()
    page.locator("#search-summary").wait_for(state="visible", timeout=30000)
    page.screenshot(path=ASSETS / "search.png", full_page=True)

    page.locator('.nav-item[data-view="review"]').click()
    page.locator("#reveal-answer").wait_for(state="visible")
    page.locator("#reveal-answer").click()
    page.screenshot(path=ASSETS / "review.png", full_page=True)
    browser.close()

print("已生成今日概览、混合检索和复习空间的真实运行截图")
