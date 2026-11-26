#!/usr/bin/env bash
# smoke test（25 步）—— 先跑这个验证配置能跑通，再跑 train.sh 全量。
# 打开就是完整命令，没有任何变量。
# 用法（Windows 调 WSL）：wsl -e bash -lc "bash /mnt/d/你的目录/smoke.sh"
# 换成你自己的：python 路径、train.py 路径、config.yaml 路径、输出目录。

/home/你的用户/Code/sft-case/.venv/bin/python /mnt/d/你的目录/train.py \
  --config /mnt/d/你的目录/config.yaml \
  --max-steps 25 \
  --logging-steps 5 \
  --save-steps 1000 \
  --output-dir /mnt/d/你的目录/outputs/smoke
