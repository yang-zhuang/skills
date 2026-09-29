# -*- coding: utf-8 -*-
"""
init_task.py — 一条命令搭出一个「拆图作业」的固定目录结构。

为什么要它（而不是让 agent 手工 mkdir + cp）:
    作业目录结构是**固定**的（见 SKILL.md「作业目录结构」一节），每个环节的产物
    都落在确定的编号目录里，子 agent 拿到路径即可读写、不必搜索 —— 省时间也省 token。

    但"固定"必须靠程序保证。让 agent 手工搭，是 6~10 次工具调用：建 7 个目录、
    复制 base、写指纹、写 state.json …… 任何一次漏掉（比如忘了写 BASE.txt），
    后面就判断不了 06_work/ 里哪些文件被改过，经验也就无法回溯到 base。

用法:
    python init_task.py <作业根目录> <源图> [<源图> ...]

    <源图> 可以是 dwg 或 dxf。一个作业根下可以有**多张**源图 ——
    每张源图各建一个同名的作业单元目录，结构完全一样（因为后面每个环节
    都是按源图切分的：整图是一源一张，报告是一源一份，代码副本也必须一源一份）。

产出（每张源图一个单元）:
    <作业根>/<源图主干>/
    ├─ 01_source/      源图副本（只读；**必须已经是天正3（T3）**，见 SKILL.md 核心边界）
    ├─ 02_overview/    整图渲染 PNG
    ├─ 03_split/       拆分产物（01/ 02/ … + split_report.json）
    ├─ 04_verify/      校验报告.json
    ├─ 05_review/      视觉意见.json
    ├─ 06_work/        base 副本（改这里）+ BASE.txt 指纹
    ├─ 07_notes/       NOTES.md + round-N.diff
    └─ state.json      当前跑到哪一步
    <作业根>/_batch.json   本作业根下一共几个单元、各自状态

为什么不含 git:
    skill 包本身不带版本库（别人拿到的是纯目录），所以现场也不建 git。
    「改了哪些行」靠 compare_work.py 把 06_work/ 与**干净 base**（本脚本所在目录，
    即发行版的 scripts/）逐行比对得出 —— 不需要 git，也不需要额外的快照。

重复运行安全:
    已存在的目录与文件**一律不覆盖**，只补缺失的。作业跑了一半再执行一次不会清掉进度。
    要重建请自己删掉那个单元目录（本脚本不做任何删除）。
"""
import json
import os
import shutil
import stat
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

# 固定结构：编号 = 流程顺序。子 agent 拿路径即读即写，不搜索。
STAGE_DIRS = ("01_source", "02_overview", "03_split",
              "04_verify", "05_review", "06_work", "07_notes")

# base 的来源 = **本脚本所在目录** —— 这样脚本无论放在主仓根（开发时）
# 还是发行版 scripts/（别人用时），拿到的都是那一份正确的 base。
BASE_SRC = Path(__file__).resolve().parent

# 不进现场的两类：
#   pack_skill.py  本仓打包工具，现场不需要
#   BASE_EXTRAS    归发行版 scripts/extras/ 的参考代码库 —— 六个环节不依赖它们，
#                  也不参与现场比对（compare_work 只比 BASE.txt 里列的文件）。
# ⚠️ BASE_EXTRAS 必须与 pack_skill.py 的 EXTRAS 一致。
# ⚠️ cluster_dwg.py **不在**这里：main.py 的 --cluster 分支在函数体里 import 它，
#    它属于必须复制的那 11 个核心文件。
# ⚠️ 主仓 scripts/*.sh 也不进现场：那是写死本机路径的命令备忘，发行版里没有它，
#    复制进去只会让"你本地现场"和"收件人现场"多出 18 个文件、BASE.txt 对不上。
BASE_SKIP = {"pack_skill.py",
             # 材料表收集是独立试验工具（2026-09-29 用户定），不属 skill 流程：
             # 不进发行版、也不复制进现场
             "material_table.py", "batch_material_table.py"}
BASE_EXTRAS = {"batch_t3.py", "merge_permille.py"}

SUPPORTED_SUFFIXES = (".dwg", ".dxf")


