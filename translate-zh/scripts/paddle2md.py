"""paddle2md.py — PaddleOCR-VL json → markdown（扫描件路径的最后一步）。

目的
    ocr_pdfs.py 的产物是 <paper>/paddle_ocr_vl_1_6/paddle_ocr_vl.json + imgs/ 切图。
    这个脚本把 json 拼回单个 markdown 文件 —— 服务端每页的 page_content 本身就是
    markdown（带 # 标题），所以转换是纯确定性拼装，零依赖，只做三件事：
    1. 各页 page_content 依次拼接
    2. fig 块转成 ![](图片路径)，统一放在该页文字之后（json 不保留图在页内的位置，
       这是有损点的如实声明；图片坐标只藏在文件名里，不解析）
    3. 图片路径按输出 md 的位置改写成相对路径，保证 md 移动后图片还能渲染

json 结构（ocr_pdfs.py 写出）
    {total_pages, pages: [[block, ...], ...]}
    block = {page_index, page_content}            每页恰好一个，整页 markdown
          | {label: "fig", figure_path: "imgs/…"} 该页切图，路径相对 json 所在目录

用法（位置参数，不用 flag）
    python paddle2md.py <json文件> [输出md]
    python paddle2md.py <目录> [输出目录]        # 递归找所有结构匹配的 json

例
    python paddle2md.py <ACL_ROOT>/ACL/2026/2026.acl-demo.0/paddle_ocr_vl_1_6
    python paddle2md.py <ARXIV_ROOT>/2026/2609.24972/paddle_ocr_vl_1_6/paddle_ocr_vl.json paper.md

产物之后接：sections.py detect <md> --apply -o paper.structured.md（born-digital 路径同款收尾）。
"""

import json
import os
import sys


def is_paddle_json(path):
    """结构判定：顶层是 {pages: [...]}，且至少一页含 page_content 块。不匹配的 json 静默跳过。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        if not isinstance(d, dict) or not isinstance(d.get("pages"), list) or not d["pages"]:
            return False
        first_texts = [b for b in d["pages"][0] if isinstance(b, dict) and "page_content" in b]
        return bool(first_texts)
    except Exception:
        return False


def convert(json_path, out_path):
    """一个 json -> 一个 md。返回 (输出路径, 页数, 图数)。"""
    with open(json_path, "r", encoding="utf-8") as f:
        d = json.load(f)
    json_dir = os.path.dirname(os.path.abspath(json_path))
    out_dir = os.path.dirname(os.path.abspath(out_path))

    parts = []
    n_imgs = 0
    for page in d["pages"]:
        texts = [b for b in page if isinstance(b, dict) and "page_content" in b]
        figs = [b for b in page if isinstance(b, dict) and b.get("label") == "fig" and b.get("figure_path")]
        page_md = "\n\n".join(b["page_content"].strip() for b in texts if b["page_content"].strip())
        img_md = []
        for b in figs:
            img_abs = os.path.normpath(os.path.join(json_dir, b["figure_path"]))
            # 相对输出 md 的路径（正斜杠），md 挪走图片也还能渲染；源图不在了就按原样写
            if os.path.exists(img_abs):
                rel = os.path.relpath(img_abs, out_dir).replace("\\", "/")
            else:
                rel = b["figure_path"].replace("\\", "/")
            img_md.append("![](%s)" % rel)
        n_imgs += len(img_md)
        block = "\n\n".join(p for p in [page_md] + img_md if p)
        if block:
            parts.append(block)
    text = "\n\n".join(parts) + "\n"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(text)
    return out_path, len(d["pages"]), n_imgs


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    src = sys.argv[1]

    if os.path.isdir(src):
        out_dir = sys.argv[2] if len(sys.argv) >= 3 else None    # 缺省原地生成（json 旁边）
        done = 0
        for root, _dirs, names in os.walk(src):
            for name in sorted(names):
                if not name.lower().endswith(".json"):
                    continue
                p = os.path.join(root, name)
                if not is_paddle_json(p):
                    continue
                out = os.path.join(out_dir, os.path.splitext(name)[0] + ".md") if out_dir \
                    else os.path.splitext(p)[0] + ".md"
                if out_dir:
                    os.makedirs(out_dir, exist_ok=True)
                out_path, n_pages, n_imgs = convert(p, out)
                print("%s -> %s（%d 页，%d 图）" % (p, out_path, n_pages, n_imgs))
                done += 1
        print("完成：%d 个 json 已转 md" % done)
        if done == 0:
            print("没找到结构匹配的 json（顶层需有 pages: [...] 且含 page_content 块）")
            sys.exit(1)
        return

    if not os.path.exists(src):
        print("文件不存在: %s" % src, file=sys.stderr)
        sys.exit(1)
    if not is_paddle_json(src):
        print("不是 paddle json（顶层需有 pages: [...] 且含 page_content 块）: %s" % src, file=sys.stderr)
        sys.exit(1)
    out = sys.argv[2] if len(sys.argv) >= 3 else os.path.splitext(src)[0] + ".md"
    parent = os.path.dirname(os.path.abspath(out))
    if parent:
        os.makedirs(parent, exist_ok=True)
    out_path, n_pages, n_imgs = convert(src, out)
    print("%s -> %s（%d 页，%d 图）" % (src, out_path, n_pages, n_imgs))


if __name__ == "__main__":
    main()
