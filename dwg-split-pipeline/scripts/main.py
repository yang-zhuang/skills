# -*- coding: utf-8 -*-
"""
main.py — dwg-split 统一入口。

给一个 dwg / dxf 文件或目录，一条命令完成「渲整图 / 拆分 + 渲染」。
**只处理已经是天正3（T3）格式的图**：源图若还带着读不到几何的天正对象
（没导出成 T3），会被拦下并提示你回天正导出一次（本工具不做这个转换 —— 理由见
split_dwg.py 常量区注释）。
本文件只做三件事：读 .env → 解析参数 → 调度到各模块（功能全部实现在模块里，
这里没有业务逻辑）：

    split_dwg.py         读图 / 自检 / 聚类拆分（run_split、read_drawing）
    png_export.py        PNG 渲染（export_png_pair、overview_png）
    split_and_render.py  「目录 → 源图」的查找与选源（run_target）
    render_png.py        只重渲已有产物（render_png.render_dir）
    cluster_dwg.py       只聚类探查不出图（cluster_dwg.run）

用法:
    python main.py <dwg文件或目录> [选项]

    # 日常
    python main.py "D:/图纸/李家墩泵房_t3.dwg"              # ① 单个 dwg 文件
    python main.py "D:/图纸/某项目"      --title-mode=auto  # ② 目录（自动选 _t3 最深的那张）
    python main.py "D:/图纸"             --variants=黑底    # ③ 批量 + 只出黑底彩色

    # 拆图作业里的两步（作业目录结构见 SKILL.md；源图必须先由人导出成天正3）
    python main.py ../01_source/x_t3.dwg         --overview               # 第 1 步 整图参照
    python main.py ../01_source/x_t3.dwg         --out=../03_split --clean --title-mode=auto

选项:
    --variants=all|黑底|白底黑白           渲染哪些配色，逗号组合（默认 all 两张全出）
    --title-mode=keep|strip|both|auto       图名（轴网下方文字）去留，默认 keep
    --out=目录                              输出目录（默认 <dwg 所在目录>/拆分输出/）
    --clean                                 先清空输出目录再跑
    --dwg=文件名                            目录里有多张 dwg 时显式指定拆哪张
    --margin=3000                           聚类间距阈值
    --map=映射.json                         手动覆盖某簇的分类/命名
    --overview                              只渲染**整张未拆分源图**的 PNG（dwg/dxf 均可）
    --rerender                              只重渲已有拆分产物的 PNG（target 为拆分输出目录）
    --cluster                               只聚类探查，不出图
    --allow-proxy                           源图没导出成天正3（含天正代理对象）时也强行跑
                                            （产物会缺这些对象的几何，默认拦下报错）

环境变量（.env 文件或系统环境变量均可；命令行参数管行为，环境变量管机器路径）:
    ODA_EXE        ODAFileConverter.exe 完整路径
    SHX_FONT_DIRS  SHX 字体目录，分号分隔，支持 glob
"""
import argparse
import sys
from pathlib import Path

from env_file import load_env

load_env()   # .env 先于一切模块导入 —— 各模块在 import 时就读 ODA_EXE / SHX_FONT_DIRS

from png_export import variants_from_tokens                            # noqa: E402
from render_png import render_dir                                      # noqa: E402
from split_and_render import run_target                                # noqa: E402


