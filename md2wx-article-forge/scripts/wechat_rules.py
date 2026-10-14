"""微信编辑器兼容性规则表 —— 本项目唯一权威来源。

微信图文编辑器在保存时会对粘贴的 HTML 做三轮清洗：
  1. 移除危险标签与事件属性
  2. 规范化结构（div 转 p，删 class / id / 大量 style）
  3. 移动端渲染时再按 iOS/Android 引擎微调

本模块把"会被清洗掉的东西"显式列成规则，渲染阶段前向规避，
而不是生成后再等着被微信吃掉。

参考：
  https://developers.weixin.qq.com/doc/service/api/draftbox/draftmanage/api_draft_add.html
  https://axtonliu.ai/newsletters/ai-2/posts/wechat-article-html-css-support
"""

# 会被整节点剔除，禁止出现在输出里
FORBIDDEN_TAGS = frozenset({
    "script", "iframe", "object", "embed", "form", "input",
    "button", "select", "textarea", "style", "link", "meta", "base",
})

# 标题层级上限。超过该深度的标题会被渲染器降级，并在报告里告警。
#
# 依据（实测来源优先于泛泛而谈的标签清单）：
#   - 对公众号编辑器**渲染后 DOM** 的逆向分析（2026）只列出 h1–h3 为标题，
#     h4–h6 未被纳入可用集合
#   - 另有资料笼统称 h1–h6 均不被过滤，但双方都承认 h4 以下行为不稳定
#   - 更硬的物理限制：375px 移动屏上超过 3 级已无法用字号/字重做视觉区分
#
# 再者，文章主标题由后台字段承载（渲染为页面级 h1），正文再用 h1 语义冲突。
# 因此：正文最多 3 级，且默认用 <p> + 内联字号表达，绕开 h4-h6 的口径分歧。
MAX_HEADING_LEVEL = 3

# 容器标签口径
#   <section> 支持 —— 第三方编辑器模板大量使用，保存后保留
#   <div>     不支持 —— 实测其 style 会被清除，结构会被改写
# 因此 div 统一改写为 section，而不是连内容一起丢弃。
# 依据：https://denghao.me/index.php/archives/wechat-editor.html
CONTAINER_TAG = "section"
REWRITTEN_CONTAINER_TAGS = frozenset({"div"})

# 保留但需要知道其受限行为的标签
RESTRICTED_TAGS = {
    "table": "微信会丢弃 colgroup/thead/tbody，表格横向溢出，需外套 overflow-x auto 的 section",
    "pre": "white-space: pre 在部分版本失效，必须改写为 pre-wrap",
    "h1-h6": "h4 以下不可靠；且正文 h1 与文章主标题语义冲突，统一用 p + 内联字号表达",
    "a": "外链域名须在公众号后台「业务域名」白名单内，否则提示链接内容不属于当前公众号",
    "img": "src 必须是 mmbiz.qpic.cn 域名，本地路径与第三方 CDN 都不显示",
}

# 任何位置出现即整体剔除的属性
FORBIDDEN_ATTRS = frozenset({
    "id", "class", "onclick", "onload", "onerror", "onmouseover",
    "style_target", "data-src", "contenteditable", "draggable",
})

# 保留但无效 / 会被剥离的样式属性，输出时必须删除
DEAD_CSS_PROPS = frozenset({
    "position", "z-index", "transform", "animation", "transition",
    "filter", "backdrop-filter", "float", "display",
})

# 微信可用的 inline 样式白名单（保守集，实测通过率高）
SAFE_CSS_PROPS = frozenset({
    "font-size", "font-weight", "font-style", "font-family", "color",
    "line-height", "letter-spacing", "text-align", "text-decoration",
    "text-indent", "background-color", "background-image",
    "background-size", "background-repeat", "background-position",
    "margin", "margin-top", "margin-bottom", "margin-left", "margin-right",
    "padding", "padding-top", "padding-bottom", "padding-left", "padding-right",
    "border", "border-top", "border-bottom", "border-left", "border-right",
    "border-color", "border-width", "border-style", "border-radius",
    "box-shadow", "opacity", "vertical-align", "white-space",
    "word-break", "word-wrap", "list-style", "max-width", "min-width",
    "border-collapse", "border-spacing", "table-layout",
    # 下面几项是「保住布局」的关键，缺一个整张卡片就塌：
    "background",          # 简写形式，承载 linear-gradient 背景
    "overflow-x",          # 宽表格横向滚动的唯一手段，容器被剥就没有了
    "width", "height",     # iOS 上 SVG 不写宽高直接不显示
    "max-height", "min-height",
})

# 微信支持但在部分机型/版本表现不稳定，需人工确认
RISKY_CSS_PROPS = frozenset({"display", "overflow", "overflow-x", "overflow-y", "float"})

# 微信图片唯一合法域名
ALLOWED_IMAGE_HOST = "mmbiz.qpic.cn"

# 正文图片大小上限（字节）
MAX_INLINE_IMAGE_BYTES = 2 * 1024 * 1024

# 封面图大小上限（字节）
MAX_THUMB_BYTES = 2 * 1024 * 1024

# 封面最小宽度（像素），不足会报 53401
MIN_THUMB_WIDTH = 900

# draft/add 字段上限
# 依据：https://developers.weixin.qq.com/doc/service/api/draftbox/draftmanage/api_draft_add.html
LIMIT_TITLE = 32          # 字符，必填
LIMIT_AUTHOR = 16          # 字符
LIMIT_DIGEST = 120          # 字符（官方文档为120，非128）
LIMIT_CONTENT_CHARS = 20_000    # 正文字符数上限
LIMIT_CONTENT_BYTES = 1024 * 1024  # 正文字节上限（1MB）
LIMIT_SOURCE_URL = 1024     # content_source_url 字节上限（1KB）

# 内容保真策略 —— 删自带作者的文字是不可逆操作，永不由脚本自动执行
#
# 低于该 token 预算，正文必须逐字保留，一个字都不删、不改述、不摘要。
# 超过预算不得自动截断，只能输出告警由人决策：拆分系列 / 删章节 / 改排期。
CONTENT_FULL_TOKEN_BUDGET = 6_000

# 标题是点击率的决定性因素，与正文字数无关，单独校验
# 32 字是 draft/add 的硬上限，但 20 字以内才是信息流里不被截断的可读区间
TITLE_SOFT_LIMIT = 20