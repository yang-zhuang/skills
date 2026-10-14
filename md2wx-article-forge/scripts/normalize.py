"""校验并规范化 markdown：层级、链接、图片、微信不兼容语法。

用法:
    python normalize.py <input.md> [output.md]
    python normalize.py <input.md> --check    # 只报告不改写
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from wechat_rules import (  # noqa: E402
    CONTENT_FULL_TOKEN_BUDGET,
    LIMIT_CONTENT_CHARS,
    LIMIT_TITLE,
    MAX_HEADING_LEVEL,
    TITLE_SOFT_LIMIT,
)

# markdown 里写了、但微信渲染后必然出问题的语法
UNSUPPORTED = [
    (r"~~(.+?)~~", "删除线", "GFM 扩展，微信兼容性不稳"),
    (r"\[\^(.+?)\]", "脚注", "微信无脚注支持，应转文末列表"),
    (r"!\[.*?\]\[.*?\]", "引用式图片", "解析歧义，应转行内式"),
    (r"<script", "script 标签", "会被整节点剔除"),
    (r"<iframe", "iframe 标签", "会被整节点剔除"),
    (r"<style", "style 标签", "会被丢弃，样式必须内联"),
    (r"<div|<span[^>]*class", "带 class 的 HTML 容器", "class 会被剥离且样式失效"),
]

IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
CJK_RE = re.compile(r"[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]")
LATIN_RE = re.compile(r"[A-Za-z]+")


def estimate_tokens(md: str) -> int:
    """粗略估 token：CJK 字与标点各计 1，英文单词计 1。

    只用于「要不要建议删减」的量级判断，不用于计费。
    """
    cjk = len(CJK_RE.findall(md))
    latin = len(LATIN_RE.findall(md))
    return cjk + latin


def audit(md: str, base: Path) -> dict:
    """只做检查，不改写。返回结构化问题清单。"""
    issues: list[dict] = []

    lines = md.split("\n")
    prev_level = 0
    for n, line in enumerate(lines, 1):
        m = HEADING_RE.match(line)
        if m:
            level = len(m.group(1))
            if not m.group(2).strip():
                issues.append({"line": n, "level": "error", "msg": "空标题"})
            if prev_level and level > prev_level + 1:
                issues.append({
                    "line": n, "level": "warn",
                    "msg": f"标题跳级 h{prev_level} → h{level}",
                })
            prev_level = level
        if line.rstrip() != line and line.strip():
            issues.append({"line": n, "level": "warn", "msg": "行尾有多余空格"})
        if "\t" in line:
            issues.append({"line": n, "level": "warn", "msg": "含制表符，缩进可能错乱"})

    for n in range(len(lines) - 2):
        if not lines[n].strip() and not lines[n + 1].strip() and not lines[n + 2].strip():
            issues.append({"line": n + 1, "level": "warn", "msg": "连续三行以上空行"})

    for pattern, name, why in UNSUPPORTED:
        for m in re.finditer(pattern, md):
            issues.append({
                "line": md[:m.start()].count("\n") + 1,
                "level": "error", "msg": f"{name} 不受支持（{why}）",
            })

    images: list[dict] = []
    missing = []
    for m in IMAGE_RE.finditer(md):
        src = m.group(2)
        local = not src.startswith(("http://", "https://", "mmbiz"))
        exists = (base / src).exists() if local else True
        images.append({"alt": m.group(1), "src": src, "resolved": exists})
        if not exists:
            missing.append(src)
            issues.append({
                "line": md[:m.start()].count("\n") + 1,
                "level": "error", "msg": f"图片不存在: {src}",
            })

    outside = []
    for m in LINK_RE.finditer(md):
        url = m.group(2)
        if url.startswith("http") and "mp.weixin.qq.com" not in url:
            outside.append(url)

    return {
        "ok": not any(i["level"] == "error" for i in issues),
        "issues": issues,
        "missing_images": missing,
        "images": images,
        "external_links": outside,
        "title": _audit_title(md, issues),
        "content_policy": _content_policy(md),
        "stats": {
            "chars": len(md),
            "lines": len(lines),
            "headings": sum(1 for x in lines if HEADING_RE.match(x)),
            "images": len(images),
            "links": len(LINK_RE.findall(md)),
        },
    }


def _audit_title(md: str, issues: list[dict]) -> dict:
    """标题单独校验：它决定打开率，且受 draft/add 硬上限约束。"""
    first = next((HEADING_RE.match(x) for x in md.split("\n") if HEADING_RE.match(x)), None)
    text = first.group(2).strip() if first else ""
    if not text:
        issues.append({"line": 1, "level": "error", "msg": "缺一级标题，发布时无标题来源"})
    elif len(text) > LIMIT_TITLE:
        issues.append({
            "line": 1, "level": "error",
            "msg": f"标题 {len(text)} 字超过硬上限 {LIMIT_TITLE} 字，提交会被截断",
        })
    elif len(text) > TITLE_SOFT_LIMIT:
        issues.append({
            "line": 1, "level": "warn",
            "msg": f"标题 {len(text)} 字，信息流里超过 {TITLE_SOFT_LIMIT} 字会被折叠，建议再压",
        })
    return {
        "text": text,
        "chars": len(text),
        "hard_limit": LIMIT_TITLE,
        "soft_limit": TITLE_SOFT_LIMIT,
    }


def _content_policy(md: str) -> dict:
    """守恒第一：低于预算逐字保留，超预算只告警不删。"""
    tokens = estimate_tokens(md)
    chars = len(md)
    if chars > LIMIT_CONTENT_CHARS:
        action = "超微信硬上限，必须拆成系列或删章节后再发"
    elif tokens > CONTENT_FULL_TOKEN_BUDGET:
        action = "超出保真预算，若要精简须经作者确认，脚本不得自动删改"
    else:
        action = "原样保留全文，不做任何删除或摘要"
    return {
        "chars": chars,
        "est_tokens": tokens,
        "char_limit": LIMIT_CONTENT_CHARS,
        "full_token_budget": CONTENT_FULL_TOKEN_BUDGET,
        "within_budget": tokens <= CONTENT_FULL_TOKEN_BUDGET and chars <= LIMIT_CONTENT_CHARS,
        "action": action,
    }


def normalize(md: str) -> str:
    """改写微信不兼容语法。"""
    out = md.replace("\r\n", "\n")
    # 删除线 → 带 line-through 的 span
    out = re.sub(r"~~(.+?)~~",
                 r'<span style="text-decoration: line-through;">\1</span>', out)
    # 引用式图片 → 行内式
    out = re.sub(r"!\[([^\]]*)\]\[([^\]]*)\]", r"![\1](\2)", out)
    # 6 级以上标题压到 3 级
    out = re.sub(r"^(#{7,})\s+", "### ", out, flags=re.M)
    # 行尾空白
    out = "\n".join(x.rstrip() for x in out.split("\n"))
    return re.sub(r"\n{3,}", "\n\n", out).strip() + "\n"


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    src = Path(sys.argv[1])
    md = src.read_text(encoding="utf-8")
    report = audit(md, src.parent)

    if "--check" in sys.argv:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report["ok"] else 2

    clean = normalize(md)
    dst = Path(sys.argv[2]) if len(sys.argv) > 2 else src.with_name(src.stem + ".norm.md")
    dst.write_text(clean, encoding="utf-8")
    report["output"] = str(dst)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())