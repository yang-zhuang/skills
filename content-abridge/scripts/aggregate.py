"""aggregate.py -- 跨任务聚合 defer 意见频次（编排器演化策略的依据）。

用法:
    python aggregate.py <runs_dir>

扫描 <runs_dir>/<task_id>/reviews/*.yaml 中 category=defer 的意见，按
(dimension, problem 归一化) 分组计数，打印报告并写 <runs_dir>/_defer-aggregate.json。
归一化规则: problem 取前 50 字符、去空白差异——足矣，不做 NLP。
"""
import json
import sys
from collections import Counter
from pathlib import Path

import yaml


def main():
    if len(sys.argv) != 2:
        sys.exit("用法: python aggregate.py <runs_dir>")
    runs = Path(sys.argv[1])
    if not runs.is_dir():
        sys.exit(f"目录不存在: {runs}")

    counter = Counter()
    for review in sorted(runs.glob("*/reviews/*.yaml")):
        try:
            data = yaml.safe_load(review.read_text(encoding="utf-8"))
        except yaml.YAMLError as e:
            print(f"[warn] 跳过解析失败的 {review}: {e}", file=sys.stderr)
            continue
        items = data.get("items") if isinstance(data, dict) else data
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict) and item.get("category") == "defer":
                key = (item.get("dimension", "?"),
                       " ".join(str(item.get("problem", ""))[:50].split()))
                counter[key] += 1

    rows = [{"dimension": d, "problem_head": p, "count": c}
            for (d, p), c in counter.most_common()]
    out = runs / "_defer-aggregate.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    if not rows:
        print("no defer opinions found")
        return
    print(f"{'count':>5}  dimension       problem（前 50 字）")
    for r in rows:
        print(f"{r['count']:>5}  {r['dimension']:<15} {r['problem_head']}")
    print(f"\nreport -> {out}  （同一组合 count >= 3 时建议人工 review 是否升为方针规则）")


if __name__ == "__main__":
    main()
