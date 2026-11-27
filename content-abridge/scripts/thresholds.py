r"""thresholds.py -- 长文档判定阈值的唯一事实来源。

为什么单独一个文件:
    `build_task_pack.py`（据它决定 mode=short/long）与 `execute_trim.py`（据它决定
    达标即停）必须**同口径**。此前两处各自硬编码 `30_000`——改一处漏一处就会出现
    "pack 判为 long 先走 trim、execute_trim 却认为已达标直接放行"的静默不一致。

    不能简单地让 build_task_pack 去 import execute_trim：后者模块级 `import yaml`，
    会连带把 pyyaml 变成打包环节的硬依赖（当前打包用裸解释器即可跑）。
"""
THRESHOLD_CHARS = 30_000
