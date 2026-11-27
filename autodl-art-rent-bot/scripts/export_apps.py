"""把 outputs/app_details.json 的应用数据导出成「一个应用一个文件夹」的分层结构。

产物结构（全部在 outputs/apps/ 下，不入库）：
    outputs/apps/
      252_MINIMAX-H3提速500高画质/
        info.json          应用全部字段（含 version_list 里的开机命令、镜像版本等；readme 为原文）
        readme.md          本地化 README：图片链接改写为本地 media/ 相对路径，离线可直接看
        cover.jpg          封面（logo 字段，保留原扩展名）
        avatar.png         作者头像
        media/
          01_xxx.png       README 里的图片，按出现顺序编号，文件名保留 URL 尾部特征防重名
          ...

说明：
    * B 站/夸克等外链视频不下载（需要登录/转码），链接原样保留在 readme 和 info.json 里；
    * 已下载过的文件跳过（按文件存在判断），可断点续跑；
    * 限速：请求间隔 1.0~2.5 秒随机；逐行输出进度与预计剩余时间；
    * 图片下载失败不中断：在文件夹里写 media/_failed.json 记录，继续其余应用。

用法：
    python export_apps.py          # 导出 app_details.json 里全部应用（默认）
    python export_apps.py 10       # 只导前 10 个
"""

import json
import random
import re
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

import config

MEDIA_RE = re.compile(r'!\[[^\]]*\]\(\s*((?:https?:)?//[^)\s]+)\s*\)')
BAD_CHARS = re.compile(r'[\\/:*?"<>|\r\n\t]+')


def sanitize(name, max_len=60):
    name = BAD_CHARS.sub("_", str(name)).strip(" ._")
    return (name or "unnamed")[:max_len]


def fetch_bytes(url, timeout=30):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0",
        "Referer": config.BASE_URL + "/",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def normalize_url(url):
    """补全协议相对链接（//xxx → https://xxx）；无 scheme 又不以 // 开头的视为脏数据返回 None。"""
    url = str(url or "").strip()
    if url.startswith("//"):
        return "https:" + url
    if url.startswith(("http://", "https://")):
        return url
    return None


def download_to(url, dest, log):
    """下载到 dest；成功返回 True。失败写日志返回 False，不抛异常。"""
    url = normalize_url(url)
    if not url:
        log("    跳过无效 URL（字段本身不完整）：%s" % str(dest.name))
        return False
    if dest.exists() and dest.stat().st_size > 0:
        return True  # 断点续跑
    for attempt in (1, 2):
        try:
            data = fetch_bytes(url)
            dest.write_bytes(data)
            time.sleep(random.uniform(config.MIN_DELAY, config.MAX_DELAY))
            return True
        except Exception as e:
            log("    下载失败（第 %d 次）%s：%s" % (attempt, url[:100], e))
            time.sleep(2)
    return False


def ext_of(url, default=".png"):
    m = re.search(r"\.(png|jpe?g|gif|webp|bmp|mp4|webm)(?:[?#].*)?$", url, re.I)
    return ("." + m.group(1).lower().replace("jpeg", ".jpg").replace("jpg", "jpg")) if m else default


def localize_readme(readme, media_map):
    """把 README 里的 markdown 图片链接替换成本地 media/ 路径。"""
    def repl(m):
        url = m.group(1)
        local = media_map.get(url)
        return "![%s](%s)" % (Path(local).name if local else "media", local) if local else m.group(0)
    return MEDIA_RE.sub(repl, readme)


def export_one(app, out_root, log):
    folder = out_root / ("%d_%s" % (app["id"], sanitize(app.get("name"))))
    folder.mkdir(parents=True, exist_ok=True)
    media_dir = folder / "media"
    media_dir.mkdir(exist_ok=True)

    # 1. info.json（全字段原文）
    (folder / "info.json").write_text(
        json.dumps(app, ensure_ascii=False, indent=2), encoding="utf-8")

    # 2. 封面 + 作者头像
    for field, filename in (("logo", "cover"), ("avatar", "avatar")):
        url = app.get(field)
        if url:
            ok = download_to(url, folder / (filename + ext_of(url)), log)
            if not ok:
                log("  [%s] %s 下载失败，跳过" % (app["id"], field))

    # 3. README：收集图片 URL → 按顺序编号下载 → 改写成本地路径
    readme = app.get("readme") or ""
    urls = MEDIA_RE.findall(readme)
    media_map, failed = {}, []
    seen = {}
    for i, url in enumerate(urls, 1):
        key = seen.setdefault(url, len(seen) + 1)
        ext = ext_of(url)
        base = sanitize(Path(url).name or "image", 50)
        if not base.lower().endswith(ext):  # 名字里已带扩展名就不重复加
            base += ext
        fname = "%02d_%s" % (key, base)
        if download_to(url, media_dir / fname, log):
            media_map[url] = "media/" + fname
        else:
            failed.append(url)
    if failed:
        (media_dir / "_failed.json").write_text(
            json.dumps(failed, ensure_ascii=False, indent=2), encoding="utf-8")

    (folder / "readme.md").write_text(localize_readme(readme, media_map), encoding="utf-8")
    return len(urls) - len(failed), len(urls)


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 0  # 0 = 全部
    details_path = config.ensure_output_dir() / "app_details.json"
    if not details_path.exists():
        sys.exit("缺少 outputs/app_details.json，先跑 fetch_details.py")
    apps = json.loads(details_path.read_text(encoding="utf-8"))["apps"]
    items = list(apps.values())
    if limit > 0:
        items = items[:limit]

    out_root = config.ensure_output_dir() / "apps"
    out_root.mkdir(exist_ok=True)
    print("导出 %d 个应用到 %s" % (len(items), out_root), flush=True)

    t0 = time.time()
    total_img = ok_img = 0
    for i, app in enumerate(items, 1):
        log = lambda msg: print(msg, flush=True)
        print("[%d/%d] %d_%s" % (i, len(items), app["id"], app.get("name")), flush=True)
        ok, total = export_one(app, out_root, log)
        ok_img += ok
        total_img += total
        left = len(items) - i
        per = (time.time() - t0) / i
        eta_min = per * left / 60
        print("    完成：图片 %d/%d；预计剩余 %.1f 分钟" % (ok, total, eta_min), flush=True)

    print("\n全部完成：用时 %.1f 分钟；README 图片成功 %d/%d；外链视频（B站等）不下载，链接保留在 readme.md/info.json"
          % ((time.time() - t0) / 60, ok_img, total_img), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
