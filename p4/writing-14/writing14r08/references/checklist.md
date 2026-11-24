# 自检清单（交付前逐条勾选，对应评分维度）

评分维度：实际效果 40、使用增益 25、创新增益 15、可靠体验 10、生态贡献 10。
每个维度映射到可勾选自检项；FAIL 级必须修复，WARN 级写入 `uncertainty` 或正文说明。

## A. 实际效果（正确性 / 完整性 / 可核验）

- [ ] `result.json` 合法、可被 JSON 解析，含 `case_id` 与题目 `output_contract` 全部字段（`check` 无 FAIL）。
- [ ] 七要素齐备：背景/问题/目标/技术路线/阶段计划/风险/预期成果均非空或已标 missing。
- [ ] 立得住：研究问题 ≤3 个、每个有 `falsifiable_criterion`；背景 known→unknown→gap 指向同一缺口，不是文献罗列。
- [ ] 对得上：目标 `answer_to` 指向存在的问题；路线 `depends_on` 无悬挂；预期成果有考核方式且与目标对应。
- [ ] 排得开：阶段 `period` 落在 `constraints.period_months` 内，里程碑与资源匹配，无起止倒置。
- [ ] 说得清：每条风险有 trigger/impact/mitigation/decision_point，类别在规范词表内。
- [ ] 事实忠实材料：无新增材料外事实、无张冠李戴数字/结论；fact/summary/inference/conflict 区分正确。
- [ ] 证据可回溯：关键论断与 `evidence_map` 逐条带 locator 或 quote，能在 `sources` 清单找到。
- [ ] `artifacts` 非空且指向真实存在的可编辑文件与预览。

## B. 使用增益（质量 / 效率 / 可验证）

- [ ] 已运行 `python scripts/main.py check`，FAIL=0，WARN 已说明或修复。
- [ ] 已运行 `python scripts/main.py render`，生成 proposal.md / proposal_plan.mmd / proposal_plan.svg。
- [ ] 前 3 分钟已落盘第一版骨架（`init`），后续为增量改进而非最后一次性重写。
- [ ] `change_log` 记录关键动作，能还原"从骨架到成品"过程；两轮相同输入结果一致（确定性）。

## C. 创新增益（新增能力 / 方法改进）

- [ ] 约束抽取：`constraints`（周期/经费/数据/人员）从 task.md 与 case.json 显式抽取，而非默写。
- [ ] 要素对齐：目标↔问题、路线↔目标、成果↔目标之间有显式引用字段（answer_to / depends_on），而非自由文本。
- [ ] 可行性校验：阶段周期与总周期比对、风险四字段完整性，可脚本化复现（见 core_impl.check）。
- [ ] 自包含 SVG 预览，无需额外软件即可检查阶段布局；Mermaid 呈现路线依赖。

## D. 可靠体验（易用 / 稳定 / 异常处理）

- [ ] 脚本仅依赖 Python 标准库；调用为相对路径 `scripts/main.py`，`python`/`python3` 均可，可重复运行。
- [ ] 空输入/坏 JSON/坏编码：`init` 仍写合法 partial 骨架，错误记入 `degradations`，进程不崩溃。
- [ ] 材料缺失/格式不支持/信息矛盾/周期过短/指标不可达时，写入 `uncertainty`/`pending_human`/`degradations`，未编造。
- [ ] 权限不足或软件缺失时，`status` 如实降级为 partial/failed，并说明缺口。
- [ ] 模糊请求下先索要课题指南、周期、经费与数据条件，再开始撰写。

## E. 生态贡献（文档 / 复用 / 分享）

- [ ] `SKILL.md` 写清角色、输入输出契约、执行流程、时间预算（900s/64 请求）、自检与降级。
- [ ] `references/output-contract.md` 定义字段与词表，他人可据此复用与二次校验。
- [ ] 最小调用示例已给出，且明确标注为示例（非正式案例结果）。
- [ ] 文件数 ≤14、脚本标准库、体积与运行环境要求均已说明；不含预写正式答案。
