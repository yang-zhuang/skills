"""AutoDL Art 零登录链路的公共配置。

所有路径都从本文件位置推导，不写本机绝对路径。
敏感值一律走环境变量或包根 .env（已被 .gitignore 排除）：
    AUTODL_DEV_TOKEN      开发者 token（dev_api 系列脚本）
    AUTODL_ART_WEB_JWT    网页登录 JWT（extract_jwt.py 自动写入，web_api / grab --signal 用）
    AUTODL_ART_LLM_KEY    大模型代理的 key（默认 "any"，本地代理不校验）
"""

import os
from pathlib import Path

# AutoDL Art 公开接口（2026-11-24 实测匿名可访问）
BASE_URL = "https://www.autodl.art"
API_APPLICATION = "/api/v1/application"
API_APPLICATION_DETAIL = "/api/v1/application/square/detail"
API_TAG_TREE = "/api/v1/unified_tag/tree"
API_WEEK = "/api/v1/application/runtime/week"

# 本地大模型代理（agnes-key-pool，OpenAI 兼容接口）
LLM_BASE_URL = os.environ.get("AUTODL_ART_LLM_BASE_URL", "http://127.0.0.1:8765/v1")
LLM_MODEL = os.environ.get("AUTODL_ART_LLM_MODEL", "agnes-3.0-flash")
LLM_API_KEY = os.environ.get("AUTODL_ART_LLM_KEY", "any")
LLM_TIMEOUT = 180

# 陈旧应用闸门：应用级 updated_at 超过这个天数就不再进入大模型候选
STALE_DAYS = int(os.environ.get("AUTODL_ART_STALE_DAYS", "180"))
AGING_DAYS = int(os.environ.get("AUTODL_ART_AGING_DAYS", "90"))

# 抓取礼仪：不是登录态，但也不高频打接口
REQUEST_TIMEOUT = 30
MIN_DELAY = 1.0
MAX_DELAY = 2.5
MAX_RETRY = 2

# 产物目录（不入库）
PACKAGE_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PACKAGE_ROOT / "outputs"


def _load_dotenv():
    """从包根 .env 读 KEY=VALUE 到环境变量；已存在的环境变量优先。不依赖第三方库。"""
    env_path = PACKAGE_ROOT / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_dotenv()

# 开发者 API（需要登录态的 token；每步都要用户授权，脚本不会自己登录）
DEV_BASE_URL = "https://www.autodl.art"
DEV_TOKEN = os.environ.get("AUTODL_DEV_TOKEN", "").strip()
API_DEV_INSTANCE = "/api/v1/adl_dev/dev/instance/pro"

# 网页登录态（浏览器 localStorage 的 "token" 键，JWT 格式）。
# 用于网页体系的接口（如 machine_pro/gpu_type 库存查询）——这套接口不吃开发者 token。
# 由 extract_jwt.py 自动提取（Playwright 专用 profile，登录一次后续全自动），存进包根 .env。
WEB_JWT = os.environ.get("AUTODL_ART_WEB_JWT", "").strip()
API_WEB_GPU_TYPE = "/api/v1/adl_server/machine_pro/gpu_type"

# 浏览器专用 profile（Playwright persistent context；登录 art 一次后长期复用）
BROWSER_PROFILE_DIR = PACKAGE_ROOT / ".browser_profile"

# 官方文档给出的算力规格 ID（文档硬编码，没有查询库存/价格的公开端点）
GPU_SPECS = [
    ("h800", "H800-80G", "通用型"),
    ("v-48g", "4090-48G", "通用型"),
    ("pro6000-p", "PRO6000-96G", "性能型"),
    ("v-32g-p", "4080(S)-32G", "性能型"),
    ("v-48g-350w", "3090-48G", "通用型"),
    ("5090-p", "5090-32G", "性能型"),
]


def ensure_output_dir():
    """确保产物目录存在并返回它。"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUTPUT_DIR
