# -*- coding: utf-8 -*-
"""
verify_split.py — 拆分结果的**确定性校验**（零 LLM、可复现）。

它和评估 agent 的分工:
    本脚本只算**能算准的**（实体数守恒、产物齐全、图面非全白、单图性几何指标、
    已知的错误组合）。语义判断（"这张图上是不是一张完整的图""图名和内容对不对得上"）
    交给视觉 agent —— 它拿本脚本的输出当输入，只判脚本判不了的那几条。
    先跑脚本再派视觉，能把视觉 agent 的负担砍掉一大半，且结论稳定可复现。

为什么**不复用** split_dwg 的聚类:
    这是审计工具，得用第三方视角。复用被审代码的逻辑，等于让同一个 bug 在两边
    同时出现、互相"印证"。所以这里自己读图、自己求 bbox、自己并查集 ——
    只依赖 ezdxf / odafc 这两个第三方库。

用法:
    python verify_split.py <拆分输出目录> <源图> [--out=目录] [--margin=3000]
                           [--skip-recluster]

    <拆分输出目录>  含 split_report.json 与各序号目录（即 --out 指过去的那个目录）
    <源图>          dwg 或 dxf —— 用来算实体数守恒
    --out           校验报告落哪（默认写在拆分输出目录里；作业里应指 04_verify/）
    --skip-recluster 跳过"单图性"重聚类（簇很多时很慢；其余判据照跑）

判据（每条都对应一个具体的、可复现的量）:
    E1 实体数守恒     Σ各簇 + 残片 == 源图里**能求出 bbox 的实体数**
                     （求不出 bbox 的实体本来就不参与聚类，见 split_dwg 的
                      cluster_entities:263-265，所以不能拿 len(modelspace) 直接比）
    E2 产物齐全       每个序号目录里 dxf + json 齐全，该有的配色 PNG 一张不缺
    E3 PNG 非全白     非白像素占比过低 → 渲染其实失败了（文件在、内容是白纸）
    E4 图名口径自洽   每条记录里「图名文本非空」必须与「图名实体数 > 0」同时成立
                     （README 明文规定：titles 非空而 title_entities=0 是错的）
    E5 字体解析探针   在用文字样式的字体被解析成 TrueType 替身 → SHX 描边路径失效、
                     加粗不生效，轴号/尺寸数字必然发虚（2026-09-23 实战根因）。
                     这是像素问题里**能算的那一半**，渲染前就能拦下
    W1 单图性         对拆出的每个产物重跑一次连通聚类：分量数应恰好为 1。
                     分量 > 1 时报出「最大分量间隙 / 图对角线」比值，交视觉终判
                     （图例块、指北针本来就离得远，脚本不该一棍打死）
    W2 巨簇           单簇实体数超阈值 —— margin 切不开时的典型症状
    W3 碎簇           只有 1~2 个实体的簇，多半是碎渣
    W4 残片占比       不出图的文字残片占源图实体数的比例过高

退出码: 0 = pass（可能有 warn）；1 = revise（有 error）。
"""
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

import ezdxf
from ezdxf.addons import odafc
from ezdxf import bbox as ezbbox

from env_file import load_env

load_env()      # 配置只写在 .env 里、没设系统环境变量时也能正确校验

ODA_EXE = os.environ.get(
    "ODA_EXE", r"C:\Program Files\ODA\ODAFileConverter 27.1.0\ODAFileConverter.exe")
ezdxf.options.set("odafc-addon", "win_exec_path", ODA_EXE)

# ---- 阈值（都可调；注释写明为什么取这个值）-----------------------------------
HUGE_CLUSTER_N = 5000     # 单簇实体数超此值告警：margin=3000 切不开时会出现 4 万+实体巨簇
TINY_CLUSTER_N = 2        # 1~2 个实体的簇，多半是没并进图框的碎渣
SEP_RATIO_WARN = 0.05     # 最大分量间隙 / 图对角线 超过 5% 就报警（不是判死 —— 交视觉终判）
BLANK_PNG_RATIO = 0.001   # 非白像素占比低于 0.1% 视为全白（实测干净图都在 5% 以上）
PNG_MIN_BYTES = 5000      # 低于此体积的 PNG 基本是全白图（实测全白图约 3.9KB）


