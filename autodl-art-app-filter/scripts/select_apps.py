"""两阶段筛选：阶段1 列表字段粗筛，阶段2 对入选者用 README 全文深判。

用法：
    python select_apps.py                        # 默认需求"视频生成"，前 5 个
    python select_apps.py "文生视频，3090 能跑" 5
    python select_apps.py "中文 TTS 语音克隆" 8

流程：
    阶段1（粗筛）：全部候选只发列表字段（无 README），大模型选出 COARSE_TOP_N 个
    阶段2（深判）：只对入选者发 README 全文（截到 DEEP_README_LIMIT 字），逐条核对需求条件，出前 N 个
两个阶段各自的 system prompt 在 prompts/coarse_system.md 与 prompts/filter_system.md。

依赖：本地 agnes-key-pool 代理（python check_proxy.py 先验证）。
产物：outputs/coarse_<需求>.json（粗筛中间结果）、outputs/selection_<需求>.json（最终排序）、
      outputs/llm_raw_coarse_<需求>.txt / outputs/llm_raw_deep_<需求>.txt（大模型原文，排错用）。
下一步：python report.py "<需求>" 生成可读的筛选报告 shortlist_<需求>.md。
"""

import json
import re
import sys
import time
import urllib.request
from datetime import datetime

import config
import prompt as prompt_module

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def safe_name(text):
    return re.sub(r'[\\/:*?"<>|\s]+', "_", text).strip("_") or "query"


def load_candidates(out_dir):
    """优先用详情文件，缺的用列表字段补齐，保持默认排序。"""
    apps_path = out_dir / "apps.json"
    if not apps_path.exists():
        print("找不到 %s，请先运行 python fetch_apps.py" % apps_path, file=sys.stderr)
        sys.exit(2)
    apps = json.loads(apps_path.read_text(encoding="utf-8"))["list"]

    details_path = out_dir / "app_details.json"
    details = {}
    if details_path.exists():
        details = json.loads(details_path.read_text(encoding="utf-8"))["apps"]

    merged = []
    for app in apps:
        app_id = str(app["id"])
        merged.append((app_id, details.get(app_id) or app))
    return merged


