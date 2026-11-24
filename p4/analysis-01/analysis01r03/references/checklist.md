# 交付前勾选单（对应评分维度）

在把包交给平台前，按顺序跑完并逐条勾选。每条右侧标注其对应的评分维度。

## A. 包结构（生态贡献 / 可靠体验）

- [ ] `SKILL.md` 存在，YAML frontmatter 含 `name`、`description`、`version`。
- [ ] `scripts/main.py`、`scripts/core_impl.py`、`scripts/selftest.py` 均可 `compile`，只 import 标准库。
- [ ] `python scripts/main.py --help` 退出码 0。
- [ ] 总文件数 ≤14、总体积 ≤1.2MB；main.py <30KB（重逻辑已拆到 core_impl.py）。
- [ ] `python scripts/selftest.py --mode package --skill-root <包根>` 全绿。

## B. 结果完整性（实际效果·完整性 5 分）

- [ ] `result.json` 含 `case_id` 与 output_contract **全部键**（缺值 `null`/`[]`，不丢键、不改名）。
- [ ] 含 `status`、`sources`、`uncertainty`、`change_log`，且 `status` 合法。
- [ ] `artifacts` 指向的每个产物真实存在。

## C. 正确性（实际效果·正确性 15 分）

- [ ] 缺失四类（未检测/低于检出限/录入缺失/真值0）分开，无插补。
- [ ] 异常值有物理上下界依据，保留/剔除理由成文。
- [ ] 去重基于真实主键，近似重复已归一，保留首条并记录重复组。
- [ ] 单位/编码转换可逆或留映射表，摩尔浓度不猜换算。

## D. 可复核性（实际效果·可核验性 5 分）

- [ ] `python scripts/selftest.py --mode result --input /input --output /app/output --work /app/work` 全绿。
- [ ] 报告数字与 `cleaned_data.csv` 逐字段一致（自检已复核）。
- [ ] 每项关键结论有 locator + 逐字 quote；冲突并列标 `source_conflict`。
- [ ] `run_manifest.json` 记录输入/输出 SHA-256。

## E. 确定性与稳定性（可靠体验 10 分）

- [ ] `python scripts/selftest.py --mode determinism --input /input --work /app/work` 两次指纹一致。
- [ ] 空表、坏 JSON、乱码、单位不明、主键冲突、文件损坏、权限不足均识别并诚实记录，退出码 0。
- [ ] partial 结果在 `pending_human`/`degradations` 明确标注局限，不编造。

## F. 诚实交付（硬性红线）

- [ ] 未预写正式答案、未编造评测分数或运行结果。
- [ ] 未伪造 DOI/引用/数值；缺失一律 `pending_human` 或 `not_reported`。
- [ ] PDF/图片材料经平台工具文本化后再入流水线，未按标签臆测内容。

全部勾选后再提交；任何一条失败都应先修复，不得带着已知失败交付。
