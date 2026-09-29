# -*- coding: utf-8 -*-
"""
split_dwg.py — 把含多张示意图的 DWG 按空间聚类拆分成多个独立 DWG。

用法:
    python split_dwg.py <输入dwg> <输出目录> [间距阈值] [映射json] [--clean] [--title-mode=...]

可选参数:
    --allow-proxy  源图含天正代理对象（没导出成 T3）时也**强行**拆。
                   默认是拦下并报错，因为硬拆只能得到缺内容的残图，
                   见 count_proxy_entities 与 README「拆分对象」。
    --clean        先清空输出目录（换过命名规则后必须加，否则新旧产物混杂）
    --title-mode=keep|strip|both|auto
                   导出时**图名文字**的取舍（默认 keep）：
                     keep  保留图名 —— 与 CAD 原图一致
                     strip 剔除图名 —— 图名常比图形本身还大（『城市疏捞队门卫室』
                           h=7234，把包围盒横向撑到 2.70×），渲染按整个包围盒定
                           像素，图形被挤成一小块；剔除后图幅只由图形决定，细节
                           铺满全幅。**只摘图名，指北针/尺寸线/构件全部保留。**
                     both  两版都出，无图名版文件名追加后缀 `_无图名`
                     auto  识别到图名就剔除（图名是标注不是内容；命名仍用它）。
                           实测 example-2 全部 40 图命中剔除此列

拆分逻辑:
    1. 全部实体算 bbox → 连通聚类（margin 膨胀合并）
    2. 图名小簇（图框正下方/图框内的独立文字）按位置合并进所属图框簇
    3. 给每个簇打一个分类标签（倾斜观测/建筑平面/结构/未分类）—— **只写进 json
       的 category 当元数据**，不再决定目录（见下方「输出结构」）
    4. 每簇用 ezdxf Importer 复制到新文档 → 写 Binary DXF（不过 ODA）
    5. 渲染 PNG（export_png_pair）—— 渲染前会临时替换 CJK 字体，渲染后还原

输出结构（所有入口统一）:
    <输出根>/
    ├─ 01/
    │   ├─ <图名>.dxf
    │   ├─ <图名>.json                     ← 该图元数据，含 category / title_missing
    │   └─ <图名>_白底黑白.png / _黑底彩色.png
    ├─ 02/ …
    └─ split_report.json                   ← 全部簇的汇总（batch_t3 兼作完成标记）

    **一张图一个序号目录**（1 起、两位补零），不按 建筑/倾斜/结构/未分类 分类建目录：
      * 序号就是拆图顺序（簇按 x 从左到右排），插进 Word 时按目录名排序即阅读顺序；
      * 分类是"一张图属于哪类"的**判断**，判断可能错；序号是**事实**，错不了。
        把判断留在 json 的 category 里，路径上不出现判断结果。

命名（宁缺毋滥，所有入口统一）:
    * **有图名** → 文件名就是图名。图名来源优先级：
        ① 簇内**唯一**一条中文图名候选（形态判据 is_title_text）
        ② **唯一**一条并入的图名小簇
        ③ 簇内**只有一条**关键词标签（图上真实写着的字，如『二层平面布置图』，
           它可能落在 DIM_* 图层上、形态判据认不出）
    * 多条候选/多条标签**严禁拼接** —— 曾产出 300+ 字的怪物文件名（东湖樱花园
      『曲院樱艳』有 24 条『平面图/剖面图/轴立面图』）。一律退回序号。
    * **没有图名** → 文件名 = 目录序号（`01/01.dxf`），json 里 title_missing=true。
      ⚠️ 绝不从内容特征（关键词/‰）猜楼名：本工程图名多是**纯楼名**
      （『吕家湾门房』『李家墩食堂一层』），一个关键词都不含，靠关键词推断
      （旧逻辑的『平面示意图N』『倾斜观测示意图』）经常是错的。

第 0 步：格式自检（天正代理对象）—— 比聚类还早:
    ezdxf / ODA **读不到天正自定义实体**（墙体/门窗/轴号/尺寸/房间填充），它们
    读出来是 ``ACAD_PROXY_ENTITY``，几何只在天正对象内部。所以读图后立刻
    ``count_proxy_entities`` 自检，数到 > 0 就说明**这张图没导出成天正3（T3）**：
      * 默认 → 抛 T3RequiredError 拦下，提示用户回**天正**做一次图形导出
        （T3 导出会把显示缓存一起写出，第 -1 步就能把几何炸回来）。
      * ``allow_proxy=True``（--allow-proxy）→ 跳过拦人直接拆（产物会缺内容）。
    本管线**不做** DWG → T3 转换（不调天正 GUI 自动化），理由见下面的常量区注释；
    也不用文件名猜格式 —— 判据是内容里有没有读不到的代理对象。
    详见 count_proxy_entities / has_tch_classes / T3RequiredError 与 README「拆分对象」。
"""
import json
import math
import os
import re
import sys
from collections import Counter
from pathlib import Path

# stdout 统一 UTF-8。必须用 reconfigure（幂等、不替换对象）——
# 若写 sys.stdout = TextIOWrapper(...)，main.py 把本模块与其他模块
# （render_png 等）import 进同一进程时会重复包装，旧 wrapper 被 GC
# 时关闭底层 buffer → 之后任何输出报 "I/O operation on closed file"。
sys.stdout.reconfigure(encoding="utf-8")

import ezdxf
from ezdxf.addons import odafc
from ezdxf.addons.importer import Importer
from ezdxf import bbox

from png_export import export_png_pair, ensure_shx_support, PNG_DPI

# ODA FileConverter 路径：环境变量 ODA_EXE 优先，未设置时用下面的默认值（换机改这里或设环境变量）
ODA_EXE = os.environ.get("ODA_EXE",
                         r"C:\Program Files\ODA\ODAFileConverter 27.1.0\ODAFileConverter.exe")
