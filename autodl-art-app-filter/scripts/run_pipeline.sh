#!/usr/bin/env bash
# 命令用法备忘（避免忘记顺序）。本文件不会自动执行任何动作：要跑哪一步，去掉对应行的 # 即可。
#
# cd "<本 skill 目录>/scripts"
# export PYTHONIOENCODING=utf-8        # Windows 中文输出乱码时才需要
# PY="python"                         # 或写本机真实 Python 解释器路径

# 0. 体检（只读）
# "$PY" inspect_api.py
# "$PY" check_proxy.py

# 1. 抓应用广场（默认排序，全部应用 + 标签树）
# "$PY" fetch_apps.py
# "$PY" fetch_apps.py 100 45          # 只抓标签 id=45（语音）

# 2. 抓详情：全部 243 个（两阶段深判需要 README 全文，建议一次抓全）；可断点续抓
# "$PY" fetch_details.py 0            # 约 4~10 分钟，逐行输出进度

# 3. 两阶段筛选（阶段1 列表字段粗筛 30 个 → 阶段2 README 全文深判出前 N）
# "$PY" select_apps.py "文生视频，3090 能跑" 5
# "$PY" select_apps.py "中文 TTS 语音克隆" 8

# 4. 生成筛选报告（导出过封面的话，报告排名表会直接显示封面图）
# "$PY" report.py "文生视频，3090 能跑"

# 5. 可选：分层导出封面/头像/README/媒体到 outputs/apps/<id>_<name>/（断点续跑，限速）
# "$PY" export_apps.py                # 全部
# "$PY" export_apps.py 10             # 只导前 10 个

printf '%s\n' "这是命令备忘，不会自动执行。请编辑本文件或按 README 的顺序逐条运行。"
