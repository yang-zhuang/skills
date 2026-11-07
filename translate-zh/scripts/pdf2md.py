"""pdf2md.py — born-digital PDF → markdown（抽文字层）。

目的
    arXiv / ACL 的新论文多为 born-digital PDF（有文字层）。这个脚本把文字层抽成
    平面 markdown，标题层级交给 sections.py detect 补 —— 全程本地确定性代码。
    OCR 是扫描件才需要的重服务调用，born-digital 不必走。

用法（位置参数，不用 flag）
    python pdf2md.py <pdf文件> [输出md]
    python pdf2md.py <pdf目录> [输出目录]

例
    python pdf2md.py <ARXIV_ROOT>/2026/2609.24972/raw/paper.pdf
    python pdf2md.py <ACL_ROOT>/ACL/2024/2024.acl-long.1/raw out_md

行为
    - 逐页抽文字层，页与页之间以空行分隔；每页末尾的页码行（纯数字独行）剔除
    - 平均每页文字 < 200 字符 -> 判为扫描件，退出码 2，提示改走 OCR
    - 输出不做结构猜测（哪行是标题留给 sections.py detect --apply，人确认 MID 候选）

后续标准动作（born-digital 论文完整路径）
    python pdf2md.py <pdf> paper.md
    python sections.py detect paper.md --apply -o paper.structured.md   # HIGH 插为标题，MID 列出待确认

依赖
    pypdf（pip install pypdf -i https://pypi.org/simple）。此外零依赖。
"""

import os
import sys

MIN_CHARS_PER_PAGE = 200     # 平均每页低于这个字符数 -> 判扫描件


def extract(pdf_path):
    """抽整个 PDF 的文字层，返回 (文本, 页数)。扫描件返回 (None, 页数)。"""
    try:
        from pypdf import PdfReader
    except ImportError:
        print("缺依赖 pypdf。安装：pip install pypdf -i https://pypi.org/simple", file=sys.stderr)
        sys.exit(3)
    reader = PdfReader(pdf_path)
    pages = []
    for page in reader.pages:
        text = (page.extract_text() or "").strip()
        pages.append(strip_page_number(text))
    n = len(pages)
    total = sum(len(p) for p in pages)
    if n == 0 or total / n < MIN_CHARS_PER_PAGE:
        return None, n
    return "\n\n".join(p for p in pages if p), n


def strip_page_number(text):
    """删掉页面末尾孤立的页码行（纯数字、罗马数字、'Page 3 of 12'）。"""
    lines = text.split("\n")
    while lines:
        s = lines[-1].strip()
        if s.isdigit() or s.lower() in ("i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x") \
                or s.lower().startswith("page "):
            lines.pop()
        else:
            break
    return "\n".join(lines).strip()


def convert_one(pdf_path, out_path):
    text, n_pages = extract(pdf_path)
    if text is None:
        print("!! 扫描件（平均每页文字 < %d 字符，共 %d 页）：改走 OCR，本文件未输出"
              % (MIN_CHARS_PER_PAGE, n_pages), file=sys.stderr)
        return False
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(text + "\n")
    print("%s -> %s（%d 页，%d 字符）"
          % (os.path.basename(pdf_path), out_path, n_pages, len(text)))
    return True


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    src = sys.argv[1]

    if os.path.isdir(src):
        out_dir = sys.argv[2] if len(sys.argv) >= 3 else os.path.join(src, "md")
        os.makedirs(out_dir, exist_ok=True)
        done = scanned = 0
        for root, _dirs, names in os.walk(src):
            for name in sorted(names):
                if not name.lower().endswith(".pdf"):
                    continue
                pdf = os.path.join(root, name)
                out = os.path.join(out_dir, os.path.splitext(name)[0] + ".md")
                if convert_one(pdf, out):
                    done += 1
                else:
                    scanned += 1
        print("完成：born-digital %d 篇 -> %s；扫描件 %d 篇（需 OCR）" % (done, out_dir, scanned))
        sys.exit(0)

    if not os.path.exists(src):
        print("文件不存在: %s" % src, file=sys.stderr)
        sys.exit(1)
    out = sys.argv[2] if len(sys.argv) >= 3 else os.path.splitext(src)[0] + ".md"
    parent = os.path.dirname(os.path.abspath(out))
    if parent:
        os.makedirs(parent, exist_ok=True)
    ok = convert_one(src, out)
    sys.exit(0 if ok else 2)


if __name__ == "__main__":
    main()