ezdxf.options.set("odafc-addon", "win_exec_path", ODA_EXE)

# ---- 本管线**不做**「DWG → 天正3」转换（2026-09-23 用户定，别再往回加）----------
# 只处理**已经是天正3（T3）格式**的图：T3 导出时天正会把显示缓存一起写出，第 -1 步
# 能把缓存炸成基本图元，Python 侧不缺内容。没导出成 T3 的图（真天正图 / 代理残影）
# 一律不转、不问、不自动处理 —— 由第 0 步拦下、让用户回天正导出一次。
#
# 为什么不做（用户原话级理由）：调用天正 GUI 自动化这件事**既难做又难验** ——
#   ① 难做：天正没有命令行接口，只能靠 GUI 自动化驱动（AutoHotkey 模拟键盘，
#      单张几秒到几分钟、运行期间独占键鼠）；
#   ② 难验：产物对不对只能靠"代理对象数有没有减少"这类间接指标反推，转不出来
#      时管线里外都拿不到可判定的失败信号（2026-09-23 实测：真天正图上脚本
#      根本不执行，7 次实验全败，连"为什么失败"都定位不到）；
#   ③ 不值：而用户自己回天正做一次图形导出只要几秒，是**很容易**的一步。
# 所以把它挡在管线外，比塞进来可靠得多。历史实现留在 G:\Code\cad-t3-auto-export
# （独立的转换工具，可单独用），本仓不再持有任何调用它的钩子。
#
# `_t3_auto` 是那套历史转换器的产物命名（`<源图主干>_t3_auto.dwg`）。本仓不再产出
# 它，但要**认得出**它 —— 老作业目录里可能还留着这种文件，挑源图时必须排除，
# 否则同一个目录会冒出两张 dwg，被当成两个源各拆一遍、产物互相覆盖（见
# is_convert_output / prefer_originals）。
T3_OUT_SUFFIX = "_t3_auto"      # 历史转换器产物后缀：<源图主干>_t3_auto.dwg

# 关键词判据用的词表 —— 只用来找「图名类文字」（喂给 category 与命名兜底），
# 不再决定目录名（目录一律是序号，见模块 docstring 的「输出结构」）。
LABEL_KEYWORDS = ("平面", "布置图", "倾斜", "观测", "示意图", "立面", "剖面", "屋顶")

# 输出根下每个簇一个**序号目录**，1 起、两位补零（01、02…99，超 99 自动变 100）。
# 补零是为了资源管理器/Word 按名排序 = 拆图顺序，不出现 1,10,2 这种乱序。
SEQ_WIDTH = 2


def seq_name(k):
    """簇下标（0 起，已按 x 从左到右排）→ 序号目录名，如 0 → '01'。"""
    return f"{k + 1:0{SEQ_WIDTH}d}"

# ---- 图名（文件名）识别 ---------------------------------------------------
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
# 纯数字 / 字母 / 轴号 / 尺寸串 形态 —— 不可能是图名
_CODE_LIKE_RE = re.compile(r"^[\s0-9A-Za-z\.\-\+×xX*°%‰/\\#()（）]+$")
# 指北针、剖切符号等单字，不能当图名
_TITLE_STOPWORDS = {"北", "南", "东", "西", "上", "下", "左", "右", "剖"}
TITLE_MIN_H = 200.0     # 字高小于此值多为标注，不作图名候选
TITLE_MAX_LEN = 40      # 超长多为说明文字
TITLE_GAP = 8000.0      # 图名簇「贴在图框正下方」允许的最大垂直间隙（DSU）
TITLE_CLUSTER_MAX_N = 12    # 图名小簇的实体数上限


def _text_of(ent):
    """取 TEXT / MTEXT 的文字内容；其他类型返回 None。"""
    t = ent.dxftype()
    if t == "TEXT":
        return ent.dxf.text or ""
    if t == "MTEXT":
        return ent.text or ""
    return None


def _text_height(ent):
    """TEXT 用 height，MTEXT 用 char_height（MTEXT 没有 height 属性）；
    非文字实体返回 0。"""
    t = ent.dxftype()
    if t == "TEXT":
        return float(ent.dxf.get("height", 0) or 0)
    if t == "MTEXT":
        return float(ent.dxf.get("char_height", 0) or 0)
    return 0.0


def entity_bbox_raw(ent):
    try:
        b = bbox.extents([ent], fast=False)
        if b.has_data:
            return (b.extmin.x, b.extmin.y, b.extmax.x, b.extmax.y)
    except Exception:
        pass
    return None


class T3RequiredError(RuntimeError):
    """源图里存在天正自定义实体的代理对象 → 必须先导出成天正3（T3）再来拆。

    单独定义一个异常类型，是为了让各入口能把它和「真失败」分开统计：
    这不是工具坏了，是**源文件格式不对**，得回天正做一步图形导出。
    """


# 代理对象（天正自定义实体在非天正环境下的残影）。
# ⚠️ 这是**唯一**一个「源格式不对」的可靠信号，而且读图时顺手就能数，
# 不额外花时间 —— 详见 README「拆分对象」一节。
PROXY_TYPE = "ACAD_PROXY_ENTITY"


def count_proxy_entities(doc):
    return sum(1 for e in doc.modelspace() if e.dxftype() == PROXY_TYPE)


