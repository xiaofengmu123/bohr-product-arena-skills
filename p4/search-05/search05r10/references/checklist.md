# 交付前检查清单与降级策略

按评分维度组织的逐条勾选清单。交付前对 `/app/output/` 逐项核对，或直接运行：

```bash
python3 -B scripts/selftest.py --input /input --output /app/output
```

## 1. 实际效果（结果可复核）

- [ ] `result.json` 是合法 JSON，含 `case_id`，`status` ∈ {ok, partial, insufficient_evidence, input_error}
- [ ] 题目 `output_contract` 的每个字段都存在（缺值填 `null`/`[]`，不改名、不删键）
- [ ] 每条 `candidates[i]` 都有 `record_id`、`provenance` 与 `field_sources`
- [ ] `raw_snapshot` 是原始响应里的逐字 JSON 片段，不是改写/转述
- [ ] 去重判定可解释：标识符精确 > 标题精确 > 标题+一作+年份
- [ ] 合并字段来自各库真实语义字段，`field_sources` 标注来源库与 locator
- [ ] `coverage` 标出每库 `fields_missing_per_db` 与 `records_in_single_db_only`

## 2. 使用增益（质量 / 效率 / 可验证）

- [ ] 同一条查询没有机械复制到所有库；每库有独立字段映射与优先级
- [ ] 重复记录被可靠归并，`duplicates_merged` 数字与实际簇一致
- [ ] `evidence_records` 给出来源文件、`locator` 与逐字 `quote`，他人可按记录复核
- [ ] 覆盖缺口被显式列出，而不是静默返回不完整清单

## 3. 创新增益（新增能力 / 方法改进 / 证据）

- [ ] 具备跨库字段映射（`references/field-mapping.md`），不是拼接原始响应
- [ ] 具备确定性去重 + 冲突消解 + 来源保留（`scripts/core_impl.py` 可独立复现）
- [ ] 具备覆盖率估计与失败降级（`coverage` + `degradations`）

## 4. 可靠体验（调用 / 稳定 / 异常）

- [ ] `python3 -B scripts/main.py --help` 退出码 0
- [ ] `python3 -B scripts/main.py --input /input --output /app/output --work /app/work` 可重复运行且内容确定
- [ ] `python3 -B scripts/selftest.py --input /input --output /app/output` 全 PASS
- [ ] 空输入、坏 JSON、坏编码、缺 `case.json`、文件 >2MB、缺 `pdftotext` 均不崩溃
- [ ] 某库不可用 / 限流 / 字段变更 / 被撤回：记录到 `degradations`/`limitations`，不编造

## 5. 生态贡献（文档 / 复用 / 推广）

- [ ] `SKILL.md` 写清用途、调用方式、输入输出、异常处理、时间预算
- [ ] 字段映射表、去重/合并规则可被他人复用（`references/field-mapping.md`）
- [ ] 最小调用示例与示意输出存在且标明「示例」

## 降级策略（缺数据 / 缺软件时如实标注，禁止编造）

| 情形 | 处置 | 结果状态 |
|---|---|---|
| 无 `/input/case.json` | 记录 `errors`，仍写合法骨架 | `input_error` |
| `case.json` 坏 JSON / 坏编码 | 捕获并记入 `errors`，继续解析其他材料 | `partial` / `insufficient_evidence` |
| 某库响应缺失 / 不可用 | 该库不进 `sources`/`coverage`，`limitations` 注明未覆盖 | `partial` |
| 响应无文献记录 | `sources` 标 `record_count: 0`，`empty_results` 说明 | `partial` / `insufficient_evidence` |
| 字段缺失 | 填 `null`/`[]`，记入 `missing_metadata` 与 `fields_missing_per_db` | `partial` |
| 标识符 / 年份冲突 | 记入 `conflicts`，保留优先级消解说明，可人工仲裁 | `partial` |
| 文件 >2MB | 跳过并记 `degradations` | `partial` |
| 缺 `pdftotext` | 论文 PDF 仅列路径，`degradations` 注明未抽文本 | `partial` |
| 无任何候选 | `status=insufficient_evidence`，列出缺口，不补造文献 | `insufficient_evidence` |

**禁止事项**：不虚构 DOI/PMID/引文/年份/命中数/评测分数；不把未授权数据汇总到未批准服务；
不绕过限流；不以示例填充充当正式结果。
