# AutoDL 社区镜像分层分析 skill

对社区镜像（应用名如「MINIMAX-H3提速500高画质」）做可复用核查：把营销名里的宣称拆成可验证命题，
按六层推进，并把「通用方法 / 固有规格 / 实例数值」分开沉淀。也覆盖「拿到镜像后怎么用」。

## 结构

- `SKILL.md` — 触发条件、决策树、硬规则、资源索引
- `references/layered-method.md` — L0~L5 六层方法，每层给入口 / 判定问题 / 判定动作 / 退出 / 常见误判
- `references/ssh-connection.md` — SSH 三路径、安全约定、认证失败的三层诊断
- `references/usage-guide.md` — 拿到镜像怎么用：先找作者说明、UI/API 入口、取产物、截图方法
- `references/environment-recipe.md` — 从基础镜像还原：git remote/tag/补丁、依赖坑、盘位与扩容
- `references/model-recipe.md` — 模型反查、软链、下载判定、区别说明与完整性校验
- `references/minimax-h3-model-variants.md` — H3 家族规格、缩写对照、量化档与显存估算（固有规格）
- `references/evidence-and-ledger.md`、`existing-analysis.md` — 四级证据与台账、已有实证索引
- `scripts/analysis-ledger-template.json` — 空台账模板
- `scripts/ssh_batch.py`、`scripts/ssh_diag.py` — 通用远程只读批量 / 分层诊断脚本

## 远程脚本用法（密码只走环境变量，不落盘）

```bash
export AUTODL_SSH_HOST='<控制台给出的 SSH 主机>'
export AUTODL_SSH_PORT='<端口>'
export AUTODL_SSH_PASS='<运行时输入>'
python scripts/ssh_diag.py
python scripts/ssh_batch.py cmd.txt out.txt
unset AUTODL_SSH_PASS
```

`ssh_diag.py` 依次检查 TCP → host key 指纹 → 认证。**认证失败就停止重试**，不要连续尝试触发风控。

## 设计原则

1. 应用名里的数字与形容词都是「宣称」，没有受控实测不得写成「实测」。
2. 方法进 skill、固有规格进规格卡、实例数值留项目台账；宣称与实测永不混写。
3. 换镜像只重跑受实例影响的层，方法与规格直接复用。
