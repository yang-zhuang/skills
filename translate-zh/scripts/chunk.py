#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""translate-zh · 长文分块翻译

为什么需要这个脚本：
  整篇一次翻译在长文上会退化（后半段术语漂移、漏译、结构走形）。
  但按字符数粗暴切断会把句子/代码块/表格切碎，拼接后读不通。
  所以：按 markdown 结构切块 + 串行翻译 + 每块带上「上一块末尾的原文与译文」作锚点，
  既不用猜上一块翻到哪，也不会一次吃太长。

子命令：
  count    <md>                      统计 token 数
  cost     <md>                      翻译成本估算（开翻前必跑，超确认线必须用户点头）
  split    <md> --out-dir DIR        切块，产出 NNN.source.md + manifest.json
  context  <run-dir> --index N       打印第 N 块的翻译包（含上一块锚点）
  bundle   <run-dir> --index N       产出自包含任务包 NN.task.md（隔离靠这个）
  join     <run-dir> -o OUT          把所有 NNN.zh.md 拼成最终 md
  reconcile <md>                     统一合并后重复句的不同译法（只动清单项）

bundle 为什么存在：
  隔离不能靠"在 prompt 里写一句禁止读其他文件" —— 执行者拿着文件系统工具，
  想读就能读，那是君子协定。真正的隔离靠数据构造：把这一块需要的全部内容
  （规则 + 本块命中的术语 + 示例 + 上一块锚点 + 本块原文）由程序拼进一个文件，
  执行者读完这一个文件就够了，不需要打开任何其它东西。

  顺带省 token：术语表不全量塞，只投影本块真正用到的词条（glossary.yaml 的
  units / pending 两段是全量，因为它们是通用规则且决定全篇风格）。

