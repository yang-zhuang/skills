"""意见分档与累积升级。

评估 agent 给出的每条意见，修改 agent 必须归入三档之一：
  fix_now    立即修改
  wont_fix   完全不需要改（写清理由，以后不再提）
  defer      暂时不改（通常是「改了会牵连更多」，或影响面小）

defer 不是免死金牌：同一类问题（`kind`）在不同文章里反复出现，
说明它是系统性缺陷而非个案 —— 累计到阈值就强制升级为 fix_now。
这个累积判定由脚本做，因为它是纯计数，让 LLM 每次重新权衡只会得出不同结论。

状态落在 assets/decisions.json，跨文章、跨轮次持续存在。
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # scripts → 根
STATE = ROOT / "work" / "decisions.json"

# 同一类问题累计出现到这个次数即强制升级为 fix_now
ESCALATE_AFTER = 3

VALID_STATUS = ("fix_now", "wont_fix", "defer")


def fingerprint(kind: str, where: str) -> str:
    """同类问题在不同文章里必须落到同一条记录上，否则累积永远归零。

    指纹只认 kind + where（不认正文描述）：描述的措辞每次都不同，
    拿它算 hash 等于把累计次数重置。
    """
    raw = f"{kind.strip().lower()}|{where.strip().lower()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def load() -> dict:
    if not STATE.exists():
        return {"config": {"escalate_after": ESCALATE_AFTER}, "entries": {}}
    return json.loads(STATE.read_text(encoding="utf-8"))


def save(state: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sync(issues: list[dict], article: str) -> tuple[dict, list[dict]]:
    """把本轮意见并入状态池，返回（状态, 每条意见的裁决）。

    裁决 = 记录里已定的档位；若该档位是 defer 但累计次数已达阈值，
    则强制改成 fix_now —— 这就是「出现多次样本时也需要修改」的落地。
    """
    state = load()
    today = date.today().isoformat()
    escalate_after = state.get("config", {}).get("escalate_after", ESCALATE_AFTER)
    verdicts: list[dict] = []

    for issue in issues:
        kind = issue.get("kind", "其他").strip() or "其他"
        where = issue.get("where", "").strip()
        fp = fingerprint(kind, where)
        entry = state["entries"].get(fp)

        if entry is None:
            entry = {
                "kind": kind,
                "where": where,
                "summary": issue.get("detail", ""),
                "status": "defer",  # 默认暂不改，等 AI 明确表态
                "seen": 0,
                "first_seen": today,
                "last_seen": today,
                "articles": [],
                "note": "",
            }
            state["entries"][fp] = entry

        entry["last_seen"] = today
        # 同一篇文章重跑不该重复计数 —— 「多个样本」指的是多篇文章，不是多次运行
        new_article = bool(article) and article not in entry["articles"]
        if article and new_article:
            entry["articles"].append(article)
        if new_article or not article:
            entry["seen"] += 1

        status = entry["status"]
        escalated = False
        if status == "defer" and entry["seen"] >= escalate_after:
            status = "fix_now"
            escalated = True
            entry["note"] = (
                f"累计出现 {entry['seen']} 次（阈值 {escalate_after}），"
                f"已从 defer 强制升级为 fix_now：跨文章反复出现说明是系统性缺陷"
            )

        verdicts.append({
            "fingerprint": fp,
            "kind": kind,
            "where": where,
            "detail": issue.get("detail", ""),
            "suggestion": issue.get("suggestion", ""),
            "status": status,
            "seen": entry["seen"],
            "escalated": escalated,
            "note": entry["note"],
        })

    return state, verdicts


def main() -> int:
    import sys

    ap = argparse.ArgumentParser(description="评估意见分档：fix_now / wont_fix / defer（含累积升级）")
    ap.add_argument("issues", nargs="?", default="", help="评估 agent 产出的 issues.json（--mark 表态时可省略）")
    ap.add_argument("--article", default="", help="当前文章名，用于统计跨文章出现次数")
    ap.add_argument("--mark", help="对某条意见表态：指纹或 kind")
    ap.add_argument("--status", choices=VALID_STATUS, help="与 --mark 配合使用")
    ap.add_argument("--note", default="", help="与 --mark 配合：表态理由，会存档")
    ap.add_argument("--json", action="store_true", help="输出裁决 JSON 而非表格")
    args = ap.parse_args()

    if args.mark:
        if not args.status:
            print("--mark 必须配 --status", file=sys.stderr)
            return 2
        state = load()
        fp = args.mark
        if fp not in state["entries"]:
            hits = [
                k for k, v in state["entries"].items()
                if v["kind"].lower() == args.mark.lower() or k.startswith(args.mark)
            ]
            if len(hits) != 1:
                print(f"指纹 {args.mark} 未命中唯一记录（候选 {len(hits)} 条）", file=sys.stderr)
                return 2
            fp = hits[0]
        entry = state["entries"][fp]
        if entry["status"] == "wont_fix" and args.status != "wont_fix":
            print(
                f"该条已标记为 wont_fix（理由：{entry['note']}）。"
                f"要推翻请先显式 --status wont_fix 覆盖理由，或手工编辑 {STATE}",
                file=sys.stderr,
            )
            return 1
        entry["status"] = args.status
        entry["note"] = args.note
        save(state)
        print(f"{fp} → {args.status}（累计出现 {entry['seen']} 次）")
        return 0

    issues_file = Path(args.issues)
    if not issues_file.exists():
        print(f"找不到意见文件：{issues_file}", file=sys.stderr)
        return 2
    payload = json.loads(issues_file.read_text(encoding="utf-8"))
    issues = payload.get("issues", []) if isinstance(payload, dict) else payload
    article = args.article or (payload.get("article", "") if isinstance(payload, dict) else "")

    state, verdicts = sync(issues, article)
    save(state)

    if args.json:
        print(json.dumps(verdicts, ensure_ascii=False, indent=2))
        return 0

    buckets = {s: [v for v in verdicts if v["status"] == s] for s in VALID_STATUS}
    print(f"本轮意见 {len(verdicts)} 条 → "
          f"立即修改 {len(buckets['fix_now'])} / 暂不改 {len(buckets['defer'])} / 不需要 {len(buckets['wont_fix'])}")
    for status in VALID_STATUS:
        if not buckets[status]:
            continue
        print(f"\n[{status}]")
        for v in buckets[status]:
            mark = "  ⚠ 累计升级" if v["escalated"] else ""
            print(f"  {v['fingerprint']}  ×{v['seen']}  {v['kind']} @ {v['where'] or '—'}{mark}")
            print(f"      {v['detail']}")
            if v["suggestion"]:
                print(f"      → 建议：{v['suggestion']}")
            if v["note"]:
                print(f"      note: {v['note']}")
    print(f"\n状态：{STATE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
