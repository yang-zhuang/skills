#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sections.py —— 论文/长文的「按节取舍」：找出该剔除的章节（参考文献等），
把对应内容整段删掉。纯标准库，零依赖。

为什么用正则不用 AI：没有参考文献的论文，正则不命中就什么都不删（天然幂等）；
AI 反而引入判断漂移。变种问题靠「归一化 + 整词匹配」解决。

核心思想：归一化后整词匹配，绝不用子串 —— 语料实测有 "API references"
"Respecting model preferences" 这种标题，子串匹配会误删正文。

子命令：
  list   <md>                    列出全部标题及分类（跳过/保留 + 命中规则 + 各节 token）
                                 —— 这是 digest 分派的底账：AI 分类只需要这份标题清单
  strip  <md> -o OUT             剔除 SKIP_CATS 命中的章节，输出到 OUT
         [--skip refs,ack,...]   只剔除指定类（默认 refs,citation）
         [--dry-run]             只打印将剔除的范围，不写文件
  test                           跑内置边界用例（References 后跟附录等）
  detect <txt> [--apply]         无结构文本流（OCR/手复制）的标题候选识别

用法示例：
  python sections.py list paper.md
  python sections.py strip paper.md -o paper.clean.md
  python sections.py strip paper.md -o out.md --skip refs,citation,ack,appendix
