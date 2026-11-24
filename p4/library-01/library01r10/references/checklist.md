# 自检清单（对应评分维度，提交/交付前逐条勾选）

## 实际效果（40 分）

- [ ] 已产出真实清单/目录树/重复组/版本候选/恢复报告/索引，而非仅建议。
- [ ] `library_manifest.csv` 每行一个源条目，`sha256` 与源文件逐字节一致（超大文件按降级记录）。
- [ ] 覆盖六类要素：文件类型、元数据字段、目录层级、重复文件、版本差异、权限与来源。
- [ ] 难处理文件（超大/非常规/符号链接/断链/无权限/坏编码）未被静默跳过，逐条进入 `errors`/`degradations`/`flags`。
- [ ] 重复按内容 SHA-256 判定；版本按“归一化主干 + 内容哈希 + 修改时间”判定并标注 `heuristic=true`，不按文件名。
- [ ] 元数据只来自库目录/清单的显式字段（BibTeX/RIS/CSV/JSON/SQLite），保留 `_catalog` 出处，不从文件名猜。
- [ ] `result.json` 通过 `python3 -B scripts/main.py check`。

## 使用增益（25 分）

- [ ] 去重/版本/别名/对账由脚本确定性层完成，Agent 只通过 `decisions.json` 做语义层，减少人工整理条目。
- [ ] 库目录（JSON/CSV/BibTeX/RIS/SQLite）自动解析并对账，减少人工比对。
- [ ] 机械问答（计数/重复/缺失/断链/版本/作者/年份）自动给出证据定位，见 `computed.answers`。

## 创新增益（15 分）

- [ ] 别名识别覆盖符号链接（含断链）与硬链接组（`st_dev`+`st_ino`，`st_nlink>1`）。
- [ ] 行政版本说明从 case.json/task.md/目录记录三路解析，并与候选链对齐（成员重叠标记 linked）。
- [ ] 恢复对账区分 matches/mismatches/missing/orphaned，输出 `recovery_report.json` 并支持 `library_index.json` 重建索引。
- [ ] 中间表示（`inventory.ir.json`）+ 决策覆盖层（`decisions.json`）把机械层与语义层解耦，是相对单体脚本的架构差异。

## 可靠体验（10 分）

- [ ] 空输入目录 → 退出码 0，`status=insufficient_evidence`，不抛异常。
- [ ] 坏 JSON/坏编码 → 记录进 `errors`/`degradations`（latin-1 兜底），继续处理其余材料。
- [ ] 缺 case.json → `status=input_error`；坏 case.json → 降级为 `partial` 并从 task.md 恢复 case_id。
- [ ] 缺 output_contract → 仅元数据字段 + `uncertainty` 标注。
- [ ] 写盘失败 → 保留已落盘骨架，退出码 2，不崩溃。
- [ ] 重复运行 → 遍历排序、哈希稳定、无随机数，除时间戳外结果一致（`selftest.py` 校验确定性）。

## 生态贡献（10 分）

- [ ] `SKILL.md` frontmatter 含 name/description/version，调用路径为相对路径 `scripts/xxx.py`。
- [ ] `references/fields-and-rules.md` 与 `references/checklist.md` 齐全。
- [ ] 最小调用示例已标明“示例”，未冒充正式案例结果。
- [ ] 只依赖 Python 标准库（`hashlib`/`csv`/`sqlite3`/`json`/`os`/`re`/`stat`/`tempfile`），无第三方依赖、无个人文件路径。
- [ ] `python3 -B scripts/selftest.py` 全绿后再交付。

## 快速自检命令

```bash
cd /skills/weekly-skill
python3 -B scripts/selftest.py
python3 -B scripts/main.py --help
```
