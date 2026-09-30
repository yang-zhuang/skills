#!/usr/bin/env python
# -*- coding: utf-8 -*-
'''
collect_extracts.py — 闭环第 2 步（收执行产出）：把执行 agent 落盘的每篇 markdown 聚合成 extracts.jsonl。

做三件事：
  1. 逐个读 runs/batch_XXX/extracts/<pid>.md，用 screen_papers.extract_json 取出 json 块，
     写成 runs/batch_XXX/extracts.jsonl（每行 {paper_id, raw, parsed}）
  2. 为每篇写 runs/batch_XXX/extracts/<pid>.fields.json —— 纯 6 字段 JSON、无前置分析，
     是评审 agent 的唯一抽取侧输入（评审不许读 <pid>.md：前置分析是执行端的推理，
     评审看了会被执行端判断锚定，失去独立性）
  3. 报告质量：解析失败（无 json 块 / JSON 不合法）、缺键、缺篇
     —— 解析失败率本身就是闭环的一个指标，写进 metrics.json 的候选

用法：
  python collect_extracts.py --batch batch_002
  python collect_extracts.py --batch batch_002 --quiet   # 只打汇总行
'''

import os
import sys
import json
import glob
import argparse

from screen_papers import extract_json

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RUNS_DIR = os.path.join(BASE_DIR, 'runs')
REQUIRED = ['research_area', 'research_question', 'method_summary',
            'training_paradigm', 'base_model', 'data_scale']


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--batch', required=True, help='批次目录名，如 batch_002')
    ap.add_argument('--quiet', action='store_true')
    args = ap.parse_args()

    bdir = os.path.join(RUNS_DIR, args.batch)
    edir = os.path.join(bdir, 'extracts')
    if not os.path.isdir(edir):
        print(f'没有 {edir}，先跑执行环节。')
        return

    manifest_path = os.path.join(bdir, 'manifest.json')
    expect = []
    if os.path.isfile(manifest_path):
        expect = json.load(open(manifest_path, encoding='utf-8')).get('paper_ids', [])

    rows, bad, missing_keys = [], [], []
    for path in sorted(glob.glob(os.path.join(edir, '*.md'))):
        pid = os.path.splitext(os.path.basename(path))[0]
        raw = open(path, encoding='utf-8').read()
        try:
            parsed = extract_json(raw)
        except Exception as e:
            bad.append((pid, str(e)))
            parsed = {}
        miss = [k for k in REQUIRED if k not in parsed]
        if miss:
            missing_keys.append((pid, miss))
        rows.append({'paper_id': pid, 'raw': raw, 'parsed': parsed})
        # 评审专用：只含 6 字段 JSON，物理隔离前置分析（即使解析失败也写出 {}，评审可判断空转）
        fields_path = os.path.join(edir, pid + '.fields.json')
        with open(fields_path, 'w', encoding='utf-8') as f:
            json.dump({'paper_id': pid, 'fields': parsed}, f, ensure_ascii=False, indent=2)

    out = os.path.join(bdir, 'extracts.jsonl')
    with open(out, 'w', encoding='utf-8') as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + '\n')

    done = {r['paper_id'] for r in rows}
    lack = [p for p in expect if p not in done]

    print(f'聚合 {len(rows)} 篇 -> {out}')
    print(f'解析失败 {len(bad)} 篇 | 缺键 {len(missing_keys)} 篇 | manifest 要求 {len(expect)} 篇、缺 {len(lack)} 篇')
    if not args.quiet:
        for pid, e in bad:
            print(f'  [解析失败] {pid}: {e}')
        for pid, m in missing_keys:
            print(f'  [缺键] {pid}: {m}')
        for pid in lack:
            print(f'  [缺篇] {pid}')


if __name__ == '__main__':
    main()