"""

import argparse
import re
import sys
import tempfile
import os

from chunk import count_tokens   # 同目录，token 估算（标准库启发式，宁可高估）

# ---------------------------------------------------------------------------
# 分类 → 归一化后整词匹配的标题集合。
# 归一化规则：去掉所有非字母非汉字字符并转小写。
#   "R eferences"   -> "references"     （PDF 抽取的空格粘连）
#   "6. References" -> "references"     （编号）
#   "REFERENCES"    -> "references"     （全大写）
#   "API references"-> "apireferences"  （≠ references，不会误删）
# ---------------------------------------------------------------------------
SKIP_CATS = {
    "refs":    {"references", "bibliography", "workscited", "literaturecited", "参考文献"},
    "citation":{"citation", "引用本文", "引用信息"},
    "ack":     {"acknowledgments", "acknowledgements", "acknowledgment", "acknowledgement", "致谢"},
    "contrib": {"authorcontributions", "authorscontributions", "作者贡献"},
    "funding": {"funding", "基金资助"},
    "conflict":{"conflictofinterest", "conflictofinterests", "declarationofinterests", "利益冲突声明", "利益冲突"},
    "data":    {"dataavailability", "数据可用性"},
    "toc":     {"contents", "tableofcontents", "toc", "outline", "目录", "纲要"},
    "appendix": "APPENDIX",   # 特殊标记：前缀匹配（appendix a: proofs -> appendixaproofs 以 appendix 开头）
}

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")          # markdown 标题（含尾部残留 #）
BOLDHEAD_RE = re.compile(r"^\*\*([^*]{1,60})\*\*:?\s*$")          # 伪标题：独立成行的 **References**（arXiv→md 常见）
CH_RE = re.compile(r"[\u4e00-\u9fff]")


def normalize(text):
    """标题归一化：只留英文字母与汉字，转小写。"""
    return re.sub(r"[^a-zA-Z\u4e00-\u9fff]+", "", text).lower()


def classify(heading_text):
    """返回 (类别 or None, 命中的词)。用于 list 展示与 strip 判断。"""
    norm = normalize(heading_text)
    if not norm:
        return None, None
    for cat, words in SKIP_CATS.items():
        if cat == "appendix":
            if norm.startswith("appendix") or norm.startswith("附录"):
                return cat, norm[:20]
            continue
        if norm in words:
            return cat, norm
    return None, None


def parse_structure(lines):
    """
    扫描行，产出标题序列：[(line_idx, level, text, is_bold)]。
    独立成行的 **粗体** 视作伪标题（level 记 2，参与分段）。
    """
    heads = []
    for i, ln in enumerate(lines):
        m = HEADING_RE.match(ln)
        if m:
            heads.append((i, len(m.group(1)), m.group(2).strip(), False))
            continue
        m = BOLDHEAD_RE.match(ln)
        if m:
            heads.append((i, 2, m.group(1).strip(), True))
    return heads


def find_spans(lines, cats):
    """
    找出待剔除的行区间 [(start, end_exclusive, heading_text, cat)]。
    区间 = 标题行 → 下一个 level <= 该标题 level 的标题行（或文件尾）。
    """
    spans = []
    heads = parse_structure(lines)
    for idx, (i, level, text, is_bold) in enumerate(heads):
        cat, hit = classify(text)
        if cat not in cats:
            continue
        end = len(lines)
        for j, level2, _t, _b in heads[idx + 1:]:
            if level2 <= level:
                end = j
                break
        spans.append((i, end, text, cat))
    return spans


def strip_file(src, cats):
    """返回 (剔除后的行列表, spans)。"""
    with open(src, "r", encoding="utf-8") as f:
        lines = f.read().split("\n")
    spans = find_spans(lines, cats)
    if not spans:
        return lines, spans
    drop = set()
    for s, e, _t, _c in spans:
        for k in range(s, e):
            drop.add(k)
    out = [ln for i, ln in enumerate(lines) if i not in drop]
    return out, spans


# --------------------------------------------------------------------------- 命令

def cmd_list(args):
    with open(args.file, "r", encoding="utf-8") as f:
        lines = f.read().split("\n")
    heads = parse_structure(lines)
    n_skip = 0
    for idx, (i, level, text, is_bold) in enumerate(heads):
        # 该节的范围：到下一个不深于它的标题（含子节 —— 分派按节做，子节跟着走）
        end = len(lines)
        for j, level2, _t, _b in heads[idx + 1:]:
            if level2 <= level:
                end = j
                break
        tok = count_tokens("\n".join(lines[i:end]))
        cat, _hit = classify(text)
        if cat:
            print("跳过 L%-2d 行%-5d %6d tok  %-12s <- %r" % (level, i + 1, tok, cat, text))
            n_skip += 1
        else:
            tag = "（粗体伪标题）" if is_bold else ""
            print("保留 L%-2d 行%-5d %6d tok  %s%s" % (level, i + 1, tok, text[:44], tag))
    # 总量直接数全文，避免父子节 span 嵌套重复累加；剔除量按 span 算（strip 实际会删的）
    total = count_tokens("\n".join(lines))
    skip_tok = sum(count_tokens("\n".join(lines[s:e]))
                   for s, e, _t, _c in find_spans(lines, cats=set(SKIP_CATS)))
    print("\n共 %d 个标题，其中 %d 个命中剔除类" % (len(heads), n_skip))
    print("全文 %d tok；纯正则可剔 %d tok；剔除后剩 %d tok" % (total, skip_tok, total - skip_tok))
    print("^ 这份清单（只有标题行）就是 AI 分类的输入 —— 每节标 full/digest/skip，约 1k token")


def cmd_strip(args):
    cats = set(args.skip.split(",")) if args.skip else {"refs", "citation"}
    bad = cats - set(SKIP_CATS)
    if bad:
        print("未知类别: %s（可选: %s）" % (", ".join(sorted(bad)), ", ".join(SKIP_CATS)))
        sys.exit(1)
    out_lines, spans = strip_file(args.file, cats)
    n_src = len(out_lines) + sum(e - s for s, e, _t, _c in spans)
    if not spans:
        print("未命中任何剔除类标题，输出 = 原文")
    for s, e, t, c in spans:
        print("剔除 [%s] 「%s」 行 %d–%d（%d 行）" % (c, t[:40], s + 1, e, e - s))
    removed = len(spans) and sum(e - s for s, e, _t, _c in spans)
    if args.dry_run:
        print("dry-run：共将剔除 %s 行，未写文件" % removed)
        return
    with open(args.out, "w", encoding="utf-8") as f:
        f.write("\n".join(out_lines))
    print("已写出 %s（%d 行 -> %d 行）" % (args.out, n_src, len(out_lines)))


def cmd_test(_args):
    """内置边界用例，全部自校验。"""
    cases = []

    # 1. References 后跟附录：附录必须幸存
    case1 = ["# Paper", "body text", "## References", "[1] foo", "[2] bar",
             "## Appendix A: Proofs", "proof content", "Q.E.D."]
    cases.append(("References后跟附录-附录幸存", case1, {"refs"},
                  lambda out: "proof content" in out and "[1] foo" not in out))

    # 2. 没有参考文献：输出 = 原文（这是"不需要 AI"的核心依据）
    case2 = ["# Paper", "## Method", "we propose X"]
    cases.append(("无References-原样输出", case2, {"refs"},
                  lambda out: out == ["# Paper", "## Method", "we propose X"]))

    # 3. 子串假阳性：API references / model preferences 不是文献节
    case3 = ["# Paper", "## API references", "see the docs", "## Respecting model preferences", "users care"]
    cases.append(("APIreferences不误删", case3, {"refs"},
                  lambda out: "see the docs" in out and "users care" in out))

    # 4. PDF 抽取变种："R eferences" / "3.2 REFERENCES" 都要命中
    case4 = ["# Paper", "text", "## R eferences", "[1] x", "## Next", "more"]
    cases.append(("空格粘连R-eferences命中", case4, {"refs"},
                  lambda out: "[1] x" not in out and "more" in out))

    # 5. 粗体伪标题：独立成行的 **References**
    case5 = ["# Paper", "text", "**References**", "[1] y", "## Ack", "end"]
    cases.append(("粗体伪标题References命中", case5, {"refs"},
                  lambda out: "[1] y" not in out))

    # 6. TOC 剔除 + 默认类别组合
    case6 = ["# Paper", "## Contents", "- [1. Intro](#a)", "- [2. Method](#b)",
             "## Introduction", "real content"]
    cases.append(("TOC剔除正文幸存", case6, {"refs", "toc"},
                  lambda out: "real content" in out and "[1. Intro]" not in out))

    # 7. 编号标题 "6. References"
    case7 = ["# Paper", "text", "## 6. References", "[1] z", "## 7. Appendix", "app"]
    cases.append(("编号References命中", case7, {"refs"},
                  lambda out: "[1] z" not in out and "app" in out))

    # ---- detect：OCR/无结构文本流 ----

    def heads_of(lines_in):
        return {(c[0], c[1], c[2]) for c in detect_heads(lines_in)}

    # 8. OCR 典型样本：多级编号 + 全大写 + 罗马数字 + 常见章节词 → high
    case8 = ["3.2 Attention Mechanisms", "We compute attention as follows.",
             "INTRODUCTION", "This paper studies...",
             "II. METHOD", "The model takes...",
             "References"]
    hs = heads_of(case8)
    ok8 = ((0, case8[0], 3) in hs and (2, case8[2], 2) in hs
           and (4, case8[4], 2) in hs and (6, case8[6], 2) in hs)
    cases.append(("OCR四种high候选全中", case8, None, lambda _o, _h=hs, _k=ok8: ok8))

    # 9. 列表连排不误判：1. 2. 3. 相邻 → 是列表
    case9 = ["1. First point", "2. Second point", "3. Third point", "body"]
    hs9 = heads_of(case9)
    cases.append(("列表连排不误判", case9, None, lambda _o: len(hs9) == 0))

    # 10. 长句 / 句末标点 → 不是标题
    case10 = ["This is a rather long line that runs on and on without any break at all here.",
              "It ends with a period.", "body"]
    cases.append(("长句与标点行排除", case10, None,
                  lambda _o: len(heads_of(case10)) == 0))

    # 11. 目录行（点引导线）→ 标记为 toc
    case11 = ["Introduction ........ 5", "Method ......... 12"]
    hs11 = heads_of(case11)
    cases.append(("点引导线识别为toc", case11, None,
                  lambda _o: len(hs11) == 2 and all(c[2] is None for c in
                              [(x[0], x[1], x[2]) for x in heads_of(case11)])))

    # 12. apply 后 strip 应能继续工作（detect→strip 链路）
    case12 = ["Deep Learning for Tables", "Some body text here.",
              "References", "Smith, J. 2020. Tables."]

    def check12():
        hl = detect_heads(case12)
        applied = list(case12)
        for i, text, level, conf, rule in hl:
            if conf == "high" and level:
                applied[i] = "#" * level + " " + text
        out, spans = strip_lines(applied, {"refs"})
        # "References"（常见章节词）应为 high 并插标题；首行只是 mid 候选不动；
        # strip 只剔 References 段（1 段），正文幸存
        return ("## References" in applied and len(spans) == 1
                and "Some body text here." in out and "Smith" not in out)
    cases.append(("detect后可strip（标题词References）", case12, None, lambda _o: check12()))

    fails = 0
    for name, lines_in, cats, check in cases:
        if cats is None:                      # detect 用例：check 只吃占位参数
            ok = check(None)
        else:
            out, spans = strip_lines(lines_in, cats)
            ok = check(out)
        print("%s  %s" % ("PASS" if ok else "FAIL", name))
        if not ok:
            fails += 1
            if cats is not None:
                for ln in out:
                    print("    |", ln)
    print("\n%d/%d 通过" % (len(cases) - fails, len(cases)))
    if fails:
        sys.exit(1)


def strip_lines(lines, cats):
    """对内存中的行做剔除（test 用）。"""
    spans = find_spans(lines, cats)
    if not spans:
        return lines, spans
    drop = set()
    for s, e, _t, _c in spans:
        drop.update(range(s, e))
    return [ln for i, ln in enumerate(lines) if i not in drop], spans


# ===========================================================================
# detect：无结构文本流（OCR / 手复制）的标题候选识别
#
# 原则：OCR 永远不可能"确定"标题 —— 输出候选 + 命中规则 + 置信度，
# 高置信度可 --apply 自动插 ##，中置信度只列出交给人确认。
# 这与 references/input-preprocessing.md §2 "中置信度不硬猜" 是同一条纪律。
# ===========================================================================

MULTI_NUM_RE = re.compile(r"^\d+(?:\.\d+)+\.?\s+\S")        # 3.2 Methods / 2.3.1 xxx
ROMAN_RE = re.compile(r"^(?:[IVXLC]{1,5})\.\s+\S")          # II. METHOD（论文分节常见）
LIST_RE = re.compile(r"^\s*(?:\d+\.|[-•*])\s+")             # 列表项：1. / - / •
CAPS_RE = re.compile(r"^[A-Z][A-Z0-9\s\-']{2,50}$")         # 全大写短行
TOC_RE = re.compile(r"\.{3,}\s*\d{1,4}\s*$")                # 点引导线：Introduction ..... 5
END_PUNCT_RE = re.compile(r"[.;:,!?]\s*$")                  # 句末标点（编号行允许）
WORD_HEADS = {"abstract", "introduction", "conclusion", "relatedwork", "background",
              "method", "methods", "results", "discussion", "experiments", "references",
              "摘要", "引言", "结论", "方法", "实验", "参考文献"}


def _isolated(lines, i):
    """上下都是空行（或文件边界）。"""
    above = (i == 0) or (not lines[i - 1].strip())
    below = (i == len(lines) - 1) or (not lines[i + 1].strip())
    return above and below


def detect_heads(lines):
    """
    返回候选列表 [(idx, text, level, conf, rule)]。
    conf: "high" / "mid"。列表连排（≥2 行同形）会被排除 —— 那是列表不是标题。
    """
    cands = []
    list_like = [bool(LIST_RE.match(l)) for l in lines]
    for i, raw in enumerate(lines):
        ln = raw.strip()
        if not ln or ln.startswith("#") or ln.startswith("!"):
            continue
        n = normalize(ln)
        # 列表连排抑制：本行像列表项，且相邻行也像 → 不是标题
        if list_like[i] and ((i > 0 and list_like[i - 1]) or (i < len(lines) - 1 and list_like[i + 1])):
            continue
        if TOC_RE.search(ln):
            cands.append((i, ln, None, "toc", "点引导线（目录页）"))
            continue
        if len(ln) > 70 or END_PUNCT_RE.search(ln):
            continue
        if MULTI_NUM_RE.match(ln):
            depth = len(re.match(r"^\d+(?:\.\d+)*", ln).group(0).split("."))
            cands.append((i, ln, min(1 + depth, 5), "high", "多级编号"))
            continue
        if ROMAN_RE.match(ln) and len(ln) <= 60:
            cands.append((i, ln, 2, "high", "罗马数字分节"))
            continue
        if CAPS_RE.match(ln) and sum(c.isalpha() for c in ln) >= 3:
            cands.append((i, ln, 2, "high", "全大写短行"))
            continue
        if n in WORD_HEADS:
            cands.append((i, ln, 2, "high", "常见章节词"))
            continue
        if _isolated(lines, i) and len(ln) <= 60 and not LIST_RE.match(ln):
            cands.append((i, ln, 3, "mid", "孤立短行无标点"))
    return cands


def cmd_detect(args):
    with open(args.file, "r", encoding="utf-8") as f:
        lines = f.read().split("\n")
    cands = detect_heads(lines)
    out = list(lines)
    n_apply = 0
    for i, text, level, conf, rule in cands:
        if conf == "high" and args.apply and level:
            out[i] = "#" * level + " " + text
            n_apply += 1
        print("%-4s 行%-5d L%-3s %-14s %s" % (conf.upper(), i + 1,
              level if level else "-", rule, text[:52]))
    print("\n候选 %d（high %d / mid %d / toc %d）" % (
        len(cands),
        sum(1 for c in cands if c[3] == "high"),
        sum(1 for c in cands if c[3] == "mid"),
        sum(1 for c in cands if c[3] == "toc")))
    if args.apply:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write("\n".join(out))
        print("已把 %d 个 high 候选插为标题 -> %s（mid 候选未动，待确认）" % (n_apply, args.out))


def main():
    p = argparse.ArgumentParser(description="论文按节取舍：剔除参考文献等章节")
    sub = p.add_subparsers(dest="cmd", required=True)

    pl = sub.add_parser("list", help="列出标题与分类")
    pl.add_argument("file")
    pl.set_defaults(func=cmd_list)

    ps = sub.add_parser("strip", help="剔除指定类别章节")
    ps.add_argument("file")
    ps.add_argument("-o", "--out", required=True)
    ps.add_argument("--skip", default="refs,citation",
                    help="逗号分隔，可选: %s（默认 refs,citation）" % ",".join(SKIP_CATS))
    ps.add_argument("--dry-run", action="store_true")
    ps.set_defaults(func=cmd_strip)

    pt = sub.add_parser("test", help="内置边界用例")
    pt.set_defaults(func=cmd_test)

    pd = sub.add_parser("detect", help="无结构文本流的标题候选（OCR/手复制）")
    pd.add_argument("file")
    pd.add_argument("-o", "--out", help="配合 --apply：插好标题的输出路径")
    pd.add_argument("--apply", action="store_true",
                    help="把 high 置信度候选插为 markdown 标题（mid 只列出不动）")
    pd.set_defaults(func=cmd_detect)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
