# 契约与复现说明

## 1. 输入契约

- `/input/case.json`：含 `case_id`、`task_instruction`、`output_contract`（可能含
  `data_dictionary`）。字段名以 case.json 为准，不假设固定键名。
- `/input/task.md`：题目说明与评分要求。
- `/input/` 其他材料：csv / tsv / json / sqlite(.db) / xlsx；xls / docx / pdf 按能力处理。
- 编码按 `utf-8-sig → utf-8 → gb18030 → latin-1` 容错；坏 JSON / 坏编码记录
  `errors` / `degradations`，继续处理其余材料，不崩溃。
- 单文件读取上限 4 MB，超限跳过并记录。

## 2. 输出契约

`/app/output/result.json` 顶层必须包含：
- `case_id`（从 case.json 原样复制，缺失写 `unknown-case`）；
- `output_contract` 的**全部键**逐键回填（缺值 `null` / `[]`，不改名、不删未知键）；
- 信封：`status`（`ok | partial | insufficient_evidence | input_error`）、
  `sources[]`、`uncertainty[]`、`change_log[]`、`errors[]`、`degradations[]`、
  `pending_human[]`、`runtime{}`；
- 主结果：`descriptive[]`、`inferential[]`、`effects[]`、`confidence_intervals[]`、
  `interpretation{}`、`evidence_map[]`、`quality_checks{}`。

## 3. 证据溯源（本版本重点）

`evidence_map` 每条证据至少含：`evidence_id`、`claim`（结论原文）、`kind`、
`source_id`、`locator`（如 `data.csv#col[outcome]#rows[0..14]` 或
`analysis_report.md#L120`）、`quote`（逐字片段）、`quote_kind`（`verbatim` /
`computed` / `note`）、`method`、`value`、`unit`、`status`。

- 数据来源结论引用原始文件中的逐字片段（表头 + 前若干单元格或原文句子）；
- 派生数值结论写明计算式与输入定位，quote 仍引用其数据来源；
- 冲突信息不隐藏：同一指标多个来源不一致时都记录并标 `source_conflict`；
- 不虚构数字、引用、DOI、软件状态、运行记录。

## 4. 复现要求

- 无随机数；固定顺序、固定种子语义；同输入两次运行字节级一致（selftest 校验）。
- `analysis_code.py` 是实际运行的流水线副本，可在 `/app/output` 复跑。
- 主产物可编辑、可复现：`cleaned/*.csv`、`analysis_report.md`、`figures/*.svg`
  （环境可渲染时补 PNG/PDF）、`quality_checks.json`、`analysis_code.py`。

## 5. 降级规则

| 场景 | 处置 |
|---|---|
| 缺数据文件 | `insufficient_evidence` + uncertainty 列缺口 |
| 坏 JSON / 坏编码 | 记 errors/degradations，继续处理其余材料 |
| 组内方差为 0 / n<2 / 缺失过高 | 该检验标"不适用"，不给 p，说明影响 |
| 字段单位不明 | 标 `unit_unknown`，不猜单位 |
| scipy 不可用 | 用标准库内置实现（Shapiro-Francia、正态近似 p），标口径 |
| 脚本不可运行 | 手写合法 partial result.json，退出码恒 0 |

## 6. 评分维度映射

- 实际效果 → 描述/推断/效应量/CI 完整性与数值正确性、可核验性；
- 使用增益 → 先落盘骨架 + 增量改进 + 证据溯源减少核对工作量；
- 创新增益 → 检验自动选择 + 效应量与 CI 绑定结论 + 逐字 evidence_map；
- 可靠体验 → 确定性、降级策略、空输入/坏输入不崩溃；
- 生态贡献 → SKILL.md / references 文档、可复用脚本与清单。
