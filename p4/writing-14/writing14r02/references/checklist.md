# 自检清单（交付前逐条勾选）

对应评分维度：实际效果（40）、使用增益（25）、创新增益（15）、可靠体验（10）、生态贡献（10）。
FAIL 级必须修复；WARN 级应写入 `uncertainty` 或正文说明。可运行 `python scripts/selftest.py` 验证脚本本身，运行 `python scripts/proposal.py check` 验证当前 result.json。

## A. 实际效果（正确性 / 完整性 / 可核验）

- [ ] `result.json` 合法、可被 JSON 解析，含 `case_id` 与题目 `output_contract` 全部字段。
- [ ] 七要素齐全：研究背景与缺口、问题定义、目标、技术路线、阶段计划、风险、预期成果；风险覆盖四类且各带触发/影响/备选/决策时点；预期成果带考核方式。
- [ ] 研究问题具体、可证伪（1–3 个），背景是“已知—未知—待验证”缺口陈述而非文献罗列。
- [ ] 事实、数字、引用忠于材料原文：无凭空术语、无张冠李戴的数值/结论；指标阈值有出处。
- [ ] 论断与引用一一对应：每个关键论断的 `evidence[]` 均能在 `evidence_map` 找到，且带 `locator` + 逐字 `quote`。
- [ ] 边界已写明：`constraints` 中的周期/经费/数据/人员与方案口径一致，超出部分已标 `infeasible`。
- [ ] `artifacts` 非空且指向真实存在的可编辑文件与预览（`render` 已运行）。

## B. 使用增益（质量 / 效率 / 可验证）

- [ ] 已运行 `python scripts/proposal.py check`，FAIL=0（WARN 已说明）。
- [ ] 已运行 `python scripts/proposal.py render`，生成 proposal.md / evidence_map.md / schedule.mmd / preview.svg。
- [ ] 前 3 分钟内已落盘第一版 partial 骨架（`init`），后续为增量改进而非最后一次性重写。
- [ ] `change_log` 记录关键动作，能还原“从骨架到成品”的过程。

## C. 创新增益（新增能力 / 方法改进）

- [ ] 要素对齐已机器校验：目标→问题、路线→目标/问题、成果→目标、验证→问题的 `ref` 可回查（`check` 捕获失配 FAIL）。
- [ ] 计划可行性已机器校验：里程碑起止月不超 `constraints.cycle`（`check` 捕获 FAIL）。
- [ ] 证据以结构化 `{id, locator, quote, claim_refs}` 对象挂接，可逐条回溯，非口头声称。
- [ ] 产出自包含 SVG 预览 + Mermaid 甘特图，无需额外软件即可检查布局与排期可读性。

## D. 可靠体验（易用 / 稳定 / 异常处理）

- [ ] 脚本仅依赖 Python 标准库，路径为相对路径 `scripts/proposal.py`，可重复运行、幂等、无随机。
- [ ] 模糊请求下先索取课题指南、周期、经费与数据条件，不臆造；缺失项已写入 `uncertainty`/`pending_human`。
- [ ] 空输入/坏 JSON/坏编码/缺文件不崩溃，`init` 仍落盘合法骨架；跨学科、跨篇幅重复运行要素不漂移。
- [ ] 材料缺失/周期过短/指标不可达/引用无法获取时已如实标注，`status` 如实降级，未编造。

## E. 生态贡献（文档 / 复用 / 分享）

- [ ] `SKILL.md` 写清角色、输入输出契约、执行流程、时间预算（900s/64 请求）、自检与降级。
- [ ] `references/output-contract.md` 定义字段、七要素与词表，他人可据此复用。
- [ ] `scripts/selftest.py` 可独立运行验证健壮性与确定性；最小调用示例已给出且标注为示意输出。
