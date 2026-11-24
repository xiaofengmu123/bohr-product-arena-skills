---
name: experiment-plan-ir
description: 以结构化设计中间表示（plan.ir.json）驱动实验方案生成、候选比较与机器复核：把研究目标拆成可证伪假设与可测量变量，配对对照、步骤、参数区间、设备材料、排期预算、判读标准与风险替代路径，每条关键依据绑定 locator+逐字 quote，经算术与证据回查后渲染 result.json 与 plan.md。当需要把科研目标转成可排期、可执行的实验方案，或在多个候选方案间按设备量程、预算与时间窗裁剪、比较、定稿时使用。
version: 1.0.0
---

# experiment-plan-ir：中间表示驱动的实验方案生成与复核

## 1. 角色与何时使用

本 Skill 面向“实验方案生成与优化”：输入研究目标、科研材料与资源约束（常含 2–3 个版本化候选方案），输出一份可排期、可执行、可复核的实验方案及候选比较、依据、风险与确认项。

适用：把目标落成可检验假设与操作变量；在有限设备量程、预算、时间窗与试剂可得性下裁剪参数；比较多个候选方案并给选择理由；对已有方案做机器复核。

不适用：文献综述、数据分析建模、论文写作；不替代安全与伦理审批——未批准的项一律列为确认项，不写成已确认事实。

三条硬原则：
1. 只把 `/input` 中真实存在的材料当事实；数值、型号、价格、批准缺失时写 `null` 并列待确认，不补造。
2. 方案以结构化中间表示 `plan.ir.json` 为唯一事实源；`result.json` 与 `plan.md` 由脚本从该表示渲染，避免正文与字段不一致。
3. 每个关键结论绑定 `locator` + 逐字 `quote`；构建时逐条回查原文，查不到的标 `not_found`，不作为已验证依据。

## 2. 输入契约（运行时）

- `/input/case.json`：案例主文件，可能含 `output_contract`、研究目标、材料索引、候选方案、资源/价格/排期约束。
- `/input/task.md`：任务说明。
- `/input/materials/` 及子目录：论文、协议、手册、报价单等；PDF 为原始文件，无预抽取文本层。

读取规则：不假设固定文件名/键名，先扫描后判断；文本按 `utf-8-sig → utf-8 → gb18030` 依次尝试，坏 JSON 记 `errors` 后继续；单文件 >2 MB 跳过并记 `degradations`；PDF 用 `pdftotext -layout`，不可用时降级为材料文本字段。工作目录 `/app/work`，最终产物 `/app/output`。

## 3. 输出契约

必产 `/app/output/result.json`，并同步产出 `plan.ir.json`（结构化中间表示，唯一事实源）、`plan.md`（由 IR 渲染的人读方案）、`checks.json`（全部校验与算术明细）。

`result.json` 顶层：
1. `case_id`：依次取 case.json 的 `case_id`/`id`/`caseId`/`challenge_id`，缺失取 task.md 的 `challenge_id`，仍无写 `"unknown"` 并列 `pending_human`。
2. `output_contract` 的全部键：case.json 有契约时逐键回填到顶层（不改名、不删未知键、缺值 `null`/`[]`）；无契约时用 fallback 三块 `plan`、`evidence_and_risks`、`confirmations`（与题面输出要求一致）。
3. 信封字段：`status`（`ok|partial|insufficient_evidence|input_error`）、`sources`、`uncertainty`、`pending_human`、`change_log`、`errors`、`degradations`、`limitations`、`runtime`、`artifacts`、`checks_summary`、`design_ir`。
4. 同名冲突时信封字段优先，契约原值保留在 `output_contract` 镜像块中。

`status` 由脚本按校验结果计算，不自报：全部硬检查通过且无阻塞确认 → `ok`；有错误/警告/阻塞确认 → `partial`；无任何科学节点 → `insufficient_evidence`；输入空或 case.json 无效且无其他材料 → `insufficient_evidence`/`input_error`。

