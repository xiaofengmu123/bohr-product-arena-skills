# 交付前检查清单与降级矩阵

按评分维度组织的逐条勾选清单；交付前对 `/app/output/` 逐项核对。自检脚本 `scripts/selftest.py` 覆盖其中可自动化的项。

## 1. 实际效果（结果可复核）

- [ ] `result.json` 合法，含 `case_id`，`status` ∈ {ok, partial, insufficient_evidence, input_error}
- [ ] 输出契约（默认契约 + case.json `output_contract` 全部键）不删键、不改名、缺值填 `null`/`[]`
- [ ] 每条 `candidates[i]` 有 `record_id`、`provenance`、`field_sources`、`raw_snapshots`
- [ ] `raw_snapshots[].snapshot` 是原始响应逐字片段，不是改写
- [ ] 去重可解释：标识符精确 > 标题+年份 > 标题+一作+年份 > 标题模糊（≥0.90 同年）
- [ ] 字段值按各库真实语义填充（见 `references/field-mapping.md`）
- [ ] `coverage` 列出每库 `fields_missing_per_database` 与 `records_in_single_db_only`

## 2. 使用增益（质量/效率/可验证）

- [ ] 每个库有独立字段映射与优先级，不是同一查询机械复制
- [ ] 重复记录可靠归并，`duplicates_merged` 与实际一致
- [ ] 每条字段级证据给出 source_file + locator + 逐字 quote
- [ ] 覆盖缺口显式列出，不静默返回不完整清单

## 3. 创新增益（新增能力/方法改进/证据）

- [ ] 字段级溯源 + 双重置信度（记录级 identity_confidence、字段级 field_confidence）
- [ ] 确定性去重 + 同 tier 冲突消解 + 来源保留（`core_impl.py` 独立可复现）
- [ ] 覆盖率估计 + 失败降级 + 显式不确定性（`coverage` + `degradations` + `uncertainty`）

## 4. 可靠体验（调用/稳定/异常）

- [ ] `python3 -B scripts/main.py --help` 退出码 0
- [ ] `python3 -B scripts/main.py --input /input --output /app/output --work /app/work` 可重复且内容确定
- [ ] `python3 -B scripts/selftest.py --input /input --output /app/output` 全 PASS
- [ ] 空输入、坏 JSON、坏编码、缺 case.json、文件 >2MB、缺 pdftotext 均不崩溃
- [ ] 某库不可用/限流/字段变更/被撤回：记入 `degradations`/`limitations`，不编造

## 5. 生态贡献（文档/复用/推广）

- [ ] `SKILL.md` 写清用途、调用、输入输出、异常、时间预算
- [ ] `references/field-mapping.md`（映射/优先级/置信度）可复用
- [ ] `references/checklist.md`（清单/降级矩阵）可复用
- [ ] 最小调用示例与示意输出存在且标明"示例"

## 降级矩阵（缺数据/缺软件时如实标注，禁止编造）

| 情形 | 处置 | 结果 status |
|-|-|-|
| 无 `/input/case.json` | `errors` 记录 `missing:.../case.json`，仍写合法骨架 | `input_error` |
| `case.json` 坏 JSON/坏编码 | 记 `errors`，继续解析其他材料 | `partial` 或 `input_error` |
| 某库响应缺失/不可用 | 不进 `sources`/`coverage`，`limitations` 注明未覆盖 | `partial` |
| 响应无文献记录 | `sources` 标 `record_count:0`，`empty_results` 说明 | `partial`/`insufficient_evidence` |
| 字段缺失 | 填 `null`/`[]`，记 `missing_metadata`，列 `fields_missing` | `partial` |
| 同 tier 标识符/年份冲突 | 记 `conflicts`，保留消解说明，可人工仲裁 | `partial` |
| 文件 >2MB | 跳过并记 `degradations` | `partial` |
| 缺 `pdftotext` | PDF 仅列路径，`degradations` 注明未抽文本 | `partial` |
| 标题模糊归并（置信度低） | `identity_confidence=low`，记 `uncertainty` 与 `limitations` | `partial` |
| 无任何候选 | `status=insufficient_evidence`，列出缺口，不补造文献 | `insufficient_evidence` |

**禁止事项**：不虚构 DOI/PMID/引文/年份/命中数/评测分数；不把未授权数据汇总到未批准服务；不绕过限流；不以示例填充充当正式结果。
