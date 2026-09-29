# -*- coding: utf-8 -*-
"""
png_export.py — 把 ezdxf 文档渲染成两种 PNG 预览图：

    <名称>_白底黑白.png   白底纯黑线，适合插入 Word
    <名称>_黑底彩色.png   黑底原色，CAD / 天正 原生观感

渲染保真策略（与 CAD 实际显示对齐）：
  1. 注册 SHX 字体目录（天正/AutoCAD 的字体路径），ezdxf 用真实 SHX 描边字体
     原生渲染 —— 文字宽度与 CAD 一致（gbenor '0.88' = 2000 单位，
     而回退 TTF 是 2719，会把本不交叠的标注挤到一起）
  2. ‰ 按 CAD 原图实测几何用「3 椭圆 + 1 斜线」重建 —— ezdxf 不支持
     BIGFONT(GBCBIG)，它原本是**描边**字形；回退 TTF 是**实心填充**，
     笔画会粗 2~3 倍，造成「数值浅、‰ 深」的反差
  3. SHX 不含的其他大字体字符（中文等）按
     「ASCII 前缀(原生 SHX) + 尾部符号(CJK 字体)」拆分渲染，
     尾部位置按 SHX 实测宽度排布，与原图作者 +2000/+1833 的摆位一致
  4. 无法解析为 SHX 的样式整体换 CJK 字体（旧行为）
  5. 「混合型」LWPOLYLINE（锥形宽 + 零宽段并存）的零宽段设极小宽度，
     否则 ezdxf 渲染器走填充带路径时零宽段不可见

以上全部是渲染期临时改动，结束后自动还原，不污染文档。
"""
from pathlib import Path
import glob
import math
import os
import random
import re

import matplotlib
matplotlib.use("Agg")  # 无窗口渲染，必须在 pyplot 之前设置

import ezdxf
from ezdxf import bbox
from ezdxf.addons.drawing import config as draw_config
from ezdxf.addons.drawing.matplotlib import qsave
from ezdxf.fonts import fonts
from ezdxf.fonts.fonts import ShapeFileFont

# SHX 字体搜索目录（支持 glob 通配）。**顺序有意义：靠前的目录优先。**
#
# 为什么必须同时给「天正 SYS」和「AutoCAD Fonts」：
#   · 天正 SYS 只带国标字体（gbenor / gbeitc / gbcbig0 / hztxt …）；
#   · 图纸样式里的 SIMPLEX / COMPLEX / TXT / ROMANS / ITALICC 属 AutoCAD 标准
#     字体，天正目录里**一个都没有**。
# 缺了后者，ezdxf 不报错，而是静默换成自带的 TTF 替身（见 fonts.py 的
# SHX→TTF 别名表：SIMPLEX→simplex_.ttf、COMPLEX→complex_.ttf、
# TXT→txt_____.ttf、ROMANS→romans__.ttf…）。这类替身的字形是「把单线笔画
# 转出来的**极细填充轮廓**」，而且 TTF 走 ``draw_filled_paths``
# —— 该路径把 ``linewidth`` 硬编码成 0，于是：
#   ① 笔画永远只有字高的几个百分点，小字号下抗锯齿塌成灰（观感「发虚」）；
#   ② 抬 ``lineweight`` 对它**完全无效**（填充路径根本不读线宽）。
# 实测：轴号 ATTRIB（style ``_TCH_AXIS`` = COMPLEX）与尺寸 MTEXT
# （style ``_TCH_DIM_T3`` = SIMPLEX）双双命中替身 —— 这正是「轴网数字/字母
# 无论黑底白底都发灰」的根因。
# 兜底目录：**只放各家机器都常见的安装位，不写作者本机路径** ——
# 发行版是发给别人的，里面不该出现只有作者机器才存在的目录。
# 天正各人装的位置都不同（作者是 G:\Tangent\TArchT20V9\SYS），必须靠 .env 的
# SHX_FONT_DIRS 给；没配时这里兜不住天正字体，check_env.py 会报 ✗ 提示去配。
_SHX_DEFAULT_DIRS = [
    r"C:\Program Files\Autodesk\AutoCAD*\Fonts",                    # AutoCAD 标准字体
    r"C:\Users\*\AppData\Roaming\Autodesk\AutoCAD *\R*\*\Support",  # 用户 Support（gdt/ltypeshp）
]


