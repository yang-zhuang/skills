# extras/ —— 参考代码库（不是流程必需）

**这个目录不参与拆图流水线的任何一个环节。** 六个环节要用的东西全在上一级
`scripts/` 根目录（11 个核心 `.py` + `.env` + `requirements.txt`）。

那为什么留着这两个脚本？因为它们各解决过一类**具体而刁钻**的问题，写法本身是现成的思路：

| 文件 | 它解决的那类问题 | 值得借鉴的写法 |
|---|---|---|
| `batch_t3.py` | 一个根目录下**几百个 `t3` 文件夹**，每个里一张 `t3.dwg`，要各拆一套 | 断点续传标记（`split_report.json` 存在即算跑完）、`os.walk` 原地裁剪（见到 `t3` 就不再往下、层数不写死）、`tqdm` 用 `try import` 兜底 |
| `merge_permille.py` | 源图把数字和 `‰` 符号做成了**两个独立实体**、渲染后叠在一起 | 按实体距离与字高配对、合并成一个 `TEXT` |

## 怎么用

单文件、只依赖 `ezdxf` 的直接跑：

```bash
python extras/merge_permille.py <输入.dwg> <输出.dwg>
```

`batch_t3.py` 会 `import main / split_dwg / png_export`（都在上一级），
从 `extras/` 直接跑找不到它们。两种跑法：

```bash
# ① 把上一级的 scripts/ 加进模块搜索路径
PYTHONPATH=<skill 的 scripts 目录> python extras/batch_t3.py <根目录>

# ② 或者复制到 scripts/ 根目录再跑（跑完记得挪回来，否则 --prune 会把它当孤儿删掉）
cp extras/batch_t3.py . && python batch_t3.py <根目录>
```

## 两个提醒

- **改流程代码请改现场 `06_work/` 里的副本。** 发行版 `scripts/` 是只读基线
  （`BASE.txt` 记着它的 sha256），改了它 `compare_work.py` 就算不出改动了。
- **想探查聚类效果，用 `python main.py <图纸.dwg> --cluster`**，不要指望另找脚本。
  （`main.py --cluster` 调的 `cluster_dwg.py` 就在上一级，是它的硬依赖；
  单独拿 `cluster_dwg.py` 手工跑出来的数字**对不上真实拆分**——它没注册 SHX 字体，
  文字量不出真实 bbox。）

本目录下的 `.md` 不受 `pack_skill.py --prune` 影响（它只清 `.py` / `.sh`）。
