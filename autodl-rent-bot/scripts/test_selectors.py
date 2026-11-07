"""只读扫描测试：验证新选择器能否在真实市场页正常工作。

只做：设置计费/数量/型号、列出当前地区卡片、跑一遍 iterate_regions。
绝不点击“可租/创建”，不产生任何费用与风控动作。
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
)


def main():
    profile_dir = ensure_profile_copy()
    print("profile 副本:", profile_dir)
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=profile_dir,
            channel="chrome",
            headless=config.HEADLESS,
            slow_mo=120,
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
        print("当前 URL:", page.url, "| 标题:", page.title())

        set_billing_mode(page)
        set_gpu_count(page)
        select_gpu_model(page)
        page.wait_for_timeout(2500)

        SHOT_DIR.mkdir(exist_ok=True)
        page.screenshot(path=str(SHOT_DIR / "scan_after_filter.png"), full_page=True)

        cards = page.locator(".list-item").all()
        print(f"\n当前地区 .list-item 卡片数: {len(cards)}")
        for c in cards[:12]:
            try:
                name = c.locator(".machine-info .gpu-name .name").first.inner_text(timeout=1500)
                free = c.locator(".gpu-num .num-bold").first.inner_text(timeout=1500)
                rent = c.locator("button.el-button--primary:has-text('可租')").count()
                print(f"  {name:28s} 空闲={free:>3s} 可租按钮数={rent}")
            except Exception as e:  # noqa
                print("  (解析失败)", repr(e))

        print("\n开始 iterate_regions（逐地区扫描，只读）...")
        btn = iterate_regions(page)
        print("iterate_regions 结果:", "✅ 找到可租机器" if btn else "❌ 所有地区都无空闲目标机器")

        context.close()
        print("测试结束（未点击任何可租/创建）")


if __name__ == "__main__":
    main()
