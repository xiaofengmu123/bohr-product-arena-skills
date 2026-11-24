# 自检清单（提交/交付前逐条勾选，对应评分维度）

## 实际效果（40 分）

- [ ] 已产出真实清单/目录树/重复组/版本候选/对账/索引，而非仅给建议。
- [ ] `library_manifest.csv` 每行一个源文件；SHA-256 与源文件逐字节一致。
- [ ] 覆盖六类要素：文件类型、元数据字段、目录层级、重复文件、版本差异、权限与来源。
- [ ] 难处理文件（超大/符号链接/硬链接/不可读/坏编码）未静默跳过，逐条进 errors/degradations。
- [ ] 去重按内容 SHA-256；版本按归一化主干 + 内容差异，不按文件名。
- [ ] `result.json` 通过 `python3 -B scripts/finalize.py --check /app/output/result.json --case-json /input/case.json`。

## 使用增益（25 分）

- [ ] 盘点/去重/版本/别名/对账/索引由脚本确定层完成，Agent 只补语义层。
- [ ] JSON/CSV/BibTeX/RIS/SQLite 库目录自动解析并恢复对账（matches/missing/orphaned）。
- [ ] 机械问答（计数/重复/断链/版本/缺失/作者/年份）自动给出证据定位。

## 创新增益（15 分）

- [ ] 别名识别（符号链接含断链 + 硬链接组）为新增能力，已落盘。
- [ ] 行政版本说明解析并与候选链对齐，不按文件名猜版本。
- [ ] 恢复对账区分 matches/mismatches/missing/orphaned，支持重建索引。

## 可靠体验（10 分）

- [ ] 空输入目录 → 退出码 0、`status=insufficient_evidence`、不抛异常。
- [ ] 坏 JSON/坏编码 → 记录进 errors/degradations，继续处理其余材料。
- [ ] 缺 case.json → `status=input_error`；缺 output_contract → 仅元数据字段 + uncertainty。
- [ ] 写盘失败 → 保留已落盘骨架、退出码 2、不崩溃。
- [ ] 重复运行 → 遍历排序、无随机数，除时间戳外结果一致（`scripts/selftest.py` 已断言）。

## 生态贡献（10 分）

- [ ] `SKILL.md` frontmatter 含 name/description/version，脚本调用用相对路径 `scripts/xxx.py`。
- [ ] `references/checklist.md` 与 `references/fields-and-rules.md` 齐全。
- [ ] 最小调用示例已标明“示例”，未冒充正式案例结果。
- [ ] 仅依赖 Python 标准库，无第三方依赖、无个人文件路径。
