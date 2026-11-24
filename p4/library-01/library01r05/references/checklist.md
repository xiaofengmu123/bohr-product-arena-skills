# 自检清单（对应评分维度，提交前逐条勾选）

运行 `python3 -B scripts/selftest.py`（或 `python3 -B scripts/main.py --selftest`），
7 项全部 PASS 后再提交。

## 实际效果（40 分）

- [ ] 已产出真实清单/目录树/重复组/版本候选/恢复报告，而非仅建议。
- [ ] 落库文件与源文件一一对应：`library_manifest.csv` 每行一个源文件，哈希与源文件一致。
- [ ] 覆盖六类要素：文件类型、元数据字段、目录层级、重复文件、版本差异、权限与来源。
- [ ] 难处理文件（超大/非常规/符号链接/无权限/坏编码）未被静默跳过，逐条记录在 `degradations`。
- [ ] 重复按内容 SHA-256 判定，版本按归一化主干 + 内容差异判定，不按文件名。
- [ ] `result.json` 通过 `python3 -B scripts/main.py --check /app/output/result.json`。

## 使用增益（25 分）

- [ ] 去重/版本/对账由脚本确定层完成，Agent 只做语义层，减少人工整理条目。
- [ ] 库目录（JSON/CSV/BibTeX/RIS/SQLite）自动解析并恢复对账，减少人工比对。
- [ ] 机械问答（计数/重复/缺失/断链/版本/作者/年份）自动给出证据定位。

## 创新增益（15 分）

- [ ] 别名识别（符号链接含断链 + 硬链接组）为新增能力。
- [ ] 行政版本说明解析（task.md 中的版本行）并与候选链对齐。
- [ ] 恢复对账区分 matches/mismatches/missing/orphaned，支持重建索引。
- [ ] 契约覆盖引擎：`contract_coverage` 报告 total/filled/missing/null 与 field_map，逐字段对齐可机器校验。

## 可靠体验（10 分）

- [ ] 空输入目录 → 退出码 0，`status=insufficient_evidence`，不抛异常。
- [ ] 坏 JSON/坏编码 → 记录进 `errors`/`degradations`，继续处理其余材料。
- [ ] 缺 case.json → `status=input_error`；坏 case.json → `status=input_error`；缺 output_contract → 仅元数据字段 + `uncertainty`。
- [ ] 写盘失败 → 保留骨架，退出码 2，不崩溃。
- [ ] 重复运行 → 遍历排序、无随机数，除时间戳外结果一致（`computed.reproducibility` 一致）。

## 生态贡献（10 分）

- [ ] `SKILL.md` frontmatter 含 name/description/version，调用路径为相对路径 `scripts/xxx.py`。
- [ ] `references/contract-and-rules.md` 与 `references/checklist.md` 齐全。
- [ ] 最小调用示例已标明“示例”，未冒充正式案例结果。
- [ ] 只依赖 Python 标准库，无第三方依赖、无个人文件路径。
- [ ] 文件数 6 个、总体积 ≤ 1.2MB、`main.py` < 30KB（重逻辑在 `core_impl.py`）。
