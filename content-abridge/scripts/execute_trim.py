r"""execute_trim.py -- 校验 trimmer 裁决并执行整节删除（长文档管线，DESIGN.md §10.3-B）。

用法:
    python execute_trim.py <task_pack_dir>                     # 一轮：删（trim-decision.yaml）
    python execute_trim.py <task_pack_dir> --validate-directives   # 二轮：校验简化指令（只校验不执行）

    任务包需已备好（前置顺序: convert -> classify -> build_outline -> extract_refs -> trim）:
        input/source.md           全文（尚未剥离）
        input/outline.json        build_outline.py 对全文跑出的目录
        input/trim-decision.yaml  trimmer 一轮裁决（schema 见 references/trimmer.md）

执行流程:
    1. 校验裁决——任一不合格则列出全部错误、非零退出、不改动任何文件（整份打回，
       不执行合法子集；validate_review.py 同一先例）:
       - YAML 单文档、无 --- 行、顶层为列表
       - id 为 L<行号> 且在 outline.json 中真实存在；同节不得重复
       - 裁决区间不得重叠（父子节同时列出属意图不明）
       - priority 为 >=1 的整数；reason 非空
       - Limitations / Ethical Considerations 等作者论述段不可删（硬约束，宁留勿删）
       - outline.json 与 source.md 逐节比对（目录陈旧即打回，须重跑 build_outline.py）
    2. 按 priority 升序执行:
       - figs>0 的节一律 status=held 不执行（图归属有误判风险，宁漏勿错，编排器复查）
       - priority 1（方针判删必删）恒执行；priority 2+ 删到剩余字符 <= 30,000 即停，
         未执行到的裁决记 skipped-budget
    3. 产出（剥离 = 纯整行删除，不改写、不折叠空行）:
       - input/source-full.md      全文永久留档（审计回退用）
       - input/source.md           剥离版（全文的纯行子序列）
       - input/trim-manifest.json  每段 {id,title,priority,reason,status,chars,...} + 统计
    4. 自检: 删除行集合 == manifest 宣称区间；剥离版 == 全文按行删减结果；
       全文留档与执行前 source.md 字节一致（sha256）。

    剥离后编排器对剥离版重跑 build_outline.py 供 trimmer 二轮看（§10.3-B）。

二轮模式（--validate-directives）:
    trimmer 二轮落盘 input/simplify-directives.json 后，编排器先跑本模式校验，再进 ready。
    机械可查的：id 在当前 outline.json 真实存在、不重复、区间不重叠、target_ratio 在 0-1、
    instruction 非空、Limitations/Ethical Considerations 硬约束禁指。
    "目标节是否属简化保留档"属判断，脚本无法机械判定——由 trimmer 自查 + 评审核对（§10.3-B）。

字符量口径: 阈值判定与 manifest 的 full_chars/remaining_chars 用 len(全文文本)；
outline.json 的 chars 是节子树去空白行字符量（trimmer 预算视角），两者口径不同、各有用途。
"""
import json
import re
import shutil
import sys
from pathlib import Path

import yaml

from build_outline import build_outline
from thresholds import THRESHOLD_CHARS

ID_RE = re.compile(r"^L\d+$")
SEP_RE = re.compile(r"(?m)^---\s*$")
# 作者论述段硬约束：任何 priority 不得指向（§10.3-B 不可删硬约束）
NO_TRIM_TITLE = re.compile(r"(?i)^(limitations|ethical considerations|ethics)\b")


def parse_decision(path: Path):
    """读裁决文件，返回 (列表或 None, 错误列表)。"""
    raw = path.read_text(encoding="utf-8")
    errors = []
    if SEP_RE.search(raw):
        errors.append("出现 --- 行（契约要求单文档、禁用 ---）")
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as e:
        return None, errors + [f"YAML 解析失败: {e}"]
    if not isinstance(data, list):
        return None, errors + ["顶层必须是裁决列表"]
    return data, errors


