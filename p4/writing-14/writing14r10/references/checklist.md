# 自检清单（交付前逐条勾选）

对应评分维度：实际效果 40、使用增益 25、创新增益 15、可靠体验 10、生态贡献 10。
FAIL 级必须修复；WARN 级写入 `uncertainty` 或正文说明。

## A. 实际效果（正确性 / 完整性 / 可核验）

- [ ] `result.json` 合法可解析，含 `case_id` 与题目 `output_contract` 全部字段（缺值
      给 `null`/`[]`，不改名不删键）。
- [ ] 七要素齐全且非空：背景缺口、问题、目标、技术路线、阶段计划、风险、预期成果。
- [ ] 事实忠于材料：无凭空术语、无张冠李戴的数字/结论/引用。
- [ ] 论断与引用一一对应：`citations` 覆盖关键论断，数字/术语/结论可逐条回溯。
- [ ] 每个 `fact`/`summary` 节点与要素的 `sources` 能在顶层 `sources` 找到，且带
      `locator` 或逐字 `quote`。
- [ ] `fact`/`summary`/`inference`/`conflict`/`missing` 区分正确；推断项已列 `uncertainty`。
- [ ] `artifacts` 非空且指向真实存在的可编辑文件与预览（`render` 已通过）。

## B. 使用增益（质量 / 效率 / 可验证）

- [ ] 已运行 `python scripts/main.py validate`，FAIL=0（WARN 已在正文/`uncertainty` 说明）。
- [ ] 已运行 `python scripts/main.py render`，生成 `proposal.md`/`evidence_map.md`/`alignment.md`。
- [ ] 前 3 分钟已落盘第一版 `partial` 骨架（`init`），后续为增量改进而非最后一次性重写。
- [ ] `change_log` 记录关键动作，可还原"从骨架到成品"的增量过程。

## C. 创新增益（新增能力 / 方法改进）

- [ ] 中间表示 `spec.json`/`result.json` 先于正文落盘，约束、七要素、对齐矩阵、证据映射同源。
- [ ] 对齐矩阵（`alignment.matrix` 或 `question_ref`/`objective_ref`）证明"对得上"，
      而非各写各的。
- [ ] 计划可行性校验（里程碑 vs 周期/预算/人员）证明"排得开"，越界即 WARN。
- [ ] 风险按四类（数据/方法/进度/伦理合规）分类，每条含触发信号、影响、备选、决策时点。
- [ ] 证据以结构化 source 对象（locator/quote/kind）挂接，可逐条回溯。

## D. 可靠体验（易用 / 稳定 / 异常处理）

- [ ] 脚本仅依赖 Python 标准库；相对路径 `scripts/main.py` 可重复运行、确定性输出。
- [ ] 空输入目录、坏 JSON、坏编码均降级为合法结果（`insufficient_evidence`/`partial`），不抛异常。
- [ ] 材料缺失/周期过短/指标不可达/学科规范冲突/引用无法获取时，如实写
      `limitations`/`uncertainty`，未编造。
- [ ] 权限不足或软件缺失时，`status` 如实降级并说明缺口。

## E. 生态贡献（文档 / 复用 / 分享）

- [ ] `SKILL.md` 写清角色、输入输出契约、执行流程、时间预算、自检与降级。
- [ ] `references/output-contract.md` 定义字段与词表，他人可据此复用。
- [ ] `scripts/selftest.py` 覆盖空输入/坏 JSON/正常流程/重复运行，可独立复核。
- [ ] 最小调用示例已给出并标注"示意输出，非正式案例结果"。

## 快速验证命令

```bash
python3 -B scripts/main.py discover --input /input
python3 -B scripts/main.py init --input /input --out /app/output --work /app/work
python3 -B scripts/main.py validate --input /input --out /app/output --work /app/work
python3 -B scripts/main.py merge --input /input --out /app/output --work /app/work
python3 -B scripts/selftest.py
```
