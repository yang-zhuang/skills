"""
AutoDL 自动租机机器人。

流程：
1. 登录 AutoDL
2. 进入算力市场
3. 设置计费方式、GPU 型号、GPU 数量
4. 遍历各地区寻找目标 GPU 空闲机器
5. 点击"可租"/"出租"进入创建页
6. 选择基础镜像 -> PyTorch -> 各项默认第一个
7. 点击"创建并开机"
8. 若提示"空闲 GPU 数量不足"，返回算力市场继续轮询

安全设计：
- 凭证从 .env 读取，不硬编码
- 默认开启 DRY_RUN，不会真的下单
- 测试阶段默认 headless=false，浏览器可见
- 不调用 page.reload()，通过点击导航降低刷新频率
"""
from __future__ import annotations

import json
import logging
import random
import re
import sys
import time
from pathlib import Path
from typing import Optional

from playwright.sync_api import (
    Locator,
    Page,
    TimeoutError as PlaywrightTimeoutError,
    sync_playwright,
)

import config
from instance_log import record_event
from profile_util import ensure_profile_copy

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("autodl-rent-bot")

# 截图目录：用于人工确认时留存证据
SHOT_DIR = Path(__file__).parent / "screenshots"


# ------------------------------ 工具函数 ------------------------------

def safe_click(locator: Locator, timeout: int = 5000) -> None:
    """等待元素可见并点击，失败时抛出清晰异常。"""
    locator.first.wait_for(state="visible", timeout=timeout)
    locator.first.scroll_into_view_if_needed(timeout=timeout)
    locator.first.click(timeout=timeout)


def click_if_visible(page: Page, selector: str, timeout: int = 3000) -> bool:
    """如果元素可见则点击，返回是否成功。"""
    try:
        loc = page.locator(selector).first
        loc.wait_for(state="visible", timeout=timeout)
        loc.click(timeout=timeout)
        return True
    except PlaywrightTimeoutError:
        return False


def wait_humanly(seconds: float) -> None:
    """在关键操作之间加入固定等待，避免触发平台风控。"""
    if seconds > 0:
        logger.info(f"等待 {seconds:.1f} 秒...")
        time.sleep(seconds)


def wait_with_jitter(base_seconds: float) -> None:
    """带随机抖动的轮询等待（防检测专用）。

    固定间隔是最容易被平台统计识别的脚本节奏：每轮恒定 N 秒。
    这里在基准值上乘 0.85~1.25 的随机系数，并追加 0~5 秒随机偏移，
    让每轮间隔都不同。仅用于长间隔轮询；短操作间等待仍用 wait_humanly。
    """
    secs = base_seconds * random.uniform(0.85, 1.25) + random.uniform(0, 5)
    logger.info(f"等待 {secs:.1f} 秒（随机抖动）...")
    time.sleep(secs)


def human_confirm(page: Page, step: str) -> bool:
    """危险操作前的人工确认闸门。

    返回 True 才允许继续。任何非 yes 的回答都会中止，避免机器人擅自
    下单/计费——这是账号安全（防封号、防误扣费）的最后一道人工防线。
    """
    try:
        SHOT_DIR.mkdir(parents=True, exist_ok=True)
        shot_path = SHOT_DIR / f"confirm_{int(time.time())}.png"
        page.screenshot(path=str(shot_path), full_page=True)
        logger.warning("已截图留存: %s", shot_path)
    except Exception as e:  # noqa: BLE001
        logger.warning("截图失败: %s", e)

    logger.warning("=" * 64)
    logger.warning("⚠️  需要人工确认: %s", step)
    logger.warning("请在浏览器中核对机器/镜像/计费信息。")
    logger.warning("输入 yes 允许机器人继续点击；输入其他任意内容则中止")
    logger.warning("（你也可以直接在浏览器里手动操作，然后输入 no 让机器人退出）。")
    logger.warning("=" * 64)
    try:
        ans = input("人工确认 [yes/NO]: ").strip().lower()
    except EOFError:
        ans = ""
    ok = ans in ("yes", "y")
    logger.warning("人工确认结果: %s", "继续" if ok else "中止")
    return ok


# ------------------------------ 页面操作 ------------------------------

def wait_for_captcha(page: Page) -> None:
    """检测登录滑块验证码，提示用户手动完成并等待其消失。"""
    captcha_selectors = [
        ".ant-modal:has-text('请完成安全验证')",
        "div:has-text('拖动滑块完成拼图')",
        ".slider-captcha",
        "[class*='captcha']",
        "[class*='slider']",
    ]
    for sel in captcha_selectors:
        loc = page.locator(sel).first
        try:
            loc.wait_for(state="visible", timeout=3000)
            logger.warning("=" * 50)
            logger.warning("检测到滑块验证码，请手动拖动完成验证！")
            logger.warning("验证完成后，脚本会自动继续...")
            logger.warning("=" * 50)
            # 最多等待 120 秒，期间每 1 秒检查一次弹窗是否消失
            for i in range(120):
                if not loc.is_visible(timeout=1000):
                    logger.info("验证码已处理")
                    return
                time.sleep(1)
            raise RuntimeError("验证码处理超时，请重试")
        except PlaywrightTimeoutError:
            continue