def explode_proxy_graphics(doc):
    """把带显示缓存的天正代理对象**原位炸开**成基本图元，返回 (总数, 炸开数)。

    天正3格式的兼容机制就是「显示缓存」（DXF 160 组码 + 310 二进制块，本质是
    AutoCAD 的矢量元数据）：正常导出的天正3文件，每个天正对象都带一份
    「它被画成什么样」，没装天正的 CAD 靠它在屏幕上显示。ezdxf 内置了
    解码器（proxygraphic.ProxyGraphic），``virtual_entities()`` 能把缓存炸成
    LINE / ARC / TEXT 等基本图元 —— 炸开后墙体门窗就进了聚类、导出与渲染，
    **Python 侧不再缺内容**。

    ⚠️ 只炸「缓存可解码」的对象：缓存是空壳的（实测天正直接保存的图全是
    8 字节空壳，几何只在天正私有数据里）炸不出任何东西，保留原样 ——
    缺多少由 verify_split 的 E0 如实报告，这里不猜。
    """
    msp = doc.modelspace()
    total = expanded = 0
    for e in list(msp.query("ACAD_PROXY_ENTITY")):
        total += 1
        try:
            ents = list(e.virtual_entities())
        except Exception:
            ents = []
        if not ents:
            continue      # 缓存空壳：炸不出任何东西。⚠️ 不能用 e.explode()——
                          # 它对空缓存也会 destroy 源对象，会把对象直接删掉
        for v in ents:
            msp.add_entity(v)
        e.destroy()
        expanded += 1
    return total, expanded


def has_tch_classes(doc):
    """文档里有没有注册天正类（CLASSES 段出现 TCH_*）。

    只用来**区分两种「没导出成 T3」的情形、把报错提示写准**：
      * 有 TCH 类 → 真天正图（天正原生保存，带完整的天正私有数据），提示
        「回天正导出为天正3」；
      * 没有 TCH 类 → 别的程序留下的代理残影（源图多半已损坏/未导出），
        提示「回原始出图工具重新导出」。
    ⚠️ 它**不再**用于「要不要自动调转换器」——本管线不做 T3 转换（见常量区注释）。
    """
    try:
        return any(getattr(c.dxf, "name", "").upper().startswith("TCH")
                   for c in doc.classes)
    except Exception:
        return False


def read_drawing(path):
    """读一张图成 ezdxf 文档 —— **dwg 与 dxf 都支持**。

    dwg 走 ODA FileConverter（ezdxf 自己读不了 dwg），dxf 直接读。
    两条路都先注册 SHX 字体 —— 文字实体的包围盒依赖字体度量，注册晚了
    （哪怕只是"读图后顺手求个 bbox"）会让 gbenor.shx 以 TTF 替身被缓存，
    之后注册也不重算（详见 png_export._ensure_shx_support 的说明）。

    抽成函数是为了让「只渲整图」等入口不必各自 import odafc、再重复一遍字体
    注册的正确顺序。
    """
    p = Path(path)
    ensure_shx_support()
    if p.suffix.lower() == ".dxf":
        return ezdxf.readfile(str(p))
    return odafc.readfile(str(p))


def is_convert_output(p):
    """这个 dwg 是不是**历史** T3 转换器的产物（不是原始图）。

    本仓已不再产出这种文件（见常量区注释），但老作业目录里可能还留着
    `<源图主干>_t3_auto.dwg` —— 它和源图**并排**落在同一个目录里，挑源图时
    必须排除（`split_and_render._direct_dwgs` / `batch_t3.collect_sources`），
    否则同一个 t3 目录会冒出两张 dwg，被当成两个源各拆一遍、产物互相覆盖。
    """
    return Path(p).stem.lower().endswith(T3_OUT_SUFFIX)


def prefer_originals(dwgs):
    """从 dwg 列表里挑源图：优先排除转换器产物。

    兜底：目录里**只有**转换产物时（用户手工删了原图）原样返回 ——
    别把"其实有图可拆"误判成"这个目录里没有图"。
    """
    orig = [p for p in dwgs if not is_convert_output(p)]
    return orig or list(dwgs)


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


def cluster_entities(msp, margin):
    ents = []
    for ent in msp:
        bb = entity_bbox_raw(ent)
        if bb is not None:
            ents.append((ent, bb))
    n = len(ents)
    order = sorted(range(n), key=lambda i: ents[i][1][0])
    dsu = DSU(n)
    for idx, i in enumerate(order):
        _, (x1i, y1i, x2i, y2i) = ents[i]
        for jdx in range(idx + 1, len(order)):
            j = order[jdx]
            bx1, by1, bx2, by2 = ents[j][1]
            if bx1 - x2i > margin:
                break
            if (x1i - margin <= bx2 and bx1 <= x2i + margin and
                    y1i - margin <= by2 and by1 <= y2i + margin):
                dsu.union(i, j)
    groups = {}
    for i, pair in enumerate(ents):
        groups.setdefault(dsu.find(i), []).append(pair)
    return list(groups.values())


def is_label_text(ent):
    """关键词判据：文字里含 LABEL_KEYWORDS 之一。

    只用于**分文件夹**（倾斜观测/建筑/结构/未分类），不用于取文件名。
    """
    t = _text_of(ent)
    if t is None:
        return False
    t = t.strip()
    return bool(t) and any(k in t for k in LABEL_KEYWORDS)


def is_title_text(ent):
    """形态判据：判断一条文字是否「可能是图名」，不依赖任何关键词。

    背景：本工程的图名常是纯楼名（『吕家湾门房』『十三支沟门房（西）』
    『李家墩食堂一层』），不含任何分类关键词；用关键词筛图名会把它们全部丢掉。
    故另立形态判据，用于取文件名。

    约束：含中文、长度 1~40、字高不低于标注级、不是纯数字/轴号/尺寸串、
    不是「北/南/东/西/上/下」等指北针剖切单字、不在 DIM_* 标注图层。
    """
    t = _text_of(ent)
    if t is None:
        return False
    t = t.strip()
    if not t or not _CJK_RE.search(t):
        return False
    if _CODE_LIKE_RE.match(t) or t in _TITLE_STOPWORDS:
        return False
    if len(t) > TITLE_MAX_LEN:
        return False
    if _text_height(ent) < TITLE_MIN_H:
        return False
    if (ent.dxf.layer or "").upper().startswith("DIM"):
        return False   # 指北针 / 尺寸标注图层不是图名
    return True


