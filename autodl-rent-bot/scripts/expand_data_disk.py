# -*- coding: utf-8 -*-
"""扩充指定实例的数据盘，并立即关机->开机使扩容生效。

用法（位置参数，无选项）:
    python expand_data_disk.py <实例ID> <扩容GB>

例:
    python expand_data_disk.py pquxnla19v-7601110a 100

说明:
- 扩容输入的是"新增容量"（如填 100 表示在现有基础上加 100GB，页面会显示扩容后容量）
- 运行中的实例点「扩容数据盘」会弹"需重启实例生效"提示，脚本自动点「继续」
- 扩容确认后不会立刻生效，必须关机再开机；脚本随后自动关机并在状态变为
  「已关机」的第一时间点「开机」（按量计费实例关机后保留 15 天，但 GPU 资源
  紧张时开机要抢，所以关->开之间不做任何多余等待）
- 全程记录 instances_log.json
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
    RELEASED_STATES,
    goto_instance_list,
    parse_instance_rows,
)


def expand_disk(page, target, gb):
    """对目标实例执行扩容操作。返回是否成功提交扩容。"""
    row = target["row"]
    print(f"展开「更多」菜单（{target['region']} {target['id']}）...")

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
    print("  已点击「扩容数据盘」")

    # 轮询等待：先弹"需重启生效"提示（运行中实例）-> 点继续；再等真正的扩容对话框。
    # 用 :visible 过滤（页面里有 15 个隐藏 .el-dialog，:has-text 直接 .first 会命中隐藏元素）。
    tip = page.locator(".el-message-box:visible")
    dlg_loc = page.locator(".el-dialog:visible").filter(has_text="扩容后容量")
    dlg = None
    clicked_continue = False
    for _ in range(20):  # 最多 10 秒
        page.wait_for_timeout(500)
        if not clicked_continue and tip.count() > 0 and tip.first.is_visible():
            txt = tip.first.inner_text(timeout=1500).strip().replace("\n", " ")
            print(f"  提示弹窗: {txt[:60]}")
            tip.first.locator("button:has-text('继续')").first.click(timeout=3000)
            clicked_continue = True
            print("  已点击「继续」")
        if dlg_loc.count() > 0 and dlg_loc.first.is_visible():
            dlg = dlg_loc.first
            break
    if dlg is None:
        raise RuntimeError("扩容对话框未出现（超时 10 秒）")

    box = dlg.locator("input").first
    # 实测：对话框出现后输入框可能长时间保持 disabled（Element UI 初始化时序），
    # 普通 fill 会一直等。这里直接用 JS 启用并按 Vue 的方式派发 input 事件写入数值。
    was_disabled = box.evaluate("el => el.disabled || el.getAttribute('aria-disabled') === 'true'")
    print(f"  输入框 disabled 状态: {was_disabled}")
    box.evaluate(
        """(el, val) => {
            el.disabled = false;
            el.removeAttribute('disabled');
            el.removeAttribute('aria-disabled');
            const setter = Object.getOwnPropertyDescriptor(
                window.HTMLInputElement.prototype, 'value'
            ).set;
            setter.call(el, val);
            el.dispatchEvent(new Event('input', {bubbles: true}));
            el.dispatchEvent(new Event('change', {bubbles: true}));
        }""",
        str(gb),
    )
    page.wait_for_timeout(800)
    after = dlg.inner_text(timeout=2000)
    print("  填入后对话框内容:")
    for line in after.splitlines():
        s = line.strip()
        if s and ("扩容" in s or "费用" in s or "GB" in s):
            print(f"    {s}")

    dlg.locator("button:has-text('确定')").first.click(timeout=3000)
    print(f"  已点击「确定」，扩容 +{gb}GB 提交")
    page.wait_for_timeout(2000)
    return True


def find_row_button(row, exact_text):
    """在实例行内按按钮可见文本精确匹配（button:text-is 会因子级 span 匹配失败，
    button:has-text 又会误中「设置定时关机」——所以逐个遍历比对最可靠）。"""
    for b in row.locator("button").all():
        try:
            if b.inner_text(timeout=500).strip() == exact_text and b.is_visible(timeout=500):
                return b
        except Exception:
            continue
    return None


def wait_shutdown_then_power_on(page, target):
    """关机并在状态变为「已关机」的瞬间立刻开机（抢资源，零多余等待）。

    注意：扩容确定后表格会重渲染，旧行引用会失效——每步都按 ID 重新定位行。
    """
    def find_row():
        rows = parse_instance_rows(page)
        return next((r for r in rows if r["id"] == target["id"]), None)

    print("\n关机以使扩容生效...")
    # 表格可能正在刷新，轮询重试定位+点击（最多 30 秒）
    clicked = False
    for _ in range(15):
        cur = find_row()
        if cur:
            btn = find_row_button(cur["row"], "关机")
            if btn is not None:
                try:
                    btn.wait_for(state="visible", timeout=2000)
                    btn.click(timeout=3000)
                    clicked = True
                    break
                except Exception:
                    pass
        page.wait_for_timeout(2000)
    if not clicked:
        print("  !! 30 秒内未能点到「关机」按钮，请人工处理")
        return False
    # 可能的确认弹窗
    try:
        dlg = page.locator(".el-message-box:visible").first
        dlg.wait_for(state="visible", timeout=3000)
        dlg.locator("button:has-text('确定'), button:has-text('确认')").first.click(timeout=3000)
        print("  已确认关机弹窗")
    except Exception:
        pass

    # 轮询等待「已关机」，1.5 秒间隔，最多 90 秒
    for _ in range(60):
        page.wait_for_timeout(1500)
        cur = find_row()
        if cur and cur["status"] == "已关机":
            print("  已关机 ✓")
            break
    else:
        cur = find_row()
        print(f"  !! 关机超时，当前状态: {cur['status'] if cur else '未知'}，不执行开机，请人工处理")
        return False

    # 立刻开机，不等待（资源紧张，晚一秒都可能抢不到）
    print("  立刻点「开机」...")
    cur = find_row()
    if not cur:
        print("  !! 找不到实例行，请人工开机")
        return False
    on_btn = find_row_button(cur["row"], "开机")
    if on_btn is None:
        print("  !! 行内未找到「开机」按钮，请人工开机")
        return False
    on_btn.wait_for(state="visible", timeout=4000)
    on_btn.click(timeout=5000)
    try:
        dlg = page.locator(".el-message-box:visible").first
        dlg.wait_for(state="visible", timeout=3000)
        dlg.locator("button:has-text('确定'), button:has-text('确认')").first.click(timeout=3000)
        print("  已确认开机弹窗")
    except Exception:
        pass

    # 轮询等待回到运行中
    for _ in range(60):
        page.wait_for_timeout(2000)
        c = find_row()
        if c and c["status"] not in RELEASED_STATES:
            print(f"  开机成功 ✓ 当前状态: {c['status']}")
            return True
    print("  !! 开机后 2 分钟仍未运行，请到控制台查看（资源可能不足）")
    return False


def main():
    args = [a for a in sys.argv[1:] if a.strip()]
    if len(args) != 2:
        print("用法: python expand_data_disk.py <实例ID> <扩容GB>")
        print("例:   python expand_data_disk.py pquxnla19v-7601110a 100")
        sys.exit(1)
    target_id, gb = args[0], int(args[1])

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
        target = next((r for r in rows if r["id"] == target_id), None)
        if not target:
            print(f"未找到实例 {target_id}，现有: {[r['id'] for r in rows]}")
            context.close()
            sys.exit(1)
        was_running = target["status"] not in RELEASED_STATES
        print(f"目标: {target['region']} {target['id']} 状态={target['status']} 扩容 +{gb}GB")

        expand_disk(page, target, gb)
        record_event(
            "data_disk_expanded",
            {"id": target["id"], "region": target["region"], "gb": gb, "was_running": was_running},
        )
        print("已记录 instances_log.json (data_disk_expanded)")

        if was_running:
            ok = wait_shutdown_then_power_on(page, target)
            record_event(
                "power_cycle_after_expand",
                {"id": target["id"], "region": target["region"], "ok": ok},
            )
        else:
            print("\n实例原本已关机：扩容在下次开机时生效（无需重启）")

        page.screenshot(path="screenshots/expand_done.png", full_page=True)
        context.close()
        print("\n完成")


if __name__ == "__main__":
    main()
