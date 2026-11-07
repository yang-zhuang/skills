#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch.py —— 从用户消息里提取 URL，查重，委托既有爬虫下载到约定目录。

原则：全链确定性。URL 提取是正则，分类是映射表，查重是文件存在性检查，
下载是 import 既有爬虫的函数 —— 没有任何一步需要 LLM。
（所以也不需要"下载 agent"：能被正则+映射表解决的事，上 agent 就是浪费。）

来源与落盘（复用既有代码与既有目录结构，不新建约定）：

  来源             URL 特征                        落盘位置                                    复用
  ---------------  ------------------------------  ------------------------------------------  -------------------------
  lilianweng 博客  lilianweng.github.io/posts/     CRAWLERS_ROOT/lilianweng-posts              crawl_lilianweng_posts.fetch_article
  LangChain 博客   blog.langchain.com / …/blog/    CRAWLERS_ROOT/langchain-blog                crawl_langchain_blog.fetch_article
  Anthropic 研究   www.anthropic.com/research/…    CRAWLERS_ROOT/anthropic-research            crawl_anthropic_research.fetch_article
  ACL Anthology    aclanthology.org/<ID>           ACL_ROOT/<VENUE>/<年>/<ID>/raw/             acl-anthology-downloader.downloader.download_paper
  arXiv            arxiv.org/abs|pdf/<ID>          ARXIV_ROOT/<年>/<ID>/raw/                   本文件直接下载（结构对齐 ACL）

  目录根 ACL_ROOT / ARXIV_ROOT / CRAWLERS_ROOT 见 _config.py（系统环境变量 > 项目根 .env > 默认值，
  模板见项目根 .env.example）。爬虫 / downloader 不在本机时自动降级：
  博客 -> 通用抓取存原始 HTML 到 outputs/articles/<site>/；ACL -> 直接下 PDF（同 arXiv 路径）。

  ACL/arXiv 均为 <paper_id>/raw/paper.pdf + metadata.json。
  PDF→md：born-digital 先跑 skills/translate-zh/scripts/pdf2md.py；扫描件才需要
  OCR（pdf-ocr-toolkit/ocr_pdfs.py --input <raw 目录>，本机有该仓库时 fetch 会提示命令）。

三个子命令：
  plan  <消息文件|->     提取 URL -> 分类 -> 查重，打印计划并写 outputs/fetch-plan.json
  fetch [plan.json]      执行计划中"待下载"项（缺省用 outputs/fetch-plan.json）
  test                   离线自检（不联网）：提取/分类/查重/ID 解析/目录推导

用法（口语化消息直接丢进来，多个 URL 混排也行）：
  echo "翻译这两篇 https://arxiv.org/abs/2609.24972 和 https://aclanthology.org/2024.acl-long.123/" > msg.txt
  python fetch.py plan msg.txt
  python fetch.py fetch
