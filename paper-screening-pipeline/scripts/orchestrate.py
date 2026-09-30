# -*- coding: utf-8 -*-
"""论文初筛流水线编排脚本。

把原来由主 agent 人肉调度的 12 次 agent 会话压成 2N 次 LLM API 直调（N=篇数）：
  抽取: RUBRIC.md + EXTRACT.md + 论文 -> 前置分析+字段JSON
  评审: RUBRIC.md + EVALUATION.md + 论文 + 抽取输出 -> 评审JSON

用法:
  python orchestrate.py --batch batch_006            # 完整跑: 抽取+评审
  python orchestrate.py --batch batch_006 --stage extract   # 只跑抽取
  python orchestrate.py --batch batch_006 --dry-run  # 不调API, 只落prompt文件供核对
  python orchestrate.py --list                       # 列出已有批次

配置: 同目录 config.json (endpoint/api_key/model/concurrency), 未建则自动生成模板。
"""

import argparse
import concurrent.futures as futures
import json
import os
import sys
import time
import urllib.request
import urllib.error

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
from screen_papers import REF_DIR, RUBRIC_PATH  # noqa: E402

CONFIG = os.path.join(ROOT, 'config.json')

CONFIG_TEMPLATE = {
    'endpoint': 'https://your-vllm-host/v1/chat/completions',
    'api_key': 'EMPTY',
    'model': 'your-model-id',
    'concurrency': 16,
    'temperature': 0.2,
    'max_tokens': 4096,
    'timeout_sec': 300,
}


def load_config():
    if not os.path.exists(CONFIG):
        with open(CONFIG, 'w', encoding='utf-8') as f:
            json.dump(CONFIG_TEMPLATE, f, ensure_ascii=False, indent=2)
        sys.exit('已生成配置模板 config.json，填好 endpoint/model 后重跑。')
    with open(CONFIG, encoding='utf-8') as f:
        return json.load(f)


def read(p):
    with open(p, encoding='utf-8') as f:
        return f.read()


def call_llm(cfg, system_text, user_text):
    """一次 OpenAI 兼容 chat/completions 调用，返回 assistant 全文。"""
    body = json.dumps({
        'model': cfg['model'],
        'temperature': cfg.get('temperature', 0.2),
        'max_tokens': cfg.get('max_tokens', 4096),
        'messages': [
            {'role': 'system', 'content': system_text},
            {'role': 'user', 'content': user_text},
        ],
    }).encode('utf-8')
    req = urllib.request.Request(
        cfg['endpoint'], data=body, method='POST',
        headers={
            'Content-Type': 'application/json',
            'Authorization': 'Bearer ' + cfg.get('api_key', 'EMPTY'),
        })
    timeout = cfg.get('timeout_sec', 300)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode('utf-8'))
    return data['choices'][0]['message']['content']


def paper_ids(batch):
    bdir = os.path.join(ROOT, 'runs', batch)
    man = read(os.path.join(bdir, 'manifest.json'))
    ids = json.loads(man).get('paper_ids', [])
    if ids:
        return ids
    inp = os.path.join(bdir, 'inputs.jsonl')  # 旧格式 manifest 无 paper_ids，回退读 inputs.jsonl
    ids = []
    if os.path.isfile(inp):
        for ln in open(inp, encoding='utf-8'):
            ln = ln.strip()
            if ln:
                ids.append(json.loads(ln)['paper_id'])
    return ids


def run_stage(batch, stage, papers, cfg, dry):
    rubric = read(RUBRIC_PATH)
    inputs_dir = os.path.join(ROOT, 'runs', batch, 'inputs')
    out_dir = os.path.join(ROOT, 'runs', batch, stage + 's')
    os.makedirs(out_dir, exist_ok=True)

    def one(pid):
        out_path = os.path.join(out_dir, pid + ('.md' if stage == 'extract' else '.full.json'))
        if not dry and os.path.exists(out_path):
            return pid, 'skip', 0.0
        paper = read(os.path.join(inputs_dir, pid + '.txt'))
        t0 = time.time()
        if stage == 'extract':
            system_text = rubric + '\n\n' + read(os.path.join(REF_DIR, 'EXTRACT.md'))
            user_text = paper
        else:
            extract_out = read(os.path.join(ROOT, 'runs', batch, 'extracts', pid + '.md'))
            system_text = rubric + '\n\n' + read(os.path.join(REF_DIR, 'EVALUATION.md'))
            user_text = '【原始输入】\n' + paper + '\n\n【抽取输出】\n' + extract_out
        if dry:
            dump = os.path.join(out_dir, pid + '.prompt.txt')
            with open(dump, 'w', encoding='utf-8') as f:
                f.write('=== system ===\n' + system_text + '\n\n=== user ===\n' + user_text)
            return pid, 'dry', time.time() - t0
        text = call_llm(cfg, system_text, user_text)
        if stage == 'review':
            # 评审要求纯JSON；容错剥离常见围栏
            t = text.strip()
            if t.startswith('```'):
                t = t.split('```')[1]
                if t.startswith('json'):
                    t = t[4:]
            json.loads(t)  # 可解析性校验, 不通过则抛异常触发重试
            text = t
        with open(out_path, 'w', encoding='utf-8') as f:
            f.write(text)
        return pid, 'ok', time.time() - t0

    conc = max(1, int(cfg.get('concurrency', 16)))
    with futures.ThreadPoolExecutor(max_workers=conc) as ex:
        results = list(ex.map(one, papers))
    for pid, status, dt in results:
        print(f'{stage:8s} {pid:28s} {status:4s} {dt:5.1f}s')
    fails = [pid for pid, st, _ in results if st not in ('ok', 'skip', 'dry')]
    return fails


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--batch', help='批次名, 如 batch_006')
    ap.add_argument('--stage', choices=['extract', 'review', 'both'], default='both')
    ap.add_argument('--dry-run', action='store_true', help='不调API, 落prompt文件核对')
    ap.add_argument('--list', action='store_true')
    args = ap.parse_args()

    if args.list:
        runs = os.path.join(ROOT, 'runs')
        for b in sorted(os.listdir(runs)):
            if b.startswith('batch_') and os.path.isdir(os.path.join(runs, b)):
                print(b, len(paper_ids(b)), '篇')
        return
    if not args.batch:
        sys.exit('需要 --batch 或 --list')

    papers = paper_ids(args.batch)
    print(f'{args.batch}: {len(papers)} 篇, stage={args.stage}, dry_run={args.dry_run}')
    cfg = {} if args.dry_run else load_config()

    stages = ['extract', 'review'] if args.stage == 'both' else [args.stage]
    all_fails = []
    for st in stages:
        fails = run_stage(args.batch, st, papers, cfg, args.dry_run)
        all_fails += [(st, p) for p in fails]
    if all_fails:
        print('失败(重跑同命令会跳过已成功篇目):', all_fails)
        sys.exit(1)
    print('done')


if __name__ == '__main__':
    main()