def save_storage_state(context) -> None:
    """把登录态（含 localStorage / sessionStorage）完整保存到文件。

    AutoDL 的登录态放在 localStorage 而非 cookie，必须用 storage_state
    整体持久化，单存 cookie 会导致下次被踢回登录页。
    """
    try:
        cookie_path = Path(config.COOKIE_FILE)
        # storage_state(path=...) 会保存 cookies + localStorage，比只存 cookies 完整
        context.storage_state(path=str(cookie_path))
        logger.info(f"登录态已保存到 {cookie_path}")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"保存登录态失败: {e}")


def load_storage_state():
    """读取已保存的登录态，返回 dict（含 cookies/origins）或 None。"""
    cookie_path = Path(config.COOKIE_FILE)
    if not cookie_path.exists():
        return None
    try:
        return json.loads(cookie_path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"读取登录态失败: {e}")
        return None


def is_logged_in(page) -> bool:
    """访问首页判断是否已经登录。"""
    page.goto("https://www.autodl.com/", wait_until="domcontentloaded")
    page.wait_for_timeout(2000)
    logged = "/login" not in page.url
    if logged:
        logger.info("已复用已保存的登录态，跳过登录")
    else:
        logger.info("已保存的登录态已失效，将重新登录")
    return logged


def login(page: Page) -> None:
    """登录 AutoDL。"""
    logger.info("打开登录页...")
    page.goto("https://www.autodl.com/login", wait_until="domcontentloaded")
    # 不等待 networkidle：页面可能有持续轮询，避免超时
    page.wait_for_timeout(1500)

    # 手机号：优先匹配 type=tel 或 placeholder 含手机
    phone_input = page.locator("input[type='tel'], input[placeholder*='手机'], input[placeholder*='手机号']").first
    phone_input.wait_for(state="visible", timeout=15000)
    phone_input.fill(config.USERNAME)
    logger.info("已填入用户名")

    # 密码
    pwd_input = page.locator("input[type='password']").first
    pwd_input.wait_for(state="visible", timeout=15000)
    pwd_input.fill(config.PASSWORD)
    logger.info("已填入密码")

    # 登录按钮（避免和顶部导航"登录"混淆，优先找表单区按钮）
    login_btn = page.locator("button:has-text('登录')").filter(has=page.locator("text=登录")).first
    # 兜底：直接点所有含"登录"文字的按钮
    if not login_btn.is_visible(timeout=3000):
        login_btn = page.locator("button:has-text('登录')").first

    login_btn.click(timeout=10000)
    logger.info("点击登录，等待跳转...")

    # 等待可能的滑块验证码并提示用户手动处理
    wait_for_captcha(page)

    # 等待离开登录页
    try:
        page.wait_for_url(lambda url: "/login" not in url, timeout=20000)
    except PlaywrightTimeoutError:
        # 再次检查验证码
        wait_for_captcha(page)
        if "/login" in page.url:
            raise RuntimeError("登录后未跳转，请检查用户名/密码或验证码是否已处理")

    page.wait_for_timeout(1500)
    logger.info(f"登录成功，当前页面: {page.url}")


def goto_market(page: Page) -> None:
    """通过顶部导航进入算力市场（不刷新页面）。"""
    logger.info("点击进入算力市场...")
    clicked = False
    for sel in [
        "a:has-text('算力市场')",
        ".nav-item:has-text('算力市场')",
        "li:has-text('算力市场')",
        "text=算力市场",
    ]:
        if click_if_visible(page, sel, timeout=3000):
            clicked = True
            break

    if not clicked:
        # 兜底：直接跳 URL（仅在首次使用，后续避免刷新）
        page.goto("https://www.autodl.com/market/list", wait_until="domcontentloaded")

    # 不严格等待 networkidle，避免页面轮询导致超时
    page.wait_for_timeout(2500)
    logger.info(f"当前页面: {page.url}")


def set_billing_mode(page: Page) -> None:
    """设置计费方式（Element UI 单选按钮，按量计费 value=payg）。"""
    logger.info(f"设置计费方式: {config.BILLING_MODE}")
    # 计费方式是一组 el-radio-button；按量计费对应 input[value=payg]
    billing_value = {"按量计费": "payg", "包日": "daily", "包周": "weekly", "包月": "monthly"}.get(
        config.BILLING_MODE, "payg"
    )
    clicked = False
    for sel in [
        f".el-radio-button:has(input[value='{billing_value}'])",
        f".el-radio-button:has(.el-radio-button__inner:text-is('{config.BILLING_MODE}'))",
    ]:
        if click_if_visible(page, sel, timeout=3000):
            clicked = True
            break
    if not clicked:
        logger.warning("未找到计费方式按钮")
    wait_humanly(0.8)


def set_gpu_count(page: Page) -> None:
    """设置 GPU 数量（Element UI 单选，文本为数字 1~12）。"""
    logger.info(f"设置 GPU 数量: {config.GPU_COUNT}")
    # GPU数量是一个 el-radio-group，按钮文本就是数字；用 text-is 精确匹配避免 1 命中 10/12
    sel = f".el-radio-button:has(.el-radio-button__inner:text-is('{config.GPU_COUNT}'))"
    if not click_if_visible(page, sel, timeout=3000):
        logger.warning("未找到 GPU 数量按钮")
    wait_humanly(0.8)