def _shx_dirs():
    """当前生效的 SHX 字体目录（**每次调用现读 env**，不在 import 时定格）。

    为什么必须现读：SHX_FONT_DIRS 由 `.env` 提供，而 `.env` 是 env_file.load_env() 装的。
    若在 import 时读取，就等于要求「所有入口都必须先 load_env、再 import 本模块」——
    漏一个（比如 split_dwg.py 自己的 CLI 就没读 .env）就会静默退回兜底目录，
    表现成「轴号/尺寸数字发虚」这种**不报错**的降级。现读则与 import 顺序无关。
    """
    raw = os.environ.get("SHX_FONT_DIRS")
    return raw.split(os.pathsep) if raw else _SHX_DEFAULT_DIRS
CJK_FONT_FILE = "simhei.ttf"        # 渲染 ‰/中文 用的 CJK 字体
CJK_STYLE_NAME = "_PNG_CJK_RENDER"
_ASCII_RE = re.compile(r"^[\x20-\x7e]+$")
_shx_ready = False
_shx_dirs_used: list[str] = []      # 实际生效的目录（排查用）

# ---------------------------------------------------------------------------
# 渲染分辨率
#
# 背景：ezdxf 的 qsave() 不传 size_inches 时，最终尺寸由
# `plt.figaspect(data_height / data_width)` 决定（见 ezdxf/addons/drawing/
# matplotlib.py finalize()）——**高度锁死 matplotlib 默认的 4.8 英寸**，
# 宽度按长宽比推算。于是「图幅越大，每 DSU 分到的像素越少」：
# 18300×15250 DSU 的图在 150 dpi 下只有 866×720 px，297.5 DSU 的尺寸数字
# 只有约 14 px 高，笔画不足 1 像素，抗锯齿后塌成浅灰 → 观感「发虚、看不清」。
#
# 解法：显式传 size_inches，按「长边目标像素」自适应，
# 并给每 DSU 的像素密度设上限（避免小图被放大到离谱）。
PNG_DPI = 300                # 默认分辨率
PNG_TARGET_LONG_PX = 2400    # 每张 PNG 长边目标像素
PNG_MAX_PX_PER_DSU = 0.5     # 每 DSU 最多放大到 0.5 px（小图不至于虚胖）
PNG_MAX_INCH = 16.0          # 单边最大英寸（与 ezdxf 自身的夹取范围一致）

# 整图（**未拆分**的源图）用更大的长边目标像素。
# 为什么单独给一个：整图的用途是「拆分前 vs 拆分后」对照，一张里装着好几张小图 ——
# 按单图的 2400px 渲出来，每张小图只剩几百像素，图框与标题栏都看不清。
# 取 4800 是有依据的：PNG_MAX_INCH=16 英寸 × 300dpi = 4800px 正好是夹取上限，
# 再往上会被 _auto_size_inches 夹回来，白设。
PNG_OVERVIEW_LONG_PX = 4800
OVERVIEW_SUFFIX = "_整图"     # 整图产物: <源图主干>_整图_<配色>.png

# 渲染随机种子 —— 让 PATTERN 剖面线的相位**跨运行可复现**。
#
# 为什么要固定：
#   ezdxf 画 DXF HATCH 的图案时，`render/hatching.py` 的 `_jiggle_factor()` 会调用
#   `random.random()`，给图案原点加一个随机微偏移（0.03%~0.1% 幅面，
#   `pattern_baselines(..., jiggle_origin=True)`，见 addons/drawing/frontend.py 的
#   `draw_hatch_pattern`）。这是 ezdxf 的**刻意设计**：避免剖面线正好压在边界线上。
#   代价是——Python 的 `random` 每个进程都从 OS 熵自动播种，于是**同一张图两个进程
#   渲出来不一样**：剖面线相位差亚像素，边缘抗锯齿差 ±1~6 灰阶。
#   实测（`平面示意图4`）：两次独立进程差异 414~627 px（最大差 6）；含 PATTERN HATCH
#   的图才会中招，纯线框图 0 差异。
#
# 固定种子后：保留 jiggle 的视觉效果，但同一张图每次渲出来**逐字节一致** ——
# 回归比对（逐像素 0 差异）才有意义。
RENDER_RANDOM_SEED = 0

