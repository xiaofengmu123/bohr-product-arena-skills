# 自检清单（对应评分维度，提交/交付前逐条勾选）

每条左侧标注其对应的评分维度，便于逐项核对是否满足原题要求。
运行 `python3 -B scripts/selftest.py` 可自动验证脚本层鲁棒性与确定性。

## 实际效果（40 分）

- [ ] 已产出真实清单/目录树/重复组/版本候选/别名/恢复报告/章节索引，而非仅建议。
- [ ] `library_manifest.csv` 每行一个源文件，SHA-256 与源文件内容一致，无静默跳过。
- [ ] 覆盖六类要素：文件类型、元数据字段、目录层级、重复文件、版本差异、权限与来源。
- [ ] 难处理文件（超大/非常规/符号链接/无权限/坏编码/无文本层）逐条进 errors/degradations。
- [ ] 检索命中落到段落级 locator（`文件路径#L行号`），每条带逐字 quote，可独立复核。
- [ ] 过滤条件（年份/项目/类型/批次/版本/权限）作为硬约束执行，无命中如实报告不放宽。
- [ ] `python3 -B scripts/main.py --check /app/output/result.json --case-json /input/case.json` 通过。

## 使用增益（25 分）

- [ ] 去重按内容 SHA-256 判定，版本按归一化主干 + 内容相似度判定，不按文件名。
- [ ] 库目录（JSON/CSV/Markdown/BibTeX/RIS/SQLite）自动解析并恢复对账，减少人工比对。
- [ ] 机械问答（计数/重复/缺失/断链/版本/哈希/未登记）自动给出证据定位。
- [ ] `--update patch.json` 只做语义层增量，Agent 不必重跑盘点，减少调用成本。

## 创新增益（15 分）

- [ ] 别名识别（符号链接含断链 + 硬链接组）为新增能力，基础 Agent 不易稳定实现。
- [ ] 行政版本说明解析并与候选链对齐（linked/orphan），形成版本关系证据。
- [ ] 恢复对账区分 matches/mismatches/missing/orphaned，支持重建可检索索引。
- [ ] 章节/段落级索引 + 硬过滤 + 回链校验构成完整"跨文件检索—定位—过滤—回链"闭环。

## 可靠体验（10 分）

- [ ] 空输入目录 → 退出码 0，`status=insufficient_evidence`，不抛异常。
- [ ] 坏 JSON/坏编码 → 记录进 errors/degradations，继续处理其余材料。
- [ ] 缺 case.json → `status=input_error`；缺 output_contract → 仅元数据字段 + uncertainty。
- [ ] 写盘失败 → 保留骨架，退出码 2，不崩溃；重启后可 `--update` 续跑。
- [ ] 重复运行 → 目录遍历排序、无随机数，除时间戳外结果一致（selftest 第 7 项验证）。

## 生态贡献（10 分）

- [ ] `SKILL.md` frontmatter 含 name/description/version；脚本调用用相对路径
      `scripts/main.py`。
- [ ] `references/fields-and-rules.md` 与 `references/checklist.md` 齐全。
- [ ] 最小调用示例已标"示例"，未冒充正式案例结果。
- [ ] 只依赖 Python 标准库，无第三方依赖、无个人文件路径、无可执行 symlink。
