#!/usr/bin/env python
# -*- coding: utf-8 -*-
'''
prepare_batch.py — 闭环第 1 步（选批次）：为一个新批次生成输入与清单。

纯确定性逻辑，不调用 LLM。做三件事：
  1. 从已完成 OCR 的论文里按 seed 随机抽 n 篇，**排除历史批次已用过的 paper_id**
     （防过拟合硬闸：禁止用提出建议的那批数据验证修改效果）
  2. 组装每篇的输入（标题 / 摘要 / 前 2 页正文 / 章节标题），复用 screen_papers.py 的组装函数
  3. 写 runs/batch_XXX/{manifest.json, inputs.jsonl}
     manifest 同时记录 rubric 与执行文档（references/EXTRACT.md）的 sha1
     —— 只写版本号不够，版本号是手写的，会记错
     （batch_001 的 manifest 标 prompt_version=v1，实际跑的不是 v1，已无法追溯）

用法：
  python prepare_batch.py --seed 1002 --n 6              # 建 runs/batch_002
  python prepare_batch.py --seed 1002 --n 6 --next 5     # 建 runs/batch_005
  python prepare_batch.py --seed 1002 --n 6 --dry-run    # 只打印将选中的 paper_id，不落盘
  python prepare_batch.py --seed 1002 --n 6 --no-exclude # 忽略历史批次（仅供对照实验显式使用）
  python prepare_batch.py --papers a,b,c                 # 定向回归：只跑指定论文，跳过随机抽样与历史排除
                                                         # （改 rubric 后验证「被判过的篇还判不判」用这个，
                                                         #   随机抽样抽不到目标篇，--no-exclude 也不行）
  python prepare_batch.py --seed 1002 --strata 2         # 分层抽样：每个内容类型抽 2 篇（广度覆盖）
  python prepare_batch.py --seed 1002 --strata 3 --types survey,theory,nonstandard
                                                         # 只在指定类型里分层（给低频型加量）

为什么要有 --strata：纯随机抽样在 1500+ 篇的池上会按占比自然流出，低频类型几乎抽不到
（综述只占 1.3%，连跑 20 批随机抽样的期望命中不到 3 篇）。rubric 在这些类型上的行为
就永远没被测过 —— 例如「综述类论文该填 `不训练` 还是 `[]`」从未被实证。
分层抽样按类型给固定配额，保证每批都覆盖到低频型。
分桶依据是 classify_papers.py 产出的 runs/paper_types.jsonl（确定性规则，不调用 LLM）。
'''

import os
import sys
import json
import time
import random
import hashlib
import argparse

from screen_papers import (
    find_completed, load_metadata, load_page_text, load_all_headings, assemble_input,
    REF_DIR,
)

# 分层抽样的类型顺序（与 classify_papers.ORDER 一致：命中即停的优先级链）
try:
    from classify_papers import ORDER as TYPE_ORDER
except Exception:
    TYPE_ORDER = ['nonstandard', 'survey', 'resource', 'system',
                  'theory', 'analysis', 'method', 'empirical']

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RUNS_DIR = os.path.join(BASE_DIR, 'runs')
RUBRIC_PATH = os.path.join(REF_DIR, 'RUBRIC.md')
EXTRACT_PATH = os.path.join(REF_DIR, 'EXTRACT.md')


def sha1_of(path):
    with open(path, 'rb') as f:
        return hashlib.sha1(f.read()).hexdigest()[:12]


def used_paper_ids():
    '''扫 runs/batch_*/，返回历史批次已用过的 paper_id 集合。
    manifest.json 优先读 paper_ids 字段；缺失时退回 inputs.jsonl 里提取。'''
    used = set()
    if not os.path.isdir(RUNS_DIR):
        return used
    for name in sorted(os.listdir(RUNS_DIR)):
        bdir = os.path.join(RUNS_DIR, name)
        if not (name.startswith('batch_') and os.path.isdir(bdir)):
            continue
        mf = os.path.join(bdir, 'manifest.json')
        if os.path.isfile(mf):
            try:
                d = json.load(open(mf, encoding='utf-8'))
                for pid in d.get('paper_ids', []):
                    used.add(pid)
                if d.get('paper_ids'):
                    continue
            except Exception:
                pass
        inp = os.path.join(bdir, 'inputs.jsonl')
        if os.path.isfile(inp):
            for ln in open(inp, encoding='utf-8'):
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    used.add(json.loads(ln)['paper_id'])
                except Exception:
                    pass
    return used


