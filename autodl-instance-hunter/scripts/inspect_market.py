"""诊断脚本：进入算力市场，dump 关键 DOM 结构供修正选择器。

安全原则（与 rent_bot 一致）：
- 只读不点：进入市场后只读取 DOM / 截图，绝不点击任何“出租/创建”。
- 零登录优先：若配置了 AUTODL_USER_DATA_DIR，直接复用本机已登录的
  真实 Chrome profile（需先完全关闭 Chrome，否则 profile 被锁定会失败）。
- 不擅自登录：若 profile 被占用（Chrome 未关）导致零登录失败，本脚本
  会提示你关闭 Chrome 后重跑，不会退化为“自动登录”去触发验证码/风控。

输出：
- market_regions.json   地区标签的 tag/class/text
- market_gpu_models.json GPU 型号筛选项的 HTML 片段
- market_machines.html  机器卡片的 HTML 片段
- market_full.html      整页 HTML（供 Grep 查找结构）
"""
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

import config
from profile_util import ensure_profile_copy
from rent_bot import (
    STEALTH_SCRIPT,
    REAL_UA,
    load_storage_state,
    is_logged_in,
    login,
    save_storage_state,
)

OUT = Path(__file__).parent
SHOT_DIR = OUT / "screenshots"


def shot(page, name: str) -> None:
    """每步截图，便于人工确认进展。"""
    try:
        SHOT_DIR.mkdir(exist_ok=True)
        page.screenshot(path=str(SHOT_DIR / f"{name}.png"), full_page=True)
        print(f"[截图] {name}.png")
    except Exception as e:  # noqa: BLE001
        print(f"[截图失败] {name}: {e}")


def dump_market(page) -> None:
    """读取并保存市场页 DOM 结构（只读，不点击任何出租/创建）。"""
    page.goto("https://www.autodl.com/market/list", wait_until="domcontentloaded")
    page.wait_for_timeout(6000)
    shot(page, "market")

    # 1) 地区标签
    regions = page.evaluate(
        """() => {
            const res = [];
            document.querySelectorAll('*').forEach(el => {
                if (el.children.length !== 0) return;
                const t = (el.innerText || '').trim();
                if (t && /^.{1,12}[区]$/.test(t)) {
                    res.push({tag: el.tagName, cls: el.className.toString(), text: t});
                }
            });
            return res;
        }"""
    )
    (OUT / "market_regions.json").write_text(
        json.dumps(regions, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"地区标签识别到 {len(regions)} 个")

    # 2) GPU 型号筛选项
    gpu_html = page.evaluate(
        """() => {
            const res = [];
            document.querySelectorAll('*').forEach(el => {
                const t = (el.innerText || '').trim();
                if (/v?GPU-|GB|显存|显卡/.test(t) && t.length < 60 && el.children.length <= 3) {
                    res.push(el.outerHTML);
                }
            });
            return res.slice(0, 40);
        }"""
    )
    (OUT / "market_gpu_models.json").write_text(
        json.dumps(gpu_html, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"GPU 型号相关元素识别到 {len(gpu_html)} 个")

    # 3) 机器卡片
    machine_html = page.evaluate(
        """() => {
            const res = [];
            document.querySelectorAll('*').forEach(el => {
                const t = (el.innerText || '');
                if (t.includes('空闲') && t.includes('可租')) {
                    let node = el;
                    for (let i = 0; i < 6 && node; i++) {
                        if (node.className && node.className.toString().includes('item')) { res.push(node.outerHTML); break; }
                        node = node.parentElement;
                    }
                }
            });
            return res.slice(0, 3);
        }"""
    )
    (OUT / "market_machines.html").write_text("\n\n".join(machine_html), encoding="utf-8")
    print(f"机器卡片识别到 {len(machine_html)} 个")

    # 4) 整页 HTML
    full = page.content()
    (OUT / "market_full.html").write_text(full, encoding="utf-8")
    print(f"整页 HTML 已保存，长度 {len(full)}")


def main():
    with sync_playwright() as p:
        launch_args = [
            "--window-size=1920,1080",
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars",
            "--no-sandbox",
        ]

        if config.USER_DATA_DIR:
            # 零登录：把本机已登录的真实 Chrome profile 复制到一个非默认路径后使用。
            # （直接用默认 profile 路径会被 Chrome 拒绝远程调试；且会被运行中的 Chrome 锁定。）
            # 副本包含 AutoDL 登录态，机器人可零登录；副本与真实 Chrome 互不干扰。
            profile_dir = ensure_profile_copy()
            if not profile_dir:
                print("未找到可用 profile，回退到全新浏览器 + 手动登录路径。")
            else:
                print(f"使用 profile 副本（零登录）: {profile_dir}")
                try:
                    context = p.chromium.launch_persistent_context(
                        user_data_dir=profile_dir,
                        channel="chrome",  # 使用本机真实 Chrome，profile 与登录态才能完全匹配
                        headless=config.HEADLESS,
                        slow_mo=150,
                        timeout=20000,  # 异常情况下快速失败，避免一直卡住
                        viewport={"width": 1920, "height": 1080},
                        locale="zh-CN",
                        user_agent=REAL_UA,
                        args=launch_args,
                        ignore_default_args=["--enable-automation"],
                    )
                except Exception as e:  # noqa: BLE001
                    # 不擅自回退到“自动登录”，以免触发验证码/风控。改为提示人工处理。
                    print("!" * 64)
                    print(f"零登录启动失败: {e}")
                    print("请检查 profile 副本是否可用，或删除 chrome_profile/ 后重试。")
                    print("!" * 64)
                    return
                context.add_init_script(STEALTH_SCRIPT)
                page = context.new_page()
                dump_market(page)
                context.close()
                print("完成。请查看 market_*.json / market_full.html 分析结果")
                return

        # 未配置 USER_DATA_DIR：启动全新浏览器，必要时手动登录一次（需人工拖动滑块）
        browser = p.chromium.launch(
            headless=config.HEADLESS,
            slow_mo=150,
            args=launch_args,
            ignore_default_args=["--enable-automation"],
        )
        storage = load_storage_state()
        context = browser.new_context(
            viewport={"width": 1920, "height": 1080},
            locale="zh-CN",
            user_agent=REAL_UA,
            storage_state=storage if storage else None,
        )
        context.add_init_script(STEALTH_SCRIPT)
        page = context.new_page()
        if not storage or not is_logged_in(page):
            print("未登录，请在弹出的浏览器中手动拖动滑块完成验证...")
            login(page)
            save_storage_state(context)
            shot(page, "after_login")

        dump_market(page)
        context.close()
        print("完成。请查看 market_*.json / market_full.html 分析结果")


if __name__ == "__main__":
    main()
