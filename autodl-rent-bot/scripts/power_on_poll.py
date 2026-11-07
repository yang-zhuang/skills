# -*- coding: utf-8 -*-
"""轮询开机指定实例（anti-ban 友好版）。

场景：目标实例（如 pquxnla19v-7601110a）已关机，但 GPU 型号（vGPU-48GB-350W）
极度稀缺、长期没有释放名额。直接点「开机」很可能因主机 GPU 资源不足而失败，
需要反复尝试直到某台主机腾出空闲卡。

防封号设计（用户硬性要求）：
- 全程复用本机已登录 Chrome profile 副本（零登录，不触发验证码/登录风控）。
- 注入 STEALTH_SCRIPT + 真实 UA + 关闭 AutomationControlled 指纹。
- slow_mo 拉长操作间隔，降低机器特征。
- 不刷新算力市场（那是最高风险区）。只在 /console/instance/list 自己的实例页
  上轮询；且优先“原地重新解析当前页 DOM”（AutoDL 列表本身会 JS 自动刷新），
  而非每轮都整页 page.goto —— 把整页导航降到最低。
- 每轮间隔 = 基准 * 随机系数(0.85~1.25) + 随机 0~10s 抖动，杜绝固定节奏被统计识别。
- 点「开机」若按钮 disabled / 不存在（主机无空闲卡的常见表现），不报错，静默等待下一轮。
- 失败不崩溃：捕获一切异常，记日志后继续下一轮。
- 自带日志文件（每行 flush），进程被上层强杀也能从日志看到进度，可安全重启。

用法（位置参数）:
    python power_on_poll.py [实例ID] [最大轮数] [基准间隔秒]

例:
    python power_on_poll.py pquxnla19v-7601110a 120 45
"""
import sys
import time
import random
import datetime as _dt

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
from expand_data_disk import find_row_button

TARGET_ID = "pquxnla19v-7601110a"
MAX_ATTEMPTS = 120
BASE_INTERVAL = 45  # 秒，每轮之间的基准等待

LOG_PATH = "power_on_poll.log"

# 每隔多少轮做一次“无资源标记也试探点击”的兜底（防止 .gpuTips 标记与实际不一致时永远开不了）
PROBE_EVERY = 5

# 弹窗安全按钮白名单：只点这些。绝不点「克隆实例」（会新建机器、额外扣费）
# 和「无卡模式开机」（没有 GPU，跑不了模型）。
# “该主机GPU不足”弹窗的唯一按钮是「我已知晓了」，点了只是关闭弹窗、无任何副作用。
SAFE_BUTTONS = ("我已知晓了", "已知晓", "我知道", "等待GPU释放", "确定", "确认", "立即开机")


def dismiss_dialog(page, tag):
    """关闭当前可见弹窗：打印文本 + 截图 + 点白名单内的安全按钮。返回弹窗文本；无弹窗返回 None。

    必须用 .el-message-box:visible / .el-dialog:visible 过滤——实例页 DOM 里有
    15 个隐藏的 .el-dialog，不加 :visible 会让 .first 命中隐藏元素、真弹窗永远点不到，
    遮罩层随后拦截一切点击（2026-10-21 实测踩坑）。
    """
    try:
        box = page.locator(".el-message-box:visible, .el-dialog:visible").first
        if not (box.count() > 0 and box.is_visible()):
            return None
        txt = box.inner_text(timeout=1500).strip().replace("\n", " ")
        log(f"  [{tag}] 弹窗内容: {txt[:80]}")
        try:
            page.screenshot(path="screenshots/power_on_dialog.png")
        except Exception:
            pass
        for label in SAFE_BUTTONS:
            b = box.locator(f"button:has-text('{label}')").first
            if b.count() > 0 and b.is_visible():
                b.click(timeout=3000)
                log(f"  [{tag}] 已点弹窗「{label}」")
                return txt
        # 兜底：点弹窗右上角关闭 ×
        x = box.locator(".el-message-box__headerbtn, .el-dialog__headerbtn").first
        if x.count() > 0 and x.is_visible():
            x.click(timeout=3000)
            log(f"  [{tag}] 已点弹窗关闭按钮")
            return txt
        log(f"  [{tag}] !! 弹窗里没有白名单安全按钮，请人工处理")
        return txt
    except Exception as e:  # noqa: BLE001
        log(f"  [{tag}] 处理弹窗异常: {e}")
        return None


