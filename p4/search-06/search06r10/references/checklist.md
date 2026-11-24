# 自检清单与降级细则

交付/运行前逐条勾选；每条对应一个评分维度或可靠性要求。核对失败即修复，不要带病交付。

## A. 实际效果（40 分：任务完成度/正确性/完整性/可核验性）

- [ ] `result.json` 为合法 JSON，含 `case_id`，`status ∈ {ok, partial, insufficient_evidence, input_error}`。
- [ ] 题目 `output_contract` 的**每一个字段**原样回填（缺失给 `null`/`[]`，不改名、不删未知键）。
- [ ] 跑通闭环：`订阅配置 → 时间窗检索 → 快照比对 → 去重归一 → 新增清单 → 规则提醒`，而非一次主题检索。
- [ ] 每条候选有来源定位（locator）与逐字引用（quote），可独立回放。
- [ ] 新增条目真实存在、确属本窗口新入库；DOI/作者/期刊/日期/版本准确。
- [ ] 预印本→正式版→勘误→撤稿关系正确，撤稿/更正与新增分开记录。
- [ ] 时间窗 UTC 闭区间、连续无缝；窗口边界与延迟入库条目已补回并注明。
- [ ] 空结果、冲突、缺失元数据、权限/接口异常分别说明，未补造数据。

## B. 使用增益 + 创新增益（25 + 15 分）

- [ ] 去重键稳定（DOI > arXiv > 归一标题），转载/同文多源/重复条目被压住。
- [ ] 版本链合并、增量状态、提醒抑制是基础 Agent 不具备的独立能力。
- [ ] 订阅配置、窗口起止、查询式、快照、去重键、提醒投递记录全部写入 `result.json`。
- [ ] 相同快照 + 相同窗口 + 相同来源 → 相同 `new/updated/removed`（复算一致）。
- [ ] 减少人工核对条数、重复抓取与通知噪声（用 `duplicates_suppressed` 与提醒数佐证）。

## C. 可靠体验（10 分）

- [ ] 空输入目录：退出码 0，写 `insufficient_evidence`，不抛异常。
- [ ] 坏 JSON / 坏编码：记 `errors`/`degradations`，继续处理其余材料。
- [ ] 缺参数：`--input/--output/--work` 缺省指向 `/input`、`/app/output`、`/app/work`。
- [ ] 写盘失败：降级到最小合法 JSON（case_id + status + errors），不崩溃。
- [ ] 重复运行：领域字段（除 runtime/change_log 时间戳）逐字节一致。
- [ ] 限流/超时/来源改版/日期缺失/时区错位/撤稿更正均有处置且幂等。
- [ ] 前 3 分钟已落盘 `partial` 骨架；中途崩溃仍留合法产物。

## D. 生态贡献（10 分）

- [ ] `SKILL.md` 写清角色、输入/输出契约、执行流程、时间预算、降级策略。
- [ ] 脚本只用标准库、相对路径；`python3 scripts/main.py --help` 退出码 0。
- [ ] 去重键规则、快照样本、提醒策略、回放步骤在 `references/` 中齐全。
- [ ] 最小调用示例已标注为示例，不冒充正式案例结果。

## 降级策略（不得编造）

1. **材料缺失/无法判断** → `status=insufficient_evidence`，`pending_human` 列缺口；不留空猜测。
2. **软件/工具缺失** → 标准库最小实现降级；无法实现的功能写 `degradations` + `limitations`。
3. **写盘失败** → 最小合法 JSON（case_id + status + errors），进程不崩溃。
4. **限流/超时/来源改版** → 幂等重试；仍失败如实记录，不伪造响应、不二次推送。
5. **字段缺失** → `null`/`not_reported`；冲突不隐藏，全部记录并标 `source_conflict`。

自检通过后，在 `change_log` 追加一条 `self_check_passed`（含时间与结论），再交付。

## 自检命令

```bash
python3 -B scripts/main.py --help                       # 退出码 0
python3 -B scripts/selftest.py                          # 14/14 checks passed
```
