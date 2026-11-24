# 交付前检查清单与降级策略

按评分维度组织的逐条勾选清单；交付前对 `/app/output/` 逐项核对，并跑
`scripts/selftest.py`。

## 1. 实际效果（结果可复核）

- [ ] `result.json` 是合法 JSON，含 `case_id`，`status` ∈ {ok, partial,
  insufficient_evidence, input_error}
- [ ] output_contract 每个字段都存在（缺值填 `null`/`[]`，不改名、不删键）
- [ ] 每条 `candidates[i]` 都有 `record_id`、`provenance`、`field_sources`、
  `member_records[].raw_snapshot`
- [ ] `raw_snapshot` 是原始响应节点的**逐字**再序列化，不是改写/转述
- [ ] 去重可解释：`dedup_reason` ∈ {doi_exact, pmid_exact, pmcid_exact,
  title_firstauthor_year, title_fuzzy_0.90}
- [ ] 合并后关键字段来自各库真实语义（见 `references/field-mapping.md`）
- [ ] `coverage` 标出每库 `fields_missing` 与 `records_in_single_db_only`

## 2. 使用增益（质量/效率/可验证）

- [ ] 同一条查询没有机械复制到所有库；每个库有独立字段映射与优先级
- [ ] 重复记录被可靠归并，`duplicates_merged` 数字与实际一致
- [ ] `evidence_records` 给出 source_file、`locator` 与逐字 `quote`
- [ ] 覆盖缺口被显式列出，而不是静默返回不完整清单

## 3. 创新增益（新增能力/方法改进/证据）

- [ ] 跨库字段映射是数据驱动的（`field-mapping.md` + `FIELD_PRIORITY`）
- [ ] 确定性去重 + 冲突全量保留 + 来源保留（`core.py` 可独立复现）
- [ ] 覆盖率估计与失败降级（`coverage` + `degradations`）
- [ ] **时间预算状态机**：`runtime.phases` 记录每阶段预算与耗时，P0 前 3
  分钟先落盘 partial 骨架（本版强化方向）

## 4. 可靠体验（调用/稳定/异常）

- [ ] `python3 -B scripts/main.py --help` 退出码 0
- [ ] `python3 -B scripts/main.py --input /input --output /app/output
  --work /app/work` 可重复运行且内容确定（去时间戳后逐字一致）
- [ ] `python3 -B scripts/selftest.py --input /input --output /app/output`
  全 PASS
- [ ] 空输入、坏 JSON、坏编码、缺 `case.json`、文件 >2MB、缺 `pdftotext`
  均不崩溃（见下表）
- [ ] 某库不可用/限流/字段变更/被撤回：记录 `degradations`/`limitations`

## 5. 生态贡献（文档/复用/推广）

- [ ] `SKILL.md` 写清用途、调用方式、输入输出、异常处理、时间预算
- [ ] 字段映射表、去重/合并规则可被他人复用（`references/` 两文档）
- [ ] 最小调用示例与示意输出存在且标明「示例」

## 降级策略（缺数据/缺软件时如实标注，禁止编造）

| 情形 | 处置 | 结果状态 |
|---|---|---|
| 无 `/input/case.json` | `errors:["missing: .../case.json"]`，仍写合法骨架 | `input_error` |
| `case.json` 坏 JSON/坏编码 | 捕获记 `errors`，继续解析其他材料 | `partial` |
| 某库响应缺失/不可用 | 该库不进 `sources`/`coverage`，`limitations` 注明 | `partial` |
| 响应无文献记录 | `sources` 标 `record_count:0`，`empty_results` 说明 | `partial`/`insufficient_evidence` |
| 字段缺失 | 填 `null`/`[]`，记 `missing_metadata`，`fields_missing` 列出 | `partial` |
| 标识符/年份冲突 | 记 `conflicts`（全部取值+来源），保留主值消解说明 | `partial` |
| 文件 >2MB | 跳过并记 `degradations` | `partial` |
| 缺 `pdftotext` | 论文 PDF 仅列路径，`degradations` 注明未抽文本 | `partial` |
| 无任何候选 | `status=insufficient_evidence`，列缺口，不补造文献 | `insufficient_evidence` |

**禁止事项**：不虚构 DOI/PMID/引文/年份/命中数/评测分数；不把未授权数据
汇总到未批准服务；不绕过限流；不以示例填充充当正式结果；不编造缺失的
软件状态（如谎称已抽 PDF 文本）。

## 时间预算（本版强化方向）

总预算 900 秒、64 次模型请求、每响应 ≤16384 tokens。阶段拆分为
`core.py` 的 `PHASES`：

| 阶段 | 预算 | 模型请求 | 动作 |
|---|---|---|---|
| P0 落盘骨架 | ≤180s | ≤2 | 读输入后立即写合法 partial 骨架 |
| P1 解析融合 | ≤420s | ≤16 | 归一、去重、合并、覆盖、证据 |
| P2 相关性排序 | ≤180s | ≤32 | 写 `decisions.json` 合并仲裁 |
| P3 自检收尾 | ≤120s | ≤14 | 自检逐项勾选，修 FAIL 后重跑 |

硬约束：**前 3 分钟内必须先落盘一版 `result.json`（`status=partial`）**，
之后只做增量改进。`--skeleton-only` 可先落骨架再思考；`--resume` 复用
`/app/work/pipeline_state.json` 跳过已完成的解析融合。