# --------------------------------------------------------------------------- #
# 独立实现：读图 / 求 bbox / 连通聚类
# --------------------------------------------------------------------------- #
def read_drawing(path):
    """dwg 走 ODA，dxf 直接读。两条路都以「SHX 优先」注册字体，保证度量一致。"""
    path = Path(path)
    _register_shx()
    if path.suffix.lower() == ".dxf":
        return ezdxf.readfile(str(path))
    return odafc.readfile(str(path))


_shx_done = False


def _register_shx():
    """从环境变量注册 SHX 字体（进程内一次）。

    为什么必须做：文字实体的 bbox 依赖字体度量。若不注册，ezdxf 会用 TTF 替身，
    字宽与真 SHX 不同 → bbox 偏移 → 连通性判断出错。这里独立实现（不复用 png_export
    的版本），代价是十几行，换来的是校验脚本能单独分发、不拖一堆依赖。
    """
    global _shx_done
    if _shx_done:
        return
    raw = os.environ.get("SHX_FONT_DIRS", "")
    dirs = []
    import glob
    for part in raw.split(os.pathsep):
        part = part.strip()
        if part:
            dirs += [d for d in glob.glob(part) if Path(d).is_dir()]
    if dirs:
        ezdxf.options.support_dirs = dirs
        try:
            from ezdxf.fonts import fonts
            fonts.font_manager.build(support_dirs=True)
            # SHX 优先（默认 "tsl" 是 TrueType 优先，会让真 SHX 永远轮不到）
            ezdxf.options.set("drawing-addon", "shx_resolve_order", "stl")
        except Exception as e:                # noqa: BLE001
            print(f"  ! SHX 注册异常（继续，度量可能略有偏差）: {e}")
    _shx_done = True


def bbox_of(ent):
    """单个实体的 bbox；求不出返回 None（与 split_dwg.cluster_entities 同口径）。"""
    try:
        ext = ezbbox.extents([ent], fast=True)
    except Exception:                         # noqa: BLE001 —— 退化图元会抛
        return None
    if not ext.has_data:
        return None
    return (float(ext.extmin.x), float(ext.extmin.y),
            float(ext.extmax.x), float(ext.extmax.y))


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


def components_of(doc, margin):
    """独立重算连通分量。

    返回 (n_total, n_bboxable, parts)
        parts = [ {"n": 实体数, "bbox": (...)} ]  按实体数降序
    """
    items = []
    n_total = 0
    for ent in doc.modelspace():
        n_total += 1
        bb = bbox_of(ent)
        if bb is not None:
            items.append(bb)
    n = len(items)
    if n == 0:
        return n_total, 0, []

    order = sorted(range(n), key=lambda i: items[i][0])
    dsu = DSU(n)
    for idx, i in enumerate(order):
        x1i, y1i, x2i, y2i = items[i]
        for jdx in range(idx + 1, len(order)):
            j = order[jdx]
            bx1, by1, bx2, by2 = items[j]
            if bx1 - x2i > margin:            # 按 x 排序，后面只会更远 → 提前收
                break
            if (x1i - margin <= bx2 and bx1 <= x2i + margin and
                    y1i - margin <= by2 and by1 <= y2i + margin):
                dsu.union(i, j)

    groups = {}
    for i in range(n):
        groups.setdefault(dsu.find(i), []).append(i)

    parts = []
    for idxs in groups.values():
        xs1 = min(items[i][0] for i in idxs); ys1 = min(items[i][1] for i in idxs)
        xs2 = max(items[i][2] for i in idxs); ys2 = max(items[i][3] for i in idxs)
        parts.append({"n": len(idxs), "bbox": (xs1, ys1, xs2, ys2)})
    parts.sort(key=lambda p: -p["n"])
    return n_total, n, parts


