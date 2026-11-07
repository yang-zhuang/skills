"""只读测试：实例列表解析 + 决策预演（绝不点击 开机/关机/任何按钮）。"""
import sys

sys.stdout.reconfigure(encoding="utf-8")

from playwright.sync_api import sync_playwright

import config
from profile_util import ensure_profile_copy
from rent_bot import (
    STEALTH_SCRIPT,
    REAL_UA,
    RELEASED_STATES,
    goto_instance_list,
    parse_instance_rows,
)


def main():
    profile_dir = ensure_profile_copy()
    print("profile 副本:", profile_dir)
    print(f"运行中实例上限: {config.MAX_RUNNING_INSTANCES}（0=不管控）")
    print(f"开机复用优先: {config.AUTO_POWER_ON}")
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

        print(f"\n解析到 {len(rows)} 台实例（按 ID 去重后）:")
        running, shutdown = [], []
        for r in rows:
            occupying = r["status"] and r["status"] not in RELEASED_STATES
            (running if occupying else shutdown).append(r)
            rem = (
                f"距释放 {r['remaining_min']} 分钟" if r["remaining_min"] >= 0 else "释放时间未知"
            )
            tag = "运行中/占用" if occupying else f"状态={r['status']!r}"
            tips = r["gpu_tips"] if r["gpu_tips"] else "无标记"
            print(f"  {r['region']:14} {r['id']}  {tag:10}  {rem}  gpuTips={tips!r}")

        print(f"\n运行中/占用: {len(running)} 台；已关机: {len(shutdown)} 台")

        # 决策预演（只打印，不执行）
        if config.MAX_RUNNING_INSTANCES <= 0:
            print("决策预演: 上限未启用 -> need_rent（直接租新）")
        elif len(running) >= config.MAX_RUNNING_INSTANCES:
            print(f"决策预演: 已达上限 {config.MAX_RUNNING_INSTANCES} -> at_limit（什么都不做）")
        else:
            slots = config.MAX_RUNNING_INSTANCES - len(running)
            print(f"决策预演: 还可开机/租新 {slots} 台")
            if shutdown and config.AUTO_POWER_ON:
                marked = [r for r in shutdown if r["gpu_tips"]]
                unmarked = [r for r in shutdown if not r["gpu_tips"]]
                cands = sorted(marked, key=lambda r: r["remaining_min"], reverse=True) + sorted(
                    unmarked, key=lambda r: r["remaining_min"], reverse=True
                )
                if marked:
                    print(f"  {len(marked)} 台带资源空闲标记（优先），{len(unmarked)} 台无标记（兜底）")
                else:
                    print("  所有已关机实例均无资源空闲标记（可能开不了机，作为兜底仍会尝试）")
                print("  将按以下顺序尝试开机:")
                for c in cands[:slots]:
                    tips = "有标记" if c["gpu_tips"] else "无标记"
                    print(
                        f"    {c['region']} {c['id']}（{tips}，距释放 {c['remaining_min']} 分钟）"
                    )
            elif shutdown:
                print("  AUTO_POWER_ON=false，跳过开机，将直接租新")
            else:
                print("  无已关机实例 -> need_rent（去市场租新）")

        page.screenshot(path="screenshots/instance_check.png", full_page=True)
        context.close()
        print("\n测试结束（纯只读，未点击任何实例操作按钮）")


if __name__ == "__main__":
    main()
