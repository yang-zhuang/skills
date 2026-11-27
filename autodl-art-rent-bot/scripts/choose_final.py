"""人工终选：打印大模型排序结果，让用户输入最终选择的 ID。

用法：
    python choose_final.py                 # 自动挑 outputs 里最新的 selection_*.json
    python choose_final.py "视频生成"      # 指定标题对应的候选文件

产物：outputs/final_choice.json
"""

import json
import sys
from datetime import datetime

import config

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def main():
    out_dir = config.ensure_output_dir()
    if len(sys.argv) > 1:
        title = sys.argv[1]
        safe = "".join("_" if c in '\\/:*?"<>|' else c for c in title).strip()
        path = out_dir / ("selection_" + safe + ".json")
    else:
        files = sorted(out_dir.glob("selection_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        if not files:
            print("没有候选文件，请先跑 python select_apps.py", file=sys.stderr)
            sys.exit(2)
        path = files[0]

    if not path.exists():
        print("找不到候选文件：%s" % path, file=sys.stderr)
        sys.exit(2)
    data = json.loads(path.read_text(encoding="utf-8"))

    print("预设标题：%s（%s）" % (data["title"], path.name), flush=True)
    for index, item in enumerate(data["selected"], 1):
        print("%d. id=%s | %s | %s分 | %s" % (
            index, item["application_id"], item["name"], item["score"], item["reason"]), flush=True)
    print("大模型说明：%s" % data.get("summary", ""), flush=True)

    raw = input("\n请输入最终选择的应用 ID（直接回车取消）：").strip()
    if not raw:
        print("已取消，未写入 final_choice.json", flush=True)
        return
    hit = next((item for item in data["selected"] if str(item["application_id"]) == raw), None)
    if hit is None:
        print("ID %s 不在候选列表里，未写入。请重新运行并输入列表中的 ID。" % raw, file=sys.stderr)
        sys.exit(3)

    choice = {
        "chosen_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "title": data["title"],
        "from_file": str(path),
        "application_id": hit["application_id"],
        "name": hit["name"],
        "note": "仅完成人工选择；未执行 GPU 选择、登录或部署。",
    }
    final_path = out_dir / "final_choice.json"
    final_path.write_text(json.dumps(choice, ensure_ascii=False, indent=2), encoding="utf-8")
    print("已记录你的选择：%s（id=%s）" % (hit["name"], hit["application_id"]), flush=True)
    print("产物：%s" % final_path, flush=True)


if __name__ == "__main__":
    main()