def font_probe(doc):
    """E5：找出「应解析成 SHX 描边字体、却落到 TrueType 替身」的在用文字样式。

    为什么能算：字体解析是**机器全局**的（本机字体目录 + 解析顺序），不随图变 ——
    所以只需对源图探一次（产物样式继承自源图）。症状链：TrueTypeFont 走填充路径、
    matplotlib 的 linewidth 硬编码 0 → 文字加粗机制整条失效 → 轴号/尺寸数字发丝化。
    只查**在用**的样式（被 TEXT/MTEXT/ATTRIB 引用过的），没用的样式定义不报 ——
    报了也是噪音。
    """
    from ezdxf.fonts import fonts

    used = set()

    def collect(layout):
        for e in layout:
            t = e.dxftype()
            if t in ("TEXT", "MTEXT", "ATTRIB", "ATTDEF"):
                used.add(e.dxf.get("style") or "Standard")
            elif t == "INSERT":
                for a in e.attribs:
                    used.add(a.dxf.get("style") or "Standard")

    collect(doc.modelspace())
    for blk in doc.blocks:
        if blk.name.startswith(("*Model_Space", "*Paper_Space")):
            continue
        collect(blk)

    bad = []
    for st in doc.styles:
        if st.dxf.name not in used:
            continue
        f = (st.dxf.font or "").strip()
        if not f:
            continue                                  # 空字体走 Standard 兜底，不在这判
        ext = f.rsplit(".", 1)[-1].lower() if "." in f else ""
        if ext in ("ttf", "ttc", "otf", "fon", "fce"):
            continue                                  # 刻意用 TrueType 的（如 simhei.ttf）正常
        # 走到这的都是「SHX 形态」的字体名（无扩展名或 .shx）——解析成 TrueType 就是病
        try:
            resolved = fonts.make_font(f, 2.5)
        except Exception:                             # noqa: BLE001 —— 解析不了让 V7 兜底
            continue
        if type(resolved).__name__ == "TrueTypeFont":
            bad.append({"style": st.dxf.name, "font": f})
    return bad


def gap_ratio(parts):
    """最大分量间隙 / 该图对角线 —— 「两块图离得有多开」的量化。

    间隙取各分量 bbox 两两之间的最小矩形间距中的最大值（保守：取最能说明问题的那个）。
    """
    if len(parts) < 2:
        return 0.0
    allb = parts[0]["bbox"]
    for p in parts[1:]:
        b = p["bbox"]
        allb = (min(allb[0], b[0]), min(allb[1], b[1]),
                max(allb[2], b[2]), max(allb[3], b[3]))
    diag = max(((allb[2] - allb[0]) ** 2 + (allb[3] - allb[1]) ** 2) ** 0.5, 1e-9)

    worst = 0.0
    for i in range(len(parts)):
        for j in range(i + 1, len(parts)):
            a, b = parts[i]["bbox"], parts[j]["bbox"]
            dx = max(b[0] - a[2], a[0] - b[2], 0.0)
            dy = max(b[1] - a[3], a[1] - b[3], 0.0)
            worst = max(worst, (dx * dx + dy * dy) ** 0.5)
    return worst / diag