"""

import argparse
import importlib.util
import json
import os
import re
import sys
import time

import requests
from bs4 import BeautifulSoup

import _config   # 路径配置唯一来源（同目录）：ACL_ROOT / ARXIV_ROOT / CRAWLERS_ROOT 可被项目根 .env 覆盖

CRAWLERS = _config.CRAWLERS_ROOT
ACL_ROOT = _config.ACL_ROOT
ARXIV_ROOT = _config.ARXIV_ROOT
DEFAULT_PLAN = os.path.join(_config.PROJECT_ROOT, "outputs", "fetch-plan.json")

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")

# ---------------------------------------------------------------------------
# URL 提取与分类。顺序即优先级；正则第 1 个捕获组统一是"文章标识"（slug / ID）。
# ---------------------------------------------------------------------------
URL_RE = re.compile(r"https?://[^\s，。；、！？)」』\"'<>【】\]]+", re.I)

SITE_TABLE = [
    ("blog-lilianweng", re.compile(r"^https?://lilianweng\.github\.io/posts/([^/?#]+)", re.I),
     os.path.join(CRAWLERS, "lilianweng-posts"), "crawl_lilianweng_posts.py",
     "https://lilianweng.github.io/posts/{key}/"),
    ("blog-langchain", re.compile(r"^https?://(?:blog\.langchain\.com|(?:www\.)?langchain\.com/blog)/([^/?#]+)", re.I),
     os.path.join(CRAWLERS, "langchain-blog"), "crawl_langchain_blog.py",
     None),  # blog.langchain.com 和 www.langchain.com/blog 两种域名形态都识别
    ("blog-anthropic", re.compile(r"^https?://www\.anthropic\.com/(?:research|engineering|news)/([^/?#]+)", re.I),
     os.path.join(CRAWLERS, "anthropic-research"), "crawl_anthropic_research.py",
     None),
    ("acl", re.compile(r"^https?://aclanthology\.org/([A-Za-z0-9][A-Za-z0-9.\-]*?)/?$", re.I),
     None, None, None),
    ("arxiv", re.compile(r"^https?://arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})(?:v\d+)?(?:[/?#].*)?$", re.I),
     None, None, None),
]


def classify(url):
    """返回 (site, key)；不认识的 URL 返回 (None, None)。"""
    for site, rx, *_ in SITE_TABLE:
        m = rx.match(url.strip())
        if m:
            return site, m.group(1).rstrip(".")
    return None, None


def norm(p):
    """路径统一成正斜杠（os.path.join 在 Windows 会混入反斜杠）。"""
    return p.replace("\\", "/")


def acl_parse(paper_id):
    """ACL ID -> (venue, year, pdf_url)。旧式 ID（A00-1001，无点分）不支持，返回 None。

    venue 规则（与既有库 papers/ACL/2026/... 保持一致）：
      2024.acl-long.123      -> ACL          （会议主名，去掉 -long 等 track 后缀）
      2020.emnlp-main.45     -> EMNLP
      2020.findings-emnlp.9  -> FINDINGS-EMNLP（findings 是独立合集，单独立目录）
      2022.tacl-1.5          -> TACL
    """
    m = re.match(r"^(\d{4})\.([a-z0-9\-]+)\.", paper_id + ".")
    if not m:
        return None
    token = m.group(2)
    venue = token.upper() if token.startswith("findings-") else token.split("-")[0].upper()
    return venue, m.group(1), "https://aclanthology.org/%s.pdf" % paper_id


def arxiv_year(paper_id):
    """arXiv ID YYMM.NNNNN -> 20YY。"""
    return "20" + paper_id[:2]


def page_title(url):
    """抓页面 <title>（博客 meta / ACL / arXiv 元数据都要用）。"""
    r = requests.get(url, headers={"User-Agent": UA}, timeout=60)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    t = soup.title.string if soup.title else ""
    return t.strip(), r.text


# --------------------------------------------------------------------------- plan

def build_plan(message):
    """消息文本 -> 计划条目列表（去重、查重、目标路径推导）。"""
    items, seen = [], set()
    for raw in URL_RE.findall(message):
        url = raw.rstrip(".,;:、）)")
        site, key = classify(url)
        if site is None:
            items.append({"url": url, "site": None, "status": "不认识的站点（人工处理）"})
            continue
        if key in seen:
            continue
        seen.add(key)
        it = {"url": url, "site": site, "key": key}
        if site.startswith("blog-"):
            site_dir, script = next((s[2], s[3]) for s in SITE_TABLE if s[0] == site)
            if not os.path.exists(os.path.join(site_dir, script)):
                # 爬虫仓库不在本机：降级为通用抓取，落盘到 outputs/articles/<site>/
                site_dir = os.path.join(_config.PROJECT_ROOT, "outputs", "articles", site)
            art_dir = norm(os.path.join(site_dir, "output", "articles", key))
            it["target_dir"] = art_dir
            it["target_file"] = norm(os.path.join(art_dir, "content.html"))
            it["status"] = "已存在（跳过）" if os.path.exists(it["target_file"]) else "待下载"
        elif site == "acl":
            key = re.sub(r"\.pdf$", "", key, flags=re.I)   # 直接给 PDF 链接也能识别
            it["key"] = key
            parsed = acl_parse(key)
            if not parsed:
                it["status"] = "旧式 ACL ID（无点分），暂不支持"
            else:
                venue, year, pdf_url = parsed
                d = norm(os.path.join(ACL_ROOT, venue, year, key, "raw"))
                it.update(venue=venue, year=year, pdf_url=pdf_url,
                          target_dir=d, target_file=norm(os.path.join(d, "paper.pdf")),
                          status="已存在（跳过）" if os.path.exists(os.path.join(d, "paper.pdf")) else "待下载")
        else:  # arxiv
            d = norm(os.path.join(ARXIV_ROOT, arxiv_year(key), key, "raw"))
            it.update(pdf_url="https://arxiv.org/pdf/%s" % key,
                      target_dir=d, target_file=norm(os.path.join(d, "paper.pdf")),
                      status="已存在（跳过）" if os.path.exists(os.path.join(d, "paper.pdf")) else "待下载")
        items.append(it)
    return items


def cmd_plan(args):
    message = sys.stdin.read() if args.source == "-" else open(args.source, "r", encoding="utf-8").read()
    items = build_plan(message)
    for it in items:
        if it["site"] is None:
            print("!!  %s  %s" % (it["status"], it["url"]))
            continue
        print("%-10s %-8s %-18s %s" % (it["status"], it["site"], it["key"][:18], it.get("target_file", "")))
    n_todo = sum(1 for it in items if it.get("status") == "待下载")
    print("\n共 %d 个 URL：待下载 %d，已存在 %d，其它 %d"
          % (len(items), n_todo,
             sum(1 for it in items if str(it.get("status", "")).startswith("已存在")),
             len(items) - n_todo - sum(1 for it in items if str(it.get("status", "")).startswith("已存在"))))
    os.makedirs(os.path.dirname(DEFAULT_PLAN), exist_ok=True)
    with open(DEFAULT_PLAN, "w", encoding="utf-8") as f:
        json.dump({"items": items, "generated": time.strftime("%Y-%m-%d %H:%M:%S")}, f, ensure_ascii=False, indent=2)
    print("计划已写 %s" % DEFAULT_PLAN)


# --------------------------------------------------------------------------- fetch

def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def fetch_blog(it):
    site_dir, script = next((s[2], s[3]) for s in SITE_TABLE if s[0] == it["site"])
    title, html_text = page_title(it["url"])
    script_path = os.path.join(site_dir, script)
    if not os.path.exists(script_path):
        # 爬虫缺失：通用抓取，存原始 HTML（preprocess.py 可转 md）
        os.makedirs(it["target_dir"], exist_ok=True)
        with open(it["target_file"], "w", encoding="utf-8") as f:
            f.write(html_text)
        return "fetched-generic"
    mod = load_module(script_path, script[:-3])
    art = {"slug": it["key"], "url": it["url"], "title": title or it["key"],
           "authors": [], "date": "", "categories": []}
    ok = mod.fetch_article(art)
    return "fetched" if ok else "fetched-empty"


def fetch_acl(it):
    title, _ = page_title(it["url"])
    if os.path.exists(os.path.join(_config.ACL_DOWNLOADER, "downloader.py")):
        sys.path.insert(0, _config.ACL_DOWNLOADER)
        try:
            from downloader import download_paper          # 复用既有下载器（含重试/补元数据）
            paper = {"paper_id": it["key"], "title": title or it["key"],
                     "venue": it["venue"], "year": it["year"], "pdf_url": it["pdf_url"]}
            result = download_paper(paper, ACL_ROOT)
            return "fetched" if result == "downloaded" else "skipped"
        finally:
            sys.path.pop(0)
    # 降级：downloader 缺失，直接下 PDF（落盘结构与 arXiv 对齐）
    return download_pdf(it["pdf_url"], it["target_dir"],
                        {"paper_id": it["key"], "title": title or it["key"],
                         "venue": it["venue"], "year": it["year"], "url": it["url"],
                         "pdf_url": it["pdf_url"],
                         "fetched": time.strftime("%Y-%m-%d %H:%M:%S")})


def download_pdf(pdf_url, target_dir, meta):
    """直接下载 PDF + metadata.json（arXiv 原生路径，也是 ACL downloader 缺失时的降级路径）。"""
    os.makedirs(target_dir, exist_ok=True)
    r = requests.get(pdf_url, headers={"User-Agent": UA}, timeout=120, stream=True)
    r.raise_for_status()
    with open(os.path.join(target_dir, "paper.pdf"), "wb") as f:
        for chunk in r.iter_content(65536):
            if chunk:
                f.write(chunk)
    with open(os.path.join(target_dir, "metadata.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    return "fetched"


def fetch_arxiv(it):
    title, _ = page_title(it["url"])
    title = re.sub(r"^\[[^\]]*\]\s*", "", title)       # arXiv <title> 形如 "[2609.24972] Real Title"
    meta = {"paper_id": it["key"], "title": title or it["key"], "year": arxiv_year(it["key"]),
            "url": it["url"], "pdf_url": it["pdf_url"], "venue": "arXiv",
            "fetched": time.strftime("%Y-%m-%d %H:%M:%S")}
    return download_pdf(it["pdf_url"], it["target_dir"], meta)


FETCHERS = {"blog-lilianweng": fetch_blog, "blog-langchain": fetch_blog,
            "blog-anthropic": fetch_blog, "acl": fetch_acl, "arxiv": fetch_arxiv}


def cmd_fetch(args):
    plan_path = args.plan or DEFAULT_PLAN
    with open(plan_path, "r", encoding="utf-8") as f:
        plan = json.load(f)
    todo = [it for it in plan["items"] if it.get("status") == "待下载"]
    if not todo:
        print("没有待下载项。")
    for it in todo:
        print("下载 [%s] %s" % (it["site"], it["url"]))
        try:
            status = FETCHERS[it["site"]](it)
        except Exception as e:
            status = str(e)
        # 返回值 -> 状态：skipped=查重命中；fetched-generic=爬虫缺失走了通用抓取；fetched=正常；其余按失败处理
        if status == "skipped":
            it["status"] = "已存在（跳过）"
        elif status == "fetched-generic":
            it["status"] = "已下载（通用抓取）"
        elif status == "fetched":
            it["status"] = "已下载"
        else:
            it["status"] = "失败: %s" % status
        print("  -> %s" % it["status"])
        # 论文类提示 PDF→md 命令（不自动跑 —— 人决定何时跑）
        if it["site"] in ("acl", "arxiv") and it["status"] in ("已下载", "已存在（跳过）", "已下载（通用抓取）"):
            print("  PDF→md（born-digital，先试这个）: python skills/translate-zh/scripts/pdf2md.py %s"
                  % it["target_file"])
            if os.path.exists(os.path.join(_config.OCR_TOOLKIT, "ocr_pdfs.py")):
                print("  OCR（扫描件才需要）: python %s --input %s"
                      % (os.path.join(_config.OCR_TOOLKIT, "ocr_pdfs.py"), it["target_dir"]))
    with open(plan_path, "w", encoding="utf-8") as f:
        json.dump(plan, f, ensure_ascii=False, indent=2)
    print("\n计划已更新 %s" % plan_path)


# --------------------------------------------------------------------------- test（离线）

def cmd_test(_args):
    cases = []

    def check(name, fn):
        cases.append((name, fn))

    # 1. 口语化消息 + 多 URL + 噪声文字
    msg = ("帮我翻译论文链接：https://arxiv.org/abs/2609.24972，"
           "另外这篇 https://aclanthology.org/2020.emnlp-main.45/ 也翻一下，"
           "还有 https://blog.langchain.com/how-to-think-about-agent-frameworks/")
    items = build_plan(msg)
    check("口语化消息提取3个URL并分类", lambda: [it["site"] for it in items] == ["arxiv", "acl", "blog-langchain"])
    check("多级编号URL->EMNLP venue", lambda: items[1]["venue"] == "EMNLP" and items[1]["year"] == "2020")
    check("arxiv年份推导2609->2026",
          lambda: items[0]["target_dir"].replace("\\", "/") == norm(os.path.join(ARXIV_ROOT, "2026", "2609.24972", "raw")))
    # 查重结果只要求和文件系统一致（语料在不在本机都可能）
    it3 = items[2]
    check("langchain查重与文件系统一致",
          lambda: it3["status"] == ("已存在（跳过）" if os.path.exists(it3["target_file"]) else "待下载"))

    # 2. 本地已有 -> 查重命中（查重结果与文件系统一致即可，不预设语料存在）
    items2 = build_plan("翻这篇 https://lilianweng.github.io/posts/2023-01-27-the-transformer-family-v2/")
    it2 = items2[0]
    check("lilianweng查重与文件系统一致",
          lambda: it2["status"] == ("已存在（跳过）" if os.path.exists(it2["target_file"]) else "待下载"))

    # 3. URL 变种：arxiv 带版本号、langchain 新旧域名、ACL 直接 PDF 链接
    #    注意 lambda 默认参数绑定当前值 —— 否则循环变量晚绑定会让所有用例看到最后一组
    site, key = classify("https://arxiv.org/abs/2609.24972v2")
    check("arxiv版本号剥离", lambda s=site, k=key: (s, k) == ("arxiv", "2609.24972"))
    site, key = classify("https://www.langchain.com/blog/some-post/")
    check("langchain新域名识别", lambda s=site, k=key: (s, k) == ("blog-langchain", "some-post"))
    site, key = classify("https://blog.langchain.com/another-post/")
    check("langchain旧域名识别", lambda s=site, k=key: (s, k) == ("blog-langchain", "another-post"))
    items3 = build_plan("https://aclanthology.org/2024.acl-long.123.pdf")
    check("ACL直接PDF链接->paper_id", lambda: items3[0]["key"] == "2024.acl-long.123")

    # 4. 旧式 ACL ID -> 明确不支持而非瞎猜
    items4 = build_plan("https://aclanthology.org/A00-1001/")
    check("旧式ACL ID报不支持", lambda: "不支持" in items4[0]["status"])

    # 5. 重复 URL 去重 + 不认识站点如实报告
    items5 = build_plan("https://arxiv.org/abs/2609.24972 和 https://arxiv.org/abs/2609.24972v2 "
                        "还有 https://example.com/foo")
    check("同文重复URL去重", lambda: sum(1 for i in items5 if i.get("key") == "2609.24972") == 1)
    check("陌生站点如实报告", lambda: any(i["site"] is None for i in items5))

    # 6. ACL 目录结构与 ACL_ROOT 配置一致（<ACL_ROOT>/<VENUE>/<year>/<id>/raw）
    items6 = build_plan("https://aclanthology.org/2024.acl-long.123/")
    check("ACL落盘路径与ACL_ROOT一致",
          lambda: items6[0]["target_file"].replace("\\", "/") ==
          norm(os.path.join(ACL_ROOT, "ACL", "2024", "2024.acl-long.123", "raw", "paper.pdf")))

    fails = 0
    for name, fn in cases:
        try:
            ok = fn()
        except Exception as e:
            ok = False
            print("EXC", name, e)
        print("%s  %s" % ("PASS" if ok else "FAIL", name))
        fails += 0 if ok else 1
    print("\n%d/%d 通过" % (len(cases) - fails, len(cases)))
    if fails:
        sys.exit(1)


def main():
    p = argparse.ArgumentParser(description="从消息提取 URL，查重，委托既有爬虫下载")
    sub = p.add_subparsers(dest="cmd", required=True)

    pp = sub.add_parser("plan", help="消息 -> 计划（提取/分类/查重）")
    pp.add_argument("source", help="消息文本文件，或 - 读 stdin")
    pp.set_defaults(func=cmd_plan)

    pf = sub.add_parser("fetch", help="执行计划中待下载项")
    pf.add_argument("--plan", default=None, help="缺省用 outputs/fetch-plan.json")
    pf.set_defaults(func=cmd_fetch)

    pt = sub.add_parser("test", help="离线自检")
    pt.set_defaults(func=cmd_test)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
