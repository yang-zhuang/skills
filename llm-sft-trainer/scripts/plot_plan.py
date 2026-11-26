#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""plot_plan.py — 把第 3 步的选型画成一张图

回答一个问题：**为什么选它，以及为什么淘汰其他。**

刻意不做的三件事（都是 12_tradeoff.md 里明确反对的）：

  1. 不把显存和时间加权成一个"综合分"——那是把两个正交维度折成标量
  2. 不把估算值画得和实测一样——估算用空心标记，图例写清楚
  3. 不在"档内"排优劣——差值小于 tolerance 时提示"同档，选实现简单的"

用法：

    python scripts/plot_plan.py --plan plan.json --out plan.png
    python scripts/plot_plan.py --plan plan.json --objective time
    python scripts/plot_plan.py --plan plan.json --markdown    # 顺带输出可贴的表格

依赖：matplotlib（可选，其余脚本都是纯标准库）
    pip install matplotlib
"""

import argparse
import json
import sys
from pathlib import Path

# ---------------------------------------------------------------- 数据


def load_plan(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        plan = json.load(f)
    for k in ("constraints", "points"):
        if k not in plan:
            sys.exit(f"plan.json 缺字段: {k}")
    c = plan["constraints"]
    for k in ("max_vram_mib", "max_hours", "min_quality"):
        if k not in c:
            sys.exit(f"constraints 缺字段: {k}")
    return plan


def fill_hours(p: dict, plan: dict) -> float:
    """优先用直接给的 total_hours；否则用 s_per_step × 步数 × epoch 算。"""
    if "total_hours" in p:
        return float(p["total_hours"])
    if "s_per_step" in p and plan.get("steps_per_epoch"):
        return float(p["s_per_step"]) * plan["steps_per_epoch"] * plan.get("epochs", 1) / 3600.0
    raise SystemExit(f"方案 {p.get('name')} 既没有 total_hours，也缺 s_per_step/steps_per_epoch")


def judge(p: dict, c: dict) -> list:
    """返回违反的硬约束；空列表 = 可行。"""
    bad = []
    if p["vram_mib"] > c["max_vram_mib"]:
        bad.append(f"超显存 {(p['vram_mib']-c['max_vram_mib'])/1024:.1f} GiB")
    if p["hours"] > c["max_hours"]:
        bad.append(f"超时 {p['hours']-c['max_hours']:.1f} h")
    if p["quality"] < c["min_quality"]:
        bad.append(f"精度差 {(c['min_quality']-p['quality'])*100:.0f} pt")
    return bad


def axis_range(values, limit: float):
    """返回 (轴上限, 上限线是否在图内)。

    上限远大于所有实际值时必须放弃完整显示上限，否则条形长度趋近 0，
    **图上看不见数据等于没画**。这时把上限标在图外。
    """
    vmax = max(values) if values else 0
    if vmax <= 0:
        return limit * 1.2, True
    if vmax * 4 < limit:          # 上限 4 倍以上远，压缩得没法看
        return vmax * 2.6, False
    return max(limit * 1.24, vmax * 1.2), True


def pareto_front(pts: list) -> list:
    """在两维（显存、时长）上不被支配的点。"""
    front = []
    for p in pts:
        dominated = any(
            q is not p
            and q["vram_mib"] <= p["vram_mib"]
            and q["hours"] <= p["hours"]
            and (q["vram_mib"] < p["vram_mib"] or q["hours"] < p["hours"])
            for q in pts
        )
        if not dominated:
            front.append(p)
    return sorted(front, key=lambda x: x["vram_mib"])


def pick(feasible: list, objective: str, tol: float, slack: float, c: dict):
    """先判断目标维度是不是真的紧，再选。

    这一步是 12_tradeoff.md 3.6 的核心：**三个约束里通常只有一个是真的紧的**。
    如果目标是省显存，但最省的方案只用了上限的 40%，那显存根本不是瓶颈——
    这时按"显存最小"去选 QLoRA，等于用 15% 的时间买了一个用不上的东西。
    """
    key = "vram_mib" if objective == "vram" else "hours"
    other = "hours" if key == "vram_mib" else "vram_mib"
    # 两套标签：中文给终端/中文图，英文给没有中文字体的环境
    lz = "显存" if objective == "vram" else "时长"
    oz = "时长" if objective == "vram" else "显存"
    le = "VRAM" if objective == "vram" else "time"
    oe = "time" if objective == "vram" else "VRAM"
    limit = c["max_vram_mib"] if objective == "vram" else c["max_hours"]

    s = sorted(feasible, key=lambda p: p[key])
    best = s[0]
    ratio = best[key] / limit

    # 目标维度余量过大 → 它不紧，改按另一维选
    if ratio < slack:
        alt = min(feasible, key=lambda p: (p[other], p[key]))
        zh = (
            f"{lz}不是瓶颈：最省的 {best['name']} 只用到上限的 {ratio*100:.0f}%，"
            f"余量超过 {(1-slack)*100:.0f}%。真正该省的是{oz}，"
            f"所以选 {alt['name']}。为不需要的{lz}牺牲{oz}是纯亏。"
        )
        en = (
            f"{le} is not the bottleneck: best candidate {best['name']} uses only "
            f"{ratio*100:.0f}% of the cap. Optimise {oe} instead, so pick "
            f"{alt['name']}. Paying for {le} you do not need is a pure loss."
        )
        return alt, zh, en

    if len(s) > 1 and best[key] > 0:
        gap = (s[1][key] - best[key]) / s[1][key]
        if gap < tol:
            zh = (
                f"{best['name']} 只比 {s[1]['name']} 省 {gap*100:.1f}%（< {tol*100:.0f}%），"
                f"属于同一档；建议选实现更简单的那个，不要为这点差异折腾"
            )
            en = (
                f"{best['name']} is only {gap*100:.1f}% better than {s[1]['name']} "
                f"(< {tol*100:.0f}%): same tier, pick the simpler one."
            )
            return best, zh, en
    zh = f"{best['name']} 在「{lz}」这一维最小（已用到上限的 {ratio*100:.0f}%，确实紧）"
    en = f"{best['name']} is smallest on {le} ({ratio*100:.0f}% of cap, genuinely tight)"
    return best, zh, en


# ---------------------------------------------------------------- 画图


def setup_matplotlib():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.font_manager as fm
    import matplotlib.pyplot as plt

    want = [
        "Microsoft YaHei", "SimHei", "Noto Sans CJK SC",
        "Source Han Sans SC", "WenQuanYi Micro Hei", "PingFang SC",
    ]
    have = {f.name for f in fm.fontManager.ttflist}
    font = next((w for w in want if w in have), None)
    if font:
        plt.rcParams["font.sans-serif"] = [font]
        plt.rcParams["axes.unicode_minus"] = False
    return plt, font


def draw(plan, points, c, chosen, reason, out: Path, objective: str):
    """reason = (中文, 英文)。没有中文字体时用英文，否则图上结论全是方块。"""
    """四联图：显存 / 时间 / 精度 各一张条形，加一张两维权衡散点。

    为什么不画一张：三个维度里前两个能放进同一平面，精度放进去只能当第三轴
    （气泡大小），而 85% 和 87% 的差别在气泡上根本看不出来。
    分成四格，每格只比一个数——比"折成综合分"清楚，也比只看两维完整。
    """
    import textwrap

    plt, font = setup_matplotlib()
    from matplotlib.lines import Line2D
    from matplotlib.patches import Rectangle

    zh = font is not None
    t = (lambda a, b: a) if zh else (lambda a, b: b)

    def style(p):
        """(填充, 斜纹, 边框, 文字色, 字重)——用 name 判等，因为时间图用的是副本"""
        if p["name"] == chosen["name"]:
            return "#d32f2f", "", "black", "#b71c1c", "bold"
        if p["bad"]:
            return "#eceff1", "///", "#90a4ae", "#757575", "normal"
        return "#66bb6a", "", "#1b5e20", "#1b5e20", "normal"

    def name_of(p, mark=True):
        star = "★ " if (mark and p["name"] == chosen["name"]) else ""
        est = "" if p.get("measured", True) else " *"
        return star + p["name"] + est

    fig = plt.figure(figsize=(14.6, 9.4), dpi=150)
    gs = fig.add_gridspec(
        2, 2, hspace=0.46, wspace=0.34,
        left=0.125, right=0.975, top=0.895, bottom=0.265,
    )
    ax_v = fig.add_subplot(gs[0, 0])
    ax_t = fig.add_subplot(gs[0, 1])
    ax_q = fig.add_subplot(gs[1, 0])
    ax_s = fig.add_subplot(gs[1, 1])

    def draw_bars(ax, pts, key, limit, title, fmt):
        s = sorted(pts, key=lambda p: p[key])
        for i, p in enumerate(s):
            fc, hat, ec, tc, fw = style(p)
            ax.barh(i, p[key], height=0.58, color=fc, edgecolor=ec,
                    hatch=hat, linewidth=1.0, zorder=3)
            ax.text(p[key], i, "  " + fmt(p[key]), va="center", ha="left",
                    fontsize=8.6, color=tc, fontweight=fw, zorder=4)
        ax.set_yticks(range(len(s)))
        ax.set_yticklabels([name_of(p) for p in s], fontsize=8.8)
        ax.invert_yaxis()
        xhi, cap_in = axis_range([p[key] for p in pts], limit)
        if cap_in:
            ax.axvline(limit, color="#c62828", ls="--", lw=1.3, zorder=2)
        else:
            ax.text(0.985, 0.5, t(f"上限 {fmt(limit)}，在图外 →", f"cap {fmt(limit)}, off-chart →"),
                    transform=ax.transAxes, ha="right", va="center",
                    fontsize=8.0, color="#c62828", style="italic")
        ax.set_xlim(0, xhi)
        ax.set_title(title, fontsize=10.5, fontweight="bold", pad=21, loc="center")
        ax.text(0.5, 1.012, t(f"红色虚线 = 上限 {fmt(limit)}", f"red dashed = cap {fmt(limit)}"),
                transform=ax.transAxes, ha="center", va="bottom",
                fontsize=8.4, color="#c62828")
        ax.grid(axis="x", alpha=0.2, lw=0.6)
        ax.set_axisbelow(True)

    draw_bars(ax_v, points, "vram_mib", c["max_vram_mib"],
              t("峰值显存（越短越好）", "Peak VRAM"), lambda v: f"{v/1024:.1f} GiB")

    # 时间单位自适应：分钟级任务用 h 全是 0.0，看不出差别
    tmax_raw = max(p["hours"] for p in points)
    t_scale, t_unit = (60.0, "min") if tmax_raw < 0.25 else (1.0, "h")
    pts_t = [dict(p, hours=p["hours"] * t_scale) for p in points]
    chosen_name = chosen["name"]
    draw_bars(ax_t, pts_t, "hours", c["max_hours"] * t_scale,
              t(f"训练总时长（越短越好，单位 {t_unit}）", f"Training time ({t_unit})"),
              lambda v: f"{v:.1f} {t_unit}")
    for p in pts_t:
        p["_chosen"] = p["name"] == chosen_name

    # 精度：条形 + 下限线 + base 参照线
    s = sorted(points, key=lambda p: p["quality"])
    for i, p in enumerate(s):
        fc, hat, ec, tc, fw = style(p)
        ax_q.barh(i, p["quality"] * 100, height=0.58, color=fc, edgecolor=ec,
                  hatch=hat, linewidth=1.0, zorder=3)
        ax_q.text(p["quality"] * 100, i, f"  {p['quality']*100:.0f}%", va="center",
                  ha="left", fontsize=8.6, color=tc, fontweight=fw, zorder=4)
    ax_q.set_yticks(range(len(s)))
    ax_q.set_yticklabels([name_of(p) for p in s], fontsize=8.8)
    ax_q.invert_yaxis()
    qmin = c["min_quality"] * 100
    ax_q.axvline(qmin, color="#c62828", ls="--", lw=1.3, zorder=2)
    bq = plan.get("baseline_quality")
    if bq is not None:
        ax_q.axvline(bq * 100, color="#6d4c41", ls=":", lw=1.7, zorder=2)
    ax_q.set_xlim(0, 108)
    ax_q.set_title(t("精度（探针集全对率，越右越好）", "Quality"), fontsize=10.5,
                   fontweight="bold", pad=21)
    note = t(f"红色虚线 = 下限 {qmin:.0f}%", f"red = floor {qmin:.0f}%")
    if bq is not None:
        note += t(f"　　棕色点线 = base 不训练 {bq*100:.0f}%", f"   brown = base {bq*100:.0f}%")
    ax_q.text(0.5, 1.012, note, transform=ax_q.transAxes, ha="center", va="bottom",
              fontsize=8.4, color="#c62828")
    ax_q.grid(axis="x", alpha=0.2, lw=0.6)
    ax_q.set_axisbelow(True)

    # 散点：只看两维权衡，不重复标名字（左边三张已经标了）
    xmax, xcap_in = axis_range([p["vram_mib"] for p in points], c["max_vram_mib"])
    ymax, ycap_in = axis_range([p["hours"] for p in pts_t], c["max_hours"] * t_scale)
    ax_s.add_patch(Rectangle((0, 0), min(c["max_vram_mib"], xmax), min(c["max_hours"] * t_scale, ymax),
                             facecolor="#e8f5e9", edgecolor="none", zorder=0))
    if xcap_in:
        ax_s.axvline(c["max_vram_mib"], color="#c62828", ls="--", lw=1.3, zorder=1)
    if ycap_in:
        ax_s.axhline(c["max_hours"] * t_scale, color="#ef6c00", ls="--", lw=1.3, zorder=1)
    feas = [p for p in pts_t if not p["bad"]]
    if len(feas) >= 2:
        ax_s.plot([p["vram_mib"] for p in feas], [p["hours"] for p in feas],
                  ls=":", lw=1.1, color="#78909c", zorder=2)
    for p in pts_t:
        measured = p.get("measured", True)
        if p["name"] == chosen["name"]:
            ax_s.scatter(p["vram_mib"], p["hours"], marker="*", s=430,
                         c="#d32f2f", edgecolors="black", linewidths=0.8, zorder=6)
            ax_s.annotate(t("★ 选定", "★ chosen"), (p["vram_mib"], p["hours"]),
                          textcoords="offset points", xytext=(16, 12),
                          fontsize=9.2, color="#b71c1c", fontweight="bold")
        elif p["bad"]:
            ax_s.scatter(p["vram_mib"], p["hours"], marker="X", s=110,
                         c="#9e9e9e", edgecolors="none", zorder=4)
        else:
            ax_s.scatter(p["vram_mib"], p["hours"], marker="o", s=140,
                         facecolors="#2e7d32" if measured else "none",
                         edgecolors="#1b5e20", linewidths=1.6, zorder=5)
    ax_s.set_xlim(0, xmax)
    ax_s.set_ylim(0, ymax)
    ax_s.set_xlabel(t("峰值显存 MiB", "Peak VRAM (MiB)"), fontsize=9.2)
    ax_s.set_ylabel(t(f"训练时长 {t_unit}", f"Time ({t_unit})"), fontsize=9.2)
    ax_s.set_title(t("两维权衡：为什么是它", "Trade-off"), fontsize=10.5,
                   fontweight="bold", pad=8)
    ax_s.grid(alpha=0.18, lw=0.6)
    ax_s.set_axisbelow(True)
    ax_s.legend(handles=[
        Line2D([], [], marker="*", ls="", ms=14, mfc="#d32f2f", mec="black", label=t("选定", "chosen")),
        Line2D([], [], marker="o", ls="", ms=7, mfc="#2e7d32", mec="#1b5e20", label=t("可行", "feasible")),
        Line2D([], [], marker="o", ls="", ms=7, mfc="none", mec="#1b5e20", label=t("估算", "estimated")),
        Line2D([], [], marker="X", ls="", ms=8, mfc="#9e9e9e", mec="none", label=t("越界", "over")),
    ], loc="upper right", fontsize=8.0, framealpha=0.92)

    fig.suptitle(plan.get("title", t("方案对比", "Plan comparison")),
                 fontsize=14.5, fontweight="bold", y=0.965)

    # 口径与判定规则
    fig.text(0.125, 0.185,
             t("判定：红条=选定｜绿条=可行｜灰色斜纹=违反硬约束｜* = 估算值（不可与实测横比）",
               "Red = chosen, green = feasible, hatched = violates constraint, * = estimated"),
             fontsize=8.4, color="#5d4037", style="italic")
    fig.text(0.125, 0.150,
             t("口径：s/step 取 train_runtime 稳态（丢弃前 2 步）；时长 = s/step × 每 epoch 步数 × epoch",
               "Definition: steady-state s/step (drop first 2 steps)"),
             fontsize=8.4, color="#5d4037", style="italic")

    excl = plan.get("excluded") or []
    if excl:
        head = t("没画进图里（够不着，不是没考虑）：", "Not plotted (out of reach): ")
        sep = "；" if zh else "; "
        items = sep.join(f"{e['name']} —— {e['reason']}" for e in excl)
        lines = textwrap.wrap(head + items, width=130)
        if len(lines) > 2:                      # 最多两行，否则压住结论框
            lines = lines[:2]
            lines[-1] = lines[-1][:120] + " …"
        fig.text(0.125, 0.128, "\n".join(lines), fontsize=8.2, color="#6d4c41", va="top")

    rtxt = reason[0] if zh else reason[1]
    wrapped = "\n".join(textwrap.wrap(rtxt, width=64 if zh else 110))
    fig.text(0.125, 0.014,
             t("结论：", "Conclusion: ") + wrapped,
             fontsize=10.0, color="#1a237e", fontweight="bold", va="bottom",
             bbox=dict(boxstyle="round,pad=0.5", fc="#e8eaf6", ec="#7986cb", lw=0.9))

    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=150, facecolor="white")
    print(f"已保存 {out}")
    if not font:
        print("⚠️ 没找到中文字体，图上文字用英文。装一个 SimHei / Noto Sans CJK 即可。")


# ---------------------------------------------------------------- 输出


def to_markdown(points, c, chosen, objective):
    lines = [
        "| 方案 | 峰值显存 | 训练时长 | 精度 | 判定 |",
        "|---|---|---|---|---|",
    ]
    for p in sorted(points, key=lambda x: x["vram_mib"]):
        mark = "**★ 选定**" if p is chosen else ("× " + "／".join(p["bad"]) if p["bad"] else "可行")
        est = "" if p.get("measured", True) else "（估）"
        lines.append(
            f"| {p['name']}{est} | {p['vram_mib']/1024:.1f} GiB | {p['hours']:.1f} h "
            f"| {p['quality']*100:.0f}% | {mark} |"
        )
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="把选型画成图（两维散点 + 硬约束 + 帕累托）")
    ap.add_argument("--plan", required=True, help="plan.json")
    ap.add_argument("--out", default="plan.png")
    ap.add_argument("--objective", choices=["vram", "time"], default="vram",
                    help="当前在省什么：显存 or 时间（一次只省一个）")
    ap.add_argument("--tolerance", type=float, default=0.15,
                    help="差值小于此比例视为同档（默认 0.15）")
    ap.add_argument("--slack", type=float, default=0.7,
                    help="最省方案占上限比例低于此值，判为目标维度不紧（默认 0.7）")
    ap.add_argument("--markdown", action="store_true", help="额外输出可粘贴的表格")
    a = ap.parse_args()

    plan = load_plan(Path(a.plan))
    c = plan["constraints"]

    points = []
    for p in plan["points"]:
        p = dict(p)
        p["hours"] = fill_hours(p, plan)
        p["bad"] = judge(p, c)
        points.append(p)

    feasible = [p for p in points if not p["bad"]]

    if not feasible:
        # 没有可行方案：告诉人该放宽哪一个，而不是画一张空图
        print("没有可行方案。逐个看差多少：")
        for p in sorted(points, key=lambda x: len(x["bad"])):
            print(f"  {p['name']}: " + ("；".join(p["bad"]) or "可行"))
        best = min(points, key=lambda x: len(x["bad"]))
        print(f"\n最接近的是 {best['name']}，差：{'；'.join(best['bad'])}")
        print("→ 放宽那个最松的约束（通常不是精度），或换更小的模型")
        sys.exit(2)

    chosen, reason_zh, reason_en = pick(feasible, a.objective, a.tolerance, a.slack, c)

    print(to_markdown(points, c, chosen, a.objective))
    print(f"\n{reason_zh}")
    if a.markdown:
        print("\n<!-- 把上面的表格直接贴进 02_training_plan.md -->")

    draw(plan, points, c, chosen, (reason_zh, reason_en), Path(a.out), a.objective)


if __name__ == "__main__":
    main()