# ---------------------------------------------------------------------------
# 文字描边加粗（三种配色都生效）
#
# 现象：轴号 ①②③ / ⒶⒷⒸ、尺寸数字 4200 / 29100 发灰发虚，同图的「北」却清晰。
# 根因是**字体分支不同**，与底色、与颜色都无关：
#   · 中文/混排 → `_transform_texts` 换成 simhei.ttf（TrueType 轮廓字体），
#     ezdxf 走 ``draw_filled_paths``（fill=True, linewidth=0），笔画宽度由字形
#     轮廓决定 → 实心饱满，「北」因此清晰；
#   · 纯 ASCII / 拉丁字母 → 保留 SHX 单线字体（SIMPLEX / COMPLEX / gbenor.shx），
#     ezdxf 走 ``draw_path``（fill=False），线宽 = ``get_lineweight()``
#     = 实体有效线宽 = 0.25 mm → 0.25 pt → **1.04 px @300dpi**。
# 1 px 的线落在像素网格之间时，抗锯齿把墨量摊到相邻像素，得到中间灰。
# 白底是「黑压白」，灰边不显眼；黑底是「灰压黑」，直接读不出来 —— 同一根
# 1 px 线，在白底被误判为「没问题」，在黑底才暴露。
#
# 解法：按「字高像素」临时抬高文字的描边线宽，渲染后还原。
# 不换字体 —— 字形几何与文字宽度都不变，不会像换 TTF 那样把标注挤错位
# （TTF 数字比 SHX 宽，会把 ``0.78`` 挤向旁边的 ``‰``，即红线 C 的老病）。
TEXT_STROKE_RATIO = 0.045   # 目标笔宽 = 字高像素 × 系数
TEXT_STROKE_MIN_PX = 2.5    # 下限：低于 ~2.5px 就会塌成灰
TEXT_STROKE_MAX_PX = 8.0    # 上限：再粗就糊成一坨、失去 SHX 观感

# DXF 规范线宽取值（单位 1/100 mm），ezdxf 只接受这些枚举值
_DXF_LINEWEIGHTS = (0, 5, 9, 13, 15, 18, 20, 25, 30, 35, 40, 50, 53, 60,
                    70, 80, 90, 100, 106, 120, 140, 158, 200, 211)


def _nearest_dxf_lw(centi_mm: float) -> int:
    """取不小于目标值的最近合法 DXF 线宽（宁可略粗，不可发虚）。"""
    for v in _DXF_LINEWEIGHTS:
        if v >= centi_mm:
            return v
    return _DXF_LINEWEIGHTS[-1]


def _resolve_support_dirs():
    """展开生效的 SHX 目录里的 glob，返回真实存在的目录（保持顺序、去重）。"""
    out = []
    for pat in _shx_dirs():
        hits = sorted(glob.glob(pat)) if any(c in pat for c in "*?[") else [pat]
        for d in hits:
            if Path(d).is_dir() and d not in out:
                out.append(d)
    return out


def _ensure_shx_support():
    """注册 SHX 字体目录并重建字体缓存（进程内一次）。

    注意：ezdxf 的 ``_CachedFont._glyph_caches`` 是**类级**字典，按字体名缓存解析
    结果。若在注册 SHX 目录之前就做过文字度量（例如对 TEXT 求 bbox），
    ``gbenor.shx`` 会先被解析成占位 TrueType 字体并把字形写入该缓存，之后即使注册
    了 SHX 目录也不会重算（``font_manager.clear()`` 只清字体管理器，清不掉它）。
    结果是字形放大 ~1.27× 且退化为空心轮廓。故此处显式清一次类级字形缓存。
    """
    global _shx_ready, _shx_dirs_used
    if _shx_ready:
        return
    dirs = _resolve_support_dirs()
    if dirs:
        _shx_dirs_used = dirs
        ezdxf.options.support_dirs = dirs
        fonts.font_manager.build(support_dirs=True)
        # ★ 决定性开关：SHX 解析顺序默认是 "tsl"（**TrueType 优先**），
        #   于是 ezdxf 先挑中自带的 TTF 替身（simplex_.ttf / complex_.ttf…），
        #   磁盘上的真 SHX 永远轮不到。后果有两条，且都只在渲染时才暴露：
        #     ① 替身是「单线笔画转出的极细轮廓」，TTF 又走 draw_filled_paths
        #        （linewidth 硬编码 0）→ 笔画细成发丝，小字号抗锯齿后一片灰；
        #     ② 替身的字宽与真 SHX 不同 → 标注间距与 CAD 不一致。
        #   改成 "stl"：SHX 优先，找不到才退 TrueType，最后 LFF。
        #   改完 "s" 命中后，文字走 draw_path（描边），lineweight 才真正生效。
        ezdxf.options.set("drawing-addon", "shx_resolve_order", "stl")
        try:
            from ezdxf.fonts.fonts import _CachedFont
            _CachedFont._glyph_caches.clear()
        except ImportError:  # 兼容未来的 ezdxf 版本
            pass
    _shx_ready = True


