"""通用 LoRA SFT 训练脚本（从 e2e2 实测版泛化，loss 1.341→0.146 跑通）。

用法（在装了 torch/trl/peft 的训练环境里，通常是 WSL）：
    python train.py --config config.yaml --output-dir outputs/full          # 全量
    python train.py --config config.yaml --max-steps 25 --output-dir outputs/smoke  # smoke

配套：train.sh（smoke/full 两模式）、config.example.yaml（复制成 config.yaml 改）。
不要直接改这个脚本里的常量——所有场景相关的东西都在 config.yaml。

【这是实测过的起点，不是标准答案】
它是 trl SFTTrainer + peft LoRA 的一条路径。参数含义、为什么这么设、字段名去哪查，
见 references/04_train.md。"照抄能跑"不等于"理解了"——替换成你的场景才算学会。

【从 e2e2 原样保留的精华（别删）】
- smoke 和 full 共用这一份代码，只靠 --max-steps / --output-dir 覆盖参数
- s/step 双口径：train_runtime 主口径 + 逐步 wall clock 交叉验证，丢前 2 步取保守值
- train() 后强制 model.eval() 再生成（案例 10：train 模式生成乱码）
- 记录峰值显存、loss 趋势、全程时长预估（对接 04_train.md §4 五项 + 10_measure.md）
- 生成只从 user turn 出发，不给 gold（诚实检查格式）

【相对 e2e2 的改造（都是踩过坑的）】
- 所有路径从 config 读，不留 /mnt/d/Code/_e2e2 死路径
- 内置 thinking 配置：Qwen3/3.5 系列数据无 think 标签时，train 和 infer 都要关
  （04_train.md §2.1 + 05_verify.md §2.1：不关会学成"空 think + 套模板"的假提升）
- 数据格式自动识别：messages / prompt-response / query-response 都能读
"""
import argparse
import json
import math
import re
import time

import torch
import yaml
from datasets import load_dataset
from peft import LoraConfig
from transformers import AutoModelForCausalLM, AutoTokenizer, TrainerCallback
from trl import SFTConfig, SFTTrainer


class StepTimer(TrainerCallback):
    """逐步墙钟时间，用于"丢前 2 步"的 s/step 交叉验证。

    主口径是 trainer 的 train_runtime / 步数（见 10_measure.md §1.2）；
    这个逐步计时只是交叉核对，最后取两者较大的保守值做时长预估。
    """

    def __init__(self):
        self.dts = []
        self._prev_t = None
        self._prev_step = 0

    def on_step_end(self, args, state, control, **kw):
        now = time.perf_counter()
        if self._prev_t is not None and state.global_step > self._prev_step:
            self.dts.append({"step": state.global_step, "dt": now - self._prev_t})
        self._prev_t = now
        self._prev_step = state.global_step


