"""按顺序抓取 AutoDL Art 应用广场列表（默认排序），零登录。

用法：
    python fetch_apps.py                    # 默认每页 100 条，不过滤标签
    python fetch_apps.py 30                 # 只抓第一页前 30 条
    python fetch_apps.py 100 45             # 按标签 id 45（语音）过滤

产物：outputs/apps.json、outputs/tags.json
"""

import json
import sys
from datetime import datetime

import api_client
import config

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def load_tag_tree(out_dir):
    """抓标签树，保存扁平化的 id→名称 映射，供按标签过滤时查 id。"""
    result = api_client.get(config.API_TAG_TREE)
    groups = result.get("data") or []
    flat = []
    for group in groups:
        for second in group.get("children") or []:
            for third in second.get("children") or []:
                flat.append({
                    "id": third.get("id"),
                    "name": third.get("name"),
                    "parent": second.get("name"),
                    "group": group.get("name"),
                })
    path = out_dir / "tags.json"
    path.write_text(json.dumps({"fetched_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                                "tree": groups, "flat": flat}, ensure_ascii=False, indent=2), encoding="utf-8")
    print("标签树已保存：%s（%d 个末级标签）" % (path, len(flat)), flush=True)
    return flat


def main():
    page_size = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    tag_id = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    page_size = max(1, min(page_size, 100))

    out_dir = config.ensure_output_dir()
    tags = load_tag_tree(out_dir)
    if tag_id:
        hit = [t for t in tags if t["id"] == tag_id]
        print("按标签过滤：%s" % (hit[0]["name"] if hit else "id=%d（未在标签树中找到）" % tag_id), flush=True)

    all_apps = []
    page = 1
    max_page = 1
    total = 0
    while page <= max_page:
        payload = {"page_index": page, "page_size": page_size, "name": "", "unified_tag_ids": [tag_id] if tag_id else []}
        result = api_client.post(config.API_APPLICATION, payload)
        data = result.get("data") or {}
        batch = data.get("list") or []
        if page == 1:
            total = data.get("result_total") or 0
            max_page = data.get("max_page") or 1
            print("应用总数：%d，共 %d 页，每页 %d 条" % (total, max_page, page_size), flush=True)
        all_apps.extend(batch)
        print("[page %d/%d] 抓到 %d 条，累计 %d 条" % (page, max_page, len(batch), len(all_apps)), flush=True)
        if not batch or len(all_apps) >= total:
            break
        page += 1

    out = {
        "fetched_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": config.BASE_URL + "/app/market",
        "tag_id": tag_id,
        "page_size": page_size,
        "result_total": total,
        "list": all_apps,
    }
    path = out_dir / "apps.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print("完成：%d 条应用已写入 %s" % (len(all_apps), path), flush=True)


if __name__ == "__main__":
    main()
