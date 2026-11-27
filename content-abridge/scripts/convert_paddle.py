"""convert_paddle.py -- PaddleOCR-VL 解析结果转 markdown + 图片本地化。

用法:
    python convert_paddle.py <paper_dir> <out_dir>

    <paper_dir>  论文目录，须含 paddle_ocr_vl_1_6/paddle_ocr_vl.json 和 paddle_ocr_vl_1_6/imgs/
                 （即 acl-anthology 每篇论文目录，如 G:/acl-anthology/papers/ACL/2026/2026.acl-long.270）
    <out_dir>    输出目录，产出 source.md / assets/ / assets-manifest.json，
                 与 convert.py 的输出同构，可直接接 build_task_pack.py

转换规则（全确定性，无判断）:
    1. 逐页按块序遍历 paddle_ocr_vl.json：
       - label=fig 的块 → 在当前位置插入 `![](assets/img-NNN.<ext>)`（alt 留空：
         alt 属判断层，按方针 B3 由起草角色从图面/图注写，转换阶段宁缺勿补）；
       - 含 page_content 的块 → 原文 markdown 直接拼接。
    2. 图片复制进 out_dir/assets/，按出现顺序重命名 img-001/002/...，同一
       figure_path 复用同一文件名（路径级去重，确定性）。
    3. 数学区转义自检：若检出 `$...$` 内有 `\\_`，调用 repair_math 修复
       （PaddleOCR 直出 markdown，理论上不该有，检出即说明上游变了，宁可修不可漏）。
    4. 输出 manifest：{source, images: [{file, original, page}]}。
"""
import json
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import repair_math

JSON_REL = Path("paddle_ocr_vl_1_6") / "paddle_ocr_vl.json"


def convert(paper_dir: Path, out_dir: Path):
    jpath = paper_dir / JSON_REL
    if not jpath.is_file():
        sys.exit(f"缺少 {jpath}")
    # front matter 拦截：各卷 .0 目录是论文集前言（致辞/Keynote 摘要/组委会名单），
    # 不是论文（2026-10-05 批次 0 实测，metadata.title 以 "Proceedings of" 开头）
    meta_path = paper_dir / "raw" / "metadata.json"
    if meta_path.is_file():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        if (meta.get("title") or "").startswith("Proceedings of"):
            sys.exit(f"front matter 目录（卷首 matter，非论文），跳过: {paper_dir.name}")
    data = json.loads(jpath.read_text(encoding="utf-8"))
    imgs_dir = jpath.parent / "imgs"

    assets = out_dir / "assets"
    assets.mkdir(parents=True, exist_ok=True)

    manifest = {"source": str(jpath), "images": []}
    by_original = {}
    pieces = []
    counter = 0
    for page_no, page in enumerate(data["pages"]):
        for blk in page:
            if not isinstance(blk, dict):
                continue
            if blk.get("label") == "fig" and blk.get("figure_path"):
                orig = blk["figure_path"]
                if orig not in by_original:
                    counter += 1
                    src = imgs_dir / Path(orig).name
                    if not src.is_file():
                        sys.exit(f"fig 块引用的图不存在: {src}")
                    name = f"img-{counter:03d}{src.suffix.lower()}"
                    shutil.copy2(src, assets / name)
                    by_original[orig] = name
                    manifest["images"].append(
                        {"file": name, "original": orig, "page": page_no})
                pieces.append(f"![](assets/{by_original[orig]})")
            elif "page_content" in blk:
                pieces.append(blk["page_content"])
            # 其余块类型（表格框、页眉页脚等）当前不产出，遇到新类型再扩

    text = "\n\n".join(pieces)
    text = re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"

    # 数学区转义自检（与 convert.py 同一修复管线；正常应为 0 处）
    blocks = repair_math._fenced_blocks(text)
    spans = repair_math._protected_spans(text, blocks)
    hits = repair_math.bare_superscripts(text, blocks, spans)
    esc_in_math = len(re.findall(r"\$[^$\n]*\\_[^$\n]*\$", text))
    if esc_in_math or hits:
        fixed, _ = repair_math.repair_markdown_escapes(text)
        text = fixed

    (out_dir / "source.md").write_text(text, encoding="utf-8")
    (out_dir / "assets-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"source.md {len(text)} 字符，图片 {len(manifest['images'])} 张，"
          f"页数 {len(data['pages'])}，数学区转义 {esc_in_math} 处")
    return out_dir


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    convert(Path(sys.argv[1]), Path(sys.argv[2]))


if __name__ == "__main__":
    main()
