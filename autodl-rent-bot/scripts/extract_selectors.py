import re
from pathlib import Path

# DOM 快照与本脚本同目录；换机器后重新导出即可
html = (Path(__file__).parent / "market_full.html").read_text(encoding="utf-8")

def ctx(kw, n=220, limit=4):
    print(f"\n########## 关键词: {kw} ##########")
    cnt = 0
    for m in re.finditer(re.escape(kw), html):
        s = max(0, m.start() - n)
        e = min(len(html), m.end() + n)
        seg = html[s:e]
        print("----")
        print(seg)
        cnt += 1
        if cnt >= limit:
            break

# 计费方式
ctx("按量计费", 120, 2)
# GPU 数量
ctx("GPU数量", 200, 2)
# 可租按钮
ctx("可租", 160, 3)
