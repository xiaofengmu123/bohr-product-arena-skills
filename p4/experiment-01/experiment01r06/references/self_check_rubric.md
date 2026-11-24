# 自检清单与评分维度对照

本文件把题目评分维度（实际效果 40、使用增益 25、创新增益 15、可靠体验 10、生态贡献 10）映射为可在 IR / result.json / checks.json 上逐条核对的检查项。每个检查项给出“常见失败”与“修复动作”。

## 1. 实际效果（40 分）

### 1.1 任务完成度（15）

- [ ] `goal.testable_question` 非空，且不是对目标的复述。
- [ ] 六要素 + 判读标准均有非空节点：假设与变量、对照组、设备材料、时间成本、参数范围、风险异常、判读标准。
- [ ] 每个变量有操作定义与单位/量程；每个步骤绑设备/试剂/时长与验收判据；每个参数有值与区间。
- 常见失败：`hypotheses=[]`、变量只有名字、步骤写成“准备—检测—分析”且无时长、参数无区间。
- 修复：按 IR 节点表逐项补齐，`python3 scripts/main.py todo` 列出缺口。

### 1.2 结果正确性（15）

- [ ] 假设 `falsifiable_by` 具体可证伪。
- [ ] 变量角色与测量方式匹配（因变量能用所述仪器/单位测出）。
- [ ] 每个对照 `excludes_confounder` 写明排除哪类混杂，且确有阳性/阴性/空白之一，不只一组空白。
- [ ] 参数 `value` 在 `allowed` 区间内（`checks.json.arithmetic.parameters` 的 `in_range` 为 true）。
- [ ] 证据 `quote` 回查通过（`evidence.status=verified`），引用与规格真实。
- 常见失败：`parameter_out_of_range`、`evidence_quote_not_found`、对照只有空白组。
- 修复：回原文摘录逐字 quote；对照补阳性对照；参数值改到区间内或给出依据。

### 1.3 结果完整性（5）

- [ ] `checks_summary.coverage` 中 7 项 `present` 均为 true。
- [ ] 预算有 `cost_total` 与逐项 `cost_items`；排期有 `wall_clock_minutes`。
- [ ] 确认项覆盖安全/伦理/设备可用性至少一类。

### 1.4 结果可核验性（5）

- [ ] `result.json.design_ir.sha256` 与 `/app/output/plan.ir.json`（去掉 `_meta` 后）摘要一致。
- [ ] 每条结论可经 `节点ID → evidence → locator+quote → 原文` 回溯。
- [ ] `change_log` 记录骨架落盘、审计、渲染、修复等事件。
- [ ] `status` 与校验计数一致，不虚报 ok。

## 2. 使用增益（25 分）

- [ ] 质量增益：相比“通用流程”，本 Skill 强制六要素落到操作定义、单位、量程与判据（由结构校验保证）。
- [ ] 效率增益：`scaffold` 一次落盘骨架，避免 Agent 从头写空模板；`todo` 列出缺口，减少返工轮次。
- [ ] 增益可验证：`scripts/selftest.py` 的 demo 场景证明“同一输入 → 契约完整、算术通过、证据回查通过”。
- 常见失败：模板化空话、无算术依据。修复：让 `build` 的 error/warn 驱动内容，而不是事后堆文字。

## 3. 创新增益（15 分）

- [ ] 新增能力：按设备量程与预算裁剪参数（`parameter_out_of_range`/`budget_cap_exceeded` 主动拦截）。
- [ ] 方法改进：以 IR 为唯一事实源渲染 result/plan，正文与 JSON 一致；候选 feasibility 由算术判定。
- [ ] 创新证据：`checks.json.arithmetic` 与 `candidates` 数组是机器可复算的创新证据，优于文字自述。
- 常见失败：只有提示词没有结构校验。修复：让脚本承担“可复核的算术”，提示词只负责“科学翻译”。

## 4. 可靠体验（10 分）

- [ ] 调用易用：`scripts/main.py all` 一条命令出全部产物；`--help` 退出码 0。
- [ ] 运行稳定：空输入、坏 JSON、坏编码都不崩溃，`selftest.py` 覆盖。
- [ ] 异常处理：
  - [ ] 输入缺失 → `insufficient_evidence` + `pending_human`；
  - [ ] 约束冲突（时间窗 vs 样本量）→ `time_window_exceeded`/`run_count_mismatch` + `uncertainty`；
  - [ ] 依赖不可用（试剂缺货/排期满）→ `unavailable_resources` 匹配 + 替代路径；
  - [ ] 内容不确定 → `uncertainty` + `selection_source=unknown`。
- 常见失败：`pdftotext` 缺失直接崩；坏 JSON 中断。修复：已在 probe 中降级并记 `degradations`。

## 5. 生态贡献（10 分）

- [ ] 文档完整：SKILL.md 写清用途、输入输出、调用、限制；references 有 schema 与 rubric。
- [ ] 资源可复用：IR 节点表、契约映射、算术公式、自检脚本可被他人直接复用。
- [ ] 分享价值：`plan.md` 可作为可编辑方案模板；`checks.json` 可作评审锚点。

## 6. 交付前终检

```bash
cd /skills/weekly-skill
python3 scripts/main.py --help                 # 退出码 0
python3 scripts/main.py all --input /input --output /app/output --work /app/work
python3 scripts/main.py validate --input /input --output /app/output --work /app/work
python3 scripts/selftest.py
```

通过标准：`validate` 退出码 0；`result.json` 含 case_id、全部契约键、status/sources/uncertainty/pending_human/change_log；`checks_summary.errors==0`（允许警告）；证据回查 `verified` 比例 100% 或缺失来源已如实标注。
