# 定向回归：条款 3 / 4 / 5 三条裁决验证（2026-09-30）

批次：`batch_020`（seed 1013，selector=papers，n=4）
指纹：`prompt_sha1 e1c4a9980fa4` / `rubric_sha1 9ce742e90e99`

## 一、这次验什么

RUBRIC 经人批改了三处（用户批准「先裁完 3 条再发」）：

| # | 条款 | 改动 |
|---|---|---|
| 1 | §3 method_summary | 字数取值 30~40 → **25~45**；反例阈值 60 → 45（条款 0 的 ±4 容差计入后实为 49） |
| 2 | §4 training_paradigm | 新增「训练动作」外延：论文方法就是训练一个判别模型 / 预测器 / 回归头 → 计 `SFT`；为分析 / 探测 / 评测拟合的探针 → **不计** |
| 3 | §5 base_model | 新增：`training_paradigm` 取 `不训练` 时 `base_model` **仍可非空**（plug-and-play / 推理时干预 / 检索增强） |

EXTRACT 由修改 agent 同步（draft → 8/8 闸门 → `--apply`），指纹 `b59050e68c9b` → `e1c4a9980fa4`。

## 二、判读标准（开跑前写死，跑完不准找理由）

| 论文 | 覆盖裁决 | 预期（新标准下的正确输出） | 依据 |
|---|---|---|---|
| acl-short.66 | 1 + 2 | `method_summary` 压到 ≤45 字（原 52 字）；`training_paradigm` 保持 `["不训练"]`；`data_scale` 保持空 | 探针不计训练动作（条款 4 新边界）；52 > 49 判违规（条款 3） |
| acl-long.392 | 2 | `training_paradigm` **含 `SFT`**（原 `["预训练"]`，预测器是方法产物 → 应变成 `["预训练","SFT"]`） | 条款 4 新边界「判别模型即方法产物」 |
| findings-acl.1213 | 3 | `training_paradigm ["不训练"]` + `base_model` 非空（`LogicLLaMA`） | 条款 5 新边界（免微调、推理时注入扰动） |
| acl-long.1411 | 3 | `training_paradigm ["不训练"]` + `base_model` 非空（`CT2Rep, M3D, RadFM`） | 条款 5 新边界（plug-and-play 检索重排） |

**不合格信号（任一出现即判该裁决未生效）**：

- `acl-short.66` 的 `method_summary` 仍 >49 字、且评审未判 violation → 裁决 1 未生效
- `acl-short.66` 落 `[]` → 裁决 2 未生效
- `acl-long.392` 仍只 `["预训练"]` → 裁决 2 未生效
- `findings-acl.1213` / `acl-long.1411` 的 `base_model` 被清空，或评审报组合违规 → 裁决 3 反向

## 三、结果

全流程：执行端两波 3+1 → 4/4 落盘；解析失败 0、缺键 0；评审两波 3+1 → 4/4；**引用核验 4/4**。

| 论文 | method_summary | training_paradigm | base_model | data_scale | 评审 | 对照预期 |
|---|---|---|---|---|---|---|
| acl-short.66 | 31 字 | `["不训练"]` | （空） | （空） | pass | **一致**（原 52 字 → 31 字；原 2 条 rubric_issue 归零） |
| acl-long.392 | 38 字 | `["预训练", "SFT"]` | （空） | `约 90 万离子化脂质分子` | pass | **一致**（原 `["预训练"]` → 补 `SFT`） |
| findings-acl.1213 | 37 字 | `["不训练"]` | `LogicLLaMA` | （空） | pass | **一致**（组合边界保持） |
| acl-long.1411 | 41 字 | `["不训练"]` | `CT2Rep, M3D, RadFM` | （空） | pass | **一致**（组合边界保持） |

指标：**violation 0/4**，rubric_issue 0，无高频组、无跨批复现、无解析失败。

### 三条裁决的生效证据

1. **条款 3 字数**：`acl-short.66` 的 `method_summary` 由 52 字压到 **31 字** —— 执行端在新取值（25~45）下主动收短，而旧标准下这一篇正落在 45~60 死区、评审只能当歧义上报。死区消失，`batch_019` 的 2 条 rubric_issue 随之归零。
2. **条款 4 训练动作外延**：`acl-short.66`（`train linear probes`）稳定落 `["不训练"]`，评审明确引条款 4 新边界放行；`acl-long.392`（预测器 `serves as an oracle`）由 `["预训练"]` 变为 `["预训练", "SFT"]` —— 同一新边界在「探针」与「方法产物」两侧都判出了预期方向，b006 与 b019 的相反主张就此统一。
3. **条款 5 组合边界**：`findings-acl.1213`（`LogicLLaMA`）与 `acl-long.1411`（`CT2Rep, M3D, RadFM`）保持 `不训练` + `base_model` 非空，两次评审都直接引条款 5 新边界放行 —— 原先「执行端填、评审放过但无条款依据」的做法，现在有了明文。

### 结论

三条裁决全部生效，**无一回退**。`batch_019` 暴露的三条人裁级待办已消解，其中两条的原始 rubric_issue 直接归零。

## 四、落档

- 本报告：`runs/regression_20260930_clauses_3_4_5.md`，包内副本 `references/` 同名文件。
- RUBRIC 快照 `runs/prompt_snapshots/rubric_v7.md`（改前 `7d72b130107c`）；EXTRACT 快照 `runs/prompt_snapshots/extract_v11.md`（改前 `b59050e68c9b`）。
- 改后指纹：`rubric_sha1 9ce742e90e99` / `prompt_sha1 e1c4a9980fa4`。
