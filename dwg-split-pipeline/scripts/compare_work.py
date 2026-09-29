# -*- coding: utf-8 -*-
"""
compare_work.py — 算出「现场副本相对干净 base 改了什么」。

为什么不用 git:
    skill 包是纯目录、不带版本库，别人拿到的现场副本里也没有 git。
    但改动**必须**能算出来 —— 否则经验文档里写"我改了 split_dwg.py 的某某阈值"
    就没有依据，事后也无法复核。

    好在有更简单的办法：base 是**干净且只读**的那一份（发行版 scripts/，
    路径记在 06_work/BASE.txt 里），把现场副本与它逐行比对就行 ——
    不需要 git，不需要额外快照，也不需要人工记"我改了什么"。

用法:
    python compare_work.py <作业单元目录> [--round=N] [--out=文件]

    <作业单元目录>   init_task.py 建出来的那个单元（里面要有 06_work/）
    --round=N       轮次号 → 输出 07_notes/round-N.diff（默认从 state.json 读，+1）
    --out=文件      显式指定输出文件

输出:
    07_notes/round-N.diff   unified diff，可直接喂给提炼 agent 归纳经验
    终端同时打印「改了哪几个文件、每个文件增删几行」

退出码: 0 = 有改动；3 = 与 base 完全一致（没改过）。
"""
import difflib
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")


def parse_base_src(base_txt):
    """从 BASE.txt 里取 base 来源目录（init_task.py 写进去的那行）。"""
    for line in base_txt.read_text(encoding="utf-8").splitlines():
        if line.startswith("# base 来源:"):
            return Path(line.split(":", 1)[1].strip())
    return None


def read_lines(path):
    """按行读文本；编码异常时用 replace 兜底，不因为一个坏字节整体失败。"""
    return path.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)


def main():
    raw = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = [a for a in sys.argv[1:] if a.startswith("--")]
    if not raw:
        print(__doc__.strip())
        sys.exit(2)

    unit = Path(raw[0]).resolve()
    work = unit / "06_work"
    base_txt = work / "BASE.txt"
    if not base_txt.is_file():
        raise SystemExit(f"没有 {base_txt} —— 这个目录不是 init_task.py 建出来的作业单元")

    base_src = parse_base_src(base_txt)
    if base_src is None or not base_src.is_dir():
        raise SystemExit(f"BASE.txt 里记的 base 来源不可用: {base_src}")

    # 轮次：显式给了就用，否则按 state.json 的当前轮次 +1
    rnd = None
    for a in flags:
        if a.startswith("--round="):
            rnd = int(a.split("=", 1)[1])
    if rnd is None:
        state_path = unit / "state.json"
        cur = 0
        if state_path.is_file():
            cur = int(json.loads(state_path.read_text(encoding="utf-8")).get("round") or 0)
        rnd = cur + 1

    out = None
    for a in flags:
        if a.startswith("--out="):
            out = Path(a.split("=", 1)[1])
    if out is None:
        (unit / "07_notes").mkdir(parents=True, exist_ok=True)
        out = unit / "07_notes" / f"round-{rnd}.diff"

    # BASE.txt 里那份清单就是"该有哪些文件"，逐条比对
    names = []
    for line in base_txt.read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) == 2:
            names.append(parts[1].strip())

    chunks, stats = [], []
    for name in sorted(names):
        b, w = base_src / name, work / name
        if not w.is_file():
            stats.append((name, "现场缺失", 0, 0))
            chunks.append(f"--- base/{name}\n+++ 现场缺失（06_work/{name} 不存在）\n")
            continue
        if not b.is_file():
            stats.append((name, "base 已无此文件（新增文件）", 0, 0))
            chunks.append(f"--- base/{name} 不存在\n+++ work/{name}（现场新增）\n")
            continue
        bl, wl = read_lines(b), read_lines(w)
        if bl == wl:
            continue
        diff = list(difflib.unified_diff(bl, wl,
                                         fromfile=f"base/{name}", tofile=f"work/{name}"))
        add = sum(1 for d in diff if d.startswith("+") and not d.startswith("+++"))
        rem = sum(1 for d in diff if d.startswith("-") and not d.startswith("---"))
        stats.append((name, "已改", add, rem))
        chunks.append("".join(diff))

    # 现场多出来的文件（base 清单里没有的）
    listed = set(names)
    extra = sorted(p.name for p in work.iterdir()
                   if p.is_file() and p.name not in listed
                   and p.name not in ("BASE.txt",))
    if extra:
        chunks.append("\n# 现场多出来的文件（base 里没有，可能是新写的工具）\n"
                      + "".join(f"#   {n}\n" for n in extra))

    if not chunks:
        print(f"与 base 完全一致（{base_src}）—— 这一轮没改任何代码")
        sys.exit(3)

    header = (f"# 现场副本 vs base 的差异\n"
              f"# base 来源: {base_src}\n"
              f"# 现场     : {work}\n"
              f"# 轮次     : {rnd}\n"
              f"# 说明     : diff 由 compare_work.py 用 difflib 算出（不依赖 git）。\n"
              f"#            提炼 agent 读这个文件归纳经验，不要手改它。\n\n")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(header + "\n".join(chunks), encoding="utf-8")

    print(f"相对 base 的改动 {len(stats)} 个文件：")
    for name, how, add, rem in stats:
        print(f"  {name:24s} {how:16s} +{add}  -{rem}")
    if extra:
        print(f"  另有多出来的文件 {len(extra)} 个: {', '.join(extra)}")
    print(f"差异文件: {out}")


if __name__ == "__main__":
    main()
