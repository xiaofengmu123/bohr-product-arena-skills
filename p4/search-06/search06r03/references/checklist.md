# 自检清单与降级策略

提交/交付前逐条勾选；每条对应一个评分维度或可靠性要求。核对失败即修复，不要带病交付。
运行 `python3 -B scripts/selftest.py` 可自动执行 A0、B2、C1–C4、D2、D3、D5 的机械检查。

## A. 结果正确性与完整性（实际效果）

- [ ] `result.json` 存在且为合法 JSON，含 `case_id`。
- [ ] 题目 `output_contract` 的**每一个**字段都原样回填（缺失给 `null`/`[]`，不改名、不删未知键）。
- [ ] 每一条候选文献都有来源定位（locator）与逐字引用（quote），可独立回放。
- [ ] 每条新增条目都能回答：是否真实存在、是否确属本窗口新入库、DOI/作者/期刊/日期/版本是否准确。
- [ ] 预印本与正式版合并关系正确；撤稿/更正与新增分开。
- [ ] 时间窗连续无缝，窗口边界与延迟入库条目已补回。
- [ ] 无结果、冲突、缺失元数据、权限/接口异常分别说明，未补造数据。

## B. 增量状态可核验（使用增益/创新增益）

- [ ] 订阅配置、查询式、窗口起止、来源快照、去重键、提醒投递记录全部写入 `result.json`。
- [ ] 任意一次增量可凭记录复算：相同快照 + 相同窗口 + 相同来源 → 相同 `new/updated/removed`。
- [ ] 已用 `scripts/main.py dedup` 或等价逻辑做过去重归一，去重键稳定。
- [ ] 重复与误报被抑制：转载、刚索引旧文献、同文多源不重复推送。

## C. 运行稳定性与异常处理（可靠体验）

- [ ] 空输入目录：退出码 0，写 `insufficient_evidence`，不抛异常。
- [ ] 坏 JSON / 坏编码：记录到 `errors`/`degradations`，继续处理其余材料。
- [ ] 缺参数：`--input/--output/--work` 有缺省值（`/input`、`/app/output`、`/app/work`）。
- [ ] 单文件超 2MB 上限：跳过并记 `degradations`，不崩溃。
- [ ] 前三分钟内已落盘一版 `partial` 骨架；中途崩溃仍留合法产物。
- [ ] 写盘失败：降级到最小合法 JSON（case_id + status + errors），不崩溃。

## D. 自检与确定性（本版重点）

- [ ] `scripts/selftest.py` 可一键运行并逐项报 PASS/FAIL，退出码 0 表示全部通过。
- [ ] 去重键 DOI > arXiv > 标题 的优先级在 `normalize` 中验证通过。
- [ ] DOI/arXiv/标题归一化稳定，版本后缀 `vN`、`doi:` 前缀、大小写与标点差异均被吸收。
- [ ] 撤稿 marker 被识别并与新增分开。
- [ ] **同输入两次运行，剥离 `runtime` 与 `change_log[*].time` 后产物逐字节一致（确定性）。**
- [ ] `main.py` 只依赖标准库，`python3 -B scripts/main.py --help` 退出码 0。

## 降级策略（不得编造）

1. **材料缺失/无法判断** → `status=insufficient_evidence`，`pending_human` 列缺口；不留空猜测。
2. **软件/工具缺失** → 标准库最小实现降级；无法实现的功能写 `degradations`，标 `limitations`。
3. **写盘失败** → 降级到最小合法 JSON（case_id + status + errors），不让进程崩溃。
4. **接口限流/超时/来源改版** → 幂等重试；仍失败则如实记录，不产生二次推送、不伪造响应。
5. **字段缺失** → 记 `null`/`not_reported`，不推断；冲突信息两个都记并标 `source_conflict`。

自检通过后，在 `change_log` 追加一条 `self_check_passed`（含时间与结论），再交付。