零依赖：只用标准库。装了 tiktoken 会自动用它精确计数，没装就用启发式估算。
"""
import argparse
import json
import os
import re
import sys

import _config   # 路径配置唯一来源（同目录），勿再写死绝对路径

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

CJK = r"\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff\u3040-\u30ff\uff00-\uffef"
CJK_RE = re.compile("[" + CJK + "]")
# 英文句末标点后必须跟空白才断句（否则 URL 里的点会被当成句号，锚点被切碎）；
# 中文标点后无空格，直接断
SENT_END = re.compile(r"(?<=[.!?])(?=\s)|(?<=[。！？])")

DEFAULT_MAX_TOKENS = 5000      # 单块目标上限。2026-10-07 由 2800 上调：A/B 实测 4319 与 6435 token 整篇一次翻均无质量退化（术语零漂移、公式字节级一致、结构守恒）；原 2800 为保守设计值，无实测悬崖依据
DEFAULT_MIN_TOKENS = 1200      # 没到这个量不主动断块（避免零碎）
ANCHOR_MAX_TOKENS = 260        # 锚点最长取这么多 token
ANCHOR_SENTENCES = 4           # 锚点最多取这么几句


# ---------------------------------------------------------------- token 计数

def count_tokens(text):
    """估算 token 数。

    装了 tiktoken 用 cl100k_base 精确算；否则：
      中日韩字符 1 字 ≈ 1 token（宁可高估，块小一点更安全）
      其余按 max(词数 * 1.3, 字符数 / 4)
    """
    if not text:
        return 0
    try:
        import tiktoken  # noqa
        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:
        pass
    cjk = len(CJK_RE.findall(text))
    rest = CJK_RE.sub(" ", text)
    words = len(rest.split())
    chars = len(rest.strip())
    return int(cjk + max(words * 1.3, chars / 4.0))


# ---------------------------------------------------------------- 切块

def parse_blocks(lines):
    """把 markdown 切成不可分割的最小单元，返回 [(kind, text)]。

    代码块 / 表格 / 标题 / 段落各自成块，任何边界都不会落在它们中间。
    """
    blocks = []
    i, n = 0, len(lines)
    while i < n:
        raw = lines[i]
        stripped = raw.lstrip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            j = i + 1
            while j < n and not lines[j].lstrip().startswith(stripped[:3]):
                j += 1
            j = min(j + 1, n)
            blocks.append(("code", "\n".join(lines[i:j])))
            i = j
        elif stripped.startswith("|") and raw.count("|") >= 2:
            j = i
            while j < n and lines[j].lstrip().startswith("|"):
                j += 1
            blocks.append(("table", "\n".join(lines[i:j])))
            i = j
        elif re.match(r"^#{1,6}\s", raw):
            blocks.append(("heading", raw))
            i += 1
        elif raw.strip() == "":
            i += 1
        else:
            j = i
            while j < n and lines[j].strip() != "" and not lines[j].lstrip().startswith("```"):
                j += 1
            blocks.append(("para", "\n".join(lines[i:j])))
            i = j
    return blocks


def split_para(text, max_tokens):
    """超长段落按句子再切，避免一个巨大段落独占一块。"""
    parts = SENT_END.split(text)
    out, cur = [], ""
    for p in parts:
        if not p.strip():
            continue
        if cur and count_tokens(cur + p) > max_tokens:
            out.append(cur)
            cur = p
        else:
            cur = cur + p
    if cur:
        out.append(cur)
    return out or [text]


def pack(blocks, max_tokens, min_tokens):
    """贪心打包成块：优先在标题前断开，绝不切开代码/表格。"""
    chunks, cur, cur_tok = [], [], 0
    for kind, text in blocks:
        t = count_tokens(text)
        if t > max_tokens and kind in ("code", "table"):
            if cur:
                chunks.append(cur)
                cur, cur_tok = [], 0
            chunks.append([(kind, text)])
            continue
        if t > max_tokens:
            # 单个段落/代码块/表格本身就超阈值：让它单独成块，绝不切碎。
            # 切碎会产生「半截段落」，翻译出来的东西也是残的 —— 宁可块大一点
            if cur:
                chunks.append(cur)
                cur, cur_tok = [], 0
            chunks.append([(kind, text)])
            continue
        at_heading = kind == "heading"
        if cur and (cur_tok + t > max_tokens or (at_heading and cur_tok >= min_tokens)):
            chunks.append(cur)
            cur, cur_tok = [], 0
        cur.append((kind, text))
        cur_tok += t
    if cur:
        chunks.append(cur)
    return ["\n\n".join(t for _k, t in c) for c in chunks]


# ---------------------------------------------------------------- 术语投影

SEC_RE = re.compile(r"^(keep|expand|translate|pending|units|meta):\s*$")


def read_sections(path):
    """按 yaml 顶层键切段，返回 {段名: [原始行]}。轻量处理，不依赖 pyyaml。"""
    sections, cur = {}, None
    for ln in open(path, "r", encoding="utf-8").read().split("\n"):
        m = SEC_RE.match(ln)
        if m:
            cur = m.group(1)
            sections[cur] = []
            continue
        if cur:
            sections[cur].append(ln)
    return sections


def split_entries(lines, kind):
    """把一段切成 [(原始行列表, [词条])]，词条用于判断是否在本块出现。"""
    entries, buf = [], []
    for ln in lines:
        s = ln.strip()
        if kind == "keep":
            if s.startswith("- ") and not buf:
                buf.append(ln)
            elif s.startswith("- "):
                buf.append(ln)
            elif buf and (s.startswith("#") or not s):
                buf.append(ln)
            elif buf:
                entries.append(buf)
                buf = []
        elif kind == "translate":
            if re.match(r"^[a-z][\w \-]*:", ln.lstrip()) or not s:
                if buf:
                    entries.append(buf)
                buf = [ln]
            else:
                buf.append(ln)
        else:  # expand
            if re.match(r"^  [A-Za-z][\w\-]*:\s*$", ln):
                if buf:
                    entries.append(buf)
                buf = [ln]
            elif buf is not None:
                buf.append(ln)
    if buf:
        entries.append(buf)
    out = []
    for blk in entries:
        terms = []
        for ln in blk:
            s = ln.strip()
            if kind == "keep" and s.startswith("- "):
                terms.append(s[2:].strip())
            elif kind == "translate" and not s.startswith("#") and ":" in s:
                terms.append(s.split(":")[0].strip())
            elif kind == "expand" and re.match(r"^[A-Za-z][\w\-]*:$", s):
                terms.append(s[:-1])
        terms = [t for t in terms if t]
        # 注释行参与输出（保留说明），但不参与命中判断
        real = [ln for ln in blk if not ln.strip().startswith("#")]
        out.append((real, terms))
    return out


def project_glossary(path, text):
    """只保留本块原文中真正出现的术语条目。units / pending 全量输出。"""
    sec = read_sections(path)
    parts = []
    for kind in ("keep", "expand", "translate"):
        hits = []
        for blk, terms in split_entries(sec.get(kind, []), kind):
            hit = False
            for t in terms:
                if re.search(r"(?i)\b" + re.escape(t) + r"\b", text):
                    hit = True
                    break
            if hit:
                hits.append("\n".join(blk).rstrip())
        if hits:
            parts.append("%s:\n%s" % (kind, "\n".join(hits)))
    for kind in ("units", "pending"):
        if sec.get(kind):
            body = "\n".join(sec[kind]).strip()
            if body:
                parts.append("%s:\n%s" % (kind, body))
    return "\n\n".join(parts)


# ---------------------------------------------------------------- 锚点

TAIL_NOTE_RE = re.compile(r"\n#+\s*(翻译说明|未确定的格式|译注|译者注)\b")


def strip_tail_notes(text):
    """锚点不能取自译文末尾的附录（翻译说明 / 未确定的格式）。

    不然下一块拿到的语气参照全是「译者觉得哪些术语待定」这类元信息，
    而不是真正的正文风格 —— 第 2 块执行者当场就反馈了这个问题。
    """
    m = TAIL_NOTE_RE.search(text)
    return text[:m.start()] if m else text


def tail_sentences(text, max_sent=ANCHOR_SENTENCES, max_tok=ANCHOR_MAX_TOKENS):
    """取文本末尾几句，供下一块接续用。"""
    flat = " ".join(text.split())
    parts = [p for p in SENT_END.split(flat) if p.strip()]
    out = []
    total = 0
    for p in reversed(parts):
        t = count_tokens(p)
        if out and (total + t > max_tok or len(out) >= max_sent):
            break
        out.append(p.strip())
        total += t
        if len(out) >= max_sent:
            break
    return [" ".join(reversed(out))]


# ---------------------------------------------------------------- 子命令

def cmd_count(args):
    text = open(args.md, "r", encoding="utf-8", errors="ignore").read()
    n = count_tokens(text)
    try:
        import tiktoken  # noqa
        method = "tiktoken/cl100k_base"
    except Exception:
        method = "启发式估算"
    print("%s: %d tokens (%s)" % (args.md, n, method))
    print("阈值 %d → %s" % (args.max_tokens, "整篇一次翻译" if n <= args.max_tokens else "需要分块"))


# ---------------------------------------------------------------- 成本闸门

# 每次翻译调用的固定开销：规则 + 术语投影 + 示例 + 锚点 + 回执。
# 实测来源：9094 token 文章分 6 块，每块 task.md ≈ 7100，减平均块文本 ≈ 5500。
# bundle 结构改了之后用 --overhead 修正，不要凭感觉拍。
CALL_OVERHEAD = 5500
OUT_RATIO = 1.2          # 中文译文 token ≈ 源 × 1.2（刻意高估，宁多勿少）
CONFIRM_LIMIT = 30000    # 预估总消耗超过此数，开翻前必须用户明确确认


def cmd_cost(args):
    """翻译成本估算。分块不省 token —— 每块都重复付一次固定开销，这里把账算出来。

    --factor 用于 digest 等减量模式：先按模式表分离/压缩，量定下来后把实际占比
    传进来复核（比如剔参考文献+实验压缩后剩 35% 源量，就 --factor 0.35）。
    """
    text = open(args.md, "r", encoding="utf-8", errors="ignore").read()
    full = count_tokens(text)
    t = int(full * args.factor)
    if t <= DEFAULT_MAX_TOKENS:
        n = 1
    elif args.factor == 1.0:
        # 真实文本在手：按实际结构切块数（标题边界会让块比均分更碎、更多）
        n = len(pack(parse_blocks(text.split("\n")), DEFAULT_MAX_TOKENS, DEFAULT_MIN_TOKENS))
    else:
        # 减量后的文本还不存在，只能按量推下限；实际会更碎 —— 末行如实标注
        n = -(-t // DEFAULT_MAX_TOKENS)
    out_tok = int(t * OUT_RATIO)
    whole_in = t + args.overhead
    chunk_in = t + n * args.overhead
    label = "" if args.factor == 1.0 else "（--factor %.2f，源 %d → 计 %d token）" % (args.factor, full, t)
    print("源文: %d token  %s  (%s)" % (t, label, os.path.basename(args.md)))
    print("输出(高估): ~%d token" % out_tok)
    print()
    if n == 1:
        total = whole_in + out_tok
        print("整篇一次翻: 输入 ~%d + 输出 ~%d  （≤%d token，唯一合理选项）"
              % (whole_in, out_tok, DEFAULT_MAX_TOKENS))
    else:
        print("整篇一次翻: 输入 ~%d + 输出 ~%d  （最省，但 %d token 的长文后段会退化，一般不可行）"
              % (whole_in, out_tok, t))
        note = "" if args.factor == 1.0 else "（块数按量推的下限，减量稿实际切块会更碎、开销略高）"
        print("分块 %d 块:   输入 ~%d + 输出 ~%d，需 %d 次独立调用  （质量稳，输入是整篇的 ×%.1f）%s"
              % (n, chunk_in, out_tok, n, chunk_in / float(whole_in), note))
        total = chunk_in + out_tok
    print()
    if total > CONFIRM_LIMIT:
        print("⚠ 预估总消耗 ~%d token > 确认线 %d —— 把这份估算给用户看过、得到明确确认后才许开翻"
              % (total, CONFIRM_LIMIT))
    else:
        print("预估总消耗 ~%d token，≤ 确认线 %d，可直接翻" % (total, CONFIRM_LIMIT))


def cmd_split(args):
    text = open(args.md, "r", encoding="utf-8", errors="ignore").read()
    lines = text.split("\n")
    os.makedirs(args.out_dir, exist_ok=True)
    total = count_tokens(text)
    # 总量在阈值内就整篇一次翻完，绝不切 —— 切了就是人为制造衔接风险
    chunks = [text] if total <= args.max_tokens else pack(parse_blocks(lines),
                                                          args.max_tokens,
                                                          args.min_tokens)

    items = []
    for i, c in enumerate(chunks):
        name = "%03d.source.md" % i
        with open(os.path.join(args.out_dir, name), "w", encoding="utf-8") as f:
            f.write(c)
        items.append({
            "index": i,
            "source": name,
            "target": "%03d.zh.md" % i,
            "tokens": count_tokens(c),
            "status": "pending",
        })
    manifest = {
        "origin": os.path.abspath(args.md),
        "max_tokens": args.max_tokens,
        "total_tokens": count_tokens(text),
        "chunk_count": len(chunks),
        "single": len(chunks) == 1,
        "chunks": items,
    }
    with open(os.path.join(args.out_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print("切出 %d 块 → %s" % (len(chunks), args.out_dir))
    for it in items:
        print("  %s  %5d tokens  %s" % (it["source"], it["tokens"], it["status"]))
    if len(chunks) == 1:
        print("总量在阈值内，直接整篇翻译即可，无需走 context。")


def cmd_context(args):
    run_dir = args.run_dir
    mf = json.load(open(os.path.join(run_dir, "manifest.json"), "r", encoding="utf-8"))
    chunks = mf["chunks"]
    if args.index >= len(chunks):
        print("只有 %d 块（0..%d）" % (len(chunks), len(chunks) - 1))
        return
    cur = chunks[args.index]
    src = open(os.path.join(run_dir, cur["source"]), "r", encoding="utf-8").read()

    lines = ["# 翻译任务 · 第 %d/%d 块" % (cur["index"] + 1, len(chunks)), ""]
    lines.append("规则文件：`skills/translate-zh/SKILL.md`（入口）+ `skills/translate-zh/references/execution-rules.md`（判断准则+硬边界，必读）+ `data/glossary.yaml` + `data/examples.md`（术语与示例已投影进本包，此处为原文位置）")
    lines.append("")

    if args.index > 0:
        prev = chunks[args.index - 1]
        prev_src = open(os.path.join(run_dir, prev["source"]), "r", encoding="utf-8").read()
        src_anchor = tail_sentences(prev_src)
        zh_path = os.path.join(run_dir, prev["target"])
        if os.path.exists(zh_path):
            zh_anchor = tail_sentences(open(zh_path, "r", encoding="utf-8").read())
        else:
            zh_anchor = []
            print("[警告] %s 还没翻译，锚点只有原文" % prev["target"], file=sys.stderr)
        lines.append("## 1. 接续锚点（只用来对齐语气与术语，不要输出）")
        lines.append("")
        lines.append("上一块原文末尾：")
        lines.append("")
        lines.append("```")
        lines.extend(src_anchor)
        lines.append("```")
        lines.append("")
        if zh_anchor:
            lines.append("上一块译文末尾：")
            lines.append("")
            lines.append("```")
            lines.extend(zh_anchor)
            lines.append("```")
            lines.append("")
        lines.append("## 2. 待翻译内容")

    lines.append("")
    lines.append("请完整翻译下面这一块，**从它的第一句开始输出，不要重复锚点内容**：")
    lines.append("")
    lines.append("```markdown")
    lines.append(src)
    lines.append("```")
    lines.append("")
    lines.append("## 3. 输出")
    lines.append("")
    lines.append("- 写入 `%s`" % os.path.join(run_dir, cur["target"]))
    lines.append("- 只写本块译文，省略规则说明；有分歧写进最终文件末尾的「翻译说明」")
    lines.append("- 写完执行：`python chunk.py context %s --index %d` 拿下一块"
                 % (run_dir, args.index + 1))
    print("\n".join(lines))


PROJECT_ROOT = _config.PROJECT_ROOT   # 仓库根（含 data/），从脚本位置推导，可被 .env / 环境变量影响
SKILL_ROOT = _config.SKILL_ROOT       # skills/translate-zh（references/ 在此）


def cmd_bundle(args):
    """产出自包含任务包：执行者读完这一个文件即可翻译，无需打开其它任何文件。"""
    mf = json.load(open(os.path.join(args.run_dir, "manifest.json"), "r", encoding="utf-8"))
    chunks = mf["chunks"]
    cur = chunks[args.index]
    src = open(os.path.join(args.run_dir, cur["source"]), "r", encoding="utf-8").read()

    parts = ["# 翻译任务 · 第 %d/%d 块（自包含）" % (cur["index"] + 1, len(chunks)), ""]
    parts.append("> 本文件包含你需要的一切：**规则 + 本块命中的术语 + 译文对照示例 + "
                 "上一块锚点 + 本块原文**。")
    parts.append("> **不需要**再去读项目的任何其它文件（术语表全集、其它块的原文都不需要读）。")
    parts.append("> 这么做的理由：翻译过程要被隔离，执行者只有本块视野，"
                 "才能验证「分块拼接后是否真的连贯」。")
    parts.append("")

    # 注意：此处必须内嵌文件实体（自包含），不能用"指引去读"——执行 agent 禁读项目其它文件。
    # 2026-10-07 修复：原指向根 SKILL.md，包结构重组后该文件已移至 skills/translate-zh/，
    # exists() 为假会被静默跳过导致规则段丢失；改内嵌 references/execution-rules.md（块级执行者所需）。
    for title, p in (("1. 翻译规则", os.path.join(SKILL_ROOT, "references", "execution-rules.md")),
                     ("3. 好坏译文对照", os.path.join(PROJECT_ROOT, "data", "examples.md"))):
        if not os.path.exists(p):
            raise FileNotFoundError("bundle 内嵌源文件缺失：%s（静默跳过会让执行者拿到残缺任务包）" % p)
        body = open(p, "r", encoding="utf-8").read().strip()
        if body:
            parts.append("## %s" % title)
            parts.append("")
            parts.append(body)
            parts.append("")

    gloss = project_glossary(os.path.join(PROJECT_ROOT, "data", "glossary.yaml"), src)
    # 累积词典：前几块执行者临机决定的术语，防止同一术语在不同块里译法不一致
    lex_path = os.path.join(args.run_dir, "lexicon.md")
    lex = ""
    if os.path.exists(lex_path):
        lex = open(lex_path, "r", encoding="utf-8").read().strip()
    parts.append("## 2. 术语表（已从全集投影，只包含本块出现的词条 + 通用单位/待定规则）")
    parts.append("")
    parts.append("```yaml")
    parts.append(gloss)
    parts.append("```")
    parts.append("")
    if lex:
        parts.append("### 2.1 前面几块已定下来的译法（**优先级最高**，务必沿用）")
        parts.append("")
        parts.append("> 这些是前几块翻译时临机决定的术语。不同块译成不同的样子，"
                     "读者会以为是两个概念 —— 所以这里强制统一。")
        parts.append("")
        parts.append(lex)
        parts.append("")

    if args.index > 0:
        prev = chunks[args.index - 1]
        prev_src = open(os.path.join(args.run_dir, prev["source"]), "r", encoding="utf-8").read()
        zh_path = os.path.join(args.run_dir, prev["target"])
        parts.append("## 4. 接续锚点（只用来对齐语气与术语，**不要输出**）")
        parts.append("")
        parts.append("上一块原文末尾：")
        parts.append("")
        parts.append("```")
        parts.append("\n".join(tail_sentences(prev_src)))
        parts.append("```")
        parts.append("")
        if os.path.exists(zh_path):
            parts.append("上一块译文末尾：")
            parts.append("")
            parts.append("```")
            parts.append("\n".join(tail_sentences(strip_tail_notes(
                open(zh_path, "r", encoding="utf-8").read()))))
            parts.append("```")
            parts.append("")

    out_name = os.path.join(args.run_dir, cur["target"].replace(".zh.md", ".task.md"))
    parts.append("## 5. 本块原文")
    parts.append("")
    parts.append("```markdown")
    parts.append(src)
    parts.append("```")
    parts.append("")
    parts.append("## 6. 你要做的事")
    parts.append("")
    parts.append("1. 完整翻译第 5 节的每一段，不删减、不概括、不加自己的评论")
    parts.append("2. 图片 URL / 链接 / markdown 结构原样保留，只译自然语言")
    parts.append("3. 术语严格按第 2 节；遇到没列进来的术语，按第 1 节 §4.3 的判据临机决定")
    parts.append("4. 若有锚点：**从本块原文的第一句开始输出**，不要重复锚点内容")
    parts.append("5. 译文写入：`%s`（UTF-8）" % os.path.abspath(os.path.join(args.run_dir,
                                                                         cur["target"])))
    parts.append("6. 写完后回报：字节数、临机决定的术语、拿不准的地方（**不要回传译文**）")

    text = "\n".join(parts)
    with open(out_name, "w", encoding="utf-8") as f:
        f.write(text)
    n_all = count_tokens(text)
    n_src = count_tokens(src)
    print("任务包: %s" % out_name)
    print("  本块原文 %d tokens · 整包 %d tokens · 规则+术语+示例开销 %d"
          % (n_src, n_all, n_all - n_src))


def cmd_reconcile(args):
    """合并后的兜底：把「同一个原文到处出现，却译成不同样子」的地方统一。

    为什么必须做：
      分块执行者看不到全文。本文开头和结尾刻意重复了同一组 TL;DR，
      六个隔离的翻译者分别重翻了一遍 —— 合并后一致度掉到 83%~97%。
      术语可以靠词典钉死，句式钉不死，只能事后对齐。
    为什么用「清单项」做判据，而不是靠相似度硬卡：
      0.85 阈值会误伤 —— 把「LangGraph 是底层编排框架」统一成「事件驱动框架」、
      把「跨 thread 长期记忆」统一成「多轮交互短期记忆」，这些是不同概念。
      真正的重复集中在清单式内容（bullet list）：TL;DR、要点总结、
      反复出现的列表。清单项语义单元独立，统一是安全的；正文段落不是。
    """
    import difflib
    LIST_RE = re.compile(r"^\s*(?:[-*+]\s|\d+\.\s)")
    lines = open(args.file, "r", encoding="utf-8").read().split("\n")
    seen, fixed = [], 0
    for i, ln in enumerate(lines):
        if not LIST_RE.match(ln):
            continue
        s = ln.strip()
        if len(s) < args.min_len:
            continue
        for prev in seen:
            r = difflib.SequenceMatcher(None, s, prev).ratio()
            if args.threshold <= r < 1.0:
                lines[i] = ln.replace(s, prev)
                fixed += 1
                if args.verbose:
                    print("  [%.0f%%] %s\n      -> %s" % (r * 100, s[:70], prev[:70]))
                break
        else:
            seen.append(s)
    open(args.file, "w", encoding="utf-8").write("\n".join(lines))
    print("统一清单项 %d 处 -> %s" % (fixed, args.file))
    print("注：只处理列表行。正文段落不在范围内 —— 相似度高的正文往往是不同概念，"
          "统一等于制造错误。")


def cmd_join(args):
    run_dir = args.run_dir
    mf = json.load(open(os.path.join(run_dir, "manifest.json"), "r", encoding="utf-8"))
    parts, missing, notes = [], [], []
    for it in mf["chunks"]:
        p = os.path.join(run_dir, it["target"])
        if not os.path.exists(p):
            missing.append(it["target"])
            continue
        raw = open(p, "r", encoding="utf-8").read().strip()
        note = strip_tail_notes(raw)
        if note != raw:                       # 这一块自带了「翻译说明」附录
            notes.append((it["index"], raw[len(note):].strip()))
        parts.append(note.strip())
    if missing:
        print("缺少译文，无法合并：%s" % ", ".join(missing))
        sys.exit(1)
    if notes:
        # 每块的翻译说明如果留在原地，会变成散落在正文中间的一堆 ## 翻译说明。
        # 抽出来统一放到文末，标注来自第几块
        buf = ["", "# 翻译说明（各块的临时取舍）", ""]
        for idx, body in notes:
            buf.append("### 来自第 %d 块" % (idx + 1))
            buf.append("")
            buf.append("\n".join(ln for ln in body.split("\n")
                                 if not ln.strip().startswith("## ")))
            buf.append("")
        parts.append("\n".join(buf))
    out = args.out or os.path.join(run_dir, "final.zh.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n\n".join(parts) + "\n")
    print("合并 %d 块 → %s" % (len(parts), out))
    print("合并后请通读一遍衔接处：锚点是用来对齐的，不代表衔接一定正确。")


def cmd_conservation(args):
    """交付前守恒校验：对比源文与译文的结构计数（标题/链接/图片/引用/围栏/$$块）。

    译文先截掉「翻译说明」类附录（主 agent 追加的元信息，不计入守恒）。
    """
    APPENDIX_KEYS = ("翻译说明", "未确定", "临时取舍")

    def body_lines(path):
        out = []
        for l in open(path, encoding="utf-8").read().splitlines():
            m = re.match(r"^#{1,6}\s*(.*)", l)
            if m and any(k in m.group(1) for k in APPENDIX_KEYS):
                break
            out.append(l)
        return out

    def stats(lines):
        t = "\n".join(lines)
        return {
            "标题": sum(1 for l in lines if l.startswith("#")),
            "链接": len(re.findall(r"\]\(http[^)]*\)", t)),
            "图片": sum(1 for l in lines if l.lstrip().startswith("![")),
            "引用行": sum(1 for l in lines if l.lstrip().startswith(">")),
            "围栏行": sum(1 for l in lines if l.lstrip().startswith("```")),
        }

    s_lines, z_lines = body_lines(args.source), body_lines(args.zh)
    s, z = stats(s_lines), stats(z_lines)
    width = max(len(k) for k in s)
    ok = True
    print(f"{'项':{width}s}  源   译")
    for k in s:
        bad = s[k] != z[k]
        ok = ok and not bad
        print(f"{k:{width}s}  {s[k]:>3d}  {z[k]:>3d}  {'<<< 不一致' if bad else 'OK'}")

    # $$ 公式块：计数 + 逐块字节级对比
    s_blocks = re.findall(r"\$\$(.+?)\$\$", "\n".join(s_lines), re.S)
    z_blocks = re.findall(r"\$\$(.+?)\$\$", "\n".join(z_lines), re.S)
    bad = len(s_blocks) != len(z_blocks)
    ok = ok and not bad
    print(f"{'$$块':{width}s}  {len(s_blocks):>3d}  {len(z_blocks):>3d}  "
          f"{'<<< 不一致' if bad else 'OK'}")
    if not bad and s_blocks:
        same = sum(1 for a, b in zip(s_blocks, z_blocks) if a == b)
        print(f"  公式块字节级一致: {same}/{len(s_blocks)}")
        ok = ok and same == len(s_blocks)

    print("守恒校验: 通过" if ok else "守恒校验: 失败 ← 逐项排查后再交付")
    if not ok:
        raise SystemExit(1)


def main():
    ap = argparse.ArgumentParser(description="长文分块翻译")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("count", help="统计 token 数")
    p.add_argument("md")
    p.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    p.set_defaults(func=cmd_count)

    p = sub.add_parser("cost", help="翻译成本估算（开翻前必跑）")
    p.add_argument("md")
    p.add_argument("--overhead", type=int, default=CALL_OVERHEAD,
                   help="每次调用的固定开销（实测 ~5500，bundle 结构变更后修正）")
    p.add_argument("--factor", type=float, default=1.0,
                   help="源量系数（digest 等减量模式分离/压缩后的实际占比，默认 1.0）")
    p.set_defaults(func=cmd_cost)

    p = sub.add_parser("split", help="切块")
    p.add_argument("md")
    p.add_argument("--out-dir", required=True)
    p.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS)
    p.add_argument("--min-tokens", type=int, default=DEFAULT_MIN_TOKENS)
    p.set_defaults(func=cmd_split)

    p = sub.add_parser("context", help="打印第 N 块的翻译包（含上一块锚点）")
    p.add_argument("run_dir")
    p.add_argument("--index", type=int, required=True)
    p.set_defaults(func=cmd_context)

    p = sub.add_parser("bundle", help="产出自包含任务包（隔离用）")
    p.add_argument("run_dir")
    p.add_argument("--index", type=int, required=True)
    p.set_defaults(func=cmd_bundle)

    p = sub.add_parser("reconcile", help="统一合并后重复句的不同译法")
    p.add_argument("file")
    p.add_argument("--threshold", type=float, default=0.96)
    p.add_argument("--min-len", type=int, default=25)
    p.add_argument("--verbose", action="store_true")
    p.set_defaults(func=cmd_reconcile)

    p = sub.add_parser("join", help="合并所有译文")
    p.add_argument("run_dir")
    p.add_argument("-o", "--out")
    p.set_defaults(func=cmd_join)

    p = sub.add_parser("conservation", help="交付前守恒校验（源文 vs 译文结构计数）")
    p.add_argument("source")
    p.add_argument("zh")
    p.set_defaults(func=cmd_conservation)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
