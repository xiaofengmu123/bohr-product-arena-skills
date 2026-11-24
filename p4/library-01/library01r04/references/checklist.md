# 自检清单（对应评分维度，提交/交付前逐条勾选）

## 实际效果（40 分）

- [ ] 已产出真实清单/目录树/重复组/版本候选/恢复报告，而非仅建议。
- [ ] 落库文件与源文件一一对应：`library_manifest.csv` 每行一个源文件，哈希与源文件一致。
- [ ] 覆盖六类要素：文件类型、元数据字段、目录层级、重复文件、版本差异、权限与来源。
- [ ] 难处理文件（超大/非常规/符号链接/无权限/坏编码）未被静默跳过，逐条记录。
- [ ] 重复按内容 SHA-256 判定；版本按归一化主干 + 内容差异判定，不按文件名，并标注 `heuristic=true`。
- [ ] `result.json` 通过 `python3 -B scripts/main.py --check /app/output/result.json --check-case-json /input/case.json`。

## 使用增益（25 分）

- [ ] 去重/版本/对账由脚本确定层完成，Agent 只做语义层，减少人工整理条目。
- [ ] 库目录（JSON/CSV/BibTeX/RIS/SQLite）自动解析并恢复对账，减少人工比对。
- [ ] 机械问答（计数/重复/缺失/断链/版本/作者/年份）自动给出证据定位（`computed.answers`）。

## 创新增益（15 分）

- [ ] 别名识别（符号链接含断链 + 硬链接组）为新增能力，写入 `computed.aliases`。
- [ ] 行政版本说明解析（扫描文本中的 replace/supersede/版本迁移行）并与候选链对齐。
- [ ] 恢复对账区分 matches/mismatches/missing/orphaned，支持重建索引。

## 可靠体验（10 分）

- [ ] 空输入目录 → 退出码 0，`status=insufficient_evidence`，不抛异常。
- [ ] 坏 JSON/坏编码 → 记录进 errors/degradations，继续处理其余材料。
- [ ] 缺 case.json → `status=input_error`；缺 output_contract → 仅元数据字段 + uncertainty。
- [ ] 写盘失败 → 保留骨架，不崩溃；重启后可 `--update`。
- [ ] 重复运行 → 遍历排序、无随机数，除时间戳外结果一致（见 `scripts/selftest.py` 的确定性断言）。

## 降级与不确定性（本版重点，10 分权重计入可靠体验与创新证据）

- [ ] 每个不确定结论（去重边界、版本候选、对账缺口）都有 `uncertainty` 条目与严重度。
- [ ] 每次跳过/失败（too_large/unreadable/non_regular/sqlite 错误）都有 `degradations` 或 `errors` 条目。
- [ ] 需要人工决断的证据缺口写入 `pending_human`，结论保持 `partial`，不升级为 `ok`。
- [ ] 没有任何字段是基于领域标签或记忆补造：缺数据写 `not_reported`/`null`。

## 生态贡献（10 分）

- [ ] `SKILL.md` frontmatter 含 name/description/version，调用路径为相对路径 `scripts/xxx.py`。
- [ ] `references/fields-and-rules.md` 与 `references/checklist.md` 齐全。
- [ ] 最小调用示例已标明"示例"，未冒充正式案例结果。
- [ ] 只依赖 Python 标准库，无第三方依赖、无个人文件路径。

## 交付前自检命令

```bash
python3 -B scripts/selftest.py
python3 -B scripts/main.py --help
```
