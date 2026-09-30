#!/usr/bin/env python
# -*- coding: utf-8 -*-
'''
cluster_reviews.py — 闭环第 4 步（汇总）：把一批评审 JSON 聚类成高频建议。

为什么必须有这一步、且必须是纯代码：
  修改 agent 只能看「聚类后的高频建议」，不能看单篇、也不能看原始评审 JSON——
  否则它会为个案改规则（过拟合）、或被单条噪声带偏。聚类是纯计数逻辑，不该交给 LLM。

聚什么：
  1. rubric_issues（rubric 自身歧义/冲突/未覆盖）→ 按 (条款, 问题类型) 分组，交**人**裁断
  2. violation（执行端违规）→ 按 (字段, 问题类型) 分组，交**修改 agent** 改执行文档

采纳阈值（单评审员架构）：
  同一 (条款/字段, 类型) 在本批命中 ≥ max(2, 篇数/3) 篇即采纳——单批内的跨篇复现。
  「跨批复现」由本脚本一并算：把历史各批的 merged.json 并进来，同一 (字段/条款, 条款集合)
  出现在 ≥2 个批次即跨批复现（三级分级里的「立即修改」级判据）——不靠人工翻历史文件。
  历史 merged.json 没有 'rules' 字段时，从样本的 rule_ref 反推（见 rules_of）。
  评审文件里的 perspective 字段是输出契约的一部分（单评审员恒为 "full"），
  仅随样本透传，不参与计数。

顺带做评审的**引用核验**（同一批文件读一遍，不用另开命令）：
  violation 的 `current` 必须能在本篇 `extracts/<pid>.fields.json` 里逐字找到
  （允许用「；」并列多段，每段都要能逐字找到）；`evidence` 里「」内的片段必须在
  本篇 `inputs/<pid>.txt` 里逐字找到。核验不过的评审不参与聚类，脚本以退出码 1 报出，
  按纪律重发评审 agent——评审端的高发残留是包装式 / 概括式 current，肉眼查不全。

用法：
  python cluster_reviews.py --batch batch_002
  python cluster_reviews.py --batch batch_002 --no-verify   # 只聚类，跳过引用核验
'''

import os
import re
import sys
import json
import glob
import argparse
import collections

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RUNS_DIR = os.path.join(BASE_DIR, 'runs')

# 问题类型：靠关键词归到固定几类，避免自由文本无法计数
KIND_RULES = [
    ('篇幅口径', ['字数', '篇幅', '上限', '超长', '过长', '25 字', '30~40', '15~30', '6 字', '边界摆动']),
    ('示例自相矛盾', ['正例', '反例', '示例', '自身矛盾', '与正例', '7 字正例']),
    ('覆盖缺失', ['未覆盖', '未规定', '没有规定', '缺', '缺失', '无条款', '未定义', '未写明', '未给出', '无判据']),
    ('规则冲突', ['冲突', '矛盾', '互斥', '两个出口', '重叠', '打架', '不一致']),
    ('依据要求', ['absence', '正面信号', '泄露', '逐字', '可核对', '编造', '脑补']),
    ('粒度', ['过粗', '过细', '粒度', '粗粒度', '形态']),
    ('出处', ['出处', '泛称', '评测集', '吞吐', '训练数据量']),
]


def kind_of(text):
    for name, kws in KIND_RULES:
        for kw in kws:
            if kw in text:
                return name
    return '其他'


def main_rule(rule_ref):
    '''把「4（取值表「不训练」行 / 边界第 2 条）」这类文本归一成主条款号 §4。'''
    s = str(rule_ref or '')
    m = re.search(r'(\d+)', s.replace('§', ''))
    return '§' + m.group(1) if m else '§?'


def verify_refs(bdir, files):
    '''核验评审的引用可核对性：current ⊆ fields.json、evidence「」引文 ⊆ inputs。
    返回 [(文件名, [问题...]), ...]；空列表即全部通过。'''
    probs = []
    for p in files:
        try:
            d = json.load(open(p, encoding='utf-8'))
        except Exception:
            continue  # 解析失败由 bad_files 单独报
        pid = d.get('paper_id', os.path.basename(p).split('.')[0])
        fj = os.path.join(bdir, 'extracts', pid + '.fields.json')
        ip = os.path.join(bdir, 'inputs', pid + '.txt')
        f_txt = open(fj, encoding='utf-8').read() if os.path.isfile(fj) else ''
        if f_txt:
            try:
                f_txt += '\n' + json.dumps(json.loads(f_txt).get('fields', {}), ensure_ascii=False)
            except Exception:
                pass
        i_txt = open(ip, encoding='utf-8').read() if os.path.isfile(ip) else ''
        bad = []
        for f in d.get('findings', []):
            if f.get('verdict') != 'violation':
                continue
            cur = str(f.get('current', '')).strip()
            segs = [s.strip() for s in re.split(r'[；;]', cur) if s.strip()]
            if not segs:
                bad.append('current 为空')
            for s in segs:
                if s not in f_txt:
                    bad.append('current 不在 fields.json: ' + s[:40])
            for q in re.findall(r'「([^」]{10,})」', str(f.get('evidence', ''))):
                if i_txt and q not in i_txt:
                    bad.append('evidence 引文不在 inputs: ' + q[:40])
        if bad:
            probs.append((os.path.basename(p), bad))
    return probs


