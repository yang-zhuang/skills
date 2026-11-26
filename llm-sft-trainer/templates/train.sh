#!/usr/bin/env bash
# 全量训练 —— 打开就是完整命令，没有任何变量、没有 case 分支。
# 跑之前先用 smoke.sh 验证配置能跑通。
# 用法（Windows 调 WSL）：wsl -e bash -lc "bash /mnt/d/Code/_deliver_sft/train.sh"
# 换成你自己的：python 路径、train.py 路径、config.yaml 路径、输出目录。

/home/yangzhuang/Code/sft-case/.venv/bin/python /mnt/d/Code/_deliver_sft/train.py \
  --config /mnt/d/Code/_deliver_sft/config.yaml \
  --output-dir /mnt/d/Code/_deliver_sft/outputs/full
