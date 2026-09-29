# -*- coding: utf-8 -*-
"""
env_file.py — 把仓库根的 .env 读进 os.environ。

为什么要单独一个模块（而不是每个入口各写一遍）:
    main.py / check_env.py / verify_split.py 都是**独立入口**，都可能在没有系统
    环境变量、配置只写在 .env 里的情况下运行。而 ODA_EXE / SHX_FONT_DIRS
    是**模块 import 时**就取的值 —— 读晚了等于没读。

    三处各写一遍读取逻辑，迟早漂移（比如只有一处支持剥引号），而这类漂移的
    表现是"某条命令突然找不到 ODA 了"，极难排查。所以收敛成这一个函数。
"""
import os
from pathlib import Path


def load_env(path=None):
    """把 .env 的 KEY=VALUE 行读进 os.environ。

    已存在的环境变量**不覆盖** —— 系统环境变量优先，这样临时改一个值不用动文件。

    path 默认取本模块同目录的 .env。现场作业里 .env 被 init_task.py 复制到了
    与脚本同一个目录（06_work/），所以默认值在任何位置都成立。
    """
    path = Path(path) if path else Path(__file__).with_name(".env")
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
