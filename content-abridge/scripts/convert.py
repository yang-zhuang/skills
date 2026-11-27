r"""convert.py -- 本地 HTML 文章转 markdown + 图片本地化 + manifest。

用法:
    python convert.py <article_dir> <out_dir>

    <article_dir>  语料文章目录，内含 content.html（可选 meta.json，用其 url 做 base）
    <out_dir>      输出目录（即任务包的 input/），产出:
                     source.md            正文 markdown，图片引用为相对路径 assets/img-NNN.ext
                     assets/img-NNN.ext   下载的图片（稳定文件名）
                     assets-manifest.json {"图片原src": {"file": "img-001.png", "status": "ok|failed"}}

说明:
- img 标签的 src/srcset/data-src 都会尝试；data: 内联图跳过。
- 下载失败的图保留原 URL 写入 markdown，manifest 里标 failed。
- 转换后自动调用 `repair_markdown_escapes()`：还原数学区（`$...$` / `$$...$$`）与代码区（围栏、
  行内反引号）里被 markdownify 转义的 `\_` `\*`——不修会让 KaTeX 把下标渲染成字面下划线字符。
- 依赖: beautifulsoup4, markdownify, requests（见同目录 README.md）。
"""
import json
import re
import sys
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md

from repair_math import repair_markdown_escapes

HEADERS = {"User-Agent": "Mozilla/5.0 (convert.py; content-distill)"}


def find_img_src(img):
    for attr in ("src", "data-src", "data-original"):
        v = img.get(attr)
        if v and not v.startswith("data:"):
            return v.strip()
    srcset = img.get("srcset") or img.get("data-srcset")
    if srcset:
        first = srcset.split(",")[0].strip().split(" ")[0]
        if first and not first.startswith("data:"):
            return first
    return None


def pick_ext(resp, url_path):
    ct = (resp.headers.get("Content-Type") or "").split(";")[0].strip()
    ext_map = {"image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif",
               "image/webp": ".webp", "image/svg+xml": ".svg"}
    if ct in ext_map:
        return ext_map[ct]
    suffix = Path(urlparse(url_path).path).suffix.lower()
    return suffix if suffix in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"} else ".png"


def main():
    if len(sys.argv) != 3:
        sys.exit("用法: python convert.py <article_dir> <out_dir>")
    article_dir = Path(sys.argv[1])
    out_dir = Path(sys.argv[2])
    html_path = article_dir / "content.html"
    if not html_path.is_file():
        sys.exit(f"找不到 {html_path}")

    base_url = ""
    meta_path = article_dir / "meta.json"
    if meta_path.is_file():
        base_url = json.loads(meta_path.read_text(encoding="utf-8")).get("url", "")

    soup = BeautifulSoup(html_path.read_text(encoding="utf-8"), "lxml")

    # 收集图片 src -> 稳定名，下载，替换引用
    manifest = {}
    seen = {}
    session = requests.Session()
    session.headers.update(HEADERS)
    for i, img in enumerate(soup.find_all("img"), 1):
        src = find_img_src(img)
        if not src:
            img.decompose()
            continue
        abs_src = urljoin(base_url, src) if base_url else src
        if src in seen:
            img["src"] = f"assets/{seen[src]}"
            img.attrs = {"src": img["src"]}
            continue
        stable = f"img-{len(seen) + 1:03d}"
        status = "failed"
        try:
            resp = session.get(abs_src, timeout=30)
            resp.raise_for_status()
            ext = pick_ext(resp, abs_src)
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "assets").mkdir(exist_ok=True)
            (out_dir / "assets" / f"{stable}{ext}").write_bytes(resp.content)
            stable = f"{stable}{ext}"
            status = "ok"
        except Exception as e:
            stable = src  # 失败则 markdown 里保留原 URL
            print(f"[warn] 图片下载失败 {abs_src}: {e}", file=sys.stderr)
        seen[src] = stable
        manifest[abs_src] = {"file": stable if status == "ok" else "", "status": status,
                             "stable_name": stable if status == "ok" else None}
        if status == "ok":
            img.attrs = {"src": f"assets/{stable}"}
        else:
            img.attrs = {"src": abs_src}

    # 去掉 script/style 后转 markdown
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    markdown = md(str(soup), heading_style="ATX", bullets="-")
    markdown = re.sub(r"\n{3,}", "\n\n", markdown).strip() + "\n"
    markdown, fixed = repair_markdown_escapes(markdown)
    if fixed:
        print(f"[info] 已还原数学区/代码区的转义污染 {fixed} 处"
              f"（markdownify 会把 $a_t$ 写成 $a\\_t$，KaTeX 会渲染成字面下划线而非下标）")

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "source.md").write_text(markdown, encoding="utf-8")
    (out_dir / "assets-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    n_ok = sum(1 for v in manifest.values() if v["status"] == "ok")
    print(f"done: source.md ({len(markdown)} chars), images ok/total = {n_ok}/{len(manifest)}")


if __name__ == "__main__":
    main()
