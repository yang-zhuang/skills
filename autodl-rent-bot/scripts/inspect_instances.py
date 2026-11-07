"""只读诊断：dump 实例列表页（/console/instance/list）的 DOM 结构。

只做：打开页面、截图、抽取每张实例卡片的关键信息（状态/按钮/规格）。
绝不点击任何 开机/关机/更多 按钮。
"""
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from playwright.sync_api import sync_playwright

import config
from profile_util import ensure_profile_copy
from rent_bot import STEALTH_SCRIPT, REAL_UA

BASE = Path(__file__).parent


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

        page.goto(
            "https://www.autodl.com/console/instance/list",
            wait_until="domcontentloaded",
        )
        page.wait_for_timeout(4000)
        print("URL:", page.url)
        print("标题:", page.title())

        (BASE / "screenshots").mkdir(exist_ok=True)
        page.screenshot(path=str(BASE / "screenshots" / "instance_list.png"), full_page=True)

        html = page.content()
        (BASE / "instance_full.html").write_text(html, encoding="utf-8")
        print("整页 HTML 已存 instance_full.html,", len(html), "字节")

        # 抽取实例卡片：先猜常见容器
        cards = page.locator(".list-item, .instance-item, tr.el-table__row, [class*='instance']").all()
        print(f"\n候选卡片元素数: {len(cards)}")

        dump = []
        for i, c in enumerate(cards[:20]):
            try:
                txt = c.inner_text(timeout=1500).replace("\n", " | ")
                if len(txt) < 5:
                    continue
                btns = []
                for b in c.locator("button").all():
                    try:
                        btxt = b.inner_text(timeout=1000).strip()
                        if btxt:
                            btns.append(btxt)
                    except Exception:
                        pass
                dump.append({"index": i, "text": txt[:300], "buttons": btns})
            except Exception:
                continue

        (BASE / "instance_cards.json").write_text(
            json.dumps(dump, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"已解析 {len(dump)} 张卡片，详见 instance_cards.json")
        for d in dump:
            print(f"\n--- 卡片 {d['index']} ---")
            print("  文本:", d["text"][:200])
            print("  按钮:", d["buttons"])

        context.close()
        print("\n完成（纯只读，未点击任何实例操作按钮）")


if __name__ == "__main__":
    main()
