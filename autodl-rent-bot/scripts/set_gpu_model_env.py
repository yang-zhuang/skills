"""把 .env 里的 AUTODL_GPU_MODEL 替换为多型号优先级（只回显该行，不回显其他敏感内容）。"""
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

env_path = Path(__file__).with_name(".env")
text = env_path.read_text(encoding="utf-8")

NEW = "AUTODL_GPU_MODEL=vGPU-48GB-350W,vGPU-32GB"

if "AUTODL_GPU_MODEL=" in text:
    new_text, n = re.subn(r"(?m)^AUTODL_GPU_MODEL=.*$", NEW, text)
    if n != 1:
        print(f"FAIL: 匹配到 {n} 行 AUTODL_GPU_MODEL，预期 1 行，未修改")
        sys.exit(1)
    env_path.write_text(new_text, encoding="utf-8")
    print("REPLACED_OK")
else:
    # 原文件没有该配置行，追加
    if not text.endswith("\n"):
        text += "\n"
    env_path.write_text(text + NEW + "\n", encoding="utf-8")
    print("APPENDED_OK")

# 只回显 GPU_MODEL 相关行做验证
for line in env_path.read_text(encoding="utf-8").splitlines():
    if line.startswith("AUTODL_GPU_MODEL="):
        print(line)