def clear_gpu_models(page: Page, keep: str) -> None:
    """取消勾选除 keep 以外的所有 GPU 型号（每轮寻机只保留一个目标型号）。

    AutoDL 的型号复选框勾选多个是并集筛选；不清掉上一轮的勾选，
    会把已无货的旧型号一起选上，导致列表里混入非目标机器。
    """
    labels = page.locator("label.el-checkbox:has(input.el-checkbox__original)").all()
    for label in labels:
        try:
            value = label.locator("input.el-checkbox__original").first.get_attribute("value") or ""
            cls = label.get_attribute("class") or ""
            if "is-checked" in cls and value != keep:
                logger.info(f"取消勾选: {value}")
                label.click(timeout=3000)
                wait_humanly(0.5)
        except Exception:
            continue


def select_gpu_model(page: Page, model: str) -> bool:
    """在当前地区勾选目标 GPU 型号。

    返回 True = 该地区有此型号且已勾选；False = 该地区没有此型号
    （AutoDL 的型号列表按地区动态变化，没有此型号时复选框不存在，
    且不勾选会导致筛选失效、列表回落到该地区全部机器）。

    model 必须是归一化后的规范写法（与 AutoDL 筛选项 value 精确一致）。
    """
    logger.info(f"勾选 GPU 型号: {model}")
    clear_gpu_models(page, keep=model)

    label = page.locator(f"label.el-checkbox:has(input[value='{model}'])").first
    try:
        label.wait_for(state="visible", timeout=4000)
        cls = label.get_attribute("class") or ""
        if "is-checked" in cls:
            logger.info(f"{model} 已勾选")
        else:
            label.click(timeout=3000)
            logger.info(f"已勾选 {model}")
        wait_humanly(1.0)
        return True
    except PlaywrightTimeoutError:
        logger.info(f"当前地区没有型号 {model}，跳过")
        return False


def model_free_count(page: Page, model: str) -> Optional[int]:
    """读取当前地区目标型号的余量 note（如 ' (3/970)' -> 3，即空闲 3 台）。

    返回 None = 当前地区没有这个型号（复选框不存在）。
    用于在勾选前快速跳过零货地区，减少点击次数（也更不易被平台察觉）。
    """
    label = page.locator(f"label.el-checkbox:has(input[value='{model}'])").first
    try:
        label.wait_for(state="attached", timeout=2000)
        note = label.locator(".note").first.inner_text(timeout=1500)
        m = re.search(r"(\d+)\s*/", note.replace("\xa0", " "))
        return int(m.group(1)) if m else 0
    except Exception:
        return None


def _num(text: str, pattern: str) -> int:
    """从文本中按正则抽取数字，抽不到返回 0。"""
    m = re.search(pattern, text.replace("\xa0", " "))
    return int(m.group(1)) if m else 0


def parse_machine_card(card: Locator) -> dict:
    """解析一张 .list-item 机器卡片的规格。

    返回 dict：gpu_name / free / memory_gb / system_disk_gb / data_disk_gb / expandable_gb
    数值解析失败一律记 0（参与排序时自然排后面）。
    """
    spec = {
        "gpu_name": "",
        "free": 0,
        "memory_gb": 0,
        "system_disk_gb": 0,
        "data_disk_gb": 0,
        "expandable_gb": 0,
    }
    try:
        spec["gpu_name"] = card.locator(".machine-info .gpu-name .name").first.inner_text(timeout=1500)
    except Exception:
        pass
    try:
        spec["free"] = int(card.locator(".gpu-num .num-bold").first.inner_text(timeout=1500).strip())
    except Exception:
        pass

    # 规格在“每GPU分配 / 硬盘”两个 config-card 的 li 里：
    #   内存: 110 GB / 系统盘: 30 GB / 数据盘: 50 GB ，可扩容 103 GB
    for li in card.locator(".config-card li").all():
        try:
            txt = li.inner_text(timeout=1500)
        except Exception:
            continue
        if "内存" in txt:
            spec["memory_gb"] = _num(txt, r"(\d+)")
        elif "系统盘" in txt:
            spec["system_disk_gb"] = _num(txt, r"(\d+)")
        elif "数据盘" in txt:
            spec["data_disk_gb"] = _num(txt, r"(\d+)")
            spec["expandable_gb"] = _num(txt, r"可扩容\s*(\d+)")
    return spec


def collect_rentable_machines(page: Page, model: str) -> list:
    """收集当前地区所有可租的目标型号机器（有可见且可用的“可租”按钮）。

    保险校验：卡片规格名（.gpu-name .name 中 '/' 前的部分）必须与 model
    **严格相等**——防止筛选意外失效时把其它型号/物理卡当目标选走。
    注意不能用包含匹配：'vGPU-48GB' 是 'vGPU-48GB-350W' 的前缀。

    排序规则（用户指定）：先按**内存最大**，再按**数据盘可扩容最大**。
    返回 [{spec..., 'rent_btn': Locator}]，已排好序，第一个就是最优。
    """
    machines = []
    for card in page.locator(".list-item").all():
        try:
            gpu_name = card.locator(".machine-info .gpu-name .name").first.inner_text(timeout=1500)
        except Exception:
            continue
        if gpu_name.split("/")[0].strip() != model:
            continue
        rent_btn = card.locator("button.el-button--primary:has-text('可租')").first
        try:
            if not rent_btn.is_visible(timeout=1500):
                continue
            if rent_btn.is_disabled():
                continue
        except Exception:
            continue
        spec = parse_machine_card(card)
        spec["gpu_name"] = gpu_name
        spec["rent_btn"] = rent_btn
        machines.append(spec)

    machines.sort(key=lambda m: (m["memory_gb"], m["expandable_gb"]), reverse=True)
    return machines


