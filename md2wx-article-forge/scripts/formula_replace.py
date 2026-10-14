"""整篇 md 的公式识别与图片化替换。

微信公众号不渲染 LaTeX，渲染前必须把公式全部转成图片。
公式的形态很多：行内 $...$、块级 $$...$$（同行或跨行成对）、
还可能夹在列表/引用里 —— 这种识别与替换是纯确定性工作，
交给 agent 现场发挥必然漏抓或错抓，所以写成脚本一次做对。

替换约定（与 render.py 的 alt 约定对应）：
    行内公式 $E=mc^2$        → ![inline](eqN.png)   走文字流，基线居中
    块级公式 $$...$$         → ![display](eqN.png)  独立段居中
    渲染器读 PNG 尺寸自动折算显示高度，无需手写尺寸

多行公式（一对 $$ 之间有换行）不支持：mathtext 无多行环境，
直接报错并给出行号，让作者拆成多个 $$ 块或降级 Unicode。

用法:
    python formula_replace.py <input.md> -o <output.md> --png-dir <公式图片目录>

产物：
    output.md       替换后的 md（图片引用为相对路径，与 png 同目录时直接可用）
    png-dir/eqN.png 每个公式一张图（N 按出现顺序编号）
    stdout JSON     清单：每个公式的类型/原文/图片/像素尺寸，供审计回溯

退出码：0 全部替换成功；1 有公式解析失败（未写 output.md）；2 参数错误
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from formula_to_png import render_formula  # noqa: E402

# 行内 $...$：前后不能是 $（避免吃进 $$），内部不允许换行
INLINE_RE = re.compile(r"(?<!\$)\$([^\$\n]+?)\$(?!\$)")


def extract_blocks(text: str) -> tuple[list[tuple[int, int, str]], list[tuple[int, str]]]:
    """找出全部块级公式。

    返回（块列表, 多行错误列表）。块 = (起始行号, 结束行号, latex)。
    支持 $$...$$ 同行闭合，也支持 $$ 开行 ... $$ 收行的跨行形式；
    跨行的报错 —— mathtext 没有多行环境。
    """
    blocks: list[tuple[int, int, str]] = []
    errors: list[tuple[int, str]] = []
    lines = text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        # 同行闭合：$$ ... $$
        m = re.search(r"\$\$(.+?)\$\$", line)
        if m:
            blocks.append((i, i, m.group(1)))
            i += 1
            continue
        # 开行：$$ 单独出现（可能后面还挂着文字则同行开闭已处理，这里只剩开定界符）
        if "$$" in line:
            close = next((j for j in range(i + 1, len(lines)) if "$$" in lines[j]), None)
            if close is None:
                errors.append((i + 1, "$$ 只有开定界符，没有收定界符"))
                i += 1
                continue
            errors.append((i + 1, f"跨行公式（第 {i + 1}~{close + 1} 行）："
                                  f"mathtext 不支持多行公式，请拆成多个 $$ 块或改用单行"))
            i = close + 1
            continue
        i += 1
    return blocks, errors


def replace_formulas(md: str, png_dir: Path, color: str = "#2e2e2e") -> tuple[str, list[dict], list[str]]:
    """把 md 里全部公式替换为图片引用。返回（新 md, 清单, 失败列表）。"""
    lines = md.split("\n")

    blocks, multi_errors = extract_blocks(md)
    if multi_errors:
        return "", [], [e[1] for e in multi_errors]

    manifest: list[dict] = []
    failures: list[str] = []
    counter = 0

    # 先处理块级：按行号从后往前替换，避免行号漂移
    for start, end, latex in sorted(blocks, key=lambda b: -b[0]):
        counter += 1
        name = f"eq{counter}.png"
        try:
            w, h = render_formula(latex.strip(), png_dir / name, color)
        except RuntimeError as e:
            failures.append(f"块级公式（第 {start + 1} 行）{latex.strip()[:40]}: {e}")
            counter -= 1
            continue
        lines[start] = re.sub(r"\$\$.+?\$\$", f"![display]({name})", lines[start])
        manifest.append({"index": counter, "type": "display", "latex": latex.strip(),
                         "png": name, "px": [w, h]})

    # 再逐行处理行内（此时块级 $$ 已被替换，不会误抓）
    text = "\n".join(lines)
    def sub_inline(m: re.Match) -> str:
        nonlocal counter
        counter += 1
        name = f"eq{counter}.png"
        try:
            w, h = render_formula(m.group(1).strip(), png_dir / name, color)
        except RuntimeError as e:
            failures.append(f"行内公式 {m.group(1).strip()[:40]}: {e}")
            counter -= 1
            return m.group(0)  # 保留原文，让失败可见
        manifest.append({"index": counter, "type": "inline", "latex": m.group(1).strip(),
                         "png": name, "px": [w, h]})
        return f"![inline]({name})"

    text = INLINE_RE.sub(sub_inline, text)
    manifest.sort(key=lambda e: e["index"])
    return text, manifest, failures


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="整篇 md 公式识别与图片化替换")
    ap.add_argument("input", help="含 LaTeX 公式的 md")
    ap.add_argument("-o", "--output", required=True, help="替换后的 md 路径")
    ap.add_argument("--png-dir", required=True, help="公式图片输出目录（建议与 output.md 同目录）")
    ap.add_argument("--color", default="#2e2e2e", help="公式颜色（深色卡片内传浅色）")
    args = ap.parse_args()

    src = Path(args.input)
    if not src.exists():
        print(f"找不到输入：{src}", file=sys.stderr)
        return 2

    text, manifest, failures = replace_formulas(
        src.read_text(encoding="utf-8"), Path(args.png_dir), args.color)

    if failures:
        print("以下公式未替换成功（output.md 未写出）：", file=sys.stderr)
        for f in failures:
            print(f"  {f}", file=sys.stderr)
        return 1
    if not manifest:
        print("未检出任何公式，原样输出")
        Path(args.output).write_text(text, encoding="utf-8")
        return 0

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    mpath = out.with_name("formula_manifest.json")
    mpath.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(out), "formulas": len(manifest),
                      "inline": sum(1 for m in manifest if m["type"] == "inline"),
                      "display": sum(1 for m in manifest if m["type"] == "display"),
                      "manifest": str(mpath)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
