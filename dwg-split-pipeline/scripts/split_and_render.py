# -*- coding: utf-8 -*-
"""
split_and_render.py — 一键入口：给定「含 dwg 的目录」，自动完成拆分 + 渲染。

与既有脚本的关系:
    * split_dwg.py     : 聚类 → 拆分 → 导出 Binary DXF（导出时**顺带渲染**两张 PNG）。
    * render_png.py    : 只对**已有的拆分产物**重渲 PNG（改过渲染代码后补渲用）。
    * split_and_render.py（本文件）: 面向日常使用的**一条龙入口** —— 只需给一个目录，
      自动「找 dwg → 拆分 → 渲染」，不必手写输出目录 / 也不用分两次调用。

用法:
    python split_and_render.py <目录或dwg> [--out=目录] [--margin=3000]
                               [--title-mode=keep|strip|both|auto] [--clean]
                               [--dwg=文件名] [--map=映射.json]
                               [--variants=all|黑底|白底黑白]

    ⚠️ 单文件可以是 dwg 或 dxf；**目录模式只认 .dwg** —— 目录里的选源规则
    （历史 `_t3_auto` 产物排除、多张时挑哪张）是按 dwg 名写的。要给 dxf 就传文件路径。

目录参数（两种都支持，见 --help 语义）:
    * 传 dwg 文件            → 用它的上级目录作为项目目录，直接拆这一张；
    * 传目录，且目录内有 dwg → 处理该目录（多张时按下面的「选源规则」挑一张）；
    * 传目录，但目录内无 dwg → 视为**批量根目录**：递归找出所有「直接含 dwg 的子目录」，
                               每个子目录各出一套「拆分输出/」。

选源规则（一个目录里有多张 dwg 时，如 X.dwg + X_t3.dwg + X_t3_t3.dwg）:
    1. 取「`_t3` 后缀层数**最多**」的那张 —— 即最新一次天正导出的**标准图元**版
       （例：`X.dwg`=0 层 < `X_t3.dwg`=1 层 < `X_t3_t3.dwg`=2 层 → 选 2 层那张）；
    2. 只有一个候选时直接用它；
    3. 最高层数并列（含都为 0 层）→ 报错并列出候选，请用 `--dwg=文件名` 显式指定。

输出（默认）:
    <项目目录>/拆分输出/                       ← 就在 dwg 所在目录下，不另立 split_demo
    ├─ 01/                                     ← 一张图一个**序号目录**（两位补零）
    │   ├─ <图名>.dxf                          ← 有图名用图名，没有用序号（01.dxf）
    │   ├─ <图名>.json
    │   └─ <图名>_白底黑白.png / _黑底彩色.png
    ├─ 02/ …
    └─ split_report.json
    批量模式下（多个项目目录）每个项目各生成自己的「拆分输出/」。
    为什么按序号而非按分类分目录，见 split_dwg 模块 docstring。

示例:
    python split_and_render.py data/example-2
    python split_and_render.py data/example-2 --title-mode=auto --clean
    python split_and_render.py "data/example-2/东西湖税务局平面图及总平图_t3_t3.dwg"
    python split_and_render.py data            # 批量：data 下每个子目录各拆一套
"""
import os
import re
import sys
from pathlib import Path

# 注意：不要在这里再包一层 sys.stdout —— split_dwg 在 import 时已经包过了，
# 重复包装会让前一个 TextIOWrapper 被 GC 时关闭底层 buffer，导致
# "I/O operation on closed file"。UTF-8 输出由 split_dwg 保证。
from split_dwg import (run_split, _title_mode_from_args, TITLE_MODE_KEEP,   # noqa: E402
                       T3RequiredError, prefer_originals)
from png_export import variants_from_tokens  # noqa: E402

OUT_DIRNAME = "拆分输出"      # 默认输出目录名；批量/递归时跳过它，避免把产物当输入