def iterate_regions(page: Page, model: str) -> Optional[dict]:
    """对单个型号遍历所有地区，返回可租机器（区内按内存↓ → 可扩容↓择优）。

    关键：AutoDL 的 GPU 型号列表按地区动态变化——切到没有该型号的地区时
    筛选会失效、列表回落到“该地区全部机器”。因此每切换一个地区后都要
    重新勾选目标型号，并用余量 note 预检跳过零货/无型号地区。

    抢机策略：**区内择优、发现即返回**。某个地区一旦有可租机器立即返回，
    不再扫剩余地区——单轮全扫约 45 秒，等扫完机器早被抢光（实测教训
    2026-10-16：西北B区抓到后干等 45 秒才点击，被抢）。排序承诺只对
    同一地区内的多台机器生效；跨地区比较在抢机场景下不值得 45 秒延迟。
    """
    logger.info(f"开始遍历地区寻找 {model} ...")

    # 地区/计费/数量都用 el-radio-button；地区的 __inner 文本以“区”结尾，据此区分
    regions = []
    for r in page.locator(".el-radio-button__inner").all():
        try:
            txt = r.inner_text(timeout=2000).strip()
        except Exception:
            continue
        if txt.endswith("区"):
            regions.append(txt)

    logger.info(f"识别到 {len(regions)} 个地区: {regions}")

    for name in regions:
        logger.info(f"切换地区: {name}")
        sel = f".el-radio-button:has(.el-radio-button__inner:text-is('{name}'))"
        if not click_if_visible(page, sel, timeout=3000):
            logger.warning(f"未能切换到地区: {name}")
            continue

        wait_humanly(1.2)

        # 预检：该地区有没有这个型号、余量是否为 0（不点击，纯读取）
        free_total = model_free_count(page, model)
        if free_total is None:
            logger.info(f"  [{name}] 无型号 {model}")
            continue
        if free_total <= 0:
            logger.info(f"  [{name}] {model} 空闲 0 台，跳过")
            continue

        # 有余量：重新勾选（切地区会清空勾选）并收集
        if not select_gpu_model(page, model):
            continue
        found = collect_rentable_machines(page, model)
        if found:
            top = found[0]
            logger.info(
                f"  [{name}] 可租 {len(found)} 台，区内最优: {top['gpu_name']} "
                f"内存={top['memory_gb']}GB 可扩容={top['expandable_gb']}GB"
            )
            logger.info(
                f"已选定机器（发现即点，区内择优）: {top['gpu_name']} "
                f"内存={top['memory_gb']}GB 系统盘={top['system_disk_gb']}GB "
                f"数据盘={top['data_disk_gb']}GB 可扩容={top['expandable_gb']}GB 空闲={top['free']}"
            )
            return top
        logger.info(f"  [{name}] 有余量但未解析到可租卡片（可能刚被抢完）")

    return None


def find_machine(page: Page) -> Optional[dict]:
    """按型号优先级找机器：前者全地区都有货才租前者，否则退回后者。

    返回最优机器 dict（含 rent_btn），全部型号都无货返回 None。
    """
    for model in config.GPU_MODELS:
        # 不在这里预先勾选：iterate_regions 内部对每个地区都会做
        # 余量预检 + 按需勾选，这里的额外点击只会增加无谓操作次数。
        machine = iterate_regions(page, model)
        if machine:
            return machine
        logger.info(f"型号 {model} 所有地区均无空闲，尝试下一个优先级型号")

    logger.warning("所有型号在所有地区均无空闲机器")
    return None


def configure_image(page: Page) -> None:
    """在创建页选择镜像：基础镜像 -> PyTorch -> 框架版本/Python/CUDA 每级选第一个。

    AutoDL 创建页的镜像是 Element UI 级联选择器(el-cascader)，四级：
    框架名称 -> 框架版本 -> Python版本 -> CUDA版本。
    子面板在点击父级后才动态渲染，因此每级都要“点最后一个可见菜单的第一个节点”。
    """
    logger.info("开始配置镜像...")

    # 1) 确保在“基础镜像” tab（value=basicImage）
    click_if_visible(page, ".el-radio-button:has(input[value='basicImage'])", timeout=3000)
    wait_humanly(0.5)

    # 2) 打开级联选择器（只读输入框，placeholder 含“请选择框架名称/框架版本/...”）
    cascader = page.locator(".el-cascader .el-input__inner").first
    try:
        cascader.wait_for(state="visible", timeout=8000)
        cascader.click(timeout=5000)
    except PlaywrightTimeoutError:
        raise RuntimeError("未找到镜像级联选择器输入框")
    wait_humanly(0.8)

    # 3) 选择框架名称 = PyTorch（config.FRAMEWORK）
    fw = page.locator(f".el-cascader-node:has-text('{config.FRAMEWORK}')").first
    try:
        fw.wait_for(state="visible", timeout=8000)
        fw.click(timeout=5000)
    except PlaywrightTimeoutError:
        raise RuntimeError(f"未找到框架: {config.FRAMEWORK}")
    logger.info(f"已选择框架: {config.FRAMEWORK}")
    wait_humanly(1.0)

    # 4) 框架版本 / Python版本 / CUDA版本：每级选“最后一个可见菜单”的第一个节点
    for level in ["框架版本", "Python版本", "CUDA版本"]:
        target = None
        menus = page.locator(".el-cascader-menu").all()
        for menu in reversed(menus):
            nodes = [
                n for n in menu.locator(".el-cascader-node").all() if n.is_visible(timeout=1000)
            ]
            if nodes:
                target = nodes[0]
                break
        if target is not None:
            try:
                txt = target.inner_text(timeout=2000).strip().replace("\n", " ")
                logger.info(f"{level} 选第一个: {txt}")
                target.click(timeout=5000)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"{level} 点击失败: {e}")
        else:
            logger.warning(f"{level} 未找到可选项（可能已默认选中）")
        wait_humanly(1.0)

    logger.info("镜像配置完成")


