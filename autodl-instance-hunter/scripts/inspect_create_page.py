"""创建页 DOM 检查（安全）：只点一次“可租”打开配置页，dump 结构后关闭。

- 绝不点击“创建并开机”，不产生任何费用。
- 只点一次“可租”进入配置页（该操作本身不扣费，扣费发生在“创建并开机”）。
- 输出 create_page_full.html + create_page.png 供分析镜像选择器。
"""
from pathlib import Path

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

OUT = Path(__file__).parent


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
        set_billing_mode(page)
        set_gpu_count(page)
        select_gpu_model(page)

        rent_btn = iterate_regions(page)
        if not rent_btn:
            print("未找到可租机器，结束")
            context.close()
            return

        print("点击“可租”进入创建配置页（不创建）...")
        rent_btn.click(timeout=10000)
        page.wait_for_timeout(5000)

        print("当前 URL:", page.url)
        print("当前标题:", page.title())

        SHOT_DIR.mkdir(exist_ok=True)
        page.screenshot(path=str(SHOT_DIR / "create_page.png"), full_page=True)

        html = page.content()
        (OUT / "create_page_full.html").write_text(html, encoding="utf-8")
        print(f"创建页 HTML 已保存，长度 {len(html)}")

        # 只读摘录：镜像/框架相关关键词周边片段
        import re

        for kw in ["基础镜像", "框架", "PyTorch", "创建并开机", "请选择"]:
            idx = html.find(kw)
            if idx >= 0:
                seg = html[max(0, idx - 120): idx + 200]
                print(f"\n---- 关键词[{kw}] ----")
                print(seg)

        print("\n检查结束：未点击“创建并开机”。关闭浏览器。")
        context.close()


if __name__ == "__main__":
    main()
