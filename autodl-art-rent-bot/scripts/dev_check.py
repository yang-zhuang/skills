"""开发者 token 体检：确认 token 有效、能读到账号下的实例。

只做只读调用，不创建、不开机、不花钱。
"""

import sys

import config
import dev_api


def main():
    if not config.DEV_TOKEN:
        print("没有找到 AUTODL_DEV_TOKEN。")
        print("获取方式：登录 www.autodl.com → 控制台 → 账号 → 设置 → 开发者Token")
        print("然后写进包根 .env（已被 .gitignore 排除）：")
        print("  AUTODL_DEV_TOKEN=你的token")
        return 2

    print("token 长度：%d 字符（不回显明文）" % len(config.DEV_TOKEN))
    try:
        data = dev_api.list_instances(page_index=1, page_size=5)
    except dev_api.DevApiError as e:
        print("调用失败：%s" % e)
        if "AuthorizeFailed" in str(e) or "登录" in str(e):
            print("多半是 token 失效或复制时漏字符，去控制台重新生成一次。")
        return 2

    data = data or {}
    print("鉴权通过。账号下实例总数：%s" % data.get("result_total"))
    rows = data.get("list") or []
    if not rows:
        print("当前没有实例。")
    for it in rows:
        app = it.get("cg_application_info") or {}
        print("- %s | %s | %s | %s卡 | %s | %s" % (
            it.get("uuid"), it.get("status"), it.get("region_name") or it.get("region_sign"),
            it.get("req_gpu_amount"), it.get("gpu_spec_uuid"), app.get("application_name")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
