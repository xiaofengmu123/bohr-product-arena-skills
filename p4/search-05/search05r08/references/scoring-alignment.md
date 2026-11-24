# 评分维度对齐表

本包的核心设计取舍：把题目五个评分维度（40/25/15/10/10）逐条映射到
**流水线步骤**与 **可机检自检项**，而非只写一段自述文字。`result.json`
中的 `scorecard` 与 `scripts/selftest.py` 的每一项检查都对应下表。

## 实际效果（40 分）

| 评分点 | 流水线步骤 | 自检项 |
|---|---|---|
| 任务完成度 15 | 阶段1：按库识别结构、抽取记录、字段归一 | `actual_orchestration`、`actual_mapping` |
| 结果正确性 15 | 去重聚类 + 合并优先级 + 冲突消解 | `actual_dedup_merge`、`actual_identity`、`cand_field_sources` |
| 结果完整性 5 | 覆盖比较：库集合、缺失字段、单库独有 | `actual_completeness`、`coverage_present` |
| 结果可核验性 5 | provenance + raw_snapshot + locator + quote | `actual_traceability`、`evidence_locator_quote` |

## 使用增益（25 分）

| 评分点 | 流水线步骤 | 自检项 |
|---|---|---|
| 质量增益 15 | 跨库合并提升字段完整度、去重减少重复 | `gain_quality` |
| 效率增益 5 | 一次运行输出唯一记录集，免人工比对 | `gain_efficiency` |
| 增益可验证性 5 | 输出可打开的合并记录与证据映射 | `gain_verifiable`、`aux_artifacts` |

## 创新增益（15 分）

| 评分点 | 流水线步骤 | 自检项 |
|---|---|---|
| 新增能力 5 | 跨库字段映射（`FIELD_PRIORITY`） | `inno_mapping`、`eco_reusable` |
| 方法改进 5 | 确定性去重 + 冲突消解优先级 | `inno_conflict` |
| 创新证据 5 | 覆盖率估计 + 失败降级记录 | `inno_degradation` |

## 可靠体验（10 分）

| 评分点 | 流水线步骤 | 自检项 |
|---|---|---|
| 调用与易用性 3 | 缺省参数指向 /input、/app/output、/app/work | `help_exit_ok` |
| 运行稳定性 4 | 无随机、无网络、确定性输出 | `deterministic` |
| 异常处理 3 | 空输入/坏 JSON/缺字段/缺 pdftotext 均降级 | `rel_abnormal`、`no_fabrication` |

## 生态贡献（10 分）

| 评分点 | 流水线步骤 | 自检项 |
|---|---|---|
| 文档完整性 3 | SKILL.md 写清用途/契约/预算/自检 | `eco_docs` |
| 资源可复用性 4 | field-mapping.md + fedsearch.py 独立可复用 | `eco_reusable` |
| 分享推广价值 3 | 最小调用示例且标明示例 | `eco_example` |

## 自检命令

```bash
python3 -B scripts/main.py --help
python3 -B scripts/main.py --input /input --output /app/output --work /app/work
python3 -B scripts/selftest.py --input /input --output /app/output
```

`selftest.py` 退出码 0 表示结构自检与评分维度机检全部通过；任一 FAIL 退出码 1。
