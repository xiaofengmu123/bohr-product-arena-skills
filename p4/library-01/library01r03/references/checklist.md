# 自检清单（对应评分维度，提交/交付前逐条勾选）

本清单由 `scripts/selftest.py` 的机器检查与人工复核共同覆盖。机器可验证项标注 `[脚本]`，
运行 `python3 -B scripts/selftest.py` 一次即可复验大部分项目。

## 1. 实际效果（40 分）

- [ ] `[脚本]` 已产出真实清单、目录树、重复组、版本候选、恢复对账、索引，而非仅建议。
- [ ] `[脚本]` 落库文件与源文件一一对应：`library_manifest.csv` 每行一个源文件，SHA-256 与源文件一致。
- [ ] 覆盖六类要素：文件类型、元数据字段、目录层级、重复文件、版本差异、权限与来源。
- [ ] 难处理文件（超大 / 非常规 / 符号链接 / 无权限 / 坏编码）未被静默跳过，逐条写入 `errors`/`degradations`。
- [ ] `[脚本]` 重复按内容 SHA-256 判定，版本按归一化主干 + 内容差异判定，不按文件名。
- [ ] `[脚本]` `python3 -B scripts/main.py --check result.json --case-json case.json` 通过。

## 2. 使用增益（25 分）

- [ ] 去重 / 版本 / 对账由脚本确定层完成，Agent 只做语义层，减少人工整理条目。
- [ ] 库目录（JSON / CSV / BibTeX / RIS / SQLite）自动解析并恢复对账，减少人工比对。
- [ ] 机械问答（计数 / 重复 / 缺失 / 断链 / 版本 / 来源）自动写入 `answers.json`，并带证据定位。

## 3. 创新增益（15 分）

- [ ] 别名识别（符号链接含断链 + 硬链接组）为新增能力。
- [ ] 行政版本说明解析并与候选链对齐，标注启发式。
- [ ] 恢复对账区分 matches / mismatches / missing / orphaned，支持重建索引。
- [ ] 确定性是新重点：同输入两次运行产物一致，见 `scripts/selftest.py`。

## 4. 可靠体验（10 分）

- [ ] `[脚本]` 空输入目录 → 退出码 0，`status=insufficient_evidence`，不抛异常。
- [ ] `[脚本]` 坏 JSON / 坏编码 → 记录进 `errors`/`degradations`，继续处理其余材料。
- [ ] `[脚本]` 缺 case.json → `status=input_error`；缺 output_contract → 仅元数据字段 + uncertainty。
- [ ] 写盘失败 → 保留骨架，退出码 2，不崩溃。
- [ ] `[脚本]` 重复运行 → 遍历排序、无随机数，除 `generated_at`/`time` 外结果一致。

## 5. 生态贡献（10 分）

- [ ] `SKILL.md` frontmatter 含 name / description / version，调用路径为相对路径 `scripts/xxx.py`。
- [ ] `references/fields-and-rules.md` 与 `references/checklist.md` 齐全。
- [ ] 最小调用示例已标明"示例"，未冒充正式案例结果。
- [ ] 只依赖 Python 标准库，无第三方依赖、无个人文件路径。
- [ ] 总文件数 ≤14、总体积 ≤1.2MB，`main.py` 薄封装、实现拆入 `core_impl.py`。

## 提交前必跑

```bash
python3 -B scripts/selftest.py                 # 退出码 0
python3 -B scripts/main.py --help              # 退出码 0
```
