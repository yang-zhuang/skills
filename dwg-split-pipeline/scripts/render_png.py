# -*- coding: utf-8 -*-
"""
render_png.py — 把拆分输出目录里的每张图重渲成两种 PNG 预览图。

输出（与产物同目录）：
    <名称>_白底黑白.png   白底纯黑线，适合插入 Word
    <名称>_黑底彩色.png   黑底原色，CAD / 天正 原生观感

用法:
    python render_png.py <包含 dxf 产物的目录> [--force] [--variants=all|黑底|白底黑白]
    （默认跳过所需图都已生成的，--force 强制重新渲染）
"""
import os
import sys
from pathlib import Path

# stdout 统一 UTF-8；用幂等的 reconfigure，不要替换 sys.stdout 对象
# （重复包装的 TextIOWrapper 被 GC 时会关掉底层 buffer，见 split_dwg.py 说明）
sys.stdout.reconfigure(encoding="utf-8")

import ezdxf
from ezdxf.addons import odafc

from png_export import export_png_pair, variants_from_tokens, PNG_DPI
from split_dwg import read_drawing

ODA_EXE = os.environ.get("ODA_EXE",
                         r"C:\Program Files\ODA\ODAFileConverter 27.1.0\ODAFileConverter.exe")
ezdxf.options.set("odafc-addon", "win_exec_path", ODA_EXE)


def render_dir(root, force=False, which=None):
    """重渲 root 下每张产物的 PNG。which=None 两张全出（见 variants_from_tokens）。"""
    files = sorted(Path(root).rglob("*.dxf"))
    print(f"待渲染: {len(files)} 张")
    for f in files:
        suffixes = which or ("白底黑白", "黑底彩色")
        expect = [f.parent / f"{f.stem}_{s}.png" for s in suffixes]
        if all(p.exists() for p in expect) and not force:
            # 用相对路径打印：产物是 out_root/<序号>/<图名>.dxf，两张图同名
            # （实测示例里有 3 张都叫『倾斜图』）时光看文件名分不清是哪个。
            print(f"跳过（已存在）: {f.relative_to(root)}")
            continue
        try:
            doc = read_drawing(f)
            made = export_png_pair(doc, f, dpi=PNG_DPI, which=which)
            names = ", ".join(f"{p.name}({p.stat().st_size}B)" for p in made)
            print(f"OK: {f.relative_to(root)} -> {names}")
        except Exception as e:
            print(f"FAIL: {f.name} -> {e}")


def main():
    root = Path(sys.argv[1])
    force = "--force" in sys.argv
    which = variants_from_tokens(_opt_variants(sys.argv))
    render_dir(root, force=force, which=which)


def _opt_variants(argv):
    """从 argv 取 --variants= 的值（缺省 all）。"""
    for a in argv:
        if a.startswith("--variants="):
            return a.split("=", 1)[1]
    return "all"


if __name__ == "__main__":
    main()
