"""Markdown → 微信内联样式 HTML。

刻意不引第三方 markdown 库：本项目只需要一个可控的子集，
自己写换来的是「输出确定 + 可审计 + 零依赖」，符合零中间层偏好。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict, field, replace
from pathlib import Path

from wechat_rules import LIMIT_DIGEST, MAX_HEADING_LEVEL
from wechat_guard import sanitize

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_THEME = ROOT / "assets" / "themes" / "default.json"

INLINE_RE = re.compile(r"`([^`]+)`")
BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
ITALIC_RE = re.compile(r"(?<!\*)\*([^*\n]+)\*(?!\*)")
LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")

# 渲染期发现的需要作者知道的事（标题降级、孤儿标记等），取走后置空。
# 任何降级都必须可见，不能静默发生。
WARNINGS: list[str] = []

# 公式图高度折算需要按路径找到 PNG 读尺寸；相对路径以 html 输出目录为基准。
# 渲染器单文档执行，与 WARNINGS 同模式：render() 入口设置。
BASE_DIR: Path | None = None


@dataclass
class Theme:
    name: str
    body_color: str
    body_size: str
    line_height: str
    h1_size: str
    h2_size: str
    h3_size: str
    accent: str
    code_bg: str
    code_color: str
    quote_color: str
    link_color: str
    font_stack: str
    # 语义化组件：角色名 → 内联样式字典。第三层视觉差异全部在这里体现，
    # 写 markdown 的人只用写 `:::card`，不需要知道色值。
    components: dict[str, dict[str, str]] = field(default_factory=dict)

    @staticmethod
    def load(path: Path = DEFAULT_THEME) -> "Theme":
        return Theme(**json.loads(path.read_text(encoding="utf-8")))

    @property
    def font(self) -> str:
        return self.font_stack


def esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _png_size(path: Path) -> tuple[int, int] | None:
    """读 PNG 宽高（IHDR 定长偏移），纯 stdlib。非 PNG 或读不到返回 None。"""
    try:
        with open(path, "rb") as f:
            head = f.read(24)
        if head[:8] != b"\x89PNG\r\n\x1a\n" or head[12:16] != b"IHDR":
            return None
        import struct
        w, h = struct.unpack(">II", head[16:24])
        return int(w), int(h)
    except OSError:
        return None


def _img_html(alt: str, src: str) -> str:
    """按 alt 约定生成图片 HTML。公式图按 300dpi 渲染 → 96dpi 显示折算（×0.9）。"""
    is_formula = alt in ("inline", "display")
    disp_h = ""
    if is_formula:
        # 相对路径以 html 输出目录为基准；找不到再试 CWD；都没有则优雅降级不写高度
        for base in (BASE_DIR, Path.cwd()):
            if base is None:
                continue
            size = _png_size(base / src)
            if size:
                disp_h = f' height: {max(12, round(size[1] * 96 / 300 * 0.9))}px;'
                break
    if alt == "inline":
        # 刻意不用 vertical-align:middle：它把图片中点对到「基线+x-height一半」，
        # 比中文字面视觉中心低约 2px，公式看着往下坠。
        # 默认基线对齐（图底边=文字基线）才是公式该有的对齐——mathtext 的 PNG
        # 底边即公式基线（无降部字母时），两边基线重合，视觉正中。
        return (f'<span style="display: inline-block;">'
                f'<img src="{src}" alt="{alt}"'
                f' style="max-width: 100%;{disp_h}" /></span>')
    radius = "" if alt == "display" else " border-radius: 4px;"
    return (f'<p style="text-align: center; margin: 12px 0px;">'
            f'<img src="{src}" alt="{alt}"'
            f' style="max-width: 100%;{disp_h}{radius}" /></p>')


def inline(text: str, t: Theme) -> str:
    """行内标记 → 内联样式 span。

    抽出的块级内容（行内代码、图片）先存成占位符，
    避免它们被后续正则规则破坏，也避免图片嵌进段落导致居中失效。
    """
    slots: list[str] = []

    def stash(html: str) -> str:
        slots.append(html)
        return f"\x00{len(slots) - 1}\x00"

    def stash_code(m: re.Match) -> str:
        return stash(
            f'<code style="font-family: Menlo, Consolas, monospace; font-size: {t.body_size};'
            f' background-color: {t.code_bg}; color: {t.code_color}; padding: 2px 5px;'
            f' border-radius: 3px;">{esc(m.group(1))}</code>'
        )

    text = INLINE_RE.sub(stash_code, text)
    text = esc(text)
    # 图片的 alt 约定（与 formula_replace.py 对应）：
    #   inline  行内公式图 —— 走文字流，高度按 96dpi 折算，基线居中
    #   display 块级公式图 —— 独立居中段，高度折算
    #   其他    普通插图   —— 独立居中段，max-width:100% 自适应
    text = IMAGE_RE.sub(lambda m: stash(_img_html(m.group(1), m.group(2))), text)
    text = LINK_RE.sub(
        lambda m: f'<a href="{m.group(2)}" style="color: {t.link_color};'
                  f' text-decoration: none;">{m.group(1)}</a>',
        text,
    )
    text = BOLD_RE.sub(
        lambda m: f'<strong style="font-weight: 700; color: {t.accent};">{m.group(1)}</strong>',
        text,
    )
    text = ITALIC_RE.sub(
        lambda m: f'<em style="font-style: italic; color: {t.body_color};">{m.group(1)}</em>',
        text,
    )

    def restore(m: re.Match) -> str:
        return slots[int(m.group(1))]

    return re.sub(r"\x00(\d+)\x00", restore, text)


def _para(text: str, t: Theme, tight: bool = False) -> str:
    """段落 → 内联样式。

    行内若含块级元素（居中图片、代码块），必须拆成独立块：
    微信编辑器会强制把 div 转 p，块级嵌套在 p 里必然塌缩。

    tight 用于组件内部：卡片自带 padding，段落再各加 18px 外边距，
    首行上方就是 34px 空隙 —— 渲染图上看非常丑。
    """
    margin = "2px 0px" if tight else "10px 0px"
    parts = re.split(
        r"(<p style=\"text-align: center;.*?</p>|<pre\b.*?</pre>|<ul\b.*?</ul>|<ol\b.*?</ol>)",
        text,
        flags=re.S,
    )
    out = []
    for chunk in (c for c in parts if c.strip()):
        if re.match(r"<(p|pre|ul|ol)\b", chunk):
            out.append(chunk)
            continue
        out.append(
            f'<p style="font-size: {t.body_size}; color: {t.body_color};'
            f' line-height: {t.line_height}; letter-spacing: 0.5px;'
            f' margin: {margin}; text-align: justify;">{chunk}</p>'
        )
    return "".join(out)


def _component(role: str, inner: list[str], t: Theme) -> str:
    """把一段内容包进主题定义的语义组件。

    组件是版式差异的唯一载体：markdown 里只写角色名，色值、圆角、留白全在主题 json 里。
    角色不存在必须报错并列出可用项 —— 静默降级成裸段落，作者要到最后才发现版式没生效。

    组件可覆写文字色（color / strong_color）：深色逆卡若沿用正文的深色强调，
    就是黑底深蓝字，渲染出来基本不可读 —— 这类问题只有看过渲染图才会发现。
    """
    comp = t.components.get(role)
    if comp is None:
        available = "、".join(sorted(t.components)) or "（当前主题未定义任何组件）"
        raise KeyError(
            f"主题「{t.name}」没有组件角色「{role}」。可用：{available}。"
            f"要么改用可用角色，要么先在主题 components 里补定义。"
        )
    style = "; ".join(f"{k}: {v}" for k, v in comp.items() if k not in ("color", "strong_color"))
    if "color" in comp or "strong_color" in comp:
        t = replace(
            t,
            body_color=comp.get("color", t.body_color),
            accent=comp.get("strong_color", comp.get("color", t.accent)),
        )
    # 组件内部一律紧凑：卡片 padding 已提供留白，段落/标题再套正文的大边距
    # 会叠出 30-50px 的空洞（渲染图实测）。嵌套子组件同样保持紧凑。
    return f'<section style="{style}">' + "".join(_render_lines(inner, t, tight=True)) + "</section>"


def render(md: str, theme: Theme | None = None, base_dir: Path | None = None) -> str:
    global BASE_DIR
    BASE_DIR = base_dir
    t = theme or Theme.load()
    WARNINGS.clear()
    return "".join(_render_lines(md.replace("\r\n", "\n").split("\n"), t))


def _render_lines(lines: list[str], t: Theme, tight: bool = False) -> list[str]:
    out: list[str] = []
    i = 0
    in_code = False
    code_buf: list[str] = []

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        if stripped.startswith("```"):
            if in_code:
                body = esc("\n".join(code_buf))
                pre_margin = "6px 0px" if tight else "12px 0px"
                out.append(
                    f'<pre style="white-space: pre-wrap; overflow-x: auto;'
                    f' background-color: {t.code_bg}; color: {t.code_color};'
                    f' padding: 12px; margin: {pre_margin}; border-radius: 6px;'
                    f' font-family: Menlo, Consolas, monospace; font-size: 13px;'
                    f' line-height: 1.6;"><code>{body}</code></pre>'
                )
                in_code, code_buf = False, []
            else:
                in_code, code_buf = True, []
            i += 1
            continue

        if in_code:
            code_buf.append(line)
            i += 1
            continue

        dm = re.match(r"^:::(\w+)\s*$", stripped)
        if dm:
            role = dm.group(1)
            inner: list[str] = []
            depth = 1
            i += 1
            while i < len(lines):
                s = lines[i].strip()
                # 嵌套指令靠计数配对：内层 :::note 的收口不该终止外层 :::module
                if re.match(r"^:::\w+\s*$", s):
                    depth += 1
                elif s == ":::":
                    depth -= 1
                    if depth == 0:
                        i += 1
                        break
                inner.append(lines[i])
                i += 1
            out.append(_component(role, inner, t))
            continue

        if stripped == ":::":
            # 没有开口的收口 —— 作者多写了一个，必须让人知道而不是静默吞掉
            WARNINGS.append("出现多余的 ::: 收口标记，已忽略；请检查区块配对")
            i += 1
            continue

        if not stripped:
            i += 1
            continue

        if stripped.startswith("|") and i + 1 < len(lines) and set(lines[i + 1].strip()) <= set("|-: "):
            rows: list[list[str]] = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                if set(lines[i].strip()) <= set("|-: "):
                    i += 1
                    continue
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            out.append(_table(rows, t))
            continue

        m = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if m:
            level = len(m.group(1))
            text = m.group(2).strip()
            if level > MAX_HEADING_LEVEL:
                WARNINGS.append(
                    f"#{level} 「{text[:24]}」超出微信可靠标题深度"
                    f"（上限 {MAX_HEADING_LEVEL}），已按第 {MAX_HEADING_LEVEL} 级视觉降级"
                )
                level = MAX_HEADING_LEVEL
            size = {1: t.h1_size, 2: t.h2_size}.get(level, t.h3_size)
            weight = 700 if level <= 2 else 600
            # 刻意不用 <h1>-<h6>：h4 以下不可靠，且正文 h1 与文章主标题语义冲突。
            # 字号 + 字重是唯一可靠的层级表达，洗不掉。
            # 组件内标题收紧：正文级 30px 上边距进卡片后变成大空洞。
            h_margin = "10px 0px 3px 0px" if tight else "10px 0px 6px 0px"
            out.append(
                f'<p style="font-size: {size}; font-weight: {weight}; color: {t.accent};'
                f' line-height: 1.4; margin: {h_margin}; letter-spacing: 0.5px;">'
                f"{inline(text, t)}</p>"
            )
            i += 1
            continue

        if stripped.startswith(">"):
            block = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                block.append(lines[i].strip().lstrip(">").strip())
                i += 1
            body = inline(" ".join(x for x in block if x), t)
            out.append(
                f'<section style="border-left: 3px solid {t.accent}; padding: 2px 0px 2px 14px;'
                f' margin: 12px 0px;">'
                f'<p style="font-size: {t.body_size}; color: {t.quote_color};'
                f' line-height: {t.line_height}; margin: 0;">{body}</p></section>'
            )
            continue

        m = re.match(r"^[-*+]\s+(.*)$", stripped)
        if m:
            items = []
            while i < len(lines) and re.match(r"^\s*[-*+]\s+", lines[i]):
                items.append(re.sub(r"^\s*[-*+]\s+", "", lines[i]))
                i += 1
            lis = "".join(
                f'<li style="font-size: {t.body_size}; color: {t.body_color};'
                f' line-height: {t.line_height}; margin: 6px 0px;">{inline(x, t)}</li>'
                for x in items
            )
            out.append(f'<ul style="margin: {"6px 0px" if tight else "12px 0px"}; padding-left: 22px;">{lis}</ul>')
            continue

        if re.match(r"^\d+\.\s+", stripped):
            items = []
            while i < len(lines) and re.match(r"^\s*\d+\.\s+", lines[i]):
                items.append(re.sub(r"^\s*\d+\.\s+", "", lines[i]))
                i += 1
            lis = "".join(
                f'<li style="font-size: {t.body_size}; color: {t.body_color};'
                f' line-height: {t.line_height}; margin: 6px 0px;">{inline(x, t)}</li>'
                for x in items
            )
            out.append(f'<ol style="margin: {"6px 0px" if tight else "12px 0px"}; padding-left: 22px;">{lis}</ol>')
            continue

        if stripped in ("---", "***", "___"):
            out.append(
                f'<hr style="border: none; border-top: 1px solid #e6e6e6; margin: 16px 0px;" />'
            )
            i += 1
            continue

        out.append(_para(inline(stripped, t), t, tight))
        i += 1

    return out


def _table(rows: list[list[str]], t: Theme) -> str:
    if not rows:
        return ""
    head, *body = rows
    ths = "".join(
        f'<th style="font-size: 13px; font-weight: 600; color: {t.accent};'
        f' text-align: left; padding: 8px 10px; border-bottom: 1px solid #e0e0e0;">'
        f"{inline(c, t)}</th>"
        for c in head
    )
    trs = ""
    for row in body:
        tds = "".join(
            f'<td style="font-size: 13px; color: {t.body_color}; padding: 8px 10px;'
            f' border-bottom: 1px solid #f0f0f0;">{inline(c, t)}</td>'
            for c in row
        )
        trs += f"<tr>{tds}</tr>"
    return (
        f'<section style="overflow-x: auto; margin: 12px 0px;">'
        f'<table style="border-collapse: collapse; width: 100%;"><tr>{ths}</tr>{trs}</table></section>'
    )


def make_digest(md: str, limit: int = LIMIT_DIGEST) -> str:
    # 容器指令标记会被扫进摘要，必须先整行摘掉
    plain = re.sub(r"^:::\w*\s*$", "", md, flags=re.M)
    plain = re.sub(r"<[^>]*>", " ", plain)
    plain = re.sub(r"[#*`>|\-![\]()]", "", plain)
    return re.sub(r"\s+", " ", plain).strip()[:limit]


def build(md_path: Path, out_path: Path, theme_path: Path | None = None, raw_out: Path | None = None) -> dict:
    """渲染 + 清洗，一步产出成品。

    raw_out 写出清洗**前**的产物，用于评估时对比「微信洗掉了什么」——
    只看清洗后的成品，等于默认微信没洗手。
    """
    md = Path(md_path).read_text(encoding="utf-8")
    theme = Theme.load(theme_path) if theme_path else Theme.load()
    raw = render(md, theme, base_dir=Path(out_path).parent)
    if raw_out:
        Path(raw_out).parent.mkdir(parents=True, exist_ok=True)
        Path(raw_out).write_text(raw, encoding="utf-8")
    clean, report = sanitize(raw)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(clean, encoding="utf-8")
    return {
        "output": str(out),
        "bytes": len(clean.encode("utf-8")),
        "digest": make_digest(md),
        "heading_warnings": list(WARNINGS),
        "report": report.to_dict(),
    }


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("用法: python render.py <input.md> <output.html> [theme.json]")
        raise SystemExit(1)
    result = build(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]) if len(sys.argv) > 3 else None)
    print(json.dumps(result, ensure_ascii=False, indent=2))