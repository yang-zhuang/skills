import re
from pathlib import Path

# DOM 快照与本脚本同目录；换机器后重新导出即可
html = (Path(__file__).parent / "create_page_full.html").read_text(encoding="utf-8")

# 1) 级联菜单数量与每级节点
menus = re.findall(r'<div class="el-cascader-menu"[^>]*>(.*?)</div>\s*</div>\s*</div>', html, re.S)
print(f"检测到 el-cascader-menu 数量(近似): {len(menus)}")

# 更稳妥：按 data-title 抽取各级选项标签
titles = re.findall(r'data-title="([^"]+)"[^>]*>([^<]+)<', html)
print("\ndata-title 选项（框架名称等各级可见项）:")
seen = set()
for t, v in titles:
    key = (t, v)
    if key in seen:
        continue
    seen.add(key)
    print(f"  [{t}] {v}")

# 2) 抽取所有 el-cascader-node 的文本（前 30 个）
nodes = re.findall(r'class="el-cascader-node[^"]*"[^>]*>(.*?)</li>', html, re.S)
print(f"\nel-cascader-node 数量: {len(nodes)}")
clean = []
for n in nodes:
    txt = re.sub(r"<[^>]+>", " ", n)
    txt = re.sub(r"\s+", " ", txt).strip()
    if txt:
        clean.append(txt)
for i, t in enumerate(clean[:30]):
    print(f"  节点{i}: {t}")

# 3) 创建按钮前是否有其他必填项（粗略看表单 label）
labels = re.findall(r'class="el-form-item__label"[^>]*>([^<]+)<', html)
print("\nel-form-item 标签:", labels[:20])
