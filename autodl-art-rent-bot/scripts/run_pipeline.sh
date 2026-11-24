#!/usr/bin/env bash
# 命令用法备忘（避免忘记顺序）。本文件不会自动执行任何动作：要跑哪一步，去掉对应行的 # 即可。
# 在 scripts/ 目录下执行：bash run_pipeline.sh   # 只显示这份备忘
#
# cd "<本 skill 目录>/scripts"
# export PYTHONIOENCODING=utf-8        # Windows 中文输出乱码时才需要
# PY="python"                         # 或写本机真实 Python 解释器路径

# 0. 体检（只读）
# "$PY" inspect_api.py
# "$PY" check_proxy.py

# 1. 抓应用广场（默认排序，243 条 + 标签树）
# "$PY" fetch_apps.py
# "$PY" fetch_apps.py 100 45          # 只抓标签 id=45（语音）

# 2. 抓详情：先 30 个试用，确认无误再全抓；可断点续抓
# "$PY" fetch_details.py 30
# "$PY" fetch_details.py 0            # 全部 243 个，约 4~10 分钟，逐行输出进度

# 2.5 分层导出（一应用一文件夹：封面/头像/README本地化/媒体图片）
# "$PY" export_apps.py                # 导出 app_details.json 里全部应用
# "$PY" export_apps.py 10             # 只导前 10 个

# 3. 大模型择优（本地 agnes-key-pool 代理）
# "$PY" select_apps.py "视频生成" 5
# "$PY" select_apps.py "图像生成" 8

# 4. 人工终选（终端输入 ID）
# "$PY" choose_final.py "视频生成"

# 5. 网页 JWT 提取（一次即可；之后脚本全自动读 profile）
# "$PY" extract_jwt.py --login                        # 第一次：弹窗口登录一次
# "$PY" extract_jwt.py                                # 之后：无头自动刷新 JWT

# 6. 开发者 token 部署（需要包根 .env 里的 AUTODL_DEV_TOKEN）
# "$PY" dev_check.py                                  # 验证 token + 列实例（只读）
# "$PY" select_gpu.py                                 # 规格 ID 对照
# "$PY" deploy.py --app-id 273 --gpu 5090-p --gpu-amount 1 --dry-run   # 只打印，不花钱
# "$PY" deploy.py --app-id 273 --gpu 5090-p --gpu-amount 1 --yes       # 真正创建（花钱）
# "$PY" grab.py stock 西北B区,北京B区                  # 看库存行情（只读，需网页 JWT）
# "$PY" grab.py list                                                  # 先看实例和状态
# "$PY" grab.py power <instance_uuid> --interval 60 --hours 12 --yes   # 抢开机（已关机实例）
# "$PY" grab.py create --app-id 273 --gpu 5090-p --interval 60 --hours 12 --yes            # 盲试抢创建
# "$PY" grab.py create --app-id 273 --gpu 5090-p,pro6000-p --region 西北B区,北京B区 --signal --hours 12 --yes   # 有货才点（推荐）
# "$PY" power_ctl.py list                             # 看实例
# "$PY" power_ctl.py status <instance_uuid>
# "$PY" power_ctl.py off <instance_uuid>
# "$PY" power_ctl.py release <instance_uuid> --yes    # 销毁，不可逆

printf '%s\n' "这是命令备忘，不会自动执行。请编辑本文件或按 README 的顺序逐条运行。"
