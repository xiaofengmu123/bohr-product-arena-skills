# 自检清单与降级策略

交付前逐条勾选。每条对应一个评分维度或可靠性要求；核对失败即修复。
运行 `python3 scripts/selftest.py` 可自动核对脚本侧的 A/B/C 项。

## A. 结果正确性与完整性（实际效果 40）

- [ ] `result.json` 存在且为合法 JSON，含 `case_id`。
- [ ] 题目 `output_contract` 的**每一个字段**都原样回填（缺失给 `null`/`[]`，
      不改名、不删未知键）；无契约时覆盖三行要求 `search_results`/`ranking`/
      `evidence`/`status_notes`/`tracking`/`dedup`。
- [ ] 每条候选都有来源定位（locator）与逐字引用（quote），可独立回放；
      `validation.evidence_ok` 为 true（`validate_evidence` 已扫）。
- [ ] 每条新增能回答：是否真实存在、是否确属本窗口新入库、
      DOI/作者/期刊/日期/版本是否准确。
- [ ] 预印本→正式版合并正确；撤稿/更正与新增分开（`version_chains` 与
      `retractions`/`updates` 分离）。
- [ ] 时间窗连续无缝、边界命中正确、`late_indexed` 延迟入库条目已补回且不被误报为新。
- [ ] 空结果、冲突、缺失元数据、权限/接口异常分别说明，未补造。

## B. 增量状态可核验（使用增益 25 / 创新增益 15）

- [ ] `subscription`/查询式/`window`/`snapshot.canonical_ids`/去重键/提醒投递
      记录全部写入 `result.json`。
- [ ] 同快照 + 同窗口 + 同来源 → 同 `new_items/updates/retractions`（确定性核心）。
- [ ] 已用 `scripts/main.py dedup` 或等价逻辑去重，去重键稳定（DOI>arXiv>标题）。
- [ ] 转载、刚索引旧文献、同文多源被抑制，且抑制带 `suppress_reason`。
- [ ] 版本链合并是基础方案没有的能力；`late_indexed` 抑制、撤稿分离是新增能力。
- [ ] 每项增量判定都有 `reason`，可与证据链对上。

## C. 运行稳定性与异常处理（可靠体验 10）

- [ ] 空输入目录：退出码 0，写 `insufficient_evidence`，不抛异常。
- [ ] 坏 JSON / 坏编码：记入 `errors`/`degradations`，继续处理其余材料。
- [ ] 缺参数：`--input/--output/--work` 缺省 `/input`、`/app/output`、`/app/work`。
- [ ] 重复运行结果一致（`sort_keys` + 原子写 + 纯函数核心）。
- [ ] 前 3 分钟已落盘 `partial` 骨架；中途崩溃仍留合法产物。
- [ ] 单文件 >2MB 跳过并记 `degradations`（`OversizeFileError`）。

## D. 文档与可复用性（生态贡献 10）

- [ ] `SKILL.md` 写清角色、输入/输出契约、执行流程、预算、降级策略、评分维度对齐。
- [ ] 脚本只用标准库、相对路径，`python3 scripts/main.py --help` 退出码 0。
- [ ] 去重键规则、快照样本、提醒策略、回放步骤在 `references/normalization.md` 齐全。
- [ ] `scripts/selftest.py` 通过，覆盖评分维度对应的边界用例。

## 降级策略（不得编造）

1. **材料缺失/无法判断** → `status=insufficient_evidence`，`pending_human` 列缺口；
   不留空猜测。
2. **软件/工具缺失** → 标准库最小实现降级；做不到的功能写 `degradations` + `limitations`。
3. **写盘失败** → 最小合法 JSON（`case_id` + `status` + `errors`），不崩溃。
4. **接口限流/超时/来源改版** → 幂等重试；仍失败如实记录，不伪造响应、不二次推送。
5. **字段缺失** → `null`/`not_reported`，不推断；冲突两个都记并标 `source_conflict`。
6. **日期/时区错位** → `parse_date` 兼容多格式并 UTC 归一；解析失败返回 `None`，
   该条进 `suppressed`（`missing_date_or_window`），不强行归类。

自检通过后，在 `change_log` 追加一条 `self_check_passed`（含时间与结论），再交付。
