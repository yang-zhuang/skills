"""verify_assets.py -- markdown 图片引用一致性校验。

用法:
    python verify_assets.py <task_pack_dir> <md_file>

    <md_file>  相对 <task_pack_dir> 的路径，如 draft/draft.md 或 revised/revised-r1.md

校验规则:
1. md 里所有图片引用（![](...) 与 <img src=...>）必须能解析到真实存在的文件；
   引用路径相对 md 所在目录解析，input/assets/ 开头的路径相对任务包根解析。
   alt 允许含方括号（图里常出现 `[CLS]`、引文编号之类的字面方括号），解析按惰性匹配到 `](` 为止。
2. 对 draft/revised: md 同级 assets/ 目录里不允许出现 md 未引用的图（防"被删章节的图"混入）。
3. 对 draft/revised（长文档任务包才有 source-full.md）: 引用了"已被 trim 删除的段"里的图
   → FAIL。判定口径：被删图片集 = source-full.md 的图片集合 − 剥离版 source.md 的图片集合
   （trim 是纯整行删除，该差集与 trim-manifest 行范围推出的图片归属严格等价，§10.6）。
   场景：末页图被 OCR 归进 References 节、trim 后图随之消失，draft 若再引它即漏查。
非零退出 = 校验失败。
"""
import re
import sys
from pathlib import Path

IMG_REF = re.compile(r"!\[(.*?)\]\(([^)\s]+)\)|<img[^>]+src=[\"']([^\"']+)[\"']", re.I)


def is_remote(ref):
    return ref.startswith(("http://", "https://", "data:"))


def _img_names(md_path: Path):
    return {Path(m.group(2) or m.group(3)).name
            for m in IMG_REF.finditer(md_path.read_text(encoding="utf-8"))}


def trimmed_out_images(inp: Path):
    """被 trim 删掉的段的图片名集合。无 source-full.md（短文档/未 trim）返回空集。"""
    full = inp / "source-full.md"
    strip = inp / "source.md"
    if not full.is_file():
        return set()
    return _img_names(full) - _img_names(strip)


def main():
    if len(sys.argv) != 3:
        sys.exit("用法: python verify_assets.py <task_pack_dir> <md_file>")
    pack = Path(sys.argv[1])
    md_path = pack / sys.argv[2]
    if not md_path.is_file():
        sys.exit(f"文件不存在: {md_path}")

    text = md_path.read_text(encoding="utf-8")
    refs = [m.group(2) or m.group(3) for m in IMG_REF.finditer(text)]
    errors = []
    local_refs = []
    for ref in refs:
        if is_remote(ref):
            errors.append(f"远程引用未本地化: {ref}")
            continue
        if ref.startswith("input/"):
            target = pack / ref
        else:
            target = (md_path.parent / ref).resolve()
        if not target.is_file():
            errors.append(f"引用不存在: {ref}")
        else:
            local_refs.append(ref)

    if "draft/" in sys.argv[2] or "revised/" in sys.argv[2]:
        gone = trimmed_out_images(pack / "input")
        for ref in local_refs:
            if Path(ref).name in gone:
                errors.append(f"{ref} 来自被 trim 删除的段（全图集差集命中），不得再引用")
        sibling_assets = md_path.parent / "assets"
        if sibling_assets.is_dir():
            referenced = {Path(r).name for r in local_refs}
            for f in sibling_assets.iterdir():
                if f.is_file() and f.name not in referenced:
                    errors.append(f"{sibling_assets.name}/{f.name} 存在但未被 md 引用（疑似被删章节的图）")

    if errors:
        print("VERIFY FAILED:")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    print(f"ok: {len(local_refs)} 张本地图引用全部可解析，无多余图"
          f"（远程引用 {sum(1 for r in refs if is_remote(r))} 个）")


if __name__ == "__main__":
    main()
