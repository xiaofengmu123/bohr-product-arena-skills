# 自检清单与降级策略

提交/交付前逐条勾选；每条对应一个评分维度或可靠性要求。核对失败即修复，不要带病交付。
机械自检可运行 `python3 scripts/main.py selftest`，覆盖空输入、坏 JSON、正常发现、去重四个场景。

## A. 结果正确性与完整性（实际效果）

- [ ] `result.json` 存在且为合法 JSON，含 `case_id`。
- [ ] 题目 `output_contract` 的**每一个字段**都原样回填（缺失给 `null`/`[]`，不改名、不删未知键）。
- [ ] 每一条候选文献都有来源定位（locator）与逐字引用（quote），可独立回放。
- [ ] 每条新增条目都能回答：是否真实存在、是否确属本窗口新入库、DOI/作者/期刊/日期/版本是否准确。
- [ ] 预印本与正式版合并关系正确；撤稿/更正与新增分开。
- [ ] 时间窗连续无缝，窗口边界与延迟入库条目已补回。
- [ ] 无结果、冲突、缺失元数据、权限/接口异常分别说明，未补造数据。

## B. 中间表示与可核验（本版重点 / 使用增益 + 创新增益）

- [ ] `plan.json` 已生成且含 `subscription`、`window`、`snapshot`、`dedup`、`reminders`、`evidence_spec`、`materials`。
- [ ] `result.json` 的 `plan_ref` 指向实际 plan.json；每个关键结论可沿 `result → findings/dedup → plan → 材料片段` 回溯。
- [ ] 订阅配置、查询式、窗口起止、来源快照、去重键、提醒投递记录全部写入 result.json/plan.json。
- [ ] 任意一次增量可凭记录复算：相同快照 + 相同窗口 + 相同来源 → 相同 `new/updated/retraction/correction`。
- [ ] 已用 `scripts/main.py dedup` 或等价逻辑做过去重归一，canonical key 稳定（DOI > arXiv > 归一标题）。
- [ ] 重复与误报被抑制：转载、刚索引旧文献、同文多源不重复推送。

## C. 运行稳定性与异常处理（可靠体验）

- [ ] 空输入目录：退出码 0，写 `insufficient_evidence`，不抛异常。
- [ ] 坏 JSON / 坏编码：记录到 `errors`/`degradations`，继续处理其余材料。
- [ ] 缺参数：`--input/--output/--work` 有缺省值（`/input`、`/app/output`、`/app/work`）。
- [ ] 写盘失败：降级到最小合法 JSON（case_id + status + errors），不让进程崩溃。
- [ ] 重复运行两次结果一致（固定种子、确定性布局、`sort_keys`）。
- [ ] 前三分钟内已落盘一版 `partial` 骨架；中途崩溃仍留合法产物。
- [ ] `python3 scripts/main.py selftest` 通过（空输入、坏 JSON、正常发现、去重）。

## D. 文档与可复用性（生态贡献）

- [ ] `SKILL.md` 写清角色、输入/输出契约、执行流程、时间预算、降级策略。
- [ ] `references/normalization.md` 给出归一化、去重键、版本链、时间窗、提醒、重试、回放的精确规则。
- [ ] 脚本只用标准库、相对路径，他人可直接 `python3 scripts/main.py --help` 运行。
- [ ] 去重键规则、快照样本、提醒策略、回放步骤齐全，可独立复用。

## 降级策略（不得编造）

1. **材料缺失/无法判断** → `status=insufficient_evidence`，`pending_human` 列缺口；不留空猜测。
2. **软件/工具缺失** → 用标准库最小实现降级；无法实现的功能写 `degradations`，标 `limitations`。
3. **写盘失败** → 降级到最小合法 JSON（case_id + status + errors），不让进程崩溃。
4. **接口限流/超时/来源改版** → 重试幂等；仍失败则如实记录，不产生二次推送、不伪造响应。
5. **字段缺失** → 记 `null`/`not_reported`，不推断；冲突信息两个都记并标 `source_conflict`。

自检通过后，在 `change_log` 追加一条 `self_check_passed`（含时间与结论），再交付。