def call_llm(system_prompt, user_prompt):
    headers = {"Content-Type": "application/json"}
    if config.LLM_API_KEY and config.LLM_API_KEY != "any":
        headers["Authorization"] = "Bearer " + config.LLM_API_KEY
    body = {
        "model": config.LLM_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.2,
        "max_tokens": 3000,
    }
    req = urllib.request.Request(
        config.LLM_BASE_URL + "/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=config.LLM_TIMEOUT) as resp:
        result = json.loads(resp.read().decode("utf-8", errors="replace"))
    return result["choices"][0]["message"]["content"]


def parse_llm_json(raw):
    """从大模型原文里抠出 JSON 对象，容忍 ```json 包裹和前后废话。"""
    text = raw.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("大模型回复里找不到 JSON 对象")
    data = json.loads(text[start:end + 1])
    if not isinstance(data.get("selected"), list):
        raise ValueError("JSON 缺少 selected 数组")
    return data


def main():
    query = sys.argv[1] if len(sys.argv) > 1 else "视频生成"
    top_n = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    top_n = max(1, min(top_n, 20))
    coarse_n = max(top_n, config.COARSE_TOP_N)
    slug = safe_name(query)
    out_dir = config.ensure_output_dir()

    # 0. 载入候选 + 陈旧闸门
    merged = load_candidates(out_dir)
    stale, alive = [], []
    for app_id, source in merged:
        summary = prompt_module.build_app_summary(source, readme_limit=0, version_limit=0)
        age = summary.get("update_age_days")
        if age is not None and age > config.STALE_DAYS:
            stale.append(summary)
        else:
            alive.append((app_id, source, summary))
    if stale:
        print("陈旧闸门：%d 个应用超过 %d 天未更新，已剔除：" % (len(stale), config.STALE_DAYS), flush=True)
        for item in stale:
            print("  id=%s %s（%s，%s 天）" % (
                item["application_id"], item["name"], item["updated_at"], item["update_age_days"]), flush=True)
    if not alive:
        print("没有候选应用，先跑 fetch_apps.py；或调大 AUTODL_ART_STALE_DAYS", file=sys.stderr)
        sys.exit(2)
    detail_count = sum(1 for _, source, _ in alive if source.get("readme") is not None)
    print("候选应用：%d 个（原始 %d 个，含 README 详情 %d 个）；需求：%s；深判前 %d 个" % (
        len(alive), len(merged), detail_count, query, top_n), flush=True)

    # ── 阶段 1：列表字段粗筛 ──
    coarse_summaries = [s for _, _, s in alive]
    coarse_prompt = prompt_module.build_user_prompt(query, coarse_summaries, coarse_n)
    print("\n[阶段1/2] 粗筛：%d 个应用、只发列表字段（%d 字提示词），请模型选出 %d 个..." % (
        len(coarse_summaries), len(coarse_prompt), coarse_n), flush=True)
    started = time.monotonic()
    try:
        coarse_raw = call_llm(prompt_module.COARSE_SYSTEM_PROMPT, coarse_prompt)
    except Exception as exc:
        print("粗筛大模型调用失败：%s。先跑 python check_proxy.py 排查。" % exc, file=sys.stderr)
        sys.exit(3)
    print("[阶段1] 大模型返回（%.1f 秒，%d 字）" % (time.monotonic() - started, len(coarse_raw)), flush=True)
    (out_dir / ("llm_raw_coarse_" + slug + ".txt")).write_text(coarse_raw, encoding="utf-8")
    try:
        coarse_parsed = parse_llm_json(coarse_raw)
    except Exception as exc:
        print("解析粗筛 JSON 失败：%s；原文已存 llm_raw_coarse_%s.txt" % (exc, slug), file=sys.stderr)
        sys.exit(4)

    by_id = {app_id: (source, summary) for app_id, source, summary in alive}
    coarse_ids, coarse_seen = [], set()
    for item in coarse_parsed["selected"]:
        app_id = str(item.get("application_id", ""))
        if app_id in by_id and app_id not in coarse_seen:
            coarse_seen.add(app_id)
            coarse_ids.append(app_id)
    if len(coarse_ids) < top_n:
        print("粗筛只给出 %d 个有效 id（要求 %d），不足部分按收藏数兜底补齐" % (
            len(coarse_ids), coarse_n), flush=True)
        for app_id, _, summary in sorted(alive, key=lambda x: x[2].get("star_num") or 0, reverse=True):
            if app_id not in coarse_seen:
                coarse_seen.add(app_id)
                coarse_ids.append(app_id)
            if len(coarse_ids) >= coarse_n:
                break

    coarse_result = {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "query": query,
        "model": config.LLM_MODEL,
        "coarse_n": coarse_n,
        "candidate_count": len(alive),
        "summary": str(coarse_parsed.get("summary") or ""),
        "selected": [{
            "application_id": int(app_id),
            "name": by_id[app_id][1]["name"],
            "reason": str(item.get("reason") or ""),
        } for item, app_id in zip(coarse_parsed["selected"], coarse_ids) if app_id in coarse_seen] or [
            {"application_id": int(app_id), "name": by_id[app_id][1]["name"], "reason": "粗筛结果不足，按收藏数兜底"}
            for app_id in coarse_ids
        ],
    }
    coarse_path = out_dir / ("coarse_" + slug + ".json")
    coarse_path.write_text(json.dumps(coarse_result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("[阶段1] 粗筛结果 %d 个已保存：%s" % (len(coarse_ids), coarse_path), flush=True)

    # ── 阶段 2：README 全文深判 ──
    no_detail = [app_id for app_id in coarse_ids if by_id[app_id][0].get("readme") is None]
    if no_detail:
        print("[阶段2] 警告：%d 个粗筛入选应用没有 README 详情，只有列表字段；"
              "要更准请跑 python fetch_details.py 0 后重跑本脚本。" % len(no_detail), flush=True)
    deep_summaries = [
        prompt_module.build_app_summary(by_id[app_id][0], readme_limit=config.DEEP_README_LIMIT)
        for app_id in coarse_ids
    ]
    deep_prompt = prompt_module.build_user_prompt(query, deep_summaries, top_n)
    print("\n[阶段2/2] 深判：%d 个应用、README 截到 %d 字（%d 字提示词），请模型选出前 %d 个..." % (
        len(deep_summaries), config.DEEP_README_LIMIT, len(deep_prompt), top_n), flush=True)
    started = time.monotonic()
    try:
        deep_raw = call_llm(prompt_module.SYSTEM_PROMPT, deep_prompt)
    except Exception as exc:
        print("深判大模型调用失败：%s。先跑 python check_proxy.py 排查。" % exc, file=sys.stderr)
        sys.exit(3)
    print("[阶段2] 大模型返回（%.1f 秒，%d 字）" % (time.monotonic() - started, len(deep_raw)), flush=True)
    (out_dir / ("llm_raw_deep_" + slug + ".txt")).write_text(deep_raw, encoding="utf-8")
    try:
        deep_parsed = parse_llm_json(deep_raw)
    except Exception as exc:
        print("解析深判 JSON 失败：%s；原文已存 llm_raw_deep_%s.txt" % (exc, slug), file=sys.stderr)
        sys.exit(4)

    selected, seen = [], set()
    for item in deep_parsed["selected"]:
        app_id = str(item.get("application_id", ""))
        if app_id not in coarse_seen or app_id in seen:
            continue
        seen.add(app_id)
        source = by_id[app_id][1]
        selected.append({
            "application_id": int(app_id),
            "name": item.get("name") or source["name"],
            "score": float(item.get("score") or 0),
            "reason": str(item.get("reason") or ""),
            "fallback": False,
        })
    if len(selected) < top_n:  # 深判给不足时按粗筛顺序兜底
        for app_id in coarse_ids:
            if app_id in seen:
                continue
            seen.add(app_id)
            selected.append({
                "application_id": int(app_id),
                "name": by_id[app_id][1]["name"],
                "score": 0.0,
                "reason": "深判未给足候选，按粗筛顺序兜底",
                "fallback": True,
            })
            if len(selected) >= top_n:
                break

    result = {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "query": query,
        "model": config.LLM_MODEL,
        "stale_days": config.STALE_DAYS,
        "candidate_count": len(alive),
        "coarse_count": len(coarse_ids),
        "excluded_stale": [{
            "application_id": s["application_id"],
            "name": s["name"],
            "updated_at": s["updated_at"],
            "update_age_days": s["update_age_days"],
        } for s in stale],
        "summary": str(deep_parsed.get("summary") or ""),
        "selected": selected,
    }
    path = out_dir / ("selection_" + slug + ".json")
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n排序结果：")
    for index, item in enumerate(selected, 1):
        print("%d. [%s分] id=%s %s —— %s" % (
            index, item["score"], item["application_id"], item["name"], item["reason"]), flush=True)
    print("\n整体说明：%s" % result["summary"], flush=True)
    print("候选已保存：%s" % path, flush=True)
    print("下一步：python report.py \"%s\"（生成可读的筛选报告）" % query, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
