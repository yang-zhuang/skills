"""检查本地 agnes-key-pool 代理是否可用（不发真实业务请求）。

用法：
    python check_proxy.py

只做两件事：GET /_pool/health 看 key 数；发一句 "hi" 确认模型能回话。
"""

import json
import sys
import urllib.error
import urllib.request

import config

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def fetch(url, payload=None, timeout=30):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if data else {}
    if config.LLM_API_KEY and config.LLM_API_KEY != "any":
        headers["Authorization"] = "Bearer " + config.LLM_API_KEY
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def main():
    root = config.LLM_BASE_URL.rstrip("/")
    if root.endswith("/v1"):
        root = root[:-3]
    try:
        health = fetch(root + "/_pool/health")
        print("代理健康：%s" % json.dumps(health, ensure_ascii=False), flush=True)
    except (urllib.error.URLError, TimeoutError) as exc:
        print("代理连不上：%s。请先启动 agnes-key-pool 的 proxy.py" % exc, file=sys.stderr)
        sys.exit(2)

    try:
        resp = fetch(config.LLM_BASE_URL + "/chat/completions", {
            "model": config.LLM_MODEL,
            "messages": [{"role": "user", "content": "只回复 OK 两个字母"}],
            "max_tokens": 16,
        }, timeout=config.LLM_TIMEOUT)
        content = resp["choices"][0]["message"]["content"]
        print("模型 %s 回复：%s" % (config.LLM_MODEL, content.strip()[:80]), flush=True)
    except Exception as exc:
        print("模型调用失败：%s" % exc, file=sys.stderr)
        sys.exit(3)


if __name__ == "__main__":
    main()