def handle_insufficient_gpu_popup(page: Page) -> bool:
    """处理'空闲GPU数量不足'弹窗，若存在则点确定并返回 True。"""
    popup_selectors = [
        ".ant-modal:has-text('空闲GPU数量不足')",
        ".ant-modal:has-text('请更换主机')",
        ".modal:has-text('空闲GPU数量不足')",
        "div[role='dialog']:has-text('空闲GPU数量不足')",
    ]
    for sel in popup_selectors:
        loc = page.locator(sel).first
        try:
            loc.wait_for(state="visible", timeout=3000)
            logger.warning("检测到弹窗：该主机空闲GPU数量不足，请更换主机")
            for ok_sel in [
                "button:has-text('确定')",
                "button:has-text('确认')",
                ".ant-btn-primary:has-text('确定')",
            ]:
                if click_if_visible(page, f"{sel} >> {ok_sel}", timeout=3000):
                    break
            wait_humanly(1.0)
            return True
        except PlaywrightTimeoutError:
            continue
    return False


# ------------------------------ 已有实例盘点与开机复用 ------------------------------

INSTANCE_LIST_URL = "https://www.autodl.com/console/instance/list"

# 占用一个"运行中"名额的状态（除已关机/已释放外都算占用，宁多勿漏）
RELEASED_STATES = ("已关机", "已释放")


def goto_instance_list(page: Page) -> None:
    """打开控制台实例列表页。"""
    logger.info("打开实例列表页...")
    page.goto(INSTANCE_LIST_URL, wait_until="domcontentloaded")
    page.wait_for_timeout(3000)


def parse_instance_rows(page: Page) -> list:
    """解析实例列表的每一行（tr.el-table__row）。

    返回 [{id, status, region, gpu_tips, remaining_min, row}]。
    Element UI 固定列会克隆行导致重复，按实例 ID 去重（保留 DOM 序靠前的主表格行）。
    remaining_min = 距释放的分钟数（已关机行为 'N天N小时N分后释放'；解析不到为 -1）。
    gpu_tips = 状态旁的 .gpuTips 标记文本：AutoDL 在"已关机"实例的主机**有空闲资源**时
    会在这里渲染提示标记（如 GPU 空闲提示）；为空则很可能开不了机（用户实测经验）。
    """
    rows = []
    seen = set()
    for row in page.locator("tr.el-table__row").all():
        try:
            txt = row.inner_text(timeout=1500).replace("\xa0", " ")
        except Exception:
            continue
        flat = txt.replace("\n", " ")
        m = re.search(r"\b([a-z0-9]{8,10}-[a-f0-9]{8})\b", flat)
        if not m:
            continue
        iid = m.group(1)
        if iid in seen:
            continue
        seen.add(iid)

        status = ""
        try:
            status = row.locator(".status span").first.inner_text(timeout=1500).strip()
        except Exception:
            pass

        region = ""
        try:
            region = row.locator(".region").first.inner_text(timeout=1500).replace("\n", " ").strip()
        except Exception:
            pass

        gpu_tips = ""
        try:
            gpu_tips = row.locator(".gpuTips").first.inner_text(timeout=1500).strip()
        except Exception:
            pass

        remaining_min = -1
        rm = re.search(r"(\d+)天(\d+)小时(\d+)分后释放", flat)
        if rm:
            remaining_min = (int(rm.group(1)) * 24 + int(rm.group(2))) * 60 + int(rm.group(3))

        rows.append(
            {
                "id": iid,
                "status": status,
                "region": region,
                "gpu_tips": gpu_tips,
                "remaining_min": remaining_min,
                "row": row,
                "text": flat,
            }
        )
    return rows