## 4. 结构化中间表示（plan.ir.json，本版核心设计）

IR 是带稳定 ID 的节点图，`plan.md` 与 `result.json` 都从它渲染，保证一致可复核：

| 节点键 | 前缀 | 必填要点 |
|-|-|-|
| `hypotheses` | H | `statement` + `falsifiable_by` |
| `variables` | V | `role` + `operational_definition` + `measurement.{method,unit}` + 量程或水平 |
| `controls` | C | `type` + `excludes_confounder`（排除哪类混杂） |
| `steps` | S | `duration_min` + `depends_on` + 设备/试剂 + `acceptance` |
| `parameters` | P | `value`/`unit`/`allowed` 区间 + `source` |
| `resources` | R | `kind`/`spec`/量程 + 可替代性 |
| `readout_criteria` | RC | 指标/阈值/决策 + `linked_variable` |
| `risks` | RSK | 失败模式/检测判据/止损点/替代路径 |
| `confirmations` | CF | 安全/伦理/设备/专家确认，状态 `pending|confirmed|...` |
| `evidence` | E | `locator` + 逐字 `quote`，可选 `metric`/`value` |

节点用 `evidence` 引用证据；候选方案在 `candidates` + `selection` 比较；约束在 `constraints`。字段语义详见 `references/plan_ir_schema.md`。

## 5. 执行流程（总 900 s / 64 次模型请求 / 单响应 ≤16384 tokens）

| 步 | 预算 | 请求 | 动作 |
|-|-|-|-|
| 1 输入发现 | ≤60s | ≤3 | `probe`：扫描 `/input`，定 case_id、output_contract、材料与候选清单 |
| 2 骨架落盘 | ≤120s | ≤3 | `scaffold`：写 partial `result.json`+`plan.ir.json` 骨架+`plan.md` |
| 3 填充 IR | ≤240s | ≤18 | 读材料原文，把目标→假设→变量→对照→步骤→参数→资源→风险逐项写入 `plan.ir.json`，逐条带 locator+quote |
| 4 候选比较 | ≤180s | ≤15 | 对 2–3 个候选做量程/预算/时间窗算术校验，选一个并写拒绝理由 |
| 5 构建自检 | ≤180s | ≤15 | `build` 渲染产物；`validate` 复核；按报告增量修复，追加 `change_log` |
| 6 收尾 | ≤60s | ≤5 | 重跑 `validate`，确认 status 诚实、sources/uncertainty/pending_human 完整 |

**硬规则：前 3 分钟内必须由 `scaffold` 落盘一版合法 partial 结果**（含全部契约键与骨架），此后任何时刻 `/app/output/result.json` 都不得缺失/损坏，只做增量改进。每次大改动原子写回（写 `.tmp` 再 `os.replace`）；`change_log` 记 `{seq, ts, event, detail}`；重复运行一致（固定 JSON 键序、无随机性）。

## 6. 机器校验（build/validate 自动完成）

- 六要素覆盖：假设与变量、对照组、设备材料、时间成本、参数范围、风险异常 + 判读标准，逐项非空且字段完整。
- 算术：步骤 DAG 无环、关键路径与声明时长一致、预算逐项求和、参数值在允许区间内、样本×重复=总运行数、预算/时间窗不被超出。
- 候选：每个候选按参数区间/预算/时间窗判定 feasible/infeasible/unknown；选中的方案必须可行。
- 证据：逐条回查 `quote` 是否为原文子串（exact/空白归一化/去空白三级），命不中标 `not_found`；同一指标冲突取值标 `source_conflict`。
- 契约：result.json 顶层与 `output_contract` 镜像块均含全部契约键。

## 7. 自检清单（交付前逐条勾选）

