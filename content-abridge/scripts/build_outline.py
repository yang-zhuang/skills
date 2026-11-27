r"""build_outline.py -- 解析 source.md 标题结构，产出长文档裁决用的目录 outline.json。

用法:
    python build_outline.py <source.md路径> <输出outline.json路径>

    对全文或剥离版均可跑（幂等，同一输入产出字节一致的输出）。
    长文档流程中跑两次：trim 前对全文跑（trimmer 第一轮看），
    execute_trim 之后对剥离版重跑（trimmer 第二轮看）。

条目语义（§10.3-B）:
    id      唯一键 = "L<起始行号>"（1-based，指本文件中的标题行）。
            同名标题会撞名（全量 survey 实测 Output format ×146），故机器引用一律用 id。
    path    人读的面包屑（"3.2/Probabilistic Vulnerability Modeling"），仅展示用。
    level   标题级别（1-6）。
    title   标题原文（去行内锚点 [#](#...)）。
    chars   该节**子树**总字符量（含嵌套子节、不含标题行本身）——即删除该节时
            实际消失的字符量，trimmer 做预算看的就是它。
    figs    子树内 ![](assets/...) 行数（删除影响面，铁律 1：含方法图的节是高危对象）。
    first   该节自有正文（不含子节）的首句预览，截 120 字符；无正文为 null。
    last    自有正文的末句预览，截 120 字符；无正文为 null。
    flags   机械打标、AI 可见、不静默处理：
            parent        有子节（自有正文可能为空，属正常结构，不算 no-body）
            no-body       无自有正文且无子节（OCR 断裂嫌疑）
            in-appendix   附录内（伪标题高发区）：本节或任一祖先标题匹配
                          ^appendix / 字母编号式（A、A.2）；后代继承
            numbered      标题以数字编号开头（^\d+(\.\d+)*\s）
            suspect-split 标题超 100 字符，或标题行无句末标点且下一非空行以
                          小写字母开头（跨行断裂嫌疑，只打标不猜）

句子切分规则（防止 preview 自己变成噪音）:
    先屏蔽 et al. / Fig. / Eq. / Sec. / 小数点 / $...$ 区间 / 行内代码区间，
    再按"句点+空白+大写"切句。
"""
import json
import re
import sys
from pathlib import Path

FENCE = re.compile(r"^\s{0,3}(```|~~~)")
HEAD = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
ANCHOR = re.compile(r"\s*\[#\]\(#[^)]*\)\s*")
NUM_PREFIX = re.compile(r"^\d+(\.\d+)*\s+")
LETTER_PREFIX = re.compile(r"^[A-Z](\.\d+)*\s+\S")
APPENDIX_HINT = re.compile(r"(?i)^appendix\b")
SHIELD = re.compile(
    r"(\bet al\.|Fig\.|Eq\.|Sec\.|\d+\.\d+|\$[^$\n]*\$|`[^`\n]*`)")
SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")
MAX_TITLE = 100
MAX_PREVIEW = 120


def _split_sentences(body):
    """屏蔽保护区内句点后按"句点+空白+大写"切句，返回原文本的句子列表。"""
    shields = [m.span() for m in SHIELD.finditer(body)]

    def shielded(a, b):
        return any(sa < b and a < sb for sa, sb in shields)

    cuts, pos = [0], 0
    for m in SENT_SPLIT.finditer(body):
        if not shielded(m.start(), m.end()):
            cuts.append(m.end())
    cuts.append(len(body))
    return [body[a:b].strip() for a, b in zip(cuts, cuts[1:]) if body[a:b].strip()]


def _preview(body):
    """自有正文 → (first, last)，各截 120 字符；无正文返回 (None, None)。"""
    sents = _split_sentences(body)
    if not sents:
        return None, None
    first = sents[0][:MAX_PREVIEW]
    last = sents[-1][:MAX_PREVIEW] if len(sents) > 1 else None
    return first, last


