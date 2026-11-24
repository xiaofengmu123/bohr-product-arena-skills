# 自检清单（交付前逐条勾选）

对应评分维度：实际效果 40、使用增益 25、创新增益 15、可靠体验 10、生态贡献 10。FAIL 级必须修复；WARN 级写入 `uncertainty` 或正文说明。

## A. 实际效果（任务完成 / 正确性 / 完整性 / 可核验）

- [ ] `result.json` 合法、可解析，含 `case_id` 与题目 `output_contract` 全部字段（未声明时按默认契约）。
- [ ] 七要素齐全：背景与缺口、问题定义、目标、技术路线、阶段计划、风险、预期成果，且与给定周期、经费、数据、人员相容（立得住/排得开）。
- [ ] 目标—路线—验证—成果与问题一一对应（`question_ref`、`depends_on`、`critical_path` 对齐），无各写各的（对得上）。
- [ ] 论断、数字、引用与来源一一对应；指标阈值有依据；不新增材料外事实。
- [ ] 每条风险有触发信号、影响范围、备选方案、决策时点（说得清）。
- [ ] `evidence_map` 每个条目的 `source_id` 在 `sources` 中存在，且带 `locator` 或逐字 `quote`。
- [ ] `fact`/`summary`/`inference` 区分正确；推断项已列入 `uncertainty`。
- [ ] `artifacts` 非空且指向真实存在的可编辑文件与预览。

## B. 使用增益（质量 / 效率 / 可验证）

- [ ] 已运行 `python scripts/main.py check`，FAIL=0（WARN 已说明）。
- [ ] 已运行 `python scripts/main.py render`，生成 proposal.md / plan.json / evidence_map.json / evidence_map.md / route.mmd / schedule.mmd / overview.svg。
- [ ] 前 3 分钟已落盘 partial 骨架（`init`），后续为增量改进而非最后一次性重写。
- [ ] `change_log` 记录关键动作，可还原“骨架 → 中间表示 → 成品”过程。
- [ ] 中间表示 `plan.json` 为唯一事实源，`proposal.md` 由它编译，两者无漂移。

## C. 创新增益（新增能力 / 方法改进）

- [ ] 约束抽取：从课题指南、前期数据、文献、组内讨论抽取“已知—未知—待验证”缺口，而非文献罗列。
- [ ] 要素对齐：目标/路线/成果通过 `question_ref`/`depends_on` 显式对齐，可机器校验。
- [ ] 计划可行性校验：里程碑月份落在研究周期内，工作量与周期/资源相容。
- [ ] 结构化中间表示（plan.json）+ 证据映射（evidence_map.json）分离，支持逐条回溯与复跑。
- [ ] 自包含 SVG 预览，无需额外软件即可检查七要素结构与布局。

## D. 可靠体验（易用 / 稳定 / 异常处理）

- [ ] 脚本仅依赖 Python 标准库，`scripts/main.py` 可重复运行、幂等。
- [ ] 空输入、坏 JSON、缺材料、格式不支持时写入 `limitations`/`uncertainty`，不编造。
- [ ] 权限不足或软件缺失时，`status` 如实降级为 `partial`/`failed`，并说明缺口。
- [ ] 不同学科/篇幅/重复运行下要素不漂移、目标数量与周期经费前后一致。

## E. 生态贡献（文档 / 复用 / 分享）

- [ ] `SKILL.md` 写清角色、输入输出契约、执行流程、时间预算、自检与降级。
- [ ] `references/output-contract.md` 定义字段与词表，他人可复用。
- [ ] 最小调用示例已给出，并标注为示意输出。
- [ ] 已运行 `python scripts/selftest.py`，三场景（空输入/坏 JSON/有效案例）通过。

## 交付门禁

- `python scripts/main.py check --strict` 退出码为 0（FAIL=0）。
- `python scripts/selftest.py` 输出 `selftest PASS 3/3`。
- 不编造评测分数、软件状态或科学数据；缺什么如实写什么。
