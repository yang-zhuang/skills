# -*- coding: utf-8 -*-
"""
check_env.py — 拆图管线的外部依赖自检。

为什么单独做成一个脚本:
    这个工具链有**三个外部依赖**，缺任何一个都跑不动，而且失败方式各不相同
    （ODA 缺 → 读不了 dwg；字体缺 → 能出图但轴号/尺寸发虚，静默降级；
    转换器缺 → 撞到天正图时拦下）。它们都不在本仓库里，别人拿到 skill 后
    第一件事就该是跑这个脚本，而不是等拆到一半才发现。

用法:
    python check_env.py

检查项:
    1. ODA FileConverter   —— ezdxf 经它读写 DWG，等价于硬依赖
    2. SHX 字体目录        —— 渲染用；缺了不报错，但轴号/尺寸数字会发虚
    3. T3 转换器命令       —— 可选；没配也能跑，只是撞到天正图时会拦下

退出码: 0 = 三项都齐（或转换器未配且其余两项齐）；1 = 有硬依赖缺失。
"""
import glob
import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

from env_file import load_env

load_env()      # 配置只写在 .env 里、没设系统环境变量时也能正确自检

OK, BAD, WARN = "✓", "✗", "!"


def check_oda():
    exe = os.environ.get(
        "ODA_EXE", r"C:\Program Files\ODA\ODAFileConverter 27.1.0\ODAFileConverter.exe")
    p = Path(exe)
    if p.is_file():
        return True, f"ODA FileConverter: {exe}"
    return False, (f"ODA FileConverter 不存在: {exe}\n"
                   "    → 装 ODA FileConverter 并改 .env 的 ODA_EXE；"
                   "没它读不了 dwg（dxf 可以不走它）")


def check_shx():
    raw = os.environ.get("SHX_FONT_DIRS", "")
    if not raw:
        return False, ("SHX_FONT_DIRS 未设置\n"
                       "    → .env 里给上 AutoCAD Fonts（SIMPLEX/COMPLEX/TXT/ROMANS）\n"
                       "      和天正 SYS 目录；分号分隔，支持 glob")
    hits, shx = [], []
    for part in raw.split(os.pathsep):
        part = part.strip()
        if not part:
            continue
        for d in glob.glob(part):
            dp = Path(d)
            if dp.is_dir():
                hits.append(d)
                shx += [f.name for f in dp.glob("*.shx")]
    if not hits:
        return False, f"SHX_FONT_DIRS 里的目录一个都不存在: {raw}"
    if not shx:
        return False, f"目录存在但里面没有 .shx 文件: {hits}"
    # 关键字体逐一验名：轴号(_TCH_AXIS=COMPLEX)与尺寸数字(_TCH_DIM=SIMPLEX)缺了
    # 任何一个都会被 ezdxf 静默替换成 TTF 替身 —— 填充路径不读线宽，加粗机制
    # 整个失效，数字细成发丝。这是 2026-09-23 实战踩过的坑：目录有 11 个 shx、
    # 本项却报 ✓，实际 SIMPLEX 在另一个盘的 AutoCAD Fonts 里。
    low = {n.lower() for n in shx}
    missing = [k for k in ("simplex", "complex", "txt", "gbenor") if not any(
        f == k + ".shx" or f.startswith(k + "0.") for f in low)]
    if missing:
        return False, (f"缺少关键 SHX 字体: {', '.join(m + '.shx' for m in missing)}\n"
                       f"    → 已有目录 {hits} 里没有它们；AutoCAD 若装在非 Program Files，\n"
                       f"      默认通配匹配不上，把实际 Fonts 目录补进 SHX_FONT_DIRS\n"
                       f"    → 症状：轴号/尺寸数字发虚，加粗无效（TTF 替身不读线宽）")
    sample = ", ".join(sorted(set(n.lower() for n in shx))[:6])
    return True, f"SHX 字体目录 {len(hits)} 个，命中 {len(set(n.lower() for n in shx))} 个字体（如 {sample}）"


def main():
    print("拆图管线外部依赖自检\n" + "=" * 60)
    results = [("ODA FileConverter", check_oda()), ("SHX 字体目录", check_shx())]

    hard_fail = False
    for title, (ok, msg) in results:
        mark = OK if ok else (WARN if ok is None else BAD)
        if ok is False:
            hard_fail = True
        print(f"[{mark}] {title}\n    {msg}\n")

    print("=" * 60)
    if hard_fail:
        print("有硬依赖缺失 —— 先补上再往下走（拆到一半才发现更费时间）")
        sys.exit(1)
    print("可以开工。下一步: python init_task.py <作业根目录> <源图>")


if __name__ == "__main__":
    main()
