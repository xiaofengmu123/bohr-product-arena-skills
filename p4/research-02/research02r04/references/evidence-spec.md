# 证据与不确定性规范（本版核心）

本文件是 SKILL.md §5、§6 的展开规范，Agent 写证据表、主题、草稿与 `uncertainty` 时以此为唯一标准。所有字段都在脚本 `scripts/core_impl.py` 中有对应常量，可被 `scripts/selftest.py --check` 自动校验。

## 1. 证据行字段（evidence_table.jsonl）

每行一个 JSON 对象，必填字段：

| 字段 | 类型 | 要求 |
|-|-|-|
| `evidence_id` | string | 唯一，`E1`、`E2`… |
| `source_id` | string | 指向 `result.json` 的 `sources` 中一条，`S1`、`S2`… |
| `locator` | string | 文件 + 页码/段落，如 `materials/paper.pdf#p3` 或 `record.json#item2` |
| `quote` | string | **逐字原文摘录**，不得改写、不得拼接不同位置 |
| `claim` | string | 该证据支撑的论断（一句） |
| `strength` | string | `established` / `suggestive` / `disputed` / `not_reported` |
| `field` | string | 比较字段：对象/方法/样本量/主要结果/结论/局限/未报告项 |
| `notes` | string | 可选：抽样条件、随访时长、度量口径等 |

## 2. strength 语义与措辞映射

| strength | 含义 | 正文措辞 |
|-|-|-|
| `established` | 多源一致且方法充分 | 表明 / 证实 |
| `suggestive` | 单源或方法有限 | 提示 / 初步表明 |
| `disputed` | 存在冲突结论 | 存在争议 / 结论不一致 |
| `not_reported` | 材料未报告该项 | 未见报告 |

关键规则：**“未见报告” ≠ “不存在”**；`not_reported` 只表示当前材料没有证据，不构成否定结论。

## 3. 降级 / 局限类型（uncertainty.limitations 与 degradations）

每条局限必须带 `kind`、`detail` 与受影响的 `source_id`（可空数组），不写空泛套话：

| kind | 触发条件 | 处置 |
|-|-|-|
| `abstract_only` | 仅有摘要/无全文 | 结论降为 `suggestive` 或 `not_reported`，不补正文数据 |
| `no_full_text` | PDF 抽取失败或无全文 | 同 `abstract_only`，并在 `degradations` 记录 |
| `metadata_incomplete` | 题录缺字段 | 该字段标 `not_reported` |
| `source_conflict` | 两源同一指标不一致 | 两源都记录并并列呈现，不静默择一 |
| `tool_missing` | 缺 `pdftotext` 等软件 | 记入 `degradations`，用已抽取文本继续，不伪造解析结果 |
| `encoding_fallback` | 编码降级读取 | 记入 `degradations`，注明实际编码 |
| `out_of_scope` | 材料超出研究问题 | 排除并说明理由 |
| `duplicate_removed` | 重复文献 | 保留一条，其余标记去重来源 |
| `extraction_failed` | 抽取失败 | 记入 `degradations` |
| `contract_missing` | 无 `output_contract` | 使用兜底字段并如实标注 |
| `size_skipped` | 文件超过大小上限 | 跳过并记入 `degradations` |

## 4. uncertainty 结构

```json
{
  "conflicts": [{"claim": "...", "sources": ["S1", "S2"], "detail": "分歧点"}],
  "gaps": [{"question": "...", "status": "no_direct_evidence"}],
  "limitations": [{"kind": "abstract_only", "detail": "...", "source_id": ["S3"]}],
  "unverifiable": [{"claim": "...", "reason": "..."}],
  "not_reported": [{"field": "样本量", "source_id": ["S4"]}]
}
```

- `gaps` 只放**具体**证据缺口（哪个问题没有任何文献覆盖），不写“未来需更多研究”这类套话。
- `unverifiable` 放无法与材料核对的声明；一旦无法核实就写 `pending_human`，不编造。

## 5. 禁止事项（一票否决）

1. 虚构文献 / DOI / 数值 / 页码；
2. 跨文档串值（把 A 文的数值填进 B 文）；
3. 把相关写成因果；
4. 把摘要当全文证据；
5. 用文件名标签或“领域标签”代替真实内容判断；
6. 编造评测分数、运行结果或缺失的软件状态。
