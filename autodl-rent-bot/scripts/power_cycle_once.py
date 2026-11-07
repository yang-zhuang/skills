# -*- coding: utf-8 -*-
"""一次性脚本：仅对指定实例执行「关机 -> 立刻开机」，使已提交的数据盘扩容生效。

不碰任何扩容入口（扩容已提交，重复执行会再扣费）。
"""
import sys

sys.stdout.reconfigure(encoding="utf-8")

from playwright.sync_api import sync_playwright

import config
from instance_log import record_event
from profile_util import ensure_profile_copy
from rent_bot import (
    STEALTH_SCRIPT,
    REAL_UA,
    goto_instance_list,
    parse_instance_rows,
)
from expand_data_disk import wait_shutdown_then_power_on

TARGET_ID = "pquxnla19v-7601110a"


def main():
    profile_dir = ensure_profile_copy()
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

        goto_instance_list(page)
        rows = parse_instance_rows(page)
        target = next((r for r in rows if r["id"] == TARGET_ID), None)
        if not target:
            print(f"未找到 {TARGET_ID}，现有: {[r['id'] for r in rows]}")
            context.close()
            sys.exit(1)
        print(f"目标: {target['region']} {target['id']} 状态={target['status']}")

        ok = wait_shutdown_then_power_on(page, target)
        record_event(
            "power_cycle_after_expand",
            {"id": TARGET_ID, "region": target["region"], "ok": ok},
        )
        page.screenshot(path="screenshots/power_cycle_done.png", full_page=True)
        context.close()
        print("\n完成" if ok else "\n未完成，请人工检查")


if __name__ == "__main__":
    main()
