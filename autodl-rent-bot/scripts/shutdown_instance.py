"""按实例 ID 指定关机的命令行工具。

用法（位置参数，无选项）:
    python shutdown_instance.py                     # 只读列出当前所有实例
    python shutdown_instance.py <实例ID> [更多ID...] # 关闭指定实例（可用 ID 前缀，需输入 yes 确认）

实例 ID 可在 instances_log.json（机器人开机/租机时记录）或实例列表页查到，
支持精确 ID 或唯一前缀（如 rzdjvsj47k）。
"""
import re
import sys

sys.stdout.reconfigure(encoding="utf-8")

from playwright.sync_api import sync_playwright

import config
from instance_log import record_event
from profile_util import ensure_profile_copy
from rent_bot import (
    STEALTH_SCRIPT,
    REAL_UA,
    RELEASED_STATES,
    goto_instance_list,
    parse_instance_rows,
)


def launch(page_holder):
    """启动浏览器并返回 context（用列表收集 page 供外层用）。"""
    profile_dir = ensure_profile_copy()
    print("profile 副本:", profile_dir)
    return profile_dir


def match_rows(rows, id_args):
    """按精确 ID 或唯一前缀匹配实例行。返回 (matched, errors)。"""
    matched, errors = [], []
    for arg in id_args:
        exact = [r for r in rows if r["id"] == arg]
        if exact:
            matched.append(exact[0])
            continue
        prefix = [r for r in rows if r["id"].startswith(arg)]
        if len(prefix) == 1:
            matched.append(prefix[0])
        elif len(prefix) > 1:
            errors.append(f"'{arg}' 匹配到多台: {[r['id'] for r in prefix]}，请用更长前缀")
        else:
            errors.append(f"未找到实例 ID 含 '{arg}'")
    # 同一行可能被重复传入
    uniq = []
    for m in matched:
        if m["id"] not in [u["id"] for u in uniq]:
            uniq.append(m)
    return uniq, errors


def main():
    id_args = [a for a in sys.argv[1:] if a.strip()]

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

        if not id_args:
            print(f"\n当前共 {len(rows)} 台实例（只读列出，未做任何操作）:")
            for r in rows:
                occupying = r["status"] and r["status"] not in RELEASED_STATES
                rem = (
                    f"距释放 {r['remaining_min']} 分钟" if r["remaining_min"] >= 0 else ""
                )
                tag = "[运行中/占用]" if occupying else f"[{r['status'] or '未知'}]"
                print(f"  {tag} {r['region']}  {r['id']}  {rem}")
            print("\n关闭某台: python shutdown_instance.py <实例ID>")
            context.close()
            return

        matched, errors = match_rows(rows, id_args)
        for e in errors:
            print(f"错误: {e}")
        if errors:
            context.close()
            sys.exit(1)

        running_targets = [m for m in matched if m["status"] not in RELEASED_STATES]
        skipped = [m for m in matched if m not in running_targets]
        for m in skipped:
            print(f"跳过（状态={m['status'] or '未知'}，无需关机）: {m['region']} {m['id']}")
        if not running_targets:
            print("没有需要关机的运行中实例")
            context.close()
            return

        print("\n即将关闭以下运行中实例（关机后停止计费，数据保留）:")
        for m in running_targets:
            print(f"  {m['region']}  {m['id']}  状态={m['status']}")
        ans = input("确认关机？输入 yes 继续: ").strip().lower()
        if ans not in ("yes", "y"):
            print("已取消，未做任何操作")
            context.close()
            return

        for m in running_targets:
            print(f"\n关闭 {m['region']} {m['id']} ...")
            # 注意：行内还有「设置定时关机」按钮，必须按可见文本精确比对；
            # 实测 button:text-is('关机') 匹配 0（文本在子级 span 里），不能用它。
            btn = None
            for b in m["row"].locator("button").all():
                try:
                    if b.inner_text(timeout=500).strip() == "关机" and b.is_visible(timeout=500):
                        btn = b
                        break
                except Exception:
                    continue
            if btn is None:
                print("  行内未找到「关机」按钮，跳过")
                record_event(
                    "manual_shutdown_failed",
                    {"id": m["id"], "region": m["region"], "reason": "未找到关机按钮"},
                )
                continue
            try:
                btn.wait_for(state="visible", timeout=4000)
                btn.click(timeout=5000)
            except Exception as e:  # noqa: BLE001
                print(f"  关机按钮点击失败: {e}")
                record_event(
                    "manual_shutdown_failed",
                    {"id": m["id"], "region": m["region"], "reason": f"点击失败: {e}"},
                )
                continue

            # 若出现确认弹窗（文案含'关机'），点其中的确定
            try:
                dlg = page.locator(
                    ".el-dialog:has-text('关机'), .el-message-box:has-text('关机')"
                ).first
                dlg.wait_for(state="visible", timeout=3000)
                dlg.locator("button:has-text('确定'), button:has-text('确认')").first.click(
                    timeout=3000
                )
                print("  已确认关机弹窗")
            except Exception:
                pass

            # 轮询状态直到已关机（最多 60 秒）
            ok = False
            for _ in range(30):
                page.wait_for_timeout(2000)
                cur = {r["id"]: r for r in parse_instance_rows(page)}.get(m["id"])
                if cur and cur["status"] == "已关机":
                    print("  已关机 ✓")
                    record_event(
                        "manual_shutdown",
                        {"id": m["id"], "region": m["region"], "status": "已关机", "by": "human"},
                    )
                    ok = True
                    break
                if cur and cur["status"] == m["status"]:
                    continue  # 状态还没变，继续等
            if not ok:
                cur = {r["id"]: r for r in parse_instance_rows(page)}.get(m["id"], {})
                print(f"  关机后状态异常: {cur.get('status', '未知')}，请到控制台确认")

        context.close()
        print("\n完成")


if __name__ == "__main__":
    main()
