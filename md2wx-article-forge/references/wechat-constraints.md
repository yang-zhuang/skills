# 微信公众号编辑器兼容性速查

按官方文档与实测整理。渲染前对照本表，可避免绝大多数静默失效。

## 一、会被整节点剔除（含内部内容）

`script` `iframe` `object` `embed` `form` `input` `button` `select`
`textarea` `style` `link` `meta` `base`

注意：`<style>.a{color:red}</style>` 被剔除后，`.a{color:red}` 会作为
**裸文本泄漏到正文里**。本项目用 `_skip_depth` 专门拦这个。

## 二、属性被静默剥离

`id`（导致锚点跳转失效）`class`（无 CSS 时本就无用）`onclick` `onload`
`onerror` `onmouseover` `contenteditable` `draggable` `data-src`

## 三、CSS 属性被过滤或无效

| 属性 | 状态 | 替代方案 |
| --- | --- | --- |
| `position`（含 absolute/fixed/relative） | 失效 | 放弃定位，纯文档流排版 |
| `z-index` | 失效 | 无解 |
| `transform` / `animation` / `transition` | 失效 | 静态图示 |
| `display` | 失效 | 用 `<p style="text-align:center">` 替代居中 |
| `float` | 不可靠 | 用表格布局或放弃 |
| `@media` / `@keyframes` / 伪类 | 完全无效 | 无 `<style>` 就没有这些 |

## 四、可靠的 inline 样式白名单

字号、字重、颜色、行高、字间距、对齐、背景（含 linear-gradient）、
内外边距、边框（含圆角）、阴影、透明度、`max-width`、`border-collapse`、
`white-space`、`word-break`

不支持 `@media` 与选择器，**响应式只能靠 `vw` 单位**，不能靠断点。

## 五、标签的受限行为

| 标签 | 限制与对策 |
| --- | --- |
| `div` | 部分编辑器转成 `p` 或丢弃。不要承担布局职责 |
| `h1`–`h6` | 部分版本降级为 `p`，层级靠内联 `font-size`/`weight` 兜底 |
| `pre` | `white-space: pre` 部分版本失效，**必须改 `pre-wrap`** |
| `table` | 丢弃 `colgroup`/`thead`/`tbody`；`overflow-x` 随容器被剥，**宽表无法横向滚动，只能拆行** |
| `a` | 外链域名须在后台「业务域名」白名单内 |
| `img` | src 必须 `mmbiz.qpic.cn`；SVG 内联、base64、第三方 CDN 均不显示 |
| `mpvoice` / `mpvideo` | 微信专用标签，普通浏览器不识别 |

## 六、容器标签被剥离时的连锁问题

`div` / `section` 被删时，其承载的布局属性（`overflow-x`、`position`）
**一并消失**，但内容保留。这是"看起来没报错但排版坏了"的主要来源。

本项目的 `wechat_guard.py` 对此会上报 `warn`，提示排版降级。

## 七、图片规则

- 正文插图：`media/uploadimg` → 返回 `mmbiz` 永久链接，≤2MB，格式 bmp/png/jpeg/jpg/gif
- 封面图：`material/add_material` → 返回**永久** media_id，必须 **JPG**，≤2MB，宽度 ≥900px
- 两者传错接口均返回 `40007 invalid media_id`

## 八、draft/add 字段限制

| 字段 | 限制 |
| --- | --- |
| `article_type` | `news`（默认）/ `newspic` |
| `title` | **必填**，≤32 字 |
| `author` | ≤16 字 |
| `digest` | ≤**120** 字（官方文档值） |
| `content` | **必填**，<20000 字符且 <1MB，会去除 JS |
| `content_source_url` | ≤1KB，即「阅读原文」 |
| `thumb_media_id` | `news` **必填**，须永久 MediaID |
| `cover_info.crop_percent_list[].ratio` | `news` 仅支持 `2.35_1`、`1_1` |

## 九、接口与权限

| 接口 | 权限 | 频次 |
| --- | --- | --- |
| `media/uploadimg` | 开发者权限 | 无明确限制 |
| `material/add_material` | 开发者权限 | 无明确限制 |
| `draft/add` | 开发者权限 | 日限 1000 次 |
| `freepublish/submit` | **需群发权限** | 订阅号 1 次/日，服务号 4 次/月 |

前置：已认证主体（2025-07 起未认证主体已被回收发布权限）+ IP 白名单（否则 40164/40165）。

## 参考

- [新增草稿](https://developers.weixin.qq.com/doc/service/api/draftbox/draftmanage/api_draft_add.html)
- [服务端 API 总览](https://developers.weixin.qq.com/doc/service/api/)
- [HTML/CSS 支持概览](https://www.axtonliu.ai/newsletters/ai-2/posts/wechat-article-html-css-support)