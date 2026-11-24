# 自检清单：评分维度 → 检查项 → 常见失败与修复

本清单与 `scripts/selftest.py` 一一对应：前者供 Agent 逐条勾选，后者把可机械判定的部分编码为断言。提交/交付前两者都要跑一遍。

## 1. 任务完成度（实际效果·15 分）

- [ ] 目标是**可检验假设**而非复述题目：写成“若 X，则 Y 在 Z 条件下变化 Δ”的形式。
- [ ] 六要素全部可操作：变量有单位/量程，对照有目的，参数有范围与来源，步骤绑定设备/试剂/时长。
- [ ] 判读标准落地：终点指标 + 统计口径 + 达标阈值。
- 常见失败：目标复述题面；步骤是“准备样品—对照—检测—统计”的目录式空话 → 逐条补操作定义。

## 2. 结果正确性（实际效果·15 分）

- [ ] 假设可检验；自变量/因变量/混杂因素与测量方式匹配。
- [ ] 每个对照明确排除哪类混杂（空白/阳性/阴性/剂量梯度各司其职）。
- [ ] 参数在设备量程与试剂规格允许范围内；引用与型号/规格真实无编造。
- 常见失败：只有空白组没有阳性对照；参数超量程；型号/价格凭空写 → 删去无据项或标 `not_reported`。

## 3. 结果完整性（实际效果·5 分）

- [ ] 六要素 6 项均非空（`hypothesis_and_variables / controls / equipment_and_materials / time_and_cost / parameter_ranges / risks_and_anomalies`）。
- 常见失败：只写 2–3 项 → 逐项补齐，缺数据就写 `not_reported` 而非留空。

## 4. 结果可核验性（实际效果·5 分）

- [ ] result.json 有 `case_id`、版本（`skill.version`）、来源定位（`sources`）、金标准与 `change_log`。
- [ ] plan.md 与 result.json 内容一致；关键数值在两处同值。
- 常见失败：change_log 用墙钟时间破坏确定性 → 用逻辑步号 `L0001…`。

## 5. 使用增益（质量/效率/可验证性·25 分）

- [ ] 候选方案比较与参数裁剪有算术依据（量程/预算/排期），不是模板式空话。
- [ ] 相对基础 Agent 的增益可被公平对照验证（同输入、同条件；本 Skill 只负责产出，不伪造评测分）。
- 常见失败：比较只写“方案 B 更优”而无算术 → 补预算/排期/量程的复算过程。

## 6. 创新增益（新增能力/方法改进/创新证据·15 分）

- [ ] 至少落实一类：按研究目标反推对照、按量程与预算裁剪参数、生成风险预案与替代路径。
- [ ] 方法改进有据：写明从目标→假设→变量→对照→参数→资源的生成次序与判定规则。
- 常见失败：创新点自述无法复现 → 把规则写进 SKILL.md 与脚本，使其可重复执行。

## 7. 可靠体验（易用性/稳定性/异常处理·10 分）

- [ ] 按 SKILL.md 第 4 节可完成一次生成并判断是否可用；`--help` 退出码 0。
- [ ] 空输入 → `insufficient_evidence`（或 `partial`）不崩溃；坏 JSON/编码 → 记录后继续；重复运行一致。
- [ ] 异常被识别并写 `pending_human`/替代路径：输入缺失、约束冲突（时间窗 vs 样本量）、依赖不可用（缺货/排期满）、内容不确定。
- 常见失败：异常静默失败 → 每类异常落到 `errors/degradations/uncertainty/pending_human` 之一。

## 8. 生态贡献（文档/复用/分享·10 分）

- [ ] 输入清单、输出结构、依赖（仅标准库 + 可选 pdftotext）、示例、适用范围、限制、维护信息齐全。
- [ ] 模板与清单可复用：`references/` 的 schema 与清单、`scripts/main.py` 的 probe/scaffold/validate 可直接改用于同类实验方案任务。
- 常见失败：文档与实现不一致 → 提交前重跑 `scripts/selftest.py`。

## 运行自检

```bash
cd /skills/weekly-skill
python3 -B scripts/main.py --help
python3 -B scripts/selftest.py        # 退出码 0 表示清单通过、确定性成立
python3 -B scripts/main.py --input /input --output /app/output --work /app/work --stage validate
```
