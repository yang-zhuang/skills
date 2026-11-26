"""selftest.py — 用指定模型当"实习生"，验证这套文档能不能被用起来。

为什么需要它
------------
文档"写得对"和"能被用起来"是两件事。前者靠内容，后者靠入口
（我在哪、先读哪篇、读不到怎么办）。内容问题人读一遍能发现，
**入口问题只有真的跑一遍才暴露**——而且要看工具调用轨迹，不能只看最终答案。

它测什么
--------
四个任务，按顺序覆盖四层能力：

    link   能不能找到文件、读到内容        （入口）
    plan   能不能照流程给出计划            （理解）
    trap   会不会犯已知的错                （判断）
    exec   能不能真的执行并如实汇报         （执行 + 抗幻觉）

怎么判读
--------
脚本会自动算三个指标（判据见 references/14_selftest.md）：

    重复率        工具调用里"同一命令重复"的占比。> 30% → 入口不清楚
    路径写法种类  出现几种不同的路径写法。> 2 → 根目录约定没生效
    失败重试      读文件/执行失败的次数。> 0 → 兜底路径没写清

用法
----
    python scripts/selftest.py --endpoint https://<host>:8443/v1 \
        --model openai:Qwen3.8-27B --task all

    python scripts/selftest.py --task trap          # 只跑一个
    python scripts/selftest.py --list               # 看有哪些任务

前置（缺一不可）
----------------
1. 一个 OpenAI 兼容的推理服务（vLLM / SGLang 均可），且服务端已就绪
2. 本机有 deepagents 的 dcode：`dcode_mcp_bridge.py` + 对应 python 环境
   （默认取 DCODE_BRIDGE 环境变量或 ../deepagents/libs/code/dcode_mcp_bridge.py）
3. 目标技能包目录（默认本文件上两级）

**它不改你的 mcp.json，也不碰已有进程**：每次都是 spawn 一个新 bridge，
用自己的 debugpy 端口（自动避开 5678）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
SKILL_ROOT = HERE.parent

TASKS: dict[str, str] = {
    "link": """你是刚入职的实习生。你的工作目录里有一个技能包。

请只做两件事，不要做别的：
1. 列出这个目录下有哪些文件和子目录（一层即可）。
2. 说出 SKILL.md 的标题行（第一行 # 后面的文字）。

回答用中文，简短。
""",
    "plan": """你是刚入职的实习生，第一次独立接任务。

【任务背景】
- 机器：Windows 笔记本，用 WSL2，单卡 8G 显存
- 模型：Qwen3.5-2B（已下载到本地）
- 目标：SFT 微调，让它把客服对话转成结构化 JSON 工单
- 数据：已有 5000 条，格式还没检查
- 现在还没开始任何工作

【要求】
请按这个技能包里的流程，给我一份执行计划，回答这四个问题：

1. 第一步做什么？为什么不是先写训练脚本？
2. 完整要走几步？每一步的关键判断点是什么？
3. 你会先读哪几个文件？分别为了解决什么问题？（说清楚"为了什么"）
4. 我这个场景里，最可能卡住的瓶颈是什么？

【约束】
- 只给计划，不要写训练脚本
- 不要下载任何东西
- 用中文，条理清楚，能让我照着执行
""",
    "trap": """你是刚入职的实习生。你在这个技能包里工作过，现在有同事来问你三个问题。

请先读必要的文件，然后逐条回答。**每条都要给出理由，不要只说"对/不对"。**

【问题 1】
同事说："我给 Qwen3.5-2B 上了 QLoRA，显存是够了，但训练变慢了。
我想把 QLoRA 关掉，换回普通 LoRA 提速，这样应该能快不少吧？"
他这样想对不对？如果不对，他真正该怎么做？

【问题 2】
同事说："我原来跑一轮要 3.2 小时，优化到 2.9 小时了，省了 10%，挺有成就感的。"
这个优化值不值得？为什么？

【问题 3】
同事说："显存和时间我都要优化，两个一起抓效率最高。"
这个思路有没有问题？应该怎么做？

【要求】
- 回答要具体，能让人照着做
- 如果他的想法有问题，说清楚"错在哪一步推理上"
- 用中文
""",
    "exec": """你是刚入职的实习生，现在要**实际动手**，不是给计划。

【任务】
按技能包里的第 2 步（探环境），真正把探环境跑一遍，然后向我汇报。

