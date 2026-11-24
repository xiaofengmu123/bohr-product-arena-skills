# 自检清单（提交/交付前逐条勾选，对应评分维度）

## 实际效果（40 分）

- [ ] 已产出真实清单/目录树/重复组/版本候选/恢复报告/索引，而非仅给建议。
- [ ] `library_manifest.csv` 每行一个源文件，哈希与源文件一致，无静默跳过。
- [ ] 覆盖六类要素：文件类型、元数据字段、目录层级、重复文件、版本差异、权限与来源。
- [ ] 超大/非常规/符号链接/不可读/坏编码文件逐条进入 `errors`/`degradations`，不静默。
- [ ] 去重按内容 SHA-256，版本按归一化主干 + 内容哈希 + mtime（并标注启发式）。
- [ ] `result.json` 通过 `python3 -B scripts/main.py --check result.json --case-json case.json`。

## 输出契约完整（本版重点，对应结构分满分档）

- [ ] `result.json` 含 `case_id` + `output_contract` 全部字段 + `status/sources/uncertainty/change_log`。
- [ ] 契约字段逐一对齐：`_contract_meta.total_fields == contract_alignment.json` 中字段数。
- [ ] 与保留键冲突的契约字段改存 `computed._contract_<字段>`，且 `_contract_meta.moved` 计数正确。
- [ ] `change_log` 覆盖骨架→盘点→对齐→语义→定稿各阶段，`fields_affected` 非空。
- [ ] `operation_log.jsonl` 与 `change_log` 一致，可重放。

## 使用增益（25 分）

- [ ] 去重/版本/对账由脚本确定层完成，Agent 只做语义层，减少人工整理条目。
- [ ] 库目录（JSON/CSV/BibTeX/RIS/SQLite）自动解析并恢复对账。
- [ ] 机械问答（计数/重复/缺失/断链/版本/作者/年份）自动给出证据定位。

## 创新增益（15 分）

- [ ] 别名识别（符号链接含断链 + 硬链接组）为新增能力。
- [ ] 行政版本说明解析并与候选链对齐（来源 + locator + 逐字 quote）。
- [ ] 恢复对账区分 matches/mismatches/missing/orphaned，支持重建索引。
- [ ] 契约对齐报告（`contract_alignment.json` + `_contract_meta`）为可机查的独立增量。

## 可靠体验（10 分）

- [ ] 空输入目录 → 退出码 0，`status=insufficient_evidence`，不抛异常。
- [ ] 坏 JSON/坏编码 → 记录进 `errors`/`degradations`，继续处理其余材料。
- [ ] 缺 case.json → `status=input_error`；缺 output_contract → 仅元数据字段 + `uncertainty`。
- [ ] 写盘失败 → 保留骨架，退出码 2，不崩溃；重启后可用 `--update` 续跑。
- [ ] 重复运行 → 除时间戳外结果一致（`selftest.py` 已覆盖）。

## 生态贡献（10 分）

- [ ] `SKILL.md` frontmatter 含 name/description/version；脚本调用用相对路径 `scripts/xxx.py`。
- [ ] `references/fields-and-rules.md` 与 `references/checklist.md` 齐全。
- [ ] 最小调用示例标明“示例”，未冒充正式案例结果。
- [ ] 只依赖 Python 标准库，无第三方依赖、无个人文件路径。
