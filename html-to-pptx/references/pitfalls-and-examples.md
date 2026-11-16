# 踩坑清单与正反例（动手前必读）

全部来自真实转换案例（11 页 HTML 汇报稿 → 可编辑 PPTX）。每条都写清楚"错在哪、怎么改"。

## 一、决策层踩坑

### 坑 1：以为有"一键高保真转换"
HTML 的 flex/grid 布局、CSS 变量、JS 翻页都不是 PPT 的原生对象。不存在把 HTML 直接转成可编辑 PPT 的可靠工具。
- ❌ 找各种"HTML 转 PPT"在线工具，结果版式全乱、文字变图片。
- ✅ 明确两条路让用户选：**可编辑重建**（推荐）or **逐页截图**。重建=按内容重新排版，讲清楚不会逐像素一致。

### 坑 2：没对齐就动手，用户拿浏览器截图逐页比对
- ❌ 直接开始生成，用户看到版式不同就返工。
- ✅ 开始前明说"版式重排、风格一致但不逐像素照搬"，并确认讲稿去向。

### 坑 3：`.ppt` vs `.pptx` 没解释
用户说"转成 .ppt"，命令行只能生成 `.pptx`。
- ✅ 开头说明：生成 `.pptx`（通用格式），PowerPoint 都能开；真要老后缀去 PowerPoint 另存为 97-2003。

## 二、素材层踩坑

### 坑 4：中文/空格图片文件名导致资源加载失败
- ❌ `<Image src="assets/发改委文件截图.png">`、`"业绩 表格.png"` —— slidep 资源路径要求英文，中文/空格/特殊字符会加载失败（`failures` 非空）。
- ✅ 复制到 `assets/` 时改英文名：`policy.png`、`qualification_table.png`。复制和改名用 `cp` 一次性做，再用 `scripts/check_images.py` 核对。

### 坑 5：图片比例不明导致拉伸
- ❌ 直接写 `width:400;height:150`，截图被压扁。
- ✅ 先跑 `scripts/check_images.py assets/` 拿宽高比，用 `objectFit:'contain'` + 接近原比例的尺寸。图偏小（宽或高 <400px）会被脚本 WARN，放大到 PPT 会糊，要换高清图。

## 三、讲稿层踩坑

### 坑 6：讲稿原文整段塞进备注
- ❌ `notes` 直接放 HTML 的 `讲稿：这一页展示了……如下图所示……讲的时候先讲……`，带前缀、元描述、超 300 字。
- ✅ 按 [speaker-notes.md](speaker-notes.md) 改写：去前缀、第一人称、删元描述、≤300 字、分点换行。用 `scripts/check_notes.py slides/ --require` 校验。

### 坑 7：多页内容没同步（改稿时高发）
真实案例：用户要求目录树"先按文件类型分类"。改了树和正文，但讲稿还停留在"历史标书按目录放"。
- ✅ 正文、图形、讲稿三处一起改；改完重新跑 `scripts/inspect_html.py` 逐页比对。

## 四、DSL / 布局踩坑

### 坑 8：把 HTML 的 grid 和 calc 直接搬进 DSL
- ❌ `display:'grid'`、`gridTemplateColumns:'1fr 1fr'`、`width:'calc(100% - 40px)'` —— 全部不支持，lint 报错。
- ✅ 两栏用 `flexDirection:'row'` + 子项 `flex:1` + `gap:36`；动态尺寸用 `flex:1` 或百分比或算好的固定值。

### 坑 9：`<strong>` / `<br>` 大小写
- ❌ `<strong>`、`<b>`、`<br>`、`<BR/>`。
- ✅ `<span style={{fontWeight:'bold'}}>`，换行 `<br />`（小写自闭合）。

### 坑 10：树/代码块行数增加导致溢出
真实案例：目录树从 11 行加到 15 行（多了文件类型分类层），原字号 16/行高 1.9 撑爆页面。
- ✅ 字号 16→14、行高 1.9→1.7、padding 收紧，然后 `slidep lint` 确认 `diagnostics:[]`。

### 坑 11：页脚/进度条等装饰与正文重叠
- ✅ 装饰用绝对定位 + `position:'relative'` 父容器；正文在相对定位的内容 Box 里（`zIndex:1`）。

## 五、工具链踩坑

### 坑 12：先 lint 后 upsert
- ❌ 页面还没写入 pptx 就 lint，page-index 对不上。
- ✅ 顺序固定：写 `.slide` → `upsert-dsl` 追加 → 用当下标 `lint`。修复时 `upsert-dsl --page-index <同一下标>` 替换后重新 lint。

### 坑 13：靠文件名猜 page-index
- ❌ 看 `09.slide` 就以为 page-index=9。
- ✅ page-index 是 **0-based**，第 9 页 = 8。下标在逐页循环里自己维护。

### 坑 14：改一页却重新生成整份
- ❌ 用户改一处，重新 create 11 页，前功尽弃还容易引入新问题。
- ✅ 只改对应 `.slide` 源文件，`upsert-dsl --page-index <NN-1>` 替换单页，其余不动。

### 坑 15：改完不刷新预览
- ✅ 单页替换后用 `present_files` 刷新，用户能看到最新结果。上下文已注入 `file_id` 时不要重复 `open_file`。

## 六、完整正反例：目录树"先分类"改稿（真实案例）

**需求**：原目录树是 `标书/项目名/{1-招标文件,2-响应文件,3-附件}`，用户要求在中间插一层文件类型分类。

### 反例 ❌ —— 只改图形，忽略其余

HTML 里的树改了，但：
- 正文列表还写"历史标书按目录放"；
- 讲稿还是"按目录放，放多少兼容多少"；
- PPT 没同步改。

结果 HTML 和 PPT 不一致，用户两处看到不同结构。

### 正例 ✅ —— 三处同步 + 单页替换

**新结构**（先分类）：
```
标书/
├── 磋商文件/
│   └── 项目名/{1-招标文件, 2-响应文件, 3-附件}
├── 竞争谈判/
│   └── 项目名/...
└── 询比文件/
    └── 项目名/...
```

**HTML 改动**：`.tree` 的 `<div>` 文本重构 + 列表项改成"按类型、项目分类放" + 讲稿补"先按磋商文件、竞争谈判、询比文件分开，每个类型下面再放各个项目"。

**PPT 改动**：只改 `slides/09.slide` 的树 `<Text>`、列表项、`notes` 三处，树字号 16→14、行高 1.9→1.7 防溢出。

**落地命令**：
```bash
slidep upsert-dsl "AI辅助标书制作.pptx" --dsl-file slides/09.slide --page-index 8
slidep lint "AI辅助标书制作.pptx" --dsl-file slides/09.slide --page-index 8
# lint 结果 ok:true / diagnostics:[] 即通过
```
最后 `present_files` 刷新预览。教训：**图形、正文、讲稿三处一起改；改一页不要重建整份。**
