"""环境验证脚本：不访问 AutoDL，仅验证 Playwright 浏览器能否启动。"""
from playwright.sync_api import sync_playwright

import config


def main():
    print("验证配置读取...")
    config.validate()
    print(f"用户名: {config.USERNAME}")
    print(f"目标 GPU: {config.GPU_MODEL} x {config.GPU_COUNT}")
    print(f"headless: {config.HEADLESS}")
    print(f"dry_run: {config.DRY_RUN}")

    print("验证 Playwright 浏览器启动...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(viewport={"width": 1920, "height": 1080})
        page = context.new_page()
        page.goto("about:blank")
        title = page.title()
        print(f"浏览器已启动，页面标题: {title!r}")
        page.wait_for_timeout(2000)
        browser.close()

    print("环境验证通过")


if __name__ == "__main__":
    main()