# 公开别名。调用方（如 split_dwg.main）必须在**任何会触发文字度量的操作之前**
# 调用它 —— 典型触发点是 bbox.extents() 对 TEXT 求包围盒。若顺序颠倒：
# 先度量 → 后注册，ezdxf 已按“无 SHX 目录”的回退字体解析并缓存了字形，
# 之后再注册 SHX 也不会重算，导致后续渲染字形走样（数字被放大 ~1.27×、变空心
# 轮廓），且 font_manager.clear() 也无法恢复。
ensure_shx_support = _ensure_shx_support


def _resolve_shx_font(name: str, cap_height: float):
    """样式字体名 -> ShapeFileFont（真实 SHX），解析失败返回 None。"""
    if not name:
        return None
    try:
        f = fonts.make_font(name, cap_height)
    except Exception:
        return None
    return f if isinstance(f, ShapeFileFont) else None


def _ensure_cjk_style(doc):
    """取/建 CJK 渲染样式。"""
    if CJK_STYLE_NAME in doc.styles:
        return doc.styles.get(CJK_STYLE_NAME)
    return doc.styles.add(CJK_STYLE_NAME, font=CJK_FONT_FILE)


# ---------------------------------------------------------------------------
# ‰（千分号）几何重建
#
# 背景：CAD 里 ‰ 由大字体 GBCBIG 以**描边**字形绘制，笔画细；而 ezdxf
# 明确不支持 BIGFONT（shapefile.py 抛 UnsupportedShapeFile），只能回退到
# CJK TrueType 字体 —— TTF 是**实心填充**，笔画比 CAD 粗 2~3 倍，于是出现
# 「数值细、‰ 粗」的反差。
#
# 解法：‰ 字形本身就是「3 个小椭圆圈 + 1 条斜线」，直接按 CAD 原图实测比例
# 用 ELLIPSE + LINE 重建，渲染即为细描边，观感与 CAD 一致。
# 下列数值单位 = 文字高度 H（源文件中 ‰ 的 height=4340.43）。
_PERMILLE_RINGS = (  # (圆心x, 圆心y, 半宽, 半高)
    (0.105, 0.625, 0.070, 0.162),  # 左上小圈
    (0.290, 0.187, 0.070, 0.162),  # 右下小圈 1
    (0.499, 0.187, 0.070, 0.162),  # 右下小圈 2
)
_PERMILLE_SLASH = ((0.0, 0.0), (0.394, 0.812))
PERMILLE_CHAR = "‰"


def _copy_text_attribs(e):
    """把 TEXT 的图层/颜色属性复制给替代几何，尽量保持与原文一致。"""
    attribs = {"layer": e.dxf.layer}
    color = e.dxf.get("color", 256)
    if color not in (0, 256):  # 0=BYBLOCK, 256=BYLAYER：交给图层决定
        attribs["color"] = color
    true_color = e.dxf.get("true_color", None)
    if true_color is not None:
        attribs["true_color"] = true_color
    return attribs


