# -*- coding: utf-8 -*-
"""
batch_t3.py — 「一个根目录下所有 t3 文件夹」批量拆分：一图一个序号目录 + 断点续传。

场景（检测报告演示库）:
    <根>/<项目>/<子项>/t3/t3.dwg        ← 实测 595 个 t3 文件夹，每个恰好一张 t3.dwg
t3 在**任意深度**（实测 592 个在第 3 层、3 个在第 4 层），不写死层数。
对**每一个** t3 文件夹，在它**同级**建一个 dwg拆分输出/，把该 t3 里的每张 dwg 拆成
一张张单图，**每张图一个序号目录**（两位补零，序号 = 拆图顺序）：

    <根>/<项目>/<子项>/dwg拆分输出/
    ├─ 01/
    │   ├─ <图名>.dxf
    │   ├─ <图名>.json                  ← 该图的元数据（含 category / title_missing）
    │   └─ <图名>_白底黑白.png / _黑底彩色.png
    ├─ 02/ …
    └─ split_report.json                ← 本源的汇总（同时充当断点续传的完成标记）

    序号目录而不是分类目录：插 Word 时按目录名排序就是拆图顺序；分类是判断、
    可能出错，只留在 json 的 category 里，不进路径（理由见 split_dwg 模块 docstring）。

与其他入口的分工:
    main.py / split_and_render.py  给**一个**dwg 或目录，拆一套（同样的序号目录结构）
    batch_t3.py（本文件）          给**一个根**，递归找全部 t3 文件夹，各拆一套

命名规则（「宁缺毋滥」）:
    * 图名 = 簇内**唯一**一条中文图名（形态判据）或唯一一条并入图名；
    * 退一步，簇内**只有一条**关键词标签（如『二层平面布置图』）时用它 —— 那是图上
      真实写着的字，不是推断；
    * 多条标签**严禁拼接**（曾产出 300+ 字的怪物文件名，见 split_dwg.build_clusters）；
    * 以上都取不到 → 文件名 = 它所在目录的序号（如 `03/03.dxf`），json 里
      title_missing=true。为什么宁可给序号名也不猜：源图内容非常多，用关键词/‰
      推断出的名字（旧逻辑的『平面示意图N』『倾斜观测示意图』）经常是错的。

断点续传:
    以「输出目录里已存在 split_report.json」为该源**已完成**的标记 —— run_split 是
    在所有簇都导出完毕之后才写这个文件的。重跑时直接跳过，已完成的不再碰。
    中途崩掉/被 Ctrl+C 的源没有这个标记，下一轮自动重跑（同名产物覆盖写）。
    要强制重跑 → --force（先清空该源的 dwg拆分输出/ 再跑）。

处理顺序:
    全部源 dwg 按**文件大小升序** —— 小文件先出结果，长跑中途就能看到产物。

用法:
    python batch_t3.py <根目录>
    python batch_t3.py "C:/Users/123/Desktop/演示/检测报告/2025"            # 全量
    python batch_t3.py "C:/Users/123/Desktop/演示/检测报告/2025" --limit=5  # 只拆最小的 5 个源

选项:
    --limit=N      只处理体积最小的 N 个源 dwg（小批量测试用；不加=全量）
    --force        已完成的不跳过：先清空该源的 dwg拆分输出/ 再重跑
    --variants=all|黑底|白底黑白           渲染哪些配色，逗号组合（默认 all）
    --title-mode=keep|strip|both|auto       导出时图名文字去留（默认 keep，与 CAD 一致）
    --margin=3000  聚类间距阈值
    --allow-proxy  源图含天正代理对象（没导出成 T3）时也强行拆，默认拦下报错

自检（第 0 步，在写任何产物之前）:
    run_split 读图后立刻数 ACAD_PROXY_ENTITY（天正自定义实体的代理对象）。> 0 就抛
    T3RequiredError 拦下 —— 这种源没导出成「天正3（T3）」，硬拆只能得到
    「轴线+图名+‰」的残图（详见 split_dwg.count_proxy_entities）。所以：
      * 它**不是失败**，汇总里单独统计成「需先在天正导出天正3」清单；
      * 抛在写 split_report.json 之前 → 没有完成标记 → 在天正里导出好 T3 后
        再跑同一条命令会自动重拆，不需要 --force。

环境变量: 同 main.py（ODA_EXE / SHX_FONT_DIRS，读仓库根目录的 .env）
"""
import argparse
import json
import os
import shutil
import sys
from pathlib import Path

# .env 必须先于 split_dwg 导入 —— split_dwg 在 import 时就读 ODA_EXE。
# main.py 模块级已经调过一次 load_env()，这里显式再调（幂等：已有变量不覆盖）。
from main import load_env                                          # noqa: E402
load_env()

from split_dwg import (run_split, TITLE_MODE_KEEP, TITLE_MODE_STRIP,   # noqa: E402
                       TITLE_MODE_BOTH, TITLE_MODE_AUTO, T3RequiredError,
                       prefer_originals)
