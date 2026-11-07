# -*- coding: utf-8 -*-
"""诊断：点击「扩容数据盘」后，dump 页面上出现的所有弹层元素（不点确定，零费用）。"""
import sys

sys.stdout.reconfigure(encoding="utf-8")

import config
from rent_bot import goto_instance_list, parse_instance_rows, REAL_UA, STEALTH_SCRIPT
from playwright.sync_api import sync_playwright
from profile_util import ensure_profile_copy

TARGET_ID = "pquxnla19v-7601110a"


def dump_overlays(page, tag):
    print(f"\n===== {tag} 弹层扫描 =====")
    for sel in [".el-message-box", ".el-dialog__wrapper", ".el-message-box__wrapper", ".el-dialog"]:
        locs = page.locator(sel).all()
        vis = []
        for l in locs:
            try:
                if l.is_visible(timeout=500):
                    txt = l.inner_text(timeout=1000).strip().replace("\n", " | ")
                    vis.append(txt[:100])
            except Exception:
                pass
        print(f"  {sel}: {len(locs)} 个，可见 {len(vis)}")
        for v in vis:
            print(f"    -> {v}")


def main():
    profile = ensure_profile_copy()
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
        assert target, f"未找到 {TARGET_ID}"
        print(f"目标: {target['region']} {target['id']} 状态={target['status']}")

        dump_overlays(page, "点击前")

        row = target["row"]
        more_btn = row.locator("button:has-text('更多'), span:has-text('更多')").first
        more_btn.wait_for(state="visible", timeout=4000)
        more_btn.hover(timeout=3000)
        page.wait_for_timeout(800)
        if page.locator("li.el-dropdown-menu__item:has-text('扩容数据盘')").count() == 0:
            more_btn.click(timeout=3000)
            page.wait_for_timeout(800)
        item = page.locator("li.el-dropdown-menu__item:has-text('扩容数据盘')").first
        item.wait_for(state="visible", timeout=4000)
        item.click(timeout=3000)
        print("已点击「扩容数据盘」，等待弹层...")

        for wait_ms in (1000, 2000, 4000, 6000):
            page.wait_for_timeout(wait_ms // 4)
            dump_overlays(page, f"+{wait_ms}ms")

        page.screenshot(path="screenshots/expand_debug.png", full_page=True)
        print("\n截图: screenshots/expand_debug.png（未点确定，零费用）")
        context.close()


if __name__ == "__main__":
    main()
