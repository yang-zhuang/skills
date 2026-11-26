#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
三个库的官方文档同步 —— 技能包自带，不依赖任何外部项目。

三个库的页面清单与正文来源（都是官方原始 markdown / rst，零转换）：
  trl      huggingface.co/docs/trl         → 首页 SideMenu JSON + 每页 .md 端点
  unsloth  unsloth.ai/docs                 → llms.txt + 其中的 .md 链接
  swift    swift.readthedocs.io/zh-cn/latest → searchindex.js + _sources/*.txt

输出（全部落在技能包内；docs/ 已被 .gitignore 排除，不进仓库）：
  docs/<lib>/...                     官方原文
  docs/<lib>/INDEX.md                按官方顺序的索引 —— agent 先读它，再决定读哪篇
  docs/.state/<lib>.manifest.json    URL 主键的增量状态（sha256）

用法：
  python scripts/sync_docs.py                       # 三个库全同步（增量）
  python scripts/sync_docs.py --only trl,swift      # 只同步指定库
  python scripts/sync_docs.py --force               # 强制重写全部文件
  python scripts/sync_docs.py --list                # 只列清单，不下载
  python scripts/sync_docs.py --page <官方页面URL>    # 只抓一页，存到 docs/<lib>/_single/

设计上的两个硬约束（别改坏）：
1. **失败页不删旧记录**。某页这次下载失败、但上次 manifest 里有它时，保留旧记录并标
   stale=true。否则 manifest 会少条、还会把失败页误报成「官网已删除」——这是原爬虫
   踩过的坑（一次 SSL 抖动丢 5 条记录）。
2. **索引由清单现算**，不写死路径。官网改组章节时，INDEX.md 自动跟着变，agent 的
   导航流程不腐烂。
"""

import argparse
import hashlib
import html as html_lib
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import requests

SKILL_ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = SKILL_ROOT / "docs"
STATE_DIR = DOCS_DIR / ".state"

HF_BASE = "https://hf-mirror.com"
HF_OFFICIAL = "https://huggingface.co"
GITBOOK_BASE = "https://unsloth.ai/docs"
SPHINX_BASE = "https://swift.readthedocs.io/zh-cn/latest"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) llm-sft-trainer/1.0",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}


def fetch(url: str, retries: int = 5, timeout: int = 60) -> str | None:
    """GET 一个 URL；429/503 按 Retry-After 退避。失败返回 None。"""
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(url, headers=HEADERS, timeout=timeout)
            if r.status_code == 200:
                r.encoding = "utf-8"
                return r.text
            if r.status_code in (429, 503):
                wait = int(r.headers.get("Retry-After") or 30)
                wait = min(max(wait, 5), 300) + (attempt * 2)
                print(f"    限流 HTTP {r.status_code}，等 {wait}s 重试（第 {attempt} 次）")
                if attempt < retries:
                    time.sleep(wait)
                    continue
            print(f"    HTTP {r.status_code}（第 {attempt} 次）")
        except requests.RequestException as e:
            print(f"    请求异常：{str(e)[:120]}（第 {attempt} 次）")
        if attempt < retries:
            time.sleep(3 * attempt)
    return None


def slugify(title: str) -> str:
    return re.sub(r"[^0-9a-zA-Z]+", "_", title.lower()).strip("_") or "unknown"


# ---------------------------------------------------------------- 清单获取

def list_hf(lib: str, base: str) -> list[dict]:
    """HF 文档站：首页内嵌 SideMenu JSON 里就是完整章节树。"""
    text = fetch(f"{base}/docs/{lib}/index")
    if text is None:
        sys.exit(f"FATAL: 拿不到 {lib} 文档首页，检查网络或换 --hf-base")
    m = re.search(r'data-target="SideMenu" data-props="([^"]*)"', text)
    if m is None:
        sys.exit("FATAL: 首页里没有 SideMenu JSON，官网结构可能变了")

    props = json.loads(html_lib.unescape(m.group(1)))
    pages: list[dict] = []

    def walk(sections, chapter, chapter_dir, groups):
        for sec in sections:
            if "url" in sec:
                pages.append({
                    "chapter": chapter,
                    "chapter_dir": chapter_dir,
                    "groups": groups,
                    "title": sec["title"],
                    "url_path": sec["url"],          # 形如 /docs/trl/sft_trainer
                })
            else:
                walk(sec["sections"], chapter, chapter_dir, groups + [sec["title"]])

    for idx, chapter in enumerate(props["chapters"]):
        walk(chapter["sections"], chapter["title"],
             f"{idx:02d}_{slugify(chapter['title'])}", [])
    return pages


def list_gitbook() -> list[dict]:
    """GitBook：llms.txt 每行 `- [标题](url.md): 简介`。"""
    text = fetch(f"{GITBOOK_BASE}/llms.txt")
    if text is None:
        sys.exit("FATAL: 拿不到 unsloth 的 llms.txt")
    line_re = re.compile(r"^- \[(.*?)\]\((https://unsloth\.ai/docs/[^)]+\.md)\)(?::\s*(.*))?$")
    prefix = GITBOOK_BASE + "/"
    pages, seen = [], set()
    for line in text.splitlines():
        m = line_re.match(line.strip())
        if not m:
            continue
        title, url, desc = m.group(1), m.group(2), (m.group(3) or "")
        rel = url[len(prefix):]
        if rel in seen:
            continue
        seen.add(rel)
        pages.append({"title": title, "url": url, "rel": rel, "description": desc})
    if not pages:
        sys.exit("FATAL: llms.txt 里没解析出链接，GitBook 格式可能变了")
    return pages


def list_sphinx() -> list[dict]:
    """Sphinx：searchindex.js 里 docnames / filenames / titles 三个数组按位对应。"""
    text = fetch(f"{SPHINX_BASE}/searchindex.js")
    if text is None:
        sys.exit("FATAL: 拿不到 swift 的 searchindex.js")
    data = json.loads(text[text.find("{"):text.rfind("}") + 1])
    docnames, filenames, titles = data["docnames"], data["filenames"], data["titles"]
    if not (len(docnames) == len(filenames) == len(titles)):
        sys.exit("FATAL: searchindex.js 三个数组长度不一致，Sphinx 版本可能变了")
    pages = []
    for docname, filename, title in zip(docnames, filenames, titles):
        ext = filename.rsplit(".", 1)[-1]
        pages.append({
            "docname": docname,
            "filename": filename,
            "title": title,
            "ext": ext,
            "src_url": f"{SPHINX_BASE}/_sources/{filename}.txt",
            "url": f"{SPHINX_BASE}/{docname}.html",
        })
    return pages


# ---------------------------------------------------------------- 落盘与索引

def read_old(lib: str) -> dict:
    p = STATE_DIR / f"{lib}.manifest.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8")).get("files", {})
    except json.JSONDecodeError:
        print(f"    WARN: {p.name} 解析失败，按首次同步处理")
        return {}


def write_manifest(lib: str, base: str, files: dict) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    (STATE_DIR / f"{lib}.manifest.json").write_text(json.dumps({
        "source": f"{lib}.docs",
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "base_url": base,
        "pages_total": len(files),
        "files": files,
    }, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")


def write_index(lib: str, title: str, base: str, entries: list[tuple[str, str, str]]) -> None:
    """entries: [(分组, 页面标题, 文件相对路径)]，顺序 = 官网顺序。"""
    p = DOCS_DIR / lib / "INDEX.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"# {title}", "",
             f"共 {len(entries)} 页 ｜ 同步时间 {datetime.now().isoformat(timespec='seconds')} ｜ 来源 {base}",
             "", "> 先读本索引定位章节，再打开对应文件；不要一次读完整棵目录。", ""]
    cur = None
    for group, page_title, rel in entries:
        if group != cur:
            cur = group
            lines.append(f"\n## {group}\n")
        lines.append(f"- [{page_title}]({rel})")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def save_page(rel: str, content: str) -> None:
    out = DOCS_DIR / rel
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(content, encoding="utf-8", newline="\n")


def put_file(files: dict, url: str, meta: dict) -> None:
    files[url] = meta


def keep_stale(files: dict, old: dict, url: str, failures: list) -> None:
    """失败页：上次有记录就保留（标 stale），避免 manifest 退化和误报删除。"""
    if url in old:
        kept = dict(old[url])
        kept["stale"] = True
        files[url] = kept
        print("    KEPT-STALE：保留上次记录，本次未更新")
    failures.append(url)


# ---------------------------------------------------------------- 三个同步器

def sync_hf(lib: str, base: str, workers: int, force: bool) -> dict:
    pages = list_hf(lib, base)
    print(f"  清单 {len(pages)} 页；线程 {workers}")

    if ARGS.list:
        for p in pages:
            print(f"  {p['title']}\t{HF_OFFICIAL}{p['url_path']}")
        return {"total": len(pages), "new": 0, "updated": 0, "unchanged": 0, "failed": 0}

    old = read_old(lib)
    files: dict = {}
    stat = {"total": len(pages), "new": 0, "updated": 0, "unchanged": 0, "failed": 0}
    failures: list = []
    index_entries: list[tuple[str, str, str]] = []

    def download(page):
        return page, fetch(base + page["url_path"] + ".md")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for i, (page, content) in enumerate(pool.map(download, pages), 1):
            label = " / ".join([page["chapter"]] + page["groups"] + [page["title"]])
            print(f"[{i}/{len(pages)}] {label}")
            url = HF_OFFICIAL + page["url_path"]
            if content is None or not content.strip() or \
               content.lstrip()[:200].lower().startswith(("<!doctype", "<html")):
                print("    FAILED")
                keep_stale(files, old, url, failures)
                stat["failed"] += 1
                continue

            rel_dir = Path(page["chapter_dir"])
            for g in page["groups"]:
                rel_dir = rel_dir / slugify(g)
            rel = (Path(lib) / rel_dir / f"{page['url_path'].rsplit('/', 1)[-1]}.md").as_posix()
            raw = content.encode("utf-8")
            sha = hashlib.sha256(raw).hexdigest()
            put_file(files, url, {
                "path": rel, "title": page["title"], "chapter": page["chapter"],
                "groups": page["groups"], "sha256": sha, "size": len(raw),
            })
            index_entries.append((
                f"{page['chapter_dir']} · {page['chapter']}",
                page["title"], rel[len(lib) + 1:],
            ))

            old_sha = old.get(url, {}).get("sha256")
            if old_sha == sha and not force:
                stat["unchanged"] += 1
                print("    unchanged")
            else:
                save_page(rel, content)
                if old_sha is None:
                    stat["new"] += 1
                    print(f"    NEW -> {rel}")
                else:
                    stat["updated"] += 1
                    print(f"    UPDATED -> {rel}")

    write_index(lib, f"{lib} 文档索引", HF_OFFICIAL, index_entries)
    write_manifest(lib, base, files)
    report_removed(old, files)
    return stat


def sync_gitbook(workers: int, force: bool) -> dict:
    pages = list_gitbook()
    print(f"  清单 {len(pages)} 页；线程 {workers}")

    if ARGS.list:
        for p in pages:
            print(f"  {p['title']}\t{p['url']}")
        return {"total": len(pages), "new": 0, "updated": 0, "unchanged": 0, "failed": 0}

    old = read_old("unsloth")
    files: dict = {}
    stat = {"total": len(pages), "new": 0, "updated": 0, "unchanged": 0, "failed": 0}
    failures: list = []
    index_entries: list[tuple[str, str, str]] = []

    def download(page):
        return page, fetch(page["url"])

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for i, (page, content) in enumerate(pool.map(download, pages), 1):
            print(f"[{i}/{len(pages)}] {page['title']}")
            if content is None or not content.strip() or \
               content.lstrip()[:200].lower().startswith(("<!doctype", "<html")):
                print("    FAILED")
                keep_stale(files, old, page["url"], failures)
                stat["failed"] += 1
                continue

            rel = (Path("unsloth") / page["rel"]).as_posix()
            raw = content.encode("utf-8")
            sha = hashlib.sha256(raw).hexdigest()
            group = page["rel"].split("/")[0] if "/" in page["rel"] else "(根目录)"
            put_file(files, page["url"], {
                "path": rel, "title": page["title"], "group": group,
                "description": page["description"], "sha256": sha, "size": len(raw),
            })
            index_entries.append((group, page["title"], rel[len("unsloth") + 1:]))

            old_sha = old.get(page["url"], {}).get("sha256")
            if old_sha == sha and not force:
                stat["unchanged"] += 1
                print("    unchanged")
            else:
                save_page(rel, content)
                if old_sha is None:
                    stat["new"] += 1
                    print(f"    NEW -> {rel}")
                else:
                    stat["updated"] += 1
                    print(f"    UPDATED -> {rel}")

    write_index("unsloth", "unsloth 文档索引", GITBOOK_BASE, index_entries)
    write_manifest("unsloth", GITBOOK_BASE, files)
    report_removed(old, files)
    return stat


def sync_sphinx(workers: int, force: bool) -> dict:
    pages = list_sphinx()
    print(f"  清单 {len(pages)} 页；线程 {workers}")

    if ARGS.list:
        for p in pages:
            print(f"  {p['title']}\t{p['url']}")
        return {"total": len(pages), "new": 0, "updated": 0, "unchanged": 0, "failed": 0}

    old = read_old("swift")
    files: dict = {}
    stat = {"total": len(pages), "new": 0, "updated": 0, "unchanged": 0, "failed": 0}
    failures: list = []
    index_entries: list[tuple[str, str, str]] = []

    def download(page):
        return page, fetch(page["src_url"])

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for i, (page, content) in enumerate(pool.map(download, pages), 1):
            print(f"[{i}/{len(pages)}] {page['title']} ({page['docname']})")
            if content is None or not content.strip() or \
               content.lstrip()[:200].lower().startswith(("<!doctype", "<html")):
                print("    FAILED")
                keep_stale(files, old, page["url"], failures)
                stat["failed"] += 1
                continue

            rel = (Path("swift") / f"{page['docname']}.{page['ext']}").as_posix()
            raw = content.encode("utf-8")
            sha = hashlib.sha256(raw).hexdigest()
            group = page["docname"].split("/")[0] if "/" in page["docname"] else "(根目录)"
            put_file(files, page["url"], {
                "path": rel, "title": page["title"], "source_url": page["src_url"],
                "format": page["ext"], "group": group, "sha256": sha, "size": len(raw),
            })
            index_entries.append((group, page["title"], rel[len("swift") + 1:]))

            old_sha = old.get(page["url"], {}).get("sha256")
            if old_sha == sha and not force:
                stat["unchanged"] += 1
                print("    unchanged")
            else:
                save_page(rel, content)
                if old_sha is None:
                    stat["new"] += 1
                    print(f"    NEW -> {rel}")
                else:
                    stat["updated"] += 1
                    print(f"    UPDATED -> {rel}")

    write_index("swift", "ms-swift 中文文档索引", SPHINX_BASE, index_entries)
    write_manifest("swift", SPHINX_BASE, files)
    report_removed(old, files)
    return stat


def report_removed(old: dict, files: dict) -> None:
    removed = sorted(set(old) - set(files))
    if removed:
        print("  官网已删除（本地文件保留，未删）:")
        for u in removed:
            print(f"    - {u}")


# ---------------------------------------------------------------- 单页抓取

def sync_one_page(url: str) -> None:
    """抓一页官方文档，存到 docs/<lib>/_single/。用于查漏补缺，不做全量。"""
    url = url.strip().split("#")[0].split("?")[0]

    if "/docs/trl/" in url:
        lib = "trl"
        md_url = url if url.endswith(".md") else url.rstrip("/") + ".md"
        name = url.rstrip("/").rsplit("/", 1)[-1].replace(".md", "") or "index"
        md_url = md_url if md_url.startswith("http") else HF_BASE + md_url
    elif "unsloth.ai/docs" in url:
        lib = "unsloth"
        md_url = url if url.endswith(".md") else url.rstrip("/") + ".md"
        name = md_url.split("/docs/", 1)[-1].replace("/", "__").replace(".md", "")
    elif "swift.readthedocs.io" in url:
        lib = "swift"
        if "/_sources/" in url:
            md_url = url
        else:
            docname = url.split("/latest/", 1)[-1].replace(".html", "")
            md_url = f"{SPHINX_BASE}/_sources/{docname}.md.txt"
        name = url.rsplit("/", 1)[-1].replace(".html", "").replace(".txt", "")
        if not name:
            name = "index"
    else:
        sys.exit("无法识别站点。只支持 huggingface.co/docs/trl、unsloth.ai/docs、swift.readthedocs.io")

    print(f"抓取 {md_url}")
    content = fetch(md_url, retries=3)
    if content is None:
        # swift 可能是 .rst
        if lib == "swift" and md_url.endswith(".md.txt"):
            alt = md_url[:-7] + ".rst.txt"
            print(f"  改试 {alt}")
            content = fetch(alt, retries=2)
            if content is not None:
                name += ".rst"
        if content is None:
            sys.exit("FAILED: 单页抓取失败")
    rel = (Path(lib) / "_single" / f"{name}.md").as_posix()
    save_page(rel, content)
    print(f"已保存 -> docs/{rel}")


# ---------------------------------------------------------------- 入口

def main() -> None:
    global ARGS
    parser = argparse.ArgumentParser(
        description="同步 trl / unsloth / ms-swift 官方文档到技能包 docs/")
    parser.add_argument("--only", default="trl,unsloth,swift",
                        help="逗号分隔：trl,unsloth,swift（默认全部）")
    parser.add_argument("--force", action="store_true", help="强制重写所有文件")
    parser.add_argument("--list", action="store_true", help="只列清单，不下载")
    parser.add_argument("--page", default=None, help="只抓一页：传官方页面 URL")
    parser.add_argument("--hf-base", default=HF_BASE,
                        help=f"HF 站点前缀，默认 {HF_BASE}；可换 {HF_OFFICIAL}")
    parser.add_argument("--workers", type=int, default=None,
                        help="覆盖默认线程数（trl 3 / unsloth 8 / swift 4）")
    ARGS = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if ARGS.page:
        sync_one_page(ARGS.page)
        return

    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)

    wanted = {x.strip().lower() for x in ARGS.only.split(",") if x.strip()}
    unknown = wanted - {"trl", "unsloth", "swift"}
    if unknown:
        sys.exit(f"未知库：{','.join(sorted(unknown))}；只支持 trl / unsloth / swift")

    jobs = []
    if "trl" in wanted:
        jobs.append(("trl", lambda: sync_hf("trl", ARGS.hf_base,
                                            ARGS.workers or 3, ARGS.force)))
    if "unsloth" in wanted:
        jobs.append(("unsloth", lambda: sync_gitbook(ARGS.workers or 8, ARGS.force)))
    if "swift" in wanted:
        jobs.append(("swift", lambda: sync_sphinx(ARGS.workers or 4, ARGS.force)))

    print(f"技能包：{SKILL_ROOT}")
    print(f"文档目录：{DOCS_DIR}\n")

    started = time.time()
    results = {}
    for name, job in jobs:
        print("=" * 64)
        print(f">>> {name}")
        print("=" * 64)
        t0 = time.time()
        try:
            results[name] = job()
        except SystemExit as e:
            results[name] = {"error": str(e)}
            print(f"  {e}")
        print(f"<<< {name} 用时 {int(time.time() - t0)}s\n")

    print("=" * 64)
    print(f"完成，总用时 {int(time.time() - started)}s")
    bad = False
    for name, st in results.items():
        if "error" in st:
            print(f"  {name}: 失败 — {st['error']}")
            bad = True
        else:
            print(f"  {name}: {st['total']} 页 | 新增 {st['new']} 更新 {st['updated']} "
                  f"未变 {st['unchanged']} 失败 {st['failed']}")
            if st["failed"]:
                bad = True
                print(f"        ↑ 有 {st['failed']} 页失败，已保留旧记录；重跑一次即可补上")
    print("\n下一步：读 docs/<lib>/INDEX.md 定位章节（见 references/06_docs_nav.md）")
    if bad:
        sys.exit(1)


if __name__ == "__main__":
    main()
