# 自检清单（对应评分维度，提交/交付前逐条勾选）

## 实际效果（40 分）

- [ ] 已产出真实清单/目录树/重复组/版本候选/别名/恢复报告/索引，而非仅建议。
- [ ] `library_manifest.csv` 每行一个源文件，哈希与源文件内容一致，无静默跳过；控制文件标 `is_control=true`。
- [ ] 覆盖六类要素：文件类型、元数据字段、目录层级、重复文件、版本差异、权限与来源。
- [ ] 难处理文件（超大/非常规/符号链接/无权限/坏编码）逐条记录进 errors/degradations。
- [ ] 重复按内容 SHA-256 判定，版本按归一化主干 + 内容差异判定（`heuristic:true`），不按文件名。
- [ ] `python3 -B scripts/main.py --check output/result.json --case-json input/case.json` 退出码 0。

## 使用增益（25 分）

- [ ] 去重/版本/别名/对账由脚本确定层完成，Agent 只做语义层，减少人工整理条目。
- [ ] 库目录（JSON/CSV/Markdown/BibTeX/RIS/SQLite）自动解析并恢复对账，减少人工比对。
- [ ] 机械问答（计数/重复/缺失/断链/版本/哈希/未登记/关键词检索）自动给出证据定位。

## 创新增益（15 分）

- [ ] 别名识别（符号链接含断链 + 硬链接组）为新增能力。
- [ ] 行政版本说明解析并与候选链对齐（linked/orphan）。
- [ ] 恢复对账区分 matches/mismatches/missing/orphaned，支持重建索引。

## 可靠体验（10 分，本版重点）

- [ ] 空输入目录 → 退出码 0，`status=insufficient_evidence`，不抛异常。
- [ ] 坏 JSON → `status=input_error`，记录进 errors，继续写合法结果。
- [ ] 坏编码 → 三级编码容错，仍失败记录 `unreadable_text`，不崩溃。
- [ ] 缺参数/未知参数 → 缺省 `/input`、`/app/output`、`/app/work`。
- [ ] 超大文件（>64MiB 哈希、>2MiB 文本索引）→ 跳过并记录 `too_large`/`too_large_text`。
- [ ] 坏 SQLite → 记录 `sqlite_open_error`/`sqlite_query_error`，不崩溃。
- [ ] 写盘失败 → 保留骨架，退出码 2，不崩溃。
- [ ] 重复运行 → 目录遍历排序、无随机数，除 `runtime`/`change_log` 时间戳外结果一致。
- [ ] `python3 -B scripts/selftest.py` 全绿。

## 生态贡献（10 分）

- [ ] `SKILL.md` frontmatter 含 name/description/version，脚本调用用相对路径 `scripts/main.py`。
- [ ] `references/fields-and-rules.md` 与 `references/checklist.md` 齐全。
- [ ] 最小调用示例已标明"示例"，未冒充正式案例结果。
- [ ] 只依赖 Python 标准库，无第三方依赖、无个人文件路径。