# --------------------------------------------------------------------------- #
# PNG 检查
# --------------------------------------------------------------------------- #
def png_nonwhite_ratio(path):
    """非白像素占比；PIL 不可用时返回 None（跳过该项，不误报）。"""
    try:
        from PIL import Image
    except ImportError:
        return None
    try:
        Image.MAX_IMAGE_PIXELS = None        # 大图会触发 DecompressionBomb 警告
        with Image.open(path) as im:
            grey = im.convert("L")
            dark = sum(grey.histogram()[:200])   # 灰度 < 200 算非白
            total = grey.width * grey.height
        return dark / total if total else 0.0
    except Exception:                         # noqa: BLE001
        return None


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def verify(split_dir, source_dwg, margin=3000.0, skip_recluster=False):
    split_dir = Path(split_dir).resolve()
    source_dwg = Path(source_dwg).resolve()
    issues, clusters = [], []

    report_path = split_dir / "split_report.json"
    if not report_path.is_file():
        return {
            "verdict": "revise",
            "issues": [{"id": "report_missing", "level": "error",
                        "msg": f"没有 {report_path} —— 这个目录不是拆分产物（或拆到一半中断了）"}],
            "clusters": [],
        }
    report = json.loads(report_path.read_text(encoding="utf-8"))

    # ---- E1 实体数守恒 ----------------------------------------------------
    doc = read_drawing(source_dwg)
    n_total, n_bboxable, _ = components_of(doc, margin)

    # ---- E0 源图可读性（天正对象未入图 → 产物必然缺内容，不许 PASS）--------
    # 2026-09-23 实战教训：源图 811 实体里 618 个是天正对象，产物只含 193 个
    # 可读实体。旧版只做「可读实体之间」的守恒，残图照样 PASS —— 评分模型在说谎。
    # 修法：数「连显示缓存都解不出」的天正对象，有多少就缺多少，直接 revise。
    # 带可解码缓存的不算缺失（拆分侧会自动从缓存炸开成基本图元）。
    # ⚠️ 字符串字面量是故意的：审计工具不复用被审代码（与 E2 配色清单同理）。
    n_proxy_src = n_undecodable = 0
    for e in doc.modelspace():
        if e.dxftype() != "ACAD_PROXY_ENTITY":
            continue
        n_proxy_src += 1
        try:
            decodable = bool(list(e.virtual_entities()))
        except Exception:
            decodable = False
        if not decodable:
            n_undecodable += 1
    if n_undecodable:
        issues.append({
            "id": "source_unreadable_objects", "level": "error",
            "msg": (f"源图里有 {n_proxy_src} 个天正对象，其中 {n_undecodable} 个"
                    f"**连显示缓存都解不出**（缓存是空壳、对象真身是天正私有二进制，"
                    f"只有天正软件能读）—— 产物**必然缺这些内容**，以下校验只对得上"
                    f"「Python 可读实体」。要完整产物：在天正里把天正对象炸开"
                    f"（EXPLODE，可能要炸两次）后另存 DWG，再重跑拆分。"),
            "detail": {"n_proxy": n_proxy_src, "n_undecodable": n_undecodable,
                       "n_total_source": n_total},
        })

    # ---- E5 字体解析探针（对源图跑一次即可，产物样式继承自源图）------------
    bad_fonts = font_probe(doc)
    if bad_fonts:
        names = ", ".join(f"{b['style']}({b['font']})" for b in bad_fonts)
        issues.append({
            "id": "font_substituted", "level": "error",
            "msg": (f"字体解析探针：{len(bad_fonts)} 个在用文字样式被解析成 TrueType 替身"
                    f" —— {names}。后果：SHX 描边路径失效、文字加粗不生效，"
                    f"轴号/尺寸数字会发虚。修法：把这些字体 .shx 所在的 Fonts 目录"
                    f"补进 .env 的 SHX_FONT_DIRS（check_env.py 会点名缺哪个；"
                    f"AutoCAD 装在非 Program Files 时默认通配匹配不上，要写实际路径）"),
            "detail": {"styles": bad_fonts},
        })

    n_report = sum(int(it.get("n_entities") or 0) for it in report)
    n_clusters = sum(1 for it in report if it.get("seq"))
    n_frags = len(report) - n_clusters
    frag_n = sum(int(it.get("n_entities") or 0) for it in report if not it.get("seq"))

    if n_report != n_bboxable:
        diff = n_report - n_bboxable
        issues.append({
            "id": "entity_conservation", "level": "error",
            "msg": (f"实体数不守恒：报告合计 {n_report}，源图可求 bbox 的实体 {n_bboxable}"
                    f"（差 {diff:+d}；源图实体总数 {n_total}，其中 {n_total - n_bboxable} 个"
                    f"求不出 bbox、本就不参与聚类）"),
            "detail": {"n_report": n_report, "n_bboxable": n_bboxable,
                       "n_total": n_total, "diff": diff},
        })

    # ---- 逐簇检查 --------------------------------------------------------
    for it in report:
        if not it.get("seq"):                 # 残片条目
            continue
        seq, n = it["seq"], int(it.get("n_entities") or 0)
        row = {"seq": seq, "name": Path(it["file"]).stem if it.get("file") else None,
               "n_entities": n, "category": it.get("category")}

        seq_dir = split_dir / seq
        art = Path(it["file"]) if it.get("file") else seq_dir / f"{seq}.dxf"

        # E2 产物齐全
        missing = []
        if not art.is_file():
            missing.append(art.name)
        if not art.with_suffix(".json").is_file():
            missing.append(art.with_suffix(".json").name)
        # ⚠️ 这份配色清单是**故意**独立于 png_export.VARIANT_TOKENS 的：verify_split
        # 是审计工具，不复用被审代码 —— 否则同一个 bug 会在两边互相印证。
        # 改 png_export 的配色必须同步改这里，否则校验会对不上实际产物。
        for v in ("白底黑白", "黑底彩色"):
            if not (seq_dir / f"{art.stem}_{v}.png").is_file():
                missing.append(f"{art.stem}_{v}.png")
        if missing:
            issues.append({"id": "artifacts_missing", "level": "error",
                           "msg": f"{seq}/ 缺文件: {', '.join(missing)}",
                           "detail": {"seq": seq, "missing": missing}})

        # E3 PNG 非全白
        bw = seq_dir / f"{art.stem}_白底黑白.png"
        if bw.is_file():
            ratio = png_nonwhite_ratio(bw)
            row["nonwhite_ratio"] = None if ratio is None else round(ratio, 5)
            low_bytes = bw.stat().st_size < PNG_MIN_BYTES
            if (ratio is not None and ratio < BLANK_PNG_RATIO) or low_bytes:
                issues.append({
                    "id": "png_blank", "level": "error",
                    "msg": (f"{seq}/{bw.name} 几乎是白纸（非白像素占比 "
                            f"{'?' if ratio is None else f'{ratio:.4%}'}，{bw.stat().st_size}B）"
                            f" —— 渲染其实失败了"),
                    "detail": {"seq": seq, "file": str(bw)}})

        # E4 图名口径自洽（report 侧用 title_texts，它才是"要摘的文字"）
        t_texts = it.get("title_texts") or []
        t_ents = int(it.get("title_entities") or 0)
        if bool(t_texts) != bool(t_ents):
            issues.append({
                "id": "title_inconsistent", "level": "error",
                "msg": (f"{seq}/：图名文本 {t_texts!r} 与图名实体数 {t_ents} 矛盾"
                        f" —— 名字来自哪条文字就该剔哪条，否则 --title-mode 静默失效"),
                "detail": {"seq": seq, "title_texts": t_texts, "title_entities": t_ents}})

        # 每图 sidecar json 也查一遍（下游读的是它）
        mpath = art.with_suffix(".json")
        if mpath.is_file():
            meta = json.loads(mpath.read_text(encoding="utf-8"))
            if bool(meta.get("titles")) != bool(int(meta.get("title_entities") or 0)):
                issues.append({
                    "id": "title_inconsistent_sidecar", "level": "error",
                    "msg": f"{seq}/{mpath.name} 的 titles 与 title_entities 矛盾",
                    "detail": {"seq": seq, "titles": meta.get("titles"),
                               "title_entities": meta.get("title_entities")}})

        # W2 / W3 规模
        if n > HUGE_CLUSTER_N:
            issues.append({"id": "huge_cluster", "level": "warn",
                           "msg": f"{seq}/ 单簇 {n} 个实体（>{HUGE_CLUSTER_N}）"
                                  f" —— margin 多半切不开这块，可能出现 4 万+实体巨簇",
                           "detail": {"seq": seq, "n_entities": n}})
        elif n <= TINY_CLUSTER_N:
            issues.append({"id": "tiny_cluster", "level": "warn",
                           "msg": f"{seq}/ 只有 {n} 个实体 —— 多半是没并进图框的碎渣",
                           "detail": {"seq": seq, "n_entities": n}})

        # W1 单图性（重聚类）
        if not skip_recluster and art.is_file():
            try:
                _, _, parts = components_of(read_drawing(art), margin)
                row["n_components"] = len(parts)
                if len(parts) > 1:
                    ratio = gap_ratio(parts)
                    row["max_gap_ratio"] = round(ratio, 4)
                    issues.append({
                        "id": "multi_component", "level": "warn",
                        "msg": (f"{seq}/{art.name} 内部还有 {len(parts)} 块互不相连的图形，"
                                f"最大间隙占图幅对角线的 {ratio:.1%}"
                                + ("　← 超过 5% 阈值，请视觉确认是不是两张图没切开"
                                   if ratio > SEP_RATIO_WARN else "　← 未超阈值，多半是图内正常留白")),
                        "detail": {"seq": seq, "n_components": len(parts),
                                   "gap_ratio": round(ratio, 4),
                                   "parts_n": [p["n"] for p in parts[:5]]}})
            except Exception as e:             # noqa: BLE001
                issues.append({"id": "recluster_failed", "level": "warn",
                               "msg": f"{seq}/ 重聚类失败: {type(e).__name__}: {e}",
                               "detail": {"seq": seq}})

        clusters.append(row)

    # ---- W4 残片占比 ------------------------------------------------------
    if n_frags and n_bboxable:
        share = frag_n / n_bboxable
        if share > 0.05:
            issues.append({"id": "fragment_share", "level": "warn",
                           "msg": (f"文字残片 {n_frags} 块共 {frag_n} 个实体，占源图 "
                                   f"{share:.1%} —— 超出图了，看看是不是有图框没并上"),
                           "detail": {"n_fragments": n_frags, "fragment_entities": frag_n,
                                      "share": round(share, 4)}})

    errors = [i for i in issues if i["level"] == "error"]
    return {
        "source_dwg": str(source_dwg),
        "split_dir": str(split_dir),
        "checked_at": f"{datetime.now():%Y-%m-%d %H:%M:%S}",
        "margin": margin,
        "verdict": "revise" if errors else "pass",
        "summary": {
            "n_clusters": n_clusters, "n_fragments": n_frags,
            "entities_source_total": n_total,
            "entities_source_bboxable": n_bboxable,
            "entities_report": n_report,
            "n_error": len(errors), "n_warn": len(issues) - len(errors),
        },
        "issues": issues,
        "clusters": clusters,
    }


