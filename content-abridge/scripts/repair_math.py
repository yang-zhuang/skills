"""repair_math.py -- 修复 markdownify 对数学区与代码区的转义污染。

用法:
    python repair_math.py <file.md> [<file2.md> ...]

背景（实测，见 scripts/README.md 附录 A）:
    convert.py 用 markdownify 把 HTML 转 markdown。markdownify 为防止正文里的
    markdown 元字符被解释，会把 `_` 转义成 `\\_`、`*` 转义成 `\\*`。它不认识 `$...$`
    里的 LaTeX，于是作者写的 `$a_t$` 被改写成了 `$a\\_t$`。

    KaTeX 实测（node + katex 渲染 `$a_t$` vs `$a\\_t$`）:
        a_t   -> <msub><mi>a</mi><mi>t</mi></msub>       正常下标
        a\\_t  -> <mi>a</mi><mi mathvariant="normal">_</mi><mi>t</mi>   字面下划线字符

    即 `\\_` 在数学环境里渲染成**一个下划线字符**，而不是下标运算符：下标塌陷成相邻
    的普通字符，上标（\\^{...}、^{...}）与之同处的结构也随之排错。MathJax 行为相同。
    同一问题落在行内代码与围栏代码块里时，`\\_` 会原样显示在代码文本中（prompt 模板、
    变量名都被污染）。

做法（机制、确定性、幂等）:
    逐字符扫出四类"保护区域"，只在区域内把 `\\_`->`_`、`\\*`->`*`（代码区另解
    `\\[ ` `\\]` `\\~`）：
        1. 围栏代码块（``` / ~~~，整块）
        2. 行内代码 span（`...`，含多反引号）
        3. 行内数学 `$...$`（配对到下一个 `$`，内容不得跨空行）
        4. 块级数学 `$$...$$`（可跨行）
    区域外一个字符都不动——正文里的 `\\_` 是 markdownify 有意保留的转义，去掉会
    意外开启斜体。
    LaTeX 真正的命令（\\mathbb、\\{、\\}、\\\\ 等）不在解转义集合内，绝不触碰。

输出:
    原地改写每个文件，打印替换计数；重复执行结果不变。
    末尾额外列出**疑似裸上下标**（数学/代码区之外出现 `X^2`、`10^-3` 形态的行）仅供
    起草角色判断，脚本不改写——是否包 `$...$` 属于判断，不是机制。
"""
import re
import sys
from pathlib import Path

FENCE_OPEN = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
MATH_UNESCAPE = "_*"          # 数学区：只解 markdownify 会转义的两个
CODE_UNESCAPE = "_*[]~"       # 代码区：多一种也是 markdownify 的转义，且代码里不可能是 LaTeX

# 疑似裸上下标：数学/代码区之外，非空白字符紧跟着 ^ 再跟 {word} 或数字
BARE_SUP = re.compile(r"[A-Za-z0-9%)\]]\^(?:\{[^{}]{1,20}\}|-?\d+(?:\.\d+)?)")


def _fenced_blocks(s):
    """返回所有围栏代码块的 (start, end) 字符区间，end 为右开。"""
    blocks = []
    start = None
    marker = None
    pos = 0
    n = len(s)
    while pos <= n:
        nl = s.find("\n", pos)
        line_end = n if nl == -1 else nl
        line = s[pos:line_end]
        m = FENCE_OPEN.match(line)
        if start is None:
            if m:
                start = pos
                marker = m.group(1)
        else:
            close = line.strip()
            if close and set(close) <= {marker[0]} and len(close) >= len(marker):
                blocks.append((start, line_end))
                start = None
                marker = None
        if nl == -1:
            break
        pos = line_end + 1
    if start is not None:  # 未闭合的围栏：保守起见不保护后续内容
        pass
    return blocks