def _add_permille_geometry(container, e):
    """把 TEXT '‰' 重建为「3 椭圆 + 1 斜线」细描边几何，返回新建实体列表。

    container: 该 TEXT 所在的布局（modelspace 或 block），几何要加在同一容器里，
               否则块内文字会被放到世界坐标而错位。
    """
    h = float(e.dxf.height)
    rot = float(e.dxf.rotation or 0.0)
    ca, sa = math.cos(math.radians(rot)), math.sin(math.radians(rot))
    ox, oy = e.dxf.insert.x, e.dxf.insert.y
    attribs = _copy_text_attribs(e)

    def tf(u, v):
        """字形局部坐标（以 H 为单位）→ 世界坐标（含文字旋转）。"""
        return (ox + u * h * ca - v * h * sa, oy + u * h * sa + v * h * ca)

    made = []
    for cx, cy, rw, rh in _PERMILLE_RINGS:
        made.append(container.add_ellipse(
            center=tf(cx, cy),
            major_axis=(-rh * h * sa, rh * h * ca),  # 局部 (0, rh) 旋转后的矢量
            ratio=rw / rh,                           # 圈高 > 圈宽，长轴沿局部 y
            dxfattribs=attribs,
        ))
    (u0, v0), (u1, v1) = _PERMILLE_SLASH
    made.append(container.add_line(tf(u0, v0), tf(u1, v1), dxfattribs=attribs))
    return made


def _transform_texts(doc):
    """按保真策略临时改写 TEXT 实体，返回可还原的快照。

    返回 (edits, temp_entities):
      edits: [(entity, 原text, 原style)] —— 渲染后还原
      temp_entities: [(container, entity)] —— 渲染后从所在容器删除
    """
    _ensure_shx_support()
    edits, temp = [], []
    cjk_style = _ensure_cjk_style(doc)

    def process(entities, container):
        # 快照迭代：循环体内会向容器添加临时实体，
        # 直接遍历容器会把新实体也处理一遍
        for e in list(entities):
            if e.dxftype() not in ("TEXT", "MTEXT", "ATTRIB", "ATTDEF"):
                continue
            if e.dxftype() == "MTEXT":
                # MTEXT 带内联格式，整体换 CJK 字体
                if e.text and not _ASCII_RE.match(e.text):
                    edits.append((e, None, e.dxf.style))
                    e.dxf.style = CJK_STYLE_NAME
                continue
            text = e.dxf.text
            if not text or _ASCII_RE.match(text):
                continue  # 纯 ASCII：原生 SHX 渲染，不动
            if text == PERMILLE_CHAR:
                # ‰ 由 BIGFONT 描边字形绘制，ezdxf 不支持 → 回退 TTF 会过粗。
                # 改为按 CAD 实测几何重建细描边（清空原文字，渲染后还原）。
                edits.append((e, text, e.dxf.style))
                e.dxf.text = ""
                for ent in _add_permille_geometry(container, e):
                    temp.append((container, ent))
                continue
            style = doc.styles.get(e.dxf.style)
            shx = _resolve_shx_font(style.dxf.font, e.dxf.height)
            m = re.match(r"^([\x20-\x7e]+)([\x80-\uffff].*)$", text, re.DOTALL)
            if shx is not None and m:
                # 「ASCII 前缀 + 非ASCII 尾部」: 前缀留原生 SHX，尾部用 CJK 补渲染
                prefix, tail = m.group(1), m.group(2)
                edits.append((e, text, e.dxf.style))
                e.dxf.text = prefix
                w = shx.text_width(prefix)
                rot = e.dxf.rotation
                dx = math.cos(math.radians(rot)) * w
                dy = math.sin(math.radians(rot)) * w
                t = container.add_text(tail, dxfattribs={
                    "style": CJK_STYLE_NAME, "height": e.dxf.height,
                    "rotation": rot, "layer": e.dxf.layer,
                    "insert": (e.dxf.insert.x + dx, e.dxf.insert.y + dy),
                })
                temp.append((container, t))
            else:
                # 全中文/复杂混排: 整体换 CJK
                edits.append((e, text, e.dxf.style))
                e.dxf.style = CJK_STYLE_NAME

    for layout in _iter_layouts(doc):
        process(layout, layout)
        # ATTRIB 不在布局遍历里（它挂在 INSERT 上），单独补一遍 ——
        # 轴号数字/字母就是 ATTRIB，中文块属性也走这条
        for e in list(layout):
            if e.dxftype() == "INSERT" and e.attribs:
                process(e.attribs, layout)
    return edits, temp


def _restore_texts(edits, temp):
    for e, text, style in edits:
        if text is not None:
            e.dxf.text = text
        e.dxf.style = style
    for container, e in temp:
        container.delete_entity(e)


