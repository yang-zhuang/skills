#!/usr/bin/env python3
"""校验每页讲稿字数（Slide DSL 的 notes 规范：≤300 字、无元描述、第一人称）。

扫描 slides/*.slide，提取 <Slide notes={`...`}> 的内容，检查：
  1. 字数 > 300（超限）
  2. 出现禁用元描述（"这一页展示了/本页聚焦于/下图描述了/接下来进入/建议先/讲的时候"）
  3. 残留"讲稿："前缀
  4. 缺少 notes 属性（若用 --require 开启）

用法:
    python check_notes.py <slides目录> [--require] [--limit 300]

退出码：0 全部通过；1 存在违规。
"""

import argparse
import glob
import os
import re
import sys

# 只列真正的「对 PPT 的说明」元描述，避免误伤"建议先指定接口人"这类正常语义。
BANNED = [
    '这一页展示', '本页聚焦', '下图描述', '接下来进入',
    '讲的时候', '这一页介绍', '本页展示', '如图所示',
    '这一页讲的是', '本页主要讲', '请看下图', '下一页我们',
]


def extract_notes(text):
    # 匹配 <Slide ... notes={`...`} ...> ，反引号内容
    m = re.search(r'notes=\{`(.*?)`\}', text, re.S)
    return m.group(1) if m else None


def main():
    ap = argparse.ArgumentParser(description='校验 Slide DSL 讲稿字数与措辞')
    ap.add_argument('slides_dir', help='slides 目录')
    ap.add_argument('--require', action='store_true', help='要求每页都必须有讲稿')
    ap.add_argument('--limit', type=int, default=300, help='字数上限，默认 300')
    args = ap.parse_args()

    files = sorted(glob.glob(os.path.join(args.slides_dir, '*.slide')))
    if not files:
        print('未找到 .slide 文件：', args.slides_dir)
        sys.exit(1)

    problems = []
    for path in files:
        with open(path, 'r', encoding='utf-8') as f:
            text = f.read()
        notes = extract_notes(text)
        name = os.path.basename(path)
        if notes is None:
            if args.require:
                problems.append(f'{name}: 缺少讲稿 notes 属性')
            continue
        n = len(notes.strip())
        if n > args.limit:
            problems.append(f'{name}: 讲稿 {n} 字，超过 {args.limit} 字')
        if '讲稿：' in notes or '讲稿:' in notes:
            problems.append(f'{name}: 残留"讲稿："前缀，应删除')
        for phrase in BANNED:
            if phrase in notes:
                problems.append(f'{name}: 含禁用元描述「{phrase}」')

    if problems:
        print('讲稿校验未通过：')
        for p in problems:
            print('  -', p)
        sys.exit(1)
    print(f'讲稿校验通过：{len(files)} 页')
    sys.exit(0)


if __name__ == '__main__':
    main()
