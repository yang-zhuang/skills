# -*- coding: utf-8 -*-
"""merge_permille.py — 合并分离的 数字 + ‰ 两个 TEXT 实体为一个 TEXT。

背景（根因）：天正原图作者把倾斜值画成两个实体——数字(h=1000)和 ‰(h=964.5)，
‰ 位置写死为数字插入点+2000 个图形单位。问题有三：
  1. 数字实际渲染宽度 ≠ 2000，‰ 会压在数字上（重叠）
  2. 两个实体高度不同（1000 vs 964.5），大小不一
  3. 数字用 txt.shx 字体，无 ‰ 字形，CAD 用其他字体替换显示，基线对不齐（高低不一）

修复：每对 (数字, ‰) 合并为单实体 "0.88‰"，样式沿用数字原样式。
前提：文档的 Standard 样式应为 gbenor.shx+gbcbig.shx（含 ‰ 字形，国标窄体，
数字宽度与原图一致）；若样式表不正确，请先用 split_dwg.py 重新拆分。

用法:
    python merge_permille.py <dwg文件或目录>     # 处理单个文件或目录下全部 dwg

注意: 文件若正在 CAD 中打开，导出会存成 <名称>_修复.dwg 副本；
     或先在 CAD 中关闭该图（不要保存）再运行本工具。
"""
import io
import os
import sys
from pathlib import Path

import ezdxf
from ezdxf.addons import odafc

ODA_EXE = os.environ.get("ODA_EXE",
                         r"C:\Program Files\ODA\ODAFileConverter 27.1.0\ODAFileConverter.exe")
ezdxf.options.set("odafc-addon", "win_exec_path", ODA_EXE)

TOL = 5.0            # 配对时坐标容差
MAX_GAP = 5000.0     # 数字与 ‰ 的最大距离


def _log():
    if not hasattr(_log, "f"):
        _logf = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                 line_buffering=True)
        _log.f = _logf
    return _log.f


def merge_permille_in_doc(doc):
    """合并 doc 中所有分离的 数字+‰ TEXT 对，返回合并数量。"""
    msp = doc.modelspace()
    texts = [e for e in msp if e.dxftype() == "TEXT"]
    permilles = [e for e in texts if e.dxf.text.strip() == "‰"]
    if not permilles:
        return 0
    numbers = [e for e in texts
               if e.dxftype() == "TEXT" and e.dxf.text.strip() not in ("‰", "")
               and not _is_excluded(e.dxf.text.strip())]

    pairs, orphans = [], []
    for pm in permilles:
        rot = round(pm.dxf.rotation)
        best = None
        for num in numbers:
            if num in [p[0] for p in pairs]:
                continue  # 已被别的 ‰ 配走
            if round(num.dxf.rotation) != rot:
                continue
            nx, ny = num.dxf.insert.x, num.dxf.insert.y
            px, py = pm.dxf.insert.x, pm.dxf.insert.y
            d = None
            if rot == 0:      # 数字在左，‰ 在右，同一基线
                if abs(ny - py) < TOL and 0 < px - nx < MAX_GAP:
                    d = px - nx
            else:             # rot 90：数字在下，‰ 在上，同一 x
                if abs(nx - px) < TOL and 0 < py - ny < MAX_GAP:
                    d = py - ny
            if d is not None and (best is None or d < best[1]):
                best = (num, d)
        if best:
            pairs.append(best)
        else:
            orphans.append(pm)

    merged = 0
    for num, pm in pairs:
        merged_text = num.dxf.text.strip() + "‰"
        rot = num.dxf.rotation
        ins = num.dxf.insert
        layer = num.dxf.layer
        height = num.dxf.height
        style = num.dxf.style  # 沿用数字原样式（应为 gbenor/gbcbig，含 ‰ 字形）
        msp.delete_entity(pm)
        msp.delete_entity(num)
        msp.add_text(merged_text, dxfattribs={
            "style": style,
            "height": height, "rotation": rot,
            "insert": (ins.x, ins.y), "width": num.dxf.get("width", 1.0),
            "layer": layer,
        })
        merged += 1
        print(f"  合并 {merged_text!r} @ ({ins.x:.0f}, {ins.y:.0f}) rot={rot:.0f}",
              file=_log())
    for pm in orphans:
        print(f"  !! 孤立 ‰ 未配对 @ ({pm.dxf.insert.x:.0f}, {pm.dxf.insert.y:.0f})",
              file=_log())
    return merged


def _is_excluded(s):
    """排除轴号/单字符等非数值文字，避免误配。"""
    if len(s) > 8:
        return True
    core = s.replace(".", "").replace("-", "")
    return not core.isdigit()


def process(path: Path):
    print(f"== {path.name}", file=_log())
    doc = odafc.readfile(str(path))
    n = merge_permille_in_doc(doc)
    if n == 0:
        print("  无分离 ‰，跳过", file=_log())
        return False
    dest = path
    try:
        odafc.export_dwg(doc, str(dest), replace=True)
    except Exception as e:
        dest = path.with_name(path.stem + "_修复.dwg")
        odafc.export_dwg(doc, str(dest), replace=True)
        print(f"  原文件被占用，已存副本: {dest.name} ({e})", file=_log())
    print(f"  DONE: 合并 {n} 对 -> {dest.name}", file=_log())
    return True


def main():
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    target = Path(sys.argv[1])
    files = [target] if target.is_file() else sorted(target.rglob("*.dwg"))
    for f in files:
        process(f)


if __name__ == "__main__":
    main()
