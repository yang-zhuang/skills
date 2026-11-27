"""网页登录态 API 客户端（JWT 来自 extract_jwt.py 自动提取）。

覆盖"网页体系"的接口——这套接口吃浏览器登录 JWT，不吃开发者 token：
    POST /api/v1/adl_server/machine_pro/gpu_type   库存查询（gpu_idle_num / gpu_total_number）
    （网页版下单/价格预览等也在这套体系里，但本包不实现网页下单——dev API 的 create 足够且更规范）

鉴权格式（2026-11-24 从 index.cf7abf41.js 请求拦截器证实）：
    headers["Authorization"] = <网页 JWT 原文>   与开发者 token 一样不加前缀
"""

import json
import urllib.error
import urllib.request

import config
from dev_api import DevApiError, FatalDevApiError, _throttle


def call(path, payload, timeout=30):
    """带网页 JWT 的 POST 请求，返回完整 result dict（不做 code 剥离，调用方自行判断）。"""
    token = config.WEB_JWT
    if not token:
        raise FatalDevApiError(
            "没有网页 JWT：先跑一次 `python extract_jwt.py --login`（登录一次即可，之后全自动）。")
    url = config.BASE_URL + path
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST", headers={
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Authorization": token,
        "Origin": config.BASE_URL,
        "Referer": config.BASE_URL + "/app/market",
    })
    _throttle()
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

    code = str(result.get("code") or "")
    if code != "Success":
        msg = str(result.get("msg") or "")
        low = (code + " " + msg).lower()
        if "authoriz" in low or "登录" in msg or "认证" in msg:
            raise FatalDevApiError(
                "网页 JWT 无效或已过期（%s：%s）——跑 `python extract_jwt.py` 重新提取，"
                "若提示未登录再 --login 登录一次。" % (code, msg))
        raise DevApiError("接口返回 %s：%s" % (code, msg))
    return result.get("data")


def stock(data_center_list):
    """查询各地区的 GPU 库存。

    data_center_list 形如 ["西北B区", "北京B区"]。
    返回 [{gpu_type, idle, total, ...}]；过滤掉 total 为 0 的行（与前端渲染逻辑一致）。
    """
    data = call(config.API_WEB_GPU_TYPE, {"data_center_list": list(data_center_list)})
    rows = []
    for item in (data or []):
        total = item.get("gpu_total_number") or 0
        if not total:
            continue
        rows.append({
            "gpu_type": item.get("gpu_type"),
            "idle": item.get("gpu_idle_num"),
            "total": total,
            "user_define_name": item.get("user_define_name"),
        })
    return rows
