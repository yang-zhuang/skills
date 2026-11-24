"""大模型筛选用的提示词装配与摘要构造。

system prompt 全文在 prompts/ 下（Markdown，自包含，改筛选标准只改它们）：
  coarse_system.md  阶段1 粗筛（只有列表字段，宁多勿漏）
  filter_system.md  阶段2 深判（README 全文逐条核对需求条件）
本文件只负责加载 prompt 和把应用字段压成紧凑摘要。
"""

import json
from datetime import datetime, timezone

import config
from prompts import load_prompt

SYSTEM_PROMPT = load_prompt("filter_system.md")
COARSE_SYSTEM_PROMPT = load_prompt("coarse_system.md")


def parse_age_days(value):
    """把 ISO 时间字符串转成距今天数；解析不了返回 None。"""
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(value)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        return max(0, (datetime.now(timezone.utc) - moment).days)
    except (ValueError, TypeError):
        return None


def app_updated_at(app):
    """返回 (更新时间, 来源)。UI 用的是应用级 updated_at；列表没有它时从版本时间推算。"""
    if app.get("updated_at"):
        return app["updated_at"], "应用更新时间"
    versions = app.get("version_list") or []
    newest = max((v.get("updated_at") or "" for v in versions), default="")
    return newest or None, "版本时间推算" if newest else "未知"


def badge_of(popular_type):
    """前端 app-item.js：excellent 显示“精”，hot 显示“热”。"""
    return {"excellent": "精", "hot": "热"}.get(popular_type or "", "无")


def build_app_summary(app, readme_limit=None, version_limit=3):
    """把详情页字段压成适合发给大模型的紧凑摘要。

    readme_limit=None 时跳过 readme 键；readme_limit=0 或负数视为无 README。
    version_limit=0 时跳过 versions 键（粗筛阶段用，省 token）。
    """
    versions = []
    for version in (app.get("version_list") or [])[:version_limit]:
        versions.append({
            "version": version.get("image_version"),
            "framework": version.get("framework"),
            "cuda": version.get("cuda_v"),
            "start_command": version.get("start_command"),
            "image_size_gb": round((version.get("image_size") or 0) / 1024 ** 3, 1),
            "describe": (version.get("describe") or "")[:200],
        })
    tags = [t.get("tag_name") or t.get("name") for t in (app.get("tag_list") or [])]
    updated_at, _ = app_updated_at(app)
    age_days = parse_age_days(updated_at)
    summary = {
        "application_id": app.get("id"),
        "name": app.get("name"),
        "author": app.get("nickname") or app.get("username"),
        "describe": app.get("describe"),
        "author_note": app.get("author_note"),
        "tags": tags,
        "badge": badge_of(app.get("popular_type")),
        "latest_version": app.get("latest_version"),
        "updated_at": updated_at,
        "update_age_days": age_days,
        "star_num": app.get("star_num"),
        "download_num": app.get("download_num"),
        "use_num": app.get("use_num"),
        "runtime": app.get("runtime"),
    }
    if version_limit > 0:
        summary["versions"] = versions
    if readme_limit is not None and readme_limit > 0:
        summary["readme"] = (app.get("readme") or "")[:readme_limit]
    return summary


def build_user_prompt(query, summaries, top_n):
    return "筛选需求：%s\n请选出前 %d 个应用。\n\n候选应用资料（JSON）：\n%s" % (
        query, top_n, json.dumps(summaries, ensure_ascii=False))
