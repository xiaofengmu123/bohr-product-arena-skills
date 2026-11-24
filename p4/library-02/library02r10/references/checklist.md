# 自检清单（对应评分维度，提交前逐条勾选）

## 实际效果（40 分）

- [ ] 已产出真实清单/目录树/重复组/版本候选/别名/对账/索引/问答，而非仅建议。
- [ ] `library_manifest.csv` 每行一个源文件，SHA-256 与源文件内容一致，无静默跳过。
- [ ] 覆盖：文件类型、元数据字段、目录层级、重复文件、版本差异、权限与来源六类要素。
- [ ] 难处理文件（超大/非常规/符号链接/无权限/坏编码/无文本层）逐条进 errors/degradations。
- [ ] 重复按内容 SHA-256，版本按归一化主干 + 内容差异（标 heuristic），不按文件名。
- [ ] `python3 -B scripts/main.py --check --result /app/output/result.json --case-json /input/case.json` 通过。

## 使用增益（25 分）

- [ ] 去重/版本/别名/对账/索引由脚本确定性层完成，Agent 只做语义层，减少人工条目。
- [ ] 库目录（JSON/CSV/Markdown/BibTeX/RIS/SQLite）自动解析并恢复对账，减少人工比对。
- [ ] 机械问答（计数/重复/缺失/断链/版本/哈希/未登记/条件命中）自动给出 locator + quote。

## 创新增益（15 分）

- [ ] 别名识别（符号链接含断链 + 硬链接组）为新增能力。
- [ ] 证据账本（append-only typed ledger）为唯一事实源，产物由账本派生、可重放复核。
- [ ] 每个阶段边界做 schema 校验（不是只在结尾），部分构建状态始终自洽。

## 可靠体验（10 分）

- [ ] 空输入目录 → 退出码 0，`status=insufficient_evidence`，不抛异常。
- [ ] 坏 JSON/坏编码 → 记录 errors/degradations，继续处理其余材料。
- [ ] 缺 case.json → `status=input_error`；缺 output_contract → 仅元数据字段 + uncertainty。
- [ ] 写盘失败 → 保留骨架，退出码 2，不崩溃。
- [ ] 重复运行 → 目录遍历排序、无随机数，除时间戳外结果一致。
- [ ] 单文件大小上限（哈希 2MB、文本 8MB），超限跳过并记录。

## 生态贡献（10 分）

- [ ] `SKILL.md` frontmatter 含 name/description/version。
- [ ] 脚本调用用相对路径 `scripts/main.py`，只依赖 Python 标准库。
- [ ] `references/fields-and-rules.md` 与 `references/checklist.md` 齐全。
- [ ] 最小调用示例已标明"示例"，未冒充正式案例结果。
- [ ] 运行 `python3 -B scripts/selftest.py` 全绿（20 项）。
