# 自检清单（交付前逐条勾选）

对应评分维度：实际效果（40）、使用增益（25）、创新增益（15）、可靠体验（10）、
生态贡献（10）。FAIL 级必须修复；WARN 级写入 `uncertainty` 或正文说明。
逐条核对后运行 `python scripts/main.py check` 确认 FAIL=0。

## A. 实际效果（正确性 / 完整性 / 可核验）

- [ ] `result.json` 合法、可被 JSON 解析，含 `case_id` 与题目 `output_contract` 全部字段。
- [ ] 七要素齐全：研究背景、研究问题、目标、技术路线、阶段计划、风险、预期成果。
- [ ] 事实、数据、引用忠于材料原文：无凭空术语、无张冠李戴的数字/结论。
- [ ] 指标阈值有依据（`metrics[].basis` 指向材料），不写无据的达标线。
- [ ] 论断与引用一一对应：每个关键论断的 `sources` 都能在 sources 清单找到，且带 locator 或逐字 quote。
- [ ] `fact` / `summary` / `inference` 区分正确；`inference` 已同时写入 `uncertainty`。
- [ ] 目标、技术路线、验证方式、预期成果与问题一一对应（`problem_ref` 无悬空引用）。
- [ ] 阶段计划落在给定周期、经费、数据与人员条件内；里程碑与任务量匹配；关键路径标注。
- [ ] 风险按四类至少各检一次，每条有触发信号、影响、备选方案与决策时点。
- [ ] `artifacts` 非空且指向真实存在的可编辑文件与预览（`check`/`render` 已通过）。

## B. 使用增益（质量 / 效率 / 可验证）

- [ ] 已运行 `python scripts/main.py init`，前 3 分钟内落盘 partial 骨架（而非最后一次性重写）。
- [ ] 后续用 `merge` 增量回填；`change_log` 记录“骨架 → 成品”的每次动作。
- [ ] 已运行 `python scripts/main.py check`，FAIL=0（WARN 已说明）。
- [ ] 已运行 `python scripts/main.py render`，生成 proposal.md / evidence_map.json / route.mmd / route.svg。
- [ ] 相同条件下骨架与自检可复现（重复运行结果一致，除时间戳）。

## C. 创新增益（新增能力 / 方法改进）

- [ ] 七要素采用结构化对象（非自由文本堆砌），字段与词表见 `references/output-contract.md`。
- [ ] “对得上”由 `problem_ref` 显式表达，并由 `check` 检测悬空引用（基础 Agent 难以稳定做到）。
- [ ] “排得开”由里程碑 + 关键路径 + 可行性说明表达，`check` 提示周期/资源缺失。
- [ ] “说得清”由逐字 `quote` + `locator` 的 source 对象挂接，可逐条回溯。
- [ ] 技术路线产出自包含 SVG 预览（标准库产出，无需额外软件即可检查布局）。

## D. 可靠体验（易用 / 稳定 / 异常处理）

- [ ] 脚本仅依赖 Python 标准库；相对路径 `scripts/main.py` 调用；可重复运行。
- [ ] `python scripts/selftest.py` 通过（空输入、坏 JSON、缺参数、超大文件、缺 result 均不崩溃）。
- [ ] 空输入目录 → 退出码 0，写 `insufficient_evidence` 并列出缺口，不抛异常。
- [ ] 坏 JSON / 坏编码 → 捕获并记录到 `errors`/`degradations`，继续处理其余材料。
- [ ] 超大文件（>2MB）→ 跳过并记录 `oversized`，不因单文件撑爆内存。
- [ ] 权限不足或写盘失败 → 降级到最小合法产物，不崩溃；`status` 如实降级。
- [ ] 材料缺失/格式不支持/信息矛盾/节点爆炸时，已写入 `uncertainty` 或 `limitations`，未编造。

## E. 生态贡献（文档 / 复用 / 分享）

- [ ] `SKILL.md` 写清角色、输入输出契约、执行流程、时间预算、自检与降级。
- [ ] `references/output-contract.md` 定义字段、七要素结构与词表，他人可据此复用。
- [ ] 最小调用示例在 SKILL.md 中给出，且标注为示意输出（不冒充正式案例结果）。
- [ ] 未编造评测分数、软件状态或科学数据。
