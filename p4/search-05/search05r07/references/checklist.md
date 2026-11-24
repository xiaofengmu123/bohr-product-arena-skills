# 交付前检查清单与降级策略

按评分维度组织；交付前运行 `python3 -B scripts/selftest.py --input /input --output /app/output`
逐项核对（退出码 0 = 全部通过）。

## 1. 实际效果（结果可复核）

- [ ] `result.json` 合法 JSON，含 `case_id`，`status` ∈ {ok, partial, insufficient_evidence, input_error}
- [ ] 输出契约每个字段都存在（缺值填 `null`/`[]`，不改名、不删键；case.json 自带 `output_contract` 全部并入）
- [ ] 每条 `candidates[i]` 有 `record_id` 与 `provenance`（库—查询—原始响应—映射—去重—合并可回溯）
- [ ] `provenance.raw_snapshots[].snapshot` 是原始响应逐字 JSON 片段，非改写
- [ ] 去重可解释：DOI 精确 > 标题+一作+年份 > 标题+年份 > 标题模糊（≥0.90 且同年）
- [ ] `coverage` 列明每库 `systematically_missing_fields` 与 `records_in_single_db_only`

## 2. 使用增益（质量/效率/可验证）

- [ ] 每条记录经独立字段映射归一，而非拼接原始响应（见 `references/field-mapping.md`）
- [ ] `duplicates_merged = total_input_records - merged_record_count`，数字与实际一致
- [ ] `evidence_records` 每条给 `source_file + locator + quote`，可逐条复核
- [ ] 覆盖缺口写入 `limitations`/`empty_results`，不静默吞掉

## 3. 创新增益（新增能力/方法改进/证据）

- [ ] 跨库字段映射表可复用（`references/field-mapping.md`）
- [ ] 确定性去重 + 优先级冲突消解 + 来源保留（`core_impl.py` 可独立复现）
- [ ] 覆盖率估计与失败降级（`coverage` + `degradations`）

## 4. 可靠体验（调用/稳定/异常）

- [ ] `python3 -B scripts/main.py --help` 退出码 0
- [ ] 缺省参数指向 `/input`、`/app/output`、`/app/work`
- [ ] 前 3 分钟先落盘 partial 骨架，之后只做增量改进（`change_log[0].step == "skeleton"`）
- [ ] 同输入重复运行内容确定（`selftest` 的 determinism 项 PASS）
- [ ] 空输入、坏 JSON、坏编码、缺 `case.json`、超大文件、缺 `pdftotext` 均不崩溃

## 5. 生态贡献（文档/复用/推广）

- [ ] `SKILL.md` 写清用途、调用方式、输入输出、异常处理、时间预算
- [ ] 字段映射表、去重/合并规则可复用
- [ ] 最小调用示例与示意输出存在且标明"示例"

## 降级策略（缺数据/缺软件时如实标注，禁止编造）

| 情形 | 处置 | 结果状态 |
|---|---|---|
| 无 `/input/case.json` | `errors:["missing: case.json"]`，仍写合法骨架并继续解析材料 | `input_error` |
| `case.json` 坏 JSON/坏编码 | 捕获记入 `errors`，继续解析其他材料 | `input_error`（有其他材料时仍产出候选） |
| 输入目录不存在/空 | 写合法骨架，`limitations` 说明 | `insufficient_evidence` |
| 某库响应缺失 | 该库不进 `sources`/`coverage`，`limitations` 注明 | `partial` |
| 响应无文献记录（含 esearch） | `sources` 标 `record_count:0`，`empty_results` 说明 | `partial`/`insufficient_evidence` |
| 字段缺失 | 填 `null`/`[]`，记 `missing_metadata`，`fields_missing` 列出 | `partial` |
| 标识符/年份冲突 | 记 `conflicts`，优先级消解，可人工仲裁 | `partial` |
| 文件 >2MB | 跳过并记 `degradations` | `partial` |
| 坏 JSON/坏编码文件 | 跳过该文件并记 `degradations`，继续其余 | `partial` |
| 缺 `pdftotext` | PDF 仅列路径，`degradations` 注明未抽文本 | `partial` |
| 无任何候选 | `status=insufficient_evidence`，列出缺口，不补造 | `insufficient_evidence` |

**禁止事项**：不虚构 DOI/PMID/引文/年份/命中数/评测分数；不把未授权数据汇总到未批准服务；
不绕过限流；不以示例填充充当正式结果。
