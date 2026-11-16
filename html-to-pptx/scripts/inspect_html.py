#!/usr/bin/env python3
"""把 HTML 幻灯片解析成结构化 JSON，供逐页重建 PPT 时使用。

只用标准库。解析内容：
  - 每页 <section class="slide..."> 的顺序、class
  - 标题（h1 / h2）、eyebrow
  - 讲稿（class="notes" 的文本）
  - 图片引用（img 的 src / alt）
  - 表格数量、文件树（class="tree"）文本

用法:
    python inspect_html.py <input.html> [-o out.json] [--pretty]

输出 JSON 到 stdout（或 -o 指定文件）。每页含 index/title/eyebrow/notes/
images/tables/tree 字段。讲稿已去掉开头的「讲稿：」前缀。
"""

import argparse
import json
import re
import sys
from html.parser import HTMLParser


class SlideParser(HTMLParser):
    """轻量解析器：按 section.slide 分节，抓取关心的片段。"""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.slides = []
        self._cur = None
        self._stack = []          # 标签栈，用于判断当前文本归属
        self._buf = []            # 当前文本缓冲（标题/讲稿）
        self._capture = None      # 正在捕获的字段: 'notes'/'tree'/'title'/'eyebrow'

    @staticmethod
    def _classes(attrs):
        for k, v in attrs:
            if k == 'class' and v:
                return v.split()
        return []

    def handle_starttag(self, tag, attrs):
        classes = self._classes(attrs)
        if tag == 'section' and 'slide' in classes:
            self._cur = {
                'index': len(self.slides),
                'classes': classes,
                'title': '',
                'eyebrow': '',
                'notes': '',
                'images': [],
                'tables': 0,
                'tree': '',
            }
            self.slides.append(self._cur)
            return
        if self._cur is None:
            return

        if tag == 'img':
            src = alt = ''
            for k, v in attrs:
                if k == 'src':
                    src = v or ''
                elif k == 'alt':
                    alt = v or ''
            self._cur['images'].append({'src': src, 'alt': alt})
        elif tag == 'table':
            self._cur['tables'] += 1
        elif tag in ('h1', 'h2') and not self._cur['title']:
            self._capture, self._buf = 'title', []
        elif 'eyebrow' in classes:
            self._capture, self._buf = 'eyebrow', []
        elif 'notes' in classes:
            self._capture, self._buf = 'notes', []
        elif 'tree' in classes:
            self._capture, self._buf = 'tree', []

    def handle_endtag(self, tag):
        if self._cur is None:
            return
        # 捕获结束：标题 h1/h2，或任意容器标签收尾时收 notes/tree/eyebrow
        if self._capture in ('title',) and tag in ('h1', 'h2'):
            self._cur['title'] = re.sub(r'\s+', ' ', ''.join(self._buf)).strip()
            self._capture, self._buf = None, []
        elif self._capture in ('notes', 'tree', 'eyebrow') and tag in ('div', 'section', 'p'):
            self._flush_capture()
        if tag == 'section':
            self._flush_capture()
            self._cur = None

    def _flush_capture(self):
        if not self._capture or self._cur is None:
            return
        text = ''.join(self._buf)
        if self._capture == 'tree':
            # 目录树按行保留，去掉每行首尾空白、丢掉空行
            lines = [ln.strip() for ln in text.splitlines()]
            text = '\n'.join(ln for ln in lines if ln)
        else:
            # 讲稿 / 标题 / eyebrow：把换行折叠成空格
            text = re.sub(r'\s*\n\s*', ' ', text)
            text = re.sub(r'[ \t]{2,}', ' ', text).strip()
            if self._capture == 'notes':
                text = re.sub(r'^讲稿[:：]\s*', '', text)
        self._cur[self._capture] = text
        self._capture, self._buf = None, []

    def handle_data(self, data):
        if self._cur is not None and self._capture:
            self._buf.append(data)

    def handle_startendtag(self, tag, attrs):
        # <br/> 在讲稿里表示分段
        if self._cur is not None and self._capture == 'notes' and tag == 'br':
            self._buf.append('\n')


def main():
    ap = argparse.ArgumentParser(description='解析 HTML 幻灯片为结构化 JSON')
    ap.add_argument('html', help='输入 HTML 文件路径')
    ap.add_argument('-o', '--out', help='输出 JSON 文件（默认 stdout）')
    ap.add_argument('--pretty', action='store_true', help='缩进美化输出')
    args = ap.parse_args()

    with open(args.html, 'r', encoding='utf-8') as f:
        content = f.read()

    parser = SlideParser()
    parser.feed(content)
    data = {
        'source': args.html,
        'slide_count': len(parser.slides),
        'slides': parser.slides,
    }
    text = json.dumps(data, ensure_ascii=False, indent=2 if args.pretty else None)
    if args.out:
        with open(args.out, 'w', encoding='utf-8') as f:
            f.write(text)
        print(f'已写出 {args.out}：{len(parser.slides)} 页', file=sys.stderr)
    else:
        print(text)


if __name__ == '__main__':
    main()
