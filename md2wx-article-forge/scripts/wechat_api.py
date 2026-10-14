"""微信公众号服务端 API 封装。

只用标准库 urllib，不引requests —— 保持零依赖。
凭据优先从环境变量读取；若无，则从项目根 `.env` 加载（不覆盖已有环境变量）。
密钥绝不写进代码，`.env` 已列入 .gitignore 不入库。

前置条件（缺一即报错，不做静默降级）：
  1. 已认证的订阅号或服务号（2025-07 起未认证主体已被回收发布接口权限）
  2. 公众平台后台已配置 IP 白名单，否则报 40165
  3. AppID / AppSecret 在环境变量或项目根 .env 中

.env 格式（项目根，与 core/ 同级）：
  WX_APPID=wx1234567890abcdef
  WX_APPSECRET=abc123def456
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://api.weixin.qq.com/cgi-bin"
TOKEN_TTL = 7000  # 提前 100s 过期，避免边界失效
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"


def load_env(path: Path = ENV_FILE) -> dict[str, str]:
    """读 .env 到 os.environ（已存在的环境变量优先，不被覆盖）。

    返回本次实际加载的键值，便于调用方确认来源。文件不存在返回空 dict，
    不报错 —— 没配 .env 而用系统环境变量是合法用法。
    """
    loaded: dict[str, str] = {}
    if not path.exists():
        return loaded
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value
            loaded[key] = value
    return loaded

# 错误码 → 人话
ERRORS = {
    40001: "AppSecret 错误或 access_token 无效",
    40007: "媒体文件标识（media_id）无效 —— 多为封面未传或用了临时素材 ID",
    40164: "服务器出口 IP 未加入白名单",
    40165: "服务器出口 IP 未加入白名单",
    41001: "缺少 access_token 参数",
    41005: "缺少 media 数据",
    45009: "接口调用超过频次限制",
    48001: "api 功能未授权（多为无群发权限）",
    53401: "封面图尺寸不合规（宽度不足 900px 或比例不符）",
}


class WeChatError(RuntimeError):
    def __init__(self, code: int, msg: str):
        self.code = code
        self.detail = msg
        super().__init__(f"[{code}] {ERRORS.get(code, '未知错误')} | 微信返回: {msg}")


def _get(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _post(url: str, payload: dict | None = None, files: dict | None = None) -> dict:
    if files:
        boundary = "----md2wx" + str(int(time.time() * 1000))
        parts: list[bytes] = []
        for name, (path, mime) in files.items():
            data = Path(path).read_bytes()
            parts.append(
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="{name}"; filename="{Path(path).name}"\r\n'
                f"Content-Type: {mime}\r\n\r\n".encode("utf-8")
                + data
                + b"\r\n"
            )
        body = b"".join(parts) + f"--{boundary}--\r\n".encode("utf-8")
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
    else:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise WeChatError(exc.code, exc.read().decode("utf-8", "ignore")[:300]) from exc
    if "errcode" in data and data["errcode"] != 0:
        raise WeChatError(data["errcode"], data.get("errmsg", ""))
    return data


def get_access_token() -> str:
    """取 access_token，带进程内缓存（微信有效期 7200s）。"""
    cached = _TOKEN_CACHE.get("token")
    if cached and time.time() - cached[0] < TOKEN_TTL:
        return cached[1]
    load_env()
    appid = os.environ.get("WX_APPID")
    secret = os.environ.get("WX_APPSECRET")
    if not appid or not secret:
        raise WeChatError(41001, "环境变量 WX_APPID / WX_APPSECRET 未设置")
    query = urllib.parse.urlencode({
        "grant_type": "client_credential", "appid": appid, "secret": secret,
    })
    data = _get(f"{API}/token?{query}")
    if "access_token" not in data:
        raise WeChatError(data.get("errcode", -1), data.get("errmsg", ""))
    _TOKEN_CACHE["token"] = (time.time(), data["access_token"])
    return data["access_token"]


_TOKEN_CACHE: dict = {}


def upload_inline_image(path: str | Path) -> str:
    """上传正文图片，返回 mmbiz.qpic.cn 永久链接。

    这是图片能显示的唯一途径：本地路径和第三方CDN 在公众号里都不显示。
    """
    p = Path(path)
    if p.stat().st_size > 2 * 1024 * 1024:
        raise ValueError(f"正文图片超过 2MB 上限: {p} ({p.stat().st_size} 字节)")
    token = get_access_token()
    mime = "image/png" if p.suffix.lower() == ".png" else "image/jpeg"
    data = _post(
        f"{API}/media/uploadimg?access_token={token}",
        files={"media": (str(p), mime)},
    )
    return data["url"]


def upload_thumb(path: str | Path) -> str:
    """上传封面永久素材，返回 thumb_media_id。

    封面必须走 material/add_material。传临时素材 ID 或直接传图片 URL
    都会报 40007 invalid media_id。
    """
    from wechat_rules import MAX_THUMB_BYTES, MIN_THUMB_WIDTH

    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"封面图不存在: {p}")
    if p.suffix.lower() not in (".jpg", ".jpeg"):
        raise ValueError(f"封面图必须是 JPG，收到 {p.suffix}（微信接口硬性要求）")
    size = p.stat().st_size
    if size > MAX_THUMB_BYTES:
        raise ValueError(f"封面图超过 2MB: {p} ({size} 字节)")

    width = _image_width(p)
    if width and width < MIN_THUMB_WIDTH:
        raise ValueError(
            f"封面图宽度 {width}px 不足 {MIN_THUMB_WIDTH}px，微信会报 53401。"
            f"建议按 2.35:1 渲染成 900×383"
        )

    token = get_access_token()
    data = _post(
        f"{API}/material/add_material?access_token={token}&type=image",
        files={"media": (str(p), "image/jpeg")},
    )
    return data["media_id"]


def _image_width(path: Path) -> int | None:
    """纯 stdlib 读 JPEG 宽高，失败返回 None（不阻断流程）。"""
    try:
        data = path.read_bytes()
        if data[:2] != b"\xff\xd8":
            return None
        i = 2
        while i < len(data) - 9:
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xC0, 0xC1, 0xC2, 0xC3):
                height = int.from_bytes(data[i + 5:i + 7], "big")
                width = int.from_bytes(data[i + 7:i + 9], "big")
                return width
            seg_len = int.from_bytes(data[i + 2:i + 4], "big")
            i += 2 + seg_len
        return None
    except Exception:
        return None


def add_draft(
    title: str,
    content: str,
    thumb_media_id: str | None = None,
    author: str = "",
    digest: str = "",
    content_source_url: str = "",
    need_open_comment: int = 0,
    cover_crop: list[dict] | None = None,
) -> dict:
    """新增草稿。

    只写草稿箱，不群发 —— 群发受订阅号每日 1 次 / 服务号每月 4 次硬限制，
    且需额外权限。人工在后台确认后再发是唯一稳妥路径。

    thumb_media_id 对 news 类型必填，缺失或非永久素材 ID 均返回 40007。
    """
    from wechat_rules import (
        COVER_RATIOS,
        LIMIT_AUTHOR,
        LIMIT_CONTENT_BYTES,
        LIMIT_CONTENT_CHARS,
        LIMIT_DIGEST,
        LIMIT_SOURCE_URL,
        LIMIT_TITLE,
    )

    title = title.strip()
    if not title:
        raise ValueError("title 必填，draft/add 会直接拒绝")
    if len(title) > LIMIT_TITLE:
        title = title[:LIMIT_TITLE]
    author = author.strip()[:LIMIT_AUTHOR]
    digest = digest.strip()[:LIMIT_DIGEST]

    if len(content) > LIMIT_CONTENT_CHARS:
        raise ValueError(
            f"正文 {len(content)} 字符，超过 {LIMIT_CONTENT_CHARS} 上限，微信会拒绝"
        )
    content_bytes = len(content.encode("utf-8"))
    if content_bytes > LIMIT_CONTENT_BYTES:
        raise ValueError(
            f"正文 {content_bytes} 字节，超过 {LIMIT_CONTENT_BYTES} 上限（1MB）"
        )
    if len(content_source_url.encode("utf-8")) > LIMIT_SOURCE_URL:
        raise ValueError("content_source_url 超过 1KB 上限")

    article = {
        "article_type": "news",
        "title": title,
        "author": author,
        "digest": digest,
        "content": content,
        "content_source_url": content_source_url,
        "thumb_media_id": thumb_media_id or "",
        "need_open_comment": need_open_comment,
        "only_fans_can_comment": 0,
    }
    if cover_crop:
        for c in cover_crop:
            if c.get("ratio") not in COVER_RATIOS:
                raise ValueError(
                    f"裁剪比例 {c.get('ratio')} 不支持，news 类型仅允许 {COVER_RATIOS}"
                )
        article["cover_info"] = {"crop_percent_list": cover_crop}

    token = get_access_token()
    return _post(f"{API}/draft/add?access_token={token}", {"articles": [article]})


def publish_draft(media_id: str) -> dict:
    """提交发布。会真实群发，需明确授权才可调用。"""
    token = get_access_token()
    return _post(f"{API}/freepublish/submit?access_token={token}", {"media_id": media_id})


def check_config() -> dict:
    """只读自检：凭据是否就位、能否取到 token。不产生任何远端副作用。"""
    loaded = load_env()
    result = {
        "env_file": str(ENV_FILE),
        "env_file_exists": ENV_FILE.exists(),
        "loaded_from_env_file": sorted(loaded),
        "appid_set": bool(os.environ.get("WX_APPID")),
        "secret_set": bool(os.environ.get("WX_APPSECRET")),
    }
    if not (result["appid_set"] and result["secret_set"]):
        result["token_ok"] = False
        result["hint"] = "缺少 WX_APPID / WX_APPSECRET 环境变量"
        return result
    try:
        get_access_token()
        result["token_ok"] = True
    except WeChatError as exc:
        result["token_ok"] = False
        result["code"] = exc.code
        result["hint"] = str(exc)
    return result


if __name__ == "__main__":
    print(json.dumps(check_config(), ensure_ascii=False, indent=2))