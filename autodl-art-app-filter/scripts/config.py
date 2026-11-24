"""AutoDL Art 应用筛选 skill 的公共配置（纯筛选，无部署/开机/登录相关配置）。"""

import os
from pathlib import Path

# AutoDL Art 公开接口（2026-11-24 实测匿名可访问，证据见 references/anonymous-api.md）
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

# 两阶段筛选：阶段1 只用列表字段粗选出 COARSE_TOP_N 个，阶段2 对入选者用 README 全文深判
COARSE_TOP_N = int(os.environ.get("AUTODL_ART_COARSE_TOP_N", "30"))
DEEP_README_LIMIT = int(os.environ.get("AUTODL_ART_DEEP_README_LIMIT", "5000"))

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


def ensure_output_dir():
    """确保产物目录存在并返回它。"""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUTPUT_DIR
