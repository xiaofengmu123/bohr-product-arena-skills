# 自检清单与降级策略（checklist.md）

交付/提交前逐条勾选；每条对应评分维度或可靠性要求。核对失败即修复，不带病交付。运行 `python3 -B scripts/selftest.py` 验证 6 个自动化场景。

## A. 结果正确性与完整性（实际效果 40 分）

- [ ] `result.json` 存在且为合法 JSON，含 `case_id`。
- [ ] 题目 `output_contract` 的**每一个字段**原样回填（缺失给 `null`/`[]`/`{}`，不改名、不删未知键；`change_log` 记录 `contract_discovered`）。
- [ ] 每一条候选文献有来源定位（locator）与逐字引用（quote），可独立回放。
- [ ] 每条新增能回答：是否真实存在、是否确属本窗口、DOI/作者/期刊/日期/版本是否准确。
- [ ] 预印本/正式版合并关系正确；撤稿/更正与新增分开。
- [ ] 时间窗连续无缝，窗口边界与延迟入库条目（`late_ingested`）已补回。
- [ ] 空结果、冲突、缺失元数据、权限/接口异常分别说明，未补造。

## B. 增量状态可核验（使用增益 25 分 / 创新增益 15 分）

- [ ] 订阅配置、查询式、窗口起止、来源快照、去重键、提醒投递记录全部写入 `result.json`。
- [ ] 任意一次增量可凭记录复算：相同快照 + 相同窗口 + 相同来源 → 相同 `new/updated/removed`。
- [ ] 已用 `scripts/main.py dedup` 或等价逻辑做去重归一，去重键稳定（DOI > arXiv > 标题）。
- [ ] 重复/转载/刚索引旧文献被抑制，`tracking.suppressed` 台账齐全。
- [ ] 形成基础方案没有的版本链合并、快照水位、提醒抑制能力，并写明边界与价值。

## C. 运行稳定性与异常处理（可靠体验 10 分）

- [ ] 空输入目录：退出码 0，写 `insufficient_evidence`，不抛异常。
- [ ] 坏 JSON / 坏编码：记入 `errors`/`degradations`，继续处理其余材料。
- [ ] 缺参数：`--input/--output/--work` 有缺省值（`/input`、`/app/output`、`/app/work`）。
- [ ] 重复运行两次结果字节一致（`sort_keys` 稳定布局）。
- [ ] 前 3 分钟已落盘 `partial` 骨架；中途崩溃仍留合法产物。
- [ ] 限流/超时/来源改版：幂等重试，仍失败如实记录，不二次推送。

## D. 文档与可复用性（生态贡献 10 分）

- [ ] `SKILL.md` 写清角色、输入/输出契约、执行流程、时间预算、降级策略。
- [ ] 脚本只用标准库、相对路径；`python3 scripts/main.py --help` 退出码 0。
- [ ] 去重键规则、快照样本、提醒策略、回放步骤在 `references/` 中齐全。

## 降级策略（不得编造）

1. **材料缺失/无法判断** → `status=insufficient_evidence`，`pending_human` 列缺口；不留空猜测。
2. **软件/工具缺失** → 标准库最小实现降级；无法实现写 `degradations` + `limitations`。
3. **写盘失败** → 降级到最小合法 JSON（`case_id` + `status` + `errors`），不让进程崩溃。
4. **接口限流/超时/来源改版** → 幂等重试；仍失败如实记录，不伪造响应、不二次推送。
5. **字段缺失** → `null`/`not_reported`，不推断；冲突信息两条都记并标 `source_conflict`。

自检通过后，在 `change_log` 追加一条 `self_check_passed`（含时间与结论），再交付。