from png_export import variants_from_tokens                        # noqa: E402

try:
    from tqdm import tqdm                    # 进度条（以「源 dwg」为单位）
except ImportError:                          # 没装也不影响拆分，只是少个进度条
    tqdm = None

# 与 t3 同级、放该源全部产物的目录名
OUT_DIRNAME = "dwg拆分输出"

# 断点续传标记：这个文件在 = 该源上一轮已跑完（run_split 最后才写它）
DONE_MARKER = "split_report.json"


def find_t3_dirs(root):
    """root 下所有名为 t3 的目录（**任意深度**，不分大小写），按路径排序。

    用 os.walk 而不是 Path.rglob / glob.glob，唯一目的是**原地裁剪**：
    rglob 会把每个 dwg拆分输出/ 也整个走一遍（全量跑完那里有上万个文件），
    重复跑纯属白遍历。这里看到产物目录/隐藏目录就掉头，看到 t3 就记下并不再往下
    （源就是 t3 里的那张 dwg，它下面不可能还有另一个源）。
    写法与 split_and_render._project_dirs 的剪枝一致。
    """
    found = []
    for cur, subdirs, _ in os.walk(root):
        keep = []
        for s in subdirs:
            if s.lower() == "t3":
                found.append(Path(cur) / s)
            elif s != OUT_DIRNAME and not s.startswith("."):
                keep.append(s)
        subdirs[:] = keep        # 原地裁剪：os.walk 才会真的不再往下走
    return sorted(found)


def done_image_count(marker):
    """从完成标记里读回该源上一轮**出了几张图**（cluster 非空的条目数）。

    为什么值得读：跳过时只打一行「跳过（已完成）」的话，**0 张的源和还没跑的源
    看起来一模一样** —— 用户会以为"怎么没产物"，其实是那张 dwg 本身就是空的
    （实测 13KB 的 t3.dwg 是 0 实体空文件）。读不出返回 -1，调用方按"不显示张数"处理。
    """
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
        return sum(1 for r in data if r.get("cluster") is not None)
    except Exception:                                            # noqa: BLE001
        return -1


def collect_sources(root):
    """→ [(源 dwg, 该源的输出目录)]，按 dwg 体积**升序**。

    一个 t3 里有几张 dwg 就出几条（实测都是 1 张 t3.dwg），输出目录都是同一个 ——
    该 t3 同级的 dwg拆分输出/。

    每个 t3 目录里先剔掉**历史** T3 转换器产物（`<名>_t3_auto.dwg`，老作业目录里
    可能还留着，它和源图并排落在同一个目录里）：不剔的话，同一个
    dwg拆分输出/ 会被拆两遍、序号目录互相覆盖。本仓已不再产出这种文件
    （不做 T3 转换，见 split_dwg.py 常量区注释）。
    """
    jobs = []
    for t3 in find_t3_dirs(root):
        dwgs = [f for f in sorted(t3.iterdir())
                if (f.is_file() and f.suffix.lower() == ".dwg"
                    and not f.name.startswith(("~", ".")))]
        for f in prefer_originals(dwgs):
            jobs.append((f, t3.parent / OUT_DIRNAME))
    jobs.sort(key=lambda job: job[0].stat().st_size)
    return jobs