def validate(decisions, secs, fresh):
    """返回 (错误列表, {id: 裁决条目})。secs = outline 的 id->节；fresh = 从 source.md
    现算的目录（比对 outline.json 是否陈旧）。"""
    errors, seen = [], {}
    for i, d in enumerate(decisions, 1):
        tag = f"第 {i} 条"
        if not isinstance(d, dict):
            errors.append(f"{tag}: 不是 dict")
            continue
        rid = str(d.get("id", ""))
        if not ID_RE.fullmatch(rid):
            errors.append(f"{tag}: id 格式错误（应为 L<行号>）: {rid!r}")
        elif rid not in secs:
            errors.append(f"{tag}: id 在 outline.json 中不存在: {rid}")
        elif rid in seen:
            errors.append(f"{tag}: id 重复: {rid}")
        else:
            seen[rid] = d
        p = d.get("priority")
        if isinstance(p, bool) or not isinstance(p, int) or p < 1:
            errors.append(f"{tag}: priority 必须是 >=1 的整数: {p!r}")
        reason = d.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            errors.append(f"{tag}: reason 缺失或为空")

    ranges = sorted((secs[r]["start_line"], secs[r]["end_line"], r) for r in seen)
    for (a1, b1, r1), (a2, b2, r2) in zip(ranges, ranges[1:]):
        if a2 < b1:
            errors.append(f"裁决区间重叠: {r1} [{a1}-{b1}] 与 {r2} [{a2}-{b2}]"
                          f"（父子节同列属意图不明）")
    for rid in seen:
        if NO_TRIM_TITLE.match(secs[rid]["title"]):
            errors.append(f"{rid} ({secs[rid]['title']}): 作者论述段不可删"
                          f"（硬约束，policy-gap 候选，宁留勿删）")

    key = lambda ss: {(s["id"], s["start_line"], s["end_line"], s["title"]) for s in ss}
    if key(secs.values()) != key(fresh["sections"]):
        errors.append("outline.json 与 source.md 不一致（目录陈旧），"
                      "须对当前 source.md 重跑 build_outline.py")
    return errors, seen


def execute(seen, secs, lines):
    """按 priority 升序删除。返回 (manifest 条目列表, 已删 0 基行集合, 剩余字符量)。"""
    remaining = len("\n".join(lines))
    met = remaining <= THRESHOLD_CHARS
    status = {}
    # 执行顺序：figs>0 一律 held；p1 恒执行；p2+ 达标即停（先算好每个 id 的归宿）
    order = sorted(seen, key=lambda r: (seen[r]["priority"], secs[r]["start_line"]))
    deleted = set()
    for rid in order:
        s = secs[rid]
        if s["figs"] > 0:
            status[rid] = "held"
            continue
        if seen[rid]["priority"] == 1 or not met:
            deleted |= set(range(s["start_line"] - 1, s["end_line"]))
            remaining = len("\n".join(l for i, l in enumerate(lines) if i not in deleted))
            met = remaining <= THRESHOLD_CHARS
            status[rid] = "deleted"
        else:
            status[rid] = "skipped-budget"

    entries = []       # manifest 按裁决文件原顺序留痕
    for rid, d in seen.items():
        s = secs[rid]
        entries.append({"id": rid, "title": s["title"], "priority": d["priority"],
                        "reason": d["reason"], "status": status[rid],
                        "figs": s["figs"], "chars": s["chars"],
                        "start_line": s["start_line"], "end_line": s["end_line"]})
    return entries, deleted, remaining


def validate_directives(inp: Path, secs):
    """二轮指令校验（只校验不执行）。返回错误列表。"""
    path = inp / "simplify-directives.json"
    if not path.is_file():
        return [f"缺少 {path}"]
    raw = path.read_text(encoding="utf-8")
    if SEP_RE.search(raw):
        return ["出现 --- 行（契约要求单文档、禁用 ---）"]
    try:
        data = yaml.safe_load(raw)     # JSON 与 YAML 同解析器，两者都收
    except yaml.YAMLError as e:
        return [f"解析失败: {e}"]
    if not isinstance(data, list):
        return ["顶层必须是指令列表"]
    errors, seen = [], {}
    for i, d in enumerate(data, 1):
        tag = f"第 {i} 条"
        if not isinstance(d, dict):
            errors.append(f"{tag}: 不是 dict")
            continue
        rid = str(d.get("id", ""))
        if not ID_RE.fullmatch(rid):
            errors.append(f"{tag}: id 格式错误（应为 L<行号>）: {rid!r}")
        elif rid not in secs:
            errors.append(f"{tag}: id 在当前 outline.json 中不存在: {rid}")
        elif rid in seen:
            errors.append(f"{tag}: id 重复: {rid}")
        else:
            seen[rid] = d
        r = d.get("target_ratio")
        if isinstance(r, bool) or not isinstance(r, (int, float)) or not 0 <= r <= 1:
            errors.append(f"{tag}: target_ratio 必须在 0-1: {r!r}")
        ins = d.get("instruction")
        if not isinstance(ins, str) or not ins.strip():
            errors.append(f"{tag}: instruction 缺失或为空")
    ranges = sorted((secs[r]["start_line"], secs[r]["end_line"], r) for r in seen)
    for (a1, b1, r1), (a2, b2, r2) in zip(ranges, ranges[1:]):
        if a2 < b1:
            errors.append(f"指令区间重叠: {r1} [{a1}-{b1}] 与 {r2} [{a2}-{b2}]")
    for rid in seen:
        if NO_TRIM_TITLE.match(secs[rid]["title"]):
            errors.append(f"{rid} ({secs[rid]['title']}): 作者论述段不可简化（硬约束）")
    return errors


