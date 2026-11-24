# 自检清单（提交/交付前逐条勾选）

运行 `python3 -B scripts/selftest.py` 后逐条核对；每项对应评分维度。

## 实际效果（40 分）

- [ ] 已产出真实清单/目录树/重复组/版本候选/恢复报告，而非仅建议。
- [ ] `library_manifest.csv` 每行一个源文件，sha256 与源文件一致，无静默跳过。
- [ ] 覆盖六类要素：文件类型（ext）、元数据字段（size/mtime/mode/readable）、
      目录层级、重复文件（sha256）、版本差异（归一化主干+内容差异）、权限与来源。
- [ ] 难处理文件（超大/非常规/符号链接/无权限/坏编码）逐条记录进 `errors`/`degradations`。
- [ ] 重复按内容 SHA-256 判定，不按文件名；版本候选标注 `heuristic`。
- [ ] `python3 -B scripts/main.py --check /app/output/result.json --case-json /input/case.json` 退出码 0。

## 使用增益（25 分）

- [ ] 去重/版本/对账/索引由脚本确定层完成，Agent 只做语义层（读规则、修正、补证据）。
- [ ] 库目录（JSON/CSV/BibTeX/RIS/SQLite）自动解析并恢复对账（matches/mismatches/missing/orphaned）。
- [ ] 机械问答（计数/重复/缺失/断链/版本/作者/年份）自动给出，减少人工整理条目。

## 创新增益（15 分）

- [ ] 别名识别（符号链接含断链 + 硬链接组）落盘 `computed.aliases`。
- [ ] 行政版本说明解析与候选链对齐（`version_notes`），并标注来源。
- [ ] 每个关键结论有 `sources` 条目：locator + 逐字 quote，冲突不隐藏（`source_conflict`）。

## 可靠体验（10 分）

- [ ] 空输入目录 → 退出码 0，写合法结果，不抛异常。
- [ ] 坏 JSON/坏编码/超大文件 → 记录进 `errors`/`degradations`，继续处理其余材料。
- [ ] 缺 case.json → `status=input_error`；缺 output_contract → 仅元数据字段 + `uncertainty`。
- [ ] 前 3 分钟先落盘 partial 骨架（`--skeleton-only`），之后 `--update` 增量改进，无中途空产物。
- [ ] 重复运行 → 遍历排序、无随机数，除时间戳外结果一致。

## 生态贡献（10 分）

- [ ] `SKILL.md` frontmatter 含 name/description/version，调用路径为相对路径 `scripts/xxx.py`。
- [ ] `references/rubric-alignment.md` 与 `references/checklist.md` 齐全。
- [ ] 最小调用示例已标明"示例"，未冒充正式案例结果。
- [ ] 只依赖 Python 标准库，无第三方依赖、无个人文件路径。

## 提交约束

- [ ] 总文件数 ≤ 14，总体积 ≤ 1.2MB；`main.py` < 30KB。
- [ ] 无预制答案、无白板、无正式测试产物。
