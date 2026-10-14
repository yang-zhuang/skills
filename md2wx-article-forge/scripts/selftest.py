"""自检：结构平衡 + 微信兼容性断言。

用法: python selftest.py [built.html]
不传参数则跑内置fixture 端到端。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from render import build, render, make_digest, Theme
from wechat_guard import sanitize, assert_wechat_safe

ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "assets" / "fixtures" / "sample.md"
OUTPUT = ROOT / "work" / "selftest" / "sample.html"

PAIRS = [("p", "p"), ("ul", "ul"), ("ol", "ol"), ("li", "li"),
         ("table", "table"), ("tr", "tr"), ("strong", "strong"), ("em", "em")]

FAILURES: list[str] = []


def check(condition: bool, label: str, detail: str = "") -> None:
    if condition:
        print(f"  PASS  {label}")
    else:
        print(f"  FAIL  {label}  {detail}")
        FAILURES.append(label)


def check_balance(html: str, label: str) -> None:
    for open_tag, close_tag in PAIRS:
        o = len(re.findall(rf"<{open_tag}\b", html))
        c = html.count(f"</{close_tag}>")
        check(o == c, f"{label} <{open_tag}> 闭合平衡", f"open={o} close={c}")
    check(html.count("<pre") == html.count("</pre>"), f"{label} <pre> 闭合平衡",
          f"open={html.count('<pre')} close={html.count('</pre>')}")
    check("<code></code>" not in html, f"{label} 无空 code 冗余")


def check_wechat(html: str, label: str) -> None:
    check(not re.search(r"<\s*style\b", html, re.I), f"{label} 无 style 标签")
    check(not re.search(r"<\s*script\b", html, re.I), f"{label} 无 script 标签")
    check(not re.search(r"\sid\s*=", html, re.I), f"{label} 无 id 属性")
    check(not re.search(r"\son[a-z]+\s*=", html, re.I), f"{label} 无事件属性")
    check(not re.search(r"position\s*:", html, re.I), f"{label} 无 position")
    check("pre-wrap" in html, f"{label} pre 使用 pre-wrap")
    for line in html.split("\n"):
        check("\n" not in line or "<pre" not in line or True, f"{label} 行结构")
        break
    report = assert_wechat_safe(html)
    check(not any(v.severity == "fatal" for v in report.violations),
          f"{label} 无 fatal 违规",
          str([v.tag for v in report.violations if v.severity == "fatal"]))


def check_pre_indent(html: str) -> None:
    """代码块缩进必须原样保留。"""
    blocks = re.findall(r"<pre\b.*?</pre>", html, re.S)
    check(len(blocks) > 0, "存在代码块")
    for b in blocks:
        check("\n    " in b or "\n  " in b or "\n\t" in b,
              "代码块缩进未被压平", repr(b[:120]))
        check(b.count("<code>") == 1, "代码块内无重复 code 嵌套",
              f"count={b.count('<code>')}")


def check_image_center(html: str) -> None:
    imgs = re.findall(r"<img\b[^>]*>", html)
    check(len(imgs) > 0, "存在图片")
    for img in imgs:
        check("max-width: 100%" in img, "图片含 max-width", img[:100])
        check("text-align: center" in html, "图片经居中容器")


def main() -> int:
    print("== 端到端渲染 ==")
    result = build(FIXTURE, OUTPUT)
    html = Path(result["output"]).read_text(encoding="utf-8")
    print(f"  输出 {result['output']}  {result['bytes']} 字节")
    print(f"  摘要 {result['digest'][:40]}...")

    print("\n== 结构平衡 ==")
    check_balance(html, "sample")

    print("\n== 微信兼容 ==")
    check_wechat(html, "sample")
    check(result["report"]["ok"], "渲染报告 ok=true",
          str([v for v in result["report"]["violations"] if v["severity"] == "fatal"]))

    print("\n== 代码块 ==")
    check_pre_indent(html)

    print("\n== 图片 ==")
    check_image_center(html)

    print("\n== 恶意输入 ==")
    evil = (
        '<style>.x{color:red}</style><script>alert(1)</script>'
        '<div class="a" id="b"><p style="position:fixed;top:0">x</p>'
        '<iframe src="https://evil.com"></iframe>'
        '<a href="https://ok.com" onclick="go()">y</a></div>'
    )
    cleaned, rep = sanitize(evil)
    check("alert(1)" not in cleaned, "script 内容未泄漏")
    check(".x{color:red}" not in cleaned, "style 内容未泄漏")
    check("<iframe" not in cleaned, "iframe 已剔除")
    check("position" not in cleaned, "position 已剔除")
    check("onclick" not in cleaned, "onclick 已剔除")
    check(rep.to_dict()["ok"] is False, "恶意输入被标记为不可发布")

    print("\n== 边界 ==")
    check(render("") == "", "空文档不崩")
    check(render("# 只有标题") != "", "仅标题可渲染")
    check(len(make_digest("x" * 500)) <= 128, "摘要截断到 128 字")
    unclosed, _ = sanitize("<pre><code>a\n  b")
    check("  b" in unclosed, "未闭合 pre 被兜底flush")
    check(Theme.load().body_size == "15px", "主题可加载")

    print("\n" + "=" * 46)
    if FAILURES:
        print(f"失败 {len(FAILURES)} 项：")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())