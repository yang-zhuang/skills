"""validate_review.py -- 评估意见 YAML 的 schema 校验，不合格非零退出（程序打回依据）。

用法:
    python validate_review.py <review.yaml>

校验规则（与 references/evaluator.md 输出契约一致）:
- 顶层为 list，或含 items / opinions 键的 dict（别名等价）；每条意见为 dict
- 必填字段: id, category, dimension, location, problem, instruction
- id 格式 E-<数字>，轮内唯一
- category ∈ {must-fix, defer, no-fix}
- dimension ∈ {fidelity, completeness, self-containment, format, citation, policy-gap}
- evidence 可选（no-fix 允许空）
"""
import re
import sys
from pathlib import Path

import yaml

CATEGORIES = {"must-fix", "defer", "no-fix"}
DIMENSIONS = {"fidelity", "completeness", "self-containment", "format", "citation", "policy-gap"}
REQUIRED = ("id", "category", "dimension", "location", "problem", "instruction")


def main():
    if len(sys.argv) != 2:
        sys.exit("用法: python validate_review.py <review.yaml>")
    path = Path(sys.argv[1])
    if not path.is_file():
        sys.exit(f"文件不存在: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        sys.exit(f"YAML 解析失败: {e}")
    items = data.get("items") if isinstance(data, dict) else data
    if isinstance(data, dict) and "items" not in data and "opinions" in data:
        items = data["opinions"]
    if not isinstance(items, list):
        sys.exit("顶层必须是意见列表（或含 items 键的 dict）")
    if not items:
        print("ok: 0 条意见（本轮无意见，确认轮通过）")
        return

    errors, seen_ids = [], set()
    for i, item in enumerate(items, 1):
        tag = f"第 {i} 条"
        if not isinstance(item, dict):
            errors.append(f"{tag}: 不是 dict")
            continue
        for field in REQUIRED:
            v = item.get(field)
            if v is None or (isinstance(v, str) and not v.strip()):
                errors.append(f"{tag}: 缺必填字段 {field}")
        rid = str(item.get("id", ""))
        if not re.fullmatch(r"E-\d+", rid):
            errors.append(f"{tag}: id 格式错误（应为 E-数字）: {rid!r}")
        elif rid in seen_ids:
            errors.append(f"{tag}: id 重复: {rid}")
        else:
            seen_ids.add(rid)
        if item.get("category") not in CATEGORIES:
            errors.append(f"{tag}: category 非法: {item.get('category')!r}")
        if item.get("dimension") not in DIMENSIONS:
            errors.append(f"{tag}: dimension 非法: {item.get('dimension')!r}")

    if errors:
        print("VALIDATION FAILED:")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    print(f"ok: {len(items)} 条意见全部通过校验")


if __name__ == "__main__":
    main()
