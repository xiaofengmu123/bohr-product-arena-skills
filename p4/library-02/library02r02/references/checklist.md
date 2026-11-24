# 自检清单（对应评分维度，提交/交付前逐条勾选）

本清单用于人工与脚本双重检查。脚本侧由 `scripts/selftest.py` 自动化；勾选项对应
题目评分维度（实际效果 40 / 使用增益 25 / 创新增益 15 / 可靠体验 10 / 生态贡献 10）。

## 实际效果（40 分）

- [ ] 已产出真实清单、目录树、重复组、版本候选、别名、恢复报告、索引，而非仅建议。
- [ ] `library_manifest.csv` 每行一个源文件，哈希与源文件内容一致，无静默跳过。
- [ ] 覆盖六类要素：文件类型、元数据字段、目录层级、重复文件、版本差异、权限与来源。
- [ ] 难处理文件（超大/非常规/符号链接/无权限/坏编码）逐条记录进 errors/degradations。
- [ ] 重复按内容 SHA-256 判定，版本按归一化主干 + 内容差异判定，不按文件名。
- [ ] `python3 -B scripts/main.py --check <result.json> --case-json /input/case.json` 通过。
- [ ] result.json 中每个关键结论都有 `sources` 条目（locator + 逐字 quote）。

## 使用增益（25 分）

- [ ] 去重/版本/别名/对账由脚本机械层完成，Agent 只做语义层，减少人工整理。
- [ ] 库目录（JSON/CSV/Markdown/BibTeX/RIS/SQLite）自动解析并恢复对账（matches/mismatches/missing/orphaned）。
- [ ] 机械问答（计数/重复/缺失/断链/版本/哈希/未登记）自动给出证据定位。
- [ ] 前 3 分钟已落盘 partial 骨架，后续只做增量改进（`--update`），不丢失进度。

## 创新增益（15 分）

- [ ] 别名识别（符号链接含断链 + 硬链接组）为新增能力。
- [ ] 行政版本说明解析并与候选链对齐（linked/orphan）。
- [ ] 恢复对账区分 matches/mismatches/missing/orphaned，支持重建索引。
- [ ] 时间预算看板（budget.json + `--budget`）把 900s/64 请求拆成阶段预算并记录 checkpoint。

## 可靠体验（10 分）

- [ ] 空输入目录 → 退出码 0，`status=insufficient_evidence`，不抛异常。
- [ ] 坏 JSON/坏编码 → 记录进 errors/degradations，继续处理其余材料。
- [ ] 缺 case.json → `status=input_error`；缺 output_contract → 默认契约 + uncertainty 标注。
- [ ] 写盘失败 → 保留骨架，退出码 2，不崩溃。
- [ ] 重复运行 → 目录遍历排序、无随机数，除时间戳外结果一致。

## 生态贡献（10 分）

- [ ] `SKILL.md` frontmatter 含 name/description/version；脚本调用用相对路径 `scripts/main.py`。
- [ ] `references/fields-and-rules.md` 与 `references/checklist.md` 齐全。
- [ ] 最小调用示例已标明"示例"，未冒充正式案例结果。
- [ ] 只依赖 Python 标准库，无第三方依赖、无个人文件路径。
