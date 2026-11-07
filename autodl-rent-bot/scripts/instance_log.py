"""实例操作日志：把机器人开机的/租到的实例元数据落盘（instances_log.json）。

用途：后续可以据此指定关闭哪台机器（配合 shutdown_instance.py）。
只记录实例元数据（ID/地区/规格/时间），不含任何账号凭证。
"""
import json
import time
from pathlib import Path

LOG_PATH = Path(__file__).with_name("instances_log.json")


def record_event(event: str, data: dict) -> None:
    """追加一条实例事件记录。data 里的值必须是可 JSON 序列化的（不能含 Locator）。"""
    entry = {"time": time.strftime("%Y-%m-%d %H:%M:%S"), "event": event, **data}
    try:
        records = json.loads(LOG_PATH.read_text(encoding="utf-8")) if LOG_PATH.exists() else []
    except Exception:
        records = []
    records.append(entry)
    LOG_PATH.write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        f"[instance_log] 已记录 {event}: {data.get('region', '')} "
        f"{data.get('id', '') or data.get('detail', '')}"
    )
