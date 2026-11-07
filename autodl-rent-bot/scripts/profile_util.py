"""Chrome profile 副本工具。

为什么需要副本：
- 直接用默认 Chrome profile 路径（...\\AppData\\Local\\Google\\Chrome\\User Data）
  启动会被 Chrome 拒绝：'DevTools remote debugging requires a non-default data
  directory'（Playwright 通过 --remote-debugging-pipe 驱动，Chrome 不允许在默认
  profile 上开启远程调试）。
- 直接用该路径还会因 Chrome 正在运行而报“profile 被锁定”。

解决方案：把 profile 复制到一个非默认路径，机器人启动时使用这个副本。
- 副本只读取原始 profile，不修改你的真实 Chrome 数据。
- 副本里包含 AutoDL 的登录态（cookies / localStorage），因此机器人可“零登录”。
- 副本与真实 Chrome 互不干扰，无需关闭 Chrome。
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import config

# 复制时跳过的目录：缓存/指标等体积大且对登录态无用，跳过可大幅提速
_SKIP_DIRS = [
    "Cache",
    "Code Cache",
    "GPUCache",
    "Service Worker",
    "optimization_guide_model_store",
    "BrowserMetrics",
    "Crashpad",
    "Crash Reports",
    "Safe Browsing",
    "pnacl",
    "ShaderCache",
    "GrShaderCache",
    "OnDeviceHeadSuggestModel",
    "hyphen-data",
    "MEIPreload",
    "FirstPartySetsPreloaded",
    "FileTypePolicies",
    "CaptchaProviders",
    "Crowd Deny",
    "component_crx_cache",
    "extensions_crx_cache",
    "ActorSafetyLists",
    "AmountExtractionHeuristicRegexes",
]

# 副本里需要删除的锁文件，避免新实例误判“profile 被占用”
_LOCK_FILES = ["SingletonLock", "SingletonCookie", "SingletonSocket"]


def profile_copy_dir() -> str:
    """返回 profile 副本目录（可被环境变量覆盖）。"""
    env = os.getenv("AUTODL_PROFILE_COPY_DIR", "").strip()
    if env:
        return env
    return str(Path(__file__).parent / "chrome_profile")


def ensure_profile_copy(force: bool = False) -> str | None:
    """确保存在一份可用的 profile 副本，返回其路径；未配置 USER_DATA_DIR 则返回 None。"""
    src = config.USER_DATA_DIR
    if not src:
        return None
    if not os.path.isdir(src):
        print(f"[profile] 源 profile 不存在: {src}")
        return None

    dst = profile_copy_dir()
    if os.path.exists(dst) and not force:
        print(f"[profile] 复用已存在的 profile 副本: {dst}")
        return dst

    print(f"[profile] 复制 profile: {src} -> {dst}")
    os.makedirs(dst, exist_ok=True)

    cmd = [
        "robocopy",
        src,
        dst,
        "/E",          # 递归复制子目录（含空目录）
        "/R:0",        # 失败不重试
        "/W:0",        # 失败不等待
        "/NFL",
        "/NDL",
        "/NJH",
        "/NJS",
        "/COPY:DAT",   # 复制数据/属性/时间戳
    ]
    for d in _SKIP_DIRS:
        cmd += ["/XD", d]

    try:
        # robocopy 退出码 0=无复制 1=成功复制 2+=出错；用 check=False 自行判断
        # robocopy 输出含非 UTF-8 字节，必须 errors="replace"，否则读线程抛 UnicodeDecodeError
        proc = subprocess.run(
            cmd, check=False, capture_output=True, text=True, errors="replace"
        )
        if proc.returncode >= 4:
            print(f"[profile] robocopy 警告（返回码 {proc.returncode}），但已尽力复制")
    except Exception as e:  # noqa: BLE001
        print(f"[profile] robocopy 执行异常: {e}")

    # 清掉副本里的锁文件
    for lf in _LOCK_FILES:
        lp = os.path.join(dst, lf)
        if os.path.exists(lp):
            try:
                os.remove(lp)
            except OSError:
                pass

    print(f"[profile] 副本就绪: {dst}")
    return dst


if __name__ == "__main__":
    out = ensure_profile_copy(force=True)
    print("RESULT:", out)
