# 微信错误码对照

排查发布问题按此表定位。完整列表见
[官方文档](https://developers.weixin.qq.com/doc/service/api/draftbox/draftmanage/api_draft_add.html)。

## 凭据与权限

| errcode | 含义 | 解决方案 |
| --- | --- | --- |
| 40001 | AppSecret 错误 / access_token 无效 | 检查环境变量；token 需强制刷新 |
| 40007 | invalid media_id | **最常见**。见下方专项说明 |
| 40164 | 服务器出口 IP 不在白名单 | 后台「设置与开发 → 基本配置 → IP 白名单」 |
| 40165 | 服务器出口 IP 不在白名单 | 同上，改动后等几分钟生效 |
| 41001 | 缺少 access_token | token 获取流程有问题 |
| 41005 | 缺少 media 数据 | multipart 字段名必须是 `media` |
| 45009 | 接口调用超频| 降低调用频率 |
| 48001 | api 功能未授权 | 无群发权限，只能写草稿箱 |
| 53401 | 封面图尺寸不合规 | resize 到宽度 ≥900px |

## 40007 专项：invalid media_id

触发这个错误码的四个原因，按排查顺序：

1. **没传 thumb_media_id**。`news` 类型必填，不传直接报 40007。
   网上大量"设了但无效"的case 实际是压根没设。

2. **用了临时素材 ID**。临时素材（`media/upload`）的 media_id 在草稿接口无效，
   必须是永久素材（`material/add_material`）返回的。

3. **把 `uploadimg` 的返回当 media_id 用**。`uploadimg` 返回的是 `url` 字段，
   不是 `media_id`。这两个接口用途完全不同：
   - `uploadimg` → 正文插图，返回 url
   - `material/add_material` → 封面，返回 media_id

4. **素材已过期或被清理**。重新上传即可。

## 非错误码的静默失败

这类问题不报错，但结果不对，必须主动检查：

| 现象 | 原因 |
| --- | --- |
| 正文图片全裂 | src 不是 `mmbiz.qpic.cn`（本地路径 / 第三方 CDN / base64） |
| 粘贴后样式全丢 | 用了 `<style>` 标签或 class 选择器 |
| 提示"链接内容不属于当前公众号" | 外链域名未配后台「业务域名」 |
| 排版塌陷、间距错乱 | 容器标签（div/section）被剥，连带其布局属性失效 |
| 代码块显示为一行 | `white-space` 未改 `pre-wrap` |
| 宽表格撑破屏幕 | `overflow-x` 随容器被剥，微信内无法横向滚动，只能拆行 |
| 保存后代码块缩进消失 | `pre` 内容被空白折叠，需特殊保护 |
| 标题层级全平 | `h1`–`h6` 被降级为 `p`，需内联 font-size 兜底 |

## 排查顺序

1. `python skills/wx-ship/scripts/ship.py --check` —— 确认 token 能取到
2. `python skills/wx-ship/scripts/ship.py <html> --title "..." --cover <jpg> --dry-run`
   —— 本地前置检查，纯只读，不调接口
3. 提交草稿后，**去公众号后台用手机预览**。预览仍不对就是 HTML 问题，
   接口只是忠实转发
4. 报错码则查上表

## 环境变量

```
WX_APPID=你的AppID
WX_APPSECRET=你的AppSecret
```

绝不要写进代码或提交入库。