def sha256(path):
    """文件内容的 sha256；分块读，图纸级大文件也不吃内存。"""
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as fp:
        for chunk in iter(lambda: fp.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def copy_editable(src, dst):
    """复制并把只读位去掉。

    源图常是只读的（实测 examples/示例1 的 dwg 是 r--r--r--），copy2 会把只读属性
    一起带过来，之后想删都不好删。现场副本一律可写。
    """
    shutil.copy2(src, dst)
    os.chmod(dst, stat.S_IWRITE | stat.S_IREAD)


def copy_base(work_dir):
    """把核心 base 的 .py 复制进 06_work/，返回 [(文件名, sha256)]。

    只复制**根目录的核心 .py**（11 个）—— 与 pack_skill.py 的发行版归类保持一致：
    主仓（开发区）与发行版复制出的现场必须一模一样，否则 compare_work 拿现场
    BASE.txt 逐条比对时会得出错误结论（多出来的文件会被当成"现场新增"）。
    """
    done = []
    for p in sorted(BASE_SRC.glob("*.py")):
        if p.name in BASE_SKIP or p.name in BASE_EXTRAS:
            continue
        dst = work_dir / p.name
        if not dst.exists():
            shutil.copy2(p, dst)
        done.append((p.name, sha256(dst)))
    return done


def write_base_txt(work_dir, digests, source_dwg):
    """记录现场副本的 base 指纹 —— 事后判断"改前的基线是哪一份"全靠它。"""
    lines = [
        "# 现场 base 指纹 —— 由 init_task.py 生成，请勿手改",
        f"# 生成时间: {datetime.now():%Y-%m-%d %H:%M:%S}",
        f"# base 来源: {BASE_SRC}",
        f"# 源图    : {source_dwg}",
        "# 说明    : 比对 06_work/ 与 base 来源目录（发行版 scripts/）即可算出改动，",
        "#           命令: python compare_work.py <作业单元目录>",
        f"# 文件数  : {len(digests)}",
    ]
    for name, digest in sorted(digests):
        lines.append(f"{digest}\t{name}")
    (work_dir / "BASE.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def init_unit(root, source_dwg):
    """为一张源图建作业单元；返回 (单元目录, 是否新建)。"""
    source_dwg = Path(source_dwg).resolve()
    unit = root / source_dwg.stem
    existed = unit.exists()

    for d in STAGE_DIRS:
        (unit / d).mkdir(parents=True, exist_ok=True)

    # 源图复制进 01_source/（不移动原件 —— 绝不动用户的原始图纸）
    dst_src = unit / "01_source" / source_dwg.name
    if not dst_src.exists():
        copy_editable(source_dwg, dst_src)

    digests = copy_base(unit / "06_work")
    # .env 也要带进现场：main.py 是「先读 .env 再 import 各模块」，而 ODA_EXE /
    # SHX_FONT_DIRS 都是 import 时就取的值 —— 现场没有 .env
    # 就会退到代码里写死的默认路径，换台机器直接跑不动。
    # 发行版的 scripts/.env 就是配置样板（含作者本机路径，无密钥），一并复制过来；
    # 改完跑 check_env.py 验。
    src_env = BASE_SRC / ".env"
    if src_env.is_file() and not (unit / "06_work" / src_env.name).exists():
        shutil.copy2(src_env, unit / "06_work" / src_env.name)
    write_base_txt(unit / "06_work", digests, source_dwg)

    state_path = unit / "state.json"
    if not state_path.exists():
        state_path.write_text(json.dumps({
            "job": source_dwg.stem,
            "source": str(source_dwg),
            "source_in_job": f"01_source/{source_dwg.name}",
            "created": f"{datetime.now():%Y-%m-%d %H:%M:%S}",
            "round": 0,
            "stage": "init",
            "history": [],
        }, ensure_ascii=False, indent=1), encoding="utf-8")

    # 07_notes/NOTES.md 先落一个空模板，让修改 agent 有确定的地方追加
    notes = unit / "07_notes" / "NOTES.md"
    if not notes.exists():
        notes.write_text(
            f"# {source_dwg.stem} —— 本张图的坑\n\n"
            "> 每轮由修改 agent 追加。写清：现象 → 判据 → 改了哪个文件哪一行 → "
            "改成什么；以及试过但没用的做法。\n"
            "> 收敛后由提炼 agent 归纳进主仓 references/LESSONS.md。\n\n",
            encoding="utf-8")
    return unit, not existed


def main():
    raw = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(raw) < 2:
        print(__doc__.strip())
        sys.exit(2)

    root = Path(raw[0]).resolve()
    sources = [Path(a) for a in raw[1:]]

    bad = [p for p in sources if p.suffix.lower() not in SUPPORTED_SUFFIXES]
    if bad:
        raise SystemExit(f"只吃 dwg / dxf，认不出的: {[str(p) for p in bad]}")
    missing = [p for p in sources if not p.is_file()]
    if missing:
        raise SystemExit(f"文件不存在: {[str(p) for p in missing]}")

    stems = [p.stem for p in sources]
    dup = {s for s in stems if stems.count(s) > 1}
    if dup:
        # 单元目录以源图主干命名，同名会互相覆盖
        raise SystemExit(f"源图主干重名，无法各自建单元: {sorted(dup)}；请先改名")

    root.mkdir(parents=True, exist_ok=True)
    units = []
    for src in sources:
        unit, is_new = init_unit(root, src)
        units.append({"job": unit.name, "source": str(src), "created": is_new})
        print(f"{'新建' if is_new else '已存在，只补缺失'}  {unit}")

    batch = root / "_batch.json"
    if batch.exists():
        old = json.loads(batch.read_text(encoding="utf-8"))
        for u in old.get("units", []):              # 保留历史单元，不丢进度
            if not any(v["job"] == u["job"] for v in units):
                units.append(u)
    batch.write_text(json.dumps({
        "root": str(root),
        "updated": f"{datetime.now():%Y-%m-%d %H:%M:%S}",
        "n_units": len(units),
        "units": units,
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"\n作业根: {root}  单元 {len(units)} 个")
    print("下一步（在 06_work/ 里跑发行版自带的那份代码）:")
    for u in units:
        n = u["job"]
        print(f"  cd {root / n / '06_work'}")
        print(f"  python main.py ../01_source/<源图> --overview --out=../02_overview   # 整图参照")
        print(f"  python main.py ../01_source/<源图> --out=../03_split --clean --title-mode=auto")
        print(f"  python verify_split.py ../03_split ../01_source/<源图> --out=../04_verify")


if __name__ == "__main__":
    main()
