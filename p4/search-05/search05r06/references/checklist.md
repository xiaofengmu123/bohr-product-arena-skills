# 交付前检查清单与降级策略

按评分维度组织的逐条勾选清单；交付前对 `/app/output/` 逐项核对，并运行：

```bash
python3 -B scripts/selftest.py --input /input --output /app/output
```

## 1. 实际效果（结果可复核）

- [ ] `result.json` 是合法 JSON，含 `case_id`，`status` ∈ {ok, partial, insufficient_evidence, input_error}
- [ ] 题目 `output_contract` 每个字段都回填（缺值 `null`/`[]`，不改名、不删键）
- [ ] 每条 `candidates[i]` 有 `record_id` 与 `provenance`（库—查询—原始响应—映射—去重—合并可回溯）
- [ ] `provenance[].raw_snapshot` 是原始响应里的**逐字片段**，不是改写
- [ ] 去重可解释：DOI 精确 > 标题+一作+年份 > 标题模糊（≥0.90 且同年），`dedup_basis` 写明
- [ ] 合并字段按 `references/field-mapping.md` 的语义映射填充，冲突写 `conflicts`
- [ ] `coverage` 标出每库 `fields_missing` 与 `records_in_single_db_only`

## 2. 使用增益（质量/效率/可验证）

- [ ] 不是把同一条查询机械复制到所有库；每库有独立字段映射与优先级
- [ ] 重复记录可靠归并，`duplicates_merged` 与实际簇数一致
- [ ] `evidence_records` 给出查询式、来源文件、`locator` 与逐字 `quote`
- [ ] 覆盖缺口显式列出，不静默返回不完整清单

## 3. 创新增益（中间表示与可复核）

- [ ] `plan.json`（执行计划快照：输入清单、库分类、规则选择、覆盖目标）存在且可解析
- [ ] `record_model.json`（归一化记录、逐字段来源、去重簇、合并决策、冲突）存在且可解析
- [ ] `result.json` 由 `record_model.json` 派生，任一字段可回指到原始响应
- [ ] 具备覆盖率估计与失败降级（`coverage` + `degradations`），不静默

## 4. 可靠体验（调用/稳定/异常）

- [ ] `python3 -B scripts/main.py --help` 退出码 0
- [ ] `python3 -B scripts/main.py --input /input --output /app/output --work /app/work` 可重复运行、内容确定（`runtime` 时间戳除外）
- [ ] `python3 -B scripts/selftest.py --input /input --output /app/output` 全 PASS
- [ ] 空输入、坏 JSON、坏编码、缺 `case.json`、文件 >2MB、缺 `pdftotext` 均不崩溃

## 5. 生态贡献（文档/复用/推广）

- [ ] `SKILL.md` 写清用途、调用方式、输入输出、异常处理、时间预算
- [ ] `references/field-mapping.md` 字段映射与去重/合并规则可被他人复用
- [ ] 最小调用示例与示意输出存在且标明“示例”

## 降级策略（缺数据/缺软件如实标注，禁止编造）

| 情形 | 处置 | 结果状态 |
|---|---|---|
| 无 `/input/case.json` | 记 `errors:["missing: .../case.json"]`，仍写合法骨架 | `input_error` |
| `case.json` 坏 JSON/坏编码 | 捕获并记入 `errors`，继续解析其它材料 | `partial` 或 `input_error` |
| 某库响应缺失/不可用 | 该库不进 `sources`/`coverage`，`limitations` 注明未覆盖 | `partial` |
| 响应无文献记录 | `sources` 标 `record_count:0`，`empty_results` 说明 | `partial`/`insufficient_evidence` |
| 字段缺失 | 填 `null`/`[]`，记入 `missing_metadata`，`fields_missing` 列出 | `partial` |
| 标识符/年份冲突 | 记入 `conflicts`，保留优先级消解，可人工仲裁 | `partial` |
| 文件 >2MB | 跳过并记 `degradations` | `partial` |
| 缺 `pdftotext` | PDF 仅列路径，`degradations` 注明未抽文本 | `partial` |
| 无任何候选 | `status=insufficient_evidence`，列出缺口，不补造文献 | `insufficient_evidence` |

**禁止事项**：不虚构 DOI/PMID/引文/年份/命中数/评测分数；不把未授权数据汇总到未批准服务；不绕过限流；不以示例填充充当正式结果。
