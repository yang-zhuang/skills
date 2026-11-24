"""把 select_apps.py 的排序结果渲染成一份可读的 Markdown 筛选报告。

用法：
    python report.py "文生视频，3090 能跑"
    python report.py            # 默认读 selection_视频生成.json

产物：outputs/shortlist_<需求>.md —— 排名表 + 整体说明 + 被陈旧闸门剔除的名单。
只读本地文件，不发任何请求。
"""

import json
import sys
from pathlib import Path

import config
from select_apps import safe_name

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BADGE_MARK = {"精": "⭐精", "热": "🔥热", "无": ""}


def cover_mark(out_dir, app_id):
    """找 outputs/apps/<id>_*/cover.* 本地封面；存在返回相对路径图片引用，否则返回 —。"""
    apps_root = out_dir / "apps"
    if not apps_root.exists():
        return "—"
    for folder in apps_root.glob("%d_*" % app_id):
        for cover in folder.glob("cover.*"):
            return "![封面](apps/%s/%s)" % (folder.name, cover.name)
    return "—"


def load_meta(out_dir):
    """id -> (badge, updated_at, download_num, describe)；详情优先，列表兜底。"""
    meta = {}
    apps_path = out_dir / "apps.json"
    if apps_path.exists():
        for app in json.loads(apps_path.read_text(encoding="utf-8"))["list"]:
            meta[str(app["id"])] = app
    details_path = out_dir / "app_details.json"
    if details_path.exists():
        for app_id, app in json.loads(details_path.read_text(encoding="utf-8"))["apps"].items():
            meta[app_id] = app  # 详情字段更全，覆盖列表
    return meta


def main():
    query = sys.argv[1] if len(sys.argv) > 1 else "视频生成"
    slug = safe_name(query)
    out_dir = config.ensure_output_dir()
    selection_path = out_dir / ("selection_" + slug + ".json")
    if not selection_path.exists():
        sys.exit("找不到 %s，先跑 python select_apps.py \"%s\"" % (selection_path, query))
    result = json.loads(selection_path.read_text(encoding="utf-8"))
    meta = load_meta(out_dir)

    import prompt as prompt_module
    rows = []
    for rank, item in enumerate(result["selected"], 1):
        src = meta.get(str(item["application_id"]), {})
        summary = prompt_module.build_app_summary(src)
        rows.append({
            "rank": rank,
            "score": item["score"],
            "id": item["application_id"],
            "name": item["name"],
            "badge": summary["badge"],
            "updated": (summary["updated_at"] or "")[:10],
            "age": summary["update_age_days"],
            "download": summary["download_num"],
            "star": summary["star_num"],
            "author": summary["author"],
            "reason": item["reason"],
            "fallback": item.get("fallback"),
            "cover": cover_mark(out_dir, item["application_id"]),
        })

    lines = [
        "# AutoDL Art 应用筛选报告",
        "",
        "- **需求**：%s" % result["query"],
        "- **时间**：%s（模型 %s，陈旧闸门 %d 天，粗筛 %s 个 → 深判前 %d）" % (
            result["created_at"], result["model"], result["stale_days"],
            result.get("coarse_count", "?"), len(result["selected"])),
        "- **候选池**：%d 个（详情覆盖率见下方说明）" % result["candidate_count"],
        "",
        "## 排名",
        "",
        "| # | 分 | 封面 | 应用 | 精热 | 更新 | 下载 | 收藏 | 作者 | 理由 |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        badge = BADGE_MARK.get(r["badge"], "")
        age_note = "（%d 天）" % r["age"] if r["age"] is not None else ""
        fallback = "（兜底，非模型判断）" if r["fallback"] else ""
        lines.append("| %d | %.1f | %s | id=%d %s | %s | %s%s | %s | %s | %s | %s%s |" % (
            r["rank"], r["score"], r["cover"], r["id"], r["name"], badge,
            r["updated"], age_note,
            "{:,}".format(r["download"] or 0), "{:,}".format(r["star"] or 0),
            r["author"] or "", r["reason"], fallback))

    lines += ["", "## 整体说明", "", result["summary"] or "（模型未给出）"]

    if not (out_dir / "apps").exists():
        lines += ["", "> 💡 报告里没有封面。跑 `python export_apps.py` 导出封面/README/媒体后重新生成本报告即可看到。"]

    if result.get("excluded_stale"):
        lines += ["", "## 被陈旧闸门剔除（超过 %d 天未更新）" % result["stale_days"], ""]
        for item in result["excluded_stale"]:
            lines.append("- id=%s %s（%s，%d 天）" % (
                item["application_id"], item["name"], item["updated_at"][:10], item["update_age_days"]))

    details_path = out_dir / "app_details.json"
    detail_count = len(json.loads(details_path.read_text(encoding="utf-8"))["apps"]) if details_path.exists() else 0
    if detail_count < result["candidate_count"]:
        lines += ["", "> ⚠️ 详情覆盖率 %d/%d：未覆盖的应用只有列表字段、没有 README 证据，排序依据较弱。" % (
            detail_count, result["candidate_count"])]

    path = out_dir / ("shortlist_" + slug + ".md")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("报告已生成：%s" % path, flush=True)
    for r in rows:
        print("  %d. [%.1f分] id=%d %s（%s，%s，下载 %s）" % (
            r["rank"], r["score"], r["id"], r["name"], r["badge"] or "无",
            r["updated"], "{:,}".format(r["download"] or 0)), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
