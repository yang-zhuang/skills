"""collect_assets.py -- 物化 md 里的图片与 SVG 资产。

用法:
    python collect_assets.py <task_pack_dir> <md_file>

作用（机制，幂等）:
- 扫描 md 中所有 input/assets/<name> 引用；
- 把对应文件复制到 <md 所在目录>/assets/（已存在则跳过）；
- md 中的引用改写为 assets/<name>（相对 md 自身，交付物脱离任务包也能渲染）。
- 扫描 ```svg <name> 围栏代码块（agent 依作者内容绘制的图）：
  物化为 assets/<name>.svg，围栏块整体替换为 ![](assets/<name>.svg) 图片引用；
  围栏名必须以 svg- 开头（svg-001 等），与原文图 img-* 区分审计来源。
- 不含 input/ 前缀的引用不动（那是 verify_assets.py 的管辖范围）。
"""
import re
import shutil
import sys
from pathlib import Path

REF = re.compile(r"(input/assets/([A-Za-z0-9._\-]+))")
SVG_BLOCK = re.compile(r"```svg[ \t]+(svg-[A-Za-z0-9._\-]+)[ \t]*\n(.*?)\n```", re.S)


def main():
    if len(sys.argv) != 3:
        sys.exit("用法: python collect_assets.py <task_pack_dir> <md_file>")
    pack = Path(sys.argv[1])
    md_path = pack / sys.argv[2]
    if not md_path.is_file():
        sys.exit(f"文件不存在: {md_path}")

    text = md_path.read_text(encoding="utf-8")
    out_assets = md_path.parent / "assets"
    copied, missing = [], []

    # 1) agent 绘制的 SVG 围栏块 -> assets/svg-*.svg + 图片引用
    svg_done = 0
    for m in list(SVG_BLOCK.finditer(text)):
        name, body = m.group(1), m.group(2)
        if not name.endswith(".svg") and "." not in name:
            pass  # 命名规范 svg-NNN，扩展名在此统一补
        fname = name if name.endswith(".svg") else f"{name}.svg"
        out_assets.mkdir(exist_ok=True)
        (out_assets / fname).write_text(body.strip() + "\n", encoding="utf-8")
        text = text.replace(m.group(0), f"![{fname}（agent 依原文内容绘制）](assets/{fname})")
        svg_done += 1

    # 2) 原文图片引用 -> assets/
    for full, name in dict.fromkeys(REF.findall(text)):
        src = pack / "input" / "assets" / name
        if not src.is_file():
            missing.append(full)
            continue
        out_assets.mkdir(exist_ok=True)
        dst = out_assets / name
        if not dst.exists():
            shutil.copy2(src, dst)
            copied.append(name)
        text = text.replace(full, f"assets/{name}")

    md_path.write_text(text, encoding="utf-8")
    if missing:
        print("MISSING（未物化，引用原样保留）:")
        for m in missing:
            print(f"  - {m}")
        sys.exit(1)
    print(f"done: 原文图物化 {len(copied)} 张，SVG 围栏物化 {svg_done} 个 -> {out_assets}，引用已改写为相对路径")


if __name__ == "__main__":
    main()