def classify_category(labels, titles, members):
    """判断簇属于哪一类，返回分类名（**只写进 json 的 category，不当目录名**）。

    labels:  关键词命中的文字
    titles:  识别到的图名（可能为空）
    members: [(ent, bbox)]

    ⚠️ 这是**判断**，判断可能错；所以结果只进元数据，不进路径 ——
    路径上是「事实」（序号 = 拆图顺序）。
    """
    joined = "".join(labels)
    title_join = "".join(titles)
    content_texts = [_text_of(e) or "" for e, _ in members]
    has_permille = any("‰" in t for t in content_texts)

    # 无任何图名、且含 ‰（倾斜值）→ 倾斜观测图（图中不写图名，只能靠内容特征）
    if "倾斜" in joined or "观测" in joined or (
            not title_join and not joined and has_permille):
        return "倾斜观测示意图"
    if "结构" in joined:
        return "结构平面示意图"
    if joined:
        return "建筑平面示意图"
    # 识别到图名但图名不含分类关键词 —— 无法判断建筑/结构，只能记「未分类」
    return "未分类示意图"


def _rect_gap(a, b):
    """两个矩形 (x1,y1,x2,y2) 之间的距离；相交时为 0。"""
    dx = max(a[0] - b[2], b[0] - a[2], 0.0)
    dy = max(a[1] - b[3], b[1] - a[3], 0.0)
    return math.hypot(dx, dy)


def _rect_area(r):
    return max(r[2] - r[0], 0.0) * max(r[3] - r[1], 0.0)


def _title_scale(c):
    """图名包围盒相对「非图名内容」的放大倍数。

    >1 表示图名比图形本身还宽或还高 —— 此时 `_auto_size_inches` 会按整个包围盒
    给长边定像素，图名越大图形越小（实测『城市疏捞队门卫室』图名 h=7234，
    把包围盒横向撑到 2.70×，轴网/构件只剩 ~890×490px）。
    这类图用 --title-mode=strip 收益最明显。
    """
    tids = {id(e) for e in c.get("title_ents", [])}
    if not tids:
        return 0.0
    geom = [bb for e, bb in c["members"] if id(e) not in tids]
    ttl = [bb for e, bb in c["members"] if id(e) in tids]
    if not geom or not ttl:
        return 0.0
    gw = max(b[2] for b in geom) - min(b[0] for b in geom)
    gh = max(b[3] for b in geom) - min(b[1] for b in geom)
    tw = max(b[2] for b in ttl) - min(b[0] for b in ttl)
    th = max(b[3] for b in ttl) - min(b[1] for b in ttl)
    return max(tw / max(gw, 1e-9), th / max(gh, 1e-9))


