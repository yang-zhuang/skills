"""AutoDL Art 开发者 API 客户端（需要 token）。

token 只从环境变量 / 包根 .env 读取（见 config.DEV_TOKEN），代码里不写死。
鉴权格式：Authorization: <token>（不要加 "token " 前缀，实测会失败）。

本模块只提供 HTTP 调用；是否执行花钱的动作由调用方（deploy.py / grab.py）决定。
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request

import config


class DevApiError(RuntimeError):
    """开发者 API 返回非 Success，或网络层失败。可重试（如无库存）。"""


class FatalDevApiError(DevApiError):
    """重试也不会好的错误：token 无效、参数错误、余额不足等。

    调用方（grab.py）见到它必须立刻停，绝对不能带着坏 token 高频重试 —— 这是防风控的硬线。
    """


_last_call_at = 0.0


def _throttle():
    """每次调用之间留一点间隔，避免连续打同一个接口。"""
    global _last_call_at
    gap = 1.0 - (time.monotonic() - _last_call_at)
    if gap > 0:
        time.sleep(gap)
    _last_call_at = time.monotonic()


def call(method, path, payload=None, params=None, token=None, timeout=30):
    """调用开发者 API，返回 data 字段；非 Success 抛 DevApiError。

    method 用大写。注意实测结论（2026-11-24）：
      - POST 端点（list/create/power_on/...）用 JSON body；
      - 官方文档写「GET 带 body」的 status/snapshot，服务端实际只认 **query 参数**，
        发 JSON body 会返回 RequestParameterIsWrong。所以这里用 params 拼 query。
    """
    token = (token or config.DEV_TOKEN).strip()
    if not token:
        raise DevApiError("没有开发者 token：请在包根 .env 里设置 AUTODL_DEV_TOKEN，"
                          "或先跑 dev_check.py 查看获取位置。")

    url = config.DEV_BASE_URL + path
    if params:
        url = url + "?" + urllib.parse.urlencode(params)
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    headers = {
        "Accept": "application/json",
        "Authorization": token,
        "Origin": config.DEV_BASE_URL,
        "Referer": config.DEV_BASE_URL + "/app/market",
    }
    if data is not None:
        headers["Content-Type"] = "application/json"

    _throttle()
    req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:300]
        if e.code in (400, 401, 403):
            raise FatalDevApiError("HTTP %s：%s（致命，重试无意义）" % (e.code, detail)) from e
        raise DevApiError("HTTP %s：%s" % (e.code, detail)) from e
    except urllib.error.URLError as e:
        raise DevApiError("网络失败：%s" % e.reason) from e

    try:
        result = json.loads(body)
    except ValueError:
        raise DevApiError("返回不是 JSON：%s" % body[:300])

    if result.get("code") != "Success":
        code = str(result.get("code") or "")
        msg = str(result.get("msg") or "")
        # 致命错误清单：重试无意义且危险（拿着无效 token 反复打接口 = 风控信号）
        fatal_marks = ("authorize", "authoriz", "token", "parameter", "balance", "认证", "登录", "参数", "余额")
        low = (code + " " + msg).lower()
        if any(m in low for m in fatal_marks):
            raise FatalDevApiError("接口返回 %s：%s（致命，重试无意义）" % (code, msg))
        raise DevApiError("接口返回 %s：%s" % (code, msg))
    return result.get("data")


# ---- 只读封装 ----

def list_instances(page_index=1, page_size=20):
    return call("POST", config.API_DEV_INSTANCE + "/list",
                {"page_index": page_index, "page_size": page_size})


def status(instance_uuid):
    # 实测：服务端只认 query 参数，发 JSON body 会 RequestParameterIsWrong
    return call("GET", config.API_DEV_INSTANCE + "/status", params={"instance_uuid": instance_uuid})


def snapshot(instance_uuid):
    return call("GET", config.API_DEV_INSTANCE + "/snapshot", params={"instance_uuid": instance_uuid})


# ---- 会改变状态 / 花钱的封装（调用方必须先拿到用户明确授权） ----

def create(gpu_spec_uuid, application_uuid, application_version="latest",
           req_gpu_amount=1, expand_system_disk_by_gb=0, instance_name="",
           start_command="sleep 1", data_center_list=None):
    payload = {
        "req_gpu_amount": req_gpu_amount,
        "expand_system_disk_by_gb": expand_system_disk_by_gb,
        "gpu_spec_uuid": gpu_spec_uuid,
        "application_uuid": application_uuid,
        "application_version": application_version,
    }
    if instance_name:
        payload["instance_name"] = instance_name
    if start_command:
        payload["start_command"] = start_command
    if data_center_list:
        payload["data_center_list"] = data_center_list
    return call("POST", config.API_DEV_INSTANCE + "/create", payload)


def power_on(instance_uuid, start_command="sleep 1"):
    return call("POST", config.API_DEV_INSTANCE + "/power_on",
                {"instance_uuid": instance_uuid, "payload": "gpu", "start_command": start_command})


def power_off(instance_uuid):
    return call("POST", config.API_DEV_INSTANCE + "/power_off", {"instance_uuid": instance_uuid})


def release(instance_uuid):
    return call("POST", config.API_DEV_INSTANCE + "/release", {"instance_uuid": instance_uuid})
