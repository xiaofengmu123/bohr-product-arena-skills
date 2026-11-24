# 自检清单、降级策略与 result.json 字段说明

执行到 Step 4/5 时逐条勾选；未勾选即视为未通过，不得把 `status` 写成 `done`。

## 1. 逐条自检清单（对应评分维度）

### 实际效果（40 分）
- [ ] 任务完成度：按 task.md 指定体裁/受众/格式产出**可编辑章节稿**（非提纲、非建议、非空模板），`artifact.path` 指向真实存在文件。
- [ ] 结果正确性：每个数字/术语/引用/图表/论断都有 `source_ref` + `locator` + 逐字 `quote`；`unmapped`/`conflict` 已单独说明；"已完成"与"拟开展"严格分开。
- [ ] 结果完整性：七要素齐全、厚度匹配任务要求、要素间衔接；含"目标—内容—路线—指标"四级对应表。
- [ ] 结果可核验性：`sources` 全（含 SHA256）、`change_log` 完整、`fact_source_map` 构成段落—要素—证据三级对应。

### 使用增益（25 分）
- [ ] 质量增益：判据可观察可操作（四问的逐句核验点），而非泛泛"准确、完整"。
- [ ] 效率增益：台账 + 自检减少返工，`change_log` 体现增量改进而非推倒重写。
- [ ] 增益可验证性：结论基于材料证据与来源映射，不靠精选案例或自夸。

### 创新增益（15 分）
- [ ] 新增能力：把"立得住/说得准/对得上/查得到"译成可观察判据。
- [ ] 方法改进：事实台账、来源快照、四级对应表、诚实降级相对通用 rubric 有实质区分。
- [ ] 创新证据：`uncertainty`/`limitations` 写清边界与失效条件。

### 可靠体验（10 分）
- [ ] 调用与易用性：SKILL.md + references 足以让新执行者独立跑通。
- [ ] 运行稳定性：脚本仅标准库、路径相对化、原子写盘、重复运行结果一致。
- [ ] 异常处理：题面歧义、答案缺失、双评分歧、材料超长/乱码、模型拒答均有识别与处置记录。

### 生态贡献（10 分）
- [ ] 文档完整：SKILL.md + rules.md + checklist.md 齐全，字段与版本/限制信息完整。
- [ ] 资源可复用：台账、快照、自检、七要素模板可被他人直接用于自己的申请书。
- [ ] 分享价值：沉淀建题/评分/复现方法与失败分析，而非一次性答案。

## 2. result.json 字段说明

| 字段 | 类型 | 说明 |
|---|---|---|
| `case_id` | string | 输入案例标识（来自 case.json，不得虚构） |
| `status` | string | `in_progress` / `done` / `partial` / `failed`；时间或材料不足一律 `partial` |
| `artifact` | object | 主产物；`path` 指向 /app/output 下可编辑文件；Office/slide/diagram 需另附真实渲染 PDF/PNG 预览 |
| `fact_source_map` | array | 事实与来源映射（字段见 rules.md §3） |
| `version_boundary` | object | `modification_diff` / `manual_confirm` / `material_gaps` / `anomalies` |
| `sources` | array | 真实使用到的来源文件清单（路径、定位、类型、sha256） |
| `uncertainty` | array | 不确定项/推断项/缺数据项，逐条说明原因 |
| `pending_human` | array | 需人工确认项（数字、口径、已完成/拟开展归属等） |
| `change_log` | array | 逐次修改记录：`ts`/`action`/`target`/`reason`/`impact`/`status` |

- 若 `case.json` 显式给出 `output_contract`/`output_schema`/`required_fields`，其全部字段**必须逐一对齐并入 result.json**（缺值给 `null`/`[]`，不改名、不删未知键）；以题面字段为最高优先级，覆盖默认契约。
- 默认契约字段：`artifact`、`fact_source_map`、`version_boundary`。

## 3. 降级策略（必须遵守）

- 缺数据/缺材料 → 写 `version_boundary.material_gaps` 与 `uncertainty`，`status=partial`；绝不补造缺失内容、分数、引用或运行结果。
- 缺软件（PDF/OCR/LaTeX/Office 渲染不可用）→ 在 `uncertainty` 写明"未能生成渲染预览"等，保留可编辑中间产物，不伪造预览。
- 材料超长/格式错乱 → 分段读取并记录已读范围；未读部分进 `material_gaps`，不得声称已全部核对。
- 模型拒答/截断（单次 ≤16384 token）→ 分块处理，已完成部分先行落盘，续写只改差异。
- 冲突与分歧 → 记录双方依据，不擅自选一、不擅自改分。

## 4. 状态与诚实性原则

- `status` 只能是 `done`（全部完成且通过自检）、`partial`（有缺口，列 `limitations`）、`failed`（无法产出合法结果）、`in_progress`（仍在写）。
- 禁止把未完成的 `in_progress`/`partial` 改成 `done`；禁止伪造 benchmark 分数、DOI、卷期页码、图表内容或运行结果。
- 每个 `change_log` 条目必须带时间戳与动作，能由第三方重放整个增量过程。
