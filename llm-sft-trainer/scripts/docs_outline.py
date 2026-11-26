#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
文档目录结构导出 —— 确定性工作一次做完，不让 agent 每次现写 grep。

数据源是 `docs/.state/<lib>.manifest.json`（同步时生成，含标题 / 章节 / 路径），
所以这个脚本**不联网、不解析 markdown**，毫秒级返回。

用法（按「文件夹 → 页 → 节 → 正文块」逐层收窄）：
  python scripts/docs_outline.py --dirs               # L0 文件夹结构（三库约 600 tok，一次给全）
  python scripts/docs_outline.py --toc --in blog      # L1 某目录下有哪些页
  python scripts/docs_outline.py --toc --level h2     # L1+ 连页内 ## 一起给（更贵，见页脚 tok 数）
  python scripts/docs_outline.py --headings <md路径>   # L2 这页分几节（到 ####）
  python scripts/docs_outline.py --read <md路径>       # L3 读正文：≤10k tok 整篇，>10k 给第 1 块
  python scripts/docs_outline.py --read <md> --chunk 2 # 下一块
  python scripts/docs_outline.py --search lora        # 按关键词找页（配 --lib / --in 锁范围）
  python scripts/docs_outline.py --recipes            # 缓存：按场景给三库对应页 + 综合判断
  python scripts/docs_outline.py --matrix             # 缓存：场景 × 最简/最强/组合
  python scripts/docs_outline.py --json               # 机器可读

