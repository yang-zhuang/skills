r"""verify_blocks.py -- 围栏代码块逐字节溯源校验（把一次性人工比对固化成机制）。

用法:
    python verify_blocks.py <task_pack_dir> <md_file>

    <md_file>  相对 <task_pack_dir> 的路径，如 draft/draft.md 或 revised/revised-r1.md

为什么需要它:
    方针铁律 2 规定代码 / 命令 / 提示词"要么一字不改地保留，要么整块删除"，此前每个任务包交付前都是
    编排器临时写一段一次性脚本去比对，没有落到 `scripts/` 里——属于该由机制承担却没承担的活。
    本脚本把它变成每次都跑的标准环节。

校验内容:
1. **溯源**：稿件里每个围栏块的内容都必须能在 `input/source.md` 里找到，两档判定：
   - `逐字节` —— 原文里存在完全相同的字符串（最强证据）；
   - `去转义` —— 两边同时剥掉 markdown 转义（`\_` `\*` `\[` `\]`）后才相同。出现这档是因为
     同一段字 wording 在原文落在段落里（markdownify 会把 `_` 转义成 `\_`）、在稿件里落进了
     围栏块（围栏内不解析转义，`repair_math.py` 会还原），并非稿件改写了内容。
   - `行级` —— 整块找不到，但块内**每一行**都能在原文里单独找到（同样允许去转义后比对）。
     出现这档是因为方针 B7 强制伪代码/提示词必须进围栏，而 PaddleOCR 转出的原文伪代码是
     「空行分隔的散行」（`Algorithm 1 ...` 后每步一行、行间有空行），起草把它收进围栏必然
     改变换行结构。两害相权：放行换行规范化、守住逐行内容不改，比让 B7 与铁律 2 直接打架更合理。
     行级档会在报告里逐块列出，可见但不静默。
   三档都算通过；都找不到 → 失败（内容被改写，违反铁律 2）。
2. **语言标记**：列出稿件用到的围栏语言标记（```python / ```json / 无标记等），供评审与人工校对——
   标记写错（把一段 prompt 标成 ```python）不算内容错误，但会出现在本报告里被看见，便于对齐方针 B7。
非零退出 = 溯源失败。
"""
import re
import sys
from pathlib import Path

FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})(.*)$")
ESCAPE = re.compile(r"\\([_*\[\]])")


def fenced_blocks(text):
    """返回 [{"lang": str, "content": str}]，不解析嵌套，按围栏开关切换。"""
    blocks = []
    open_mark = None
    buf = []
    for line in text.split("\n"):
        m = FENCE.match(line)
        if open_mark is None:
            if m:
                open_mark = m.group(1)
                lang = m.group(2).strip() or "(无)"
                buf = [lang]
            continue
        close = line.strip()
        if close and set(close) <= {open_mark[0]} and len(close) >= len(open_mark):
            lang = buf[0]
            blocks.append({"lang": lang, "content": "\n".join(buf[1:])})
            open_mark = None
            continue
        buf.append(line)
    return blocks


def locate(source, block):
    """在原文里定位块内容，返回 '逐字节' / '去转义' / '行级' / None。"""
    body = block.strip("\n")
    if not body.strip():
        return "空块"
    if body in source:
        return "逐字节"
    src_unescaped = ESCAPE.sub(r"\1", source)
    if ESCAPE.sub(r"\1", body) in src_unescaped:
        return "去转义"
    lines = [l for l in body.split("\n") if l.strip()]
    if lines and all(l in source or ESCAPE.sub(r"\1", l) in src_unescaped for l in lines):
        return "行级"
    return None


def main():
    if len(sys.argv) != 3:
        sys.exit("用法: python verify_blocks.py <task_pack_dir> <md_file>")
    pack = Path(sys.argv[1])
    md_path = pack / sys.argv[2]
    source = pack / "input" / "source.md"
    if not md_path.is_file():
        sys.exit(f"文件不存在: {md_path}")
    if not source.is_file():
        sys.exit(f"缺少原文（无法溯源）: {source}")

    src_text = source.read_text(encoding="utf-8")
    blocks = fenced_blocks(md_path.read_text(encoding="utf-8"))
    failed = []
    tiers = {"逐字节": 0, "去转义": 0, "行级": 0}
    for i, b in enumerate(blocks, 1):
        head = b["content"].strip().split("\n")[0][:60]
        tier = locate(src_text, b["content"])
        if tier is None:
            failed.append(f"第 {i} 块（{b['lang']}）内容在原文里找不到，疑似改写: {head}")
            continue
        tiers[tier] = tiers.get(tier, 0) + 1
        if tier in ("去转义", "行级"):
            note = "去转义后一致" if tier == "去转义" else "行级逐行一致（换行规范化）"
            print(f"  - 第 {i} 块（{b['lang']}）{note}: {head}")

    langs = {}
    for b in blocks:
        langs[b["lang"]] = langs.get(b["lang"], 0) + 1
    print(f"围栏块 {len(blocks)} 个：逐字节 {tiers.get('逐字节', 0)}、去转义 {tiers.get('去转义', 0)}、"
          f"行级 {tiers.get('行级', 0)}、失败 {len(failed)}；语言标记 {langs or '{}'}")

    if failed:
        print("BLOCK VERIFY FAILED:")
        for f in failed:
            print(f"  - {f}")
        sys.exit(1)
    print("ok: 全部围栏块内容均可在原文溯源")


if __name__ == "__main__":
    main()
