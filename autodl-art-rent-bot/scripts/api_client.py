"""AutoDL Art 公开接口的极小 HTTP 客户端，仅用标准库。"""

import json
import random
import time
import urllib.error
import urllib.request

import config

_last_request_at = 0.0


def request_json(method, path, payload=None):
    """请求 JSON 接口；失败重试，返回解析后的 dict。"""
    global _last_request_at
    url = config.BASE_URL + path
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    headers = {
        "Accept": "application/json",
        "Origin": config.BASE_URL,
        "Referer": config.BASE_URL + "/app/market",
    }
    if data is not None:
        headers["Content-Type"] = "application/json"

    wait = random.uniform(config.MIN_DELAY, config.MAX_DELAY) - (time.monotonic() - _last_request_at)
    if wait > 0:
        time.sleep(wait)

    last_error = None
    for attempt in range(config.MAX_RETRY + 1):
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=config.REQUEST_TIMEOUT) as resp:
                body = resp.read().decode("utf-8", errors="replace")
            _last_request_at = time.monotonic()
            result = json.loads(body)
            if result.get("code") != "Success":
                raise RuntimeError("接口返回非 Success: code=%s msg=%s" % (result.get("code"), result.get("msg")))
            return result
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, json.JSONDecodeError, RuntimeError) as exc:
            last_error = exc
            if attempt < config.MAX_RETRY:
                delay = 4 + 2 * attempt
                print("  [retry %d/%d] %s，%.0f 秒后重试" % (attempt + 1, config.MAX_RETRY, exc, delay), flush=True)
                time.sleep(delay)
    raise RuntimeError("请求失败 %s %s：%s" % (method, path, last_error))


def post(path, payload):
    return request_json("POST", path, payload)


def get(path):
    return request_json("GET", path)
