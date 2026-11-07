# -*- coding: utf-8 -*-
"""只读验证：REUSE_INSTANCES=false 时 manage_instances 应跳过开机，直接返回 need_rent。

不点任何可租/开机/创建按钮，零风险。
"""
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

import config
from rent_bot import manage_instances, REAL_UA, STEALTH_SCRIPT
from playwright.sync_api import sync_playwright
from profile_util import ensure_profile_copy


def main():
    assert config.REUSE_INSTANCES is False, "请先把 .env 中 AUTODL_REUSE_INSTANCES 设为 false"
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

        status, seen_ids = manage_instances(page)
        print(f"\n返回状态: {status!r}（期望 'need_rent'）")
        print(f"当前实例 ID（供租新对比）: {sorted(seen_ids)}")
        assert status == "need_rent", "应该返回 need_rent 而不是 " + status
        print("\nPASS: 已跳过实例盘点/开机复用，机器人将直接去算力市场租新机")
        context.close()


if __name__ == "__main__":
    main()
