"""HTML → 微信兼容HTML 的清洗器。

输入：渲染器产出的任意 HTML 片段
输出：只含微信保留标签、只含 SAFE_CSS_PROPS、style 全部压成单行的片段
      + 一份违规报告（结构化，供 wx-typeset 判断是否需要降级改写）

设计原则：不生成会被微信吃掉的东西，而不是生成完再等微信清洗。
"""
from __future__ import annotations

import re
from html.parser import HTMLParser
from dataclasses import dataclass, field

from wechat_rules import (
    CONTAINER_TAG,
    DEAD_CSS_PROPS,
    FORBIDDEN_ATTRS,
    FORBIDDEN_TAGS,
    REWRITTEN_CONTAINER_TAGS,
    SAFE_CSS_PROPS,
)

VOID_TAGS = frozenset({"br", "img", "hr", "wbr"})

# 丢弃标签**连同内部文本**（CSS/脚本内容会变成裸文本泄漏到正文）
SKIP_CONTENT_TAGS = frozenset({"style", "script", "textarea", "title"})


@dataclass
class Violation:
    tag: str
    attr: str | None
    reason: str
    severity: str  # fatal | warn


@dataclass
class Report:
    violations: list[Violation] = field(default_factory=list)
    removed_tags: int = 0
    stripped_attrs: int = 0
    stripped_props: int = 0

    @property
    def ok(self) -> bool:
        return not any(v.severity == "fatal" for v in self.violations)

    def to_dict(self) -> dict:
        return {
            "ok": self.ok,
            "removed_tags": self.removed_tags,
            "stripped_attrs": self.stripped_attrs,
            "stripped_props": self.stripped_props,
            "violations": [
                {"tag": v.tag, "attr": v.attr, "reason": v.reason, "severity": v.severity}
                for v in self.violations
            ],
        }


def _clean_style(raw: str, tag: str, report: Report) -> str:
    """压成单行、剔除禁用属性、只保留白名单属性。"""
    kept: list[str] = []
    # 分号在 url(data:...) 内部也有，先按分号切会碎；用正则按顶层分号切
    for decl in re.split(r";(?![^(]*\))", raw):
        if ":" not in decl:
            continue
        prop, _, value = decl.partition(":")
        prop = prop.strip().lower()
        value = value.strip()
        if not prop or not value:
            continue
        if prop in FORBIDDEN_ATTRS:
            report.violations.append(Violation(tag, prop, "属性会被微信剥离", "warn"))
            report.stripped_attrs += 1
            continue
        if prop in DEAD_CSS_PROPS:
            report.violations.append(Violation(tag, prop, "属性会被微信过滤", "warn"))
            report.stripped_props += 1
            continue
        if prop not in SAFE_CSS_PROPS:
            report.violations.append(Violation(tag, prop, "不在安全白名单内", "warn"))
            report.stripped_props += 1
            continue
        kept.append(f"{prop}: {value}")
    return "; ".join(kept)


