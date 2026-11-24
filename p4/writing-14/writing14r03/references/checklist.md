# 自检清单（交付前逐条勾选）

对应评分维度：实际效果（40）、使用增益（25）、创新增益（15）、可靠体验（10）、生态贡献（10）。
每一项在交付前核对；FAIL 级必须修复，WARN 级写入 `uncertainty` 或正文说明。

## A. 实际效果（正确性 / 完整性 / 可核验）

- [ ] `result.json` 合法、可被 JSON 解析，含 `case_id` 与题目 `output_contract` 全部字段（原样回填键名）。
- [ ] 七要素齐全且非空：`background` / `problems` / `objectives` / `methods` / `plan` / `risks` / `expected_outcomes`。
- [ ] 立得住：`problems` 为 1–3 个可证伪问题，每条含 `question` + `falsifiable` + `gap_link`；`background.gap` 具体指向待验证问题而非综述。
- [ ] 对得上：每个 `objective` 有 `problem_ref` 且指向存在的 `problems.id`；`criteria` 含指标/样本/对照/时间点；`methods` 每步有 `step`/`input`/`output`/`depends_on`。
- [ ] 排得开：`plan` 含 `period`/`budget`/`milestones`；里程碑落在给定周期、经费、数据与人员条件内；关键路径与可并行步骤已标。
- [ ] 说得清：`risks` 每条含 `category`/`trigger`/`impact`/`alternative`/`decision_point`，覆盖数据获取/方法可行性/进度/伦理与合规/资源；`expected_outcomes` 每条含 `outcome`+`verification`。
- [ ] 论断与引用一一对应：`references` 每条有 `claim` ↔ `source_id`；每个数字/术语/关键论断可回溯。
- [ ] 证据可回溯：每个 source 有 `id` + `locator` + 逐字 `quote`；`fact`/`summary`/`inference` 区分正确，`inference` 已列入 `uncertainty`。
- [ ] `artifacts` 非空且指向真实存在的可编辑文件与预览；边界（`boundaries`）已写明。

## B. 使用增益（质量 / 效率 / 可验证）

- [ ] 已运行 `python scripts/main.py check`，FAIL=0（WARN 已说明或写入 uncertainty）。
- [ ] 已运行 `python scripts/main.py render`，生成 `proposal.md` + `evidence.md`。
- [ ] 前 3 分钟内已落盘 `status=partial` 骨架（`init`），后续为增量改进而非最后一次性重写。
- [ ] `change_log` 记录了关键动作，能还原"从骨架到成品"的过程。

## C. 创新增益（新增能力 / 方法改进）

- [ ] 七要素有严格子字段与词表（风险分类、source 结构），非自由文本堆砌。
- [ ] 目标↔问题、方法步骤↔依赖、里程碑↔周期经费、风险↔触发/备选 的对应关系可机读核验。
- [ ] 证据以结构化 source 对象挂接（locator + 逐字 quote + kind + claim），可逐条回溯。
- [ ] `scripts/selftest.py` 可复跑，验证标准库限定、契约往返与确定性渲染；同输入两次产物一致。

## D. 可靠体验（易用 / 稳定 / 异常处理）

- [ ] 脚本仅依赖 Python 标准库，用相对路径 `scripts/main.py` 调用，可重复运行。
- [ ] 材料缺失/格式不支持/信息矛盾/指标不可达时，已写入 `limitations`/`uncertainty`/`pending_human`，未编造。
- [ ] 权限不足或软件缺失时，`status` 如实降级为 `partial`/`failed`，并在 `change_log` 说明缺口。
- [ ] 不同学科/篇幅/重复运行下，目标数量与周期经费口径前后一致、要素不漂移。

## E. 生态贡献（文档 / 复用 / 分享）

- [ ] `SKILL.md` 写清角色、输入输出契约、执行流程、时间预算、自检与降级。
- [ ] `references/output-contract.md` 定义字段与词表，他人可据此复用。
- [ ] 最小调用示例已在说明中给出，且标注为示意输出。