# 批量递归时要**排除**的目录名：输出目录本身 + 历史演示产物目录（split_demo）。
# 这两个目录里装的都是「拆出来的 dwg」，不排除的话批量跑 data/ 会把它们再当输入拆一遍。
SKIP_DIRNAMES = (OUT_DIRNAME, "split_demo")


# --------------------------------------------------------------------------- #
# 找 dwg / 选源
# --------------------------------------------------------------------------- #
def _direct_dwgs(d: Path):
    """目录**直接**含有的 dwg 文件（不递归），按名排序，跳过临时/隐藏文件。

    还要排除**历史** T3 转换器产物（`<名>_t3_auto.dwg`）—— 老作业目录里可能还留着，
    它和源图并排落在同一个目录里，当成源图会让选源规则并列、报「无法唯一确定」。
    （本仓已不再产出这种文件：不做 T3 转换，见 split_dwg.py 常量区注释。）
    """
    out = []
    for p in sorted(d.iterdir()):
        if not p.is_file():
            continue
        if p.suffix.lower() != ".dwg":
            continue
        if p.name.startswith(("~", ".")):
            continue
        out.append(p)
    return prefer_originals(out)


def _project_dirs(root: Path):
    """给定目录 → 返回需要处理的「项目目录」列表。

    * root 本身直接含 dwg  → [root]（单个项目）
    * 否则                 → 递归收集所有「直接含 dwg 的目录」交集（批量）
    递归时跳过 SKIP_DIRNAMES（输出目录 + 历史 split_demo/），避免把上一次的
    拆分产物再当输入拆一遍。
    """
    if _direct_dwgs(root):
        return [root]
    found = []
    for cur, subdirs, files in os.walk(root):
        # 原地裁剪：不进输出目录 / 历史演示目录
        subdirs[:] = [s for s in subdirs
                      if s not in SKIP_DIRNAMES and not s.startswith(".")]
        cur_p = Path(cur)
        if any(f.lower().endswith(".dwg") and not f.startswith(("~", "."))
               for f in files):
            found.append(cur_p)
    return sorted(found)


def _t3_layers(p: Path) -> int:
    """文件名尾部 `_t3` 的**重复层数**：`X.dwg`→0，`X_t3.dwg`→1，`X_t3_t3.dwg`→2。

    层数越多越"新"：天正每导出一次就追加一个 `_t3`。拆分管线要的是最新一次导出的
    标准图元版，所以取层数最多的那张。
    """
    m = re.search(r"(?:_t3)+$", p.stem, re.IGNORECASE)
    return m.group(0).lower().count("_t3") if m else 0


def _pick_source(dwg_dir: Path, want: str | None) -> Path:
    """按「选源规则」在 dwg_dir 里挑一张 dwg 返回；不唯一则抛 ValueError。"""
    cands = _direct_dwgs(dwg_dir)
    if not cands:
        raise ValueError(f"{dwg_dir} 里没有 dwg")
    if want:
        wt = Path(want).stem.lower()
        hit = [p for p in cands if p.name.lower() == want.lower()
               or p.stem.lower() == wt]
        if not hit:
            raise ValueError(
                f"--dwg={want} 未命中；候选：{[p.name for p in cands]}")
        return hit[0]
    if len(cands) == 1:
        return cands[0]
    # 多个候选 → 取 `_t3` 层数最多者；并列则不猜（避免把 X.dwg 当 _t3_t3.dwg 拆）
    best = max(_t3_layers(p) for p in cands)
    top = [p for p in cands if _t3_layers(p) == best]
    if len(top) == 1:
        return top[0]
    raise ValueError(
        f"目录里有 {len(cands)} 张 dwg，无法唯一确定拆分源："
        f"{[p.name for p in cands]}；请用 --dwg= 指定")


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _opt(flags, name, default=None):
    pfx = f"--{name}="
    for a in flags:
        if a.startswith(pfx):
            return a[len(pfx):]
    return default


