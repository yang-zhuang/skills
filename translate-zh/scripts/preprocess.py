"""preprocess.py — HTML → markdown 的确定性转换。

目的
    翻译的输入常是 HTML（爬取结果或网页另存）。这个脚本把它转成 markdown，
    翻译之前先把结构定下来，避免翻译时一边理解一边猜格式。

用法（位置参数，不用 flag）
    python preprocess.py <html文件或目录>
    python preprocess.py <html文件或目录> <输出文件或目录>
    python preprocess.py <html文件或目录> <输出> <base_url>

例
    python preprocess.py article/content.html
    python preprocess.py article/content.html out.md https://www.langchain.com
    python preprocess.py blog-research-crawlers/langchain-blog/output/articles out_dir

处理要点
    - h1-h6 / p / ul / ol（含嵌套）/ pre / blockquote / table / a / img / em / strong
    - 图片：优先取 data-src、data-original（lazy-load），再取 src
    - 相对路径用 base_url 补成绝对 URL，否则 md 打开渲染不出图
    - 忽略 head / script / style / nav / footer

只依赖标准库。
"""

import os
import re
import sys
from html.parser import HTMLParser

SKIP_TAGS = {"head", "script", "style", "nav", "footer", "noscript"}
BLOCK_TAGS = {
    "p", "div", "section", "article", "h1", "h2", "h3", "h4", "h5", "h6",
    "li", "pre", "blockquote", "tr", "table", "thead", "tbody", "br", "hr",
}
INLINE_TAGS = {"a", "em", "strong", "code", "span", "i", "b"}


def absolutize(url, base_url):
    if not url:
        return ""
    url = url.strip()
    if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", url):
        return url
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("data:"):
        return url
    if base_url and url.startswith("/"):
        return base_url.rstrip("/") + url
    return url