def next_batch_no():
    n = 0
    if os.path.isdir(RUNS_DIR):
        for name in os.listdir(RUNS_DIR):
            if name.startswith('batch_') and os.path.isdir(os.path.join(RUNS_DIR, name)):
                try:
                    n = max(n, int(name.split('_')[1]))
                except Exception:
                    pass
    return n + 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--seed', type=int, required=True, help='随机种子，每批必须换')
    ap.add_argument('--n', type=int, default=6, help='抽样篇数')
    ap.add_argument('--next', type=int, default=0, help='指定批次号，默认取现有最大号+1')
    ap.add_argument('--dry-run', action='store_true', help='只打印将选中的 paper_id，不落盘')
    ap.add_argument('--no-exclude', action='store_true', help='不排除历史批次（仅对照实验显式使用）')
    ap.add_argument('--papers', default='', help='逗号分隔的 paper_id 清单：定向回归，跳过随机抽样与历史排除')
    ap.add_argument('--strata', type=int, default=0,
                    help='分层抽样：每个内容类型抽 N 篇（类型来自 runs/paper_types.jsonl）')
    ap.add_argument('--types', default='',
                    help='配合 --strata：限定参与分层的类型（逗号分隔）')
    args = ap.parse_args()

    rows = find_completed()
    if not rows:
        print('未找到任何已完成 OCR 的论文。先跑 ocr_pdfs.py。')
        return
    rows.sort(key=lambda r: r[0])

    if args.papers:
        want = [x.strip() for x in args.papers.split(',') if x.strip()]
        by_id = {r[0]: r for r in rows}
        missing = [p for p in want if p not in by_id]
        if missing:
            print('以下 paper_id 不在已完成 OCR 列表里：')
            for p in missing:
                print('  ' + p)
            return
        picked = [by_id[p] for p in want]
        pids = want
        used = used_paper_ids()
        print(f'定向回归：指定 {len(pids)} 篇（跳过随机抽样与历史排除）')
    elif args.strata:
        used = set() if args.no_exclude else used_paper_ids()
        tpath = os.path.join(RUNS_DIR, 'paper_types.jsonl')
        if not os.path.isfile(tpath):
            print(f'缺 {tpath}，先跑 python classify_papers.py 生成类型分桶。')
            return
        tmap = {}
        for ln in open(tpath, encoding='utf-8'):
            ln = ln.strip()
            if ln:
                r = json.loads(ln)
                tmap[r['paper_id']] = r['type']
        by_id = {r[0]: r for r in rows}
        want_types = set(x.strip() for x in args.types.split(',') if x.strip())
        groups = {}
        for pid, ty in sorted(tmap.items()):
            if pid not in by_id or pid in used:
                continue
            if want_types and ty not in want_types:
                continue
            groups.setdefault(ty, []).append(pid)

        random.seed(args.seed)
        picked, pids, quota = [], [], {}
        for ty in TYPE_ORDER:
            g = groups.get(ty, [])
            k = min(args.strata, len(g))
            quota[ty] = k
            for pid in random.sample(g, k):
                picked.append(by_id[pid])
                pids.append(pid)
        print(f'分层抽样：每型 {args.strata} 篇 | 可用 {len(tmap)} 篇 | 历史已用 {len(used)} 篇')
        print('  配额: ' + ', '.join(f'{t}={q}(池{len(groups.get(t, []))})'
                                     for t, q in quota.items()))
        if not pids:
            print('无可用论文（全部已用或类型被 --types 过滤空）。')
            return
    else:
        used = set() if args.no_exclude else used_paper_ids()
        pool = [r for r in rows if r[0] not in used]
        print(f'论文池 {len(rows)} 篇 | 历史已用 {len(used)} 篇 | 可用 {len(pool)} 篇')

        if len(pool) < args.n:
            print(f'可用论文不足 {args.n} 篇，请先补跑 OCR。')
            return

        random.seed(args.seed)
        picked = random.sample(pool, args.n)
        pids = [p[0] for p in picked]

    if args.dry_run:
        print('将选中：')
        for pid in pids:
            print('  ' + pid)
        return

    no = args.next or next_batch_no()
    bdir = os.path.join(RUNS_DIR, f'batch_{no:03d}')
    os.makedirs(os.path.join(bdir, 'reviews'), exist_ok=True)
    os.makedirs(os.path.join(bdir, 'extracts'), exist_ok=True)
    idir = os.path.join(bdir, 'inputs')
    os.makedirs(idir, exist_ok=True)

    n_input = 0
    with open(os.path.join(bdir, 'inputs.jsonl'), 'w', encoding='utf-8') as f:
        for pid, jp, pdir in picked:
            title, abstract = load_metadata(pdir)
            page_text = load_page_text(jp, 2)
            inp = assemble_input(title, abstract, page_text, load_all_headings(jp))
            f.write(json.dumps({'paper_id': pid, 'input': inp}, ensure_ascii=False) + '\n')
            # 单篇单独落一个文件：子 agent 只读自己那篇，避免跨篇锚定
            with open(os.path.join(idir, pid + '.txt'), 'w', encoding='utf-8') as g:
                g.write(inp)
            n_input += 1

    manifest = {
        'batch': f'batch_{no:03d}',
        'seed': args.seed,
        'selector': ('papers' if args.papers else
                     ('strata:%d' % args.strata if args.strata else 'random')),
        'n': n_input,
        'paper_ids': pids,
        'excluded_used': len(used),
        'pool_size': len(rows),
        'prompt_file': 'references/EXTRACT.md',
        'prompt_sha1': sha1_of(EXTRACT_PATH),
        'rubric_sha1': sha1_of(RUBRIC_PATH),
        'max_pages': 2,
        'created_at': time.strftime('%Y-%m-%d %H:%M:%S'),
    }
    if args.strata:
        # 记录本批的类型构成：跨批对账时按类型看覆盖，而不是只看篇数
        manifest['type_quota'] = quota
        tmap2 = {}
        for ln in open(os.path.join(RUNS_DIR, 'paper_types.jsonl'), encoding='utf-8'):
            ln = ln.strip()
            if ln:
                r = json.loads(ln)
                tmap2[r['paper_id']] = r['type']
        manifest['paper_types'] = {p: tmap2.get(p, '?') for p in pids}
    with open(os.path.join(bdir, 'manifest.json'), 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print(f'写出 {n_input} 篇输入 -> {bdir}/inputs.jsonl')
    print(f'清单 -> {bdir}/manifest.json  prompt_sha1={manifest["prompt_sha1"]}  rubric_sha1={manifest["rubric_sha1"]}')


if __name__ == '__main__':
    main()
