#!/usr/bin/env python
# -*- coding: utf-8 -*-
'''
check_draft.py — 闭环第 5 步的闸门：校验修改 agent 的 draft 能否安全覆盖执行文档。

为什么必须是纯代码：修改 agent 改的正是「下游程序要解析的东西」——JSON 键名、代码块、
两步顺序。这些是确定性事实，肉眼复核会漏；交给程序逐条查，不过关就不许覆盖。

查什么（8 项，任一 FAIL 即不可覆盖）：
  1. 三段齐全且按序：修改后的执行文档 / rubric 修订建议 / 变更说明
  2. 执行文档段非空
  3. 6 个 JSON 键在正文中齐全
  4. 两步顺序：前置分析 出现在 字段抽取 之前
  5. ```json 输出契约仍在，正文内示例块（若有）均可 json.loads
  6. training_paradigm 被要求为数组（看示例块，或看契约文字）
  7. 既有契约结构未被破坏（以 base 为准：6 键、两步关键词、```json 输出契约不复存在即失败）
  8. 末尾维度分布行存在且可解析

另有 3 项 WARN（不阻断，须人工看一眼）：
  - 指代表述（「如前所述」「见前文」类，修改 agent 的高发残留）
  - 正文与 base 逐字一致（本次没改动，先确认清单是不是空的）
  - 维度分布非全零但变更说明里没有 `[维度标签]` 条目

加 `--apply` 可直接落地：过闸门才执行「快照旧版 -> 覆盖执行文档 -> 报新指纹」，
省掉人工 cp（人工 cp 会漏快照或覆盖错版本）；不过闸门时 --apply 一律无效。

用法：
  python check_draft.py --draft runs/batch_013/revise_drill/extract_v8_draft.md
  python check_draft.py --draft <path> --base <另一个 EXTRACT.md>
  python check_draft.py --draft <path> --apply     # 过了闸门就直接落地：快照旧版 + 覆盖执行文档
退出码：0=通过，1=有 FAIL，2=文件不存在。
'''

import os
import re
import sys
import json
import shutil
import hashlib
import argparse

from screen_papers import REF_DIR, extract_json

KEYS = ['research_area', 'research_question', 'method_summary',
        'training_paradigm', 'base_model', 'data_scale']
SEC = ['## 修改后的执行文档', '## rubric 修订建议', '## 变更说明']
STEP = ['前置分析', '字段抽取']
TAGS = re.compile(r'\[(role|goal|steps|constraints|format)(\s*\+\s*(role|goal|steps|constraints|format))*\]')
REF_WORDS = ['如前所述', '如上所述', '见前文', '如前文', '见上文', '上文提到', '前面提到', '前述内容']
PLACEHOLDER = ['（略）', '(略)', '此处省略', '（同原文）', '（保持原样）', '（以下省略']


def sha1(path):
    return hashlib.sha1(open(path, 'rb').read()).hexdigest()[:12]


def next_snapshot(sdir, prefix='extract_v'):
    '''下一个可用快照名：扫描 extract_vN.md 取最大 N + 1。'''
    n = 0
    if os.path.isdir(sdir):
        for fn in os.listdir(sdir):
            m = re.match(re.escape(prefix) + r'(\d+)\.md$', fn)
            if m:
                n = max(n, int(m.group(1)))
    return os.path.join(sdir, f'{prefix}{n + 1}.md')