def build_clusters(doc, margin, overrides=None):
    """聚类 → 图名合并 → 定序号 → 命名。

    返回 (boxes, fragments)：
        boxes     按 x 排序、已定好 seq/name 的图簇列表
        fragments 判定为「文字残片」的簇（既不并入任何图框、也不单独出图）

    每个 box 包含:
        index   簇下标（0 起，按 x 从左到右）
        seq     序号目录名，如 '01'（index+1，两位补零；出图落在 <输出根>/<seq>/）
        members [(ent, bbox)]
        bbox    (x1, y1, x2, y2)
        labels  关键词命中的文字
        titles  识别到的**真图名**（命名用；可能为空 = 没识别到）
        category 分类标签（只进 json 元数据，不当目录名）
        name    文件名（不含扩展名）：有图名用图名，没有用 seq
        title_missing 未识别到图名（文件名退化成序号）

    overrides: {"簇下标": {"name": ..., "folder": ...}} 手动覆盖。
              name → 文件名；folder → 这个簇的序号目录名（少见，想手动改目录名时用）。
    """
    msp = doc.modelspace()
    groups = cluster_entities(msp, margin)

    clusters = []
    for members in groups:
        x1 = min(m[1][0] for m in members); y1 = min(m[1][1] for m in members)
        x2 = max(m[1][2] for m in members); y2 = max(m[1][3] for m in members)
        labels, cands = [], []
        for e, _ in members:
            txt = _text_of(e)
            if not txt:
                continue
            txt = txt.strip()
            if not txt:
                continue
            if is_label_text(e):
                labels.append((txt, e))
            if is_title_text(e):
                cands.append((txt, e))
        # 高置信图名 = 簇内**唯一**一条中文图名候选。
        # 多条候选时说明这是图纸内容（房间名『食堂』/道路名『慈惠街』/工程名混杂），
        # 猜错代价大于留空，故宁可不取。
        own_titles = [t for t, _ in cands] if len(cands) == 1 else []
        clusters.append({
            "members": members, "bbox": (x1, y1, x2, y2),
            "labels": labels, "title_cands": cands,
            "own_titles": own_titles,
            # 图名实体（不是文字！）—— 供 --title-mode=strip 剔除用。
            # own 侧：簇内唯一一条候选才算图名（与 own_titles 口径一致）。
            "_own_title_ents": [e for _, e in cands] if len(cands) == 1 else [],
            "_merged": [], "_merged_ents": [],
        })

    # ---- 图名小簇 / 符号残片归并 ----
    # 独立成簇、成员很少的，基本是「掉队的图名」或「贴边的符号残片」
    # （指北针 = CIRCLE+LINE，建筑轮廓残片 = LWPOLYLINE+『1F』）。
    #   阶段1：贴在图框正下方 + x 有重叠 → 并入，唯一候选者**参与命名**
    #   阶段2：中心落在图框包围盒内   → 只并入实体，不参与命名
    #          （总平面图里的『空地』『内部道路』就是这类图内标注）
    #   阶段3：离图框很近且并入后图框不会明显变大 → 只并入实体
    #   都不满足                        → 判为「文字残片」，不出图
    _TEXT_LIKE = ("TEXT", "MTEXT", "LINE", "LWPOLYLINE")
    _SYMBOL_LIKE = _TEXT_LIKE + ("CIRCLE", "ARC", "ELLIPSE", "POINT")
    boxes, small = [], []
    for c in clusters:
        n = len(c["members"])
        only_textlike = all(e.dxftype() in _TEXT_LIKE for e, _ in c["members"])
        symbol_like = all(e.dxftype() in _SYMBOL_LIKE for e, _ in c["members"])
        # 图名小簇 = 形态判据命中（纯楼名）**或**关键词判据命中。
        # 关键词那条不能省：『二层平面布置图』这类图名可能落在 DIM_* 图层上，
        # 形态判据会因图层被排除，只剩关键词能识别它。
        title_cluster = bool(c["title_cands"] or c["labels"]) and only_textlike
        symbol_frag = (not c["title_cands"] and not c["labels"]
                       and symbol_like and n <= 3)
        is_small = n <= TITLE_CLUSTER_MAX_N and (title_cluster or symbol_frag)
        (small if is_small else boxes).append(c)

    fragments = []
    for lab in small:
        lx1, ly1, lx2, ly2 = lab["bbox"]
        lcy = (ly1 + ly2) / 2.0
        lcx = (lx1 + lx2) / 2.0
        # 允许的垂直间隙：字号大的图名可能离图框远一点，但要封顶 ——
        # 否则 h=16879 的图内大字（『空地』）会「够到」好几万单位外的图框，
        # 被错认成图名，把名字污染成「…空地空地门房仓库」。
        gap = min(max(TITLE_GAP,
                      2.0 * max((_text_height(e) for e, _ in lab["members"]), default=0.0)),
                  16000.0)

        best, best_overlap = None, 0.0
        for c in boxes:                       # 阶段1：图框正下方
            ox = min(lx2, c["bbox"][2]) - max(lx1, c["bbox"][0])
            # drop = 图框底边 到 图名顶边 的垂直距离。
            # 必须**双向**限制：只卡上界的话，图框下方十万单位的『空地』
            # 也会被判成「正下方图名」，把名字污染成「空地」。
            drop = c["bbox"][1] - ly2
            if ox > best_overlap and 0.0 <= drop <= gap:
                best, best_overlap = c, ox
        named = best is not None
        if best is None:                      # 阶段2：落在图框内部
            for c in boxes:
                bx1, by1, bx2, by2 = c["bbox"]
                if bx1 <= lcx <= bx2 and by1 <= lcy <= by2:
                    best = c
                    break
        if best is None and boxes:            # 阶段3：就近并入，但图框不许明显变大
            near = min(boxes, key=lambda c: _rect_gap(c["bbox"], lab["bbox"]))
            nb = (min(near["bbox"][0], lx1), min(near["bbox"][1], ly1),
                  max(near["bbox"][2], lx2), max(near["bbox"][3], ly2))
            a0, a1 = _rect_area(near["bbox"]), _rect_area(nb)
            if a0 <= 0 or a1 <= a0 * 1.5:
                near["bbox"] = nb
                best = near

        if best is not None:
            best["members"].extend(lab["members"])
            best["labels"].extend(lab["labels"])
            # 只有「唯一一条候选」的小簇才够格贡献图名；多条候选说明它自身
            # 就是一坨图纸内容（房间名等），拿它命名会得到「办公楼内部道路」。
            if named and len(lab["title_cands"]) == 1:
                best["_merged"].extend(t for t, _ in lab["title_cands"])
                best["_merged_ents"].extend(e for _, e in lab["title_cands"])
                best["bbox"] = (min(best["bbox"][0], lx1), min(best["bbox"][1], ly1),
                                max(best["bbox"][2], lx2), max(best["bbox"][3], ly2))
        else:
            fragments.append(lab)

    boxes.sort(key=lambda c: c["bbox"][0])

    overrides = overrides or {}
    for k, c in enumerate(boxes):
        # 命名优先级：簇自身唯一图名 > 唯一一条并入图名 > 唯一一条关键词标签。
        # own 优先是有意的：掺进来的外部文字再多，也不该盖掉图上自带的图名。
        merged = c["_merged"]
        titles = list(c["own_titles"]) or (merged if len(merged) == 1 else [])
        label_texts = [t for t, _ in c["labels"]]
        if not titles and len(label_texts) == 1:
            # 退一步认「簇内**只有一条**关键词标签」—— 那是图上真实写着的字
            # （『二层平面布置图』常落在 DIM_* 图层，形态判据认不出、关键词能认）。
            # ⚠ 多条标签**严禁**拼接：总图里几十条『平面图/剖面图/屋顶平面图』
            # 拼起来会得到 300+ 字的怪物文件名（实测东湖樱花园『曲院樱艳』）。
            titles = [label_texts[0]]
        ov = overrides.get(str(k), {})
        seq = ov.get("folder") or seq_name(k)
        # 文件名：有图名用图名，没有就用序号（= 它所在的目录名）。
        # 不做重名去重 —— 一簇一个序号目录，同名图各在自己目录里，不会互相覆盖；
        # 硬加 '2'/'3' 后缀反而把真图名改掉（『倾斜图』→『倾斜图2』）。
        name = ov.get("name") or (titles[0] if titles else seq)
        name = name.replace("/", "_").replace("\\", "_")
        c["index"], c["seq"] = k, seq
        c["category"] = classify_category(label_texts, titles, c["members"])
        c["name"] = name
        c["titles"] = titles
        c["title_missing"] = not titles
        # ---- 图名实体（供 --title-mode=strip 剔除）----
        # 口径与命名**严格一致**：名字取自哪条文字，就剔哪条文字。
        #   ① 名字 = 真图名        → 剔 own / merged 的图名实体
        #   ② 名字 = 唯一一条关键词标签（如『二层平面布置图』落在 DIM_* 图层，
        #      形态判据认不出、只能靠关键词兜底）→ 剔那条标签实体
        #      ⚠️ 这一档不能漏：漏了就会出现「titles 有值但 title_entities=0」
        #      —— 名字叫『倾斜图』却不剔它，--title-mode=strip/auto 静默失效
        #   ③ 名字退化成序号（根本没识别到图名）→ 一条都不剔（auto 的语义是
        #      「识别到图名才剔」，没识别到就不动原图）
        # 一道保险：指北针（『北/南/东/西…』单字）永不剔除 —— 它常在轴网之外，
        # 但属于必须保留的内容。
        if titles:
            t_ents = [e for e in (c["_own_title_ents"] or c["_merged_ents"]
                                  or [e for _, e in c["labels"]])]
        else:
            t_ents = []
        c["title_ents"] = [e for e in t_ents
                           if (_text_of(e) or "").strip() not in _TITLE_STOPWORDS]
        c["title_scale"] = _title_scale(c)
    return boxes, fragments