def build_outline(source: Path):
    lines = source.read_text(encoding="utf-8").split("\n")
    n = len(lines)

    # 扫描标题行（跳过围栏代码块内部——代码注释里的 # 不是标题）
    headings = []       # (line_idx0, level, title)
    fence_open = False
    for i, ln in enumerate(lines):
        if FENCE.match(ln):
            fence_open = not fence_open
            continue
        if fence_open:
            continue
        m = HEAD.match(ln)
        if m:
            headings.append((i, len(m.group(1)), ANCHOR.sub(" ", m.group(2)).strip()))

    sections = []
    for k, (start, level, title) in enumerate(headings):
        # 节体范围：到下一个级别 <= 自己的标题为止
        end = n
        for j in range(k + 1, len(headings)):
            if headings[j][1] <= level:
                end = headings[j][0]
                break
        # 自有正文 = 标题行后到第一个子节标题（若有，即下一个标题级别更深）；否则到节尾
        own_end = end
        if k + 1 < len(headings) and headings[k + 1][0] < end \
                and headings[k + 1][1] > level:
            own_end = headings[k + 1][0]
        own_lines = lines[start + 1:own_end]
        own_body = "\n".join(own_lines).strip()
        sub_lines = lines[own_end:end] if own_end < end else []

        # 子树统计（chars/figs = 删除影响面；不含本节标题行）
        subtree_lines = lines[start + 1:end]
        chars = sum(len(l.strip()) for l in subtree_lines)
        figs = sum(1 for l in subtree_lines if re.match(r"^\s*!\[\]\(assets/", l))

        first, last = _preview(own_body)
        has_sub = own_end < end
        flags = []
        if has_sub:
            flags.append("parent")
        elif not own_body:
            flags.append("no-body")
        if NUM_PREFIX.match(title):
            flags.append("numbered")
        nxt = next((l for l in lines[start + 1:] if l.strip()), "")
        if len(title) > MAX_TITLE or (
                not title.endswith((".", "!", "?", ":", ")")) and
                re.match(r"^[a-z]", nxt)):
            flags.append("suspect-split")

        sections.append({
            "id": f"L{start + 1}",
            "path": title,
            "level": level,
            "title": title,
            "chars": chars,
            "figs": figs,
            "first": first,
            "last": last,
            "flags": flags,
            "start_line": start + 1,       # 1-based，含标题行
            "end_line": end,               # 1-based，含到下一同级标题前一行
            "_own_chars": sum(len(l.strip()) for l in own_lines),
        })

    # in-appendix：本节或任一祖先命中附录特征，后代继承
    in_appendix = False
    for sec in sections:
        t = sec["title"]
        if APPENDIX_HINT.match(t) or (LETTER_PREFIX.match(t) and sec["level"] <= 3):
            in_appendix = True
        elif sec["level"] <= 2:
            in_appendix = False        # 回到正文级标题
        if in_appendix and "in-appendix" not in sec["flags"]:
            sec["flags"].append("in-appendix")

    for sec in sections:
        sec.pop("_own_chars")
    # path 补面包屑（祖先标题/自身标题）
    stack = []
    for sec in sections:
        while stack and stack[-1][0] >= sec["level"]:
            stack.pop()
        ancestors = [t for _, t in stack]
        sec["path"] = "/".join(ancestors + [sec["title"]])
        stack.append((sec["level"], sec["title"]))

    return {
        "source": str(source),
        "total_chars": sum(len(l.strip()) for l in lines),
        "sections": sections,
    }


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    src, out = Path(sys.argv[1]), Path(sys.argv[2])
    if not src.is_file():
        sys.exit(f"source 不存在: {src}")
    outline = build_outline(src)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(outline, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    ids = [s["id"] for s in outline["sections"]]
    assert len(ids) == len(set(ids)), "id 撞名（L行号应唯一），程序 bug"
    flagged = sum(1 for s in outline["sections"] if s["flags"])
    print(f"outline: {len(ids)} 节 / 全文 {outline['total_chars']} 字符，"
          f"带 flags {flagged} 节 -> {out}")


if __name__ == "__main__":
    main()
