# 六要素覆盖表与模板

供第 2–3 步使用。六要素逐项覆盖，避免"只剩能读通的主干"；所有模板字段均需带回
`[S#]` 或 locator+quote，不允许出现无来源的空格。

## 1. 六要素覆盖表

| 要素 | 必须写明的字段 | 常见缺口（写 MATERIAL GAP） | 典型来源 |
|-|-|-|-|
| 方法步骤 | 输入→操作→输出→判断条件 | 步骤合并、缺判断条件、缺中间产物名 | 代码、脚本、README |
| 实验设置 | 硬件、软件版本、运行环境、随机种子、重复次数；train/val/test 分开 | 训练与评测条件串台、缺种子、缺重复次数 | 配置、日志、requirements |
| 参数与来源 | 名称、取值、单位、作用对象、来源 | 数值无来源、单位缺失、正文与表不一致 | 配置文件、日志、作者确认 |
| 数据集与划分 | 名称、版本、规模、预处理、划分比例、随机方式、各子集用途 | 版本不明、划分比例缺失、随机方式缺失 | 数据说明、预处理脚本 |
| 对照实验 | 基线名称与版本、是否同预算、评价指标、统计方式 | 基线无版本、未说明同预算、无统计方式 | 论文、实验记录、脚本 |
| 可复现与限制 | 代码版本、环境、命令、数据获取方式；限制落到具体失效场景 | 限制写成套话、缺数据获取方式 | 仓库、Dockerfile、README |

## 2. 参数表模板

```markdown
| 参数 | 取值 | 单位 | 作用对象 | 来源 | 证据 |
|---|---|---|---|---|---|
| learning_rate | 1e-3 | — | 优化器 | config.yaml#L12 | [S3] |
| batch_size | 32 | 样本/步 | 训练循环 | train.py#L45 | [S4] |
| seed | 42 | — | 全局随机 | config.yaml#L9 | [S5] |
```

- 数值逐字复用，不改有效数字与单位；未知写 `not_reported`，来源不明写 `pending_human`。
- 正文每个数值都要与表对齐（第 4 步逐条核对）。

## 3. 证据映射模板（evidence_map.json）

```json
{
  "S1": {
    "locator": "materials/config.yaml#L12",
    "quote": "learning_rate: 1e-3",
    "claim": "学习率设为 1e-3"
  },
  "S2": {
    "locator": "materials/paper.pdf#p3",
    "quote": "accuracy 92.3% (n=5)",
    "claim": "准确率 92.3%，n=5"
  }
}
```

- 每条 `[S#]` 都要有 locator + 逐字 quote；quote 必须是材料原文片段，不改写。
- 冲突同一指标多来源时，两个都记，并在 `claim` 或 `uncertainty` 标 `source_conflict`。

## 4. 追问清单模板（pending_human）

```json
[
  {"id": "PH-1", "item": "数据集版本", "question": "请确认 ImageNet 使用 2012 版还是 ILSVRC 版本？材料仅写 'ImageNet'。", "evidence": "materials/README.md#L8"},
  {"id": "PH-2", "item": "随机种子重复次数", "question": "请确认实验是否用同一 seed 重复 3 次？日志只出现一次 seed=42。", "evidence": "materials/config.yaml#L9"}
]
```

- 追问只针对材料缺口，不追问已由材料回答的内容；无法确认时写 `not_reported` 并保留 `pending_human`。

## 5. 复现清单模板

```markdown
- 代码版本：`git rev-parse HEAD` → <hash>（或 not_reported）
- 环境：Python 3.10.12；依赖见 requirements.txt [S6]
- 数据获取：<URL/来源>（无来源写 not_reported）
- 命令：`python train.py --config config.yaml --seed 42` [S7]
- 评测命令：`python evaluate.py --split test --ckpt best.pt` [S8]
```

## 6. 条件串台校验（第 4 步专用）

train / val / test 三条件分别核对以下字段，任一字段在材料中缺失或与别处冲突都要显式标注：

| 条件 | 数据子集 | 种子 | 批大小 | 学习率 | 评测指标 | 来源 locator |
|-|-|-|-|-|-|-|
| train | 训练集 | | | | — | |
| val | 验证集 | | | | | |
| test | 测试集 | | | | | |

核对通过后，正文中任何"训练/评测条件"表述都应与上表一致，消除串台。