为什么要有它：导航的固定动作是「先看目录」，而目录是清单的确定函数。
让 agent 临时 grep INDEX.md 既慢又容易漏，不如一条命令拿到。
"""

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
DOCS_DIR = SKILL_ROOT / "docs"
STATE_DIR = DOCS_DIR / ".state"
RECIPES = SKILL_ROOT / "references" / "recipes.json"

LIBS = ["trl", "unsloth", "swift"]

# 目录名的「功能词」→ 目录类型。
# 只认识功能词，**不认识任何库名**：官网把 blog 改名 case-studies、把 how_to_guides
# 改名 guides，规则照样命中。这就是「换库也能用」的关键。
# 顺序即优先级：概念 > 配方 > 实证 > 算法 > 手册 > 入口 > 集成 > 部署 > 答疑 > 实验 > 对象 > 定制
DIR_WORDS: list[tuple[tuple[str, ...], str]] = [
    (("concept", "architecture", "原理", "概念", "架构"), "概念"),
    (("howto", "guide", "tutorial", "recipe", "bestpractice", "practice",
      "指南", "教程", "最佳实践", "实践", "流程"), "配方"),
    (("blog", "case", "example", "showcase", "benchmark", "案例", "实战", "战报"), "实证"),
    (("trainer", "algorithm", "method", "算法", "方法", "grpo", "dpo", "ppo",
      "rlhf", "reinforcement", "强化", "train"), "算法"),
    (("reference", "api", "command", "cli", "config", "参数", "配置", "手册",
      "instruction", "操作", "说明", "utilit", "utils", "工具"), "手册"),
    (("install", "gettingstarted", "quickstart", "安装", "快速开始", "入门",
      "basic", "基础", "started"), "入口"),
    (("integration", "plugin", "connector", "集成", "插件", "connection", "连接"), "集成"),
    (("inference", "deploy", "serving", "推理", "部署", "export", "导出", "gguf"), "部署"),
    (("troubleshoot", "faq", "常见问题", "排错", "答疑"), "答疑"),
    (("experimental", "实验"), "实验"),
    (("model", "模型"), "对象"),
    (("custom", "自定义", "扩展"), "定制"),
]


def _norm(s: str) -> str:
    return re.sub(r"[-_\s]", "", s.lower())


def _classify_one(text: str) -> str | None:
    low = _norm(text)
    for words, label in DIR_WORDS:
        if any(_norm(w) in low for w in words):
            return label
    return None


def classify_dir(d: str) -> str:
    """先按叶子目录名判，判不出再用整条路径（避免继承父目录的类型）。"""
    return _classify_one(d.split("/")[-1]) or _classify_one(d) or "—"



def est_tokens(text: str) -> int:
    """粗估 token：CJK 1 字 ≈ 1 token，其余 4 字符 ≈ 1 token。误差约 ±20%。

    只用来做"要不要分块"的判断，不用来做精确计费。
    """
    cjk = sum(1 for c in text if "\u4e00" <= c <= "\u9fff")
    return int(cjk + (len(text) - cjk) / 4)


def _resolve_doc(rel: str) -> Path:
    """支持绝对/相对 skill 根/相对 docs/ 三种写法。"""
    for p in (Path(rel), SKILL_ROOT / rel, DOCS_DIR / rel):
        if p.is_file():
            return p
    sys.exit(f"FATAL: 文件不存在：{rel}（也没在 docs/ 下找到）")


def cmd_dirs(lib: str | None, as_json: bool, type_filter: str | None = None) -> None:
    """文件夹结构（全部嵌套层级）—— 三库合计约 700 tok，一次给全毫无压力。

    这是「先看目录」的第一步：只告诉你有哪些文件夹、各装几页、属于哪一类，
    不含任何标题和正文。`--type 配方` 只留该类型的目录（类型由功能词规则推出）。
    """
    targets = [lib] if lib else LIBS
    result = []
    buf: list[str] = []
    for name in targets:
        pages = load_pages(name)
        if pages is None:
            continue
        counts: dict[str, int] = {}
        for p in pages:
            d = Path(p["path"]).parent.as_posix()
            parts = d.split("/")
            for i in range(1, len(parts) + 1):
                counts.setdefault("/".join(parts[:i]), 0)
            counts[d] += 1
        dirs = sorted(counts)
        # 累计页数：看目录时关心的是「这个子树有多大」，不是只看直接子页
        cum = dict(counts)
        for d in sorted(counts, key=lambda x: -len(x.split("/"))):
            parent = "/".join(d.split("/")[:-1])
            if parent:
                cum[parent] = cum.get(parent, 0) + cum[d]
        result.append({"lib": name, "total_pages": len(pages),
                       "dirs": [{"path": d, "pages": cum[d], "type": classify_dir(d)}
                                for d in dirs
                                if not type_filter or classify_dir(d) == type_filter]})
        if as_json:
            continue
        rows = []
        for d in dirs:
            kind = classify_dir(d)
            if type_filter and kind != type_filter:
                continue
            depth = len(d.split("/")) - 1
            rows.append(("  " * depth + d.split("/")[-1] + "/", cum[d], kind))
        if not rows:
            continue
        scope = f"，只看「{type_filter}」" if type_filter else ""
        buf.append(f"\n=== {name}（{len(pages)} 页 / {len(dirs)} 个目录{scope}）===")
        w = max(_w(r[0]) for r in rows) + 2
        for label, c, kind in rows:
            buf.append(f"  {_pad(label, w)}{c:>4}  {kind}")
    if as_json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    out = "\n".join(buf)
    print(out)
    print(f"\n—— 本次输出 {len(buf):,} 行 ≈ {est_tokens(out):,} tok。"
          f"这是**文件夹结构**，不含页内标题；下一步用 --toc --in <目录> 看该目录下有哪些页。")


def cmd_toc(level: str, lib: str | None, in_filter: str | None, as_json: bool) -> None:
    """全量层级目录：章 → 页 →（可选）页内标题到 ##/###/####。

    这是「先看目录再决定读什么」的第一层，一次给全，按需加深。
    """
    max_level = {"page": 0, "h2": 2, "h3": 3, "h4": 4}[level]
    targets = [lib] if lib else LIBS
    tree = []
    for name in targets:
        pages = load_pages(name)
        if pages is None:
            continue
        chapters: list[dict] = []
        cur = None
        for p in pages:
            if in_filter:
                scope = f"{p['chapter']} {p['path']} {' '.join(p['groups'])}".lower()
                if in_filter.lower() not in scope:
                    continue
            node = {"title": p["title"], "path": p["path"], "heads": []}
            if max_level:
                f = DOCS_DIR / p["path"]
                if f.is_file():
                    for ln, line in enumerate(
                        f.read_text(encoding="utf-8", errors="replace").splitlines(), 1
                    ):
                        m = re.match(r"^(#{1,6})\s+\S", line)
                        if m and 2 <= len(m.group(1)) <= max_level:
                            node["heads"].append(
                                {"level": len(m.group(1)), "text": line.strip(), "line": ln}
                            )
            if p["chapter"] != cur:
                cur = p["chapter"]
                chapters.append({"chapter": cur, "pages": []})
            chapters[-1]["pages"].append(node)
        tree.append({"lib": name, "chapters": chapters})

    if as_json:
        print(json.dumps(tree, ensure_ascii=False, indent=2))
        return

    buf: list[str] = []
    for block in tree:
        n_pages = sum(len(c["pages"]) for c in block["chapters"])
        if not n_pages:
            continue
        buf.append(f"\n=== {block['lib']}（{n_pages} 页）===")
        for c in block["chapters"]:
            buf.append(f"\n## {c['chapter']}")
            for p in c["pages"]:
                buf.append(f"  - {p['title']}  →  {p['path']}")
                for h in p["heads"]:
                    buf.append("      " + "  " * (h["level"] - 2) + f"{h['text']}  @{h['line']}")
    out = "\n".join(buf)
    print(out)
    print(f"\n—— 本次输出 {len(buf):,} 行 ≈ {est_tokens(out):,} tok"
          f"（层级 {level}）。正文全文约 1.59M tok，不要整库读正文。")


def _split_chunks(lines: list[str], budget: int) -> list[tuple[int, int]]:
    """按 token 预算切块，切点回退到空行或标题行，避免把句子劈开。"""
    chunks: list[tuple[int, int]] = []
    n = len(lines)
    i = 0
    while i < n:
        start = i
        acc = 0
        while i < n and acc < budget:
            acc += est_tokens(lines[i]) + 1
            i += 1
        end = i
        if end < n and end - start > 3:
            # 在最后 20% 里往回找一个好断点
            floor = start + max(1, int((end - start) * 0.8))
            for j in range(end - 1, floor - 1, -1):
                s = lines[j].strip()
                if s == "" or s.startswith("#"):
                    end = j + 1 if s == "" else j
                    break
            if end <= start:
                end = i
        if end <= start:  # 兜底，防死循环
            end = start + 1
        chunks.append((start, end))
        i = end
    return chunks


def cmd_read(rel: str, chunk_idx: int, budget: int) -> None:
    """按预算分块读正文：整篇小于预算就一次给完，否则给第 N 块。"""
    p = _resolve_doc(rel).resolve()
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    total = est_tokens("\n".join(lines))
    relpath = p.relative_to(SKILL_ROOT).as_posix() if p.is_relative_to(SKILL_ROOT) else p.as_posix()

    if total <= budget:
        print(f"{relpath}  |  {len(lines):,} 行  ≈ {total:,} tok  |  预算 {budget:,} → 整篇一次读完")
        print("-" * 72)
        print("\n".join(lines))
        print("-" * 72)
        print(f"（整篇已在上面，约 {total:,} tok。不用分块。）")
        return

    chunks = _split_chunks(lines, budget)
    n = len(chunks)
    if not 1 <= chunk_idx <= n:
        sys.exit(f"FATAL: --chunk 只能是 1~{n}（这篇共 {n} 块）")
    s, e = chunks[chunk_idx - 1]
    body = "\n".join(lines[s:e])
    print(f"{relpath}  |  {len(lines):,} 行  ≈ {total:,} tok  |  预算 {budget:,}/块  |  共 {n} 块")
    print(f"[第 {chunk_idx}/{n} 块]  行 {s + 1}-{e}  ≈ {est_tokens(body):,} tok")
    print("-" * 72)
    print(body)
    print("-" * 72)
    if chunk_idx < n:
        print(f"下一块：python scripts/docs_outline.py --read {relpath} --chunk {chunk_idx + 1}")
    else:
        print("（已是最后一块。读完判断：够用就停，不要为了'读全'继续翻别的页。）")



def load_pages(lib: str) -> list[dict] | None:
    f = STATE_DIR / f"{lib}.manifest.json"
    if not f.exists():
        return None
    data = json.loads(f.read_text(encoding="utf-8"))
    pages = []
    for url, v in data["files"].items():
        path = v.get("path", "")
        parts = path.split("/")
        has_chapter = bool(v.get("chapter"))
        pages.append({
            "url": url,
            "title": v.get("title", ""),
            "path": path,
            "chapter": v.get("chapter") or v.get("group") or "(根目录)",
            "chapter_dir": parts[1] if has_chapter and len(parts) > 2 else "",
            "groups": v.get("groups") or [],
        })
    return pages


def group_by_chapter(pages: list[dict]) -> list[tuple[str, list[dict]]]:
    """按 chapter 分组，保持 manifest 里的官网顺序。"""
    order, buckets = [], {}
    for p in pages:
        key = p["chapter"]
        if key not in buckets:
            buckets[key] = []
            order.append(key)
        buckets[key].append(p)
    return [(k, buckets[k]) for k in order]


def cmd_overview(as_json: bool) -> None:
    data = {}
    for lib in LIBS:
        pages = load_pages(lib)
        if pages is None:
            data[lib] = None
            continue
        data[lib] = [{"chapter": c, "pages": len(ps),
                      "chapter_dir": ps[0]["chapter_dir"]} for c, ps in group_by_chapter(pages)]

    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return

    missing = [k for k, v in data.items() if v is None]
    if missing:
        print(f"WARN: 这些库还没同步：{','.join(missing)}；先跑 python scripts/sync_docs.py\n")

    total = sum(sum(x["pages"] for x in v) for v in data.values() if v)
    print(f"docs/ 共 {total} 页\n")
    for lib, groups in data.items():
        if not groups:
            continue
        print(f"=== {lib}（{sum(g['pages'] for g in groups)} 页）===")
        for g in groups:
            prefix = f"{g['chapter_dir']} · " if g["chapter_dir"] else ""
            print(f"  {prefix}{g['chapter']}  [{g['pages']} 页]")
        print()


def cmd_tree(lib: str, depth: int, as_json: bool) -> None:
    pages = load_pages(lib)
    if pages is None:
        sys.exit(f"FATAL: {lib} 还没同步；先跑 python scripts/sync_docs.py --only {lib}")

    if as_json:
        out = [{"chapter": c,
                "chapter_dir": ps[0]["chapter_dir"],
                "pages": [{"title": p["title"], "path": p["path"], "groups": p["groups"]}
                          for p in ps]} for c, ps in group_by_chapter(pages)]
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return

    print(f"{lib} 完整目录（{len(pages)} 页，depth={depth}）\n")
    for chapter, ps in group_by_chapter(pages):
        prefix = f"{ps[0]['chapter_dir']} · " if ps[0]["chapter_dir"] else ""
        print(f"{prefix}{chapter}  [{len(ps)} 页]")
        if depth <= 1:
            continue
        cur_groups = None
        for p in ps:
            if depth >= 3 and p["groups"] != cur_groups:
                cur_groups = p["groups"]
                if cur_groups:
                    print(f"    [{ ' / '.join(cur_groups) }]")
            indent = "    " if depth < 3 or not p["groups"] else "      "
            print(f"{indent}- {p['title']}  →  {p['path']}")
        print()


def cmd_search(query: str, lib: str | None, as_json: bool,
               in_filter: str | None = None) -> None:
    # 按词分解："long context" 要能命中 long_context_training.md
    # （整串匹配会因为下划线/连字符失效，实测踩过）
    tokens = [t for t in query.lower().split() if t]
    if not tokens:
        sys.exit("FATAL: --search 关键词为空")
    targets = [lib] if lib else LIBS
    hits = []
    for name in targets:
        pages = load_pages(name)
        if pages is None:
            continue
        for p in pages:
            # --in：把搜索限制在某一章/某一级目录内（心法第 2 层"先定章、再搜"）
            if in_filter:
                scope = f"{p['chapter']} {p['path']} {' '.join(p['groups'])}".lower()
                if in_filter.lower() not in scope:
                    continue
            hay = f"{p['title']} {p['path']} {p['chapter']} {' '.join(p['groups'])}".lower()
            if all(t in hay for t in tokens):
                hits.append({"lib": name, **p})

    if as_json:
        print(json.dumps(hits, ensure_ascii=False, indent=2))
        return

    if not hits:
        print(f"没命中「{query}」。换同义词再试（见 references/06_docs_nav.md 的关键词表）。")
        return
    print(f"命中「{query}」{len(hits)} 条：\n")
    for h in hits:
        group = " / ".join([h["chapter"]] + h["groups"])
        print(f"  [{h['lib']}] {group} · {h['title']}")
        print(f"         {h['path']}")


def cmd_headings(rel: str) -> None:
    p = Path(rel)
    if not p.exists():
        p = DOCS_DIR / rel
    if not p.exists():
        sys.exit(f"FATAL: 文件不存在：{rel}（也没在 docs/ 下找到）")
    print(f"{p}  的标题结构：\n")
    for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
        if re.match(r"^#{1,4}\s+\S", line):
            level = len(line) - len(line.lstrip("#"))
            print(f"{i:6d}  {'  ' * (level - 1)}{line.strip()}")


def resolve(lib: str, match: str, in_path: str | None = None) -> tuple[dict | None, str]:
    """按标题子串在当前清单里定位一页：先精确、再包含、最后退到路径。

    in_path 用于消歧：swift 的「命令行参数」「快速开始」各有两处（Instruction / Megatron-SWIFT），
    只按标题会命中最前面那个，未必是你想要的那套参数体系。
    """
    pages = load_pages(lib)
    if pages is None:
        return None, "该库还没同步"
    if in_path:
        pool = [p for p in pages if in_path.lower() in p["path"].lower()]
        if not pool:
            return None, f"路径里没有「{in_path}」"
        pages = pool
    m = match.lower()
    exact = [p for p in pages if p["title"].lower() == m]
    if exact:
        return exact[0], ""
    part = [p for p in pages if m in p["title"].lower()]
    if part:
        return part[0], ""
    part = [p for p in pages if m in p["path"].lower()]
    if part:
        return part[0], ""
    return None, "标题和路径里都没找到"


STEP_NAMES = {
    1: "定任务",
    2: "探环境",
    3: "定方案",
    4: "备数据",
    5: "写脚本",
    6: "跑训练 / 效果不对",
    7: "验收 / 部署",
}


def cmd_recipes(as_json: bool, only_step: int | None = None, only_lib: str | None = None) -> None:
    """三库并列 + 综合判断。

    输出不是「主推一篇、备选一篇」——同一个决策点，三个库各有各的答案和侧重，
    必须并排列出来，再给一行综合结论（最简 / 最强 / 怎么组合 / 什么不能混）。
    """
    if not RECIPES.exists():
        sys.exit(f"FATAL: 缺 {RECIPES}；这个文件是场景食谱，不能删")
    data = json.loads(RECIPES.read_text(encoding="utf-8"))
    recipes = data["recipes"]
    if only_step is not None:
        recipes = [r for r in recipes if r.get("step") == only_step]
        if not recipes:
            hint = "（第 2 步是跑脚本采事实，不需要查文档；决策类文档在 --step 3）" if only_step == 2 else ""
            sys.exit(f"第 {only_step} 步没有食谱条目{hint}")
    libs = [only_lib] if only_lib else LIBS

    rows, miss, hits = [], 0, set()
    for r in recipes:
        row = {"id": r.get("id"), "step": r.get("step"), "question": r["question"],
               "compare": r.get("compare"), "verify": r.get("verify", ""), "libs": {}}
        for lib in libs:
            spec = r.get("by_lib", {}).get(lib)
            if spec is None:
                row["libs"][lib] = {"status": "absent"}
                continue
            if "no_page" in spec:
                item = {"status": "no_page", "reason": spec["no_page"]}
                see = spec.get("see")
                if see:
                    p, e = resolve(lib, see["match"], see.get("in_path"))
                    item["see"] = {"match": see["match"], "page": p, "error": e}
                    if p is None:
                        miss += 1
                    else:
                        hits.add(p["path"])
                row["libs"][lib] = item
                continue
            p, e = resolve(lib, spec["match"], spec.get("in_path"))
            row["libs"][lib] = {"status": "ok" if p else "miss", "match": spec["match"],
                                "page": p, "error": e, "role": spec.get("role", "")}
            if p is None:
                miss += 1
            else:
                hits.add(p["path"])
        rows.append(row)

    if as_json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        cur = None
        for row in sorted(rows, key=lambda x: x.get("step", 0)):
            step = row.get("step", 0)
            if step != cur:
                cur = step
                print(f"\n# 第 {step} 步 · {STEP_NAMES.get(step, '')}\n")
            print(f"## {row['question']}")
            for lib, it in row["libs"].items():
                tag = f"[{lib}]".ljust(9)
                if it["status"] == "ok":
                    print(f"  {tag} {it['page']['title']}")
                    print(f"            {it['page']['path']}")
                    if it.get("role"):
                        print(f"            {it['role']}")
                elif it["status"] == "no_page":
                    print(f"  {tag} —（无对应页）{it['reason']}")
                    see = it.get("see")
                    if see:
                        if see["page"]:
                            print(f"            退到：{see['page']['title']}")
                            print(f"                  {see['page']['path']}")
                        else:
                            print(f"            退路也 MISS：「{see['match']}」{see['error']}")
                elif it["status"] == "absent":
                    print(f"  {tag} —（这条食谱没为它写对应项）")
                else:
                    print(f"  {tag} ✗ MISS：找不到「{it['match']}」——{it['error']}")
            cmp_ = row.get("compare")
            if cmp_:
                print("  ── 综合 ──")
                if cmp_.get("simplest"):
                    s = cmp_["simplest"]
                    print(f"  最简：{s['lib']} —— {s['text']}")
                if cmp_.get("strongest"):
                    g = cmp_["strongest"]
                    print(f"  最强：{g['lib']} —— {g['text']}")
                c = cmp_.get("combine")
                if c:
                    txt = "+".join(c.get("libs") or []) or "无"
                    print(f"  组合：{txt} —— {c['text']}")
                if cmp_.get("avoid"):
                    print(f"  禁忌：{cmp_['avoid']}")
            if row.get("verify"):
                print(f"  看完验证：{row['verify']}")
            print()
        print(f"共 {len(rows)} 条场景，命中 {len(hits)} 篇（去重），MISS {miss} 条"
              + ("（官网可能改名：用 --search 复核后改 recipes.json 的 match / in_path）" if miss else ""))

    if miss:
        sys.exit(1)


def _w(s: str) -> int:
    """中文按 2 列算宽度，否则表格对不齐。"""
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in s)


def _pad(s: str, width: int) -> str:
    return s + " " * max(0, width - _w(s))


def cmd_matrix(as_json: bool) -> None:
    """场景 × 三库覆盖 × 最简/最强：一眼看出这件事该看几个库、结论是什么。"""
    if not RECIPES.exists():
        sys.exit(f"FATAL: 缺 {RECIPES}")
    recipes = json.loads(RECIPES.read_text(encoding="utf-8"))["recipes"]

    rows = []
    for r in recipes:
        cover = {}
        for lib in LIBS:
            spec = r.get("by_lib", {}).get(lib)
            if spec is None:
                cover[lib] = "·"
            elif "no_page" in spec:
                cover[lib] = "—"
            else:
                p, _ = resolve(lib, spec["match"], spec.get("in_path"))
                cover[lib] = "✓" if p else "✗"
        c = r.get("compare") or {}
        rows.append({
            "step": r.get("step"),
            "question": r["question"],
            "cover": cover,
            "simplest": (c.get("simplest") or {}).get("lib", ""),
            "strongest": (c.get("strongest") or {}).get("lib", ""),
            "combine": "+".join((c.get("combine") or {}).get("libs") or []),
        })
    rows.sort(key=lambda x: x.get("step") or 0)

    if as_json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return

    qw = max(_w(r["question"]) for r in rows)
    col = [4, 5, 9, 7, 10, 10]          # 步 / trl / unsloth / swift / 最简 / 最强
    head = (_pad("步", col[0]) + _pad("场景", qw + 2) + _pad("trl", col[1])
            + _pad("unsloth", col[2]) + _pad("swift", col[3])
            + _pad("最简", col[4]) + _pad("最强", col[5]) + "组合")
    print(head)
    print("-" * _w(head))
    for r in rows:
        print(_pad(str(r["step"]), col[0]) + _pad(r["question"], qw + 2)
              + _pad(r["cover"]["trl"], col[1]) + _pad(r["cover"]["unsloth"], col[2])
              + _pad(r["cover"]["swift"], col[3]) + _pad(r["simplest"], col[4])
              + _pad(r["strongest"], col[5]) + (r["combine"] or "无"))
    print("\n✓=有专页  —=该库确实没有  ·=食谱未覆盖  ✗=应该有但没找到"
          "\n完整理由和看后验证：--recipes（可配 --step / --lib）")


def main() -> None:
    parser = argparse.ArgumentParser(description="导出三库文档目录结构（离线、读 manifest）")
    parser.add_argument("--lib", choices=LIBS, default=None)
    parser.add_argument("--depth", type=int, default=2, choices=[1, 2, 3],
                        help="1=只到章节 2=章节+页面（默认） 3=页面+子分组")
    parser.add_argument("--search", default=None, help="按关键词搜标题/路径")
    parser.add_argument("--in", dest="in_filter", default=None,
                        help="配合 --search：只在某一章内搜（如 --in how_to_guides）")
    parser.add_argument("--headings", default=None, help="打印某个 md 的标题结构")
    parser.add_argument("--toc", action="store_true",
                        help="全量层级目录（章→页→页内标题），配 --level 加深")
    parser.add_argument("--dirs", action="store_true",
                        help="文件夹结构（全部嵌套层级，三库合计约 700 tok），带目录类型标注")
    parser.add_argument("--type", dest="type_filter", default=None,
                        help="配合 --dirs：只看某一类目录（配方/实证/手册/概念/算法/对象/入口/集成/部署/答疑/实验/定制）")
    parser.add_argument("--level", choices=["page", "h2", "h3", "h4"], default="page",
                        help="配合 --toc：page=只到页(约2.9k tok) h2/h3/h4=加深到对应标题级")
    parser.add_argument("--read", default=None,
                        help="按预算分块读正文（配合 --chunk / --budget）")
    parser.add_argument("--chunk", type=int, default=1, help="配合 --read：读第几块（从 1 开始）")
    parser.add_argument("--budget", type=int, default=10000,
                        help="配合 --read：每块 token 预算，默认 10000")
    parser.add_argument("--recipes", action="store_true",
                        help="按场景给三库对应页 + 综合判断（读 references/recipes.json）")
    parser.add_argument("--matrix", action="store_true",
                        help="场景 × 三库覆盖 × 最简/最强 的横向对照")
    parser.add_argument("--step", type=int, choices=[1, 2, 3, 4, 5, 6, 7], default=None,
                        help="配合 --recipes：只看某一步的场景")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    if args.read:
        cmd_read(args.read, args.chunk, args.budget)
    elif args.dirs:
        cmd_dirs(args.lib, args.json, args.type_filter)
    elif args.toc:
        cmd_toc(args.level, args.lib, args.in_filter, args.json)
    elif args.headings:
        cmd_headings(args.headings)
    elif args.matrix:
        cmd_matrix(args.json)
    elif args.recipes:
        cmd_recipes(args.json, args.step, args.lib)
    elif args.search:
        cmd_search(args.search, args.lib, args.json, args.in_filter)
    elif args.lib:
        cmd_tree(args.lib, args.depth, args.json)
    else:
        cmd_overview(args.json)


if __name__ == "__main__":
    main()
