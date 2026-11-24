# 自检清单（交付前逐条勾选）

对应评分维度：实际效果（40）、使用增益（25）、创新增益（15）、可靠体验（10）、生态贡献（10）。
FAIL 级必须修复；WARN 级应写入 `uncertainty` 或正文说明。完整判定逻辑见 `scripts/main.py check`。

## A. 实际效果（正确性 / 完整性 / 可核验）

- [ ] `result.json` 合法可解析，含 `case_id` 与题目 `output_contract` 全部字段（未知字段不改名、不删除）。
- [ ] 七要素齐全：背景缺口、研究问题、目标、技术路线、阶段计划、风险、预期成果。
- [ ] 内容忠于材料原文：无凭空术语、无张冠李戴的数字/结论；不新增材料外事实。
- [ ] 立得住：`gap_statement` 由 known/unknown/to_verify 推出，指向明确缺口。
- [ ] 对得上：`objective.linked_question`、`expected_outcome.linked_objective`、`technical_route.depends_on` 全部可解析、无悬空引用。
- [ ] 排得开：`phase_plan` 有 duration/milestones/resources，与 `constraints` 周期/经费/人员相容。
- [ ] 说得清：每条风险有 trigger_signal/impact/fallback/decision_point；关键论断与引用一一对应。
- [ ] 每个 fact/summary 项的 `sources` 均能在 `sources` 清单找到，且带 `locator` 或 `quote`。
- [ ] `kind` 三分类（fact/summary/inference）区分正确；推断项已列入 `uncertainty`。
- [ ] `artifacts` 非空且指向真实存在的可编辑文件与预览。

## B. 使用增益（质量 / 效率 / 可验证）

- [ ] 已运行 `python scripts/main.py check`，FAIL=0（WARN 已说明）。
- [ ] 已运行 `python scripts/main.py render`，生成 proposal.md / evidence_map.json / proposal_preview.html。
- [ ] 前 3 分钟内已落盘第一版骨架（`init`），后续为 `update` 增量改进而非最后一次性重写。
- [ ] `change_log` 记录了 init→update→render 的完整过程，可还原从骨架到成品的路径。
- [ ] 重复运行结果一致（无随机源，render/check 幂等）。

## C. 创新增益（新增能力 / 方法改进）

- [ ] 七要素为**结构化对象**（每项有 id/kind/sources），而非自由文本堆砌。
- [ ] 四问自检（立得住/对得上/排得开/说得清）落地为可执行的 `check` 规则，而非口头声称。
- [ ] 证据以 `sources` + `evidence_map` 双结构挂接（locator/quote），可逐条回溯复跑。
- [ ] 约束抽取（period/budget/personnel/data）进入 `constraints` 并被可行性校验使用。
- [ ] 产出自包含 HTML 预览，无需额外软件即可检查排版与来源标注。

## D. 可靠体验（易用 / 稳定 / 异常处理）

- [ ] 脚本仅依赖 Python 标准库，路径为相对路径 `scripts/main.py`，可重复运行、幂等。
- [ ] 空输入、坏 JSON、坏编码、超限文件均不崩溃：写合法 `input_error`/`partial` 结果并记录 `errors`。
- [ ] 材料缺失/格式不支持/信息矛盾时，已写入 `limitations` 或 `uncertainty`，未编造。
- [ ] 存在 pending_human 或缺口时，`status` 如实降级（不标 complete）。
- [ ] 权限不足或软件缺失时，`status` 如实降级为 `partial`/`failed`，并说明缺口。

## E. 生态贡献（文档 / 复用 / 分享）

- [ ] `SKILL.md` 写清角色、输入输出契约、执行流程、时间预算、自检与降级。
- [ ] `references/output-contract.md` 定义字段与词表，他人可据此复用。
- [ ] `references/checklist.md` 提供逐条可勾选清单与 FAIL/WARN 语义。
- [ ] 最小调用示例已在 SKILL.md 给出，且标注为示意输出。
- [ ] `scripts/selftest.py` 可独立运行验证四步链路与异常路径。
