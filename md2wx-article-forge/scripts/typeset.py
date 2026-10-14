"""渲染 + 审计 + 预览：markdown → 微信内联样式 HTML 的一站式入口。

用法:
    python typeset.py <input.md> -o out.html [--theme <theme.json>]

做了三件事：
    1. 审计    调 normalize.py --check，语法问题先报告再渲染（不阻断，缺陷留在告警里）
    2. 渲染    render.build()：markdown → 内联样式 HTML → wechat_guard 清洗
    3. 预览    总是另写浏览器可读的预览页（本地浏览不依赖任何凭据；真机判定仍以 evaluate 截图为准）

退出码：0 成功；1 渲染失败；2 参数错误
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]  # scripts → 根
sys.path.insert(0, str(ROOT / "scripts"))

from render import build  # noqa: E402

NORMALIZE = ROOT / "scripts" / "normalize.py"

# 预览模板：宽 500px 只是粗略示意，375px 截图（evaluate.py）才是判定标准
PREVIEW_TEMPLATE = """<!doctype html><meta charset="utf-8">
<title>{name} 预览</title>
<div style="max-width: 500px; margin: 0 auto; padding: 20px 16px; background: #fafafa;
            font-family: -apple-system, 'PingFang SC', 'Microsoft YaHei', sans-serif;">
  <p style="font-size: 13px; color: #999; margin: 0 0 16px;">
    本地预览宽度 500px。真机为 375px 左右，最终判定以 work/&lt;篇名&gt;/3_eval/ 下的分页截图为准。
  </p>
  <div style="background: #fff; padding: 16px; border-radius: 8px;">{body}</div>
</div>
"""


def run_audit(md: Path) -> int:
    """normalize --check 只报告不改写。返回 2 表示检出语法问题（属正常信号，不阻断）。"""
    r = subprocess.run(
        [sys.executable, str(NORMALIZE), str(md), "--check"],
        capture_output=True, text=True, encoding="utf-8",
    )
    if r.stdout:
        print(r.stdout.rstrip())
    if r.stderr:
        print(r.stderr.rstrip(), file=sys.stderr)
    return r.returncode


def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="渲染 + 审计 + 预览")
    ap.add_argument("markdown", help="来源 markdown")
    ap.add_argument("-o", "--output", required=True, help="输出 HTML 路径")
    ap.add_argument("--theme", help="主题 json，默认 assets/themes/default.json")
    args = ap.parse_args()

    md = Path(args.markdown).resolve()
    if not md.exists():
        print(f"找不到输入：{md}", file=sys.stderr)
        return 2

    run_audit(md)  # 审计只是信号，检出问题也继续渲染 —— 缺陷交给评估环节抓

    theme_path = Path(args.theme).resolve() if args.theme else None
    out = Path(args.output).resolve()
    raw_out = out.with_name(out.stem + ".raw.html")
    result = build(md, out, theme_path, raw_out=raw_out)
    print(json.dumps(
        {k: v for k, v in result.items() if k != "report"},
        ensure_ascii=False, indent=2,
    ))
    rep = result["report"]
    print(f"清洗 ok={rep['ok']}  移除标签={rep['removed_tags']}  剔除属性={rep['stripped_props']}")
    for v in rep["violations"]:
        print(f"  [{v['severity']}] {v['tag']}: {v['reason']}")
    if result["heading_warnings"]:
        print("标题降级告警：")
        for w in result["heading_warnings"]:
            print(f"  {w}")

    preview = out.with_name(out.stem + ".preview.html")
    preview.write_text(
        PREVIEW_TEMPLATE.format(name=md.stem, body=out.read_text(encoding="utf-8")),
        encoding="utf-8",
    )
    print(f"预览：{preview}")

    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
