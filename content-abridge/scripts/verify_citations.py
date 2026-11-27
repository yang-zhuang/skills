r"""verify_citations.py -- 引用合规性校验（方针铁律 10 的机械化检查）。

用法:
    python verify_citations.py <task_pack_dir> <md_file>

    <md_file>  相对 <task_pack_dir> 的路径，如 draft/draft.md 或 revised/revised-r1.md

校验三条铁律 10 的硬约束。方针允许判断的地方不算错，只有"能机器判定"的越界才打回：

1. **URL 必须逐字节来自原文**：稿件里出现的每一个 http(s) 链接，都必须能在
   `input/source.md` 里原样找到。找不到 = 起草方自造/替换/补全的链接 —— 违反铁律 1（不添加）。
2. **编号与条目一一对应**：正文里的每个 `[n]` 必须在文末 `## 参考资料` 节有对应条目；
   反过来，条目里的每个编号也要在正文被引用过（不许留没人引的"装饰性参考书目"）。
3. **编号连续且按首次出现顺序**：`1..N` 无缺号、无重复号；同一 URL 不得占两个编号。

文末节与条目的格式（角色侧的输出契约，本脚本按此解析）:

    ## 参考资料

    - [1] Shen et al. 2023: HuggingGPT <https://arxiv.org/abs/2303.17580>
    - [2] Chain of Hindsight <https://arxiv.org/abs/2202.10276>

正文引用标记：`[1]`、`[2][3]` 之类方括号数字。标记与行内 markdown 链接可以同时存在。
`assets/` 开头的相对路径不是 URL，不参与校验。
非零退出 = 校验失败。
"""
import re
import sys
from pathlib import Path

URL = re.compile(r"https?://[^\s)>\]]+")
MD_LINK_URL = re.compile(r"!?\[[^\]]*\]\(\s*(https?://[^)\s]+)")
REF_HEADING = re.compile(r"^\s{0,3}#{2,6}\s*参考资料\s*$", re.M)
REF_ENTRY = re.compile(r"^\s*[-*]\s*\[(\d+)\]\s+(.*?)\s*<?(https?://[^>\s]+?)>?\s*$", re.M)
CITE = re.compile(r"(?<!\[)\[(\d+)\](?![(\s]*\()")


FENCE_BLOCK = re.compile(r"(?ms)^\s{0,3}(?:```|~~~).*?^\s{0,3}(?:```|~~~)")
INLINE_CODE = re.compile(r"`[^`\n]*`")


def _strip_code(text):
    """把围栏块与行内代码替换成等长空格（保留换行）：`tokens[0]` 这类下标不应被当成引用标记。"""

    def blank(m):
        return "\n".join(" " * len(l) for l in m.group(0).split("\n"))

    text = FENCE_BLOCK.sub(blank, text)
    return INLINE_CODE.sub(lambda m: " " * len(m.group(0)), text)


def _strip_ref_section(text):
    m = REF_HEADING.search(text)
    if not m:
        return text, ""
    # 参考资料节到文末（或下一个同级及更高标题）为止
    rest = text[m.end():]
    nxt = re.search(r"^\s{0,3}#{1,6}\s", rest, re.M)
    section = rest if not nxt else rest[:nxt.start()]
    body = text[:m.start()] + (rest[nxt.start():] if nxt else "")
    return body, section


def main():
    if len(sys.argv) != 3:
        sys.exit("用法: python verify_citations.py <task_pack_dir> <md_file>")
    pack = Path(sys.argv[1])
    md_path = pack / sys.argv[2]
    source = pack / "input" / "source.md"
    if not md_path.is_file():
        sys.exit(f"文件不存在: {md_path}")
    if not source.is_file():
        sys.exit(f"缺少原文（无法核对 URL 出处）: {source}")

    source_text = source.read_text(encoding="utf-8")
    md_text = md_path.read_text(encoding="utf-8")
    errors = []

    # 1. URL 出处核对
    urls = [m.group(0) for m in URL.finditer(md_text)]
    urls += [m.group(1) for m in MD_LINK_URL.finditer(md_text)]
    urls = list(dict.fromkeys(urls))
    for u in urls:
        if u not in source_text:
            errors.append(f"URL 未逐字节出现在原文里（疑似自造/替换）: {u}")

    # 2. 编号与条目（在"剥掉代码块"的视图里扫：tokens[0] 这类下标不算引用）
    body, section = _strip_ref_section(_strip_code(md_text))
    cited = [int(m.group(1)) for m in CITE.finditer(body)]
    entries = [(int(m.group(1)), m.group(3)) for m in REF_ENTRY.finditer(section)]

    entry_nums = [n for n, _ in entries]
    entry_urls = [u for _, u in entries]
    if cited and not section.strip():
        errors.append(f"正文用了 {len(cited)} 处数字引用，但没有 ## 参考资料 节")
    if not cited and section.strip():
        errors.append("有 ## 参考资料 节，但正文没有任何 [n] 引用标记（不许列无人引用的参考书目）")

    for n in cited:
        if n not in entry_nums:
            errors.append(f"正文引用 [{n}] 在 ## 参考资料 里没有对应条目")
    for n in entry_nums:
        if n not in cited:
            errors.append(f"参考资料条目 [{n}] 在正文里没有被引用")

    if entry_nums:
        expect = list(range(1, max(entry_nums) + 1))
        if entry_nums != expect:
            dup = sorted({n for n in entry_nums if entry_nums.count(n) > 1})
            missing = sorted(set(expect) - set(entry_nums))
            detail = []
            if dup:
                detail.append(f"编号重复: {dup}")
            if missing:
                detail.append(f"缺号: {missing}")
            if entry_nums != sorted(entry_nums):
                detail.append("条目顺序未按编号排列")
            errors.append("参考资料编号异常 -> " + "；".join(detail))

    for u in set(entry_urls):
        if entry_urls.count(u) > 1:
            errors.append(f"同一 URL 被给了多个编号（应合并为一条）: {u}")

    # 3. 首次出现顺序（[n] 应按编号先后第一次登场）
    order = list(dict.fromkeys(cited))
    if entry_nums and order != sorted(set(order)):
        errors.append(f"正文 [n] 未按编号升序首次出现: 实际顺序 {order}")

    if errors:
        print("CITATION VERIFY FAILED:")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    print(f"ok: URL {len(urls)} 条全部逐字节源自原文；编号引用 {len(cited)} 处，"
          f"参考资料条目 {len(entries)} 条一一对应")


if __name__ == "__main__":
    main()