def power_on_instance(page: Page, entry: dict) -> bool:
    """点击指定已关机实例的"开机"按钮，等待状态变化。返回是否成功开机。

    主机 GPU 资源不足时开机不会成功（状态一直停在'已关机'），视为失败。
    成功/失败都会把该实例元数据写入 instances_log.json（便于后续指定关机）。
    """
    tips = f"，资源标记: {entry['gpu_tips']}" if entry.get("gpu_tips") else "（无资源空闲标记）"
    logger.info(f"尝试开机复用已有实例: {entry['region']} (ID {entry['id']}){tips}")

    meta = {
        "id": entry["id"],
        "region": entry["region"],
        "gpu_tips": entry.get("gpu_tips", ""),
        "remaining_min": entry.get("remaining_min", -1),
        "spec": next(
            (s for s in entry["text"].split(" ") if "卡" in s or "vGPU" in s or "GPU" in s),
            "",
        ),
    }

    btn = entry["row"].locator("button:has-text('开机')").first
    try:
        btn.wait_for(state="visible", timeout=4000)
        btn.click(timeout=5000)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"  开机按钮点击失败: {e}")
        record_event("power_on_failed", {**meta, "reason": f"点击失败: {e}"})
        return False

    # 若出现确认弹窗（文案含'开机'），点其中的确定
    try:
        dlg = page.locator(
            ".el-dialog:has-text('开机'), .el-message-box:has-text('开机')"
        ).first
        dlg.wait_for(state="visible", timeout=3000)
        dlg.locator("button:has-text('确定'), button:has-text('确认')").first.click(timeout=3000)
        logger.info("  已确认开机弹窗")
    except PlaywrightTimeoutError:
        pass

    # 轮询该实例状态：开机成功会变成 开机中/运行中
    for _ in range(10):
        page.wait_for_timeout(2000)
        current = {r["id"]: r for r in parse_instance_rows(page)}.get(entry["id"])
        if current and current["status"] not in RELEASED_STATES:
            logger.info(f"  开机成功，当前状态: {current['status']}")
            record_event(
                "power_on", {**meta, "status": current["status"], "by": "bot"}
            )
            return True
    logger.warning("  开机后状态未变化（可能该主机 GPU 资源不足），视为失败")
    record_event("power_on_failed", {**meta, "reason": "状态未变化，主机可能资源不足"})
    return False


def manage_instances(page: Page) -> tuple:
    """盘点已有实例，按需开机复用。

    返回 (状态, 本轮看到的实例 ID 集合)：
    - 'powered'  : 已开机复用补满名额，无需再租
    - 'at_limit' : 运行中已达上限，不做任何操作
    - 'need_rent': 没有可复用的关机实例（或全部开机失败），需要去市场租新机
    seen_ids 供租新后对比找出新实例 ID 用。
    """
    if config.MAX_RUNNING_INSTANCES <= 0:
        # 上限未启用：保持旧行为，直接租新
        goto_instance_list(page)
        seen_ids = {r["id"] for r in parse_instance_rows(page)}
        return "need_rent", seen_ids

    if not config.REUSE_INSTANCES:
        # 用户要求忽略实例库：只记录当前实例 ID（供租新后对比），不做任何开机操作
        logger.info("已配置 AUTODL_REUSE_INSTANCES=false，跳过实例盘点/开机复用，直接租新")
        goto_instance_list(page)
        seen_ids = {r["id"] for r in parse_instance_rows(page)}
        return "need_rent", seen_ids

    goto_instance_list(page)
    rows = parse_instance_rows(page)
    seen_ids = {r["id"] for r in rows}
    running = [r for r in rows if r["status"] and r["status"] not in RELEASED_STATES]
    shutdown = [r for r in rows if r["status"] == "已关机"]

    logger.info(
        f"已有实例 {len(rows)} 台：运行中/占用 {len(running)}，已关机 {len(shutdown)}"
    )
    for r in running:
        logger.info(f"  [运行中] {r['region']} {r['id']} ({r['status']})")
    for r in shutdown:
        rem = f"，距释放 {r['remaining_min']} 分钟" if r["remaining_min"] >= 0 else ""
        tips = f"，资源标记: {r['gpu_tips']}" if r["gpu_tips"] else "，无资源空闲标记"
        logger.info(f"  [已关机] {r['region']} {r['id']}{rem}{tips}")

    if len(running) >= config.MAX_RUNNING_INSTANCES:
        logger.info(
            f"运行中实例已达上限 {config.MAX_RUNNING_INSTANCES}，不再开机/租新"
        )
        return "at_limit", seen_ids

    slots = config.MAX_RUNNING_INSTANCES - len(running)
    logger.info(f"还可开机/租新 {slots} 台")

    if not config.AUTO_POWER_ON:
        return "need_rent", seen_ids

    # 开机候选顺序：带"资源空闲"标记(.gpuTips 非空)的关机实例优先（大概率开得了），
    # 同组内按距释放最久排序（最不容易中途被释放）；无标记的排最后兜底尝试——
    # 标记缺失只是"可能"开不了机，多试一次的成本远低于误租一台新机。
    marked = [r for r in shutdown if r["gpu_tips"]]
    unmarked = [r for r in shutdown if not r["gpu_tips"]]
    candidates = sorted(marked, key=lambda r: r["remaining_min"], reverse=True) + sorted(
        unmarked, key=lambda r: r["remaining_min"], reverse=True
    )
    if marked:
        logger.info(f"{len(marked)} 台关机实例带资源空闲标记，优先尝试开机")
    if unmarked:
        logger.info(f"{len(unmarked)} 台关机实例无标记（可能开不了机），排后兜底")

    powered = 0
    for entry in candidates:
        if powered >= slots:
            break
        if power_on_instance(page, entry):
            powered += 1
        # 失败（如主机资源不足）则自动试下一台

    if powered >= slots:
        logger.info(f"已通过开机复用 {powered} 台，满足名额要求，无需租新")
        return "powered", seen_ids

    if powered > 0:
        # 开机补上了一部分但没补满：剩余名额继续去算力市场租新
        logger.info(f"已开机复用 {powered} 台，还差 {slots - powered} 台，将去算力市场租新")
    elif shutdown:
        logger.info("已关机实例全部开机失败（资源不足），将尝试租新机")
    else:
        logger.info("没有已关机实例可复用，将尝试租新机")
    return "need_rent", seen_ids


