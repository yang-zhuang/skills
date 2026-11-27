"""只读探查四个匿名接口，页面/接口改版后先跑这个确认字段结构。

用法：
    python inspect_api.py

产物：outputs/probe/{apps,detail,tags,week}.json
"""

import json
import sys
from datetime import datetime

import api_client
import config

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def save(probe_dir, name, data):
    path = probe_dir / (name + ".json")
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print("  -> %s" % path, flush=True)


def main():
    probe_dir = config.ensure_output_dir() / "probe"
    probe_dir.mkdir(parents=True, exist_ok=True)

    print("1) 应用列表 POST %s" % config.API_APPLICATION, flush=True)
    apps = api_client.post(config.API_APPLICATION, {"page_index": 1, "page_size": 3, "name": "", "unified_tag_ids": []})
    save(probe_dir, "apps", apps)
    first = ((apps.get("data") or {}).get("list") or [{}])[0]
    print("   总数=%s 首条 id=%s name=%s" % ((apps.get("data") or {}).get("result_total"), first.get("id"), first.get("name")), flush=True)

    print("2) 应用详情 POST %s" % config.API_APPLICATION_DETAIL, flush=True)
    detail = api_client.post(config.API_APPLICATION_DETAIL, {"application_id": first.get("id")})
    save(probe_dir, "detail", detail)
    print("   字段=%s" % list((detail.get("data") or {}).keys()), flush=True)

    print("3) 标签树 GET %s" % config.API_TAG_TREE, flush=True)
    tags = api_client.get(config.API_TAG_TREE)
    save(probe_dir, "tags", tags)
    print("   一级分类=%d" % len(tags.get("data") or []), flush=True)

    print("4) 周榜 POST %s" % config.API_WEEK, flush=True)
    week = api_client.post(config.API_WEEK, {})
    save(probe_dir, "week", week)
    print("   条数=%d" % len(week.get("data") or []), flush=True)

    print("探查完成：%s" % datetime.now().astimezone().isoformat(timespec="seconds"), flush=True)


if __name__ == "__main__":
    main()
