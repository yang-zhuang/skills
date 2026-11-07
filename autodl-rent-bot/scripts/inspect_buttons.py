# -*- coding: utf-8 -*-
"""只读诊断：dump 运行中实例行内所有按钮的结构（类名/文本/可见性）。"""
import sys

sys.stdout.reconfigure(encoding="utf-8")

import config
from rent_bot import goto_instance_list, parse_instance_rows, REAL_UA, STEALTH_SCRIPT
from playwright.sync_api import sync_playwright
from profile_util import ensure_profile_copy

TARGET_ID = "pquxnla19v-7601110a"


def main():
    profile = ensure_profile_copy()
    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=profile,
            channel="chrome",
            headless=config.HEADLESS,
            slow_mo=120,
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
        print(f"去重后 {len(rows)} 台: {[(r['id'], r['status']) for r in rows]}")
        target = next((r for r in rows if r["id"] == TARGET_ID), None)
        assert target, f"未找到 {TARGET_ID}"

        # 1) 主行（我们去重的那个）里的按钮
        print(f"\n--- 主行 {target['id']} 状态={target['status']} 的按钮 ---")
        btns = target["row"].locator("button").all()
        print(f"共 {len(btns)} 个 button:")
        for b in btns:
            try:
                vis = b.is_visible(timeout=500)
                txt = b.inner_text(timeout=500).strip()
                cls = b.get_attribute("class") or ""
                print(f"  visible={vis} text={txt!r} class={cls[:60]}")
            except Exception as e:
                print(f"  读取失败: {e}")

        # 2) 页面上所有含「关机」文本的按钮（全表）
        print("\n--- 全页含『关机』文本的 button ---")
        all_btns = page.locator("button").all()
        n = 0
        for b in all_btns:
            try:
                txt = b.inner_text(timeout=300).strip()
            except Exception:
                continue
            if "关机" in txt:
                n += 1
                vis = b.is_visible(timeout=300)
                print(f"  visible={vis} text={txt!r}")
        print(f"共 {n} 个")

        # 3) exact text-is 匹配测试
        m1 = target["row"].locator("button:text-is('关机')").count()
        m2 = target["row"].locator("button:has-text('关机')").count()
        print(f"\n行内 button:text-is('关机') 匹配数 = {m1}")
        print(f"行内 button:has-text('关机') 匹配数 = {m2}")

        context.close()


if __name__ == "__main__":
    main()
