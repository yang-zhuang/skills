"""_config.py —— 路径配置唯一来源（所有脚本共用，脚本里不要再写死路径）。

优先级：系统环境变量 > 就近 .env > 默认值。
.env 格式：KEY=VALUE，# 开头是注释，值两端的引号会被剥掉。零依赖，只用标准库。
模板见 .env.example；复制为 .env 并按本机情况修改（.env 不入库）。

兼容两种目录布局（脚本位置永远在 <包>/scripts/ 下）：
  A 原项目：<root>/skills/translate-zh/scripts  -> 工作根 = <root>（data/ outputs/ 在项目根）
  B skills 仓库：<repo>/translate-zh/scripts    -> 工作根 = 包自己（data/ outputs/ 在包内）
判别不能靠"父目录名是不是 skills" —— clone 出来的仓库本身就叫 skills，会把 B 误判成 A，
把工作根推到磁盘根。改用 data/ 实际落在哪一层：包内自带 data/ 优先（就近），
否则找项目根 data/，都没有就以包自己为工作根（下一步 bundle 会明确报缺文件）。
"""

import os

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_ROOT = os.path.normpath(os.path.join(SCRIPT_DIR, ".."))                  # translate-zh 包根
ROOT_A = os.path.normpath(os.path.join(SKILL_ROOT, "..", ".."))               # 布局 A 候选：项目根
if os.path.isdir(os.path.join(SKILL_ROOT, "data")):
    PROJECT_ROOT = SKILL_ROOT       # 布局 B：包内自带 data/
elif os.path.isdir(os.path.join(ROOT_A, "data")):
    PROJECT_ROOT = ROOT_A           # 布局 A：项目根 data/
else:
    PROJECT_ROOT = SKILL_ROOT       # 兜底：以包为工作根，bundle 会明确报缺术语表


def _read_dotenv():
    """就近优先：先找包内 .env，再找工作根 .env；都不存在返回空 dict。"""
    for path in (os.path.join(SKILL_ROOT, ".env"), os.path.join(PROJECT_ROOT, ".env")):
        if not os.path.exists(path):
            continue
        out = {}
        for line in open(path, "r", encoding="utf-8-sig"):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            out[key.strip()] = value.strip().strip("'\"")
        return out
    return {}


_DOTENV = _read_dotenv()


def get(key, default):
    """取配置：系统环境变量 > .env > 默认值。"""
    return os.environ.get(key) or _DOTENV.get(key) or default


# ---- 可配置项（.env.example 里有逐条说明）----

# ACL Anthology 论文落盘根（fetch.py 下载 + 查重）
ACL_ROOT = os.path.normpath(get("ACL_ROOT", os.path.join(PROJECT_ROOT, "corpus", "acl-anthology", "papers")))

# arXiv 论文落盘根
ARXIV_ROOT = os.path.normpath(get("ARXIV_ROOT", os.path.join(PROJECT_ROOT, "corpus", "arxiv")))

# 博客爬虫仓库根（lilianweng-posts / langchain-blog / anthropic-research 三个子目录）。
# 缺失时 fetch.py 自动降级为通用抓取，落盘到 outputs/articles/。
CRAWLERS_ROOT = os.path.normpath(get("CRAWLERS_ROOT",
                                     os.path.join(os.path.dirname(PROJECT_ROOT), "blog-research-crawlers")))

# acl-anthology-downloader 仓库根（与爬虫同为兄弟仓库）。缺失时降级为直接下 PDF。
ACL_DOWNLOADER = os.path.normpath(get("ACL_DOWNLOADER",
                                      os.path.join(os.path.dirname(PROJECT_ROOT), "acl-anthology-downloader")))

# pdf-ocr-toolkit 仓库根（扫描件 OCR 用，可选增强，缺失不碍 born-digital 路径）
OCR_TOOLKIT = os.path.normpath(get("OCR_TOOLKIT",
                                   os.path.join(os.path.dirname(PROJECT_ROOT), "pdf-ocr-toolkit")))
