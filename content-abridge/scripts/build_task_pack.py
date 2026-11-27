r"""build_task_pack.py -- 构造缩编任务包（只组装，不做任何删除/改写）。

用法:
    python build_task_pack.py <converted_dir> <runs_dir> <skill_dir> <task_id>

    <converted_dir>  convert.py / convert_paddle.py 的输出目录（source.md / assets/ / assets-manifest.json）
    <runs_dir>       runs 根目录，任务包建在 <runs_dir>/<task_id>/
    <skill_dir>      skill 包目录（复制 references/retention-policy.md 快照进任务包 policy/）
    <task_id>        任务包目录名，如 20261002-alignment-faking

组装原则（DESIGN.md §10.3-B，2026-10-05 重做）:
    input/source.md **原样复制全文，不剥离**。长文档的删除统一走
    trimmer 裁决（trim-decision.yaml）+ execute_trim 执行（纯整行删除 + manifest 留痕
    + source-full.md 回退）。v1 的"打包时正则直剥 References/附录"已废弃：它折叠空行、
    绕过四道门，与行集合核验的审计要求冲突。

产出结构:
    <runs_dir>/<task_id>/
    ├── meta.json           # type=uncertain 待 classify.py 或人工填；mode=short/long；
    │                       #   长文档判定 = len(全文) >= 30,000（阈值与 execute_trim 同口径）
    ├── state.json          # 编排状态：short -> stage=ready（直接入起草）；
    │                       #   long  -> stage=trim-delete-pending（先 classify ->
    │                       #   build_outline -> trimmer 裁决 -> execute_trim）
    ├── input/              # source.md（全文）、assets/、assets-manifest.json、
    │                       #   refs-candidates.json（永远从全文抽，References 段的 URL
    │                       #   是铁律 10 的主要来源，必须先抽好再谈删）
    ├── policy/             # retention-policy.md 快照（审计追溯）
    └── draft/  reviews/  revised/   # 空骨架

    长文档后续由 build_outline.py / execute_trim.py 增补 input/outline.json、
    trim-decision.yaml、trim-manifest.json、source-full.md（见 scripts/README.md 长文档分支）。
"""
import json
import re
import shutil
import sys
from pathlib import Path

from extract_refs import collect_refs
from thresholds import THRESHOLD_CHARS

REQUIRED = ("source.md", "assets-manifest.json")


def main():
    if len(sys.argv) != 5:
        sys.exit(__doc__)
    converted, runs, skill = map(Path, sys.argv[1:4])
    task_id = sys.argv[4]
    for name in REQUIRED:
        if not (converted / name).is_file():
            sys.exit(f"{converted} 缺少 {name}（先跑 convert.py / convert_paddle.py）")
    policy_src = skill / "references" / "retention-policy.md"
    if not policy_src.is_file():
        sys.exit(f"{policy_src} 不存在")

    pack = runs / task_id
    if pack.exists():
        sys.exit(f"任务包已存在: {pack}")

    policy_text = policy_src.read_text(encoding="utf-8")
    m = re.search(r"版本：(\S+)", policy_text)
    policy_version = m.group(1) if m else "unknown"

    (pack / "input").mkdir(parents=True)
    (pack / "policy").mkdir()
    for name in ("draft", "reviews", "revised"):
        (pack / name).mkdir()

    shutil.copy2(converted / "source.md", pack / "input" / "source.md")
    shutil.copytree(converted / "assets", pack / "input" / "assets")
    shutil.copy2(converted / "assets-manifest.json", pack / "input" / "assets-manifest.json")
    shutil.copy2(policy_src, pack / "policy" / "retention-policy.md")

    full_text = (pack / "input" / "source.md").read_text(encoding="utf-8")

    # 引用候选永远从全文抽（References 段可能随后被 trim 删除）
    refs = collect_refs(full_text)
    (pack / "input" / "refs-candidates.json").write_text(
        json.dumps(refs, ensure_ascii=False, indent=2), encoding="utf-8")

    mode = "long" if len(full_text) >= THRESHOLD_CHARS else "short"
    stage = "trim-delete-pending" if mode == "long" else "ready"

    meta = {
        "task_id": task_id,
        "type": "uncertain",
        "policy_version": policy_version,
        "converted_from": str(converted.resolve()),
        "mode": mode,
        "source_chars_full": len(full_text),
    }
    (pack / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2),
                                    encoding="utf-8")
    (pack / "state.json").write_text(json.dumps({
        "stage": stage, "round": 0,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"done: {pack}（候选引用 {len(refs)} 条，mode={mode}，stage={stage}，"
          f"全文 {len(full_text)} 字符原样入包）")


if __name__ == "__main__":
    main()
