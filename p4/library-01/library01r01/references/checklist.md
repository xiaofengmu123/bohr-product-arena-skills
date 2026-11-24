# 自检清单（对应评分维度，提交/交付前逐条勾选）

> 本清单与 `SKILL.md` 第 6 节一致，逐条对应评分维度；提交前逐项勾选，
> 并运行 `python3 -B scripts/selftest.py` 做机器自检。

## 实际效果（40 分）

- [ ] 已产出真实清单/目录树/重复组/版本候选/恢复报告，而非仅给建议。
- [ ] `library_manifest.csv` 每行一个源文件/目录，哈希与源文件一致。
- [ ] 落库文件与源文件一一对应：路径为相对 `input/` 的 POSIX 路径。
- [ ] 覆盖六类要素：文件类型、元数据字段、目录层级、重复文件、版本差异、权限与来源。
- [ ] 难处理文件（超大/非常规/符号链接/无权限/坏编码）未被静默跳过，逐条进 `degradations`/`errors`。
- [ ] 重复按内容 SHA-256 判定、版本按归一化主干 + 内容相似度 + mtime 判定，不按文件名。
- [ ] `result.json` 通过 `python3 -B scripts/core_impl.py --check output/result.json --case-json input/case.json`。

## 使用增益（25 分）

- [ ] 去重/版本/对账由脚本确定层完成，Agent 只做语义层，减少人工整理条目。
- [ ] 库目录（JSON/CSV/BibTeX/RIS/SQLite）自动解析并恢复对账，减少人工比对。
- [ ] 机械问答（计数/重复/缺失/断链/版本/作者/年份）自动给出证据定位。

## 创新增益（15 分）

- [ ] 别名识别（符号链接含断链 + 硬链接组）为新增能力。
- [ ] 行政版本说明解析并与候选链对齐（matched_files 链接/未链接）。
- [ ] 恢复对账区分 matches/mismatches/missing/metadata_only/orphaned，支持重建索引。
- [ ] **证据溯源强化（本版侧重）**：每条结论带 `locator + 逐字 quote`，`evidence_map` 以 `conclusions.<id>` 细分，并标注 `strength / kind / method / uncertainty`。

## 可靠体验（10 分）

- [ ] 空输入目录 → 退出码 0，`status=insufficient_evidence`，不抛异常。
- [ ] 坏 JSON/坏编码 → 记录进 `errors`/`degradations`，继续处理其余材料。
- [ ] 缺 case.json → `status=partial` 且记录错误；缺 output_contract → 仅元数据字段 + uncertainty。
- [ ] 写盘失败 → 保留已落盘骨架，不崩溃（先 `--skeleton-only` 后增量）。
- [ ] 重复运行 → 遍历排序、无随机数，除时间戳外结果一致。

## 生态贡献（10 分）

- [ ] `SKILL.md` frontmatter 含 name/description/version，调用路径为相对路径 `scripts/xxx.py`。
- [ ] `references/checklist.md` 与 `references/evidence-schema.md` 齐全。
- [ ] 最小调用示例已标明"示例"，未冒充正式案例结果。
- [ ] 只依赖 Python 标准库，无第三方依赖、无个人文件路径。

## 机器自检

```bash
python3 -B scripts/selftest.py
# 预期输出: selftest: all checks passed（退出码 0）
```
