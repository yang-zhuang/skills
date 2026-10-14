"""LaTeX 公式 → PNG 图片。

微信公众号正文不渲染 LaTeX（实测：$...$ 原样显示，MathJax/KaTeX 均被剥离），
唯一可靠路径是转图片。批量替换（$...$ 与 $$...$$ 混排的整篇 md）用
formula_replace.py；本脚本只负责单个公式，供调试与 formula_replace 复用。

用 matplotlib 的 mathtext 渲染（纯 Python，不需要系统 LaTeX）。
mathtext 覆盖常用数学语法；不支持 \\begin{align} 等环境，会解析失败，
此时应把公式拆成多行分别转，或降级为 Unicode 字符。

用法:
    python formula_to_png.py "<latex 不带 $>" -o out.png
    python formula_to_png.py "E=mc^2" -o f.png --display          # 独立公式（居中块）
    python formula_to_png.py "x_i^2" -o f.png --color "#e2e8f0"   # 深色卡片内用浅色

输出 JSON：图片路径、像素尺寸、显示高度、建议的内联 img 片段。

依赖：matplotlib（pip install matplotlib -i https://pypi.org/simple）
退出码：0 成功；1 公式解析失败；2 参数错误或缺依赖
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

DPI = 300
# 显示高度 = PNG 高 × DPI 换算 × 0.9（数学公式惯例略小于正文，字号才协调）
SCALE = 96 / DPI * 0.9
MIN_DISP_H = 12


def render_formula(latex: str, out: str | Path, color: str = "#2e2e2e") -> tuple[int, int]:
    """渲染单个公式为 PNG（300dpi 透明底，bbox 自动裁剪）。

    返回 (宽, 高)。解析失败抛 RuntimeError（含 mathtext 原始错误），
    缺 matplotlib 抛 ImportError，IO 失败抛 OSError。
    """
    import matplotlib
    matplotlib.use("Agg")
    from matplotlib import mathtext

    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        mathtext.math_to_image(f"${latex}$", out, dpi=DPI, format="png", color=color)
    except Exception as e:
        if isinstance(e, OSError):
            raise
        raise RuntimeError(f"{e}") from e
    from PIL import Image
    return Image.open(out).size


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="LaTeX 公式转 PNG（mathtext）")
    ap.add_argument("latex", help="公式，不带定界符 $（如 E=mc^2）")
    ap.add_argument("-o", "--output", required=True, help="输出 png 路径")
    ap.add_argument("--display", action="store_true",
                    help="独立公式模式：居中展示块，渲染字号更大")
    ap.add_argument("--color", default="#2e2e2e", help="公式颜色（深色卡片内传浅色）")
    args = ap.parse_args()

    latex = args.latex.strip().strip("$").strip()
    if not latex:
        print("空公式", file=sys.stderr)
        return 2

    try:
        w, h = render_formula(latex, args.output, args.color)
    except ImportError:
        print("缺依赖：pip install matplotlib -i https://pypi.org/simple", file=sys.stderr)
        return 2
    except OSError as e:
        print(f"写文件失败：{e}", file=sys.stderr)
        return 1
    except RuntimeError as e:
        print(f"公式解析失败：{e}\n"
              f"mathtext 不支持部分 LaTeX 环境（align/数组等）。"
              f"拆成多行分别转，或降级为 Unicode 字符。", file=sys.stderr)
        return 1

    disp_h = max(MIN_DISP_H, round(h * SCALE))
    # 行内图刻意不用 vertical-align:middle——对齐点是「基线+x-height一半」，
    # 比中文字面视觉中心低约 2px，公式会看着往下坠；默认基线对齐才正中
    align = "text-align: center; margin: 10px 0px;" if args.display \
        else "display: inline-block;"
    img = (f'<img src="{args.output.replace(chr(92), "/")}" '
           f'style="max-width: 100%; height: {disp_h}px;" '
           f'alt="{latex[:40]}" />')
    print(json.dumps({
        "png": args.output,
        "px": [w, h],
        "display_height_px": disp_h,
        "img_html": f'<span style="{align}">{img}</span>',
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