# ---- 导出时的图名去留 -----------------------------------------------------
# keep  保留图名文字（与 CAD 原图一致）
# strip 剔除图名文字 —— 图名常比图形本身还大（如『城市疏捞队门卫室』h=7234，
#       把包围盒横向撑到 2.70×），渲染时按整个包围盒定像素，图形就被挤成一小块。
#       剔除后图幅只由图形决定，细节铺满全幅。**指北针、尺寸线一律保留。**
# both  两版都出，无图名版文件名追加后缀 _无图名
TITLE_MODE_KEEP = "keep"
TITLE_MODE_STRIP = "strip"
TITLE_MODE_BOTH = "both"
TITLE_MODE_AUTO = "auto"     # 识别到图名就剔除（2026-09-15 起；旧规则是仅当
                             # title_scale > 1.0 才剔，那张『办公楼一层』
                             # title_scale=0.38 被漏掉，用户确认改为全剔）
TITLE_NAME_SUFFIX = "_无图名"


def _title_mode_from_args(argv):
    mode = TITLE_MODE_KEEP
    for a in argv:
        if a.startswith("--title-mode="):
            mode = a.split("=", 1)[1].strip()
    if mode not in (TITLE_MODE_KEEP, TITLE_MODE_STRIP,
                    TITLE_MODE_BOTH, TITLE_MODE_AUTO):
        raise SystemExit(
            f"--title-mode 只接受 keep / strip / both / auto，收到 {mode!r}")
    return mode


def _export_cluster(doc, ents, dest, which=None):
    """把 ents 复制进一个新文档 → 渲染 PNG → 导出**二维交换格式**，返回 dest。

    抽成函数是因为 --title-mode=both 要对同一簇导出两版（带图名 / 去图名）。
    which: 只渲染指定配色（png_export.variants_from_tokens 的返回值），None=两张全出。
    """
    # 复制到新文档（编码沿用源文件，避免中文被写成 \U+ 转义）
    target = ezdxf.new("R2000")
    target.encoding = doc.encoding or "gbk"
    importer = Importer(doc, target)
    importer.import_entities(ents)
    importer.finalize()
    # 关键：用源文件的样式表覆盖目标文档同名样式。
    # ezdxf.new 自带 Standard(txt 字体)，Importer 遇重名样式会跳过，
    # 导致源文件的 gbenor/gbcbig 等国标字体丢失、文字渲染变宽产生交叠。
    for src_style in doc.styles:
        if src_style.dxf.name in target.styles:
            tgt_style = target.styles.get(src_style.dxf.name)
            tgt_style.dxf.font = src_style.dxf.font
            tgt_style.dxf.bigfont = src_style.dxf.bigfont
            tgt_style.dxf.width = src_style.dxf.get("width", 1.0)
        else:
            # ezdxf >=1.3 起 TextstyleTable.add() 只接受 font=，bigfont/width
            # 改为创建后再设属性（旧版曾支持 bigfont=/width= 关键字，已移除）
            new_style = target.styles.add(src_style.dxf.name,
                                          font=src_style.dxf.font)
            new_style.dxf.bigfont = src_style.dxf.bigfont
            new_style.dxf.width = src_style.dxf.get("width", 1.0)

    dest.parent.mkdir(parents=True, exist_ok=True)
    # PNG 预览图先渲染（中文字体替换在函数内自动还原，不污染导出的文档）
    try:
        for p in export_png_pair(target, dest, dpi=PNG_DPI, which=which):
            print(f"    PNG: {p.name}")
    except Exception as e:
        print(f"    PNG 导出失败: {e}")
    # 产物用 Binary DXF，由 ezdxf 直接写盘 —— **不过 ODA**。
    # 为什么不写 DWG：odafc.export_dwg 是「写盘」侧唯一的外部依赖，而下游拿 Python
    # 读图时本来就要转成 dxf 一道。换成 Binary DXF 之后外部依赖只剩「读 dwg 源图」，
    # 且体积接近 DWG（ASCII DXF 会大 2~5 倍）。
    try:
        target.saveas(dest, fmt="bin")
    except Exception as e:
        print(f"    产物导出失败（文件被占用?）: {e}")
    return dest