def create_machine(page: Page) -> str:
    """
    在创建页点击"创建并开机"。
    返回状态: 'created' | 'insufficient' | 'dry_run' | 'aborted'
    'aborted' 表示被人工确认闸门中止，未真正下单。
    """
    configure_image(page)

    if config.DRY_RUN:
        logger.info("【DRY_RUN】已跑到'创建并开机'前一步，不会真的点击创建按钮")
        logger.info("如需真实下单，请将 .env 中的 AUTODL_DRY_RUN 改为 false")
        return "dry_run"

    # 安全闸门：真正计费/下单前必须人工确认，防止机器人擅自扣费或触发风控封号
    if config.REQUIRE_HUMAN_CONFIRM:
        if not human_confirm(page, "即将点击【创建并开机】，这将真正下单并计费"):
            logger.warning("人工中止：未创建实例，机器人退出")
            return "aborted"

    logger.info("点击【创建并开机】...")
    create_clicked = False
    for sel in [
        "button:has-text('创建并开机')",
        "button:has-text('创建并启动')",
        "button:has-text('创建')",
    ]:
        if click_if_visible(page, sel, timeout=5000):
            create_clicked = True
            break

    if not create_clicked:
        # 创建按钮点不了（机器被抢/页面异常）：按"机器没了"处理，回市场继续抢
        logger.warning("未能点击创建按钮（机器可能刚被抢走），回算力市场继续")
        return "insufficient"

    wait_humanly(3.0)

    if handle_insufficient_gpu_popup(page):
        return "insufficient"

    # 若未弹窗，简单判断页面是否跳转或出现成功提示
    if "/console" in page.url or "创建成功" in page.content():
        logger.info("机器创建成功")
        return "created"

    # 兜底：再检查一次弹窗
    if handle_insufficient_gpu_popup(page):
        return "insufficient"

    return "created"


# ------------------------------ 主流程 ------------------------------

# 真实 Chrome UA：Playwright 默认 UA 带 headless/chromium 标记，易被风控识别。
# 使用与本机 Chrome 版本一致的最新 Windows UA。
REAL_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)

# 反自动化检测：在页面脚本执行前注入，覆盖 webdriver / CDP 指纹特征。
# 否则极验滑块会识别到 Playwright 而判定验证失败（即便手动拖动也失败）。
STEALTH_SCRIPT = """
() => {
    // 1) 覆盖 navigator.webdriver
    Object.defineProperty(navigator, 'webdriver', { get: () => false });
    // 2) 清除 CDP 注入的 cdc_ 前缀属性
    const w = window;
    const cdcProps = ['cdc_adoQpoasnfa76pfcZLmcfl_Array',
                      'cdc_adoQpoasnfa76pfcZLmcfl_Promise',
                      'cdc_adoQpoasnfa76pfcZLmcfl_Symbol'];
    for (const p of cdcProps) { try { delete w[p]; } catch (e) {} }
    // 3) 补齐典型真实浏览器特征
    if (!w.chrome) { w.chrome = { runtime: {} }; }
    Object.defineProperty(navigator, 'plugins', { get: () => [1,2,3,4,5] });
    Object.defineProperty(navigator, 'languages', { get: () => ['zh-CN','zh','en'] });
    // 4) 抹掉 "Headless" 字样相关特征
    Object.defineProperty(navigator, 'userAgent', {
        get: () => navigator.userAgent.replace(/HeadlessChrome/i, 'Chrome')
    });
}
"""


