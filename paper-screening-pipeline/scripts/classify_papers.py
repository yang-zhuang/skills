#!/usr/bin/env python
# -*- coding: utf-8 -*-
'''
classify_papers.py — 论文内容类型分类器（确定性规则，不调用 LLM）。

用途：给 prepare_batch.py 的分层抽样提供分桶依据。随机抽样在 1500+ 篇的池里
会稳定抽到占比最大的那类（empirical / method），而综述、资源、理论、非标准论文
这类低频类型几乎抽不到 —— rubric 在它们身上的行为就永远没被测过。

分类只用三类信号，全部来自本地 OCR 产物，不联网、不看正文全文：
  1. venue（paper_id 的第二段：acl-long / acl-demo / acl-srw / ...）
  2. 标题 + 摘要（raw/metadata.json）
  3. 全文章节标题（各页 OCR 的 # 行）

判定走**优先级链**，命中即停（一篇只归一类），顺序按「结构性 > 内容性」排：
  nonstandard > survey > resource > system > theory > analysis > method > empirical

类型说明（与 rubric 的判定难点对应，不是学术分类法）：
  nonstandard  非标准论文：thesis proposal（acl-srw）、教程（acl-tutorials）、
               摘要缺失或过短、正文过短 —— 六字段可能根本抽不出来
  survey       综述 / 文献梳理
  resource     资源型：数据集、语料、基准的构建与发布（§4「不训练」的高发区）
  system       应用型：系统、工具、平台、流水线（acl-demo 主类）
  theory       理论 / 形式化 / 证明类
  analysis     机制分析类：可解释性、探针、审计、行为分析（§5 宿主模型的高发区）
  method       方法 / 模型型：提出并训练模型（§4 各范式的主战场）
  empirical    实验 / 评测型：以实验与消融为主的实证研究（兜底类）

用法：
  python classify_papers.py                     # 全池分类，写 runs/paper_types.jsonl 并打印分布
  python classify_papers.py --no-write          # 只打印分布，不落盘
  python classify_papers.py --types nonstandard,survey   # 只列出指定类型的 paper_id
  python classify_papers.py --pid 2026.acl-long.304      # 单篇诊断，打印命中信号
'''

import os
import re
import sys
import json
import collections
import argparse