def rules_of(row):
    '''组的条款集合：新格式直接有 'rules'；历史 merged.json 从 samples 的 rule_ref 反推。'''
    if row.get('rules'):
        return tuple(sorted(row['rules']))
    return tuple(sorted({main_rule(s.get('rule_ref')) for s in row.get('samples', [])}))


def brief(row, n=110):
    '''取组内首个样本的建议摘要，供人快速分辨「同条款但方向相反」的假家族。'''
    s = (row.get('samples') or [{}])[0]
    t = str(s.get('suggestion') or s.get('issue') or s.get('evidence') or '')
    return t[:n].replace('\n', ' ')


def cross_batch(batch, rub_all, vio_all):
    '''跨批复现判定：并进历史各批 merged.json，按 (组类, scope, 条款集合) 数出现在几个批次。
    跨批 ≥2 即三级分级里的「立即修改」级**候选**——是否真属同一家族要人看每批的建议摘要：
    同条款下方向可能相反（实测条款 4：一批要求「应填不训练」，另一批要求「应填 []」），
    那是 rubric 歧义，走人裁，不改执行文档。
    对齐用**条款**而不是 kind——kind 是关键词启发式，兜底值「其他」会把不同家族混成一个。'''
    hist = {}
    for p in sorted(glob.glob(os.path.join(RUNS_DIR, 'batch_*', 'merged.json'))):
        b = os.path.basename(os.path.dirname(p))
        if b == batch:
            continue
        try:
            d = json.load(open(p, encoding='utf-8'))
        except Exception:
            continue
        for key in ('rubric_issue_groups', 'violation_groups'):
            for row in d.get(key, []):
                hist.setdefault((key, row.get('scope'), rules_of(row)), {})[b] = row
    out = {'violation_cross': [], 'rubric_issue_cross': []}
    for key, rows, slot in (('violation_groups', vio_all, 'violation_cross'),
                            ('rubric_issue_groups', rub_all, 'rubric_issue_cross')):
        for row in rows:
            hits = hist.get((key, row['scope'], rules_of(row)), {})
            if not hits:
                continue
            per_batch = [{'batch': b, 'suggestion': brief(r)} for b, r in sorted(hits.items())]
            per_batch.append({'batch': batch, 'suggestion': brief(row)})
            out[slot].append({'scope': row['scope'], 'rules': row['rules'], 'kind': row['kind'],
                              'batches': [x['batch'] for x in per_batch], 'n_batches': len(per_batch),
                              'this_batch_papers': row['n_papers'], 'per_batch': per_batch})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--batch', required=True)
    ap.add_argument('--no-verify', action='store_true', help='跳过评审引用核验')
    args = ap.parse_args()

    bdir = os.path.join(RUNS_DIR, args.batch)
    files = sorted(glob.glob(os.path.join(bdir, 'reviews', '*.json')))
    if not files:
        print('没有评审文件，先跑评估环节。')
        return 0

    probs = [] if args.no_verify else verify_refs(bdir, files)
    if probs:
        print(f'引用核验未通过 {len(probs)}/{len(files)} 份（不参与聚类，按纪律重发评审 agent）：')
        for name, bad in probs:
            print(f'  [✗] {name}: ' + ' | '.join(bad[:3]))
        failed = {n for n, _ in probs}
        files = [p for p in files if os.path.basename(p) not in failed]
        if not files:
            print('全部评审核验不过，无可用评审。')
            return 1
        print(f'剩余 {len(files)} 份通过核验，继续聚类。')
    else:
        print(f'引用核验 {len(files)}/{len(files)} 通过。')

    n_papers, n_expect = 6, 6
    mani = os.path.join(bdir, 'manifest.json')
    if os.path.isfile(mani):
        n_expect = len(json.load(open(mani, encoding='utf-8')).get('paper_ids', [])) or 6

    rub_groups = collections.defaultdict(lambda: {'papers': set(), 'samples': [], 'rules': set()})
    vio_groups = collections.defaultdict(lambda: {'papers': set(), 'samples': [], 'rules': set()})
    bad_files = []
    papers_with_vio = set()

    for p in files:
        try:
            d = json.load(open(p, encoding='utf-8'))
        except Exception as e:
            bad_files.append((os.path.basename(p), str(e)))
            continue
        pid = d.get('paper_id', os.path.basename(p).split('.')[0])

        for r in d.get('rubric_issues', []):
            k = (main_rule(r.get('rule_ref')), kind_of(str(r.get('issue', '')) + str(r.get('rule_ref', ''))))
            g = rub_groups[k]
            g['papers'].add(pid)
            g['rules'].add(main_rule(r.get('rule_ref')))
            if len(g['samples']) < 3:
                g['samples'].append({'paper_id': pid,
                                     'rule_ref': r.get('rule_ref'), 'issue': r.get('issue'),
                                     'suggestion': r.get('suggestion')})

        for f in d.get('findings', []):
            if f.get('verdict') != 'violation':
                continue
            papers_with_vio.add(pid)
            k = (str(f.get('field', '?')), kind_of(str(f.get('suggestion', '')) + str(f.get('rule_ref', ''))))
            g = vio_groups[k]
            g['papers'].add(pid)
            g['rules'].add(main_rule(f.get('rule_ref')))
            if len(g['samples']) < 3:
                g['samples'].append({'paper_id': pid,
                                     'rule_ref': f.get('rule_ref'), 'evidence': f.get('evidence'),
                                     'suggestion': f.get('suggestion')})

    thr_papers = max(2, n_expect // 3)

    def pick(groups):
        hi, allg = [], []
        for (scope, kind), g in sorted(groups.items(), key=lambda kv: -len(kv[1]['papers'])):
            row = {'scope': scope, 'kind': kind, 'rules': sorted(g['rules']),
                   'n_papers': len(g['papers']), 'papers': sorted(g['papers']), 'samples': g['samples']}
            allg.append(row)
            if len(g['papers']) >= thr_papers:
                hi.append(row)
        return allg, hi

    rub_all, rub_hi = pick(rub_groups)
    vio_all, vio_hi = pick(vio_groups)
    cross = cross_batch(args.batch, rub_all, vio_all)

    n_vio = sum(
        1 for p in files
        for f in json.load(open(p, encoding='utf-8')).get('findings', [])
        if f.get('verdict') == 'violation')
    n_rub = sum(len(json.load(open(p, encoding='utf-8')).get('rubric_issues', [])) for p in files)

    merged = {'batch': args.batch, 'n_reviews': len(files),
              'rubric_issue_groups': rub_all, 'violation_groups': vio_all}
    high = {'batch': args.batch, 'threshold_min_papers': thr_papers,
            'rubric_issue_highfreq': rub_hi, 'violation_highfreq': vio_hi}
    metrics = {'batch': args.batch, 'n_papers': n_expect, 'n_reviews': len(files),
               'violation_total': n_vio,
               'papers_with_violation': len(papers_with_vio),
               'violation_rate_by_paper': round(len(papers_with_vio) / n_expect, 3),
               'rubric_issue_total': n_rub,
               'cross_batch_violation': len(cross['violation_cross']),
               'cross_batch_rubric_issue': len(cross['rubric_issue_cross']),
               'verify_failed': len(probs),
               'parse_failed': len(bad_files), 'parse_failed_files': bad_files}

    for name, obj in [('merged.json', merged), ('merged_highfreq.json', high),
                      ('metrics.json', metrics), ('cross_batch.json', cross)]:
        with open(os.path.join(bdir, name), 'w', encoding='utf-8') as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)

    print(f'评审 {len(files)} 个 | 篇数 {n_expect}')
    print(f'violation 合计 {n_vio} | 有 violation 的篇数 {len(papers_with_vio)}/{n_expect} → violation 率 {metrics["violation_rate_by_paper"]}')
    print(f'rubric_issue 合计 {n_rub}')
    print(f'聚类：rubric 分组 {len(rub_all)}（高频 {len(rub_hi)}）| violation 分组 {len(vio_all)}（高频 {len(vio_hi)}）')
    print(f'--- 高频 rubric 问题（需人裁断）---')
    for r in rub_hi:
        print(f"  {r['scope']} | {r['kind']} | 篇{len(r['papers'])}")
    print('--- 高频 violation（交修改 agent）---')
    for r in vio_hi:
        print(f"  {r['scope']} | {r['kind']} | 篇{len(r['papers'])}")

    print('--- 跨批复现（按 (字段/条款, 条款) 对齐历史 merged.json；≥2 批即「立即修改」级候选）---')
    for kind_key, rows, tail in (('violation', cross['violation_cross'], ''),
                                 ('rubric_issue', cross['rubric_issue_cross'], ' → 人裁')):
        if not rows:
            print(f'  {kind_key}：无跨批复现' + (' → 本批 violation 全部按「暂缓记录」' if kind_key == 'violation' else ' → 暂不下发人裁'))
            continue
        for r in rows:
            print(f"  {kind_key} | {r['scope']} | {'/'.join(r['rules'])} | {r['n_batches']} 批: {','.join(r['batches'])}{tail}")
            for pb in r['per_batch']:
                print(f"      {pb['batch']}: {pb['suggestion']}")
            print('      ↑ 逐批对读：同条款但建议方向相反 = rubric 歧义（走人裁），不是同一家族')

    return 1 if probs else 0


if __name__ == '__main__':
    sys.exit(main())