def apply_draft(base_path, body, sdir):
    '''把过闸门的正文落地：先快照旧版，再覆盖执行文档，最后报新指纹。'''
    os.makedirs(sdir, exist_ok=True)
    snap = next_snapshot(sdir)
    shutil.copyfile(base_path, snap)
    with open(base_path, 'w', encoding='utf-8', newline='\n') as f:
        f.write(body + '\n')
    print()
    print(f'已快照旧版 -> {snap}  (sha1 {sha1(snap)})')
    print(f'已覆盖执行文档 -> {base_path}  (sha1 {sha1(base_path)} / {len(body.encode("utf-8")) + 1} B)')
    print('下一步：换新 seed 建批复测（不要用提出建议的那批验证）= python prepare_batch.py --seed <新> --n 6')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--draft', required=True, help='修改 agent 产出的 draft 路径')
    ap.add_argument('--base', default=os.path.join(REF_DIR, 'EXTRACT.md'),
                    help='当前执行文档（对比基线），默认取 skill 包 references/EXTRACT.md')
    ap.add_argument('--apply', action='store_true',
                    help='过闸门后直接落地（快照旧版 + 覆盖执行文档）；不过闸门绝不落地')
    ap.add_argument('--snapshot-dir', default=os.path.join(os.getcwd(), 'runs', 'prompt_snapshots'),
                    help='快照目录，默认 <cwd>/runs/prompt_snapshots')
    args = ap.parse_args()

    if not os.path.isfile(args.draft):
        print('draft 不存在: ' + args.draft)
        return 2
    draft = open(args.draft, encoding='utf-8').read()
    base = open(args.base, encoding='utf-8').read() if os.path.isfile(args.base) else ''

    fails, warns = [], []

    def chk(cond, name, detail=''):
        print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f' — {detail}' if detail else ''))
        if not cond:
            fails.append(name)
        return cond

    def warn(cond, name):
        if not cond:
            warns.append(name)
        return cond

    print(f'draft: {args.draft}  ({len(draft.splitlines())} 行 / {len(draft.encode("utf-8"))} B)')
    print(f'base : {args.base}  ({len(base.splitlines())} 行 / {len(base.encode("utf-8"))} B)')
    print()

    # 1 三段齐全且有序
    pos = [draft.find(s) for s in SEC]
    detail = '缺 ' + '、'.join(s for s, p in zip(SEC, pos) if p == -1) if -1 in pos else '齐全'
    chk(-1 not in pos and pos == sorted(pos), '1. 三段齐全且有序', detail)

    # 正文段（缺段落时退化为全文，让后续检查仍能给出有意义的结论）
    body = draft
    if pos[0] != -1:
        end = pos[1] if pos[1] != -1 else len(draft)
        body = draft[pos[0] + len(SEC[0]):end].strip()

    # 2 正文非空且无截断占位
    ph = [w for w in PLACEHOLDER if w in body]
    chk(len(body) > 500 and not ph, '2. 执行文档段非空、无截断占位',
        f'{len(body.encode("utf-8"))} B' + (f'，含占位词 {ph}' if ph else ''))

    # 3 六个键
    missing = [k for k in KEYS if k not in body]
    chk(not missing, '3. 6 个 JSON 键齐全', '缺 ' + str(missing) if missing else '')

    # 4 两步顺序
    i_pre, i_fld = body.find(STEP[0]), body.find(STEP[1])
    chk(i_pre != -1 and i_fld != -1 and i_pre < i_fld, '4. 两步顺序（前置分析 先于 字段抽取）',
        f'位置 {i_pre} / {i_fld}')

    # 5、6 输出契约：```json 指令必须在（下游 extract_json 靠它抓块），json 示例块逐个试解析
    blocks = re.findall(r'```json\s*\n(.*?)\n\s*```', body, re.S) or \
             re.findall(r'```\s*\n(\{.*?\})\n\s*```', body, re.S)
    parse_ok = True
    for b in blocks:
        try:
            d = json.loads(b)
        except Exception as e:
            parse_ok = False
            print(f'     [坏块] {str(e)[:60]}')
            continue
        miss = [k for k in KEYS if k not in d]
        if miss:
            parse_ok = False
            print(f'     [缺键块] {miss}')
    has_fence = '```json' in body
    chk(has_fence and parse_ok, '5. ```json 输出契约在、示例块均可解析',
        f'示例块 {len(blocks)} 个' + ('' if has_fence else '，缺 ```json 指令'))

    # 6 training_paradigm 必须被要求为数组：有示例块看示例，没有就看契约文字
    typed = all(isinstance(json.loads(b).get('training_paradigm'), list) for b in blocks) if blocks \
        else ('training_paradigm' in body and '数组' in body)
    chk(typed, '6. training_paradigm 要求为数组')

    # 7 既有契约结构未被破坏
    broke = []
    if base:
        if [k for k in KEYS if k in base and k not in body]:
            broke.append('键缺失')
        if [s for s in STEP if s in base and s not in body]:
            broke.append('两步关键词缺失')
        if '```json' in base and '```json' not in body:
            broke.append('```json 输出契约缺失')
    chk(not broke, '7. 既有契约结构未被破坏', '、'.join(broke) + '（对比 base）' if broke else '')

    # 8 维度分布行
    m = re.search(r'维度分布[:：]\s*(.+)', draft)
    dist = {}
    if m:
        for kv in re.findall(r'([a-z]+)\s*=\s*(\d+)', m.group(1)):
            dist[kv[0]] = int(kv[1])
    chk(bool(dist) and set(dist) == {'role', 'goal', 'steps', 'constraints', 'format'},
        '8. 末尾维度分布行存在且可解析', str(dist) if dist else '未找到')

    print()
    # WARN 1：指代表述
    hits = [w for w in REF_WORDS if w in body]
    for w in hits:
        print(f'[WARN] 指代表述「{w}」出现 {body.count(w)} 处——修改 agent 高发残留，人工确认指向是否正确')
    warn(not hits, '指代表述')

    # WARN 2：与 base 逐字一致
    same = base and body == base.strip()
    if same:
        print('[WARN] 正文与 base 逐字一致——本次无任何改动，先确认过滤后清单是不是空的')
        warns.append('无改动')

    # WARN 3：有改动却没打维度标签
    if any(dist.values()) and not TAGS.search(draft):
        print('[WARN] 维度分布非全零但变更说明里没有 `[维度标签]` 条目（REVISE.md 输出格式要求）')
        warns.append('缺维度标签')

    if fails:
        print(f'契约校验: 未通过（{len(fails)} 项失败，禁止覆盖执行文档）: {"、".join(fails)}')
        return 1
    print(f'契约校验: 通过（8/8' + (f'，{len(warns)} 项待人工确认' if warns else '') + '）')

    if args.apply:
        if same:
            print('未应用：正文与 baseline 逐字一致（本次无改动），不产生快照。')
        else:
            apply_draft(args.base, body, args.snapshot_dir)
    return 0


if __name__ == '__main__':
    sys.exit(main())
