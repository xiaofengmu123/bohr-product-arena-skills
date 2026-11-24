# 降级决策表与标记规则

本文件给出可执行的降级规则：任何材料/工具缺口都**显式记录**而非编造，保证 result.json 始终合法、诚实、可继续编辑。

## 1. 降级决策表

| 场景 | 触发 | 必须动作 | status 建议 |
|-|-|-|-|
| 材料缺失/字段不明 | 所需数值、统计量、单位未提供 | 写 `uncertainty`（type 见证据文件）；字段留空或 `null` | `partial` |
| 引用无法核实 | 文献无全文/DOI 不可得 | locator 保留，`status=unverified`，不伪造 DOI/URL | `partial` |
| 来源冲突 | 两来源同一指标不一致 | 两值都记，标 `source_conflict`，正文选一个并说明 | `partial` |
| 软件/工具不可用 | 无 pdf-text-tool、office 渲染、字体 | 写 `limitations`，交付纯文本/结构化产物，不假装已渲染 | `partial` |
| 输入超长/跨度过大 | 单文件超上限或跨度大 | 截取关键段，`uncertainty` 标 `truncated` | `partial` |
| 空输入目录 | 无任何材料 | 退出码 0，`status=insufficient_evidence`，列出缺口 | `insufficient_evidence` |
| 坏 JSON/坏编码 | case.json 或材料解析失败 | 记录到 `degradations`/`limitations`，继续处理其余材料 | `partial` |
| 运行失败 | 异常中断 | `status=failed`，change_log 记录失败原因与已完成中间产物，result.json 仍落盘 | `failed` |

## 2. 已完成 / 拟开展标记

- 材料中已有的数据、方法、前期成果标“已完成”。
- 讨论中的机制解释、未来工作、预测标“拟开展/推测”，并写入 `version_boundaries`。
- 不得把拟开展写成已完成的结论。

## 3. 结果 / 讨论标记

- 每个正文段落给 `claim_class`：`result_observation` | `discussion_interpretation` | `limitation` | `comparison_to_literature` | `mechanism_hypothesis`。
- 结果段段落不允许 `mechanism_hypothesis`；机制推测只出现在讨论段并标 `speculative`。

## 4. 自检脚本的使用

```bash
python scripts/check_result.py --result /app/output/result.json --summary /app/work/input_summary.json
python scripts/check_result.py --selftest
```

- 返回码 0 表示无硬错误（结构/契约/一致性）；警告（禁词、来源缺 quote 等）仍需逐条处理。
- 每次修复后把结论追加到 `change_log`：`python scripts/build_result.py ... --log "..."`。

## 5. 不编造清单（红线）

禁止：编造数值、单位、p 值、样本量、作者、文献、DOI/URL；伪造评测分数或运行结果；在工具缺失时声称“已渲染”；用文件名或领域标签补造正文数据。

## 6. 评分维度 ↔ 自检映射（可复用检查表）

| 评分维度 | 分值 | 对应自检项 | 脚本/资源落点 |
|-|-|-|-|
| 实际效果·任务完成度 | 15 | 交付可编辑结果段+讨论段，非提纲 | `deliverable` + manuscript.md |
| 实际效果·结果正确性 | 15 | 数值/单位/误差棒/统计量与原始一致 | §5 图表读数 + `facts_sources.quote` |
| 实际效果·结果完整性 | 5 | 图表、阴性结果、不一致文献、局限如实交代 | `version_boundaries` + `uncertainty` |
| 实际效果·结果可核验性 | 5 | 段落→数值→证据三级对应 | locator + quote（§1） |
| 使用增益·质量/效率 | 20 | 减少起草/核对/返工，增益可验证 | 脚本流水线 + `change_log` |
| 使用增益·可验证性 | 5 | 公平对照记录 | 不伪造对照，交由平台记录 |
| 创新增益 | 15 | 四关由脚本强制执行 | `check_result.py` |
| 可靠体验 | 10 | 换图/换学科/缺数据后仍可用 | `--selftest` + 降级表 |
| 生态贡献 | 10 | 文档、模板、清单可复用 | SKILL.md + 两份 references |

## 7. 修复优先级（回修顺序）

1. **结构硬错误**（先修）：缺 `case_id`/契约字段/信封字段、`case_id` 不一致、status 非法。
2. **来源硬错误**（再修）：`verified` 缺 locator 或逐字 quote。
3. **禁词警告**：删除无据措辞，或补 locator+quote 佐证。
4. **边界警告**：补 `uncertainty`/`limitations`/`pending_human`。
5. 每修一处即 `--log` 记录，最后再跑一次 `check_result.py` 确认无硬错误。

## 8. 常见失败模式与 Skill 的强制处置（可复用对照）

| 基础 Agent 常见失败 | 本 Skill 的强制处置 |
|-|-|
| 把小波动写成“显著升高” | 语气强度阶梯（§2）+ 禁词扫描 |
| 结果段混入“可能是因为” | 结果/讨论分界 + `claim_class` 检查 |
| 数值与图表不一致却默默改掉 | locator+quote 回检，标 `source_conflict` |
| 误差棒含义靠猜（SD/SEM/CI） | 必须从图注/方法确认，否则 `unit_ambiguous`/`significance_unstated` |
| 相关写成“导致/证明” | `causal_boundary` + `speculative` 标记 |
| 局限写成泛泛自评 | Limitations 落到方法/样本/测量/统计层面 |
| 缺数据时补造数值 | `not_reported` + 字段留空，禁止编造 |
| 工具缺失时假装已渲染 | `limitations` + 交付纯文本/结构化产物 |
