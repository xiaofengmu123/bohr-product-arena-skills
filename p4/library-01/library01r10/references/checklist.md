# 自检清单（对应评分维度，提交/交付前逐条勾选）

本清单与 `SKILL.md` 第 6 节一致，逐项对应题目五个评分维度。勾选前应实际运行
`python3 -B scripts/selftest.py`（六场景全绿）与
`python3 -B scripts/main.py --check /app/output/result.json --case-json /input/case.json`。

## 实际效果（40 分）

- [ ] 已产出真实清单/目录树/重复组/版本候选/恢复报告，而非仅建议。
- [ ] 落库文件与源文件一一对应：`library_manifest.csv` 每行一个源条目，哈希与源文件一致。
- [ ] 覆盖六类要素：文件类型、元数据字段、目录层级、重复文件、版本差异、权限与来源。
- [ ] 难处理文件（超大/非常规/符号链接/无权限/坏编码）未被静默跳过，逐条记录进 `errors`/`degradations`。
- [ ] 重复按内容 SHA-256 判定，版本按归一化主干 + 内容差异判定，不按文件名。
- [ ] `result.json` 通过 `--check`（case_id 非空、status 合法、contract 字段齐全）。

## 使用增益（25 分）

- [ ] 去重/版本/对账由脚本确定层完成（`crawl`→`analyze`），Agent 只做语义层，减少人工整理条目。
- [ ] 库目录（JSON/CSV/BibTeX/RIS/SQLite）自动解析并恢复对账，减少人工比对。
- [ ] 机械问答（计数/重复/缺失/断链/版本/作者/年份）自动给出证据定位（`computed.answers`）。
- [ ] `--update` 增量合并支持语义层逐步补全，不重跑整个流程。

## 创新增益（15 分）

- [ ] 别名识别（符号链接含断链 + 硬链接组，按 inode/nlink）为新增能力。
- [ ] 行政版本说明解析（`task.md` 行级 locator）并与候选链对齐。
- [ ] 恢复对账区分 matches/mismatches/missing/orphaned，支持重建索引。
- [ ] 三层流水线 + 单一 IR：坏阶段不写半成品，产物是 IR 的纯投影。

## 可靠体验（10 分）

- [ ] 空输入目录 → 退出码 0，`status=insufficient_evidence`，不抛异常。
- [ ] 坏 JSON/坏编码 → 记录进 errors/degradations，继续处理其余材料。
- [ ] 缺 case.json → `status=input_error`；缺 output_contract → 仅元数据字段 + uncertainty。
- [ ] 写盘失败 → 保留骨架，不崩溃。
- [ ] 重复运行 → 遍历排序、无随机数，除时间戳外结果一致（`selftest.py` 验证确定性）。

## 生态贡献（10 分）

- [ ] `SKILL.md` frontmatter 含 name/description/version，调用路径为相对路径 `scripts/main.py`。
- [ ] `references/fields-and-rules.md` 与 `references/checklist.md` 齐全。
- [ ] 最小调用示例已标明"示例"，未冒充正式案例结果。
- [ ] 只依赖 Python 标准库，无第三方依赖、无个人文件路径。
- [ ] `main.py` < 30KB（实现拆入 `core_impl.py`），`selftest.py` 可独立运行。