def log(msg: str) -> None:
    ts = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
    except Exception:
        pass


def find_power_on_button(row):
    """返回行内可见且未禁用的「开机」按钮；不存在或禁用都返回 None。"""
    for b in row.locator("button").all():
        try:
            txt = b.inner_text(timeout=500).strip()
            if txt != "开机":
                continue
            if not b.is_visible(timeout=500):
                continue
            if b.is_disabled():
                return None  # 禁用态 = 主机暂无空闲卡，常见“开不了机”表现
            return b
        except Exception:
            continue
    return None


def jitter_wait(base: float) -> None:
    secs = base * random.uniform(0.85, 1.25) + random.uniform(0, 10)
    log(f"随机抖动等待 {secs:.1f}s 后进入下一轮...")
    time.sleep(secs)


def attempt_power_on(page, target) -> str:
    """对单台已关机实例尝试一次开机。

    返回:
      'running'  -> 已成功开机运行（终态）
      'pending'  -> 点了开机但仍在 开机中/排队，状态未稳定（可继续观察）
      'no_slot'  -> 主机无空闲卡（按钮禁用/不存在），本轮不可开
      'still_off'-> 点了开机但状态仍停在 已关机（资源不足），需下轮重试
      'error'    -> 异常
    """
    btn = find_power_on_button(target["row"])
    if btn is None:
        log(f"  [{target['id']}] 无可用「开机」按钮（主机大概率无空闲卡），本轮跳过")
        return "no_slot"

    log(f"  [{target['id']}] 点击「开机」...")
    try:
        btn.click(timeout=5000)
    except Exception as e:  # noqa: BLE001
        log(f"  [{target['id']}] 点击开机失败: {e}")
        return "error"

    # 可能的弹窗（开机确认框 / “该主机GPU不足”提示）。必须 :visible 过滤——
    # 页面里有 15 个隐藏 .el-dialog，不加 :visible 会让 .first 命中隐藏元素、真弹窗点不到。
    page.wait_for_timeout(800)
    dlg_txt = dismiss_dialog(page, target["id"])
    if dlg_txt and "GPU不足" in dlg_txt:
        log(f"  [{target['id']}] 主机 GPU 不足（0 空闲卡），本轮不开机，等释放后重试")
        return "no_slot"

    # 轮询状态：开机成功会变为 开机中 -> 运行中
    for i in range(15):
        time.sleep(2)
        cur = {r["id"]: r for r in parse_instance_rows(page)}.get(target["id"])
        if not cur:
            continue
        st = cur["status"]
        if st == "运行中":
            log(f"  [{target['id']}] 状态=运行中 ✓")
            return "running"
        if st not in RELEASED_STATES and st != "已关机":
            # 开机中 / 排队中 等中间态
            if i % 3 == 0:
                log(f"  [{target['id']}] 状态={st}（中间态，继续观察）")
            return "pending" if i < 14 else "still_off"
    log(f"  [{target['id']}] 点击开机后状态仍停在 已关机（主机资源不足），本轮失败")
    return "still_off"


