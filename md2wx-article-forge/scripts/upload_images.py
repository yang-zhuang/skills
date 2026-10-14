#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""把 built.html 里的本地图片批量上传为微信 mmbiz 链接并就地替换 src。

为什么必须这一步：公众号正文只显示 mmbiz.qpic.cn 域名的图片，
本地路径和第三方 CDN 一律裂图。封面走另一条通道（material/add_material），
本脚本只管正文图。

用法：
    python scripts/upload_images.py work/<篇名>/2_built.html             # 上传并改写
    python scripts/upload_images.py work/<篇名>/2_built.html --dry-run   # 只列清单不调接口
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

IMG_SRC_RE = re.compile(r'(<img[^>]*?\ssrc=")([^"]+)(")', re.I)
MMbiz = "mmbiz.qpic.cn"


def collect(html: str) -> list[str]:
    """取出所有 img src，去重并保持出现顺序。"""
    seen, out = set(), []
    for m in IMG_SRC_RE.finditer(html):
        src = m.group(2)
        if src not in seen:
            seen.add(src)
            out.append(src)
    return out


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    dry = "--dry-run" in sys.argv
    if not args:
        print("用法：python scripts/upload_images.py <built.html> [--dry-run]", file=sys.stderr)
        return 2

    html_path = Path(args[0])
    if not html_path.exists():
        print(f"找不到文件：{html_path}", file=sys.stderr)
        return 2

    html = html_path.read_text(encoding="utf-8")
    srcs = collect(html)

    uploaded, skipped, missing = [], [], []
    for src in srcs:
        if src.startswith("http"):
            # 已是网络图：只放行微信自家的，第三方图在公众号里同样不显示
            (skipped if MMbiz in src else missing).append(src)
            continue
        p = (html_path.parent / src) if not Path(src).is_absolute() else Path(src)
        if not p.exists():
            missing.append(src)
            continue
        if dry:
            skipped.append(f"{src}  ({p.stat().st_size} 字节)")
            continue
        try:
            import wechat_api
            url = wechat_api.upload_inline_image(p)
        except Exception as e:  # 凭据缺失、超限、接口报错都在这里汇总，不静默吞掉
            print(f"上传失败 {src}：{e}", file=sys.stderr)
            return 1
        html = html.replace(f'src="{src}"', f'src="{url}"')
        uploaded.append({"local": src, "url": url})

    if uploaded:
        html_path.write_text(html, encoding="utf-8")

    report = {
        "html": str(html_path),
        "dry_run": dry,
        "uploaded": uploaded,
        "skipped": skipped,
        "missing": missing,
        "ok": not missing,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if missing:
        print(f"\n有 {len(missing)} 张图无法处理（本地缺失或第三方外链），"
              f"发布到公众号会裂图，先修 md 再重跑渲染。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
