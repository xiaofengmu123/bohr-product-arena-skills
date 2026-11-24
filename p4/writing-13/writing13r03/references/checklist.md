# 自检清单、降级策略与 result.json 字段说明

## 1. 逐条自检清单（对应评分维度；selftest.py 自动复核硬性项）

执行到 Step 4/5 时逐条勾选；硬性项未通过不得把 `status` 写成 `done`。

### 实际效果（40 分）
- [ ] 任务完成度（15）：已按 task.md 指定体裁/受众/格式产出"章节稿＋要素对照说明"，`artifact.path` 指向真实文件。
- [ ] 结果正确性（15）：每个数字/术语/引用/图表/论断都有 `source_ref` 与逐字 `quote`；`unmapped`/`conflict` 单独说明；已完成/拟开展已分清。
- [ ] 结果完整性（5）：覆盖任务要求的七要素与题量，要素之间衔接，一致性矩阵无孤儿项。
- [ ] 结果可核验性（5）：`sources` 列全来源文件（含 SHA256），`change_log` 完整，段落→要素→证据三级对应可回溯。

### 使用增益（25 分）
- [ ] 质量增益（15）：判据可观察可操作（四个判据），而非泛泛"准确、完整"。
- [ ] 效率增益（5）：台账＋自检减少返工，`change_log` 体现增量改进而非推倒重写。
- [ ] 增益可验证性（5）：结论基于材料证据与来源映射，不靠精选案例或自夸。

### 创新增益（15 分）
- [ ] 新增能力（5）：把"立得住/说得准/对得上/查得到"译成可观察判据。
- [ ] 方法改进（5）：事实台账、七要素一致性矩阵、时序标注、确定性自检有实质区分。
- [ ] 创新证据（5）：`uncertainty`/`limitations` 写清边界与失效条件。

### 可靠体验（10 分）
- [ ] 调用与易用性（3）：新评分员按 SKILL.md＋references 可独立跑通。
- [ ] 运行稳定性（4）：脚本仅标准库、路径相对化、排序键固定，同输入两次产物逐字节一致。
- [ ] 异常处理（3）：题面歧义、材料缺失、超长/乱码、引用无法核实、学科跨度大、模型拒答均有记录。

### 生态贡献（10 分）
- [ ] 文档完整（3）：SKILL.md＋rules.md＋checklist.md 齐全。
- [ ] 资源可复用（4）：台账、快照、自检脚本可被他人直接复用。
- [ ] 分享价值（3）：沉淀写作/复现方法与失败分析。

## 2. result.json 字段说明

- `case_id`：案例标识（来自 case.json，不得虚构）。
- `status`：`in_progress`（进行中）/`done`（完成）/`partial`（部分完成，含缺口）/`failed`（失败）/`insufficient_evidence`（材料不足）/`input_error`（输入不可解析）。时间不足或材料缺失一律 `partial`，禁止把未完成写成 `done`。
- `artifact`：目标产物。`path` 指向 `/app/output` 下可编辑文件（相对路径）；`element_matrix` 指向要素对照说明；Office/slide/diagram 另附渲染预览并在 `path`/`sources` 注明。
- `fact_source_map`：事实-来源映射数组，字段见 `rules.md` 第 1 节。
- `version_boundary`：`modification_diff`（修改差异）、`manual_confirm`（需人工确认项）、`material_gaps`（材料缺口）、`anomalies`（异常）。
- `sources`：真实用到的来源文件清单（路径、大小、SHA256、类型）。
- `uncertainty`：不确定项、推断项、缺数据项，逐条说明原因。
- `pending_human`：需要人工确认或补填的项（缺失元数据、冲突裁决等）。
- `limitations`：本产物的边界与失效条件。
- `change_log`：逐次修改记录（step/action/reason/impact），步序号而非时间戳以保证确定性。
- `runtime`：`deterministic`、`rerun_identical`、`input_digest`、`result_digest`、`python_version`。
- `contract_hint`：case.json 中显式给出的 `output_contract`/`output_schema`/`required_fields` 原样回填。

## 3. 降级策略（必须遵守）

- 缺数据/缺材料 → 写 `version_boundary.material_gaps` 与 `uncertainty`，`status=partial`，绝不补造缺失内容、分数、引用或运行结果。
- 缺软件（PDF/OCR/LaTeX/Office 渲染不可用）→ 在 `uncertainty` 写明"未能生成渲染预览"，用纯文本/Markdown 保留可编辑中间产物，不伪造预览。
- 材料超长/格式错乱 → 分段读取并记录已读范围；未读部分写入 `material_gaps`，不得声称已全部核对。
- 模型拒答/输出截断（单次 ≤16384 token）→ 分块处理，已完成的中间结果先行落盘，续写只改差异。
- 冲突与分歧 → 记录双方依据（`status=conflict`），不擅自选一、不擅自改分。
- 学科跨度过大/指南不明 → 在 `version_boundary.anomalies` 记录无法确定的字段，`status=partial`。

## 4. 确定性规则

- 同输入两次运行，`/app/output/result.json` 逐字节一致；`selftest.py` 重算 `runtime.result_digest` 校验。
- 禁止在产物中写墙钟时间、随机数、环境绝对路径；`change_log` 用步序号。
- 脚本均为标准库、路径相对化、排序键固定；重复运行结果一致可解释。