def main():
    args = [a for a in sys.argv[1:] if a.strip()]
    target_id = args[0] if len(args) > 0 else TARGET_ID
    max_attempts = int(args[1]) if len(args) > 1 else MAX_ATTEMPTS
    base_interval = float(args[2]) if len(args) > 2 else BASE_INTERVAL

    log("=" * 60)
    log(f"轮询开机目标: {target_id} | 最大轮数={max_attempts} | 基准间隔={base_interval}s")
    log("=" * 60)

    profile_dir = ensure_profile_copy()
    if not profile_dir:
        log("!! 未找到 profile 副本，无法零登录，退出")
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

        # 仅打开一次实例列表页；后续优先原地重新解析 DOM（AutoDL 自己会 JS 刷新）
        goto_instance_list(page)
        log("已进入实例列表页，开始轮询...")

        success = False
        for attempt in range(1, max_attempts + 1):
            log(f"----- 第 {attempt}/{max_attempts} 轮 -----")
            try:
                rows = parse_instance_rows(page)
                target = next((r for r in rows if r["id"] == target_id), None)

                if not target:
                    # 解析不到（页面偶发异常/未加载完）：整页重导航一次兜底
                    log(f"  [{target_id}] 本轮未解析到目标行，整页重导航兜底")
                    goto_instance_list(page)
                    rows = parse_instance_rows(page)
                    target = next((r for r in rows if r["id"] == target_id), None)
                    if not target:
                        log(f"  [{target_id}] 仍找不到，等待后重试")
                        jitter_wait(base_interval)
                        continue

                st = target["status"]
                log(f"  [{target_id}] 当前状态={st} 区域={target['region']}")

                if st not in RELEASED_STATES:
                    log(f"  [{target_id}] 已处于非关机状态（{st}），视为开机成功 ✓")
                    record_event(
                        "power_on",
                        {
                            "id": target_id,
                            "region": target["region"],
                            "status": st,
                            "by": "bot",
                            "via": "poll",
                        },
                    )
                    success = True
                    break

                # 自愈：清掉上一轮残留弹窗（遮罩层会挡住一切点击）
                dismiss_dialog(page, target_id)

                # 主机无空闲卡时不点开机：AutoDL 只在主机有空闲资源时给关机实例行渲染
                # .gpuTips 标记（parse_instance_rows 的 gpu_tips 字段），这是只读信号。
                # 无标记时点击几乎只会弹“该主机GPU不足”，纯属浪费请求、徒增风控特征；
                # 每 PROBE_EVERY 轮做一次兜底试探，防止标记与实际不一致时永远开不了。
                if not target.get("gpu_tips") and attempt % PROBE_EVERY != 0:
                    log(f"  [{target_id}] 无资源空闲标记（主机大概率无空闲卡），本轮零点击，仅等待")
                    jitter_wait(base_interval)
                    continue
                if not target.get("gpu_tips"):
                    log(f"  [{target_id}] 第 {attempt} 轮：无资源标记，兜底试探点击")

                # 已关机 -> 尝试开机
                result = attempt_power_on(page, target)
                if result == "running" or result == "pending":
                    record_event(
                        "power_on",
                        {
                            "id": target_id,
                            "region": target["region"],
                            "status": result,
                            "by": "bot",
                            "via": "poll",
                        },
                    )
                    if result == "running":
                        success = True
                        break
                    # pending：状态还在中间态，短暂等待后继续观察本页（不计入轮数重开）
                    log(f"  [{target_id}] 开机中，原地再观察 20s...")
                    time.sleep(20)
                    continue

                # no_slot / still_off / error -> 抖动等待后下一轮
                jitter_wait(base_interval)

            except Exception as e:  # noqa: BLE001
                log(f"  !! 第 {attempt} 轮异常: {e}（不退出，等待后重试）")
                try:
                    page.screenshot(path="screenshots/poll_error.png", full_page=True)
                except Exception:
                    pass
                jitter_wait(base_interval)

        if success:
            log("=" * 60)
            log(f"✅ 目标实例 {target_id} 已开机，轮询结束")
            log("=" * 60)
            try:
                page.screenshot(path="screenshots/poll_success.png", full_page=True)
            except Exception:
                pass
        else:
            log("=" * 60)
            log(f"⏱  已达最大轮数（{max_attempts}）仍未成功开机，请人工查看或延长轮数后重跑")
            log("=" * 60)

        context.close()


if __name__ == "__main__":
    main()
