"""主题合规检查 + 与历史主题查重。

用法:
    python theme_lint.py <theme.json>             # 只检查
    python theme_lint.py <theme.json> --register  # 检查通过后登记进 registry.json

两道关卡：
    1. 合规    必填字段齐全；正文字号 ≥ 14px（真机再小难读）；组件必须带 padding
    2. 查重    与 registry.json 里每个历史主题比色值集合的 Jaccard 相似度，
               ≥ 0.6 视为换皮重复、拒绝登记 —— 防止「每篇文章存一个只差一档灰度的新主题」
               组件角色集合完全一致且全部背景色相同也算形态重复

查重跳过同名主题（改自己的主题不算重复）。
退出码：0 通过；1 拒绝；2 参数错误
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # scripts → 根
THEMES_DIR = ROOT / "assets" / "themes"
REGISTRY = THEMES_DIR / "registry.json"

REQUIRED = (
    "name", "body_color", "body_size", "line_height",
    "h1_size", "h2_size", "h3_size", "accent",
    "code_bg", "code_color", "quote_color", "link_color", "font_stack",
)

MIN_BODY_PX = 14
JACCARD_LIMIT = 0.6

COLOR_RE = re.compile(r"#[0-9a-fA-F]{3,8}|rgba?\([^)]+\)")


def norm_color(c: str) -> str:
    return c.strip().lower()


def colors_of(theme: dict) -> set[str]:
    """主题级色值 + 每个组件的背景/边框/文字色，全部收进一个集合。"""
    out = set()
    for v in theme.values():
        if isinstance(v, str) and COLOR_RE.fullmatch(v.strip()):
            out.add(norm_color(v))
    for comp in theme.get("components", {}).values():
        for k, v in comp.items():
            if k in ("color", "strong_color") or COLOR_RE.fullmatch(v.strip()):
                out.add(norm_color(v))
    return out


def lint(theme: dict) -> list[str]:
    problems = []
    for k in REQUIRED:
        if k not in theme:
            problems.append(f"缺必填字段 {k}")
    m = re.fullmatch(r"(\d+(?:\.\d+)?)px", theme.get("body_size", ""))
    if m and float(m.group(1)) < MIN_BODY_PX:
        problems.append(f"正文字号 {theme['body_size']} 小于 {MIN_BODY_PX}px，真机难读")
    for role, comp in theme.get("components", {}).items():
        if "padding" not in comp:
            problems.append(f"组件 {role} 缺 padding —— 无内边距的卡片贴字")
        if "background" not in comp and "border-left" not in comp:
            # footer 这类无卡片组件是合法的，但必须有差异化的文字色，
            # 否则渲染出来和正文无法区分、等于没有这个组件
            has_own_color = comp.get("color") and comp.get("color") != theme.get("body_color")
            if not has_own_color:
                problems.append(
                    f"组件 {role} 既无背景/边框，文字色也与正文相同，渲染后没有视觉形态"
                )
    return problems


def load_registry() -> list[dict]:
    if not REGISTRY.exists():
        return []
    return json.loads(REGISTRY.read_text(encoding="utf-8")).get("themes", [])


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="主题合规检查 + 历史查重")
    ap.add_argument("theme", help="待检主题 json")
    ap.add_argument("--register", action="store_true", help="通过后登记进 registry.json")
    args = ap.parse_args()

    path = Path(args.theme).resolve()
    if not path.exists():
        print(f"找不到主题：{path}", file=sys.stderr)
        return 2
    theme = json.loads(path.read_text(encoding="utf-8"))
    name = theme.get("name", path.stem)

    problems = lint(theme)
    if problems:
        print("合规检查未通过：")
        for p in problems:
            print(f"  {p}")
        return 1
    print(f"合规检查通过：{name}")

    mine = colors_of(theme)
    reject = []
    for rec in load_registry():
        if rec["name"] == name:
            continue  # 改自己的主题不算查重对象
        other_path = THEMES_DIR / rec["file"]
        if not other_path.exists():
            continue
        other = json.loads(other_path.read_text(encoding="utf-8"))
        theirs = colors_of(other)
        j = len(mine & theirs) / len(mine | theirs) if mine | theirs else 0
        same_shape = (
            set(theme.get("components", {})) == set(other.get("components", {}))
            and {c.get("background") for c in theme.get("components", {}).values()}
            == {c.get("background") for c in other.get("components", {}).values()}
        )
        verdict = f"Jaccard={j:.2f}" + ("，形态相同" if same_shape else "")
        if j >= JACCARD_LIMIT or same_shape:
            reject.append(f"  与「{rec['name']}」重复：{verdict}")
        else:
            print(f"  vs {rec['name']}: {verdict} — 通过")

    if reject:
        print("查重未通过 —— 主题与已有主题过近：")
        for r in reject:
            print(r)
        return 1

    if args.register:
        reg = json.loads(REGISTRY.read_text(encoding="utf-8")) if REGISTRY.exists() else {"themes": []}
        themes = [t for t in reg.get("themes", []) if t["name"] != name]
        themes.append({"name": name, "file": path.name})
        reg["themes"] = themes
        REGISTRY.write_text(json.dumps(reg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"已登记：{name} → {REGISTRY}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
