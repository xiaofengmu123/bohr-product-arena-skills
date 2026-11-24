# 六类要素覆盖表、事实台账 Schema 与参数表模板

## 1. 六类要素覆盖表（结果完整性评分点）

| # | 要素 | 必写内容 | 常见缺陷（扣分点） |
|---|------|----------|--------------------|
| 1 | 方法步骤 | 每步：输入→操作→输出→判断条件 | 多步压成一句；缺终止/判据；步骤不可执行 |
| 2 | 实验设置 | 训练/验证/测试分开；硬件、软件版本、运行环境、随机种子、重复次数 | 条件串台；seed/重复次数缺位 |
| 3 | 参数与来源 | 参数表含 名称、取值、单位、作用对象、来源；正文数值与表对齐 | 正文与表不一致；来源空写“默认” |
| 4 | 数据集与划分 | 名称、版本、规模、预处理、划分比例、随机方式、各子集用途 | 版本含混；划分比例缺失；用途不明 |
| 5 | 对照实验 | 基线名称与版本、是否同预算、评价指标、统计方式 | 与基线比较成断言而非可比结论 |
| 6 | 可复现与限制 | 复现清单（代码版本、环境、命令、数据获取方式）；限制落到具体失效场景 | 套话限制；缺命令/环境版本 |

## 2. 事实台账 Schema（唯一中间表示）

`/app/work/fact_ledger.json` 顶层结构：

```json
{
  "ledger": {
    "steps":     [ { "kind": "command", "value": "...", "locator": "...", "quote": "...", "strength": "confirmed|candidate", "conflicts": [] } ],
    "setup":     [ ... ],
    "params":    [ ... ],
    "data":      [ ... ],
    "baselines": [ ... ],
    "repro":     [ ... ]
  },
  "counts": { "steps": 0, "setup": 0, "params": 0, "data": 0, "baselines": 0, "repro": 0 }
}
```

每条记录必须含：

| 字段 | 含义 | 要求 |
|------|------|------|
| `locator` | 来源定位 | `materials/<file>#L<line>` 或 `<paper>.pdf#p<page>`，非空 |
| `quote` | 逐字片段 | 原文一字不差，非空 |
| `value` | 抽取值 | 数值不改单位/有效数字；对象可用字符串或小对象 |
| `strength` | 置信度 | `candidate`（未核实）→ `confirmed`（模型对照原文确认） |
| `conflicts` | 冲突 | 同一指标另一来源的 locator+quote，无则空数组 |

规则：

- 成稿 `manuscript.md`、`evidence_map.json`、`preserved_facts.json` 均由台账渲染，`[S#]` 与台账编号一一对应，避免“正文-证据”漂移。
- 材料没有的数值写 `not_reported`；无法判断的缺口写 `MATERIAL GAP`；需作者确认的写 `pending_human`。
- 冲突不隐藏：两个来源都记，标 `source_conflict`。

## 3. 参数表模板（正文与表对齐）

| 参数 | 取值 | 单位 | 作用对象 | 来源 | 证据编号 |
|------|------|------|----------|------|----------|
| learning rate | 0.001 | - | 优化器 | train_config.yaml#L1 | S12 |
| random seed | 42 | - | 数据划分 | train_config.yaml#L2 | S13 |

正文引用该数值时须与表中“取值”逐字一致，并挂同一 `[S#]`。

## 4. 六要素关键词（抽取辅助，非硬性结论）

- steps：step/procedure/protocol/first/then/run/execute/步骤/流程
- setup：epoch/batch/lr/gpu/seed/cuda/device/训练/验证/测试/随机种子/重复
- params：parameter/threshold/temperature/weight/参数/阈值/系数/超参
- data：dataset/corpus/split/samples/images/tokens/数据/样本/划分/子集/预处理
- baselines：baseline/compare/vs/sota/ablation/基线/对照/消融
- repro：environment/requirements/docker/conda/python/version/复现/环境/依赖/版本

关键词只用于**候选归位**，最终 facet 归属必须由模型对照原文语义确认。

## 5. result.json 标准字段说明（输出契约填写参考）

除 `output_contract` 全部原始字段（键名/数量/嵌套结构原样，未知值 `null`/`[]`/`{}`）外，顶层必须含：

| 字段 | 类型 | 何时填 | 语义 |
|------|------|--------|------|
| `case_id` | string | 始终 | 来自 `case.json` 的 `case_id`/`id`/`challenge_id`，缺省 `"unknown"` |
| `status` | string | 始终 | `ok` / `partial` / `insufficient_evidence` / `input_error`，如实取值 |
| `artifacts` | list[str] | 始终 | 可编辑产物路径，**相对 `/app/output/`**（如 `manuscript.md`） |
| `sources` | list | 始终 | 实际读取并采用的材料文件清单 |
| `uncertainty` | list | 有缺口时 | 材料未给出、仅推断、或低置信的条目 |
| `pending_human` | list | 有需确认项时 | 需作者确认的缺口（每条含 locator + question） |
| `change_log` | list | 始终 | 骨架→初稿→改进→收口 的逐条记录，可回退 |
| `errors` | list | 有异常时 | 坏 JSON/坏编码/读盘失败等输入错误 |
| `degradations` | list | 有降级时 | 超限跳过、缺软件、编码替换等降级处置 |
| `limitations` | list | 始终 | 本结果适用范围与限制（指向具体失效场景） |
| `runtime` | object | 始终 | 耗时/阶段等运行信息 |

`status` 判定：材料足以覆盖六类要素且全部有来源 → `ok`；部分覆盖或有未确认项 → `partial`；几乎无材料 → `insufficient_evidence`；`case.json` 无法解析 → `input_error`。

## 6. 六要素核对示例（结构示例，非正式答案）

> 以下仅为**结构示意**，展示“方法步骤”一节如何逐条挂 `[S#]`，不包含任何真实实验数据。

```markdown
## 方法步骤
1. 输入 `<dataset>`，执行 `<preprocess>`，得到 `X`（判断：形状合法）[S1]。
2. 以 `lr=0.001`、`seed=42` 训练 `<epochs>` 轮，输出检查点（判断：loss 收敛）[S2][S3]。
```

对应 `evidence_map.json`：

```json
{
  "S1": {"facet": "steps", "locator": "materials/preprocess.py#L12", "quote": "X = preprocess(dataset)"},
  "S2": {"facet": "params", "locator": "materials/train_config.yaml#L1", "quote": "lr: 0.001"},
  "S3": {"facet": "setup", "locator": "materials/train_config.yaml#L2", "quote": "seed: 42"}
}
```

规则：`[S#]` 编号与台账一一对应；`quote` 必须逐字等于材料原文，不得转写或缩略。
