r"""extract_refs.py -- 从原文抽取全部外部链接，产出"候选引用表"。

用法:
    python extract_refs.py <source.md> <out.json>

    <source.md>  convert.py 产出的原文 markdown
    <out.json>   候选引用表（任务包里固定为 input/refs-candidates.json，
                 由 build_task_pack.py 自动生成，起草角色只读不算原始材料）

为什么要有它（机制/判断分离）:
    方针铁律 10 要求"保留下来的外部内容必须留下可追溯的出处"，而 URL 只能**逐字节来自原文**——
    起草方若凭记忆或搜索补链接，就是在编造。本脚本把原文里每一个 http(s) 链接连同它的锚文本、
    上下文位置一次性抽出来，起草方只做判断（哪些内容留下了→该挂哪几条），不做"找 URL"这种
    机制性劳动，也无法自造（自造的 URL 会被 verify_citations.py 拦下）。

产出格式（JSON 数组，按首次出现顺序）:
    [
      {
        "n": 1,
        "url": "https://arxiv.org/abs/2303.17580",
        "anchor": "Shen et al. 2023",     # markdown 锚文本；裸 URL 则为 ""
        "bare": false,                     # true = 原文里是没有锚文本的裸 URL
        "count": 1,                        # 原文出现次数
        "first_line": 42,                  # 首次出现的行号（1 起）
        "context": "...",                  # 首次出现所在行的前后文（截 160 字）
        "in_image": false                  # 是否出现在 ![]() 里（此类通常是图，不是正文资料）
      }
    ]

不做的事: 不联网校验 URL 是否可达（那是读者的事）；不去重归一（域名相同路径不同算两条，判断交给角色）。
去重规则: 同一 URL 只保留首次出现那一条，count 记总次数；多次出现且锚文本不同时 anchors 列出全部，
anchor 字段取首次的锚文本。URL 必须原样复制本表，称呼允许按方针铁律 10 从 anchors/context 里取。
"""
import json
import re
import sys
from pathlib import Path

LINK = re.compile(r"(!?)\[([^\]]{0,200})\]\(\s*(https?://[^)\s]+)\s*\)")
BARE_URL = re.compile(r"(?<![(\[])\bhttps?://[^\s)>\]]+")
CONTEXT = 160


def collect_refs(text):
    """抽取原文全部外部链接，返回按首次出现顺序的 dict 列表（格式见模块 docstring）。"""
    lines = text.split("\n")
    entries = {}
    order = []
    for i, line in enumerate(lines, 1):
        consumed = set()
        for m in LINK.finditer(line):
            bang, anchor, url = m.group(1), m.group(2).strip(), m.group(3)
            consumed.add(m.start(3))
            _push(entries, order, url, url=url, anchor=anchor, bare=False,
                  line_no=i, in_image=bool(bang))
        for m in BARE_URL.finditer(line):
            if m.start() in consumed:
                continue
            url = m.group(0).rstrip(".,;:!?)）】」")
            _push(entries, order, url, url=url, anchor="", bare=True,
                  line_no=i, in_image=False)
    out = []
    for idx, url in enumerate(order, 1):
        e = entries[url]
        first_line = e["lines"][0]
        ctx_lines = lines[max(0, first_line - 1): first_line]
        out.append({
            "n": idx,
            "url": url,
            "anchor": e["anchor"],
            "anchors": sorted(set(a for a in e["anchors"] if a)),
            "bare": e["bare"],
            "count": len(e["lines"]),
            "lines": e["lines"],
            "first_line": first_line,
            "in_image": e["in_image"],
            "context": " ".join(ctx_lines)[:CONTEXT],
        })
    return out


def _push(entries, order, key, *, url, anchor, bare, line_no, in_image):
    if key not in entries:
        entries[key] = {"url": url, "anchor": anchor, "anchors": [], "bare": bare,
                        "lines": [], "in_image": in_image}
        order.append(key)
    e = entries[key]
    e["lines"].append(line_no)
    if anchor:
        e["anchors"].append(anchor)
    if anchor and not e["anchor"]:
        e["anchor"] = anchor


def main():
    if len(sys.argv) != 3:
        sys.exit("用法: python extract_refs.py <source.md> <out.json>")
    src = Path(sys.argv[1])
    if not src.is_file():
        sys.exit(f"文件不存在: {src}")
    refs = collect_refs(src.read_text(encoding="utf-8"))
    out = Path(sys.argv[2])
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(refs, ensure_ascii=False, indent=2), encoding="utf-8")
    n_img = sum(1 for r in refs if r["in_image"])
    n_bare = sum(1 for r in refs if r["bare"])
    print(f"done: {len(refs)} 条链接 -> {out}（图中 {n_img}、裸 URL {n_bare}）")


if __name__ == "__main__":
    main()
