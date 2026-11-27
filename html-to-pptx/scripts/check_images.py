#!/usr/bin/env python3
"""核对图片素材：尺寸、宽高比、文件大小，并给出在 1280x720 PPT 中的安全显示尺寸建议。

依赖 Pillow（缺失时降级为只报文件大小，不会中断）。

用法:
    python check_images.py <图片目录> [--max-w 560] [--max-h 420]

输出表格到 stdout；发现超小图（宽或高 < 400px）时以 WARN 标注——这类图放大到
PPT 里会糊，需要换高清原图。
"""

import argparse
import os
import sys

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


def fmt_size(n):
    return f'{n / 1024:.0f} KB' if n < 1024 * 1024 else f'{n / 1024 / 1024:.2f} MB'


def main():
    ap = argparse.ArgumentParser(description='核对图片素材尺寸与比例')
    ap.add_argument('directory', help='图片目录')
    ap.add_argument('--max-w', type=int, default=560, help='显示区最大宽度（px），默认 560')
    ap.add_argument('--max-h', type=int, default=420, help='显示区最大高度（px），默认 420')
    args = ap.parse_args()

    if not HAS_PIL:
        print('WARN: 未安装 Pillow，仅报告文件大小；建议 pip install pillow', file=sys.stderr)

    exts = ('.png', '.jpg', '.jpeg', '.webp', '.bmp', '.gif')
    files = sorted(
        f for f in os.listdir(args.directory)
        if f.lower().endswith(exts)
    )
    if not files:
        print('目录中没有图片：', args.directory)
        return

    warn_count = 0
    print(f'{"文件":<28}{"尺寸":<14}{"比例":<8}{"大小":<12}{"建议显示 (contain)":<18}备注')
    print('-' * 100)
    for name in files:
        path = os.path.join(args.directory, name)
        size = os.path.getsize(path)
        note = ''
        if HAS_PIL:
            with Image.open(path) as im:
                w, h = im.size
            ratio = w / h
            # contain：等比缩放到不超过显示框
            scale = min(args.max_w / w, args.max_h / h, 1.0)
            disp = f'{int(w * scale)}x{int(h * scale)}'
            if w < 400 or h < 400:
                note = 'WARN 图偏小，放大易糊'
                warn_count += 1
            print(f'{name:<28}{f"{w}x{h}":<14}{ratio:<8.2f}{fmt_size(size):<12}{disp:<18}{note}')
        else:
            print(f'{name:<28}{"-":<14}{"-":<8}{fmt_size(size):<12}{"-":<18}')
    print('-' * 100)
    if warn_count:
        print(f'共 {warn_count} 张图偏小，建议换高清原图后再写入 PPT。')


if __name__ == '__main__':
    main()
