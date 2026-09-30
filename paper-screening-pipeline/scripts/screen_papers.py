#!/usr/bin/env python
# -*- coding: utf-8 -*-
'''
screen_papers.py — ACL 论文初筛：从 OCR 结果 + metadata 抽取 6 个字段（闭环的「执行」环节）。

输入（每篇论文）：
  <paper_id>/raw/metadata.json         -> title, abstract
  <paper_id>/paddle_ocr_vl_1_6/paddle_ocr_vl.json  -> 前 2 页 page_content + 全文各页 '#' 行（章节标题）

抽取字段（JSON 键 / 中文表头，完整定义见 references/RUBRIC.md）：
  research_area     研究方向        不超过6字研究标签（数据合成/机器翻译/评测/理论分析/安全对齐...），粗粒度领域
  research_question 研究问题        一句话点明「痛点/缺口/任务」，是后续按问题检索/聚类的主键
  method_summary    方法概述        模板「用<方法>做<事>，<关键规模/结果>」，30~40 字；撑不起填「信息不足」
  training_paradigm 训练范式        预训练 / SFT / RL / 不训练（JSON 数组，可多选，如 [SFT, RL]）
  base_model        基础模型        第一页写了才给（如 Qwen2.5-7B）；没写留空
  data_scale        数据规模        第一页写了才给（如 1M 样本 / 2T tokens）；没写留空

规则外置（重要）：字段标准只在 references/RUBRIC.md 定义一次，执行/评估/修改三个环节共用；
环节指令在 references/EXTRACT.md、references/EVALUATION.md、references/REVISE.md。
本脚本只把「rubric + 对应作业文档」拼接，不硬编码规则文本——修改 agent 只需重写 .md，碰不到 .py 逻辑。

用法：
  # 仅验证抽取（不调用 LLM）：随机抽 5 篇，打印组装好的输入
  python screen_papers.py --dry-run --sample 5

  # 真实跑（需配置 LLM，见环境变量）：跑全部已完成论文，输出 CSV
  python screen_papers.py --out screen_result.csv

  # 抽查前 20 篇
  python screen_papers.py --limit 20 --out screen_result.csv

LLM 配置（OpenAI 兼容 chat/completions）：
  SCREEN_LLM_BASE_URL   默认 https://api.openai.com/v1
  SCREEN_LLM_API_KEY    必填（不填则自动 dry-run）
  SCREEN_LLM_MODEL      默认 gpt-4o-mini
'''

import os
import sys
import json
import re
import glob
import csv
import argparse
import random

# ---- 配置：论文根目录 ----
PAPERS_DIR = os.getenv('PAPERS_DIR', r'G:/acl-anthology/papers/ACL/2026')
SCREEN_NAME = 'paddle_ocr_vl_1_6'
JSON_NAME = 'paddle_ocr_vl.json'

# ---- 规则材料路径（skill 包 references/，闭环直接改这里）----
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REF_DIR = os.getenv('SCREEN_REF_DIR', r'G:/skills/paper-screening-pipeline/references')
RUBRIC_PATH = os.path.join(REF_DIR, 'RUBRIC.md')
DOC_FILES = {'extract': 'EXTRACT.md', 'review': 'EVALUATION.md', 'revise': 'REVISE.md'}

LLM_BASE_URL = os.getenv('SCREEN_LLM_BASE_URL', 'https://api.openai.com/v1')
LLM_API_KEY = os.getenv('SCREEN_LLM_API_KEY', '')
LLM_MODEL = os.getenv('SCREEN_LLM_MODEL', 'gpt-4o-mini')

FIELDS = ['paper_id', 'title', '研究方向', '研究问题', '方法概述', '训练范式', '基础模型', '数据规模']


def load_metadata(paper_dir):
    '''读 raw/metadata.json 拿 title / abstract；没有就返回空。'''
    p = os.path.join(paper_dir, 'raw', 'metadata.json')
    if not os.path.isfile(p):
        return '', ''
    try:
        d = json.load(open(p, encoding='utf-8'))
        return (d.get('title', '') or '').strip(), (d.get('abstract', '') or '').strip()
    except Exception:
        return '', ''


