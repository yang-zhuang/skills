"""往 .env 追加/更新实例管控配置（只回显目标键，不回显其他敏感内容）。"""
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

env_path = Path(__file__).with_name(".env")
text = env_path.read_text(encoding="utf-8")

targets = {
    "AUTODL_MAX_RUNNING_INSTANCES": "2",
    "AUTODL_AUTO_POWER_ON": "true",
}

for key, value in targets.items():
    pattern = rf"(?m)^{key}=.*$"
    if re.search(pattern, text):
        text = re.sub(pattern, f"{key}={value}", text)
        print(f"UPDATED {key}={value}")
    else:
        if not text.endswith("\n"):
            text += "\n"
        text += f"{key}={value}\n"
        print(f"APPENDED {key}={value}")

env_path.write_text(text, encoding="utf-8")
