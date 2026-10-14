# 图片与封面规格

## 两条通道，不可混用

| 用途 | 接口 | 返回值 | 硬性限制 |
| --- | --- | --- | --- |
| 正文插图 | `media/uploadimg` | `url`（mmbiz 链接） | bmp/png/jpeg/jpg/gif，≤2MB |
| 封面图 | `material/add_material?type=image` | `media_id`（永久） | **必须 JPG**，≤2MB，宽度 ≥900px |

传反的后果：封面传 `uploadimg` 的 url → 40007；正文用 material 的 media_id → 图片不显示。

## 正文插图

- 目标宽度 900–1080px。微信按375pt 屏宽自动等比缩放
- src **必须**是 `mmbiz.qpic.cn`。以下全部无效：
  - 本地相对路径 `./a.png`
  - 第三方 CDN（阿里云OSS、七牛等）
  - base64 data URI
  - SVG 内联或外链
- 重跑同一篇文章时复用 `assets/image-map.json`，不必重复上传

## 封面图

- 比例 **2.35:1**（如 900×383）。正方形封面在列表页会明显劣化
- 宽度 ≥900px，不足报 53401
- 上传走 `material/add_material`，不是 `uploadimg`
- `cover_info.crop_percent_list` 的 `ratio` 只接受 `2.35_1` 与 `1_1`：

```json
"cover_info": {"crop_percent_list": [
  {"ratio": "2.35_1", "x1": "0.1945", "y1": "0", "x2": "1", "y2": "0.5236"},
  {"ratio": "1_1",     "x1": "0",       "y1": "0", "x2": "1", "y2": "1"}
]}
```

## 图片居中

微信图片默认左对齐。**不要**用 `display:block; margin:auto` —— `display`
会被过滤。

正确做法是用段落居中包一层：

```html
<p style="text-align: center; margin: 18px 0px;">
  <img src="https://mmbiz.qpic.cn/..." alt="说明" style="max-width: 100%; border-radius: 4px;" />
</p>
```

渲染器已按此生成，不要手工改回。

## 生成封面

```bash
python skills/wx-imagine/scripts/make_cover.py \
  --title "文章标题" --subtitle "副标题" -o assets/build/cover.jpg
```

需要 Pillow。若未安装会明确提示，不静默降级：

```
pip install Pillow -i https://pypi.org/simple
```

注意本机 pip 全局 index-url 配置有误（指向清华镜像且路径多了 `/pypi/web`），
安装时**必须显式加 `-i https://pypi.org/simple`**。

## AI 生成配图

产出后**必须**再走一遍上传流程，直接引用本地文件或第三方 CDN 链接一律无效。