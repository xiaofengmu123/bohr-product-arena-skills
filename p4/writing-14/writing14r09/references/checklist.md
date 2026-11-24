# 自检清单（references/checklist.md）

交付前逐条勾选；对应题目评分维度：实际效果（40）、使用增益（25）、创新增益（15）、可靠体验（10）、生态贡献（10）。FAIL 级必须修复，WARN 级写入 `uncertainty` 或正文说明。

## A. 实际效果（正确性 / 完整性 / 可核验）

- [ ] `result.json` 合法可解析，含 `case_id` 与题目 `output_contract` 全部字段 + 信封字段。
- [ ] 七要素齐全，忠于材料：无凭空术语、无张冠李戴的数字/结论/引用。
- [ ] 研究问题具体、可证伪（`falsifiable_test` 已写）；背景真指向缺口，非文献罗列。
- [ ] 目标/技术路线/验证/预期成果与问题一一对应（`objectives[].question_id` 可回溯）。
- [ ] 阶段计划落在给定周期/经费/数据/人员内；里程碑与任务量匹配，关键路径与依赖已标。
- [ ] 风险覆盖数据/方法/进度/伦理四类，每条含触发信号、影响范围、备选方案、决策时点。
- [ ] 每个事实/数字/引用/关键论断可定位到 `sources`（locator + quote），评分人可按记录复跑。

## B. 使用增益（质量 / 效率 / 可验证）

- [ ] 已运行 `python scripts/main.py check`，FAIL=0（WARN 已说明）。
- [ ] 已运行 `python scripts/main.py render`，生成 proposal.md / evidence_map.md，artifacts 指向真实文件。
- [ ] 前 3 分钟已落盘 partial 骨架，后续为增量改进而非最后一次性重写。
- [ ] `change_log` 记录关键动作，可还原"从骨架到成品"的过程。

## C. 创新增益（新增能力 / 方法改进）

- [ ] 七要素结构化 `elements`（而非自由文本堆砌），可机器校验"对得上/排得开/说得清"。
- [ ] 证据以结构化 `source` 对象挂接（locator/quote/claims/kind），可逐条回溯。
- [ ] `check` 自动校验：问题数量 1–3、目标指向问题、风险四要素、计划约束、sources 引用悬空。
- [ ] 可编辑 Markdown + evidence_map 同步产出，降低大纲重写与材料核对成本。

## D. 可靠体验（易用 / 稳定 / 异常处理）

- [ ] 脚本仅依赖 Python 标准库，相对路径 `scripts/main.py`，可重复运行、幂等。
- [ ] 空输入/坏 JSON/坏编码/缺文件不崩溃，已写 `uncertainty`（missing/input_error）。
- [ ] 材料冲突、周期过短、指标不可达、引用无法获取时已识别并写 pending_human/uncertainty，未编造。
- [ ] 权限不足或软件缺失时，`status` 如实降级为 partial/failed，并说明缺口。

## E. 生态贡献（文档 / 复用 / 分享）

- [ ] `SKILL.md` 写清角色、输入输出契约、执行流程、时间预算、自检与降级。
- [ ] `references/contract.md` 定义字段与词表，他人可据此复用。
- [ ] `references/checklist.md` 提供可勾选自检，最小调用示例已给出且标注为示意输出。
- [ ] 未编造评测分数、软件状态或科学数据；不提交隐藏测试或白板。