def normalize_dataset(ds, data_cfg):
    """把三种常见格式统一成 trl 要的 messages 列。

    这是 e2e2 之后补的：当时 check_dataset 不认 prompt/response 吃了亏，
    这里训练侧也做一层兼容，避免"数据格式"卡住训练。
    """
    cols = set(ds.column_names)
    if "messages" in cols:
        return ds
    if "query" in cols and "response" in cols:
        qk, ak = "query", "response"
    elif "prompt" in cols and (
        {"completion", "response", "answer", "output"} & cols
    ):
        qk = "prompt"
        ak = next(k for k in ("completion", "response", "answer", "output") if k in cols)
    else:
        raise ValueError(f"不认识的数据格式 {cols}，需要 messages / query+response / prompt+response")

    def to_messages(ex):
        return {"messages": [
            {"role": "user", "content": str(ex[qk])},
            {"role": "assistant", "content": str(ex[ak])},
        ]}

    return ds.map(to_messages, remove_columns=ds.column_names)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True, help="config.yaml 路径")
    ap.add_argument("--max-steps", type=int, default=None,
                    help="覆盖 epoch 做 smoke test；只跑这么多步")
    ap.add_argument("--logging-steps", type=int, default=None)
    ap.add_argument("--save-steps", type=int, default=None)
    ap.add_argument("--output-dir", default=None)
    args = ap.parse_args()

    with open(args.config, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    data_cfg, lora_cfg, tr = cfg["data"], cfg["lora"], cfg["train"]

    out_dir = args.output_dir or tr["output_dir"]
    logging_steps = args.logging_steps if args.logging_steps is not None else tr["logging_steps"]
    save_steps = args.save_steps if args.save_steps is not None else tr["save_steps"]
    max_steps = args.max_steps if args.max_steps is not None else -1
    model_id = cfg["model"]

    tok = AutoTokenizer.from_pretrained(model_id, trust_remote_code=True)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token

    model = AutoModelForCausalLM.from_pretrained(
        model_id,
        torch_dtype=torch.bfloat16,
        attn_implementation=tr.get("attn_implementation", "eager"),
        trust_remote_code=True,
    )

    lora = LoraConfig(
        r=lora_cfg["r"], lora_alpha=lora_cfg["lora_alpha"],
        lora_dropout=lora_cfg["lora_dropout"],
        target_modules=lora_cfg["target_modules"],
        bias="none", task_type="CAUSAL_LM",
    )

    train_ds = normalize_dataset(
        load_dataset("json", data_files=data_cfg["train"], split="train"), data_cfg)
    probe_ds = normalize_dataset(
        load_dataset("json", data_files=data_cfg["probe"], split="train"), data_cfg)

    # thinking 开关（04_train.md §2.1）：数据无 think 标签时，train 和 infer 都关。
    # 关键：SFTConfig 的 chat_template_kwargs 决定训练时模板怎么渲染；
    # 推理时必须传同一个开关，否则就是 05_verify.md §2.1 的假对比。
    enable_thinking = data_cfg.get("enable_thinking", False)
    extra_kwargs = {}
    if "chat_template_kwargs" in tr or not enable_thinking:
        extra_kwargs["chat_template_kwargs"] = {"enable_thinking": enable_thinking}

    sft_args = SFTConfig(
        output_dir=out_dir,
        max_length=data_cfg["seq_len"],
        packing=data_cfg.get("packing", False),
        assistant_only_loss=data_cfg.get("assistant_only_loss", True),
        per_device_train_batch_size=tr["per_device_train_batch_size"],
        gradient_accumulation_steps=tr["gradient_accumulation_steps"],
        learning_rate=tr["learning_rate"],
        num_train_epochs=tr["num_train_epochs"] if max_steps <= 0 else 1,
        max_steps=max_steps if max_steps > 0 else -1,
        warmup_steps=tr.get("warmup_steps", 1),
        gradient_checkpointing=tr["gradient_checkpointing"],
        bf16=True,
        logging_steps=logging_steps,
        save_steps=save_steps,
        save_total_limit=tr.get("save_total_limit", 3),
        optim=tr["optim"],
        weight_decay=tr["weight_decay"],
        max_grad_norm=tr["max_grad_norm"],
        use_liger_kernel=tr.get("use_liger_kernel", False),  # 显存紧开 true，见 04_train.md §3.1
        eval_strategy="no",
        remove_unused_columns=False,
        report_to=[],
        seed=tr.get("seed", 0),
        **extra_kwargs,
    )

    timer = StepTimer()
    trainer = SFTTrainer(
        model=model, args=sft_args, train_dataset=train_ds,
        processing_class=tok, peft_config=lora, callbacks=[timer],
    )

    t0 = time.perf_counter()
    trainer.train()
    wall_seconds = time.perf_counter() - t0
    trainer.save_model(out_dir)
    tok.save_pretrained(out_dir)

    # ---- smoke 五项报告（04_train.md §4）----
    peak_mib = torch.cuda.max_memory_allocated() / (1024 ** 2)
    st = trainer.state
    final_log = st.log_history[-1] if st.log_history else {}
    total_steps = st.global_step
    train_runtime = final_log.get("train_runtime", float("nan"))

    s_runtime = train_runtime / total_steps if total_steps else float("nan")
    dts = timer.dts
    steady = sorted(d["dt"] for d in dts[2:]) if len(dts) >= 3 else sorted(d["dt"] for d in dts)
    s_steady = (sum(steady) / len(steady)) if steady else float("nan")
    s_cons = max(s_runtime, s_steady) if (
        s_runtime == s_runtime and s_steady == s_steady
    ) else (s_runtime if s_runtime == s_runtime else s_steady)

    losses = [l["loss"] for l in st.log_history if "loss" in l]
    first_loss = losses[0] if losses else float("nan")
    last_loss = losses[-1] if losses else float("nan")

    n_train = len(train_ds)
    eff_batch = tr["per_device_train_batch_size"] * tr["gradient_accumulation_steps"]
    steps_per_epoch = math.ceil(n_train / eff_batch)
    n_epochs = tr["num_train_epochs"]
    total_hours = s_cons * steps_per_epoch * n_epochs / 3600.0

    # ---- 生成检查：必须 eval()，只从 user turn 出发 ----
    model.eval()
    sample = probe_ds[0]["messages"]
    user_turns = [m for m in sample if m.get("role") == "user"]
    prompt = tok.apply_chat_template(
        user_turns, tokenize=False, add_generation_prompt=True,
        enable_thinking=enable_thinking)
    inputs = tok([prompt], return_tensors="pt").to(model.device)
    with torch.inference_mode():
        out_ids = model.generate(
            **inputs, do_sample=False, max_new_tokens=128,
            temperature=1.0, top_p=1.0)
    gen = tok.decode(out_ids[0][inputs["input_ids"].shape[1]:],
                     skip_special_tokens=True).strip()
    gen = re.sub(r"<think>.*?</think>", "", gen, flags=re.DOTALL).strip()  # 剥 think（05_verify §2.2）

    report = {
        "peak_vram_mib": round(peak_mib, 1),
        "vram_ceiling_mib": tr.get("vram_ceiling_mib", 6714),
        "peak_ok": bool(peak_mib <= tr.get("vram_ceiling_mib", 6714)),
        "no_oom": True, "num_steps": total_steps,
        "train_runtime": round(train_runtime, 4),
        "s_per_step_runtime": round(s_runtime, 4),
        "s_per_step_steady_drop2": round(s_steady, 4),
        "s_step_conservative": round(s_cons, 4),
        "first_loss": first_loss, "last_loss": last_loss,
        "loss_moving": bool(last_loss < first_loss) if losses else None,
        "n_train": n_train, "eff_batch": eff_batch,
        "steps_per_epoch": steps_per_epoch, "num_epochs": n_epochs,
        "proj_total_hours": round(total_hours, 4),
        "time_ok": bool(total_hours <= tr.get("time_budget_hours", 2.0)),
        "enable_thinking": enable_thinking,
        "gen_len": len(gen),
        "gen_ok": bool(gen) and ("//" * 2 not in gen) and (len(set(gen)) > 2),
        "gen": gen,
    }
    print("SMOKE_REPORT_JSON=" + json.dumps(report, ensure_ascii=False))
    print("\n--- generated (eval mode, probe[0], user turn only) ---")
    print("GENERATED:", gen)


if __name__ == "__main__":
    main()
