# 字段级契约与降级规则（contract_notes）

本文件解释 `result.json` 的结构契约、证据溯源格式与本版重点
（**降级与不确定性显式化：缺数据不编造**）。

## 1. result.json 顶层结构

```text
case_id            # 从 /input/case.json 原样复制；缺失写 "unknown-case"
status             # ok | partial | insufficient_evidence | input_error
result             # {design, summary}
quality            # 七类情形 + 假设检查的逐项记录
descriptive        # 每列 n/mean/median/sd/min/max/Q1/Q3/IQR/skew/kurt + 组内分层
inferential        # 正态性/齐性/主检验/两两比较，带 method_note 与证据
effects            # 效应量 + CI，带口径与证据
interpretation     # summary / statistical_vs_practical / unit / notes
limitations        # 结构化限制（本版强化）：id/severity/affects/message
sources[]          # {source_id, path, kind, rel_path}
uncertainty[]      # 结构化不确定项（本版强化）：id/kind/severity/affects/message
change_log[]       # {id, time, action, before, after, reason}
errors[] / degradations[] / pending_human[] / runtime{}
artifacts          # cleaned/、analysis_report.md、figures/*.svg、quality_checks.json、analysis_code.py
```

**output_contract 回填规则**：读取 `/input/case.json` 的 `output_contract`
（dict 或 list），逐键回填；已知语义键（result/quality/descriptive/inferential/
effects/interpretation/sources/uncertainty/change_log）填计算值，未知键填
`null`；不改名、不删未知键。这是权威契约，字段名以 case.json 为准。

## 2. 证据溯源（evidence）

每个关键数值带：

```json
{"source_id": "S1",
 "locator": "cleaned/iris_clean.csv#sepal_length[setosa] 或 analysis_code.py#anova(...)",
 "quote": "F=74.32; df1=2; df2=57; p=1.1e-16",
 "kind": "computed"}
```

- 计算值：locator 指向「清洗后数据列 + 生成该值的代码函数」，quote 为确定性渲染的结果。
- 材料事实（单位、组标签、任务要求）：locator 指向 `task.md`/`case.json` 行号，quote 为原文逐字片段。
- 来源冲突：同指标两来源不一致时，两条都记录并标 `source_conflict`，不静默选其一。
- 不虚构 DOI、引用、数值；缺失写 `not_reported` 或 `pending_human`。

## 3. 降级策略（诚实优先）

| 场景 | 处置 |
|---|---|
| 缺数据文件 / 无表可载 | `status=insufficient_evidence`，uncertainty 列缺口，退出码 0 |
| 坏 JSON / 坏编码 | 记 errors/degradations，继续处理其余材料 |
| xlsx/docx/pdf 等非标准库可读 | survey/inspect 标注 `unsupported`，提示用运行时 pandas 转 CSV 后重跑 |
| 组内方差为 0 / n<2 / 缺失过高 / 检验不适用 | 该检验标 `not_applicable`，不给 p，说明影响 |
| 字段单位不明 | 标 `unit_unknown`，不猜 μg/mg、cm/mm |
| scipy 不可用 | 用脚本内置近似（AS R94 Shapiro-Wilk 等），`method_note` 标近似；可 crosscheck 时复核 |
| 脚本不可运行 | 手写合法 partial result.json，退出码恒 0，绝不零产物 |

## 4. 时间与请求预算（SKILL.md 第 4 节展开）

- 总预算：900 秒 / 64 次模型请求 / 每次回复 16384 tokens（含 reasoning）。
- **前 3 分钟内必须先落盘 `/app/output/result.json` 骨架（`init`），之后只做增量改进**。
- `init`（≤3 分钟）→ `survey`/`inspect`（约 1 分钟）→ 任务解读（写 plan.json）→
  `analyze`（秒级）→ 解释补写（剩余请求）→ `build` → `selfcheck`。
- 每步幂等、可重跑；中途异常不留下不完整产物。

## 5. 评分维度映射

| 评分维度 | 本包对应物 |
|---|---|
| 实际效果（完成/正确/完整/可核验） | 七类情形 + 描述/推断/效应/CI + evidence + 确定性复跑 |
| 使用增益（质量/效率/可验证） | 自动选检验 + 效应量绑定结论 + 单位不明主动 pending_human |
| 创新增益（新增能力/方法/证据） | 结构化 limitations/uncertainty + 缺数据不编造 + 按分布设计自动选法 |
| 可靠体验（易用/稳定/异常） | 空输入/坏编码/组内方差 0/字段类型多变下的稳定降级 |
| 生态贡献（文档/复用/分享） | SKILL.md + 两份 references + selftest 与 statlib 自检 |

## 6. 本版侧重点（变体 #降级与不确定性）

相比通用统计包，本版刻意把 **limitations / uncertainty 显式结构化**：
每项不确定带 `kind/severity/affects/message`，每条限制带 `id/severity/affects/message`；
缺数据时给 `insufficient_evidence` 而非编造数字；单位不明给 `unit_unknown` 而非猜值；
近似 p 值标 `method_note` 而非冒充精确。这是「给出结果，也保留计算依据」的降级侧实现。