def _swap_cjk_fonts(doc):
    """旧接口保留：把所有样式换成 CJK 字体（MTEXT 兜底用）。"""
    snap = []
    for ts in doc.styles:
        snap.append((ts, ts.dxf.font, ts.dxf.bigfont))
        ts.dxf.font = CJK_FONT_FILE
        ts.dxf.bigfont = ""
    return snap


def _restore_fonts(snap):
    for ts, font, bigfont in snap:
        ts.dxf.font = font
        ts.dxf.bigfont = bigfont


def _bump_zero_widths(doc, eps):
    """给混合型 LWPOLYLINE 的零宽段设极小宽度，返回可还原的快照列表。

    ezdxf 渲染器：entity.has_width 为真时整条线走 TraceBuilder 填充带路径，
    零宽段 → 零面积填充 → 不可见。 bumped 后零宽段渲染为 eps 宽的细条带。
    """
    snap = []

    def fix(polylines):
        for e in polylines:
            if not e.has_width:
                continue  # 全零宽线走正常描边路径，无需处理
            pts = e.get_points()
            if not any(p[2] == 0 or p[3] == 0 for p in pts):
                continue
            snap.append((e, pts))
            new_pts = [(p[0], p[1],
                        eps if p[2] == 0 else p[2],
                        eps if p[3] == 0 else p[3],
                        p[4]) for p in pts]
            e.set_points(new_pts, format="xyseb")

    fix(e for e in doc.modelspace() if e.dxftype() == "LWPOLYLINE")
    for blk in doc.blocks:  # 块定义里的多段线同样处理
        if blk.name.startswith("*"):
            continue
        fix(e for e in blk if e.dxftype() == "LWPOLYLINE")
    return snap


def _restore_widths(snap):
    for e, pts in snap:
        e.set_points(pts, format="xyseb")


def _text_height(ent):
    """TEXT / ATTRIB / ATTDEF 用 height，MTEXT 用 char_height（MTEXT 没有 height）。"""
    if ent.dxftype() == "MTEXT":
        return float(ent.dxf.get("char_height", 0) or 0)
    return float(ent.dxf.get("height", 0) or 0)


# 布局别名：`*Model_Space` 与 doc.modelspace() 是**同一批实体**，
# `*Paper_Space*` 同理。遍历块定义时必须跳过它们，否则同一实体被快照两次，
# 「还原」会把已经抬高的值当成原值写回（线宽泄漏进文档）。
_LAYOUT_ALIASES = ("*Model_Space", "*Paper_Space")


def _iter_layouts(doc):
    """产出「模型空间 + 所有真块定义」，跳过 Model/Paper 空间别名。

    注意**必须包含匿名块**（``*D1`` ``*D2``…）：尺寸标注的数值文字就寄居在
    这些匿名块里，旧实现用 ``startswith("*")`` 一刀切，把 31 个 ``*D#``
    全跳过了 —— 这是轴网尺寸数字一直发虚的直接原因。
    """
    yield doc.modelspace()
    for blk in doc.blocks:
        if blk.name.startswith(_LAYOUT_ALIASES):
            continue
        yield blk


def _iter_text_like(layout):
    """产出该布局里渲染器**实际会画**的文字实体。

    除了 TEXT / MTEXT / ATTDEF，还包含 INSERT 上的 ATTRIB —— 块属性
    （轴号数字 ①②③、字母 ⒶⒷⒸ 就是这种）**不属于**布局遍历的结果，
    只能顺着 INSERT 取，否则永远碰不到。
    """
    for e in layout:
        t = e.dxftype()
        if t in ("TEXT", "MTEXT", "ATTRIB", "ATTDEF"):
            yield e
        elif t == "INSERT":
            for a in e.attribs:
                yield a


def _px_per_dsu(ext, size_inches, dpi):
    """由最终尺寸反算「每 DSU 分到多少像素」（取 x/y 里较小的那个）。"""
    if size_inches is None or not ext.has_data:
        return 0.0
    w = max(float(ext.size.x), 1e-9)
    h = max(float(ext.size.y), 1e-9)
    return min(size_inches[0] * dpi / w, size_inches[1] * dpi / h)