def load_page_text(json_path, max_pages=2):
    '''读 paddle_ocr_vl.json，返回前 max_pages 页拼接文本。'''
    try:
        d = json.load(open(json_path, encoding='utf-8'))
    except Exception:
        return ''
    chunks = []
    pages = d.get('pages', [])
    for i in range(min(max_pages, len(pages))):
        block = pages[i]
        if isinstance(block, list) and block and isinstance(block[0], dict):
            chunks.append(block[0].get('page_content', ''))
        elif isinstance(block, dict):
            chunks.append(block.get('page_content', ''))
    return '\n'.join(chunks)


def extract_headings(text):
    '''抓 '#' 开头的行（章节标题），去前后空白，去重保序。'''
    seen = []
    for ln in text.splitlines():
        s = ln.strip()
        if s.startswith('#'):
            s = s.lstrip('#').strip()
            if s and s not in seen:
                seen.append(s)
    return seen


def load_all_headings(json_path):
    '''全文所有页的 page_content 拼接后抓 '#' 行（章节标题），去重保序。'''
    try:
        d = json.load(open(json_path, encoding='utf-8'))
    except Exception:
        return []
    texts = []
    for block in d.get('pages', []):
        if isinstance(block, list) and block and isinstance(block[0], dict):
            texts.append(block[0].get('page_content', ''))
        elif isinstance(block, dict):
            texts.append(block.get('page_content', ''))
    return extract_headings('\n'.join(texts))


def assemble_input(title, abstract, page_text, heads):
    '''组装给 LLM 的纯文本输入。正文抽空/过短时，用摘要兜底正文段。heads 为全文章节标题列表。'''
    if len(page_text.strip()) < 200:
        page_text = page_text.strip() + '\n\n[正文OCR偏短，以下用摘要补足]\n' + abstract
    out = []
    out.append(f'# 标题\n{title}\n')
    out.append(f'# 摘要\n{abstract}\n')
    out.append(f'# 前 2 页正文（含摘要、引言）\n{page_text}\n')
    if heads:
        out.append('# 全文章节标题（来自全文各页 OCR # 行）\n' + '\n'.join(f'- {h}' for h in heads))
    return '\n'.join(out)


def extract_json(text):
    '''从 LLM 响应中提取 JSON 对象：优先 ```json 代码块，否则最后一个 {..}。'''
    m = re.search(r'```json\s*(\{.*?\})\s*```', text, re.DOTALL)
    if m:
        return json.loads(m.group(1))
    m = re.search(r'```\s*(\{.*?\})\s*```', text, re.DOTALL)
    if m:
        return json.loads(m.group(1))
    s, e = text.rfind('{'), text.rfind('}')
    if s != -1 and e > s:
        return json.loads(text[s:e + 1])
    raise ValueError('响应中未找到 JSON 对象')


def load_prompt(kind):
    '''读 RUBRIC.md + references/<KIND>.md 拼接成完整 system prompt。kind: extract / review / revise。'''
    with open(RUBRIC_PATH, encoding='utf-8') as f:
        rubric = f.read()
    p = os.path.join(REF_DIR, DOC_FILES[kind])
    if not os.path.isfile(p):
        raise FileNotFoundError('缺少作业文档: ' + p)
    with open(p, encoding='utf-8') as f:
        body = f.read()
    return rubric + '\n\n' + body


def build_prompt(inp, analysis=True):
    '''组装执行 agent 的 system prompt：RUBRIC.md（标准）+ EXTRACT.md（指令）拼接。'''
    sys_msg = load_prompt('extract')
    if not analysis:
        sys_msg += ('\n\n[外部指令] 本次关闭前置分析：不输出 markdown 分析，'
                    '只输出一个 JSON 对象，键为 research_area, research_question, '
                    'method_summary, training_paradigm, base_model, data_scale。')
    return sys_msg, inp


