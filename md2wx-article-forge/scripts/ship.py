"""提交草稿箱。支持 --dry-run 预演。

用法:
    python ship.py <built.html> --title "标题" --cover cover.jpg
    python ship.py <built.html> --title "标题" --cover cover.jpg --dry-run
    python ship.py --check        # 只做凭据自检
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import wechat_api                       # noqa: E402
from render import make_digest          # noqa: E402


def preflight(html: str, title: str, cover: Path | None) -> dict:
    """发布前本地检查。只读，无任何远端副作用。"""
    checks = []

    checks.append({
        "name": "标题非空",
        "ok": bool(title.strip()),
        "detail": f"{len(title)} 字（上限 32）" if title.strip() else "未提供",
    })
    checks.append({
        "name": "标题长度",
        "ok": len(title) <= 32,
        "detail": f"{len(title)}/32",
    })
    checks.append({
        "name": "封面图存在",
        "ok": cover is not None and cover.exists(),
        "detail": str(cover) if cover else "未提供 —— news 类型必填，缺则 40007",
    })
    if cover and cover.exists():
        checks.append({
            "name": "封面为 JPG",
            "ok": cover.suffix.lower() in (".jpg", ".jpeg"),
            "detail": f"{cover.suffix}（微信硬性要求 JPG）",
        })
        checks.append({
            "name": "封面体积",
            "ok": cover.stat().st_size <= 2 * 1024 * 1024,
            "detail": f"{cover.stat().st_size} 字节（上限 2MB）",
        })
        w = wechat_api._image_width(cover)
        checks.append({
            "name": "封面宽度",
            "ok": w is None or w >= 900,
            "detail": f"{w}px（不足 900 会报 53401）" if w else "无法读取宽度",
        })

    checks.append({
        "name": "正文字符数",
        "ok": len(html) <= 20_000,
        "detail": f"{len(html)}/20000",
    })
    checks.append({
        "name": "正文体积",
        "ok": len(html.encode("utf-8")) <= 1024 * 1024,
        "detail": f"{len(html.encode('utf-8'))} 字节（上限 1MB）",
    })

    local_imgs = [u for u in __import__("re").findall(r'src="([^"]+)"', html)
                  if not u.startswith(("http://", "https://", "mmbiz"))]
    checks.append({
        "name": "图片已上微信服务器",
        "ok": not local_imgs,
        "detail": f"仍有本地路径: {local_imgs}" if local_imgs else "全部为远端链接",
    })

    return {
        "ok": all(c["ok"] for c in checks),
        "checks": checks,
        "env": wechat_api.check_config(),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("html", nargs="?", help="排版好的 html")
    ap.add_argument("--title", default="", help="标题，≤32 字")
    ap.add_argument("--cover", help="封面 JPG 路径")
    ap.add_argument("--author", default="", help="作者，≤16 字")
    ap.add_argument("--digest", default="", help="摘要，≤120 字，留空则自动截取")
    ap.add_argument("--source-url", default="", help="阅读原文链接，≤1KB")
    ap.add_argument("--dry-run", action="store_true", help="只做发布前检查，不调用接口")
    ap.add_argument("--check", action="store_true", help="只做凭据自检")
    args = ap.parse_args()

    if args.check:
        print(json.dumps(wechat_api.check_config(), ensure_ascii=False, indent=2))
        return 0

    if not args.html:
        ap.print_help()
        return 1

    html = Path(args.html).read_text(encoding="utf-8")
    cover = Path(args.cover) if args.cover else None
    digest = args.digest or make_digest(html)

    report = preflight(html, args.title, cover)
    print(json.dumps(report, ensure_ascii=False, indent=2))

    if args.dry_run:
        print("\n[dry-run] 未调用任何接口")
        return 0 if report["ok"] else 2
    if not report["ok"]:
        print("\n前置检查未通过，已中止。")
        return 2
    if not report["env"].get("token_ok"):
        print(f"\n凭据不可用: {report['env'].get('hint')}")
        return 3

    thumb = wechat_api.upload_thumb(cover)
    result = wechat_api.add_draft(
        title=args.title,
        content=html,
        thumb_media_id=thumb,
        author=args.author,
        digest=digest,
        content_source_url=args.source_url,
    )
    print(json.dumps({"thumb_media_id": thumb, "draft": result},
                     ensure_ascii=False, indent=2))
    print("\n已写入草稿箱。请到公众号后台用手机预览确认后再群发。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())