def run_target(target, *, out=None, margin=3000.0, want=None, map_file=None,
               clean=False, title_mode=TITLE_MODE_KEEP, png_which=None,
               allow_proxy=False):
    """处理一个目标（dwg 文件或目录），返回 (成功数, 失败数)。

    本函数不含任何参数解析 —— main.py / main() 解析完参数后调这里。

    「源图没导出成天正3（含天正代理对象）」不算失败，单独统计成 `需先在天正导出天正3` 清单
    —— 那是源文件格式问题，回天正做一步图形导出即可，不是工具坏了。
    """
    target = Path(target)
    if not target.exists():
        raise SystemExit(f"路径不存在: {target}")

    # 组装「项目目录 → 源图」清单
    if target.is_file():
        if target.suffix.lower() not in (".dwg", ".dxf"):
            raise SystemExit(f"不是 dwg / dxf 文件: {target}")
        jobs = [(target.parent, target)]
    else:
        dirs = _project_dirs(target)
        if not dirs:
            raise SystemExit(f"在 {target} 下没找到任何 dwg")
        jobs = []
        for d in dirs:
            try:
                jobs.append((d, _pick_source(d, want)))
            except ValueError as e:
                print(f"[跳过] {d}: {e}")

    if not jobs:
        raise SystemExit("没有可处理的项目")

    batch = len(jobs) > 1
    print(f"待处理项目: {len(jobs)} 个"
          + ("（批量模式，各出各的 拆分输出/）" if batch else ""))
    print(f"图名策略: {title_mode}   间距阈值: {margin:g}"
          + ("   [--clean 先清空输出]" if clean else ""))
    print(f"渲染配色: {'两张全出' if not png_which else '、'.join(sorted(png_which))}")

    ok = fail = 0
    need_t3 = []                      # 源图没导出成 T3 的项目（不算失败）
    for proj_dir, src in jobs:
        if out:
            dest_root = Path(out) / proj_dir.name if batch else Path(out)
        else:
            dest_root = proj_dir / OUT_DIRNAME
        print(f"\n{'=' * 70}\n[{proj_dir}]  源图: {src.name}  →  输出: {dest_root}\n{'=' * 70}")
        try:
            n_clusters, n_frag, _ = run_split(
                src, dest_root, margin=margin, map_file=map_file, clean=clean,
                title_mode=title_mode, png_which=png_which,
                allow_proxy=allow_proxy)
            ok += 1
            print(f"✓ {proj_dir.name}: {n_clusters} 图"
                  + (f"，{n_frag} 残片未出图" if n_frag else ""))
        except T3RequiredError as e:
            # 源格式不对 ≠ 失败：单独列出来，用户回天正导出一次再跑同一命令即可
            need_t3.append(f"{src}  ——  {e}")
            print(f"⚠ 需先在天正里导出成天正3: {src.name}")
        except Exception as e:            # noqa: BLE001
            fail += 1
            print(f"✗ FAIL {proj_dir}: {type(e).__name__}: {e}")

    print(f"\n{'=' * 70}\n全部完成：成功 {ok} / 失败 {fail}"
          + (f" / 需先在天正导出天正3 {len(need_t3)}" if need_t3 else ""))
    if need_t3:
        print(f"\n以下 {len(need_t3)} 个源图没导出成天正3（含天正代理对象），已跳过"
              f"（不是失败；在天正里导出一次再跑同一条命令即可，断点续传不会跳过它们）：")
        for s in need_t3:
            print(f"  - {s}")
    return ok, fail


def main():
    raw = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = [a for a in sys.argv[1:] if a.startswith("--")]

    if not raw or "--help" in flags or "-h" in flags:
        print(__doc__.strip())
        return

    png_which = variants_from_tokens(_opt(flags, "variants", "all"))
    ok, fail = run_target(
        Path(raw[0]),
        out=_opt(flags, "out"),
        margin=float(_opt(flags, "margin", 3000.0)),
        want=_opt(flags, "dwg"),
        map_file=_opt(flags, "map"),
        clean="--clean" in flags,
        title_mode=_title_mode_from_args(sys.argv[1:]),   # 复用 split_dwg 的校验
        png_which=png_which)
    if fail:
        sys.exit(1)


if __name__ == "__main__":
    main()