def call_llm(sys_msg, user_msg, analysis=True):
    '''OpenAI 兼容接口调用，返回解析后的 dict。无 key 时抛 RuntimeError。
    analysis=True 时响应为 markdown 分析 + ```json 块，用 extract_json 取 JSON 部分；
    analysis=False 时强制 json_object 响应。'''
    if not LLM_API_KEY:
        raise RuntimeError('未配置 SCREEN_LLM_API_KEY，无法调用 LLM')
    import requests

    payload = {
        'model': LLM_MODEL,
        'messages': [
            {'role': 'system', 'content': sys_msg},
            {'role': 'user', 'content': user_msg},
        ],
        'temperature': 0,
    }
    if not analysis:
        payload['response_format'] = {'type': 'json_object'}
    resp = requests.post(
        f'{LLM_BASE_URL.rstrip("/")}/chat/completions',
        headers={'Authorization': f'Bearer {LLM_API_KEY}', 'Content-Type': 'application/json'},
        json=payload,
        timeout=120,
    )
    resp.raise_for_status()
    content = resp.json()['choices'][0]['message']['content']
    return extract_json(content)


def find_completed():
    '''返回 [(paper_id, json_path, paper_dir)] 列表。'''
    rows = []
    for jp in glob.glob(os.path.join(PAPERS_DIR, '*', SCREEN_NAME, JSON_NAME)):
        parts = jp.split(os.sep)
        pid = parts[-3]
        pdir = os.path.dirname(os.path.dirname(jp))
        rows.append((pid, jp, pdir))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default='screen_result.csv', help='输出 CSV 路径')
    ap.add_argument('--limit', type=int, default=0, help='只处理前 N 篇（按扫描顺序）')
    ap.add_argument('--sample', type=int, default=0, help='随机抽取 N 篇（与 --dry-run 配合抽查）')
    ap.add_argument('--dry-run', action='store_true', help='只抽取并打印输入，不调用 LLM')
    ap.add_argument('--no-analysis', dest='analysis', action='store_false',
                   help='关闭前置分析（端点支持思考或强制 json_object 时用）')
    args = ap.parse_args()

    rows = find_completed()
    if not rows:
        print('未找到任何已完成 OCR 的论文。先跑 ocr_pdfs.py。')
        return

    if args.sample:
        random.seed(42)
        rows = random.sample(rows, min(args.sample, len(rows)))
    if args.limit:
        rows = rows[: args.limit]

    print(f'待处理论文：{len(rows)} 篇 | dry-run={args.dry_run} | 前置分析={args.analysis} | LLM={"启用" if LLM_API_KEY else "未配置(自动dry-run)"}')

    if args.dry_run or not LLM_API_KEY:
        for pid, jp, pdir in rows:
            title, abstract = load_metadata(pdir)
            page_text = load_page_text(jp, 2)
            heads = load_all_headings(jp)
            inp = assemble_input(title, abstract, page_text, heads)
            print('=' * 70)
            print(f'paper_id: {pid}')
            print(f'title: {title[:60]}')
            print(f'abstract前80字: {abstract[:80]}')
            print(f'前2页字符数: {len(page_text)} | 全文章节标题数: {len(heads)}')
            print('--- 组装输入预览（前 1200 字）---')
            print(inp[:1200])
        print('=' * 70)
        print('dry-run 结束。确认抽取无误后，配置 SCREEN_LLM_API_KEY 再跑真实分类。')
        return

    out_rows = []
    for pid, jp, pdir in rows:
        title, abstract = load_metadata(pdir)
        page_text = load_page_text(jp, 2)
        inp = assemble_input(title, abstract, page_text, load_all_headings(jp))
        sys_msg, user_msg = build_prompt(inp, args.analysis)
        try:
            res = call_llm(sys_msg, user_msg, args.analysis)
        except Exception as e:
            print(f'[跳过] {pid} LLM 失败: {e}', file=sys.stderr)
            res = {}
        train = res.get('training_paradigm', [])
        if isinstance(train, list):
            train = '+'.join(str(x) for x in train)
        out_rows.append([
            pid, title,
            res.get('research_area', ''),
            res.get('research_question', ''),
            res.get('method_summary', ''),
            train,
            res.get('base_model', ''),
            res.get('data_scale', ''),
        ])
        print(f'完成 {pid}: 研究方向={out_rows[-1][2]} | 研究问题={out_rows[-1][3]} | 训练={out_rows[-1][5]}')

    with open(args.out, 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f)
        w.writerow(FIELDS)
        w.writerows(out_rows)
    print(f'写出 {len(out_rows)} 行 -> {args.out}')


if __name__ == '__main__':
    main()
