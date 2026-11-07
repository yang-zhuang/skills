# -*- coding: utf-8 -*-
"""定向修改 .env：忽略实例库 + 只租 vGPU-48GB-350W。只打印改动摘要，不回显敏感行。"""
import io
from pathlib import Path

# 与同组 set_gpu_model_env.py / set_instance_env.py 保持一致：相对本脚本定位 .env，
# 换机器 / clone 到任意目录都能正确改到当前项目的 .env（上游这里曾硬编码本机绝对路径）。
PATH = Path(__file__).with_name(".env")

with open(PATH, "r", encoding="utf-8") as f:
    lines = f.read().splitlines()

changed = []
out = []
seen_gpu = False
seen_reuse = False
for line in lines:
    if line.startswith("AUTODL_GPU_MODEL="):
        out.append("AUTODL_GPU_MODEL=vGPU-48g-350w")
        changed.append(f"AUTODL_GPU_MODEL: {line.split('=',1)[1]!r} -> 'vGPU-48g-350w'")
        seen_gpu = True
    elif line.startswith("AUTODL_REUSE_INSTANCES="):
        out.append("AUTODL_REUSE_INSTANCES=false")
        changed.append(f"AUTODL_REUSE_INSTANCES: {line.split('=',1)[1]!r} -> 'false'")
        seen_reuse = True
    else:
        out.append(line)

if not seen_gpu:
    out.append("AUTODL_GPU_MODEL=vGPU-48g-350w")
    changed.append("AUTODL_GPU_MODEL: <缺失> -> 新增 'vGPU-48g-350w'")
if not seen_reuse:
    out.append("AUTODL_REUSE_INSTANCES=false")
    changed.append("AUTODL_REUSE_INSTANCES: <缺失> -> 新增 'false'")

with open(PATH, "w", encoding="utf-8") as f:
    f.write("\n".join(out) + "\n")

for c in changed:
    print(c)
print("DONE")
