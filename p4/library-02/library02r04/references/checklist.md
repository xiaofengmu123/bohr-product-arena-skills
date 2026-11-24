# 自检清单（提交/交付前逐条勾选）

## 实际效果（40 分）

- [ ] 已产出真实清单/目录树/重复组/版本候选/别名/恢复报告/索引，而非仅建议。
- [ ] `library_manifest.csv` 每行一个源文件，哈希与源文件内容一致，无静默跳过。
- [ ] 检索命中落到原文段落：给出文件相对路径、页码/章节、段落首句与逐字 quote。
- [ ] 过滤条件（年份/项目编号/类型/批次/版本/权限）作为硬约束执行，未满足时写 `filter_unsatisfied`。
- [ ] 覆盖六类要素：文件类型、元数据字段、目录层级、重复文件、版本差异、权限与来源。
- [ ] 难处理文件（超大/非常规/符号链接/无权限/坏编码/无文本层）逐条记录进 errors/degradations。
- [ ] 重复按内容 SHA-256 判定，版本按归一化主干+内容差异判定，不按文件名。
- [ ] `python3 -B scripts/main.py --check /app/output/result.json` 通过。

## 使用增益（25 分）

- [ ] 去重/版本/别名/对账由脚本确定性层完成，Agent 只做语义层，减少人工整理条目。
- [ ] 库目录（JSON/CSV/Markdown/BibTeX/RIS/SQLite）自动解析并恢复对账，减少人工比对。
- [ ] 机械问答（计数/重复/缺失/断链/版本/哈希/未登记）自动给出证据定位与候选命中。

## 创新增益（15 分）

- [ ] 别名识别（符号链接含断链 + 硬链接组）为新增能力。
- [ ] 行政版本说明解析并与候选链对齐（linked/orphan）。
- [ ] 恢复对账区分 matches/mismatches/missing/orphaned，支持重建索引。
- [ ] 检索—定位—过滤—回链四步闭环，且每条回链带 confidence 与逐字 quote。

## 可靠体验（10 分）

- [ ] 空输入目录 → 退出码 0，`status=insufficient_evidence`，不抛异常。
- [ ] 坏 JSON/坏编码 → 记录进 errors/degradations，继续处理其余材料。
- [ ] 缺 case.json → `status=input_error`；缺 output_contract → 仅元数据字段 + uncertainty。
- [ ] 写盘失败 → 保留骨架，退出码 2，不崩溃。
- [ ] 重复运行 → 目录遍历排序、无随机数，除时间戳外结果一致。
- [ ] `python3 -B scripts/selftest.py` 全 PASS（交付前本机跑一遍）。

## 生态贡献（10 分）

- [ ] `SKILL.md` frontmatter 含 name/description/version；脚本调用用相对路径 `scripts/main.py` / `scripts/selftest.py`。
- [ ] `references/fields-and-rules.md` 与 `references/checklist.md` 齐全。
- [ ] 最小调用示例已标"示例"，未冒充正式案例结果。
- [ ] 只依赖 Python 标准库，无第三方依赖、无个人文件路径。
- [ ] 降级与不确定性显式化：每个结论有 confidence，缺数据写 limitations/uncertainty/pending_human，不编造。

## 运行方式（复核用）

```bash
python3 -B scripts/main.py --help
python3 -B scripts/selftest.py
```