def _bump_text_stroke(doc, px_per_dsu, dpi, extra_entities=(),
                      ratio=TEXT_STROKE_RATIO,
                      min_px=TEXT_STROKE_MIN_PX,
                      max_px=TEXT_STROKE_MAX_PX):
    """临时抬高所有文字的描边线宽，返回可还原的快照。

    只动文字（含块属性、含 ‰ 重建几何）的 ``lineweight``，
    不改图层、不改颜色、不改字体 —— 线条/图框/墙体的线宽保持原样。
    """
    if px_per_dsu <= 0:
        return []
    snap = []
    used_lw = []
    px_per_pt = dpi / 72.0

    def set_lw(e, lw):
        cur = e.dxf.get("lineweight", -1)
        if lw > max(cur, 0):        # 原本就更粗的（含 BYBLOCK/BYLAYER=-1）才抬
            snap.append((e, cur))
            e.dxf.lineweight = lw

    for layout in _iter_layouts(doc):
        for e in _iter_text_like(layout):
            h_px = _text_height(e) * px_per_dsu
            if h_px <= 0:
                continue
            want_px = min(max(h_px * ratio, min_px), max_px)
            lw = _nearest_dxf_lw(want_px / px_per_pt * 100.0)
            used_lw.append(lw)
            set_lw(e, lw)

    # ‰ 等「按文字重建的几何」没有字高，套用同图文字线宽的中位数
    if used_lw:
        mid = sorted(used_lw)[len(used_lw) // 2]
        for e in extra_entities:
            if e.is_alive:
                set_lw(e, mid)
    return snap


def _restore_lineweights(snap):
    for e, lw in snap:
        if e.is_alive:
            e.dxf.lineweight = lw


def _auto_size_inches(doc, dpi, target_long_px=PNG_TARGET_LONG_PX,
                      max_px_per_dsu=PNG_MAX_PX_PER_DSU, max_inch=PNG_MAX_INCH):
    """按图幅自适应计算 qsave 的 size_inches，使长边不少于 target_long_px 像素。

    返回 (w_in, h_in)；无实体时返回 None（交给 ezdxf 自己算）。
    """
    ext = bbox.extents(list(doc.modelspace()), fast=True)
    if not ext.has_data:
        return None
    w = max(float(ext.size.x), 1e-9)
    h = max(float(ext.size.y), 1e-9)
    long_side = max(w, h)
    # 每 DSU 分到的像素 = min(目标长边 / 图幅长边, 上限)
    px_per_dsu = min(target_long_px / long_side, max_px_per_dsu)
    w_in, h_in = w * px_per_dsu / dpi, h * px_per_dsu / dpi
    if max(w_in, h_in) > max_inch:
        f = max_inch / max(w_in, h_in)
        w_in, h_in = w_in * f, h_in * f
    return (w_in, h_in)


# 配色选择：--variants 的记号 → 文件名后缀。渲染哪些图由这一个表说了算。
#
# ⚠️ 改这里必须同步三处，否则校验/重渲会与实际产物对不上：
#     render_png.py 的默认 suffixes、verify_split.py 的 E2 配色清单与 E3 的
#     `_白底黑白.png`。（verify_split 那份是**故意**独立写的 —— 审计工具不复用被审
#     代码，免得同一个 bug 两边互相印证。）
VARIANT_TOKENS = {
    "黑底":     "黑底彩色",
    "黑底彩色": "黑底彩色",
    "白底黑白": "白底黑白",
    "黑白":     "白底黑白",
}


def variants_from_tokens(s):
    """把 `--variants` 的值解析成配色后缀集合。

    'all' / 空 → None（两张全出）；否则逗号分隔记号，如 '黑底'、'白底黑白'。
    """
    s = (s or "").strip()
    if not s or s.lower() == "all":
        return None
    out = set()
    for tok in s.replace("，", ",").split(","):
        tok = tok.strip()
        if not tok:
            continue
        if tok not in VARIANT_TOKENS:
            raise SystemExit(f"未知配色 {tok!r}；可选: all / 黑底 / 白底黑白")
        out.add(VARIANT_TOKENS[tok])
    return out or None


def export_png_pair(doc, out_base, dpi=PNG_DPI, which=None,
                    target_long_px=PNG_TARGET_LONG_PX):
    """渲染 PNG 预览图（默认两种配色全出）。

    Args:
        doc: ezdxf 文档
        out_base: 输出基准路径（通常传产物的 Path，函数只取 stem 命名）
        dpi: 分辨率。图幅英寸尺寸按「长边目标像素」自适应（_auto_size_inches），
             与 dpi 无关，因此提高 dpi 只是让同一幅面更清晰，不会改变构图。
        which: 配色后缀集合（来自 variants_from_tokens）；None = 两张全渲染。
        target_long_px: 长边目标像素。单张拆分图用默认值；整图渲染传
             PNG_OVERVIEW_LONG_PX（更大，见该常量的说明）。

    Returns:
        生成的文件路径列表（失败项会抛出异常，由调用方捕获）
    """
    _ensure_shx_support()
    out_base = Path(out_base)
    stem = out_base.stem
    parent = out_base.parent

    variants = [
        # ① 白底纯黑线 —— 插 Word 用
        (f"{stem}_白底黑白.png", "#FFFFFF", draw_config.ColorPolicy.BLACK),
        # ② 黑底原色 —— CAD / 天正 原生观感
        (f"{stem}_黑底彩色.png", "#000000", draw_config.ColorPolicy.COLOR),
    ]
    if which:
        variants = [v for v in variants if any(w in v[0] for w in which)]

    made = []
    text_edits, temp_ents = _transform_texts(doc)
    font_snap = None
    width_snap = None
    try:
        # 零宽段补偿宽度：取图幅对角线长度的 0.05%（视觉细线，且不产生零面积多边形）
        ext = bbox.extents(list(doc.modelspace()), fast=True)
        diag = (ext.size.x + ext.size.y) if ext.has_data else 20000.0
        width_snap = _bump_zero_widths(doc, eps=max(diag * 0.0005, 0.5))
        # 图幅英寸尺寸按长边目标像素自适应，避免「图幅越大、笔画越虚」
        size_inches = _auto_size_inches(doc, dpi, target_long_px=target_long_px)
        px_per_dsu = _px_per_dsu(ext, size_inches, dpi)
        temp_entities = [e for _, e in temp_ents]
        # 文字描边加粗：三种配色共用（同一批实体，抬高 / 还原各一次）
        lw_snap = _bump_text_stroke(doc, px_per_dsu, dpi, temp_entities)
        try:
            for fname, bg, policy in variants:
                path = parent / fname
                cfg = draw_config.Configuration(color_policy=policy)
                # 固定随机种子：HATCH 图案的 jiggle 走 random.random()，
                # 不播种则剖面线相位随进程变化、跨运行无法逐像素复现。
                # 每个变体渲染前都重置，三种配色得到同一相位（叠图时对得齐）。
                random.seed(RENDER_RANDOM_SEED)
                qsave(doc.modelspace(), str(path), bg=bg, dpi=dpi, config=cfg,
                      size_inches=size_inches)
                made.append(path)
        finally:
            _restore_lineweights(lw_snap)
    finally:
        if width_snap:
            _restore_widths(width_snap)
        _restore_texts(text_edits, temp_ents)
    return made


def overview_png(doc, src, dpi=PNG_DPI, which=None, out_dir=None):
    """把**未拆分的整张源图**渲染成 PNG —— 「拆分前」的对照基准。

    产物: <源图主干>_整图_<配色>.png
      * 不给 out_dir → 落在源图旁边（手工用时和源图放一起最好找）；
      * 给了 out_dir → 落在那个目录（作业里指到 02_overview/，位置**固定**，
        agent 拿路径直接读，不必搜索）。

    用法（在 main.py 里）:
        python main.py "某项目总图.dwg" --overview
        python main.py "../01_source/x.dwg" --overview --out=../02_overview

    ⚠️ 调用方必须先确保源图是**标准图元**（没有 ACAD_PROXY_ENTITY）。否则渲出来的是
    只有轴线/图名/‰ 的残图，拿它跟拆分结果对比会得出"拆分把墙都弄丢了"的错误结论 ——
    main.py 的 --overview 分支会先自检并拦下。
    """
    src = Path(src)
    pool = Path(out_dir) if out_dir else src.parent
    return export_png_pair(doc, pool / (src.stem + OVERVIEW_SUFFIX),
                           dpi=dpi, which=which,
                           target_long_px=PNG_OVERVIEW_LONG_PX)
