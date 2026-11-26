#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
环境探测：GPU / 显存 / 驱动 / 深度学习库版本 / 磁盘。

**只报事实，不给建议**。能不能全参、能上多长上下文，在
references/02_decide.md 里按这些事实 + 模型结构算，且必须实测复核。

用法：
  python scripts/probe_env.py          # markdown 报告（给人看、给 agent 读）
  python scripts/probe_env.py --json   # JSON（给别的脚本消费）

这个脚本只用标准库，可以在「技能环境」或「训练环境」任意一边跑；
在训练环境里跑能额外拿到 torch / CUDA 的真实信息。
"""

import argparse
import json
import os
import platform as _platform
import shutil
import subprocess
import sys
from importlib import metadata
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent

# 包名 → 说明（import 名和 pip 名不一致的标出来）
LIBS = [
    ("torch", "PyTorch"),
    ("transformers", "模型与 tokenizer 底座"),
    ("trl", "SFT / DPO / GRPO 官方库"),
    ("unsloth", "省显存训练（含自己的 kernel）"),
    ("ms-swift", "ms-swift（import 名 swift）"),
    ("peft", "LoRA / adapter"),
    ("bitsandbytes", "4bit/8bit 量化（QLoRA 依赖）"),
    ("accelerate", "多卡与混合精度"),
    ("datasets", "数据集加载"),
    ("deepspeed", "ZeRO / 多卡"),
    ("flash-attn", "FlashAttention"),
    ("vllm", "推理部署"),
]


def run(cmd: list[str], timeout: int = 20):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()
    except FileNotFoundError:
        return 127, "", "not found"
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"


def _decode_wsl_output(raw: bytes) -> str:
    """wsl.exe 输出是 UTF-16（带 BOM），直接用 utf-8 解会乱码。"""
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16", errors="replace")
    return raw.decode("utf-8", errors="replace")


def list_wsl_distros() -> list[str]:
    try:
        r = subprocess.run(["wsl", "-l", "-q"], capture_output=True, timeout=20)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return []
    text = _decode_wsl_output(r.stdout or b"")
    out = []
    for line in text.replace("\x00", "").splitlines():
        s = line.strip().strip("\ufeff")
        if s and "Windows Subsystem" not in s:
            out.append(s)
    return out


def probe_platform() -> dict:
    """判断当前在 Windows / WSL / Linux；Windows 上再看有没有装 WSL。"""
    info = {
        "system": _platform.system(),
        "release": _platform.release(),
        "version": _platform.version(),
        "machine": _platform.machine(),
        "python": _platform.python_version(),
        "executable": sys.executable,
        "is_wsl": False,
        "has_wsl": False,
        "wsl_distros": [],
    }
    if info["system"] == "Linux":
        try:
            ver = Path("/proc/version").read_text(errors="ignore").lower()
            info["is_wsl"] = ("microsoft" in ver) or ("wsl" in ver)
        except OSError:
            pass
        if os.environ.get("WSL_DISTRO_NAME"):
            info["is_wsl"] = True
        if info["is_wsl"]:
            info["wsl_distro"] = os.environ.get("WSL_DISTRO_NAME") or ""
            info["skill_on_windows_drive"] = str(SKILL_ROOT).startswith("/mnt/")
    elif info["system"] == "Windows":
        info["has_wsl"] = shutil.which("wsl") is not None
        if info["has_wsl"]:
            info["wsl_distros"] = list_wsl_distros()
    return info


def platform_advice(p: dict) -> str:
    """给建议（不是事实）。报告里会明确标成「建议」。"""
    if p.get("is_wsl"):
        if p.get("skill_on_windows_drive"):
            return ("在 WSL 里，但技能包位于 /mnt/（Windows 盘）。训练数据、模型权重、输出"
                    "一律放 ~/ 下：drvfs 的 IO 比 ext4 慢 5~10 倍，dataloader 会被拖死。")
        return ("在 WSL 里，这是推荐环境。CUDA 由 Windows 驱动透传，WSL 内不要再装显卡驱动，"
                "只装 CUDA toolkit（可选）和对应 cu 版本的 torch。")
    if p["system"] == "Windows":
        if p.get("has_wsl") and p.get("wsl_distros"):
            distros = "、".join(p["wsl_distros"][:3])
            return (f"Windows 且已装 WSL（{distros}）。训练建议进 WSL 跑：flash-attn、deepspeed、"
                    f"vllm 在 Windows 原生支持都不完整，WSL 里基本等同 Linux。")
        if p.get("has_wsl"):
            return "有 wsl 命令但没列出发行版。先 `wsl --install -d Ubuntu`，再进 WSL 建训练环境。"
        return ("Windows 且没装 WSL。重度训练（flash-attn / deepspeed / vllm）建议装 WSL2；"
                "只做小模型 LoRA 可以先在 Windows 原生跑，但别指望这些库全支持。")
    if p["system"] == "Linux":
        return "Linux 原生，直接跑，不需要 WSL。"
    if p["system"] == "Darwin":
        return "macOS：只能 MPS/CPU，本 skill 按 NVIDIA 显存做的估算不适用。"
    return f"未知系统：{p['system']}，按 Linux 假设，自行确认。"


def probe_gpus() -> dict:
    if shutil.which("nvidia-smi") is None:
        return {"available": False, "reason": "nvidia-smi 不在 PATH（无 NVIDIA 驱动或未装驱动）"}

    fields = "index,name,memory.total,memory.used,driver_version,compute_cap"
    rc, out, err = run(["nvidia-smi", f"--query-gpu={fields}",
                        "--format=csv,noheader,nounits"])
    if rc != 0:
        # 老驱动不认 compute_cap，退一步
        fields = "index,name,memory.total,memory.used,driver_version"
        rc, out, err = run(["nvidia-smi", f"--query-gpu={fields}",
                            "--format=csv,noheader,nounits"])
    if rc != 0:
        return {"available": False, "reason": (err or out)[:300]}

    gpus = []
    for line in out.splitlines():
        parts = [x.strip() for x in line.split(",")]
        if len(parts) < 5:
            continue
        try:
            gpus.append({
                "index": int(parts[0]),
                "name": parts[1],
                "memory_total_mib": int(float(parts[2])),
                "memory_used_mib": int(float(parts[3])),
                "driver": parts[4],
                "compute_cap": parts[5] if len(parts) > 5 else "",
            })
        except ValueError:
            continue
    return {"available": bool(gpus), "gpus": gpus}


def probe_torch() -> dict:
    try:
        import torch  # noqa: PLC0415
    except Exception as e:  # noqa: BLE001
        return {"installed": False, "reason": f"{type(e).__name__}: {e}"}

    info = {
        "installed": True,
        "version": torch.__version__,
        "cuda_built": getattr(torch.version, "cuda", None),
        "cuda_available": bool(torch.cuda.is_available()),
    }
    if info["cuda_available"]:
        info["device_count"] = torch.cuda.device_count()
        devs = []
        for i in range(torch.cuda.device_count()):
            p = torch.cuda.get_device_properties(i)
            devs.append({
                "index": i,
                "name": p.name,
                "total_mib": round(p.total_memory / 1024 ** 2),
                "capability": f"{p.major}.{p.minor}",
            })
        info["devices"] = devs
        # bf16 支持（Ampere 起算力 8.0+；Turing 7.5 只有 fp16）
        caps = [d["capability"] for d in devs]
        info["bf16_likely"] = all(float(c) >= 8.0 for c in caps) if caps else None
    return info


def probe_libs() -> dict:
    out = {}
    for pkg, desc in LIBS:
        try:
            out[pkg] = {"version": metadata.version(pkg), "desc": desc}
        except metadata.PackageNotFoundError:
            out[pkg] = {"version": None, "desc": desc}
    return out


def probe_disk() -> dict:
    try:
        total, used, free = shutil.disk_usage(str(SKILL_ROOT))
        return {
            "path": str(SKILL_ROOT),
            "total_gb": round(total / 1024 ** 3, 1),
            "free_gb": round(free / 1024 ** 3, 1),
        }
    except OSError as e:
        return {"path": str(SKILL_ROOT), "error": str(e)}


def render_md(data: dict) -> str:
    L = ["# 环境探测报告", ""]
    L.append(f"- 探测时间：{data['probed_at']}")
    L.append(f"- 技能包：`{data['skill_root']}`")
    L.append(f"- 磁盘：总 {data['disk'].get('total_gb', '?')} GB，"
             f"可用 {data['disk'].get('free_gb', '?')} GB")
    L.append("")

    p = data["platform"]
    L.append("## 平台")
    os_name = p["system"]
    if p.get("is_wsl"):
        os_name = f"Windows + WSL（{p.get('wsl_distro') or '未知发行版'}）"
    L.append(f"- 系统：{os_name} / {p['release']} / {p['machine']}")
    if p["system"] == "Windows":
        wsl_line = f"- 已装 WSL：{'是' if p.get('has_wsl') else '否'}"
        if p.get("wsl_distros"):
            wsl_line += f"（{'、'.join(p['wsl_distros'])}）"
        L.append(wsl_line)
    L.append(f"- 解释器：`{p['executable']}`（Python {p['python']}）")
    L.append("")
    L.append(f"- **建议（不是事实）**：{platform_advice(p)}")
    L.append("")

    g = data["gpu"]
    L.append("## GPU")
    if not g.get("available"):
        L.append(f"- 不可用：{g.get('reason', '未知')}")
    else:
        L.append("| # | 型号 | 显存总量 | 已用 | 驱动 | 算力 |")
        L.append("|---|---|---|---|---|---|")
        for x in g["gpus"]:
            L.append(f"| {x['index']} | {x['name']} | {x['memory_total_mib']} MiB | "
                     f"{x['memory_used_mib']} MiB | {x['driver']} | {x['compute_cap'] or '-'} |")
        total = sum(x["memory_total_mib"] for x in g["gpus"])
        free = sum(x["memory_total_mib"] - x["memory_used_mib"] for x in g["gpus"])
        L.append("")
        L.append(f"- **卡数 {len(g['gpus'])}，显存合计 {total} MiB，当前空闲 {free} MiB**")
        L.append("- 判断能否训练只看「空闲」，不要看总量（别人/自己可能还占着）")
    L.append("")

    t = data["torch"]
    L.append("## PyTorch / CUDA")
    if not t.get("installed"):
        L.append(f"- 当前解释器没装 torch（{t.get('reason', '')}）")
        L.append("- 这通常说明你在**技能环境**里跑；要看 CUDA 细节请到训练环境再跑一次")
    else:
        L.append(f"- torch {t['version']}，编译 CUDA {t.get('cuda_built')}，"
                 f"可用 {t['cuda_available']}")
        if t.get("cuda_available"):
            L.append(f"- 可见设备 {t['device_count']} 个，bf16 大概率支持：{t.get('bf16_likely')}")
    L.append("")

    L.append("## 已装库")
    L.append("| 包 | 版本 | 用途 |")
    L.append("|---|---|---|")
    for pkg, info in data["libs"].items():
        L.append(f"| {pkg} | {info['version'] or '**未装**'} | {info['desc']} |")
    L.append("")
    L.append("> 下一步：把本报告 + 模型信息交给 references/02_decide.md 算三档方案。")
    return "\n".join(L)


def main() -> None:
    parser = argparse.ArgumentParser(description="环境探测（只报事实）")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    data = {
        "probed_at": __import__("datetime").datetime.now().isoformat(timespec="seconds"),
        "skill_root": str(SKILL_ROOT),
        "platform": probe_platform(),
        "gpu": probe_gpus(),
        "torch": probe_torch(),
        "libs": probe_libs(),
        "disk": probe_disk(),
    }
    print(json.dumps(data, ensure_ascii=False, indent=2) if args.json else render_md(data))


if __name__ == "__main__":
    main()
