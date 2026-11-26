"""selftest_e2e.py — 让模型完整走完七步，每步验产出物。

和 selftest.py 的分工
--------------------
    selftest.py      四个探针，分钟级，每次新 session     → 改完文档快速回归
    selftest_e2e.py  完整七步，小时级，同一 session 连续跑 → 大改后验全流程

**它测的不是"跑得通"，是"有没有在预定步数内交出产出物"。**
实测最常见的失败不是写错参数，是钻进了验证兔子洞、训练脚本根本没写出来
（见 references/09_real_cases.md 案例 16）。

前置（缺一不可）
----------------
1. 一个 OpenAI 兼容的推理服务，且服务端装着 dcode 要用的模型
2. 本机有 dcode（`dcode_mcp_bridge.py`）
3. 一个已备好的工作目录：
   - `<workdir>/data/train.jsonl`（建议 50~200 条，messages 格式）
   - `<workdir>/data/probe.jsonl`（20 条左右）
4. 一个已装好 torch/trl 的 python（脚本不会替你装环境，也不会下载模型）
   —— 通过 `--train-python` 告诉它，比如 `~/Code/x/.venv/bin/python`（WSL 内路径）

用法
----
    python scripts/selftest_e2e.py \
        --endpoint https://host:8443/v1 \
        --workdir D:/Code/_e2e \
        --train-python '~/Code/sft-case/.venv/bin/python' \
        --model-path /mnt/d/modelscope/Qwen3.5-2B

    python scripts/selftest_e2e.py ... --start 4 --stop 5   # 只重跑第 4、5 步

结果落在 `<workdir>/e2e-out/`：`step<N>.md` 含轨迹与回答，`progress.jsonl` 一行一步。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from selftest import Bridge, analyze  # noqa: E402

SKILL_ROOT = Path(__file__).resolve().parent.parent


def build_steps() -> dict[int, tuple[str, list[str]]]:
    """七步的正文与产出物。环境事实由 main() 统一拼在前面。"""
    return {
        1: ("【第 1 步：定任务】\n\n"
            "读技能包第 1 步的要求（references/00_workflow.md 的任务模板 + SKILL.md），"
            "在 cwd 写出 00_task_profile.md。\n\n"
            "汇报三件事：1) 精度标准怎么定的、依据 2) 时间档和显存硬顶填了什么 "
            "3) 唯一卡住的维度判给谁。\n\n**只做第 1 步。**",
            ["00_task_profile.md"]),
        2: ("【第 2 步：探环境】\n\n按技能包第 2 步，用真实数字。\n\n"
            "1. 跑 scripts/probe_env.py\n"
            "2. 跑 scripts/probe_model.py 探模型，--seq-len 1024\n"
            "3. 写 01_env_report.md\n\n"
            "汇报：实测可用显存、三档估算、选哪档为什么。\n\n**只做第 2 步。**",
            ["01_env_report.md"]),
        3: ("【第 3 步：定方案】\n\n按技能包第 3 步。必须先查文档再写参数名。\n\n"
            "1. 用 scripts/docs_outline.py 查三库在精度档位 / 显存优化上各有什么\n"
            "2. 写 02_training_plan.md：档位、上下文长度、用哪个库、"
            "**为什么不用另外两个**、验证方法\n"
            "3. 如果列了 3 个以上候选，写 plan.json 并用 scripts/plot_plan.py "
            "画 plan.png（估算值必须标 measured:false）\n\n"
            "汇报：选了哪个组合、依据哪份文档哪一节、放弃的两条路是什么。\n\n**只做第 3 步。**",
            ["02_training_plan.md"]),
        4: ("【第 4 步：备数据】\n\n按技能包第 4 步。\n\n"
            "1. 跑 scripts/check_dataset.py 体检 train.jsonl，--tokenizer <模型> --seq-len 1024\n"
            "2. 判断格式、超长比例\n3. 写 03_dataset_report.md\n\n"
            "汇报：token 分布 p50/p99/max、超长比例与处理、label mask 开不开。\n\n"
            "**注意：label mask 只决定「开/不开」，不要去验证库的内部实现**——"
            "参数拼写和实际行为留到第 5 步，以跑通为准。\n\n**只做第 4 步。**",
            ["03_dataset_report.md"]),
        5: ("【第 5 步：写脚本 + smoke test】\n\n按技能包第 5 步。"
            "**这一步的产出物是能跑的脚本，不是验证报告。**\n\n"
            "1. 写 train.py + train.sh + config.yaml（可从技能包 templates/ 复制起步），"
            "参数和 02_training_plan.md 一致\n"
            "2. 跑 20~30 步 smoke test，记录 train_runtime、峰值显存、s/step（丢前 2 步）、"
            "生成一条看格式\n"
            "3. OOM / 报错就按技能包顺序调，一次只改一个\n\n"
            "汇报：五项各过没过、实测 s/step 和峰值显存、推算 1 epoch 多久。\n\n"
            "**交付要求（04_train.md §7）：把 train.py / train.sh / config.yaml 的"
            "完整内容贴在你的回答里，并写出 smoke/full 两条命令、"
            "以及用户按自己场景必须改的字段。写在磁盘上不算交付。**\n\n"
            "**如果你正在写第 3 个 *_probe.py，停下来去写 train.sh。**\n\n**只做第 5 步。**",
            ["train.sh", "train.py"]),
        6: ("【第 6 步：跑训练】\n\n按技能包第 6 步，真跑。\n\n"
            "1. 用第 5 步验证过的配置跑 1 个 epoch\n2. 产物放 outputs/\n"
            "3. 读 train_loss / eval_loss\n\n"
            "汇报：实际多久、loss 从多少到多少、有无 NaN。\n\n**只做第 6 步。**",
            ["outputs"]),
        7: ("【第 7 步：验收】\n\n按技能包第 7 步。\n\n"
            "1. 加载 adapter，对 probe.jsonl 生成\n2. 和 base 对比至少 5 条\n"
            "3. 注意 05_verify.md 2.1/2.2 节的坑：\n"
            "   - 2.1 base 和 tuned 必须用同一套 thinking 配置，否则是假对比\n"
            "   - 2.2 剥 think 要删整段（<think>.*?</think>），只删结束标签会制造 base 0 分的假象\n"
            "4. 写 05_eval_report.md；不达标必须写回退决策（回第几步、只改哪个变量）\n\n"
            "汇报：生成前做了什么、两者差别、算不算成功依据哪一条。\n\n**这是最后一步。**",
            ["05_eval_report.md"]),
    }


def check(workdir: Path, names: list[str]) -> str:
    if not names:
        return "（本步无固定产出物）"
    out = []
    for n in names:
        p = workdir / n
        if p.exists():
            size = sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.is_dir() else p.stat().st_size
            out.append(f"OK {n} ({size} B)")
        else:
            out.append(f"MISSING {n}")
    return "  ".join(out)


def check_delivery(ans: str, step: int) -> str:
    """第 5 步要检查的不只是文件在不在，还有脚本有没有交付给用户。

    案例（e2e2）：train.sh 写完了、文件在磁盘上，但 agent 回复是超时空错误，
    用户从头到尾没看到脚本。04_train.md §7 把它定为交付要求。
    """
    if step != 5:
        return ""
    # 回复里要能看到代码内容：train.py 的特征串 + 启动命令
    has_code = "def main" in ans or "SFTTrainer" in ans or "from trl" in ans
    has_cmd = "train.sh" in ans and ("smoke" in ans or "full" in ans)
    if has_code and has_cmd:
        return "✅ 脚本已交付（回复含代码+启动命令）"
    return "⚠️ 脚本可能未交付给用户（回复里没贴出代码/命令，见 04_train.md §7）"


def check_rollback(workdir: Path) -> str:
    """第 7 步不能只看文件存在。

    不达标却没写回退决策，等于流程断了——这是案例 16 的教训：
    **验收标准写松了，失败会被判成成功**（当时第 5 步产出物写成"无",
    结果它写 5 个 probe 也算通过）。
    """
    p = workdir / "05_eval_report.md"
    if not p.exists():
        return ""
    t = p.read_text(encoding="utf-8", errors="ignore")
    failed = any(k in t for k in ("未达", "不达标", "❌"))
    has_rb = any(k in t for k in ("回退", "回到第"))
    if failed and not has_rb:
        return "⚠️ 未达标但缺回退决策（回第几步 / 只改哪个变量）"
    return "✅ 未达标已给回退决策" if failed else "✅ 达标"


def main():
    ap = argparse.ArgumentParser(description="完整七步自测")
    ap.add_argument("--endpoint", required=True)
    ap.add_argument("--workdir", required=True, help="已备好的工作目录（含 data/）")
    ap.add_argument("--train-python", default="python", help="装了 torch/trl 的解释器路径")
    ap.add_argument("--model-path", default="<模型路径>", help="模型在训练环境里的路径")
    ap.add_argument("--model", default="openai:Qwen3.8-27B")
    ap.add_argument("--bridge", default=r"D:/Code/deepagents/libs/code/dcode_mcp_bridge.py")
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--start", type=int, default=1)
    ap.add_argument("--stop", type=int, default=7)
    ap.add_argument("--timeout", type=float, default=3600,
                    help="单步上限秒数。写脚本+跑 smoke 这类步骤实测要 25 分钟，别设太小")
    ap.add_argument("--port", type=int, default=5681, help="debugpy 端口，避开被占的 5678")
    a = ap.parse_args()

    workdir = Path(a.workdir).resolve()
    for need in ["data/train.jsonl"]:
        if not (workdir / need).exists():
            sys.exit(f"工作目录缺 {need}：{workdir}")

    ctx = (f"【环境事实（不要重新探测这些）】\n"
           f"- 工作目录（你的 cwd）：{workdir}\n"
           f"- 技能包：{SKILL_ROOT}\n"
           f"- 模型：{a.model_path}（Windows 侧自行换盘符；WSL 内就是这条路径）\n"
           f"- 数据：{workdir}/data/train.jsonl、probe.jsonl，messages 格式\n"
           f"- 训练环境：{a.train_python}，已装好 torch/transformers/trl/peft/bitsandbytes。\n"
           f"  **直接用它，不要新建 venv、不要装包、不要下载。**\n"
           f"- 从 Windows 跑 WSL 命令：wsl -e bash -lc \"命令\"\n"
           f"- 训练产物放 {workdir}/，不要写进技能包目录\n\n"
           f"【硬约束，违反即判定失败】\n"
           f"- 一个事实最多验 3 次，超了就按最保守选项决策，在产出物里标注"
           f"\"未验证｜假设 X｜风险 Y\"，然后往下走\n"
           f"- 每步必须交出指定产出物；**产出物 > 验证**\n")

    steps = build_steps()
    outdir = workdir / "e2e-out"
    outdir.mkdir(parents=True, exist_ok=True)
    prog = outdir / "progress.jsonl"

    b = Bridge(a.bridge, a.python, a.endpoint, a.model, str(workdir), outdir, "e2e", a.port)
    rows = []
    try:
        b.open()
        print(f"[e2e] cwd={workdir}", flush=True)
        for i in range(a.start, a.stop + 1):
            body, arts = steps[i]
            t0 = time.time()
            print(f"[第 {i} 步] ...", flush=True)
            try:
                ans = b.ask(ctx + "\n" + body, a.timeout)
            except Exception as e:
                ans = f"【异常】{type(e).__name__}: {e}"
            el = time.time() - t0
            (outdir / f"step{i}.md").write_text(ans, encoding="utf-8")
            st = analyze(ans)
            art = check(workdir, arts)
            # 0 次调用 + 耗时很久 = 通道断了，不是模型不动脑（案例 17）
            if st["工具调用"] == 0 and el > 60:
                art = f"⚠️ 链路故障（0 调用 / {el:.0f}s）：{ans[:90]}"
            if i == 5:
                extra = check_delivery(ans, i)
                if extra:
                    art = f"{art}  {extra}"
            if i == 7:
                extra = check_rollback(workdir)
                if extra:
                    art = f"{art}  {extra}"
            rec = {"step": i, "seconds": round(el), "calls": st["工具调用"],
                   "dup": st["重复率"], "artifacts": art}
            rows.append(rec)
            with prog.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            print(f"[第 {i} 步] {el:.0f}s {st['工具调用']}调用  {art}", flush=True)
    finally:
        b.close()

    def _ok(r):
        a = r["artifacts"]
        return "MISSING" not in a and "链路故障" not in a
    ok = sum(1 for r in rows if _ok(r))
    failed = [r["step"] for r in rows if not _ok(r)]
    print(f"\n=== {ok}/{len(rows)} 步通过 ===" +
          (f"｜失败：第 {failed} 步" if failed else ""), flush=True)
    for r in rows:
        print(f"第{r['step']}步 {r['seconds']:>5}s {r['calls']:>3}调用  {r['artifacts']}", flush=True)


if __name__ == "__main__":
    main()