def _protected_spans(s, blocks):
    """在未被围栏覆盖的文本里，扫出行内代码 span 与数学 span 的 (start, end)。"""
    spans = []
    blocked = blocks
    i = 0
    n = len(s)

    def in_fenced(p):
        return any(a <= p < b for a, b in blocked)

    while i < n:
        if in_fenced(i):
            i += 1
            continue
        ch = s[i]
        if ch == "\\" and i + 1 < n:
            i += 2  # 跳过转义字符本身，避免把 \\$ 当数学起始
            continue
        if ch == "`":
            j = i
            while j < n and s[j] == "`":
                j += 1
            ticks = s[i:j]
            close = s.find(ticks, j)
            close = -1 if close == -1 else close
            # 行内代码不跨围栏、不跨段落
            nl = s.find("\n\n", j)
            if close != -1 and (nl == -1 or close < nl) and not in_fenced(close):
                spans.append((i, close + len(ticks), "code"))
                i = close + len(ticks)
                continue
            i = j
            continue
        if ch == "$":
            delim = "$$" if s.startswith("$$", i) else "$"
            start = i + len(delim)
            if delim == "$$":
                close = s.find("$$", start)
                if close != -1:
                    spans.append((i, close + 2, "math"))
                    i = close + 2
                    continue
            else:
                # 行内数学：截至下一个 $ 或空行（防止正文里孤立 $ 造成超长误配对）
                nl = s.find("\n\n", start)
                limit = n if nl == -1 else nl
                close = s.find("$", start)
                if close != -1 and close < limit and close > start:
                    spans.append((i, close + 1, "math"))
                    i = close + 1
                    continue
            i += 1
            continue
        i += 1
    return spans


def repair_markdown_escapes(text):
    """返回 (修复后文本, 替换次数)。纯函数，幂等。"""
    blocks = _fenced_blocks(text)
    spans = _protected_spans(text, blocks)
    regions = [(a, b, "code") for a, b in blocks] + spans
    regions.sort()
    out = []
    total = 0
    prev = 0
    for a, b, kind in regions:
        if a < prev:  # 区间重叠时跳过（例如数学跨越围栏起始）
            continue
        out.append(text[prev:a])
        seg = text[a:b]
        charset = MATH_UNESCAPE if kind == "math" else CODE_UNESCAPE
        fixed, cnt = _unescape(seg, charset)
        total += cnt
        out.append(fixed)
        prev = b
    out.append(text[prev:])
    return "".join(out), total


def _unescape(seg, charset):
    """把 seg 里 \\x (x in charset) 还原为 x。不碰 LaTeX 命令（\\mathbb 等不在集合内）。"""
    res = []
    cnt = 0
    i = 0
    n = len(seg)
    while i < n:
        if seg[i] == "\\" and i + 1 < n and seg[i + 1] in charset:
            res.append(seg[i + 1])
            cnt += 1
            i += 2
            continue
        res.append(seg[i])
        i += 1
    return "".join(res), cnt


def bare_superscripts(text, blocks, spans):
    """数学/代码区之外的疑似裸上标，返回 [(行号, 片段)]。只报告，不改写。"""
    hits = []
    lines = text.split("\n")
    off = 0
    for idx, line in enumerate(lines, 1):
        line_start, line_end = off, off + len(line)
        off = line_end + 1
        for m in BARE_SUP.finditer(line):
            p = line_start + m.start()
            if any(a <= p < b for a, b in blocks) or any(a <= p < b for a, b, _ in spans):
                continue
            hits.append((idx, m.group(0)))
    return hits


def main():
    if len(sys.argv) < 2:
        sys.exit("用法: python repair_math.py <file.md> [<file2.md> ...]")
    for arg in sys.argv[1:]:
        p = Path(arg)
        if not p.is_file():
            print(f"[skip] 不是文件: {p}")
            continue
        text = p.read_text(encoding="utf-8")
        fixed, cnt = repair_markdown_escapes(text)
        fixed2, cnt2 = repair_markdown_escapes(fixed)
        assert fixed2 == fixed and cnt2 == 0, "幂等性自检失败"
        p.write_text(fixed, encoding="utf-8")
        # 报告必须基于修复后的文本重新定位：删掉反斜杠后字符偏移已整体移动
        hits = bare_superscripts(fixed, _fenced_blocks(fixed), _protected_spans(fixed, _fenced_blocks(fixed)))
        print(f"{p}: 解转义 {cnt} 处")
        if hits:
            seen = set()
            for line_no, frag in hits:
                if frag in seen:
                    continue
                seen.add(frag)
                print(f"    [提示] 数学/代码区外的疑似裸上标 {frag!r} 首次出现在 line {line_no}（是否包 $...$ 属判断，脚本不改）")


if __name__ == "__main__":
    main()
