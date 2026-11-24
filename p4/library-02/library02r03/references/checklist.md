# 自检清单（对应评分维度，提交/交付前逐条勾选）

本清单与 `scripts/selftest.py` 配合：凡标"自动"的条目由 `python3 -B scripts/selftest.py`
机械验证；标"人工"的条目由提交前人工复核。清单顺序与题目评分维度一一对应。

## 实际效果（40 分）

- [ ]（自动）已产出真实清单/目录树/重复组/版本候选/别名/恢复报告/索引/检索答案，而非仅建议。
- [ ]（自动）`library_manifest.csv` 每行一个源文件，SHA-256 与源文件内容一致，无静默跳过。
- [ ]（人工）覆盖六类要素：文件类型（含 PDF/Word/Excel/PPT 文本层）、元数据字段、目录层级、重复文件、版本差异、权限与来源。
- [ ]（自动）难处理文件（超大/非常规/符号链接/无权限/坏编码/无文本层扫描件）逐条记录进 `errors`/`degradations`。
- [ ]（自动）重复按内容 SHA-256 判定，版本按归一化主干 + 内容相似度判定，不按文件名；版本候选标 `heuristic: true`。
- [ ]（自动）`python3 -B scripts/main.py --check output/result.json` 退出码 0。
- [ ]（人工）检索答案的每一条定位都落在原文那一段，`sources[].quote` 为逐字原文，可独立复核。

## 使用增益（25 分）

- [ ]（人工）去重/版本/别名/对账/索引由脚本确定层完成，Agent 只做语义层（对照原文确认定位、补 `sources`）。
- [ ]（人工）库目录（JSON/CSV/Markdown/BibTeX/RIS/SQLite）自动解析并恢复对账，减少人工比对。
- [ ]（自动）机械问答（计数/重复/缺失/断链/版本/哈希/未登记）自动给出证据定位。

## 创新增益（15 分）

- [ ]（自动）别名识别（符号链接含断链 + 硬链接组）为新增能力。
- [ ]（自动）行政版本说明解析并写入 `dedup_version.json` 的 `version_notes`。
- [ ]（自动）恢复对账区分 `matches/mismatches/missing/orphaned`，支持重建索引。
- [ ]（自动）PDF/Word/Excel/PPT 文本层提取（标准库 zipfile/zlib 实现，无第三方依赖）。

## 可靠体验（10 分）

- [ ]（自动）空输入目录 → 退出码 0，`status=insufficient_evidence`，不抛异常。
- [ ]（自动）坏 JSON/坏编码 → 记录进 `errors`/`degradations`，继续处理其余材料。
- [ ]（自动）缺 case.json → `status=input_error` 或按空输入处理；缺 `output_contract` → 仅元数据字段 + `uncertainty`。
- [ ]（自动）写盘失败 → 保留骨架，退出码 2，不崩溃。
- [ ]（自动）重复运行 → 目录遍历排序、无随机数，除 `generated_at`/`runtime`/`time` 时间字段与 `output_dir`/`work_dir` 绝对路径外结果一致（由 `selftest.py` 验证）。

## 生态贡献（10 分）

- [ ]（自动）`SKILL.md` frontmatter 含 name/description/version，脚本调用用相对路径 `scripts/main.py`。
- [ ]（自动）`references/fields-and-rules.md` 与 `references/checklist.md` 齐全。
- [ ]（人工）最小调用示例已标明"示例"，未冒充正式案例结果。
- [ ]（自动）只依赖 Python 标准库，无第三方依赖、无个人文件路径。

## 本版侧重点：自检与确定性（变体）

- [ ]（自动）`scripts/selftest.py` 全部 PASS：同一 fixture 连跑两次，`result.json` 深层一致、其余产物逐字节一致、`--check` 通过。
- [ ]（人工）确认所有随机源已消除：无 `random`、无未排序集合迭代、JSON 序列化 `sort_keys=True`。
- [ ]（人工）确认 `result.json` 中 `computed.*` 只含确定性事实，时间戳仅出现在 `generated_at`/`runtime.seconds`/`change_log[].time`。
