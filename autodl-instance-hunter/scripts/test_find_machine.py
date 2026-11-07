"""只读扫描测试（多型号优先级版）：验证 find_machine 全流程。

只做：设置计费/数量 → 按优先级遍历型号与地区 → 返回最优机器。
绝不点击"可租/创建"，不产生任何费用与风控动作。
"""
import sys

sys.stdout.reconfigure(encoding="utf-8")

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
    find_machine,
)


def main():
    profile_dir = ensure_profile_copy()
    print("profile 副本:", profile_dir)
    print("型号优先级:", config.GPU_MODELS)
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

        print("\n开始 find_machine（350W 优先 → 32GB 兜底，逐地区扫描，只读）...")
        machine = find_machine(page)
        if machine:
            print("✅ 找到最优可租机器:")
            print(f"   型号: {machine['gpu_name']}")
            print(f"   内存: {machine['memory_gb']} GB")
            print(f"   系统盘: {machine['system_disk_gb']} GB")
            print(f"   数据盘: {machine['data_disk_gb']} GB，可扩容 {machine['expandable_gb']} GB")
            print(f"   空闲: {machine['free']}")
            print("   （未点击可租，流程到此为止）")
        else:
            print("❌ 所有型号在所有地区均无空闲（只读扫描结论）")

        SHOT_DIR.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(SHOT_DIR / "find_machine_scan.png"), full_page=True)
        context.close()
        print("测试结束（未点击任何可租/创建）")


if __name__ == "__main__":
    main()
