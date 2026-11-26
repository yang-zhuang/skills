#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
模型结构解析 + 三档显存粗估。

输入可以是：
  - 本地模型目录（含 config.json）
  - 本地 config.json 路径
  - HF 仓库 id（如 Qwen/Qwen2.5-7B-Instruct），只拉 config.json，不拉权重

输出：参数量、结构关键字段、全参 / LoRA / QLoRA 三档显存估算与判定。

**估算就是估算**：权重部分误差很小（由参数量直接算），激活部分是经验系数，
误差可达 ±30%。任何结论都必须用 references/04_train.md 的 smoke test 实测复核。

用法：
  python scripts/probe_model.py ./models/Qwen2.5-7B-Instruct
  python scripts/probe_model.py Qwen/Qwen2.5-7B-Instruct --seq-len 8192 --batch 1
  python scripts/probe_model.py <config.json> --vram-mib 24576 --json
"""

import argparse
import json
import sys
from pathlib import Path

import requests

HF_RAW = "https://huggingface.co/{repo}/raw/main/config.json"
MIRROR_RAW = "https://hf-mirror.com/{repo}/raw/main/config.json"

# bytes / 参数（AdamW + bf16 混合精度的经验值）
BYTES_FULL = 16        # 权重2 + 梯度2 + Adam m/v 8 + fp32 master 4
BYTES_FULL_CKPT = 12   # 开梯度检查点后权重/优化器不变，省的是激活；这里给保守值
BYTES_LORA = 2         # bf16 基座 + 很小 adapter
BYTES_QLORA = 0.5      # 4bit NF4 基座 + 很小 adapter

# 激活经验系数（bytes per (batch × seq × hidden × layer)）
K_ACT_NOCKPT = 20
K_ACT_CKPT = 5


def load_config(src: str, hf_base: str | None) -> tuple[dict, str]:
    p = Path(src)
    if p.is_dir():
        cfg = p / "config.json"
        if not cfg.exists():
            sys.exit(f"FATAL: {p} 下没有 config.json")
        return json.loads(cfg.read_text(encoding="utf-8")), str(p)
    if p.is_file():
        return json.loads(p.read_text(encoding="utf-8")), str(p)

    # 当作 HF repo id
    urls = [HF_RAW.format(repo=src)]
    if hf_base:
        urls.insert(0, hf_base.rstrip("/") + f"/{src}/raw/main/config.json")
    else:
        urls.insert(0, MIRROR_RAW.format(repo=src))
    for u in urls:
        try:
            r = requests.get(u, timeout=30)
            if r.status_code == 200:
                return r.json(), src
        except requests.RequestException:
            continue
    sys.exit(f"FATAL: 拉不到 {src} 的 config.json；请传本地目录或检查网络")


def pick(cfg: dict, *keys, default=None):
    for k in keys:
        if k in cfg and cfg[k] is not None:
            return cfg[k]
    return default


# 多模态模型的文本塔参数在自己的子配置里，不展开就会误判成"不是标准 decoder"。
# 2026-11-15 实测：Qwen3.5-2B 的 config.json 顶层只有 architectures / text_config / vision_config，
# 直接读顶层 hidden_size 会 FATAL。
TEXT_SUBCONFIG_KEYS = ("text_config", "llm_config", "language_config", "text_model_config", "decoder")


def find_text_config(cfg: dict) -> tuple[str | None, dict]:
    """返回 (子配置键名, 该用哪份配置)；顶层就是文本模型时键名为 None。"""
    for key in TEXT_SUBCONFIG_KEYS:
        sub = cfg.get(key)
        if not isinstance(sub, dict):
            continue
        if pick(sub, "hidden_size", "n_embd", "d_model") and \
           pick(sub, "num_hidden_layers", "n_layer", "num_layers"):
            return key, sub
    return None, cfg


def estimate(cfg: dict) -> dict:
    sub_key, tcfg = find_text_config(cfg)
    h = pick(tcfg, "hidden_size", "n_embd", "d_model")
    layers = pick(tcfg, "num_hidden_layers", "n_layer", "num_layers")
    if not h or not layers:
        sys.exit("FATAL: config.json 里找不到 hidden_size / num_hidden_layers，"
                 "可能不是标准 decoder 模型，请手工确认结构"
                 f"（已尝试子配置：{', '.join(TEXT_SUBCONFIG_KEYS)}）")

    inter = pick(tcfg, "intermediate_size", "n_inner", default=4 * h)
    vocab = pick(tcfg, "vocab_size", default=0)
    heads = pick(tcfg, "num_attention_heads", "n_head", default=1)
    kv_heads = pick(tcfg, "num_key_value_heads", default=heads)
    head_dim = pick(tcfg, "head_dim", default=h // heads if heads else h)
    tie = bool(pick(tcfg, "tie_word_embeddings", default=pick(cfg, "tie_word_embeddings", default=False)))

    experts = pick(tcfg, "num_local_experts", "num_experts", "n_routed_experts", default=1)
    is_moe = int(experts or 1) > 1

    attn = h * heads * head_dim + 2 * (h * kv_heads * head_dim) + heads * head_dim * h
    mlp = 3 * h * inter * (int(experts) if is_moe else 1)
    per_layer = attn + mlp + 2 * h
    emb = vocab * h
    lm_head = 0 if tie else vocab * h
    total = emb + layers * per_layer + lm_head + h

    return {
        "hidden_size": h,
        "num_layers": layers,
        "intermediate_size": inter,
        "vocab_size": vocab,
        "num_attention_heads": heads,
        "num_key_value_heads": kv_heads,
        "head_dim": head_dim,
        "tie_word_embeddings": tie,
        "is_moe": is_moe,
        "experts": int(experts or 1),
        "params": int(total),
        "max_position_embeddings": pick(tcfg, "max_position_embeddings", default=None),
        "text_config_key": sub_key,
        "has_vision": "vision_config" in cfg,
        "architectures": cfg.get("architectures"),
    }


def act_mib(h: int, layers: int, batch: int, seq: int, ckpt: bool) -> float:
    k = K_ACT_CKPT if ckpt else K_ACT_NOCKPT
    return batch * seq * h * layers * k / 1024 ** 2


def plan(params: int, h: int, layers: int, batch: int, seq: int,
         vram_mib: int | None) -> list[dict]:
    p_b = params / 1e9
    rows = []
    for name, bytes_per, ckpt in [
        ("全参 bf16 + AdamW", BYTES_FULL, False),
        ("全参 + 梯度检查点", BYTES_FULL_CKPT, True),
        ("LoRA（bf16 基座）", BYTES_LORA, False),
        ("QLoRA（4bit 基座）", BYTES_QLORA, False),
    ]:
        weight = params * (2 if bytes_per >= 2 else 0.5) / 1024 ** 3
        optim = params * (bytes_per - (2 if bytes_per >= 2 else 0.5)) / 1024 ** 3
        act = act_mib(h, layers, batch, seq, ckpt)
        total_mib = (weight + optim) * 1024 + act
        fit = ""
        if vram_mib:
            ratio = total_mib / vram_mib
            fit = "✅ 放得下" if ratio <= 0.85 else ("⚠️ 勉强" if ratio <= 1.0 else "❌ 放不下")
        rows.append({
            "plan": name,
            "weight_gib": round(weight, 2),
            "optim_gib": round(optim, 2),
            "act_mib": round(act, 0),
            "total_gib": round(total_mib / 1024, 2),
            "fit": fit,
        })
    return rows


def render(src: str, s: dict, rows: list[dict], args, vram_mib) -> str:
    L = [f"# 模型探测：{src}", ""]
    L.append("## 结构")
    if s["text_config_key"]:
        L.append(f"> **多模态模型**（{s['architectures']}）：结构取自 `{s['text_config_key']}`，"
                 f"视觉塔参数**未计入**，参数量与显存估算偏低，必须以 smoke test 实测为准。")
        L.append("")
    L.append("| 项 | 值 |")
    L.append("|---|---|")
    for k in ["hidden_size", "num_layers", "intermediate_size", "vocab_size",
              "num_attention_heads", "num_key_value_heads", "head_dim",
              "tie_word_embeddings", "is_moe", "experts", "max_position_embeddings"]:
        L.append(f"| {k} | {s[k]} |")
    L.append("")
    L.append(f"## 参数量：**{s['params'] / 1e9:.2f} B**"
             f"（bf16 权重约 {s['params'] * 2 / 1024 ** 3:.1f} GiB）")
    if s["is_moe"]:
        L.append("")
        L.append("> MoE 模型：参数量包含全部专家，实际激活参数远小于此，"
                 "下面的显存估算会**明显偏高**，必须实测。")
    L.append("")
    L.append(f"## 三档显存（batch={args.batch}, seq_len={args.seq_len}, "
             f"{'开' if args.ckpt else '不开'}梯度检查点）")
    if vram_mib:
        L.append(f"参照显存：**{vram_mib} MiB**（单卡空闲）")
    L.append("")
    L.append("| 方案 | 权重 GiB | 梯度+优化器 GiB | 激活 MiB | 合计 GiB | 判定 |")
    L.append("|---|---|---|---|---|---|")
    for r in rows:
        L.append(f"| {r['plan']} | {r['weight_gib']} | {r['optim_gib']} | "
                 f"{r['act_mib']:.0f} | {r['total_gib']} | {r['fit']} |")
    L.append("")
    L.append("**必读**：")
    L.append("- 权重按参数量精确算；**激活是经验系数，误差 ±30%**，长序列尤其不准。")
    L.append("- 上表没算 KV cache、CUDA context、碎片；实际占用会比「合计」更高。")
    L.append("- 判定只作初筛，**必须**用 20~50 步 smoke test 实测峰值显存"
             "（references/04_train.md）。")
    return "\n".join(L)


def main() -> None:
    parser = argparse.ArgumentParser(description="模型结构解析 + 显存粗估")
    parser.add_argument("src", help="本地模型目录 / config.json / HF repo id")
    parser.add_argument("--seq-len", type=int, default=4096)
    parser.add_argument("--batch", type=int, default=1)
    parser.add_argument("--ckpt", action="store_true", help="按开梯度检查点估算激活")
    parser.add_argument("--vram-mib", type=int, default=None,
                        help="单卡可用显存 MiB；不传则自动探测")
    parser.add_argument("--hf-base", default=None, help="HF 镜像前缀，如 https://hf-mirror.com")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    vram = args.vram_mib
    if vram is None:
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            from probe_env import probe_gpus  # noqa: PLC0415
            g = probe_gpus()
            if g.get("available"):
                vram = sum(x["memory_total_mib"] - x["memory_used_mib"] for x in g["gpus"])
        except Exception:  # noqa: BLE001
            vram = None

    cfg, name = load_config(args.src, args.hf_base)
    s = estimate(cfg)
    rows = plan(s["params"], s["hidden_size"], s["num_layers"],
                args.batch, args.seq_len, vram)

    if args.json:
        print(json.dumps({"source": name, "structure": s, "plans": rows,
                          "vram_mib": vram, "seq_len": args.seq_len,
                          "batch": args.batch}, ensure_ascii=False, indent=2))
    else:
        print(render(name, s, rows, args, vram))


if __name__ == "__main__":
    main()
