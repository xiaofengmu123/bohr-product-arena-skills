# 自检清单（交付前逐条勾选）

对应评分维度：实际效果（40）、使用增益（25）、创新增益（15）、可靠体验（10）、
生态贡献（10）。FAIL 级必须修复；WARN 级应写入 `uncertainty` 或正文说明。

## A. 实际效果（正确性 / 完整性 / 可核验）

- [ ] `result.json` 合法可解析，含 `case_id` 与 `output_contract` 全部字段。
- [ ] 七要素齐全：研究背景与缺口、问题定义、目标、技术路线、阶段计划、风险、预期成果。
- [ ] 问题定义 1–3 个、每个可证伪，背景"已知—未知—待验证"确实指向该缺口（立得住）。
- [ ] 目标/路线/验证/成果与问题一一对应，指标阈值有依据（对得上）。
- [ ] 阶段计划落在给定周期/经费/数据/人员内，里程碑与任务量匹配（排得开）。
- [ ] 每条风险有触发信号、影响、备选方案、决策时点；论断与引用一一对应（说得清）。
- [ ] 每个关键数字/术语/引用/图表都有 `evidence_map` 条目，带 locator 或逐字 quote。
- [ ] 无材料外事实；`fact/summary/inference` 区分正确，推断项已列 `uncertainty`。
- [ ] `artifacts` 非空且指向真实文件（proposal.md / evidence_map.md / checks.md / proposal.docx）。

## B. 使用增益（质量 / 效率 / 可验证）

- [ ] 已运行 `python scripts/main.py check`，FAIL=0（WARN 已说明）。
- [ ] 已运行 `python scripts/main.py build`，产物齐备且可编辑。
- [ ] 前 3 分钟内已运行 `init` 落盘 partial 骨架，之后为增量改进。
- [ ] `change_log` 能还原"从骨架到成品"的过程。

## C. 创新增益（新增能力 / 方法改进）

- [ ] 从课题指南/任务卡抽取了 `constraints`（周期/经费/数据/人员/格式）。
- [ ] 用 `four_checks` 做四问校验（立得住/对得上/排得开/说得清），而非自由文本。
- [ ] `evidence_map` 每条带结构化 locator + quote，可逐条回溯，非口头声称。
- [ ] 用标准库产出真实 `.docx`（OOXML），可继续编辑，而非仅 Markdown。

## D. 可靠体验（易用 / 稳定 / 异常处理）

- [ ] 脚本仅依赖 Python 标准库，路径为相对路径 `scripts/main.py`，可重复运行。
- [ ] 材料缺失/格式不支持/信息矛盾/节点爆炸时，已写入 `limitations` 或 `uncertainty`。
- [ ] 权限不足或软件缺失时，`status` 如实降级为 `partial`/`failed`，未编造。
- [ ] 空输入/坏 JSON/坏编码/缺参数均能降级不崩溃（`selftest` 已覆盖）。

## E. 生态贡献（文档 / 复用 / 分享）

- [ ] `SKILL.md` 写清角色、输入输出契约、执行流程、时间预算、自检与降级。
- [ ] `references/output-contract.md` 定义字段与词表，他人可复用。
- [ ] `references/checklist.md` 提供可勾选自检；最小调用示例已标注为示意。
- [ ] `scripts/selftest.py` 可独立运行（退出码 0 表示脚本自身健康）。

## 交付前最终确认

```bash
python scripts/main.py selftest     # 必须退出码 0
python scripts/main.py check        # 必须 FAIL=0
python scripts/main.py build        # 产物齐全
```
