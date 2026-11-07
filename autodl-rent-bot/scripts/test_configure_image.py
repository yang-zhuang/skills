"""安全验证 configure_image：打开创建页、配置镜像，然后关闭。

- 只点一次“可租”进入创建页，调用 configure_image 选 PyTorch + 每级第一个。
- 读取级联输入框最终值、截图，确认“创建并开机”按钮存在。
- 绝不点击“创建并开机”，不产生任何费用。
"""
from playwright.sync_api import sync_playwright

import config
from profile_util import ensure_profile_copy
from rent_bot import (
    STEALTH_SCRIPT,
    REAL_UA,
    SHOT_DIR,
    goto_market,
    set_billing_mode,
    set_gpu_count,
    select_gpu_model,
    iterate_regions,
    configure_image,
)


def main():
    profile_dir = ensure_profile_copy()
    print("profile 副本:", profile_dir)
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=profile_dir,
            channel="chrome",
            headless=config.HEADLESS,
            slow_mo=150,
            timeout=20000,
            viewport={"width": 1920, "height": 1080},
            locale="zh-CN",
            user_agent=REAL_UA,
            args=[
                "--window-size=1920,1080",
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--no-sandbox",
            ],
            ignore_default_args=["--enable-automation"],
        )
        context.add_init_script(STEALTH_SCRIPT)
        page = context.new_page()

        goto_market(page)

        # 机器是秒级动态的，反复扫描直到抓到一台（最多若干轮）
        rent_btn = None
        for attempt in range(1, 9):
            set_billing_mode(page)
            set_gpu_count(page)
            select_gpu_model(page)
            print(f"\n=== 第 {attempt} 轮扫描 ===")
            rent_btn = iterate_regions(page)
            if rent_btn:
                break
            print(f"本轮无空闲机器，{config.RETRY_INTERVAL_SECONDS}s 后重扫...")
            page.wait_for_timeout(config.RETRY_INTERVAL_SECONDS * 1000)
            goto_market(page)

        if not rent_btn:
            print("多轮仍无空闲目标机器，无法进入创建页，结束")
            context.close()
            return

        print("点击“可租”进入创建配置页...")
        rent_btn.click(timeout=10000)
        page.wait_for_timeout(5000)
        print("创建页 URL:", page.url)

        # 只配置镜像，不创建
        configure_image(page)
        page.wait_for_timeout(1500)

        SHOT_DIR.mkdir(exist_ok=True)
        page.screenshot(path=str(SHOT_DIR / "after_configure_image.png"), full_page=True)

        try:
            val = page.locator(".el-cascader .el-input__inner").first.input_value(timeout=3000)
            print("级联输入框最终值:", repr(val))
        except Exception as e:  # noqa
            print("读取级联值失败:", repr(e))

        cnt = page.locator("button.el-button--primary:has-text('创建并开机')").count()
        print(f"“创建并开机”按钮数量: {cnt} （存在但不点击）")

        print("验证结束：未点击“创建并开机”。关闭浏览器。")
        context.close()


if __name__ == "__main__":
    main()
