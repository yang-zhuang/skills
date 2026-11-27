"""网页登录 JWT 自动提取：登录一次，之后全自动。

原理（2026-11-24 从前端源码证实）：
    www.autodl.art 的请求拦截器给所有请求加 `Authorization: localStorage.getItem("token")`，
    即网页登录 JWT 存在 localStorage 的 "token" 键里。这个 JWT 用于网页体系的接口
    （如 machine_pro/gpu_type 库存查询），和开发者 token 是两套凭据。

用法：
    python extract_jwt.py          # 先无头试读；读不到才弹浏览器窗口让你登录一次
    python extract_jwt.py --login  # 直接弹浏览器窗口（第一次使用就这么跑）

流程：
    1. 用 Playwright persistent context 打开专用 profile（.browser_profile/，已 gitignore）；
    2. 无头模式先试读 localStorage 的 token —— profile 里已有登录态时**不需要你做任何事**；
    3. 读不到才开有头窗口，你手动登录一次（账号密码/扫码都行，脚本不碰登录表单）；
    4. 拿到 token 后写进包根 .env 的 AUTODL_ART_WEB_JWT=，并尝试解出过期时间打印。

注意：
    * 和 autodl-rent-bot 一样，JWT 是长效的（那边实测约 60 天），过期后 re-run 本脚本
      重新登录一次即可；grab.py 遇到 AuthorizeFailed 会明确提示。
    * 登录动作永远由你人工完成，脚本只负责读 token，绝不碰账号密码。
"""

import argparse
import base64
import json
import sys
import time
from datetime import datetime
from pathlib import Path

import config

STORAGE_KEY = "token"
LOGIN_URL = config.BASE_URL + "/app/market"
ENV_KEY = "AUTODL_ART_WEB_JWT"


def decode_exp(token):
    """尝试解出 JWT 的 exp 过期时间；解不出返回 None（不报错，token 格式可能不是标准 JWT）。"""
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
        exp = data.get("exp")
        if exp:
            return datetime.fromtimestamp(exp)
    except Exception:
        pass
    return None


def save_to_env(token):
    """把 JWT 写进包根 .env（替换已有的 AUTODL_ART_WEB_JWT 行）；返回 .env 路径。"""
    env_path = config.PACKAGE_ROOT / ".env"
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    new_lines, replaced = [], False
    for line in lines:
        if line.strip().startswith(ENV_KEY + "="):
            new_lines.append("%s=%s" % (ENV_KEY, token))
            replaced = True
        else:
            new_lines.append(line)
    if not replaced:
        if new_lines and new_lines[-1].strip():
            new_lines.append("")
        new_lines.append("# 网页登录 JWT（extract_jwt.py 自动写入；过期后重跑该脚本）")
        new_lines.append("%s=%s" % (ENV_KEY, token))
    env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    return env_path


def main():
    ap = argparse.ArgumentParser(description="自动提取 art 网页登录 JWT（登录一次，后续全自动）")
    ap.add_argument("--login", action="store_true", help="直接打开浏览器窗口登录（第一次用这个）")
    args = ap.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        sys.exit("需要 playwright：pip install playwright && playwright install chromium")

    profile = config.BROWSER_PROFILE_DIR
    profile.mkdir(parents=True, exist_ok=True)
    print("浏览器专用 profile：%s" % profile, flush=True)

    with sync_playwright() as pw:
        browser = pw.chromium.launch_persistent_context(
            str(profile),
            headless=not args.login,   # 有头=用户可能要登录；无头=直接试读
            viewport={"width": 1280, "height": 850},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = browser.new_page()
        page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)

        token = page.evaluate("k => localStorage.getItem(k)", STORAGE_KEY)
        if token:
            print("无头模式直接读到了已有登录态，不需要你做任何事。", flush=True)
        else:
            if not args.login:
                print("profile 里没有登录态，切换为有头窗口：请在打开的页面里登录 art 账号。", flush=True)
                browser.close()
                browser = pw.chromium.launch_persistent_context(
                    str(profile),
                    headless=False,
                    viewport={"width": 1280, "height": 850},
                    args=["--disable-blink-features=AutomationControlled"],
                )
                page = browser.new_page()
                page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
            print("等待登录……（脚本只读 localStorage 的 token，不碰你的账号密码）", flush=True)
            deadline = time.time() + 600  # 最多等 10 分钟
            token = ""
            while time.time() < deadline:
                token = page.evaluate("k => localStorage.getItem(k)", STORAGE_KEY) or ""
                if token:
                    break
                time.sleep(2)
            if not token:
                browser.close()
                sys.exit("10 分钟内没等到登录，退出。重跑本脚本再试。")
            print("检测到登录成功。", flush=True)

        browser.close()

    exp = decode_exp(token)
    env_path = save_to_env(token)
    print("JWT 已写入 %s（%s=…%s，长度 %d）" % (env_path, ENV_KEY, token[-8:], len(token)))
    if exp:
        left = exp - datetime.now()
        print("过期时间：%s（剩余 %d 天 %d 小时）" % (exp, left.days, left.seconds // 3600))
        if left.days < 3:
            print("⚠️ 快过期了，建议找时间重跑本脚本刷新。")
    else:
        print("（未能解出过期时间，不影响使用；失效时调用方会收到 AuthorizeFailed）")
    print("之后跑 grab.py / stock 查询会自动读这个 JWT，无需你再手动提供。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