def main():
    argv = sys.argv[1:]
    validate_only = "--validate-directives" in argv
    argv = [a for a in argv if a != "--validate-directives"]
    if len(argv) != 1:
        sys.exit(__doc__)
    pack = Path(argv[0])
    inp = pack / "input"
    src = inp / "source.md"
    if validate_only:
        # 二轮模式：对 simplify-directives.json 做机械校验（outline 必须是剥离版现算口径）
        outline_path = inp / "outline.json"
        if not outline_path.is_file():
            sys.exit(f"缺少 {outline_path}")
        outline = json.loads(outline_path.read_text(encoding="utf-8"))
        secs = {s["id"]: s for s in outline["sections"]}
        fresh = build_outline(src)
        if {(s["id"], s["start_line"], s["end_line"], s["title"]) for s in secs.values()} != \
           {(s["id"], s["start_line"], s["end_line"], s["title"]) for s in fresh["sections"]}:
            sys.exit("outline.json 与 source.md 不一致（目录陈旧），"
                     "须对当前 source.md 重跑 build_outline.py")
        errors = validate_directives(inp, secs)
        if errors:
            print("VALIDATION FAILED（整份打回）:")
            for e in errors:
                print(f"  - {e}")
            sys.exit(1)
        n = len(yaml.safe_load((inp / "simplify-directives.json").read_text(encoding="utf-8")))
        print(f"ok: simplify-directives {n} 条全部通过校验（id 存在/无重复/无重叠/ratio 0-1/"
              f"instruction 非空/硬约束）-> 进 ready")
        return
    for p in (src, inp / "outline.json", inp / "trim-decision.yaml"):
        if not p.is_file():
            sys.exit(f"缺少 {p}（前置: build_task_pack -> build_outline -> trimmer 裁决）")

    original_bytes = src.read_bytes()
    lines = src.read_text(encoding="utf-8").split("\n")
    outline = json.loads((inp / "outline.json").read_text(encoding="utf-8"))
    secs = {s["id"]: s for s in outline["sections"]}

    decisions, errors = parse_decision(inp / "trim-decision.yaml")
    if decisions is not None:
        errors_, seen = validate(decisions, secs, build_outline(src))
        errors += errors_
    else:
        seen = {}
    if errors:
        print("VALIDATION FAILED（整份打回，未改动任何文件）:")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)

    entries, deleted, remaining = execute(seen, secs, lines)

    # 产出：全文留档 -> 剥离版 -> manifest
    full = inp / "source-full.md"
    shutil.copy2(src, full)
    stripped = "\n".join(l for i, l in enumerate(lines) if i not in deleted)
    src.write_text(stripped, encoding="utf-8", newline="")
    n = {"deleted": 0, "held": 0, "skipped-budget": 0}
    for e in entries:
        n[e["status"]] += 1
    manifest = {"threshold_chars": THRESHOLD_CHARS,
                "full_chars": len("\n".join(lines)),
                "remaining_chars": remaining,
                "budget_met": remaining <= THRESHOLD_CHARS,
                "deleted_chars": len("\n".join(lines)) - remaining,
                "counts": n,
                "decisions": entries}
    (inp / "trim-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    # 自检：留档字节一致 / 删除行集合 == manifest 宣称区间 / 剥离版 == 按行删减结果
    assert full.read_bytes() == original_bytes, "source-full.md 与执行前 source.md 字节不一致"
    claimed = set()
    for e in entries:
        if e["status"] == "deleted":
            claimed |= set(range(e["start_line"] - 1, e["end_line"]))
    assert deleted == claimed, "实际删除行集合 != manifest 宣称区间"
    assert src.read_text(encoding="utf-8") == stripped, "落盘的剥离版与计算结果不一致"

    print(f"done: 裁决 {len(entries)} 条（deleted {n['deleted']} / held {n['held']} / "
          f"skipped-budget {n['skipped-budget']}），"
          f"删除 {manifest['deleted_chars']} 字符，剩余 {remaining} "
          f"（阈值 {THRESHOLD_CHARS}，{'达标 -> ready' if manifest['budget_met'] else '仍超 -> trim-simplify-pending'}）")
    print(f"产出: {full} / {src} / {inp / 'trim-manifest.json'}")


if __name__ == "__main__":
    main()
