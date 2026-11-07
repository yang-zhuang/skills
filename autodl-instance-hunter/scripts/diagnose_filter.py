"""
诊断：GPU 型号筛选在“勾选后 / 切换地区后”是否仍然生效。
只读操作：不点击任何“可租”按钮，零费用。
步骤：记录初始 -> 勾选 vGPU-48GB-350W -> 记录 -> 切到北京B区 -> 记录。
"""
import sys

from playwright.sync_api import sync_playwright

import config
from profile_util import ensure_profile_copy
from rent_bot import STEALTH_SCRIPT, REAL_UA, goto_market


def dump_state(page, tag):
    print(f"\n===== {tag} =====")
    # 1) 目标勾选框状态
    for model in ["vGPU-48GB-350W", "vGPU-32GB", "vGPU-48GB"]:
        label = page.locator(f"label.el-checkbox:has(input[value='{model}'])").first
        try:
            cls = label.get_attribute("class") or ""
            checked = label.locator("input").first.is_checked()
            print(f"checkbox {model}: class={cls!r} checked={checked}")
        except Exception as e:
            print(f"checkbox {model}: 读取失败 {e}")
    # 2) 筛选栏“GPU型号”当前显示（全部 / 已选个数）
    try:
        row = page.locator(".el-form-item:has-text('GPU型号'), div:has-text('GPU型号')").first
        txt = row.inner_text(timeout=2000)
        print(f"筛选栏GPU型号行文本: {txt!r}")
    except Exception as e:
        print(f"筛选栏读取失败: {e}")
    # 3) 当前列表所有卡片 gpu-name（前 12 个）
    names = []
    for card in page.locator(".list-item").all()[:12]:
        try:
            names.append(card.locator(".machine-info .gpu-name .name").first.inner_text(timeout=1500))
        except Exception:
            names.append("?")
    print(f"列表卡片 gpu-name ({len(names)}): {names}")


def main() -> None:
    profile_dir = ensure_profile_copy()
    if not profile_dir:
        print("未找到可用 profile 副本")
        sys.exit(1)

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
        dump_state(page, "初始状态（未勾选）")

        # 勾选 vGPU-48GB-350W
        label = page.locator("label.el-checkbox:has(input[value='vGPU-48GB-350W'])").first
        label.click(timeout=5000)
        page.wait_for_timeout(2000)
        dump_state(page, "勾选 vGPU-48GB-350W 后")

        # 切换到北京B区
        sel = ".el-radio-button:has(.el-radio-button__inner:text-is('北京B区'))"
        page.locator(sel).first.click(timeout=5000)
        page.wait_for_timeout(2500)
        dump_state(page, "切换到北京B区后")

        page.screenshot(path="screenshots/diagnose_filter.png")
        print("\n诊断完成（未点可租，零费用）")
        context.close()


if __name__ == "__main__":
    main()
