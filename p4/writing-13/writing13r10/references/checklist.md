# 自检清单、降级策略与 result.json 字段说明（references/checklist.md）

## 1. 逐条自检清单（对应评分维度，交付前逐条勾选）

未勾选即视为未通过，不得把 `status` 写成 `done`。

### 实际效果（40 分）
- [ ] 任务完成度：已按 task.md 指定体裁/受众/格式产出可编辑产物，`artifact.path` 指向真实文件。
- [ ] 结果正确性：每条数字/术语/引用/图表/论断都有 `locator` + `quote`，`unmapped`/`conflict` 已单独说明，已完成与拟开展已分清。
- [ ] 结果完整性：七要素齐全、厚度与任务匹配、要素之间衔接；评测类任务覆盖完整闭环。
- [ ] 结果可核验性：`sources` 列全来源文件且含 SHA256，`change_log` 完整，段落—要素—证据三级对应可被第三方复核。

### 使用增益（25 分）
- [ ] 质量增益：先校验后成稿（证据闭合→要素覆盖→一致性→成稿），判据可观察可操作。
- [ ] 效率增益：前 3 分钟落盘 partial 骨架，之后增量改进，不推倒重写；`change_log` 体现增量。
- [ ] 增益可验证性：结论基于材料证据与来源映射，不靠精选案例或自我夸奖。

### 创新增益（15 分）
- [ ] 新增能力：把“准确/完整/可编辑/可复核”翻译为四道闸门与一致性矩阵的可观察判据。
- [ ] 方法改进：三层 IR（来源清单→证据台账→要素映射）+ 先校验后成稿，与通用 rubric 有实质区分。
- [ ] 创新证据：`uncertainty`/`version_boundary` 写清边界与失效条件（哪类材料可靠、哪类失效）。

### 可靠体验（10 分）
- [ ] 调用与易用性：新用户按 SKILL.md 可独立跑通；`python3 -B scripts/selftest.py` 全 PASS。
- [ ] 运行稳定性：脚本仅标准库、路径相对化、重复运行口径一致。
- [ ] 异常处理：题面歧义、答案缺失、双评分歧、材料超长/乱码、模型拒答均有识别与处置记录。

### 生态贡献（10 分）
- [ ] 文档完整：SKILL.md + rules.md + checklist.md 齐全，字段/版本/限制信息完整。
- [ ] 资源可复用：三层 IR、七要素 schema、一致性检查表、自检脚本可被他人直接复用。
- [ ] 分享价值：沉淀了建题/评分/复现方法与失败分析，而非一次性答案。

## 2. result.json 字段说明

- `case_id`：输入案例标识（来自 case.json 或脚本生成，不得虚构）。
- `status`：`in_progress` / `done` / `partial` / `failed`。时间不足或材料缺失一律 `partial`，禁止把未完成写成 `done`。
- `artifact`：目标产物。`path` 指向 /app/output 下可编辑文件；`preview_path` 指真实渲染预览；`kind/genre/audience/format/elements_covered` 描述产物性质。
- `fact_source_map`：事实与来源映射数组，每项含 `claim/kind/source_ref/locator/quote/status`。
- `version_boundary`：`modification_diff`（修改差异）、`manual_confirm`（需人工确认）、`material_gaps`（材料缺口）、`anomalies`（异常）。
- `sources`：真实使用的来源文件清单（路径、定位、类型、SHA256、编码、大小）。
- `uncertainty`：不确定项、推断项、缺数据项，逐条说明原因。
- `pending_human`：需要人工确认的事项（引用核验、歧义消解、双评分歧等）。
- `change_log`：逐次修改记录（时间/动作/原因/影响）。
- `errors` / `degradations`：运行期错误与降级记录（坏 JSON、坏编码、超长、缺软件）。
- `runtime`：`started_at/updated_at/elapsed_s/stage/requests_used`，运行时如实记录。
- `contract_hint`：case.json 中显式的 `output_contract/output_schema/required_fields` 原样回填。

## 3. 降级策略（必须遵守）

- 缺数据/缺材料 → 写 `version_boundary.material_gaps` 与 `uncertainty`，`status=partial`，绝不补造。
- 缺软件（PDF/OCR/LaTeX/Office 渲染不可用）→ `uncertainty` 写明未能生成的项，保留可编辑中间产物，不伪造预览。
- 材料超长或格式错乱 → 分段读取并记录已读范围；未读部分进 `material_gaps`，不声称已全部核对。
- 模型拒答/截断 → 分块处理，已完成部分先行落盘，续写只改差异。
- 双评分歧超过一档、参考答案缺失 → 记录分歧判据与双方依据，不擅自改分。
- 空输入目录 → 退出码 0，写合法 `insufficient_evidence` 结果，不抛异常。
- 坏 JSON/坏编码 → 捕获并记录到 `errors/degradations`，继续处理其余材料。

## 4. 与脚本的对应

- `scripts/main.py bootstrap|inventory|record|check|finalize`：落盘/盘点/登记/校验/收尾。
- `scripts/evidence.py`：IR 与校验核心（L1/L2/L3、四道闸门、契约构建）。
- `scripts/selftest.py`：提交前自检（stdlib-only、frontmatter、端到端、无伪造分数）。