from screen_papers import (
    find_completed, load_metadata, load_all_headings,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RUNS_DIR = os.path.join(BASE_DIR, 'runs')
OUT_PATH = os.path.join(RUNS_DIR, 'paper_types.jsonl')

# 判定优先级链：靠前的先判，命中即停
ORDER = ['nonstandard', 'survey', 'resource', 'system',
         'theory', 'analysis', 'method', 'empirical']

# venue → 结构性归类（只在 nonstandard / system 两处使用，其余类型跨 venue）
VENUE_NONSTANDARD = {'acl-srw', 'acl-tutorials'}   # srw = student research workshop（多为 thesis proposal）
VENUE_SYSTEM = {'acl-demo'}

# 摘要过短阈值：低于此值视为元数据缺失，无法可靠判定
ABSTRACT_MIN_LEN = 180

# ---- 信号词表（全部小写匹配）----
PAT = {
    'survey': {
        'title': [r'\bsurvey\b', r'\breview\b', r'\bsystematic\b', r'\bstate[- ]of[- ]the[- ]art\b',
                  r'\bscoping review\b', r'\bbibliometric\b'],
        'abstract': [r'we (?:present |conduct |perform )?a (?:comprehensive |systematic )?(?:survey|review)',
                     r'this (?:survey|paper) (?:surveys|reviews)',
                     r'\bsurvey (?:of|on) \b', r'\breview(?:s|ed)? (?:the|recent|existing) ',
                     r'\bwe organize\b', r'\btaxonomy of\b'],
    },
    'resource': {
        'title': [r'\bdataset\b', r'\bcorpus\b', r'\bbenchmark\b', r'\bresources?\b',
                  r'\bdata\s?set\b', r'\bcollection of\b', r'\btreebank\b', r'\bparallel data\b'],
        'abstract': [r'we (?:release|introduce|present|construct|build|create|annotate|collect)\b[^.]{0,60}?'
                     r'(?:dataset|corpus|benchmark|resource|collection|treebank)',
                     r'(?:dataset|corpus|benchmark)\s+(?:of|for|consisting)\b',
                     r'we (?:make )?(?:publicly )?available\b',
                     r'\bnewly (?:collected|annotated|constructed)\b'],
    },
    'system': {
        'title': [r'\bsystem\b', r'\btoolkit\b', r'\bplatform\b', r'\bpipeline\b',
                  r'\btool\b', r'\binterface\b', r'\bdemo\b', r'\bapplication\b', r'\bweb app\b'],
        'abstract': [r'we (?:present|introduce|build|develop|deploy)\b[^.]{0,60}?'
                     r'(?:system|toolkit|platform|pipeline|tool|interface|prototype|application)',
                     r'\bopen[- ]sourc(?:e|ed)\b', r'\bavailable at\b.*github',
                     r'\bdemonstration\b', r'\bwe demo\b'],
    },
    'theory': {
        'title': [r'\btheory\b', r'\btheoretical\b', r'\bformal\b', r'\bformaliz',
                  r'\bproof\b', r'\bprovab', r'\baxiom', r'\bmathematical\b'],
        'abstract': [r'\bwe prove\b', r'\btheoretically\b', r'\bformal(?:ly|ism|ize)\b',
                     r'\btheorem\b', r'\bclosed[- ]form\b', r'\bconvergence\b.*\bproof\b'],
    },
    'analysis': {
        'title': [r'\banalys', r'\banalysis\b', r'\binterpreta', r'\bexplain', r'\bexplanat',
                  r'\bprob(?:e|ing)\b', r'\bunderstand', r'\baudit', r'\bdiagnos',
                  r'\bwhat\b.*\blearn', r'\bhow\b.*\bwork', r'\bmechanis', r'\binspect',
                  r'\buncover', r'\breveal', r'\bfrom the inside\b'],
        'abstract': [r'we (?:analy[sz]e|investigate|examine|study|audit|probe|inspect|dissect)\b',
                     r'\bwe (?:seek|aim) to understand\b',
                     r'\bempirical(?:ly)? (?:analysis|study|investigation)\b',
                     r'\bto what extent\b', r'\bwe (?:find|observe|show) that\b.*\b(?:fail|bias|bias)'],
    },
    'method': {
        'title': [r'\bvia\b', r'\bwith\b.*\b(?:learning|training)\b', r'\bimproving\b',
                  r'\benhanc', r'\bboosting\b', r'\btoward', r'\bnovel\b', r'\bframework\b'],
        'abstract': [r'we (?:propose|introduce|present|develop)\b[^.]{0,60}?'
                     r'(?:method|approach|model|framework|architecture|technique|strategy)',
                     r'we (?:train|fine[- ]?tune|pretrain|distill)\b',
                     r'\bsupervised fine[- ]?tuning\b', r'\breinforcement learning\b'],
    },
}

# empirical 是兜底类，只用于统计命中强度，不做判定
EMPIRICAL_PAT = [r'\bexperiment', r'\babla', r'\bevaluation\b', r'\bwe evaluate\b',
                 r'\bempiric', r'\bresults show\b', r'\bbenchmark.{0,20}evaluat']

COMPILED = {k: {f: [re.compile(p) for p in v] for f, v in d.items()}
            for k, d in PAT.items()}
EMPIRICAL = [re.compile(p) for p in EMPIRICAL_PAT]


def venue_of(pid):
    ps = pid.split('.')
    return ps[1] if len(ps) >= 3 else ''


def classify(pid, title, abstract, heads):
    '''返回 (类型, 命中的信号描述列表)。命中即停，优先级链见 ORDER。'''
    t = (title or '').lower()
    a = (abstract or '').lower()
    hl = [h.lower() for h in (heads or [])]
    htext = ' | '.join(hl)
    hits = []

    # 1) nonstandard：结构性判定优先——这类文本上任何内容信号都不可靠
    v = venue_of(pid)
    if v in VENUE_NONSTANDARD:
        hits.append('venue=%s' % v)
        return 'nonstandard', hits
    if len(a.strip()) < ABSTRACT_MIN_LEN:
        hits.append('摘要过短(%d<%d)' % (len(a.strip()), ABSTRACT_MIN_LEN))
        return 'nonstandard', hits

    # 2) survey：标题或摘要的显式自陈
    for rx in COMPILED['survey']['title']:
        if rx.search(t):
            hits.append('title~%s' % rx.pattern[:28])
            return 'survey', hits
    for rx in COMPILED['survey']['abstract']:
        if rx.search(a):
            hits.append('abs~%s' % rx.pattern[:28])
            return 'survey', hits

    # 3) resource：数据集/基准/语料的构建与发布
    for rx in COMPILED['resource']['title']:
        if rx.search(t):
            hits.append('title~%s' % rx.pattern[:28])
            return 'resource', hits
    for rx in COMPILED['resource']['abstract']:
        if rx.search(a):
            hits.append('abs~%s' % rx.pattern[:28])
            return 'resource', hits

    # 4) system：系统/工具/平台；acl-demo 的 venue 直接归此
    if v in VENUE_SYSTEM:
        hits.append('venue=%s' % v)
        return 'system', hits
    for rx in COMPILED['system']['title']:
        if rx.search(t):
            hits.append('title~%s' % rx.pattern[:28])
            return 'system', hits
    for rx in COMPILED['system']['abstract']:
        if rx.search(a):
            hits.append('abs~%s' % rx.pattern[:28])
            return 'system', hits

    # 5) theory
    for rx in COMPILED['theory']['title']:
        if rx.search(t):
            hits.append('title~%s' % rx.pattern[:28])
            return 'theory', hits
    for rx in COMPILED['theory']['abstract']:
        if rx.search(a):
            hits.append('abs~%s' % rx.pattern[:28])
            return 'theory', hits

    # 6) analysis
    for rx in COMPILED['analysis']['title']:
        if rx.search(t):
            hits.append('title~%s' % rx.pattern[:28])
            return 'analysis', hits
    for rx in COMPILED['analysis']['abstract']:
        if rx.search(a):
            hits.append('abs~%s' % rx.pattern[:28])
            return 'analysis', hits

    # 7) method
    for rx in COMPILED['method']['title']:
        if rx.search(t):
            hits.append('title~%s' % rx.pattern[:28])
            return 'method', hits
    for rx in COMPILED['method']['abstract']:
        if rx.search(a):
            hits.append('abs~%s' % rx.pattern[:28])
            return 'method', hits

    # 8) empirical：兜底
    for rx in EMPIRICAL:
        if rx.search(a) or rx.search(htext):
            hits.append('abs/head~%s' % rx.pattern[:28])
            return 'empirical', hits
    hits.append('无命中，兜底')
    return 'empirical', hits


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--no-write', action='store_true', help='只打印分布，不写 paper_types.jsonl')
    ap.add_argument('--types', help='只列出指定类型（逗号分隔）的 paper_id')
    ap.add_argument('--pid', help='单篇诊断：打印命中信号')
    args = ap.parse_args()

    papers = find_completed()

    if args.pid:
        for pid, jp, d in papers:
            if pid == args.pid:
                t, a = load_metadata(d)
                h = load_all_headings(jp)
                ty, hits = classify(pid, t, a, h)
                print('%s\n  venue=%s\n  title=%s\n  类型=%s\n  命中=%s' %
                      (pid, venue_of(pid), t, ty, hits))
                return
        print('未找到', args.pid)
        return

    rows = []
    for pid, jp, d in papers:
        t, a = load_metadata(d)
        h = load_all_headings(jp)
        ty, hits = classify(pid, t, a, h)
        rows.append({'paper_id': pid, 'venue': venue_of(pid),
                     'type': ty, 'hits': hits})

    cnt = collections.Counter(r['type'] for r in rows)
    tot = len(rows)
    print('论文池 %d 篇 → 内容类型分布' % tot)
    print('%-12s %7s %8s   %s' % ('类型', '篇数', '占比', 'venue 主要来源'))
    for ty in ORDER:
        vs = collections.Counter(r['venue'] for r in rows if r['type'] == ty)
        top = ', '.join('%s(%d)' % (k, v) for k, v in vs.most_common(3))
        print('%-12s %7d %7.1f%%   %s' % (ty, cnt.get(ty, 0),
                                           cnt.get(ty, 0) * 100.0 / tot, top))
    print('%-12s %7d' % ('合计', tot))

    if args.types:
        want = set(args.types.split(','))
        sel = [r['paper_id'] for r in rows if r['type'] in want]
        print('\n%s 共 %d 篇：' % (','.join(sorted(want)), len(sel)))
        print(','.join(sel))
        return

    if not args.no_write:
        os.makedirs(RUNS_DIR, exist_ok=True)
        with open(OUT_PATH, 'w', encoding='utf-8', newline='\n') as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + '\n')
        print('\n写出 -> %s' % OUT_PATH)


if __name__ == '__main__':
    main()
