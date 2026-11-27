"""按列表顺序逐个抓应用详情（介绍、README、标签、全部版本），零登录、可断点续抓。

用法：
    python fetch_details.py            # 抓前 30 个应用
    python fetch_details.py 243        # 抓全部（约 4~10 分钟，每行实时输出进度）
    python fetch_details.py 0          # 同上，0 表示全部

产物：outputs/app_details.json（已抓过的 id 会跳过，重复运行不会重复请求）
"""

import json
import sys
import time
from datetime import datetime

import api_client
import config

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    out_dir = config.ensure_output_dir()
    apps_path = out_dir / "apps.json"
    if not apps_path.exists():
        print("找不到 %s，请先运行 python fetch_apps.py" % apps_path, file=sys.stderr)
        sys.exit(2)

    apps = json.loads(apps_path.read_text(encoding="utf-8"))["list"]
    if limit > 0:
        apps = apps[:limit]
    total = len(apps)

    details_path = out_dir / "app_details.json"
    if details_path.exists():
        saved = json.loads(details_path.read_text(encoding="utf-8"))
    else:
        saved = {"fetched_at": None, "apps": {}}
    store = saved["apps"]

    todo = [a for a in apps if str(a["id"]) not in store]
    print("待抓 %d 个 / 共 %d 个（已有 %d 个跳过）" % (len(todo), total, total - len(todo)), flush=True)
    started = time.monotonic()
    done = 0
    for app in todo:
        app_id = app["id"]
        try:
            result = api_client.post(config.API_APPLICATION_DETAIL, {"application_id": app_id})
            detail = result.get("data") or {}
            store[str(app_id)] = detail
            done += 1
            elapsed = time.monotonic() - started
            eta = elapsed / done * (len(todo) - done)
            versions = detail.get("version_list") or []
            latest = detail.get("latest_version") or "-"
            print("[%d/%d] id=%s %s | 最新 v%s | %d 个版本 | 收藏 %s | 预计剩余 %.1f 分钟" % (
                done, len(todo), app_id, detail.get("name", "")[:22], latest,
                len(versions), detail.get("star_num", "-"), eta / 60), flush=True)
        except Exception as exc:
            print("[%d/%d] id=%s 失败：%s（本条跳过，可重跑本脚本续抓）" % (
                done + 1, len(todo), app_id, exc), flush=True)
        saved["fetched_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        details_path.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")

    print("完成：%d 个详情已写入 %s" % (len(store), details_path), flush=True)


if __name__ == "__main__":
    main()
