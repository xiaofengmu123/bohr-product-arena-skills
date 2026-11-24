# 模板与要素清单

## 1. 六要素覆盖表

| 要素 | 必写项 | 常见缺口（判分点） |
|---|---|---|
| 方法步骤 | 输入→操作→输出→判断条件 | 多步压成一句 |
| 实验设置 | 训练/验证/测试分开；硬件、软件版本、环境、种子、重复次数 | 条件串台 |
| 参数与来源 | 名称、取值、单位、作用对象、来源 | 正文与表不一致、无来源 |
| 数据集与划分 | 名称、版本、规模、预处理、划分比例、随机方式、子集用途 | 版本含混 |
| 对照实验 | 基线名称与版本、是否同预算、评价指标、统计方式 | 断言式比较 |
| 可复现与限制 | 代码版本、环境、命令、数据获取方式；具体失效场景 | 套话限制 |

## 2. 参数表模板

| 参数 | 取值 | 单位 | 作用对象 | 来源 |
|---|---|---|---|---|
| learning_rate | 0.001 | — | 训练 | configs/train.yaml#L12 |

来源列填 locator；无来源填 `not_reported`，交人工确认填 `pending_human`。

## 3. 证据映射模板（evidence_map.json）

```json
{
  "S1": {"locator": "materials/configs/train.yaml#L12", "quote": "lr: 0.001"},
  "S2": {"locator": "materials/paper.pdf#p3", "quote": "在 10k 样本上训练 100 epochs"}
}
```

正文中以 `[S#]` 挂接；locator + quote 两者都非空才算有效证据。

## 4. 复现清单模板

| 项 | 内容 | 来源 |
|---|---|---|
| 代码版本 | commit/版本号 | git log / requirements |
| 环境 | python 与依赖版本 | environment.yaml / requirements.txt |
| 命令 | 训练/评测命令 | README / 脚本 |
| 数据获取 | 下载/授权方式 | 数据说明 |

## 5. 追问清单模板（pending_human 条目）

每条含 `id`、`question`、`why`（为什么阻塞成稿）。例：

```json
{"id": "PH1", "question": "请确认数据集版本号", "why": "材料仅给出名称，版本含混会导致复现失败"}
```

## 6. 降级决策表

| 情况 | status | 处置 |
|---|---|---|
| 无材料 | insufficient_evidence | 列出缺口，不编造示例答案 |
| 缺软件 | partial | `degradations` 记录，其余材料继续 |
| 坏 JSON/编码 | partial | `errors`/`degradations` 记录 |
| 参数无来源 | partial | `pending_human`，不补数 |
| 指标冲突 | partial | 并列 + `source_conflict` |
