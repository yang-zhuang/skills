"""分析 instance_full.html：找出真实实例行（el-table row）的精确结构与状态/按钮位置。"""
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

html = Path(__file__).with_name("instance_full.html").read_text(encoding="utf-8")
print("HTML 大小:", len(html))

# 1) 找出所有 el-table__row 出现次数
for pat in ["el-table__row", "已关机", "运行中", ">开机<", ">关机<", "instance", "table"]:
    print(f"{pat!r:20} 出现 {html.count(pat)} 次")

# 2) 截取每个「已关机」周边 1500 字符，观察行结构
idxs = [m.start() for m in re.finditer("已关机", html)]
print("\n=== 已关机 出现位置:", len(idxs), "处，取第一处周边 ===")
if idxs:
    s = max(0, idxs[0] - 1200)
    seg = html[s : idxs[0] + 400]
    # 压缩空白便于阅读
    seg = re.sub(r"\s+", " ", seg)
    print(seg)

print("\n=== 运行中 第一处周边 ===")
idxs2 = [m.start() for m in re.finditer("运行中", html)]
if idxs2:
    s = max(0, idxs2[0] - 1200)
    seg = re.sub(r"\s+", " ", html[s : idxs2[0] + 400])
    print(seg)
