# Slide DSL 速查（HTML → PPT 转换常用子集）

转换用的是 `workbuddy-pptx` 技能的 `slidep`，DSL 是受限 JSX，直接编译成 OOXML。**不要** `import`/`export`/hooks/state。文件最后一个表达式必须是 `<Slide>`。

## 布局硬约束（从这里踩坑最多）

- 画布固定 **1280×720**。padding 上下左右各 **≥60px**（推荐左右 80），内容区 **1160×600**。
- 高度必须预先算：`上padding + 标题区 + 内容区 + 各间距 + 下padding ≤ 720`。**禁止**主内容区用 `flex:1` 又不算高度。
- 页脚 y≈670（bottom:28），字号 13–16，全篇位置固定。
- 封面/目录/附录页也放页脚页码可以，但要全篇一致（用户原有设计就带页码时保留）。
- **Box 只支持 `display:flex`**，严禁 `grid`、`table` 及 `gridTemplateColumns` 等。HTML 里的 `cols2/cols3`（grid）要用 `flexDirection:'row'` + 子项 `flex:1` 重写。
- **不支持 `calc()`**。要动态尺寸用 `flex:1` / 百分比 / 精确算好的固定值。
- 同页文字样式 ≤4 种；相邻层级字号差 ≥4px；行高 ≥1.2。

## 组件速查

### Slide —— 页面根容器

```jsx
<Slide style={{ width: '1280px', height: '720px', background: '#FFFFFF',
                padding: '60px 80px', position: 'relative', flexDirection: 'column' }}
       notes={`讲稿正文……`}>
  ...
</Slide>
```

`notes` 见 [speaker-notes.md](speaker-notes.md)。顶部渐变条用绝对定位的 Box 贴在 `top:0`。

### Box —— 布局容器

默认 `display:flex; flexDirection:column`；`box-sizing:border-box`。

```jsx
{/* 横向两栏，等高 */}
<Box style={{ flexDirection: 'row', gap: 36, alignItems: 'stretch' }}>
  <Box style={{ flex: 1 }}>...</Box>
  <Box style={{ flex: 1 }}>...</Box>
</Box>
```

绝对定位装饰：父 Box 设 `position:'relative'`，装饰设 `position:'absolute'` + `top/left/right/bottom`。

### Text —— 文字

```jsx
<Text style={{ fontSize: 20, color: '#334155', lineHeight: 1.7 }}>
  <span style={{ fontWeight: 'bold', color: '#0f766e' }}>重点</span>后面<br />换行
</Text>
```

- 换行用 **`<br />`**（小写自闭合），**不要 `\n`**（只有讲稿 notes 里用换行）。
- 局部样式用 `<span style={{...}}>`；**不支持** `<strong>/<em>/<u>`，一律 span。
- 等宽文本（目录树/代码）用 `fontFamily: 'Consolas, Courier New, monospace'`。

### Image —— 图片

```jsx
<Image src="assets/policy.png" style={{ width: '100%', height: 150,
       objectFit: 'contain', borderRadius: 8 }} />
```

- `src` **必须本地相对路径**（相对项目根），图片先放 `assets/` 且用英文名。
- `objectFit:'contain'` 完整显示（截图推荐）；`'cover'` 裁切填满。
- 显示宽高比尽量与图片实际比例一致，先跑 `scripts/check_images.py` 看比例。

### Table —— 表格

```jsx
<Table style={{ width: '100%', height: 360 }}
  defaultTextStyle={{ fontSize: 15, textAlign: 'left', color: '#334155' }}
  defaultCellStyle={{ border: {
    left: { width: 1, color: '#e2e8f0' }, right: { width: 1, color: '#e2e8f0' },
    top: { width: 1, color: '#e2e8f0' }, bottom: { width: 1, color: '#e2e8f0' } } }}
  cells={[
    [ { text: '表头', textStyle: { bold: true, color: '#fff' },
        cellStyle: { background: { color: '#0f766e' } } }, '列2' ],
    ['行1列1', '行1列2'],
  ]}
/>
```

- `style.width`/`style.height` **必填**；推荐百分比宽度防溢出。
- `defaultTextStyle`/`defaultCellStyle` **必填**；所有行列数一致。

## HTML → DSL 常见映射

| HTML | DSL |
|---|---|
| `<div class="cols2">`（grid） | `<Box flexDirection:'row'>` + 子项 `flex:1` |
| `<div class="card accent">` | `<Box background:'#ccfbf1' border:'1px solid #99f6e4'>` |
| `<strong>` / `<b>` | `<span style={{fontWeight:'bold'}}>` |
| `<br>` | `<br />` |
| `<div class="tree">…</div>`（pre 文本） | `<Text fontFamily:'Consolas,...'>` + `<br />` 逐行 |
| `<img src="img/x.png">` | 复制到 `assets/x_en.png` + `<Image src="assets/x_en.png">` |
| `<div class="notes"><b>讲稿：</b>…</div>` | `<Slide notes={...}>`（改写后，见讲稿规范） |
| 顶部 `.slide::before` 渐变条 | 绝对定位 Box，`top:0;height:5;background:linear-gradient(...)` |
| `.foot` 页脚 | 绝对定位 Box，`bottom:28;left:80;right:80;flexDirection:'row'` |

## 目录树行数变多的处理

树每多一层，行数增加。原来 11 行、字号 16、行高 1.9 刚好；加到 15 行会溢出。处理：把字号降到 14、行高 1.7、padding 收一点，再 `slidep lint` 确认无 `no-overflow`。真实案例见 [pitfalls-and-examples.md](pitfalls-and-examples.md)。
