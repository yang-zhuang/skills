"""按预设标题让大模型从抓取结果里挑前 N 个应用，人工终选留给用户。

用法：
    python select_apps.py                 # 标题“视频生成”，前 5 个
    python select_apps.py "视频生成" 5
    python select_apps.py "图像生成" 8

依赖：本地 agnes-key-pool 代理（python check_proxy.py 先验证）。
产物：outputs/selection_<标题>.json、outputs/llm_raw_<标题>.txt（大模型原文，排错用）
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
    return re.sub(r'[\\/:*?"<>|]', "_", text).strip() or "untitled"


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

    summaries = []
    for app in apps:
        app_id = str(app["id"])
        source = details.get(app_id) or app
        summaries.append(prompt_module.build_app_summary(source))
    return summaries


def call_llm(user_prompt):
    headers = {"Content-Type": "application/json"}
    if config.LLM_API_KEY and config.LLM_API_KEY != "any":
        headers["Authorization"] = "Bearer " + config.LLM_API_KEY
    body = {
        "model": config.LLM_MODEL,
        "messages": [
            {"role": "system", "content": prompt_module.SYSTEM_PROMPT},
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
    title = sys.argv[1] if len(sys.argv) > 1 else "视频生成"
    top_n = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    top_n = max(1, min(top_n, 20))

    out_dir = config.ensure_output_dir()
    all_summaries = load_candidates(out_dir)
    stale = [s for s in all_summaries
             if s.get("update_age_days") is not None and s["update_age_days"] > config.STALE_DAYS]
    summaries = [s for s in all_summaries
                 if not (s.get("update_age_days") is not None and s["update_age_days"] > config.STALE_DAYS)]
    if stale:
        print("陈旧闸门：%d 个应用超过 %d 天未更新，已剔除：" % (len(stale), config.STALE_DAYS), flush=True)
        for item in stale:
            print("  id=%s %s（%s，%s 天）" % (
                item["application_id"], item["name"], item["updated_at"], item["update_age_days"]), flush=True)
    print("候选应用：%d 个（原始 %d 个）；标题：%s；选取前 %d 个" % (
        len(summaries), len(all_summaries), title, top_n), flush=True)
    details_path = out_dir / "app_details.json"
    detail_count = len(json.loads(details_path.read_text(encoding="utf-8"))["apps"]) if details_path.exists() else 0
    if detail_count < len(all_summaries):
        print("提示：详情覆盖率 %d/%d，其余 %d 个只有列表字段、没有 README 证据；"
              "要更准请跑 python fetch_details.py 0 后重跑本脚本。" % (
                  detail_count, len(all_summaries), len(all_summaries) - detail_count), flush=True)
    if not summaries:
        print("没有候选应用，先跑 fetch_apps.py；或调大 AUTODL_ART_STALE_DAYS", file=sys.stderr)
        sys.exit(2)

    user_prompt = prompt_module.build_user_prompt(title, summaries, top_n)
    print("正在请求大模型 %s（%d 字提示词）..." % (config.LLM_MODEL, len(user_prompt)), flush=True)
    started = time.monotonic()
    try:
        raw = call_llm(user_prompt)
    except Exception as exc:
        print("大模型调用失败：%s。先跑 python check_proxy.py 排查。" % exc, file=sys.stderr)
        sys.exit(3)
    print("大模型返回（%.1f 秒，%d 字）" % (time.monotonic() - started, len(raw)), flush=True)

    slug = safe_name(title)
    (out_dir / ("llm_raw_" + slug + ".txt")).write_text(raw, encoding="utf-8")
    try:
        parsed = parse_llm_json(raw)
    except Exception as exc:
        print("解析大模型 JSON 失败：%s；原文已存 llm_raw_%s.txt" % (exc, slug), file=sys.stderr)
        sys.exit(4)

    by_id = {str(s["application_id"]): s for s in summaries}
    selected = []
    seen = set()
    for item in parsed["selected"]:
        app_id = str(item.get("application_id", ""))
        if app_id not in by_id or app_id in seen:
            continue
        seen.add(app_id)
        source = by_id[app_id]
        selected.append({
            "application_id": int(app_id),
            "name": item.get("name") or source["name"],
            "score": float(item.get("score") or 0),
            "reason": str(item.get("reason") or ""),
            "fallback": False,
        })

    if len(selected) < top_n:
        for source in sorted(summaries, key=lambda s: s.get("star_num") or 0, reverse=True):
            app_id = str(source["application_id"])
            if app_id in seen:
                continue
            selected.append({
                "application_id": int(app_id),
                "name": source["name"],
                "score": 0.0,
                "reason": "大模型未给足候选，按收藏数兜底",
                "fallback": True,
            })
            seen.add(app_id)
            if len(selected) >= top_n:
                break

    result = {
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "title": title,
        "model": config.LLM_MODEL,
        "stale_days": config.STALE_DAYS,
        "candidate_count": len(summaries),
        "excluded_stale": [{
            "application_id": s["application_id"],
            "name": s["name"],
            "updated_at": s["updated_at"],
            "update_age_days": s["update_age_days"],
        } for s in stale],
        "summary": str(parsed.get("summary") or ""),
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
    print("下一步：python choose_final.py \"%s\"（由你决定最终选哪个）" % title, flush=True)


if __name__ == "__main__":
    main()
