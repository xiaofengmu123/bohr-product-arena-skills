# 自检清单、降级策略与 result.json 字段说明（checklist）

## 1. 逐条自检清单（对应评分维度，交付前逐条勾选）

### 实际效果（40 分）
- [ ] 任务完成度(15)：产出"可直接进入申请书的章节稿 + 要素对照说明"，体裁/受众/格式符合 task.md，`artifact.path` 指向真实文件。
- [ ] 结果正确性(15)：每条数字/术语/引用/论断有 `source_ref`（file+loc+quote）；已完成/拟开展分清；对齐矩阵无空行。
- [ ] 结果完整性(5)：七要素齐全、厚度匹配、要素衔接；对齐矩阵覆盖全部目标。
- [ ] 结果可核验性(5)：`sources` 全、`change_log` 完整、`source_manifest.json` 含 SHA256。

### 使用增益（25 分）
- [ ] 质量增益(15)：四问判据可观察可操作（立得住/说得准/对得上/查得到），而非泛泛"准确、完整"。
- [ ] 效率增益(5)：plan.json 先行 + 台账回检减少返工，`change_log` 体现增量改进而非推倒重写。
- [ ] 增益可验证性(5)：结论基于材料证据与来源映射，不靠精选案例或自我夸奖。

### 创新增益（15 分）
- [ ] 新增能力(5)：七要素对齐矩阵、禁止修饰语扫描、已完成/拟开展标注是基础 Agent 与通用模板不具备的能力。
- [ ] 方法改进(5)：中间表示渲染 + 一致性回检 + 评审人视角自检带来实质改进。
- [ ] 创新证据(5)：`uncertainty`/`limitations` 写清边界与失效条件（哪类材料下可靠、哪类失效）。

### 可靠体验（10 分）
- [ ] 调用与易用性(3)：新评分员按 SKILL.md 可独立跑通 bootstrap → scan → 写作 → verify → selftest → finalize。
- [ ] 运行稳定性(4)：脚本仅标准库、路径相对化、重复运行结果一致；坏 JSON/坏编码不崩溃。
- [ ] 异常处理(3)：题面歧义、答案缺失、材料超长/乱码、引用无法核实、模型拒答均有识别与处置记录。

### 生态贡献（10 分）
- [ ] 文档完整(3)：SKILL.md + rules.md + checklist.md 齐全，字段与版本/限制信息完整。
- [ ] 资源可复用(4)：plan.json schema、对齐矩阵、台账、自检脚本可被他人直接用于自己的申请书。
- [ ] 分享价值(3)：沉淀建题/写作/回检方法与失败分析，而非一次性答案。

## 2. result.json 字段说明

- `case_id`：输入案例标识（来自 case.json，不虚构）。
- `status`：`in_progress` / `done` / `partial`（部分完成，含缺口）/ `failed`。时间不足或材料缺失一律 `partial`，禁止把未完成写成 `done`。
- `artifact`：目标产物。`path` 指向 /app/output 下可编辑文件（章节稿 `.md`）；Office/slide/diagram 需另附真实渲染 PDF/PNG 预览并注明。
- `fact_source_map`：事实与来源映射数组，条目格式见 `rules.md` 第 3 节。
- `version_boundary`：`modification_diff`（修改差异）、`manual_confirm`（需人工确认项）、`material_gaps`（材料缺口）、`anomalies`（异常）。
- `sources`：真实使用的来源文件清单（路径、大小、SHA256、类型）。
- `uncertainty`：不确定项、推断项、缺数据项，逐条说明原因。
- `pending_human`：需人工确认的项（无法核实引用、真实申请人证据缺失等）。
- `change_log`：逐次修改记录（时间/动作/原因/影响）。
- `errors` / `degradations` / `runtime`：运行错误、降级记录、耗时。

## 3. 降级策略（必须遵守）

- 缺数据/缺材料 → 写 `version_boundary.material_gaps` 与 `uncertainty`，`status=partial`，绝不补造缺失内容。
- 缺软件（PDF/OCR/LaTeX/Office 渲染不可用）→ 在 `uncertainty` 写明未生成的项，用纯文本/Markdown 保留可编辑中间产物，不伪造预览。
- 材料超长/格式错乱 → 分段读取并记录已读范围；未读部分进 `material_gaps`，不得声称已全部核对。
- 模型拒答/截断（单次 ≤16384 token）→ 分块处理，已完成部分先落盘，续写只改差异。
- 引用无法核实 → 标 `unmapped`，进 `pending_human`，不补造 DOI/卷期页码。
- 双评分歧/参考答案缺失 → 记录分歧判据与双方依据，不擅自改分。

## 4. 评分维度对应表

| 评分维度（分值） | 对应产物位置 |
|---|---|
| 实际效果(40) | `/app/output/proposal_section.md` + `/app/output/element_map.md` + `result.json.fact_source_map` |
| 使用增益(25) | `/app/work/plan.json`（对齐矩阵）+ `evidence_ledger.json` + `verify_report.json` |
| 创新增益(15) | `plan.json` 中间表示、禁止修饰语扫描、已完成/拟开展标注、`uncertainty`/`limitations` |
| 可靠体验(10) | SKILL.md 流程、`scripts/*.py`（标准库、相对路径）、异常处理记录 |
| 生态贡献(10) | SKILL.md + rules.md + checklist.md、可复用 schema 与脚本 |