def _parse(argv):
    ap = argparse.ArgumentParser(
        prog="batch_t3.py",
        description="批量拆分：递归找根目录下所有 t3 文件夹，各拆一套、每张图一个子文件夹"
                    "放进同级的 " + OUT_DIRNAME + "/（详见本文件 docstring）",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", help="根目录（其下每个 t3 文件夹各出一个 " + OUT_DIRNAME + "/）")
    ap.add_argument("--limit", type=int,
                    help="只处理体积最小的 N 个源 dwg（小批量测试；不加=全量）")
    ap.add_argument("--force", action="store_true",
                    help="已完成的不跳过：先清空该源的 " + OUT_DIRNAME + "/ 再重跑")
    ap.add_argument("--variants", default="all",
                    help="渲染哪些配色: all / 黑底 / 白底黑白 / 逗号组合（默认 all）")
    ap.add_argument("--title-mode", default=TITLE_MODE_KEEP,
                    choices=[TITLE_MODE_KEEP, TITLE_MODE_STRIP,
                             TITLE_MODE_BOTH, TITLE_MODE_AUTO],
                    help="导出时图名文字去留（默认 keep；命名一律照用识别到的图名）")
    ap.add_argument("--margin", type=float, default=3000.0, help="聚类间距阈值（默认 3000）")
    ap.add_argument("--allow-proxy", action="store_true",
                    help="源图含天正代理对象（没导出成 T3）时也强行拆，"
                         "默认拦下报错（硬拆只能得到缺内容的残图）")
    return ap.parse_args(argv)


def main():
    args = _parse(sys.argv[1:])
    root = Path(args.root)
    if not root.is_dir():
        raise SystemExit(f"根目录不存在: {root}")

    jobs = collect_sources(root)
    if not jobs:
        raise SystemExit(f"在 {root} 下没找到任何 t3 文件夹里的 dwg")

    todo = jobs if args.limit is None else jobs[:max(args.limit, 0)]
    png_which = variants_from_tokens(args.variants)

    print(f"根目录: {root}")
    print(f"源 dwg: 共 {len(jobs)} 个，按体积升序"
          + (f"，本次只处理最小 {len(todo)} 个（--limit={args.limit}）"
             if args.limit is not None else "，全量处理"))
    print(f"图名策略: {args.title_mode}   间距阈值: {args.margin:g}   渲染配色: "
          + ("两张全出" if not png_which else "、".join(sorted(png_which))))
    print(f"输出: 每个 t3 同级的 {OUT_DIRNAME}/<序号>/（一张图一个序号目录）"
          + ("   [--force 重跑已完成项]" if args.force else ""))

    ok = skip = fail = 0
    failed = []
    empty = []               # 跑通了但一张图都没拆出来的源（源 dwg 是空图）
    need_t3 = []             # 源图没导出成天正3（含天正代理对象），已跳过 —— 不是失败
    # 进度条以「源 dwg」为单位 —— 一个源内部能拆出几张，跑起来才知道。
    seq = tqdm(todo, desc="拆分源 dwg", unit="张") if tqdm else todo
    for i, (src, dest) in enumerate(seq, 1):
        tag = f"[{i}/{len(todo)}]"
        # 断点续传：标记在 = 上一轮跑完了，跳过
        if (dest / DONE_MARKER).exists() and not args.force:
            skip += 1
            n = done_image_count(dest / DONE_MARKER)
            print(f"{tag} 跳过（已完成" + (f"，{n} 张" if n >= 0 else "") + "）: "
                  f"{src.parent.parent.name}/{src.parent.name}"
                  + ("   ← 源 dwg 里没有可拆的图元（空图），不是没跑"
                     if n == 0 else ""))
        else:
            # --force：只删我们自己生成的那种目录，别的一律不动
            if args.force and dest.name == OUT_DIRNAME and dest.exists():
                shutil.rmtree(dest)
            print(f"\n{'=' * 70}\n{tag} {src}\n{'-' * 70}")
            try:
                n_clusters, n_frag, _ = run_split(
                    src, dest, margin=args.margin,
                    title_mode=args.title_mode, png_which=png_which,
                    allow_proxy=args.allow_proxy)
                ok += 1
                if not n_clusters:
                    empty.append(str(src))
                print(f"✓ {src.parent.parent.name}: {n_clusters} 张"
                      + (f"，{n_frag} 残片未出图" if n_frag else "")
                      + f"  → {dest}")
            except T3RequiredError:
                # 源格式不对 ≠ 失败。run_split 是在写完成标记**之前**抛的，所以
                # 这个源没有 split_report.json → 在天正里导出好 T3 后再跑会自动重拆。
                need_t3.append(str(src))
                print(f"⚠ {tag} 需先在天正导出天正3，已跳过: "
                      f"{src.parent.parent.name}/{src.parent.name}")
            except Exception as e:                              # noqa: BLE001
                fail += 1
                failed.append(str(src))
                print(f"✗ FAIL {src}: {type(e).__name__}: {e}")
        if tqdm is not None:
            seq.set_postfix_str(f"成功 {ok} / 需导出T3 {len(need_t3)} / 跳过 {skip} / 失败 {fail}")

    print(f"\n{'=' * 70}\n全部完成：成功 {ok} / 跳过 {skip} / 失败 {fail}"
          + (f" / 需先在天正导出天正3 {len(need_t3)}" if need_t3 else ""))
    if need_t3:
        # 天正自定义实体（墙体/门窗/轴号/尺寸/房间填充）在非天正环境下只剩代理对象，
        # ezdxf 读不到几何 —— 这种源硬拆只能得到「轴线+图名+‰」的残图，所以拦下。
        print(f"\n以下 {len(need_t3)} 个源图没导出成天正3（含天正代理对象），已跳过"
              f"（不是失败；在天正里导出一次再跑同一条命令，它们没有完成标记，会自动重拆）：")
        for s in need_t3[:10]:
            print(f"  - {s}")
        if len(need_t3) > 10:
            print(f"  … 另有 {len(need_t3) - 10} 个")
    if empty:
        # 空图不算失败（跑通了，就是没东西可拆），但必须报出来 —— 否则用户看到
        # 一个只有 split_report.json 的目录，会以为工具坏了。
        print(f"本次有 {len(empty)} 个源一张图都没拆出来（源 dwg 里没有可拆的图元，"
              f"产物只有 split_report.json）：")
        for e in empty[:10]:
            print(f"  - {e}")
        if len(empty) > 10:
            print(f"  … 另有 {len(empty) - 10} 个")
    if failed:
        print("失败清单（没有完成标记，下一轮会自动重跑）：")
        for f in failed:
            print(f"  - {f}")
    if fail:
        sys.exit(1)


if __name__ == "__main__":
    main()