def _opt(flags, name, default=None):
    pfx = f"--{name}="
    for a in flags:
        if a.startswith(pfx):
            return a[len(pfx):]
    return default


def main():
    raw = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = [a for a in sys.argv[1:] if a.startswith("--")]
    if len(raw) < 2 or "--help" in flags or "-h" in flags:
        print(__doc__.strip())
        sys.exit(0 if raw else 2)

    split_dir = Path(raw[0])
    out_dir = Path(_opt(flags, "out", str(split_dir)))
    result = verify(split_dir, raw[1],
                    margin=float(_opt(flags, "margin", 3000.0)),
                    skip_recluster="--skip-recluster" in flags)

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "校验报告.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")

    s = result["summary"]
    print(f"判定: {result['verdict'].upper()}   "
          f"图 {s['n_clusters']} 张 / 残片 {s['n_fragments']} 块 / "
          f"实体 {s['entities_report']} vs 源图可求 bbox {s['entities_source_bboxable']}")
    for i in result["issues"]:
        mark = "✗" if i["level"] == "error" else "⚠"
        print(f"  {mark} [{i['id']}] {i['msg']}")
    if not result["issues"]:
        print("  ✓ 所有确定性判据都过")
    print(f"报告: {out_dir / '校验报告.json'}")

    sys.exit(1 if result["verdict"] == "revise" else 0)


if __name__ == "__main__":
    main()
