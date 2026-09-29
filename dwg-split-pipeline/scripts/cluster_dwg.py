# -*- coding: utf-8 -*-
"""
cluster_dwg.py — 对普通 DWG 做空间连通聚类，识别图中的独立示意图分组。

用法:
    python cluster_dwg.py <dwg路径> [间距阈值] [输出json路径]

算法:
    1. 每个模型空间实体计算 bbox（INSERT/DIMENSION 按几何展开）
    2. bbox 向四周膨胀 margin 后做连通合并（union-find）
    3. 每个簇内检索图名类文字（含"平面/布置图/倾斜/观测"等关键词）
"""
import json
import os
import sys
from pathlib import Path

# stdout 统一 UTF-8；用幂等的 reconfigure，不要替换 sys.stdout 对象
# （重复包装的 TextIOWrapper 被 GC 时会关掉底层 buffer，见 split_dwg.py 说明）
sys.stdout.reconfigure(encoding="utf-8")

import ezdxf
from ezdxf.addons import odafc
from ezdxf import bbox

ODA_EXE = os.environ.get("ODA_EXE",
                         r"C:\Program Files\ODA\ODAFileConverter 27.1.0\ODAFileConverter.exe")
ezdxf.options.set("odafc-addon", "win_exec_path", ODA_EXE)

LABEL_KEYWORDS = ("平面", "布置图", "倾斜", "观测", "示意图", "立面", "剖面", "屋顶")


def entity_bbox_raw(ent):
    try:
        b = bbox.extents([ent], fast=False)
        if b.has_data:
            return (b.extmin.x, b.extmin.y, b.extmax.x, b.extmax.y)
    except Exception:
        pass
    return None


class DSU:
    def __init__(self, n):
        self.p = list(range(n))

    def find(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[ra] = rb


def run(dwg_path, margin=3000.0, out_json=None):
    """对单个 DWG 做空间连通聚类，把分簇信息写到 out_json（只探查不出图）。"""
    dwg_path = Path(dwg_path)
    out_json = Path(out_json) if out_json else dwg_path.with_suffix(".clusters.json")

    print(f"读取: {dwg_path}")
    doc = odafc.readfile(str(dwg_path))
    msp = doc.modelspace()

    ents = []  # (entity, bbox)
    for ent in msp:
        bb = entity_bbox_raw(ent)
        if bb is None:
            continue
        ents.append((ent, bb))
    n = len(ents)
    print(f"有效实体: {n}")

    # 膨胀后连通合并（按 x 排序后只比较窗口内候选，O(n log n) 近似）
    order = sorted(range(n), key=lambda i: ents[i][1][0])
    dsu = DSU(n)
    for idx, i in enumerate(order):
        bi = ents[i][1]
        x1i, y1i, x2i, y2i = bi
        for jdx in range(idx + 1, len(order)):
            j = order[jdx]
            bj = ents[j][1]
            if bj[0] - x2i > margin:  # x 间隙超出，后面更远
                break
            if (x1i - margin <= bj[2] and bj[0] <= x2i + margin and
                    y1i - margin <= bj[3] and bj[0 + 1] <= y2i + margin):
                dsu.union(i, j)

    groups = {}
    for i, (ent, bb) in enumerate(ents):
        groups.setdefault(dsu.find(i), []).append((ent, bb))

    # 输出簇摘要
    clusters = []
    for root, members in groups.items():
        x1 = min(m[1][0] for m in members)
        y1 = min(m[1][1] for m in members)
        x2 = max(m[1][2] for m in members)
        y2 = max(m[1][3] for m in members)
        labels = []
        for ent, bb in members:
            if ent.dxftype() == "TEXT":
                t = ent.dxf.text.strip()
                if any(k in t for k in LABEL_KEYWORDS):
                    labels.append({"text": t, "x": round(ent.dxf.insert[0], 1),
                                   "y": round(ent.dxf.insert[1], 1),
                                   "layer": ent.dxf.layer, "height": round(ent.dxf.height, 1)})
        clusters.append({
            "n_entities": len(members),
            "bbox": [round(x1, 1), round(y1, 1), round(x2, 1), round(y2, 1)],
            "size": [round(x2 - x1, 1), round(y2 - y1, 1)],
            "labels": sorted(labels, key=lambda L: -L["height"]),
        })
    clusters.sort(key=lambda c: (c["bbox"][0], c["bbox"][1]))

    for k, c in enumerate(clusters):
        print(f"\n=== 簇 {k}: {c['n_entities']} 个实体, bbox={c['bbox']}, 尺寸={c['size']}")
        for L in c["labels"][:8]:
            print(f"    图名候选: {L['text']!r} @({L['x']},{L['y']}) h={L['height']} layer={L['layer']}")

    out_json.write_text(json.dumps(
        {"margin": margin, "n_entities": n, "n_clusters": len(clusters), "clusters": clusters},
        ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n簇总数: {len(clusters)}, 结果已写入: {out_json}")


def main():
    run(Path(sys.argv[1]),
        margin=float(sys.argv[2]) if len(sys.argv) > 2 else 3000.0,
        out_json=sys.argv[3] if len(sys.argv) > 3 else None)


if __name__ == "__main__":
    main()
