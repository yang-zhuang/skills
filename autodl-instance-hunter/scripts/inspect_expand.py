# -*- coding: utf-8 -*-
"""只读探查：实例行「更多」下拉菜单 + 「扩充数据盘」弹窗的真实 DOM。

只点「更多」展开菜单并 dump HTML，不点击任何扩容/确定按钮，零费用零风险。
"""
import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

import config
from rent_bot import goto_instance_list, parse_instance_rows, REAL_UA, STEALTH_SCRIPT
from playwright.sync_api import sync_playwright
from profile_util import ensure_profile_copy

TARGET_ID = "pquxnla19v-7601110a"  # 刚租的 B55机


def main():
    profile = ensure_profile_copy()
    assert profile, "profile 复制失败"
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=profile,
            channel="chrome",
            headless=config.HEADLESS,
            slow_mo=150,
            timeout=20000,
            viewport={"width": 1920, "height": 1080},
            locale="zh-CN",
            user_agent=REAL_UA,
            args=["--window-size=1920,1080", "--disable-blink-features=AutomationControlled"],
            ignore_default_args=["--enable-automation"],
        )
        context.add_init_script(STEALTH_SCRIPT)
        page = context.new_page()

        goto_instance_list(page)
        rows = parse_instance_rows(page)
        target = next((r for r in rows if r["id"] == TARGET_ID), None)
        assert target, f"未找到目标实例 {TARGET_ID}，现有: {[r['id'] for r in rows]}"
        print(f"目标实例: {target['region']} {target['id']} 状态={target['status']}")

        row = target["row"]

        # 「更多」按钮：点击展开下拉（Element UI dropdown，也可能 hover 触发）
        more_btn = row.locator("button:has-text('更多'), span:has-text('更多')").first
        try:
            more_btn.wait_for(state="visible", timeout=4000)
            more_btn.hover(timeout=3000)
            page.wait_for_timeout(800)
            # hover 无效则点击
            menu_items = page.locator("text=扩充数据盘")
            if menu_items.count() == 0:
                more_btn.click(timeout=3000)
                page.wait_for_timeout(800)
        except Exception as e:
            print(f"展开更多菜单失败: {e}")

        # dump 下拉菜单 HTML
        dropdowns = page.locator(".el-dropdown-menu").all()
        print(f"\n找到 {len(dropdowns)} 个下拉菜单")
        for i, dd in enumerate(dropdowns):
            try:
                if dd.is_visible(timeout=1000):
                    html = dd.inner_html(timeout=2000)
                    with open("expand_dropdown.html", "w", encoding="utf-8") as f:
                        f.write(html)
                    print(f"菜单 {i}: 已保存 expand_dropdown.html（{len(html)} 字节）")
                    items = dd.locator("li").all()
                    print("  菜单项:", [it.inner_text().strip() for it in items if it.inner_text().strip()])
            except Exception:
                pass

        page.screenshot(path="screenshots/expand_dropdown.png", full_page=True)
        print("截图: screenshots/expand_dropdown.png")
        print("\n（到此为止，未点击扩充数据盘/确定，零费用）")
        context.close()


if __name__ == "__main__":
    main()