def main():
    ap = argparse.ArgumentParser(
        prog="main.py",
        description="DWG 拆分 + 渲染一条龙（详见模块 docstring 与 README.md）",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target", help="dwg/dxf 文件、含图纸的目录、或（--rerender 时）拆分输出目录")
    ap.add_argument("--variants", default="all",
                    help="渲染哪些配色: all / 黑底 / 白底黑白 / 逗号组合（默认 all）")
    ap.add_argument("--title-mode", default="keep",
                    choices=["keep", "strip", "both", "auto"], help="图名去留（默认 keep）")
    ap.add_argument("--out", help="输出目录（默认 <dwg 所在目录>/拆分输出/）")
    ap.add_argument("--clean", action="store_true", help="先清空输出目录再跑")
    ap.add_argument("--dwg", help="目录里有多张 dwg 时指定拆哪张（文件名）")
    ap.add_argument("--margin", type=float, default=3000.0, help="聚类间距阈值（默认 3000）")
    ap.add_argument("--map", help="映射 json：手动覆盖某簇的分类/命名")
    ap.add_argument("--overview", action="store_true",
                    help="只渲染**整张未拆分源图**的 PNG（拆分前的对照基准）")
    ap.add_argument("--rerender", action="store_true",
                    help="只重渲已有拆分产物的 PNG（不重新拆分）")
    ap.add_argument("--cluster", action="store_true", help="只聚类探查，不出图")
    ap.add_argument("--allow-proxy", action="store_true",
                    help="源图含天正代理对象（没导出成 T3）时也强行跑，"
                         "默认拦下报错（硬跑只能得到缺内容的残图）")
    args = ap.parse_args()

    target = Path(args.target)
    if not target.exists():
        raise SystemExit(f"路径不存在: {target}")

    png_which = variants_from_tokens(args.variants)

    # ---- 只聚类探查 --------------------------------------------------------
    if args.cluster:
        import cluster_dwg
        if target.is_dir():
            raise SystemExit("--cluster 需要给单个 dwg 文件，不是目录")
        cluster_dwg.run(target, margin=args.margin)
        return

    # ---- 只渲整图（作业流程的第 1 步，拆分前的对照基准）----------------------
    if args.overview:
        import split_dwg
        from png_export import overview_png
        if not target.is_file():
            raise SystemExit("--overview 需要给单个 dwg / dxf 文件")
        doc = split_dwg.read_drawing(target)
        n_all, n_exploded = split_dwg.explode_proxy_graphics(doc)
        if n_exploded:
            print(f"✓ 从显示缓存炸开 {n_exploded}/{n_all} 个天正对象（渲染用）")
        n = split_dwg.count_proxy_entities(doc)
        if n and not args.allow_proxy:
            # 与拆分侧同口径：有读不到几何的天正对象就拦下（这张图没导出成天正3），
            # 否则用来当"对照基准"的整图本身就是残图，比出来的差异毫无意义。
            raise SystemExit(
                f"✗ {target.name} 里有 {n} 个天正代理对象（没导出成天正3）—— "
                f"渲出来的整图会缺这些对象的几何，不能当拆分产物的对照基准。\n"
                f"  请先在**天正**里把它导出为天正3（T3）再来渲：\n"
                f"    天正命令行敲 TSaveAs（或菜单『图形导出』）→ 保存类型选『天正3』。\n"
                f"  就是想看这张图: 加 --allow-proxy")
        if n:
            print(f"⚠ 整图缺 {n} 个天正对象的几何（按 --allow-proxy 照渲，别当基准用）")
        made = overview_png(doc, target, which=png_which, out_dir=args.out)
        print(f"整图（未拆分）渲染完成，{len(made)} 张：")
        for p in made:
            print(f"  ✓ {p.name}  ({p.stat().st_size:,} B)")
        return

    # ---- 只重渲 ------------------------------------------------------------
    if args.rerender:
        if not target.is_dir():
            raise SystemExit("--rerender 需要给拆分输出目录")
        render_dir(target, force=args.clean, which=png_which)
        return

    # ---- 默认：拆分 + 渲染 -------------------------------------------------
    ok, fail = run_target(
        target, out=args.out, margin=args.margin, want=args.dwg,
        map_file=args.map, clean=args.clean, title_mode=args.title_mode,
        png_which=png_which, allow_proxy=args.allow_proxy)
    if fail:
        sys.exit(1)


if __name__ == "__main__":
    main()