def run_split(dwg_path, out_root, margin=3000.0, map_file=None, *,
              clean=False, title_mode=TITLE_MODE_KEEP, png_which=None,
              allow_proxy=False):
    """执行一次完整的「拆分 + 渲染」，返回 ``(簇数, 文字残片数, report)``。

    抽成函数是为了让 ``split_and_render.py`` / ``batch_t3.py`` 复用；
    CLI 行为由各入口解析参数后转调本函数。

    输出结构（所有入口一致，没有开关）::

        out_root/
        ├─ 01/<图名>.dxf + <图名>.json + 两张 PNG
        ├─ 02/…
        └─ split_report.json

    为什么固定成「序号目录」而不是「分类目录」：见模块 docstring。要点是
    序号=拆图顺序（订单即阅读顺序），分类只是可能出错的判断，所以只进 json。

    allow_proxy=False（默认）：遇到读不到几何的天正代理对象直接拦下报错。
        判型见第 0 步（run_split 体内）：有代理对象 = 这张图没导出成天正3（T3），
        抛 T3RequiredError 提示用户回天正导出一次（本管线不做 T3 转换）。
        allow_proxy=True 才照拆（产物会缺墙体/门窗等内容）。
        抛异常会发生在写 split_report.json **之前**，所以批量模式下的断点续传
        标记不会落下，修好之后再跑会自动重拆，不用 --force。
    """
    dwg_path = Path(dwg_path)
    out_root = Path(out_root)
    overrides = (json.loads(Path(map_file).read_text(encoding="utf-8"))
                 if map_file else {})

    # --clean: 先清空输出目录。序号是每轮按簇下标**现算**的 —— 上一轮的 03/ 可能是
    # 另一张图，不清掉就会和新产物混在一起，且无法从目录名分辨新旧。
    if clean and out_root.exists():
        import shutil
        shutil.rmtree(out_root)
        print(f"已清空输出目录: {out_root}")
    elif out_root.exists() and any(out_root.iterdir()):
        print(f"注意: 输出目录非空（{out_root}）。序号目录每轮按簇下标重新编号，"
              f"历史产物不会自动消失；建议加 --clean 清空重跑。")

    print(f"读取: {dwg_path}")
    print(f"图名策略: {title_mode}" + {
        TITLE_MODE_STRIP: "（剔除图名文字，指北针/尺寸线保留）",
        TITLE_MODE_BOTH: "（带图名 + 无图名 两版）",
        TITLE_MODE_AUTO: "（识别到图名就剔除）",
    }.get(title_mode, "（与 CAD 一致）"))
    # 关键顺序：必须在 build_clusters 之前注册 SHX 字体目录。
    # build_clusters → cluster_entities 会对全部实体（含 TEXT）求 bbox，
    # 触发 ezdxf 的文字度量；若此时 SHX 目录尚未注册，gbenor.shx 会以回退
    # 字体解析并被缓存，之后注册也不重算 → 数字被放大、字形变空心轮廓，
    # 倾斜观测的 ①② 撑破圆圈，与原图对不上。
    ensure_shx_support()
    doc = read_drawing(dwg_path)

    # ---- 第 -1 步：显示缓存炸开 ---------------------------------------------
    # 必须在第 0 步之前：带可解码显示缓存的天正对象（正常导出的天正3文件），
    # 炸开后墙体门窗就是标准图元，聚类/导出/渲染全都能看到 —— Python 侧不缺内容。
    # 缓存空壳的（天正直接保存的图）炸不出东西，保留原样，交第 0 步与 E0 处理。
    n_proxy_all, n_exploded = explode_proxy_graphics(doc)
    if n_exploded:
        print(f"  ✓ 从显示缓存炸开 {n_exploded}/{n_proxy_all} 个天正对象"
              f"（炸成基本图元，进入拆分）")

    # ---- 第 0 步：格式自检（天正代理对象）------------------------------------
    # 必须在聚类之前 —— 先搞清楚图里有什么、缺什么，别让用户事后才发现产物缺内容。
    #
    # 判据是**内容**（读不到的代理对象有几个），不是文件名 —— 文件名带不带 _t3 只能
    # 当提示，不能当依据（用户手工改名、导出时改了名都会骗过它）。
    #
    # 有代理对象 = 这张图没导出成天正3（T3），几何只在天正对象内部，ezdxf/ODA 读
    # 不到。本管线**不做** T3 转换（见常量区注释：调天正 GUI 既难做又难验，而用户
    # 自己回天正导出一次只要几秒），所以默认**拦下并让用户去转**，不猜、不试。
    n_proxy = count_proxy_entities(doc)
    if n_proxy:
        n_all = len(doc.modelspace())
        why = (f"源图里有 {n_proxy}/{n_all} 个实体是天正自定义实体"
               f"（{PROXY_TYPE}）—— 它们的几何只存在于天正对象内部，"
               f"ezdxf 读不到，拆出来的图会缺墙体/门窗等内容。")
        if allow_proxy:
            print(f"⚠️ {why}")
            print("⚠️ 按 --allow-proxy 继续 —— 拆出来的图会缺上述内容，别当正常产物。")
        elif has_tch_classes(doc):
            # 带 TCH_* 类 = 真天正图（天正原生保存，天正私有数据完整）。它必须
            # 在**天正里**走一次导出才能拿到可读副本 —— 见下方提示语。
            raise T3RequiredError(
                why + "这是一张**真天正图**（CLASSES 段带 TCH_* 类定义）。\n"
                "    请先在**天正**里把它导出为天正3（T3）格式再来拆：\n"
                "      天正命令行敲 TSaveAs（或菜单『图形导出』）→ 保存类型选"
                "『天正3』→ 导出（这一步只要几秒；导出后文件名通常带 _t3 后缀）。\n"
                "    ⚠️ 本工具**不做**这个转换：驱动天正 GUI 既难做又难验"
                "（转不出来时连失败原因都拿不到），交给你点一次更可靠。\n"
                "    确实要照拆这张原图就加 --allow-proxy。")
        else:
            # 没有 TCH 类却带代理 = 别的程序（非天正）留下的代理残影，源图多半
            # 已损坏或来自其它 CAD 软件 —— 同样不猜、不转，让用户回源头重出。
            raise T3RequiredError(
                why + "图里没有天正类定义，是**别的程序留下的代理残影**"
                "（源图出口时就丢了原始对象）。\n"
                "    请回**原始出图工具**重新导出这张图（导出成天正3 / 标准"
                "图元格式），确保新图读不到代理对象再来拆。\n"
                "    确实要照拆这张图就加 --allow-proxy。")

    clusters, fragments = build_clusters(doc, margin, overrides)

    report = []
    for c in clusters:
        k, seq, name = c["index"], c["seq"], c["name"]
        ents_all = [e for e, _ in c["members"]]
        # 图名实体（见 build_clusters 的 title_ents），strip 时从导出集合里摘掉
        tids = {id(e) for e in c["title_ents"]}
        has_title = bool(tids)
        ents_no_title = [e for e in ents_all if id(e) not in tids]

        if title_mode == TITLE_MODE_BOTH and has_title:
            jobs = [(name, ents_all, False),
                    (name + TITLE_NAME_SUFFIX, ents_no_title, True)]
        elif has_title and title_mode in (TITLE_MODE_STRIP, TITLE_MODE_AUTO):
            jobs = [(name, ents_no_title, True)]
        else:
            jobs = [(name, ents_all, False)]

        files = []
        for stem, ents, stripped in jobs:
            # 一簇一个序号目录；--title-mode=both 的两版 stem 不同（带图名 /
            # 带 _无图名 后缀），同落进这个序号目录，互不覆盖。
            dest = _export_cluster(doc, ents, out_root / seq / f"{stem}.dxf",
                                   which=png_which)
            files.append({"file": str(dest), "n_entities": len(ents),
                          "title_stripped": stripped})

        layers = Counter(e.dxf.layer for e in ents_all)
        # 打印/记录「真正要摘的图名实体」而不是命名用的 titles：
        # example-1 的『二层平面布置图』是靠关键词标签认出来的（names 走 labels），
        # 若打 titles 会显示成空，容易误判成"没识别到图名"。
        title_texts = [(_text_of(e) or "").strip() for e in c["title_ents"]]

        # sidecar 元数据：每个导出的 DWG 旁写一个同名 <名称>.json。
        # 图名（轴网下方文字）在 strip/auto 下已从 DWG/PNG 里摘掉，这里把它
        # 连同定位信息落盘，下游直接读图旁的 json，不必翻 split_report.json。
        for f in files:
            meta = {
                "name": Path(f["file"]).stem,
                "seq": seq,                         # 所在序号目录名，如 '01'
                "category": c["category"],          # 分类判断（仅元数据）
                "cluster": k,
                "titles": title_texts,              # 轴网下方图名文字（可能为空）
                # 文件名是识别到的图名，还是序号兜底？下游拿到 03.json 才知道
                # "这张没识别到图名"（文件名 03.dxf 不携带任何图名信息）。
                "title_missing": c["title_missing"],
                "title_entities": len(c["title_ents"]),
                "title_scale": round(c["title_scale"], 2),
                "title_stripped": f["title_stripped"],
                "n_entities": f["n_entities"],
                "bbox": [round(v, 1) for v in c["bbox"]],
                "source_dwg": str(dwg_path),
            }
            Path(f["file"]).with_suffix(".json").write_text(
                json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")

        info = {
            "cluster": k, "seq": seq, "category": c["category"],
            "file": files[0]["file"], "files": files,
            "n_entities": len(ents_all), "bbox": [round(v, 1) for v in c["bbox"]],
            # c["labels"]/c["titles"] 是 [(文本, 实体)] 或纯文本列表，
            # 实体无法 JSON 序列化，统一只取文本
            "labels": [t for t, _ in c["labels"]],
            "titles": list(c["titles"]),
            "title_texts": title_texts,
            "title_entities": len(c["title_ents"]),
            "title_scale": round(c["title_scale"], 2),
            "layers": dict(layers.most_common()),
        }
        report.append(info)
        warn = (f"  ⚠图名比图形大 {info['title_scale']:.1f}×" if info["title_scale"] > 1.0
                else "")
        missing = "  ← 未识别到图名，文件名用序号" if c["title_missing"] else ""
        print(f"[簇{k}] {seq}/{name}.dxf  实体={len(ents_all)}  "
              f"分类={c['category']}  图名={title_texts}{warn}{missing}")
        for f in files[1:]:
            print(f"        另存: {Path(f['file']).name}  实体={f['n_entities']}"
                  f"（已去图名）")

    # 文字残片：只含文字、且配不上任何图框 —— 不是图，不出图（旧逻辑会把它们
    # 当独立图导出，于是出现「平面示意图2 = 孤零零的『吕家湾门房』四个大字」）。
    for lab in fragments:
        x1, y1, x2, y2 = lab["bbox"]
        texts = [t for t, _ in lab["title_cands"]] or [t for t, _ in lab["labels"]]
        report.append({
            "cluster": None, "seq": None, "category": None, "file": None,
            "n_entities": len(lab["members"]),
            "bbox": [round(v, 1) for v in (x1, y1, x2, y2)],
            "labels": [t for t, _ in lab["labels"]], "titles": texts,
            "skipped": "文字残片（无图元、配不上图框）",
        })
        print(f"[跳过] 文字残片 实体={len(lab['members'])} 文字={texts}")

    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "split_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    n_skip = len(fragments)
    print(f"\n完成，共 {len(clusters)} 个图（另有 {n_skip} 个文字残片未出图）。"
          f"报告: {out_root / 'split_report.json'}")
    return len(clusters), len(fragments), report


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = {a for a in sys.argv[1:] if a.startswith("--")}
    if len(args) < 2:
        raise SystemExit(
            "用法: python split_dwg.py <输入dwg> <输出目录> [间距阈值] [映射json] "
            "[--clean] [--title-mode=keep|strip|both|auto]")
    dwg_path = Path(args[0])
    out_root = Path(args[1])
    margin = float(args[2]) if len(args) > 2 else 3000.0
    map_file = Path(args[3]) if len(args) > 3 else None
    run_split(dwg_path, out_root, margin, map_file,
              clean=("--clean" in flags),
              title_mode=_title_mode_from_args(sys.argv[1:]),
              allow_proxy=("--allow-proxy" in flags))


if __name__ == "__main__":
    main()
