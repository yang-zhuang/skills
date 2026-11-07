"""scan_corpus.py — 从爬取的 blog HTML 语料里扫出术语候选。

目的
    术语表不靠凭空列举，而是从真实语料里扫出来、人工确认后填入 glossary.yaml。

用法（位置参数，不用 flag）
    python scan_corpus.py <语料目录>
    python scan_corpus.py <语料目录> <输出文件>

例
    python scan_corpus.py <博客语料目录>
    python scan_corpus.py <博客语料目录> data/candidates.md

扫什么
    1. ACRONYM  全大写缩写，2-8 字符，如 RLHF / RAG / CoT
    2. CAMEL    首字母大写的复合专有名词，如 LangGraph / Claude / LangSmith
    3. UNIT     参数量写法分布，如 7B / 70B / 1.5T / billion parameters

只依赖标准库。默认输出到 <语料目录> 同级的 data/candidates.md。
"""

import collections
import html
import os
import re
import sys

import _config   # 路径配置唯一来源（同目录），勿再写死绝对路径

# 全大写但显然不是术语的英文常用词
STOP_UPPER = {
    "I", "A", "AN", "THE", "AND", "OR", "BUT", "IF", "IN", "ON", "AT", "TO",
    "OF", "IS", "IT", "AS", "BE", "BY", "DO", "GO", "HE", "ME", "MY", "NO",
    "SO", "UP", "US", "WE", "AM", "ARE", "WAS", "WERE", "YOU", "YOUR", "THIS",
    "THAT", "THESE", "THOSE", "THEN", "THAN", "HERE", "THERE", "WHAT", "WHEN",
    "WHICH", "WHILE", "WITH", "FROM", "FOR", "NOT", "ALL", "CAN", "MAY", "NEW",
    "ONE", "TWO", "OUR", "OUT", "HOW", "WHY", "WHO", "HAS", "HAD", "HAVE",
    "HTML", "CSS", "HTTP", "HTTPS", "URL", "JSON", "YAML", "ID", "OK", "FAQ",
    "NOTE", "TODO", "SEE", "FIG", "TABLE", "ET", "AL", "VS", "PM", "AM",
}

# 首字母大写但出现在句首时会被误判的常用词
STOP_TITLE = {
    "The", "This", "That", "These", "Those", "We", "You", "Your", "Our", "It",
    "Its", "In", "On", "At", "To", "Of", "For", "With", "From", "But", "And",
    "Or", "If", "So", "As", "By", "An", "A", "When", "What", "Why", "How",
    "While", "Then", "There", "Here", "However", "Moreover", "Furthermore",
    "First", "Second", "Third", "Finally", "Now", "Let", "Consider", "Note",
    "For", "See", "Figure", "Table", "Below", "Above", "After", "Before",
    "Because", "Since", "Thus", "Therefore", "Instead", "Rather", "Also",
    "Both", "Each", "Every", "Some", "Most", "Many", "Much", "More", "Less",
    "One", "Two", "Three", "Another", "Other", "Others", "Such", "Same",
    "I", "My", "Me", "He", "She", "They", "Them", "Their", "His", "Her",
}

RE_SCRIPT_STYLE = re.compile(r"(?is)<(script|style)\b.*?</\1>")
RE_TAG = re.compile(r"(?s)<[^>]+>")
RE_ACRONYM = re.compile(r"\b[A-Z][A-Z0-9]{1,7}\b")
RE_CAMEL = re.compile(r"\b[A-Z][a-zA-Z]{2,}\b")
RE_UNIT = re.compile(r"\b\d+(?:\.\d+)?\s?[KMBT]\b")
RE_UNIT_WORD = re.compile(r"\b\d+(?:\.\d+)?\s?(?:billion|million|trillion)\b", re.I)
RE_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")


def strip_html(raw):
    """去掉 script/style 与全部标签，返回纯文本。"""
    raw = RE_SCRIPT_STYLE.sub(" ", raw)
    raw = RE_TAG.sub(" ", raw)
    return html.unescape(raw)


