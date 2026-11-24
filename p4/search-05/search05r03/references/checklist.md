# 交付前检查清单与降级策略

按评分维度逐条勾选；交付前对 `/app/output/` 与 `/app/work/` 逐项核对。可用 `python3 -B scripts/selftest.py --input /input --output /app/output` 自动执行 1–6 项。

## 1. 实际效果（结果可复核）

- [ ] `result.json` 是合法 JSON，含 `case_id`，`status` ∈ {ok, partial, insufficient_evidence, input_error}
- [ ] 输出契约每个字段都存在；`case.json` 声明的 `output_contract` 键全部原样回填（缺值 `null`/`[]`，不改名不删键）
- [ ] 每条 `candidates[i]` 都有 `record_id` 与 `provenance`（库—查询—原始响应—映射—去重—合并可回溯）
- [ ] `provenance.raw_snapshots` 是原始响应里的**逐字片段**，不是改写/转述
- [ ] 去重判定可解释：DOI 精确 > PMID 精确 > 标题+一作+年份 > 标题模糊（≥0.90 且同年）
- [ ] 合并字段按 `field-mapping.md` 的优先级填充，`field_sources` 齐全；冲突写入 `conflicts`
- [ ] `coverage` 列出每库 `fields_missing` 与 `records_in_single_db_only`，`duplicates_merged` 与 `total-merged` 自洽

## 2. 使用增益（质量/效率/可验证）

- [ ] 查询没有机械复制到所有库：每个库独立字段映射与规范化（见 `field-mapping.md`）
- [ ] 重复记录被可靠归并，`duplicates_merged` 数字与实际一致
- [ ] `evidence_records` 给出查询式、来源文件、`locator` 与逐字 `quote`，他人可按条复核
- [ ] 覆盖缺口显式列出（`records_in_single_db_only`、`systematically_missing_fields`），不静默返回不完整清单

## 3. 创新增益（新增能力/方法改进/证据）

- [ ] 具备跨库字段映射（`references/field-mapping.md`），不拼接原始响应
- [ ] 具备确定性去重 + 冲突消解 + 来源保留（`fedcore.py` 可独立复现）
- [ ] 具备覆盖率估计与失败降级（`coverage` + `degradations` + `limitations`）

## 4. 可靠体验（调用/稳定/异常）

- [ ] `python3 -B scripts/main.py --help` 退出码 0
- [ ] `python3 -B scripts/main.py --input /input --output /app/output --work /app/work` 可重复运行且内容确定
- [ ] `python3 -B scripts/selftest.py --input /input --output /app/output` 全 PASS
- [ ] 空输入、坏 JSON、坏编码、缺 `case.json`、文件 >2MB、缺 `pdftotext` 均不崩溃（见降级表）
- [ ] 某库不可用/限流/字段变更：记录到 `degradations`/`limitations`，不编造
- [ ] 两次运行 `runtime.content_fingerprint` 相同（确定性锚点）

## 5. 生态贡献（文档/复用/推广）

- [ ] `SKILL.md` 写清用途、调用方式、输入/输出、异常处理、时间预算
- [ ] 字段映射、去重/合并规则可复用（`references/field-mapping.md`）
- [ ] 最小调用示例与示意输出存在且标明"示例"

## 降级策略（缺数据/缺软件时如实标注，禁止编造）

| 情形 | 处置 | 结果状态 |
|---|---|---|
| 无 `/input/case.json` | 记 `errors:["missing: .../case.json"]`，仍写合法骨架 | `input_error` |
| `case.json` 坏 JSON/坏编码 | 记 `errors`/`degradations`，继续解析其他材料 | `input_error` |
| 某库响应缺失/不可用 | 该库不进 `sources`/`coverage`，`limitations` 注明未覆盖 | `partial` |
| 响应无文献记录 | `sources` 标 `record_count:0`，`empty_results` 说明 | `partial`/`insufficient_evidence` |
| 字段缺失 | 填 `null`/`[]`，记 `missing_metadata`，`fields_missing` 列出 | `partial` |
| 标识符/年份冲突 | 记 `conflicts`，保留优先级消解说明，可人工仲裁 | `partial` |
| 文件 >2MB | 跳过并记 `degradations` | `partial` |
| 缺 `pdftotext` | PDF 仅列路径，`degradations`/`limitations` 注明未抽文本 | `partial` |
| 无任何候选 | `status=insufficient_evidence`，列出缺口，不补造文献 | `insufficient_evidence` |

**禁止事项**：不虚构 DOI/PMID/引文/年份/命中数/评测分数；不把未授权数据汇总到未批准服务；不绕过限流；不以示例填充充当正式结果。