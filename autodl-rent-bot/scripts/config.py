"""从环境变量读取配置，支持 .env 文件。"""
import os
import re
from pathlib import Path

from dotenv import load_dotenv

# 加载项目根目录下的 .env（如果存在）
load_dotenv(Path(__file__).with_name(".env"))


USERNAME = os.getenv("AUTODL_USERNAME", "").strip()
PASSWORD = os.getenv("AUTODL_PASSWORD", "").strip()

# GPU 型号优先级：逗号分隔，越靠前优先级越高。
# 例：VGPU-48g-350w,VGPU-32G 表示先找 48G-350W，全地区都没有才找 32G。
# 注意 vGPU-48GB-350W 与 vGPU-48GB 是 AutoDL 上两个独立型号，绝不混为一谈。
GPU_MODEL = os.getenv("AUTODL_GPU_MODEL", "vGPU-32GB").strip()


def normalize_model(name: str) -> str:
    """把用户写法归一成 AutoDL 筛选项的规范形式。

    AutoDL 真实筛选项形如：vGPU-32GB / vGPU-48GB / vGPU-48GB-350W
    用户可能写：VGPU-48g-350w / VGPU-32G / vgpu-32gb

    规则（按 '-' 分段，逐段归一）：
    - vgpu/VGPU -> vGPU
    - 48g/48G/48gb -> 48GB；32g -> 32GB；350w/350W -> 350W
    - 其余段原样保留（仅去空白）

    安全约束：**只做整体精确匹配**，绝不做前缀/包含匹配——
    "vGPU-48GB" 是 "vGPU-48GB-350W" 的前缀，包含匹配会把 48GB 误判成 350W。
    """
    n = name.strip()
    if not n:
        return ""
    unit_map = {"gb": "GB", "g": "GB", "tb": "TB", "t": "TB", "w": "W", "mb": "MB", "m": "MB"}
    segs = []
    for seg in n.split("-"):
        s = seg.strip()
        if s.lower() == "vgpu":
            segs.append("vGPU")
            continue
        m = re.fullmatch(r"(\d+)\s*([a-zA-Z]+)?", s)
        if m:
            num, unit = m.group(1), (m.group(2) or "").lower()
            segs.append(num + unit_map.get(unit, unit.upper() if unit else ""))
        else:
            segs.append(s)
    return "-".join(segs)


# 归一化后的型号优先级列表（去重、保序）
GPU_MODELS = []
for _m in GPU_MODEL.split(","):
    _n = normalize_model(_m)
    if _n and _n not in GPU_MODELS:
        GPU_MODELS.append(_n)

GPU_COUNT = int(os.getenv("AUTODL_GPU_COUNT", "1").strip() or "1")
BILLING_MODE = os.getenv("AUTODL_BILLING_MODE", "按量计费").strip()
FRAMEWORK = os.getenv("AUTODL_FRAMEWORK", "PyTorch").strip()

# 同时处于开机/运行状态的实例上限（含已有运行中 + 新开机 + 新租用）。
# 出租新机/开机前先盘点 /console/instance/list：
#   运行中已达上限 -> 什么都不做；
#   未达上限且有已关机实例 -> 优先点"开机"复用（资源不足会自动试下一台）；
#   未达上限且无已关机实例 -> 才去算力市场租新机。
# 0 = 不管控上限（保持旧行为：直接租新）。
MAX_RUNNING_INSTANCES = int(
    os.getenv("AUTODL_MAX_RUNNING_INSTANCES", "2").strip() or "2"
)

# 发现已关机实例时是否自动点"开机"复用（true=开机优先于租新）
AUTO_POWER_ON = os.getenv("AUTODL_AUTO_POWER_ON", "true").strip().lower() in (
    "1",
    "true",
    "yes",
)

# 是否启用"实例盘点/开机复用"流程（false = 完全忽略已有实例，直接去算力市场租全新机器）。
# 典型用途：想强制再开一台全新 vGPU-48GB-350W，不想动已有实例时设为 false。
# 注意：即使 false，运行中实例上限 MAX_RUNNING_INSTANCES 仍然生效（达上限就不租）。
REUSE_INSTANCES = os.getenv("AUTODL_REUSE_INSTANCES", "true").strip().lower() in (
    "1",
    "true",
    "yes",
)

HEADLESS = os.getenv("AUTODL_HEADLESS", "false").strip().lower() in ("1", "true", "yes")
DRY_RUN = os.getenv("AUTODL_DRY_RUN", "true").strip().lower() in ("1", "true", "yes")

RETRY_INTERVAL_SECONDS = int(os.getenv("AUTODL_RETRY_INTERVAL_SECONDS", "15").strip() or "15")
MAX_RETRIES = int(os.getenv("AUTODL_MAX_RETRIES", "0").strip() or "0")

# 安全闸门：真正下单/计费（点击“创建并开机”）前必须人工确认。
# 强烈建议保持 true —— 自动点下去既花钱又容易触发平台风控封号。
REQUIRE_HUMAN_CONFIRM = os.getenv("AUTODL_REQUIRE_HUMAN_CONFIRM", "true").strip().lower() in (
    "1",
    "true",
    "yes",
)

# 登录态持久化：登录成功后保存 cookies 到该文件，下次自动复用，避免每次验证
COOKIE_FILE = os.getenv("AUTODL_COOKIE_FILE", "autodl_cookies.json").strip()

# 零登录方案（推荐，风控最低）：直接复用本机已登录 AutoDL 的 Chrome 用户数据目录。
# 例如：C:\Users\<你的用户名>\AppData\Local\Google\Chrome\User Data
# 启用后脚本启动即为已登录状态，完全不触发验证码/登录风控。
# 注意：运行时需先关闭正在占用该 profile 的 Chrome 实例（profile 会被锁定）。
USER_DATA_DIR = os.getenv("AUTODL_USER_DATA_DIR", "").strip()


def validate() -> None:
    """启动前校验必填项。"""
    missing = []
    if not USERNAME:
        missing.append("AUTODL_USERNAME")
    if not PASSWORD:
        missing.append("AUTODL_PASSWORD")
    if missing:
        raise RuntimeError(f"缺少环境变量: {', '.join(missing)}，请参照 .env.example 创建 .env 文件")