具体要求：
1. 找到并运行技能包里的探环境脚本，拿到**真实输出**。
2. 汇报这几项的真实值：操作系统/平台、有没有 GPU、GPU 型号、显存总量、Python 版本、装了哪些训练相关的库。
3. 如果某项拿不到，**直接说拿不到、为什么**，不要猜、不要用"通常""应该"这种词。
4. 顺便告诉我：按这台机器的实际情况，第 3 步选三档精度里的哪一档，依据是哪几个数字。

【约束】
- 必须真的跑命令，用真实输出回答
- 不要编造硬件信息
- 不要下载任何东西、不要装包
- 用中文，结论放前面
""",
}


class Bridge:
    """spawn 一个全新的 dcode ACP 进程，用 MCP 协议驱动它。"""

    def __init__(self, bridge_py: str, python: str, endpoint: str, model: str,
                 cwd: str, logdir: Path, tag: str, debug_port: int = 5679):
        logdir.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env.update({
            "OPENAI_API_BASE": endpoint,
            "OPENAI_API_KEY": env.get("OPENAI_API_KEY", "empty"),
            "DCODE_BRIDGE_MODEL": model,
            "DCODE_BRIDGE_CWD": cwd,
            "DCODE_BRIDGE_LOG": str(logdir / f"bridge-{tag}.log"),
            "DCODE_ACP_DEBUG_PORT": str(debug_port),
            "PYTHONUNBUFFERED": "1",
            "PYTHONUTF8": "1",
        })
        env.pop("DCODE_BRIDGE_NO_WARMUP", None)
        self.err = open(logdir / f"stderr-{tag}.log", "w", encoding="utf-8")
        self.p = subprocess.Popen(
            [python, "-X", "frozen_modules=off", bridge_py],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.err,
            env=env, text=True, encoding="utf-8", bufsize=1,
        )
        self._id = 0

    def _send(self, method: str, params: dict | None = None, notify: bool = False):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        if not notify:
            self._id += 1
            msg["id"] = self._id
        self.p.stdin.write(json.dumps(msg, ensure_ascii=False) + "\n")
        self.p.stdin.flush()
        return msg.get("id")

    def _read(self, want: int, timeout: float):
        end = time.time() + timeout
        while time.time() < end:
            line = self.p.stdout.readline()
            if not line:
                raise RuntimeError("bridge 提前退出，看 stderr 日志")
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if obj.get("id") == want:
                return obj
        raise TimeoutError(f"等 id={want} 超时")

    def open(self, timeout: float = 180):
        i = self._send("initialize", {"protocolVersion": "2024-11-05",
                                      "capabilities": {},
                                      "clientInfo": {"name": "selftest", "version": "1.0"}})
        self._read(i, timeout)
        self._send("notifications/initialized", {}, notify=True)

    def ask(self, prompt: str, timeout: float) -> str:
        i = self._send("tools/call", {"name": "dcode_ask",
                                      "arguments": {"prompt": prompt, "timeout": timeout - 60}})
        r = self._read(i, timeout)
        res = r.get("result") or {}
        if "content" in res:
            return "\n".join(c.get("text", "") for c in res["content"])
        return json.dumps(res, ensure_ascii=False)

    def close(self):
        try:
            self.p.terminate()
            self.p.wait(timeout=10)
        except Exception:
            self.p.kill()
        self.err.close()


# ---------------------------------------------------------------- 判读

TOOL_RE = re.compile(r"^• 调用 (.+)$", re.M)
PATH_STYLES = [
    ("unix-abs", re.compile(r"^/[A-Za-z]/")),      # /d/Code
    ("win-drive", re.compile(r"^[A-Za-z]:[\\/]")),  # D:\ 或 D:/
    ("longpath", re.compile(r"^//\?/")),           # //?/D:/
    ("unix-upper", re.compile(r"^/[A-Z][A-Za-z]*/")),  # /D/Code 这种
]


def analyze(answer: str) -> dict:
    cmds = [c.strip().split("\n")[0][:120] for c in TOOL_RE.findall(answer)]
    uniq = Counter(cmds)
    dup = sum(n - 1 for n in uniq.values() if n > 1)
    styles = set()
    for c in cmds:
        for token in re.findall(r"[^\s\"']*[/\\][^\s\"']*", c):
            for name, rx in PATH_STYLES:
                if rx.match(token):
                    styles.add(name)
    fails = len(re.findall(r"状态:\s*(failed|error)|No such file|not found", answer, re.I))
    return {
        "工具调用": len(cmds),
        "去重后": len(uniq),
        "重复率": f"{(dup / len(cmds) * 100):.0f}%" if cmds else "—",
        "路径写法": sorted(styles),
        "失败/报错提及": fails,
        "答案字数": len(answer),
    }


def verdict(st: dict) -> list[str]:
    out = []
    if not st["工具调用"]:
        out.append("⚠ 没有工具调用——可能它根本没动手，答案不可信")
    r = st["重复率"]
    if r != "—" and int(r.rstrip("%")) > 30:
        out.append(f"⚠ 重复率 {r} > 30%：入口不清楚，它在反复确认同一件事")
    if len(st["路径写法"]) > 2:
        out.append(f"⚠ 路径写法 {len(st['路径写法'])} 种：根目录约定没生效")
    if st["失败/报错提及"] > 0:
        out.append(f"⚠ 出现 {st['失败/报错提及']} 次失败：读文件/执行的兜底没写清")
    return out or ["✅ 三项指标正常"]


def main():
    ap = argparse.ArgumentParser(description="用模型自测技能包的可用性")
    ap.add_argument("--endpoint", default=os.environ.get("OPENAI_API_BASE", ""),
                    help="OpenAI 兼容 base_url，如 https://host:8443/v1")
    ap.add_argument("--model", default="openai:Qwen3.8-27B", help="传给 dcode 的 --model")
    ap.add_argument("--cwd", default=str(SKILL_ROOT), help="被测技能包目录（agent 的工作目录）")
    ap.add_argument("--task", default="link", help="link|plan|trap|exec|all")
    ap.add_argument("--bridge", default=os.environ.get(
        "DCODE_BRIDGE", r"D:/Code/deepagents/libs/code/dcode_mcp_bridge.py"))
    ap.add_argument("--python", default=sys.executable, help="跑 bridge 的 python")
    ap.add_argument("--out", default=str(HERE / "selftest-out"), help="结果目录")
    ap.add_argument("--timeout", type=float, default=900, help="单个任务超时秒数")
    ap.add_argument("--port", type=int, default=5679, help="debugpy 端口（避开 5678）")
    ap.add_argument("--list", action="store_true", help="列出任务后退出")
    a = ap.parse_args()

    if a.list:
        for k, v in TASKS.items():
            print(f"{k:6s} {len(v):4d} 字  {v.splitlines()[0][:60]}")
        return

    if not a.endpoint:
        sys.exit("缺少 --endpoint（或设 OPENAI_API_BASE 环境变量）")
    if not Path(a.bridge).exists():
        sys.exit(f"找不到 bridge：{a.bridge}（用 --bridge 指定 dcode_mcp_bridge.py）")

    names = list(TASKS) if a.task == "all" else [a.task]
    for n in names:
        if n not in TASKS:
            sys.exit(f"未知任务 {n}，可选：{', '.join(TASKS)}")

    outdir = Path(a.out)
    summary = []
    for name in names:
        tag = f"{name}"
        b = Bridge(a.bridge, a.python, a.endpoint, a.model, a.cwd, outdir, tag, a.port)
        t0 = time.time()
        try:
            b.open()
            print(f"[{name}] 已启动，cwd={a.cwd}", flush=True)
            ans = b.ask(TASKS[name], a.timeout)
        finally:
            b.close()
        el = time.time() - t0
        (outdir / f"answer-{tag}.md").write_text(ans, encoding="utf-8")
        st = analyze(ans)
        print(f"[{name}] {el:.0f}s  {st['工具调用']} 次调用  重复率 {st['重复率']}  → answer-{tag}.md")
        for line in verdict(st):
            print("        " + line)
        summary.append((name, el, st))

    if len(summary) > 1:
        print("\n=== 汇总 ===")
        print(f"{'任务':6s}{'耗时':>8s}{'调用':>6s}{'重复率':>8s}{'路径写法':>10s}")
        for n, el, st in summary:
            print(f"{n:6s}{el:>7.0f}s{st['工具调用']:>6d}{st['重复率']:>8s}{len(st['路径写法']):>10d}")


if __name__ == "__main__":
    main()