- [ ] 任务完成度：目标是可检验假设而非复述；六要素+判读标准全部可操作（变量有单位/量程、步骤绑设备/时长、参数有区间与来源）。
- [ ] 正确性：假设可证伪；变量与测量方式匹配；每个对照写明排除哪类混杂；参数在设备/试剂量程内。
- [ ] 完整性：假设与变量、对照组、设备材料、时间成本、参数范围、风险异常、判读标准均非空。
- [ ] 可核验性：`design_ir.sha256` 与 plan.ir.json 一致；每条证据带 locator+逐字 quote 且回查通过；change_log 完整。
- [ ] 使用增益：候选方案比较与参数裁剪有算术依据（量程/预算/排期），不是模板空话。
- [ ] 创新增益：按目标反推对照、按量程与预算裁剪参数、生成风险替代路径至少落实一类。
- [ ] 易用性：`scripts/main.py all` 可一次完成生成并判断可用；空输入不崩溃。
- [ ] 稳定性：坏 JSON/编码→记录后继续；重复运行一致；退出码 0=可用、1=必须修复。
- [ ] 异常处理：输入缺失、约束冲突、试剂缺货/排期满、内容不确定均写 `pending_human`/替代路径。
- [ ] 生态贡献：`scripts/selftest.py` 可复跑；模板与清单在 `references/` 可复用。

## 8. 降级策略（不允许编造）

- 缺材料/数值：字段写 `null`，status 降为 `partial`/`insufficient_evidence`，缺口列 `pending_human`。
- 缺软件（`pdftotext` 不可用）：`degradations` 记“无法提取 PDF 正文”，改用材料文本字段，依赖 PDF 的结论标为未核实。
- 约束冲突：`uncertainty` 写冲突双方与影响，给两条替代路径并标需专家确认。
- 证据失真：`quote` 回查失败标 `not_found`，该依据降级为假设，不冒充已核实。
- 写盘失败：降级为最小合法 result.json（case_id+status+errors），进程不崩溃。
- 任何情况不编造数据、结论、报价、审批或评测分数；`runtime.model_requests_used` 保持 `null`（由平台记录）。

## 9. 脚本与资源

```bash
cd /skills/weekly-skill
python3 scripts/main.py probe    --input /input --work /app/work
python3 scripts/main.py scaffold --input /input --output /app/output --work /app/work
# ……读材料并填充 /app/work/plan.ir.json（唯一事实源）……
python3 scripts/main.py build    --input /input --output /app/output --work /app/work
python3 scripts/main.py validate --input /input --output /app/output --work /app/work
python3 scripts/selftest.py      # 自检：空输入/坏JSON/完整示例/幂等/证据回查
```

- `scripts/main.py`：入口（<30KB）；`scripts/core_impl.py`：实现（纯标准库）。
- `scripts/selftest.py`：五场景自检，仅在临时目录运行，不产生正式测试产物。
- `references/plan_ir_schema.md`：IR 字段语义、契约映射、算术公式、status 判定。
- `references/self_check_rubric.md`：评分维度→检查项→常见失败与修复。

## 10. 最小调用示例（示意，非正式案例结果）

```json
{"case_id": "demo-001", "goal": "评估某抑制剂对某细胞系增殖的影响",
 "output_contract": {"plan": {}, "evidence_and_risks": {}, "confirmations": {}}}
```

```bash
cd /skills/weekly-skill
python3 scripts/main.py all --input ./demo/input --output ./demo/output --work ./demo/work
```

骨架输出 result.json 示意（示例，非正式结果）：

```json
{"case_id": "demo-001", "plan": {}, "evidence_and_risks": {}, "confirmations": {},
 "status": "partial", "sources": [], "uncertainty": [], "pending_human": [],
 "change_log": [{"seq": 1, "ts": "...", "event": "skeleton_written"}]}
```

## 11. 限制

本 Skill 只做方案生成、比较与复核，不做实验执行、不替代安全伦理审查，也不声称材料为真实报价/审批；组织方给定的前瞻性价格与排期只标“案例给定约束（非真实报价）”。适合一个明确科研目标，不做“通用大而全”的抽象。