def context_of(text, word, width=90):
    """取该词首次出现的一段上下文，方便人工判断是不是术语。"""
    m = re.search(r"\b" + re.escape(word) + r"\b", text)
    if not m:
        return ""
    start = max(0, m.start() - width // 3)
    snippet = text[start:m.end() + width]
    snippet = " ".join(snippet.split())
    return snippet[:width]


def scan(corpus_dir):
    files = []
    for root, _dirs, names in os.walk(corpus_dir):
        for name in names:
            if name == "content.html":
                files.append(os.path.join(root, name))
    files.sort()

    acronyms = collections.Counter()
    camel = collections.Counter()
    units = collections.Counter()
    unit_words = collections.Counter()
    ctx_acronym = {}
    ctx_camel = {}
    n_files = 0

    for path in files:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            raw = f.read()
        text = strip_html(raw)
        if not text.strip():
            continue
        n_files += 1

        for w in RE_ACRONYM.findall(text):
            if w not in STOP_UPPER:
                acronyms[w] += 1
                ctx_acronym.setdefault(w, context_of(text, w))
        for w in RE_CAMEL.findall(text):
            if w not in STOP_TITLE:
                camel[w] += 1
                ctx_camel.setdefault(w, context_of(text, w))
        for w in RE_UNIT.findall(text):
            units[w.replace(" ", "")] += 1
        for w in RE_UNIT_WORD.findall(text):
            unit_words[w.lower().replace(" ", "")] += 1

    return {
        "n_files": n_files,
        "acronyms": acronyms,
        "camel": camel,
        "units": units,
        "unit_words": unit_words,
        "ctx_acronym": ctx_acronym,
        "ctx_camel": ctx_camel,
    }


def render(result, top=120):
    out = []
    out.append("# 术语候选（由 scan_corpus.py 从语料扫描生成）\n")
    out.append("扫描文件数：%d\n" % result["n_files"])
    out.append("")
    out.append("说明：本文件是**候选**，不是结论。人工确认后填入 `glossary.yaml`。\n")

    out.append("## 1. 缩写 ACRONYM（全大写，需决定：展开 / 保留 / 翻译）\n")
    out.append("| 缩写 | 次数 | 首次出现的上下文 |")
    out.append("|---|---|---|")
    for w, c in result["acronyms"].most_common(top):
        if c < 3:
            break
        ctx = result["ctx_acronym"].get(w, "").replace("|", "\\|")
        out.append("| %s | %d | %s |" % (w, c, ctx))
    out.append("")

    out.append("## 2. 复合专有名词 CAMEL（模型名 / 产品名 / 库名，通常 keep）\n")
    out.append("| 词 | 次数 | 首次出现的上下文 |")
    out.append("|---|---|---|")
    for w, c in result["camel"].most_common(top):
        if c < 5:
            break
        ctx = result["ctx_camel"].get(w, "").replace("|", "\\|")
        out.append("| %s | %d | %s |" % (w, c, ctx))
    out.append("")

    out.append("## 3. 参数量写法 UNIT（决定 units 规则：符号保留还是换算）\n")
    out.append("### 3.1 符号写法\n")
    out.append("| 写法 | 次数 |")
    out.append("|---|---|")
    for w, c in result["units"].most_common(40):
        out.append("| %s | %d |" % (w, c))
    out.append("")
    out.append("### 3.2 英文词写法\n")
    out.append("| 写法 | 次数 |")
    out.append("|---|---|")
    for w, c in result["unit_words"].most_common(40):
        out.append("| %s | %d |" % (w, c))
    out.append("")

    return "\n".join(out) + "\n"


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    corpus_dir = sys.argv[1]
    if len(sys.argv) >= 3:
        out_path = sys.argv[2]
    else:
        out_path = os.path.join(_config.PROJECT_ROOT, "data", "candidates.md")   # 仓库根 data/，从脚本位置推导
    os.makedirs(os.path.dirname(out_path), exist_ok=True)

    if not os.path.isdir(corpus_dir):
        print("目录不存在: %s" % corpus_dir)
        sys.exit(1)

    result = scan(corpus_dir)
    text = render(result)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(text)
    print("扫描文件数: %d" % result["n_files"])
    print("缩写候选: %d 个（>=3 次）" % sum(1 for _w, c in result["acronyms"].most_common() if c >= 3))
    print("复合词候选: %d 个（>=5 次）" % sum(1 for _w, c in result["camel"].most_common() if c >= 5))
    print("输出: %s" % out_path)


if __name__ == "__main__":
    main()