class MarkdownConverter(HTMLParser):
    def __init__(self, base_url=""):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.out = []
        self.skip_depth = 0
        self.list_stack = []
        self.in_pre = False
        self.pre_buf = []
        self.pending_inline = []
        self.link_href = None
        self.table_rows = None
        self.row = None
        self.cell = None
        self.heading_level = None
        self.heading_buf = []
        self.quote_depth = 0
        self.link_start = None
        self.suppress_anchor = False
        self.quote_buf = []
        self.quote_last_blank = False

    def emit(self, text):
        if self.quote_depth > 0:
            # 引用块内先缓存，凑够一行再统一加 "> " 前缀。
            # 直接逐片段加前缀会得到 **> text**> 这种交错垃圾
            self.quote_buf.append(text)
            if "\n" in text:
                self.flush_quote_lines()
            return
        self.out.append(text)

    def flush_quote_lines(self):
        if not self.quote_buf:
            return
        raw = "".join(self.quote_buf)
        self.quote_buf = []
        for ln in raw.split("\n"):
            s = ln.strip()
            if not s:
                # 空行压缩成一个分隔；状态要跨 flush 保留，否则每次刷新边界都会多出一个 ">"
                if self.quote_last_blank:
                    continue
                self.quote_last_blank = True
                self.out.append(">\n")
            else:
                self.quote_last_blank = False
                self.out.append("> " + s + "\n")

    def flush_line(self, text):
        text = text.strip()
        if text:
            self.out.append(text)

    def handle_starttag(self, tag, attrs):
        if tag in SKIP_TAGS:
            self.skip_depth += 1
            return
        if self.skip_depth:
            return
        attr = dict(attrs)

        if tag == "img":
            src = attr.get("data-src") or attr.get("data-original") or attr.get("src", "")
            alt = attr.get("alt", "").strip()
            url = absolutize(src, self.base_url)
            if url:
                # 前后都留空行：图片独占一段，否则图注/正文会黏在图片后面变成一行
                self.emit("\n\n![%s](%s)\n\n" % (alt, url))
            return

        if tag == "table":
            self.table_rows = []
            return
        if tag == "tr":
            self.row = []
            return
        if tag in ("th", "td"):
            self.cell = []
            return

        if tag == "pre":
            self.in_pre = True
            self.pre_buf = []
            return
        if tag == "code" and not self.in_pre:
            self.emit("`")
            self.pending_inline.append("code")
            return

        if tag == "a":
            href = attr.get("href", "")
            # 标题里的锚点链接（<h2>Foo<a href="#foo">#</a></h2>）：整段丢掉，
            # 否则标题会变成 "# Foo#"
            if self.heading_level is not None and href.startswith("#"):
                self.suppress_anchor = True
                return
            self.link_href = absolutize(href, self.base_url)
            self.link_start = len(self.out)
            self.emit("[")
            self.pending_inline.append("a")
            return
        if tag in ("em", "i"):
            self.emit("*")
            self.pending_inline.append("em")
            return
        if tag in ("strong", "b"):
            self.emit("**")
            self.pending_inline.append("strong")
            return

        if tag in ("ul", "ol"):
            self.list_stack.append([tag, 0])
            return
        if tag == "li":
            if self.list_stack:
                depth = len(self.list_stack) - 1
                kind, counter = self.list_stack[-1]
                if kind == "ol":
                    counter += 1
                    self.list_stack[-1][1] = counter
                    marker = "%d. " % counter
                else:
                    marker = "- "
            else:
                depth = 0
                marker = "- "
            self.emit("\n" + "  " * depth + marker)
            return

        if re.match(r"^h[1-6]$", tag):
            self.heading_level = int(tag[1])
            self.heading_buf = []
            return
        if tag == "blockquote":
            self.quote_depth += 1
            return
        if tag == "br":
            self.emit("\n")
            return
        if tag == "hr":
            self.emit("\n\n---\n\n")
            return

    def handle_endtag(self, tag):
        if tag in SKIP_TAGS:
            self.skip_depth = max(0, self.skip_depth - 1)
            return
        if self.skip_depth:
            return

        if self.in_pre:
            if tag == "pre":
                lang = ""
                self.emit("\n\n```%s\n%s\n```\n" % (lang, "".join(self.pre_buf).strip()))
                self.in_pre = False
                self.pre_buf = []
            return

        if tag == "table":
            self.emit_table()
            self.table_rows = None
            return
        if tag == "tr":
            if self.table_rows is not None and self.row is not None:
                self.table_rows.append(self.row)
            self.row = None
            return
        if tag in ("th", "td"):
            if self.row is not None and self.cell is not None:
                text = " ".join("".join(self.cell).split())
                self.row.append(text)
            self.cell = None
            return

        if tag in ("code", "a", "em", "i", "strong", "b"):
            if tag == "a":
                if self.suppress_anchor:      # 标题内的锚点链接，标题文本里不要它
                    self.suppress_anchor = False
                    return
                inner = ""
                if self.link_start is not None:
                    inner = "".join(str(x) for x in self.out[self.link_start + 1:]).strip()
                # 锚点链接（无文本 / 只有 # ¶ 这类符号 / 页内跳转且文本极短）整段丢弃：
                # 翻译后页内锚点必然失效，留着就是 [](#xxx) 这种噪声
                drop = inner in ("", "#", "¶", "§", "^", "*", "🔗") or (
                    (self.link_href or "").startswith("#") and len(inner) <= 2)
                if self.link_start is not None and drop:
                    del self.out[self.link_start:]
                    self.link_start = None
                    self.link_href = None
                    if "a" in self.pending_inline:
                        self.pending_inline.remove("a")
                    return
                self.emit("](%s)" % (self.link_href or ""))
                self.link_start = None
                self.link_href = None
            elif tag in ("em", "i"):
                self.emit("*")
            elif tag in ("strong", "b"):
                self.emit("**")
            else:
                self.emit("`")
            # 嵌套时栈顶未必是本标签（<strong><a>x</a></strong>），从右往左找最近的
            for i in range(len(self.pending_inline) - 1, -1, -1):
                if self.pending_inline[i] == tag:
                    del self.pending_inline[i]
                    break
            return

        if tag in ("ul", "ol"):
            if self.list_stack:
                self.list_stack.pop()
            if not self.list_stack:
                self.emit("\n")
            return
        if tag == "li":
            self.emit("\n")
            return

        if re.match(r"^h[1-6]$", tag):
            text = " ".join("".join(self.heading_buf).split())
            if text:
                prefix = ">" * self.quote_depth
                self.emit("\n\n%s%s %s\n" % (prefix + " " if prefix else "",
                                             "#" * self.heading_level, text))
            self.heading_level = None
            self.heading_buf = []
            return
        if tag == "blockquote":
            self.flush_quote_lines()      # 先把残余内容输出成引用行
            self.quote_depth = max(0, self.quote_depth - 1)
            self.quote_last_blank = False
            self.emit("\n\n")             # 必须留空行，否则下一段会被算进引用
            return
        if tag in ("p", "div", "section", "article"):
            self.emit("\n\n")
            return

    def handle_data(self, data):
        if self.skip_depth:
            return
        if self.in_pre:
            self.pre_buf.append(data)
            return
        if self.cell is not None:
            self.cell.append(data)
            return
        if self.heading_level is not None:
            if not self.suppress_anchor:
                self.heading_buf.append(data)
            return
        if data.strip():
            self.emit(data)

    def emit_table(self):
        rows = [r for r in (self.table_rows or []) if r]
        if not rows:
            return
        width = max(len(r) for r in rows)
        rows = [r + [""] * (width - len(r)) for r in rows]
        header, body = rows[0], rows[1:]
        lines = ["| " + " | ".join(header) + " |",
                 "|" + "|".join(["---"] * width) + "|"]
        for r in body:
            lines.append("| " + " | ".join(c.replace("|", "\\|") for c in r) + " |")
        self.emit("\n\n" + "\n".join(lines) + "\n")

    def result(self):
        text = "".join(self.out)
        text = re.sub(r"[ \t]+\n", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip() + "\n"


def convert(html_text, base_url=""):
    parser = MarkdownConverter(base_url)
    parser.feed(html_text)
    return parser.result()


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    src = sys.argv[1]
    base_url = sys.argv[3] if len(sys.argv) >= 4 else ""

    if os.path.isdir(src):
        out_dir = sys.argv[2] if len(sys.argv) >= 3 else os.path.join(src, "md")
        os.makedirs(out_dir, exist_ok=True)
        count = 0
        for root, _dirs, names in os.walk(src):
            for name in sorted(names):
                if not name.endswith((".html", ".htm")):
                    continue
                with open(os.path.join(root, name), "r", encoding="utf-8", errors="ignore") as f:
                    md = convert(f.read(), base_url)
                slug = os.path.basename(root)
                target = os.path.join(out_dir, (slug or name) + ".md")
                with open(target, "w", encoding="utf-8") as f:
                    f.write(md)
                count += 1
        print("转换 %d 个文件 -> %s" % (count, out_dir))
        return

    with open(src, "r", encoding="utf-8", errors="ignore") as f:
        md = convert(f.read(), base_url)
    if len(sys.argv) >= 3:
        out_path = sys.argv[2]
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(md)
        print("写出: %s (%d 字节)" % (out_path, len(md.encode("utf-8"))))
    else:
        sys.stdout.write(md)


if __name__ == "__main__":
    main()
