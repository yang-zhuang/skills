#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据集体检：识别格式、统计 token 长度分布、给出「要不要截断 / packing」的判据。

支持 jsonl / json / csv / parquet，自动识别常见 SFT 格式：
  alpaca            instruction / input / output
  sharegpt          conversations[{from,value}]
  openai-messages   messages[{role,content}]
  prompt-completion prompt / completion（= response / answer / output 同义）
  query-response    query / response（含 question / answer）
  text              纯文本

token 统计优先用真实 tokenizer（需要 transformers，通常在训练环境里）；
没有就退化成字符数估算（中文约 1.7 字/token，英文约 4 字符/token）。

用法：
  python scripts/check_dataset.py data/train.jsonl --tokenizer Qwen/Qwen2.5-7B-Instruct --seq-len 4096
  python scripts/check_dataset.py data/train.jsonl --chars-per-token 1.8 --seq-len 4096
  python scripts/check_dataset.py data/train.jsonl --json
"""

import argparse
import csv
import json
import statistics
import sys
from pathlib import Path


def load_rows(path: Path) -> list[dict]:
    suffix = path.suffix.lower()
    if suffix == ".jsonl":
        rows = []
        with path.open(encoding="utf-8") as f:
            for i, line in enumerate(f, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError as e:
                    sys.exit(f"FATAL: 第 {i} 行不是合法 JSON：{e}")
        return rows
    if suffix == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return data
        for key in ("data", "rows", "instances", "samples"):
            if isinstance(data.get(key), list):
                return data[key]
        sys.exit("FATAL: JSON 顶层既不是数组，也找不到 data/rows/instances/samples")
    if suffix == ".csv":
        with path.open(encoding="utf-8", newline="") as f:
            return list(csv.DictReader(f))
    if suffix == ".parquet":
        try:
            import pandas as pd  # noqa: PLC0415
        except ImportError:
            sys.exit("FATAL: 读 parquet 需要 pandas + pyarrow")
        return pd.read_parquet(path).to_dict("records")
    sys.exit(f"FATAL: 不支持的扩展名 {suffix}（支持 jsonl/json/csv/parquet）")


def detect_format(row: dict) -> str:
    keys = set(row)
    if {"instruction", "output"} <= keys:
        return "alpaca"
    if "conversations" in keys:
        return "sharegpt"
    if "messages" in keys:
        return "openai-messages"
    # prompt 的常见搭档：completion / response / answer / output
    if "prompt" in keys and ({"completion", "response", "answer", "output"} & keys):
        return "prompt-completion"
    if "query" in keys and ({"response", "answer"} & keys):
        return "query-response"
    if "text" in keys:
        return "text"
    return "unknown"


def render(row: dict, fmt: str) -> str:
    if fmt == "alpaca":
        return "\n".join(x for x in (row.get("instruction"), row.get("input"),
                                     row.get("output")) if x)
    if fmt == "sharegpt":
        return "\n".join(str(m.get("value", "")) for m in row.get("conversations") or [])
    if fmt == "openai-messages":
        return "\n".join(str(m.get("content", "")) for m in row.get("messages") or [])
    if fmt == "prompt-completion":
        tail = next((row[k] for k in ("completion", "response", "answer", "output") if row.get(k)), "")
        return f"{row.get('prompt') or ''}{tail}"
    if fmt == "query-response":
        return f"{row.get('query') or ''}{row.get('response') or row.get('answer') or ''}"
    if fmt == "text":
        return str(row.get("text") or "")
    return json.dumps(row, ensure_ascii=False)


def get_tokenizer(name: str | None):
    if not name:
        return None
    try:
        from transformers import AutoTokenizer  # noqa: PLC0415
        return AutoTokenizer.from_pretrained(name, trust_remote_code=True)
    except Exception as e:  # noqa: BLE001
        print(f"WARN: 加载 tokenizer 失败（{type(e).__name__}: {str(e)[:100]}），改用字符估算")
        return None


def pct(values: list[int], q: float) -> int:
    if not values:
        return 0
    k = max(0, min(len(values) - 1, int(round(q * (len(values) - 1)))))
    return sorted(values)[k]


def main() -> None:
    parser = argparse.ArgumentParser(description="数据集体检")
    parser.add_argument("path", help="数据文件：jsonl / json / csv / parquet")
    parser.add_argument("--tokenizer", default=None, help="tokenizer 路径或 HF id")
    parser.add_argument("--chars-per-token", type=float, default=1.8,
                        help="没有 tokenizer 时的估算系数（中文约 1.7~2.0）")
    parser.add_argument("--seq-len", type=int, default=4096, help="计划用的最大序列长度")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    path = Path(args.path)
    if not path.is_file():
        sys.exit(f"FATAL: 文件不存在 {path}")

    rows = load_rows(path)
    if not rows:
        sys.exit("FATAL: 数据为空")
    fmt = detect_format(rows[0])
    if fmt == "unknown":
        print(f"WARN: 认不出格式，按原文 JSON 长度统计。字段：{list(rows[0])[:10]}")

    tok = get_tokenizer(args.tokenizer)
    lengths: list[int] = []
    skipped = 0
    for r in rows:
        text = render(r, fmt)
        if not text.strip():
            skipped += 1
            continue
        if tok is not None:
            lengths.append(len(tok.encode(text, add_special_tokens=False)))
        else:
            lengths.append(max(1, int(len(text) / args.chars_per_token)))

    total_tokens = sum(lengths)
    over = sum(1 for x in lengths if x > args.seq_len)
    stats = {
        "file": str(path),
        "format": fmt,
        "rows": len(rows),
        "empty_rows": skipped,
        "tokenizer": args.tokenizer or f"字符估算（÷{args.chars_per_token}）",
        "seq_len": args.seq_len,
        "tokens": {
            "total": total_tokens,
            "min": min(lengths) if lengths else 0,
            "p50": pct(lengths, 0.50),
            "p90": pct(lengths, 0.90),
            "p99": pct(lengths, 0.99),
            "max": max(lengths) if lengths else 0,
            "mean": round(statistics.mean(lengths), 1) if lengths else 0,
        },
        "over_seq_len": over,
        "over_ratio": round(over / len(lengths), 4) if lengths else 0,
    }

    if args.json:
        print(json.dumps(stats, ensure_ascii=False, indent=2))
        return

    t = stats["tokens"]
    L = [f"# 数据集体检：{path.name}", ""]
    L.append("| 项 | 值 |")
    L.append("|---|---|")
    L.append(f"| 格式 | `{fmt}` |")
    L.append(f"| 样本数 | {stats['rows']}（空样本 {skipped}） |")
    L.append(f"| 计数方式 | {stats['tokenizer']} |")
    L.append(f"| token 总量 | {total_tokens:,} |")
    L.append(f"| 长度 min / p50 / p90 / p99 / max | "
             f"{t['min']} / {t['p50']} / {t['p90']} / {t['p99']} / {t['max']} |")
    L.append(f"| 超过 seq_len={args.seq_len} | {over} 条（{stats['over_ratio'] * 100:.1f}%） |")
    L.append("")

    L.append("## 判据（按这个决定截断还是 packing）")
    if over == 0:
        L.append(f"- 全部样本 ≤ {args.seq_len}，**不需要截断**；可考虑 packing 提升吞吐。")
    elif stats["over_ratio"] <= 0.01:
        L.append(f"- 仅 {stats['over_ratio'] * 100:.2f}% 超长，**直接截断**损失最小。")
    elif stats["over_ratio"] <= 0.10:
        L.append(f"- {stats['over_ratio'] * 100:.1f}% 超长：先看这些样本是不是脏数据；"
                 f"确认有效就提高 seq_len 或做分段。")
    else:
        L.append(f"- **{stats['over_ratio'] * 100:.1f}% 超长**，截断会丢大量内容："
                 f"要么提高 seq_len，要么重做数据切分。")

    if t["p50"] and t["max"] > t["p50"] * 10:
        L.append("- 长度分布极不均（max > p50×10）：短样本 padding 会浪费显存，"
                 "建议按长度分桶或开 packing。")
    if stats["rows"] < 500:
        L.append("- 样本数 < 500：SFT 很容易过拟合，先确认这不是「玩具集」。")
    L.append("")
    L.append("> 下一步：把这份报告交给 references/03_data.md 决定格式映射与 packing 策略。")
    print("\n".join(L))


if __name__ == "__main__":
    main()
