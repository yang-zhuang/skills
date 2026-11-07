"""分析 instance_full.html 中 gpuTips 容器与已关机行的标记结构。"""
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

html = Path(__file__).with_name("instance_full.html").read_text(encoding="utf-8")

print("gpuTips 出现次数:", html.count("gpuTips"))
print("'空闲' 出现次数:", html.count("空闲"))
print("'可开' 出现次数:", html.count("可开"))
print("'资源' 出现次数:", html.count("资源"))

# 每个 gpuTips 周边 600 字符
for i, m in enumerate(re.finditer(r"gpuTips", html)):
    s = max(0, m.start() - 200)
    seg = re.sub(r"\s+", " ", html[s : m.start() + 500])
    print(f"\n=== gpuTips #{i} ===")
    print(seg)

# 已关机行 status 区域的完整结构（找 v-if 占位）
print("\n=== 已关机 status 完整片段 ===")
m = re.search(r"已关机", html)
if m:
    s = max(0, m.start() - 300)
    print(re.sub(r"\s+", " ", html[s : m.start() + 900]))
