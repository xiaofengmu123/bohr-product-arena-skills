# 交付自检与验收清单

运行前先执行一次 `python scripts/selftest.py`，三场景应全部 PASS；再对每个正式案例按下列条目逐项勾选。

## A. 输入与契约

- [ ] `/input/case.json` 与 `/input/task.md` 已读，`case_id` 已取到。
- [ ] `output_contract` 全部键已回填到 result.json，缺值给 `null`/`[]`，未知键不丢、不改名。
- [ ] 数据字典、单位/QC 规则、清洗工单均已打开并按内容执行（不是按文件名臆测）。

## B. 清洗正确性

- [ ] 缺失/未检测/低于检出限/真值 0 四类分开，无插补、无静默改值。
- [ ] 异常值有物理上下界与 locator 依据；保留/剔除/盖帽理由成文。
- [ ] 重复识别基于真实主键，近似重复已报告，保留策略明确。
- [ ] 单位/编码转换可逆或留映射表；摩尔换算标 `pending_human`；精度未丢失。

## C. 中间表示与可复核

- [ ] `plan.json` 存在且每字段含 `evidence`（locator + quote）。
- [ ] `field_model.json` 由清洗表重算，非手写。
- [ ] `quality_report.md` 数字与 `field_model.json`、`cleaned_data.csv` 一致。
- [ ] `--phase selfcheck` 输出 `selfcheck.json` 且 `ok: true`。

## D. 证据与异常

- [ ] 每个关键结论有 locator + 逐字 quote；冲突信息并列并标 `source_conflict`。
- [ ] `change_log` 每条含 `time/step/action`。
- [ ] `uncertainty` / `pending_human` 记录单位不明、规则冲突、主键冲突、无法解释项。

## E. 健壮性（每种都跑一遍）

- [ ] 空输入目录 → `insufficient_evidence`，退出码 0。
- [ ] 坏 JSON / 坏编码 → 记录到 `errors`/`degradations`，继续处理其余材料。
- [ ] 空表 / 全列缺失 / 单位不明 / 主键冲突 / 文件损坏 / 权限不足 → 识别并诚实记录，不崩溃。
- [ ] 同一输入两次运行，数据结果一致（确定性）。

## F. 交付前

- [ ] 文件清单：`SKILL.md` + `scripts/{main.py,core_impl.py,selftest.py}` + `references/{rules.md,checklist.md}`，共 6 个文件。
- [ ] 无示例答案、无正式测试产物、无预写 result.json。
- [ ] 总提交体积极限内（≤1.2MB），`main.py` < 30KB。
