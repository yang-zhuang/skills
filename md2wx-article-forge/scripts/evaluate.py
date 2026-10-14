"""可视化评估：浏览器真实渲染 + DOM 断言 + 截图。

分工原则（本 skill 的核心立场）：
    能用代码判定的，一律由本脚本判定 —— 横向溢出、字号下限、裂图、非微信域名、
    样式经清洗后的存活情况，这些都是布尔判定，让 AI 用眼睛看是在浪费算力、
    且每次结论可能不一致。

    AI 只做一件事：打开 PNG，回答「好不好看」。这部分无法写成断言，
    也不该写成断言 —— 一旦写死成规则，就等于规定死了审美。

因此本脚本的输出被一分为二：
    eval-report.json  结构化事实，供脚本与 CI 消费
    *.png             给 AI（和人）看的图

用法：
    python evaluate.py <built.html> [--raw built.raw.html] [--width 375] [--out <目录>]
退出码：0 全部通过；1 存在硬失败；2 环境缺失或参数错误

渲染引擎用本机 Chrome / Edge 的 headless CLI，不依赖 playwright：
度量数据靠注入脚本写进隐藏 DOM 后用 --dump-dom 读回，
截图靠 --screenshot，两者都不需要 CDP 会话，也就不需要那 170MB 依赖。
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_WIDTH = 375          # iPhone SE / 多数安卓基准宽度
MIN_BODY_FONT_PX = 12        # 低于此值移动端不可读
MAX_BLANK_RATIO = 0.30       # 截图里纯空白行的占比上限
MAX_GAP_PX = 40              # 最长连续垂直空洞上限


def static_checks(html: str, width: int) -> list[dict]:
    """不开浏览器也能做的检查 —— 纯文本层，快且零依赖。"""
    out: list[dict] = []

    imgs = re.findall(r"<img[^>]*>", html, re.I)
    bad_host = [
        i for i in imgs
        if (m := re.search(r'src="([^"]+)"', i))
        and not m.group(1).startswith(("data:", "https://mmbiz.qpic.cn"))
    ]
    out.append({
        "name": "图片域名合法",
        "pass": not bad_host,
        "detail": f"{len(imgs)} 张图，{len(bad_host)} 张非 mmbiz.qpic.cn 或 data URI",
        "severity": "hard",
    })

    has_inline_style = re.search(r'style="[^"]*font-size', html, re.I) is not None
    out.append({
        "name": "样式已内联",
        "pass": has_inline_style,
        "detail": "内联样式齐全" if has_inline_style
                  else "未检出内联 font-size，极可能存在外部 CSS 依赖",
        "severity": "hard",
    })

    forbidden = [t for t in ("<script", "<iframe", "<style", "<form", "<embed")
                 if re.search(re.escape(t), html, re.I)]
    out.append({
        "name": "无微信禁用标签",
        "pass": not forbidden,
        "detail": f"残留：{forbidden}" if forbidden else "无",
        "severity": "hard",
    })

    attrs = [a for a in ("class=", "id=", "onclick=") if re.search(rf"\s{a}\s*[=>]", html, re.I)]
    out.append({
        "name": "无会被剥离的属性",
        "pass": not attrs,
        "detail": f"残留：{attrs}" if attrs else "无",
        "severity": "hard",
    })

    fixed_px = re.findall(r"width:\s*(\d{3,})px", html)
    too_wide = [p for p in fixed_px if int(p) > width]
    out.append({
        "name": "无固定宽度溢出",
        "pass": not too_wide,
        "detail": f"超过 {width}px 的固定宽度：{too_wide}" if too_wide else "无",
        "severity": "hard",
    })

    return out


CHROME_CANDIDATES = (
    Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
    Path("C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"),
    Path("C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe"),
)

# 度量脚本注入到 probe 页里跑，结果写进一个隐藏 <pre>，再用 --dump-dom 读回来。
# 这样不需要 CDP、不需要 playwright —— Chrome CLI 自身够用，少 ~170MB 依赖。
METRICS_SCRIPT = """
<script>
window.addEventListener('load', function () {
  var frame = document.getElementById('__wxframe');
  var fr = frame.getBoundingClientRect();
  var out = {vw: Math.round(fr.width), maxRight: 0, scrollWidth: 0,
             height: Math.ceil(document.body.scrollHeight),
             imgs: [], fonts: [], wide: 0, headings: 0};
  document.querySelectorAll('img').forEach(function (i) {
    out.imgs.push({src: i.getAttribute('src') || '', ok: !!(i.complete && i.naturalWidth > 0)});
  });
  var sizes = {};
  document.querySelectorAll('p, section, li, td, div').forEach(function (el) {
    var node = Array.prototype.some.call(el.childNodes, function (n) {
      return n.nodeType === 3 && n.textContent.trim();
    });
    if (!node) return;
    var cs = getComputedStyle(el);
    out.fonts.push(parseFloat(cs.fontSize));
    var r = el.getBoundingClientRect();
    if (r.right > out.maxRight) out.maxRight = r.right;
    if (r.width > 0 && r.right > fr.right + 1) out.wide++;
    if ((parseInt(cs.fontWeight, 10) || 400) >= 600 && parseFloat(cs.fontSize) >= 16) {
      sizes[cs.fontSize] = 1;
    }
  });
  // 只统计容器自身的字号层级，不把 <strong> 这类行内强调算成标题，否则会虚高
  out.headings = Object.keys(sizes).length;
  out.scrollWidth = Math.round(out.maxRight - fr.left);
  // 卡片内的无效留白：padding 之外多出来的 margin 叠加。
  // 阈值 16px —— 卡片 padding 已提供留白，段落再叠 18px 就是肉眼可见的空洞。
  out.holes = [];
  document.querySelectorAll('section').forEach(function (sec) {
    var cs = getComputedStyle(sec);
    var bg = cs.backgroundColor;
    var hasBg = bg && bg !== 'rgba(0, 0, 0, 0)';
    var hasBorder = parseFloat(cs.borderLeftWidth) > 0;
    if (!hasBg && !hasBorder) return;
    var sr = sec.getBoundingClientRect();
    if (sr.height < 40) return;
    // 只看直接子元素：嵌套子卡自带 padding，其内部间隙由子卡自己的检查负责，
    // 跨容器边界的间隙算到外层头上是误报。
    var kids = Array.prototype.filter.call(sec.children, function (k) {
      return k.offsetHeight > 0 && k.textContent.trim();
    });
    if (kids.length < 1) return;
    var padT = parseFloat(cs.paddingTop), padB = parseFloat(cs.paddingBottom);
    var bt = parseFloat(cs.borderTopWidth), bb = parseFloat(cs.borderBottomWidth);
    var first = kids[0].getBoundingClientRect();
    var last = kids[kids.length - 1].getBoundingClientRect();
    var topHole = Math.round(first.top - sr.top - bt - padT);
    var botHole = Math.round(sr.bottom - bb - padB - last.bottom);
    var worst = Math.max(topHole, botHole);
    if (worst > 16) {
      out.holes.push({tag: sec.getAttribute('style').slice(0, 40),
                      hole: worst, n: kids.length});
    }
    // 相邻块之间的空洞（margin 叠加）
    for (var k = 1; k < kids.length; k++) {
      var gap = Math.round(kids[k].getBoundingClientRect().top - kids[k-1].getBoundingClientRect().bottom);
      if (gap > 24) {
        out.holes.push({tag: sec.getAttribute('style').slice(0, 40), hole: gap, n: kids.length});
        break;
      }
    }
  });
  // 间隙归因：只知道「空白 33%」没用，得知道是哪几处撑出来的。
  // 逐层记录每个块的高度与它上方的实际间隙（slice(2) 去掉 'r.'，路径用于定位嵌套层级）。
  out.gaps = [];
  (function scan(parent, path) {
    var kids = Array.prototype.filter.call(parent.children, function (k) {
      // 只排除度量容器本身；正文里的 <pre> 代码块是真内容，排除它会让相邻间隙被合并
      return k.id !== '__wxmetrics' && k.getBoundingClientRect().height > 0;
    });
    if (!kids.length) return;
    var pr = parent.getBoundingClientRect();
    var prevBottom = null;
    kids.forEach(function (k, idx) {
      var r = k.getBoundingClientRect();
      var cs = getComputedStyle(k);
      var mt = parseFloat(cs.marginTop) || 0;
      var mb = parseFloat(cs.marginBottom) || 0;
      var gap = prevBottom === null
        ? Math.round(r.top - pr.top - (parseFloat(cs.marginTop) || 0))
        : Math.round(r.top - prevBottom);
      out.gaps.push({
        p: path.slice(2), i: idx, tag: k.tagName.toLowerCase(),
        h: Math.round(r.height), gap: gap, mt: mt, mb: mb, inside: [mt, mb].some(function (v) { return v > 0; }),
        s: (k.getAttribute('style') || '').slice(0, 70),
      });
      prevBottom = r.bottom;
      var bg = cs.backgroundColor;
      var hasSkin = (bg && bg !== 'rgba(0, 0, 0, 0)') || parseFloat(cs.borderLeftWidth) > 0
                    || cs.boxShadow !== 'none';
      if (hasSkin && r.height >= 40) scan(k, path + '.' + idx);
    });
    out.gaps.push({p: path.slice(2), i: -1, tag: '__end', h: 0, inside: false,
                   gap: Math.round(pr.bottom - prevBottom), mt: 0, mb: 0, s: ''});
  })(document.getElementById('__wxframe'), 'r');

  var pre = document.createElement('pre');
  pre.id = '__wxmetrics';
  pre.style.display = 'none';
  pre.textContent = JSON.stringify(out);
  document.body.appendChild(pre);
});
</script>
"""


def find_browser() -> Path | None:
    for c in CHROME_CANDIDATES:
        if c.exists():
            return c
    return None


def build_probe(fragment: str, width: int, base_dir: Path) -> str:
    """预览页。刻意不引外部 CSS —— 微信正文也是裸内联样式，
    包一层带样式的容器会掩盖真实问题。

    Windows 下 headless Chrome 有约 500px 的最小窗口宽度，
    --window-size=375 会被抬高，量出来的是桌面宽度。所以这里不依赖视口，
    而是把容器定死成目标宽度，再按容器的右边界判溢出。
    """
    return (
        "<!doctype html><meta charset=\"utf-8\">"
        # base 必须是绝对路径，相对路径 as_uri 会直接抛错
        f"<base href=\"{base_dir.resolve().as_uri()}/\">"
        # 不能 margin:auto 居中：截图按左上角裁 375px，居中会把右半边裁掉
        f'<div id="__wxframe" style="box-sizing: border-box; width: {width}px;'
        " margin: 0; padding: 0 16px; "
        "font-family: -apple-system, BlinkMacSystemFont, 'PingFang SC', "
        "'Hiragino Sans GB', 'Microsoft YaHei', sans-serif;\">"
        f"{fragment}</div>{METRICS_SCRIPT}"
    )


def run_browser(exe: Path, args: list[str], timeout: int = 90) -> tuple[int, str]:
    r = subprocess.run([str(exe)] + args, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    noise = "updater|Failed to open named pipe|GPU process|Fontconfig"
    lines = ((r.stdout or "") + (r.stderr or "")).split("\n")
    clean = "\n".join(x for x in lines if not re.search(noise, x))
    return r.returncode, clean


def trim_png(png: Path, width: int) -> bool:
    """把截图右侧因系统最小窗口宽度多出来的空白裁掉，得到精确的移动端宽。"""
    try:
        from PIL import Image
    except ImportError:
        return False
    im = Image.open(png)
    if im.width <= width:
        return False
    im.crop((0, 0, width, im.height)).save(png)
    return True


def whitespace_check(full_png: Path) -> dict:
    """按像素量垂直留白 —— 这是唯一不依赖「谁去看图」的检查。

    教训：间距过大这种问题，肉眼一眼可见，但把图丢给模型未必看得出来
    （模型可能读图失败、也可能只扫重点区域）。空白是可以逐行量出来的：
    纯色行的明暗跨度为 0，文字行不是。量成数字，就不存在「没看见」。

    阈值来自实测：一篇排版紧凑的公众号文章，空白行占比约 20-28%，
    连续空洞很少超过 40px（分隔段落的空档另算，故只看最长几段）。
    """
    try:
        from PIL import Image
    except ImportError:
        return {"name": "垂直留白密度", "pass": True,
                "detail": "未装 Pillow，跳过像素级留白测量", "severity": "soft"}

    im = Image.open(full_png).convert("L")
    w, h = im.size
    blank = []
    for y in range(h):
        lo, hi = im.crop((0, y, w, y + 1)).getextrema()
        # 阈值 4：浅色卡片底（如 #fff8f0，与纯白亮度差约 5）要算作内容，
        # 阈值太高会把整张浅色卡都误判成空白
        blank.append(hi - lo < 4)

    runs, start = [], None
    for y in range(h):
        if blank[y] and start is None:
            start = y
        elif not blank[y] and start is not None:
            runs.append(y - start)
            start = None
    if start is not None:
        runs.append(h - start)

    ratio = sum(blank) / h
    runs.sort(reverse=True)
    worst = runs[0] if runs else 0
    big = sum(1 for n in runs if n > MAX_GAP_PX)
    ok = ratio <= MAX_BLANK_RATIO and worst <= MAX_GAP_PX
    return {
        "name": "垂直留白密度",
        "pass": ok,
        "detail": (
            f"空白行 {ratio:.0%}（上限 {MAX_BLANK_RATIO:.0%}），"
            f"最长连续空洞 {worst}px（上限 {MAX_GAP_PX}px），"
            f">{MAX_GAP_PX}px 的空洞 {big} 处"
        ),
        "severity": "soft",
    }


def crop_pages(full_png: Path, out_dir: Path, page_height: int = 812) -> list[str]:
    """长图切成手机屏大小的页：让 AI 一次看一张图，而不是看一条几米长的面条。"""
    try:
        from PIL import Image
    except ImportError:
        return []
    im = Image.open(full_png)
    w, h = im.size
    pages = min(8, max(1, -(-h // page_height)))
    paths = []
    for idx in range(pages):
        top = min(idx * page_height, max(0, h - page_height))
        bottom = min(top + page_height, h)
        p = out_dir / f"p{idx + 1}.png"
        im.crop((0, top, w, bottom)).save(p)
        paths.append(str(p))
    return paths


def browser_checks(html_file: Path, width: int, out_dir: Path) -> tuple[list[dict], list[str], dict]:
    """真实渲染后的 DOM 断言 + 截图。返回（结果列表, PNG 路径列表, 度量数据）。"""
    exe = find_browser()
    if exe is None:
        return [{
            "name": "浏览器检查",
            "pass": False,
            "detail": "未找到 Chrome / Edge，跳过真实渲染检查与截图",
            "severity": "hard",
        }], [], {}

    fragment = html_file.read_text(encoding="utf-8")
    probe = out_dir / "page.html"
    probe.write_text(build_probe(fragment, width, html_file.parent), encoding="utf-8")

    base = ["--headless=new", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
            "--allow-file-access-from-files"]
    uri = probe.resolve().as_uri()

    # 度量必须在与截图完全相同的视口下跑，否则量出来的是桌面窗口的宽度
    _, dom = run_browser(
        exe, base + ["--window-size=%d,1200" % width, "--virtual-time-budget=5000", "--dump-dom", uri]
    )
    m = re.search(r'<pre id="__wxmetrics"[^>]*>(.*?)</pre>', dom, re.S)
    if not m:
        return [{
            "name": "浏览器检查",
            "pass": False,
            "detail": f"未能从渲染结果取回度量数据（浏览器输出被截断或脚本被拦）：{dom[:200]}",
            "severity": "hard",
        }], [], {}
    metrics = json.loads(m.group(1))

    # 出图路径必须是绝对路径：Chrome 按自己的 CWD 解析相对路径，容易落到别处
    full = (out_dir / "full.png").resolve()
    # 余量只给 2px：给多了尾部就是一条纯空白带，会被留白统计误判成内容空洞
    height = min(int(metrics["height"]) + 2, 26000)
    # 窗口宽度会被系统抬到 500 左右，多出来的部分截图后用 PIL 裁掉
    run_browser(exe, base + [f"--window-size={width},{height}", f"--screenshot={full}", uri])
    if not full.exists():
        return [{
            "name": "截图产出",
            "pass": False,
            "detail": f"Chrome 未产出截图：{full}",
            "severity": "hard",
        }], [], {}
    trim_png(full, width)

    shots = [str(full)] + crop_pages(full, out_dir)
    broken = [i for i in metrics["imgs"] if not i["ok"]]
    fonts = sorted(metrics["fonts"])
    min_font = fonts[0] if fonts else None

    results = [
        {
            "name": "无横向溢出",
            "pass": metrics["scrollWidth"] <= width + 1,
            "detail": f"文档宽 {metrics['scrollWidth']}px / 视口 {width}px",
            "severity": "hard",
        },
        {
            "name": "图片可加载",
            "pass": not broken,
            "detail": f"{len(metrics['imgs'])} 张参与渲染，{len(broken)} 张裂图",
            "severity": "hard",
        },
        {
            "name": "正文字号可读",
            "pass": min_font is None or min_font >= MIN_BODY_FONT_PX,
            "detail": f"最小字号 {min_font}px（下限 {MIN_BODY_FONT_PX}px）",
            "severity": "soft",
        },
        {
            "name": "元素未超出右边界",
            "pass": metrics["wide"] == 0,
            "detail": f"{metrics['wide']} 个元素右边界越界",
            "severity": "soft",
        },
        {
            "name": "标题层级数不超限",
            "pass": metrics["headings"] <= 3,
            "detail": f"检出 {metrics['headings']} 级粗体标题（超过 3 级移动端已难视觉区分）",
            "severity": "soft",
        },
        {
            "name": "卡片内无空洞留白",
            "pass": not metrics.get("holes"),
            "detail": (
                f"{len(metrics['holes'])} 处 padding 外的额外留白（阈值 16px），"
                f"最严重 {max((h['hole'] for h in metrics['holes']), default=0)}px —— "
                "通常是段落大边距叠加卡片 padding 所致"
                if metrics.get("holes") else "无"
            ),
            "severity": "soft",
        },
    ]
    return results, shots, metrics



def main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="公众号排版可视化评估")
    ap.add_argument("html", help="built.html 成品")
    ap.add_argument("--raw", help="未经 wechat_guard 清洗的渲染产物，用于对比清洗前后的视觉差异")
    ap.add_argument("--width", type=int, default=DEFAULT_WIDTH, help=f"视口宽度，默认 {DEFAULT_WIDTH}")
    ap.add_argument("--out", help="输出目录，默认与输入同级的 <name>-eval/")
    args = ap.parse_args()

    src = Path(args.html)
    if not src.exists():
        print(f"找不到输入文件：{src}", file=sys.stderr)
        return 2

    out_dir = (Path(args.out) if args.out else src.parent / f"{src.stem}-eval").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    html = src.read_text(encoding="utf-8")

    checks: list[dict] = static_checks(html, args.width)
    dom_checks, shots, metrics = browser_checks(src, args.width, out_dir)
    checks += dom_checks
    if shots:
        checks.append(whitespace_check(Path(shots[0])))

    # 归因：光说「空白 33%」没法改，必须指出是哪几个块之间撑出来的
    gaps = sorted(metrics.get("gaps", []), key=lambda g: -g["gap"])
    top_gaps = gaps[:12]

    raw_shots: list[str] = []
    if args.raw:
        rp = Path(args.raw)
        if rp.exists():
            tmp_out = out_dir / "raw"
            tmp_out.mkdir(exist_ok=True)
            _, raw_shots, _ = browser_checks(rp, args.width, tmp_out)
            raw_shots = [s.replace("\\", "/") for s in raw_shots if s.endswith("full.png")]

    hard_fail = [c for c in checks if c["severity"] == "hard" and not c["pass"]]
    soft_fail = [c for c in checks if c["severity"] == "soft" and not c["pass"]]

    report = {
        "input": str(src),
        "viewport_width": args.width,
        "checks": checks,
        "hard_fail": len(hard_fail),
        "soft_fail": len(soft_fail),
        "screenshots": [s.replace("\\", "/") for s in shots],
        "raw_screenshot": raw_shots,
        "gaps": top_gaps,
        "verdict": "FAIL" if hard_fail else ("WARN" if soft_fail else "PASS"),
    }
    rep_path = out_dir / "eval-report.json"
    rep_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"[{report['verdict']}] 硬失败 {len(hard_fail)} / 软告警 {len(soft_fail)}")
    for c in checks:
        flag = "PASS" if c["pass"] else ("FAIL" if c["severity"] == "hard" else "WARN")
        print(f"  {flag:4}  {c['name']} — {c['detail']}")

    if any(c["name"] == "垂直留白密度" and not c["pass"] for c in checks) and top_gaps:
        print("\n留白归因（间隙最大的几处，p=嵌套路径 i=同级序号）：")
        for g in top_gaps:
            if g["gap"] < 12:
                break
            where = f"{g['p'] or 'root'}#{g['i']}" if g["i"] >= 0 else f"{g['p'] or 'root'} 末尾"
            print(f"  {g['gap']:>3}px  {where:<12} <{g['tag']}> h={g['h']}"
                  f"  mt={g['mt']:g} mb={g['mb']:g}")
            if g["s"]:
                print(f"        style: {g['s']}")

    print(f"\n报告：{rep_path}")
    for s in shots:
        print(f"截图：{s}")

    return 1 if hard_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