class _Cleaner(HTMLParser):
    def __init__(self, report: Report):
        # convert_charrefs 必须为 False：渲染器已把正文转义(&lt; 等)，
        # 若在此处再还原成真实字符，用户文本里的 <script> 就会变成真标签。
        # 不还原字符实体不等于跳过清洗——真标签仍会走 parse 事件被处理。
        super().__init__(convert_charrefs=False)
        self.out: list[str] = []
        self.report = report
        self._skip_depth = 0
        self._pre_depth = 0
        self._pre_buf: list[str] = []
        self._pre_style = ""
        self._pre_has_code = False

    def handle_starttag(self, tag, attrs):
        if self._skip_depth:
            # 已在丢弃区间内，只维护嵌套深度
            if tag in SKIP_CONTENT_TAGS and tag not in VOID_TAGS:
                self._skip_depth += 1
            return
        if tag in FORBIDDEN_TAGS:
            self.report.violations.append(Violation(tag, None, "禁用标签，会被整节点剔除", "fatal"))
            self.report.removed_tags += 1
            if tag in SKIP_CONTENT_TAGS:
                self._skip_depth = 1
            return
        if tag in SKIP_CONTENT_TAGS:
            self.report.violations.append(Violation(tag, None, "标签与内容一并丢弃", "fatal"))
            self.report.removed_tags += 1
            self._skip_depth = 1
            return
        if tag == "pre":
            # 代码块内部整体绕过清洗：代码文本不需要样式清洗，
            # 且缩进与嵌套必须逐字符原样透传。
            self._pre_depth += 1
            if self._pre_depth > 1:
                self._pre_buf.append(f"<{tag}>")
                return
            style = ""
            for name, value in attrs:
                if name.lower() == "style" and value:
                    style = _clean_style(value, "pre", self.report)
            self._pre_style = style
            return
        if tag == "code" and self._pre_depth:
            self._pre_has_code = True
        if tag in REWRITTEN_CONTAINER_TAGS:
            # div 的 style 会被微信清除，结构也会被改写 —— 改写成等价的 section。
            # 不能像早先那样丢标签：容器承载了卡片底色、圆角、横向滚动等布局职责，
            # 丢掉等于把整篇版式拆散。
            tag = CONTAINER_TAG

        parts = [tag]
        for name, value in attrs:
            name = name.lower()
            if name in FORBIDDEN_ATTRS:
                self.report.violations.append(
                    Violation(tag, name, "属性会被微信剥离", "warn")
                )
                self.report.stripped_attrs += 1
                continue
            if name == "style" and value:
                cleaned = _clean_style(value, tag, self.report)
                if not cleaned:
                    continue
                parts.append(f'style="{cleaned}"')
                continue
            if value is None:
                parts.append(name)
            else:
                safe = value.replace('"', "&quot;")
                parts.append(f'{name}="{safe}"')

        open_html = "<" + " ".join(parts) + ">"
        if self._pre_depth:
            # pre 内的开标签并入缓冲，保证嵌套结构完整
            self._pre_buf.append(open_html)
        else:
            self.out.append(open_html)

    def _pre_html(self) -> str:
        """收口代码块。输入自带 <code> 时原样透传，避免嵌套出空 code 壳。"""
        body = "".join(self._pre_buf)
        style = self._pre_style or "white-space: pre-wrap"
        if self._pre_has_code:
            return f'<pre style="{style}">{body}</pre>'
        return f'<pre style="{style}"><code>{body}</code></pre>'

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if self._skip_depth:
            if tag in SKIP_CONTENT_TAGS:
                self._skip_depth -= 1
            return
        if tag == "pre":
            if self._pre_depth:
                self._pre_depth -= 1
                if self._pre_depth == 0:
                    self.out.append(self._pre_html())
                    self._pre_buf = []
                    self._pre_style = ""
                    self._pre_has_code = False
            return
        if tag in FORBIDDEN_TAGS or tag in VOID_TAGS:
            return
        if tag in REWRITTEN_CONTAINER_TAGS:
            tag = CONTAINER_TAG
        if self._pre_depth:
            # pre 内的闭合标签并入缓冲，保证嵌套结构完整
            self._pre_buf.append(f"</{tag}>")
            return
        self.out.append(f"</{tag}>")

    def _emit(self, chunk: str) -> None:
        if self._skip_depth:
            return
        if self._pre_depth:
            self._pre_buf.append(chunk)
            return
        self.out.append(chunk)

    def handle_data(self, data):
        self._emit(data)

    def handle_entityref(self, name):
        # 实体必须原样透传。默认实现什么都不做会把 &amp; 静默吃掉变成 amp;
        self._emit(f"&{name};")

    def handle_charref(self, name):
        self._emit(f"&#{name};")

    def flush_pre(self) -> None:
        """容错：未闭合的 pre 在 close() 时补齐。"""
        if self._pre_depth:
            self._pre_depth = 0
            self.out.append(self._pre_html())
            self._pre_buf = []
            self._pre_style = ""
            self._pre_has_code = False


def sanitize(html: str) -> tuple[str, Report]:
    report = Report()
    parser = _Cleaner(report)
    parser.feed(html)
    parser.close()
    parser.flush_pre()

    text = "".join(parser.out)

    # 保护 <pre> 内容，避免全局空白折叠破坏代码缩进
    blocks: list[str] = []

    def stash(m: re.Match) -> str:
        blocks.append(m.group(0))
        return f"\x01{len(blocks) - 1}\x01"

    text = re.sub(r"<pre\b.*?</pre>", stash, text, flags=re.S)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()
    for i, block in enumerate(blocks):
        text = text.replace(f"\x01{i}\x01", block)
    return text, report


def assert_wechat_safe(html: str) -> Report:
    """只审计不改写，供 CI/人工检查使用。"""
    report = Report()
    for tag in FORBIDDEN_TAGS:
        if re.search(rf"<\s*{tag}\b", html, re.I):
            report.violations.append(Violation(tag, None, "出现禁用标签", "fatal"))
    for attr in FORBIDDEN_ATTRS:
        if re.search(rf'\s{attr}\s*=', html, re.I):
            report.violations.append(Violation("<any>", attr, "出现会被剥离的属性", "warn"))
    if re.search(r"<style\b", html, re.I):
        report.violations.append(Violation("style", None, "style 标签会被丢弃", "fatal"))
    for match in re.finditer(r"https?://([\w.-]+)/", html):
        host = match.group(1)
        if host.endswith("mmbiz.qpic.cn"):
            continue
        report.violations.append(
            Violation("img/a", None, f"非微信域名：{host}", "warn")
        )
    return report


if __name__ == "__main__":
    import sys
    import json

    if len(sys.argv) < 2:
        print("用法: python wechat_guard.py <input.html> [output.html]")
        raise SystemExit(1)
    raw = open(sys.argv[1], encoding="utf-8").read()
    clean, rep = sanitize(raw)
    print(json.dumps(rep.to_dict(), ensure_ascii=False, indent=2))
    if len(sys.argv) > 2:
        open(sys.argv[2], "w", encoding="utf-8").write(clean)
    else:
        print(clean)