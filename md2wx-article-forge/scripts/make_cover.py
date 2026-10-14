"""生成合规封面图。

用法:
    python make_cover.py --title "文章标题" -o cover.jpg
    python make_cover.py --title "标题" --subtitle "副标题" -o cover.jpg --w 900

约束（违反会被微信拒绝）：
  - 必须 JPG
  - 宽度 >= 900px，否则报 53401
  - <= 2MB
  - news 类型裁剪比例仅支持 2.35:1 与 1:1

依赖 Pillow；若未安装会给出明确提示，不静默降级。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

W, H = 900, 383  # 2.35:1
BG = (26, 42, 58)
FG = (245, 247, 250)
ACCENT = (7, 193, 96)
MUTED = (150, 165, 180)

# 优先用项目 venv 的 Python（已装 Pillow）
if sys.version_info < (3, 0):
    raise SystemExit("需要 Python 3")


def _require_pil():
    try:
        from PIL import Image, ImageDraw, ImageFont
        return Image, ImageDraw, ImageFont
    except ImportError:
        raise SystemExit(
            "需要 Pillow。请用带 Pillow 的解释器运行：\n"
            '  "C:/Users/lenovo/.workbuddy/binaries/python/envs/default/Scripts/python.exe" '
            "make_cover.py ...\n"
            "或先执行： pip install Pillow -i https://pypi.org/simple"
        )


def _font(size: int, bold: bool = False):
    _, _, ImageFont = _require_pil()
    names = [
        "C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
        "C:/Windows/Fonts/msyh.ttc",
    ]
    for n in names:
        if Path(n).exists():
            try:
                return ImageFont.truetype(n, size)
            except Exception:
                continue
    return ImageFont.load_default()


def wrap(draw, text: str, font, max_w: int) -> list[str]:
    """按像素宽度折行，中文逐字、英文按词。"""
    lines: list[str] = []
    cur = ""
    for ch in text:
        trial = cur + ch
        if draw.textlength(trial, font=font) > max_w and cur:
            lines.append(cur)
            cur = ch
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines


def render(title: str, subtitle: str = "", width: int = W, height: int = H) -> bytes:
    from io import BytesIO

    Image, ImageDraw, _ = _require_pil()
    img = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(img)

    # 左侧绿色装饰条
    draw.rectangle([0, height - 10, width // 4, height - 4], fill=ACCENT)

    pad = 64
    max_w = width - pad * 2

    t_font = _font(52, bold=True)
    s_font = _font(26)

    t_lines = wrap(draw, title, t_font, max_w)[:3]
    s_lines = wrap(draw, subtitle, s_font, max_w)[:2] if subtitle else []

    th = 62
    sh = 34
    total = len(t_lines) * th + (len(s_lines) * sh + 16 if s_lines else 0)
    y = (height - total) // 2

    for line in t_lines:
        draw.text((pad, y), line, font=t_font, fill=FG)
        y += th

    if s_lines:
        y += 16
        for line in s_lines:
            draw.text((pad, y), line, font=s_font, fill=MUTED)
            y += sh

    buf = BytesIO()
    img.save(buf, format="JPEG", quality=88, optimize=True)
    return buf.getvalue()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--title", required=True, help="封面标题，支持中文")
    ap.add_argument("--subtitle", default="", help="副标题")
    ap.add_argument("-o", "--output", default="cover.jpg")
    ap.add_argument("--w", type=int, default=W)
    ap.add_argument("--h", type=int, default=H)
    args = ap.parse_args()

    if args.w < 900:
        print(f"警告: 宽度 {args.w} < 900px，微信会报 53401", file=sys.stderr)

    data = render(args.title, args.subtitle, args.w, args.h)
    out = Path(args.output)
    out.write_bytes(data)
    print(f"已生成 {out}  {len(data)} 字节  {args.w}x{args.h}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())