def rent_loop(page) -> None:
    """主循环：先盘点已有实例（开机复用优先），不够才去市场租新。

    每轮：manage_instances() 盘点 /console/instance/list ——
      - 运行中已达上限 -> 结束，什么都不做；
      - 成功开机复用   -> 结束，无需租新；
      - 需要租新       -> 去算力市场按型号优先级寻机。
    租到的机器点击'创建并开机'后即成为运行中实例。
    """
    attempt = 0
    while True:
        attempt += 1
        logger.info(f"===== 第 {attempt} 轮 =====")

        # 第一步：盘点已有实例，能开机就开机
        inv, seen_ids = manage_instances(page)
        if inv in ("powered", "at_limit"):
            return

        # need_rent -> 去算力市场
        goto_market(page)

        logger.info("开始寻机...")
        set_billing_mode(page)
        set_gpu_count(page)
        machine = find_machine(page)

        if machine is None:
            if config.MAX_RETRIES > 0 and attempt >= config.MAX_RETRIES:
                logger.warning("达到最大重试次数，仍未找到空闲机器")
                return
            logger.info("未找到空闲机器，稍后重试（不刷新页面，间隔含随机抖动防检测）")
            wait_with_jitter(config.RETRY_INTERVAL_SECONDS)
            continue

        # 点击出租按钮，进入创建页。
        # 竞态：从扫描到点击的间隙机器可能被抢走，按钮元素失效导致 click 超时——
        # 属于正常现象，捕获后继续下一轮轮询，绝不能让整个进程崩掉。
        logger.info(
            f"点击出租按钮，进入创建页...（{machine['gpu_name']} 内存={machine['memory_gb']}GB "
            f"可扩容={machine['expandable_gb']}GB）"
        )
        try:
            machine["rent_btn"].click(timeout=5000)
        except PlaywrightTimeoutError:
            logger.warning("机器刚被抢走（按钮已失效），继续轮询下一轮")
            wait_with_jitter(3)  # 短暂等待即可，尽快回到扫描
            continue
        page.wait_for_timeout(2500)
        wait_humanly(2.0)

        status = create_machine(page)

        if status == "created":
            # 同步新实例元数据：对比本轮盘点时的 ID，新出现的即本次租到的
            try:
                goto_instance_list(page)
                for r in parse_instance_rows(page):
                    if r["id"] not in seen_ids:
                        record_event(
                            "rent_created",
                            {
                                "id": r["id"],
                                "region": r["region"],
                                "status": r["status"],
                                "by": "bot",
                                "summary": r["text"][:120],
                            },
                        )
            except Exception as e:  # noqa: BLE001
                logger.warning(f"记录新实例元数据失败（不影响租机结果）: {e}")
            logger.info("租机完成")
            return
        elif status == "dry_run":
            logger.info("DRY_RUN 模式结束")
            return
        elif status == "aborted":
            logger.warning("已被人工中止，结束流程")
            return
        elif status == "insufficient":
            # 机器已被抢：回到循环开头，先重新盘点已有实例再继续
            logger.info("机器已被抢，下一轮先重新盘点已有实例...")
            continue
        else:
            raise RuntimeError(f"未知的创建状态: {status}")


def run() -> None:
    config.validate()

    logger.info("=" * 50)
    logger.info("启动 AutoDL 自动租机机器人")
    logger.info(f"目标 GPU（按优先级）: {' > '.join(config.GPU_MODELS)} x {config.GPU_COUNT}")
    logger.info(f"运行中实例上限: {config.MAX_RUNNING_INSTANCES}（0=不管控，直接租新）")
    logger.info(f"开机复用优先: {config.AUTO_POWER_ON}")
    logger.info(f"计费方式: {config.BILLING_MODE}")
    logger.info(f"框架: {config.FRAMEWORK}")
    logger.info(f"headless: {config.HEADLESS}")
    logger.info(f"dry_run: {config.DRY_RUN}")
    logger.info("=" * 50)

    with sync_playwright() as p:
        launch_args = [
            "--window-size=1920,1080",
            # 关闭 AutomationControlled 特征（极验滑块据此判定为机器人）
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars",
            "--no-sandbox",
        ]
        # 去除默认 --enable-automation 开关，避免被识别为自动化
        ignore_args = ["--enable-automation"]

        page = None
        context = None
        browser = None
        try:
            if config.USER_DATA_DIR:
                # 零登录方案：把本机已登录的真实 Chrome profile 复制到非默认路径后使用。
                # 直接用默认 profile 会被 Chrome 拒绝远程调试，且会被运行中的 Chrome 锁定；
                # 副本包含 AutoDL 登录态，机器人可零登录，且与真实 Chrome 互不干扰。
                # 注意：launch_persistent_context 是 BrowserType 的方法，直接返回 context，
                # 不能先调用 launch()，否则会 AttributeError。
                profile_dir = ensure_profile_copy()
                if not profile_dir:
                    logger.warning("未找到可用 profile 副本，回退到全新浏览器 + 手动登录")
                else:
                    logger.info(f"使用 profile 副本（零登录）: {profile_dir}")
                    context = p.chromium.launch_persistent_context(
                        user_data_dir=profile_dir,
                        channel="chrome",  # 使用本机真实 Chrome，profile 与登录态才能完全匹配
                        headless=config.HEADLESS,
                        slow_mo=150,  # 操作间隔，降低机器特征
                        timeout=20000,  # 锁定等情况下快速失败
                        viewport={"width": 1920, "height": 1080},
                        locale="zh-CN",
                        user_agent=REAL_UA,
                        args=launch_args,
                        ignore_default_args=ignore_args,
                    )
                    context.add_init_script(STEALTH_SCRIPT)
                    page = context.new_page()
                    rent_loop(page)
            else:
                browser = p.chromium.launch(
                    headless=config.HEADLESS,
                    slow_mo=150,  # 操作间隔，降低机器特征
                    args=launch_args,
                    ignore_default_args=ignore_args,
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
                if storage and is_logged_in(page):
                    pass
                else:
                    login(page)
                    save_storage_state(context)
                rent_loop(page)

        except Exception:
            logger.exception("运行失败")
            if page is not None and not config.HEADLESS:
                logger.info("30 秒后关闭浏览器...")
                page.wait_for_timeout(30000)
            raise
        finally:
            # 正常结束或失败时都保留一会儿，便于查看/排查
            if page is not None and not config.HEADLESS and config.DRY_RUN:
                logger.info("浏览器将保持 30 秒...")
                page.wait_for_timeout(30000)
            try:
                if context is not None:
                    context.close()
            except Exception:
                pass
            try:
                if browser is not None:
                    browser.close()
            except Exception:
                pass


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        logger.info("用户中断")
        sys.exit(0)
