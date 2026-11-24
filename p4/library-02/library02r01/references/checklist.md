# 自检清单（对应评分维度，提交/交付前逐条勾选）

## 实际效果（40 分）

- [ ] 已产出真实清单/目录树/重复组/版本候选/别名/恢复报告/索引/evidence_index，而非仅建议。
- [ ] `library_manifest.csv` 每行一个源文件，哈希与源文件内容一致，无静默跳过。
- [ ] 覆盖六类要素：文件类型、元数据字段、目录层级、重复文件、版本差异、权限与来源。
- [ ] 难处理文件（超大/非常规/符号链接/无权限/坏编码/PDF 无文本层）逐条记录进 errors/degradations。
- [ ] 重复按内容 SHA-256 判定，版本按归一化主干 + 内容差异判定，不按文件名。
- [ ] `python3 -B scripts/main.py --check /app/output/result.json --case-json /input/case.json` 通过。

## 使用增益（25 分）

- [ ] 去重/版本/别名/对账由脚本确定层完成，Agent 只做语义层，减少人工整理条目。
- [ ] 库目录（JSON/CSV/Markdown/BibTeX/RIS/SQLite）自动解析并恢复对账，减少人工比对。
- [ ] 机械问答（计数/重复/缺失/断链/版本/哈希/未登记）自动给出证据定位。
- [ ] `--search` 可对文本索引做词元+中文 bigram 检索，返回 locator+quote，减少翻文件。

## 创新增益（15 分）

- [ ] 别名识别（符号链接含断链 + 硬链接组）为新增能力。
- [ ] 行政版本说明解析并与候选链对齐。
- [ ] 恢复对账区分 matches/mismatches/missing/orphaned，支持重建索引。
- [ ] **证据溯源强化**：`evidence_map` 每条结论绑定 locator + 逐字 quote + `quote_span`
      + `source_sha256`；`evidence_index.json` 支持 quote→源定位反查。

## 可靠体验（10 分）

- [ ] 空输入目录 → 退出码 0，`status=insufficient_evidence` 或 `partial`，不抛异常。
- [ ] 坏 JSON/坏编码 → 记录进 errors/degradations，继续处理其余材料。
- [ ] 缺 case.json → `status=input_error`；缺 output_contract → 仅元数据字段 + uncertainty。
- [ ] 写盘失败 → 保留骨架，退出码 2，不崩溃。
- [ ] 重复运行 → 目录遍历排序、无随机数，除时间戳外结果一致。

## 生态贡献（10 分）

- [ ] `SKILL.md` frontmatter 含 name/description/version，脚本调用用相对路径
      `scripts/main.py`。
- [ ] `references/fields-and-rules.md` 与 `references/checklist.md` 齐全。
- [ ] 最小调用示例已标明"示例"，未冒充正式案例结果。
- [ ] 只依赖 Python 标准库，无第三方依赖、无个人文件路径。

## 证据溯源硬要求（本版重点，逐条核对）

- [ ] 每条关键结论都有 `evidence_map.conclusions[]` 条目。
- [ ] 每个 locator 都有 `quote`（逐字子串）、`quote_span[起,止]`、`source_file`、
      `source_sha256`；无文本层的难处理文件如实写 `pdf_no_text_layer`，不编造 quote。
- [ ] 冲突信息不隐藏：同指标两来源不一致时都记录并标 `source_conflict`。
- [ ] `quote` 可由他人用 `source_sha256` 定位文件、用 `quote_span` 重放原文。
